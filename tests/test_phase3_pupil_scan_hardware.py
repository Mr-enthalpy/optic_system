"""
Phase 3.1 hardware pupil scan smoke test (opt-in).

Requirements:
    OPTIC_SYSTEM_RUN_PHASE3_HARDWARE_TESTS=1
    OPTIC_SYSTEM_RUN_PUPIL_SCAN_HARDWARE_TESTS=1
    TLS_C1_SERIAL=...  (optional)
"""

from __future__ import annotations

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
_PUPIL_ENABLED = os.environ.get("OPTIC_SYSTEM_RUN_PUPIL_SCAN_HARDWARE_TESTS") == "1"

if not _HW_ENABLED or not _PUPIL_ENABLED:
    pytest.skip("Phase 3 pupil scan hardware tests are opt-in", allow_module_level=True)


def _ensure_sys_path() -> None:
    root = str(_REPO)
    if root not in __import__("sys").path:
        __import__("sys").path.insert(0, root)


def test_phase3_pupil_scan_hardware_smoke() -> None:
    _ensure_sys_path()
    from devices.camera_service import CameraServiceClient
    from devices.lcd_service import LCDService
    from scripts.analyze_pupil_scan import analyze_pupil_scan
    from scripts.capture_pupil_scan import run_pupil_scan

    camera_params_source = _REPO / "outputs" / "exposure_calibration" / "camera_params.json"
    if not camera_params_source.exists():
        pytest.skip(f"camera params not found: {camera_params_source}")

    tmp_dir = Path(tempfile.mkdtemp(prefix="optsys_pupil_hw_"))
    plan = {
        "plan_id": "phase3_pupil_hw_smoke",
        "camera_params_source": str(camera_params_source),
        "wavelength": {"wavelength_nm": 550.0, "grating": 1, "settle_ms": 0},
        "lcd": {
            "settle_ms": 50,
            "mode": "procedural_scan",
            "physical_shape": None,
            "subpixel_axis": None,
        },
        "scan": {
            "scan_modes": ["bars_x", "bars_y", "blocks"],
            "active_code": 255,
            "background_code": 0,
            "bar_count": 4,
            "block_rows": 3,
            "block_cols": 4,
            "include_baselines": True,
            "store_physical_masks": False,
        },
        "camera": {"frames_per_capture": 2},
        "analysis_hint": {"response_metric": "robust_energy"},
        "lock_file": str(tmp_dir / "capture_hardware.lock"),
        "output": {
            "raw_h5": str(tmp_dir / "pupil_hw.h5"),
            "output_dir": str(tmp_dir / "pupil_out"),
        },
    }

    camera_index = int(os.environ.get("OPTIC_SYSTEM_CAMERA_INDEX", "0"))
    display_index_raw = os.environ.get("OPTIC_SYSTEM_LCD_DISPLAY_INDEX", "").strip()
    display_index = int(display_index_raw) if display_index_raw else None
    axis_raw = os.environ.get("OPTIC_SYSTEM_LCD_SUBPIXEL_AXIS", "").strip()
    subpixel_axis = int(axis_raw) if axis_raw in ("0", "1") else None

    camera_service = CameraServiceClient(timeout_ms=5000, auto_ensure=True)
    lcd_service = LCDService(display_index=display_index, subpixel_axis=subpixel_axis)

    tls_service = None
    tls_serial = os.environ.get("TLS_C1_SERIAL")
    if tls_serial:
        try:
            from devices.tls_service import TLSService

            tls_service = TLSService(default_serial_number=tls_serial)
            status = tls_service.connect(serial_number=tls_serial)
            assert status.connected
        except ImportError:
            print("TLS not available, running without TLS")
        except Exception as exc:
            print(f"TLS connection failed: {exc}")

    try:
        reply = camera_service.open_camera(index=camera_index, disable_trigger=True)
        print(f"camera: {reply.get('serial')} {reply['width']}x{reply['height']}")

        raw_h5 = run_pupil_scan(
            plan,
            dry_run=False,
            camera_service=camera_service,
            lcd_service=lcd_service,
            tls_service=tls_service,
        )
        assert raw_h5.exists()

        with h5py.File(raw_h5, "r") as f:
            frames = np.asarray(f["raw/frames_avg"])
            assert frames.shape[0] > 0
            assert np.isfinite(frames).all()
            assert f["scan/mask_id"].shape[0] == frames.shape[0]
            raw = f["camera/camera_params_source_json"][()]
            if isinstance(raw, bytes):
                raw = raw.decode()
            assert json.loads(raw)["source"] == str(camera_params_source)

        result = analyze_pupil_scan(
            raw_h5,
            tmp_dir / "pupil_out",
            smooth_window=3,
            min_component_size=1,
        )
        assert (tmp_dir / "pupil_out" / "effective_lcd_roi.json").exists()
        assert result["confidence"]["level"] != "failed"

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
        if tls_service is not None:
            tls_service.close()
