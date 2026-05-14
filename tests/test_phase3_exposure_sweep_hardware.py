"""
Phase 3.0.5 — Hardware exposure sweep test (opt-in).

Requirements:
    OPTIC_SYSTEM_RUN_PHASE3_HARDWARE_TESTS=1
    OPTIC_SYSTEM_RUN_EXPOSURE_SWEEP_HARDWARE_TESTS=1
    TLS_C1_SERIAL=...  (if TLS is available)
"""

from __future__ import annotations

import importlib
import json
import os
import tempfile
from pathlib import Path

import h5py
import numpy as np
import pytest

_REPO = Path(__file__).resolve().parents[1]

pytestmark = pytest.mark.phase3_hardware

_HW_ENABLED = os.environ.get("OPTIC_SYSTEM_RUN_PHASE3_HARDWARE_TESTS") == "1"
_SWEEP_ENABLED = os.environ.get("OPTIC_SYSTEM_RUN_EXPOSURE_SWEEP_HARDWARE_TESTS") == "1"

if not _HW_ENABLED or not _SWEEP_ENABLED:
    pytest.skip(
        "Phase 3 exposure sweep hardware tests are opt-in",
        allow_module_level=True,
    )


def _ensure_sys_path() -> None:
    root = str(_REPO)
    if root not in __import__("sys").path:
        __import__("sys").path.insert(0, root)


def test_exposure_sweep_hardware() -> None:
    _ensure_sys_path()
    from scripts.calibrate_camera_exposure_sweep import (
        load_exposure_sweep_plan,
        run_exposure_sweep,
    )
    from devices.camera_service import CameraServiceClient
    from devices.lcd_service import LCDService

    plan_path = _REPO / "plans" / "bishe_exposure_sweep.yaml"
    if not plan_path.exists():
        pytest.skip(f"plan not found: {plan_path}")

    plan = load_exposure_sweep_plan(plan_path)

    camera_index = int(os.environ.get("OPTIC_SYSTEM_CAMERA_INDEX", "0"))
    display_index_raw = os.environ.get("OPTIC_SYSTEM_LCD_DISPLAY_INDEX", "").strip()
    display_index = int(display_index_raw) if display_index_raw else None
    subpaxis_raw = os.environ.get("OPTIC_SYSTEM_LCD_SUBPIXEL_AXIS", "").strip()
    subpixel_axis = int(subpaxis_raw) if subpaxis_raw in ("0", "1") else None

    camera_service = CameraServiceClient(timeout_ms=5000, auto_ensure=True)
    lcd_service = LCDService(display_index=display_index, subpixel_axis=subpixel_axis)

    tls_service = None
    tls_serial = os.environ.get("TLS_C1_SERIAL")
    if tls_serial:
        try:
            from devices.tls_service import TLSService
            tls_service = TLSService(default_serial_number=tls_serial)
            tls_status = tls_service.connect(serial_number=tls_serial)
            assert tls_status.connected
            print(f"  TLS connected: {tls_status.device_id}")
        except ImportError:
            print("  TLS not available, running without TLS")
        except Exception as e:
            print(f"  TLS connection failed: {e}")

    tmp_dir = tempfile.mkdtemp(prefix="optsys_es_")
    plan["output"]["raw_h5"] = str(Path(tmp_dir) / "sweep.h5")
    plan["output"]["camera_params_json"] = str(Path(tmp_dir) / "params.json")
    plan["lock_file"] = str(Path(tmp_dir) / "capture_hardware.lock")

    try:
        reply = camera_service.open_camera(index=camera_index, disable_trigger=True)
        print(f"  camera: {reply.get('serial')} {reply['width']}x{reply['height']}")

        h5_path, result = run_exposure_sweep(
            plan, camera_service, lcd_service, tls_service,
        )

        assert h5_path.exists()
        with h5py.File(h5_path, "r") as f:
            assert f["sweep/exposure_us"].shape[0] > 0
            full_scale = float(f["sweep"].attrs["frame_dtype_full_scale"])
            assert full_scale > 0
            assert "saturated_pixel_count" in f["sweep"]
            assert "psf_safe" in f["sweep"]
            pf_raw = f["capture/processing_flags_json"][()]
            if isinstance(pf_raw, bytes):
                pf_raw = pf_raw.decode()
            pf = json.loads(pf_raw)
            assert pf["scientific_calibration_valid"] is False
            assert pf["training_ready"] is False
            assert pf["phase"] == "phase3_0_5b_psf_safe_exposure"

        gsc = result.get("global_safe_camera", {})
        if gsc.get("exposure_us") is not None:
            print(f"\nSAFE: exposure={gsc['exposure_us']} us  "
                  f"gain={gsc['gain_db']} dB  "
                  f"gain_elevated={gsc.get('gain_elevated')}")
            assert result["validity"]["exposure_safety_valid"] is True
            assert result["validity"]["psf_exposure_safe"] is True
            full_scale = float(result["frame_dtype_full_scale"])
            for wl, metrics in result["per_wavelength_metrics"].items():
                print(
                    f"  wl={wl} max={metrics['max_pixel']} "
                    f"p99.9={metrics['p99_9']} "
                    f"sat_count={metrics['saturated_pixel_count']} "
                    f"psf_safe={metrics['psf_safe']}"
                )
                assert metrics["max_pixel"] < full_scale
                assert metrics["max_pixel"] <= full_scale * 0.90
                assert metrics["saturated_pixel_count"] == 0
                assert metrics["psf_safe"] is True
        else:
            print(f"\nSAFE params not found: {result.get('selection_reason')}")
            print(f"  error: {result.get('error')}")
            assert result["selection_reason"] is not None

        params_path = Path(plan["output"]["camera_params_json"])
        assert params_path.exists()
        with open(params_path, "r") as f:
            saved = json.load(f)
        assert "global_safe_camera" in saved
        assert "selection_reason" in saved
        assert saved["validity"]["scientific_calibration_valid"] is False
        assert saved["validity"]["training_ready"] is False

    finally:
        try:
            camera_service.stop_stream()
        except Exception:
            pass
        try:
            camera_service.close_camera()
        except Exception:
            pass
        camera_service.close()
        lcd_service.close()
        if tls_service:
            tls_service.close()
