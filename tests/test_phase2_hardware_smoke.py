"""
Phase 2B — Hardware smoke capture validation tests.

All tests are opt-in:

    OPTIC_SYSTEM_RUN_PHASE2_HARDWARE_TESTS=1

TLS tests additionally require:

    OPTIC_SYSTEM_RUN_TLS_HARDWARE_TESTS=1
    TLS_C1_SERIAL

These tests verify hardware control and raw HDF5 structure only.
Scientific optical validity is explicitly out of scope.
"""

from __future__ import annotations

import importlib
import os
import tempfile
from pathlib import Path

import h5py
import numpy as np
import pytest

_SYS_MODULE = "tasks.capture_forward_dataset"

pytestmark = pytest.mark.phase2_hardware

_HW_ENABLED = os.environ.get("OPTIC_SYSTEM_RUN_PHASE2_HARDWARE_TESTS") == "1"
_TLS_ENABLED = os.environ.get("OPTIC_SYSTEM_RUN_TLS_HARDWARE_TESTS") == "1"

if not _HW_ENABLED:
    pytest.skip(
        "Phase 2B hardware tests are opt-in",
        allow_module_level=True,
    )


_SUSPICIOUS_DISPLAY_HEIGHTS = frozenset({1080, 1440, 2160, 768, 900, 1050, 1200, 1600})


def _require_target_lcd(lcd_service) -> dict:
    meta = lcd_service.get_metadata()
    reported = tuple(meta["reported_shape"])
    h, w, _ = reported
    if h in _SUSPICIOUS_DISPLAY_HEIGHTS or w in _SUSPICIOUS_DISPLAY_HEIGHTS:
        pytest.fail(
            f"LCD reported_shape={reported} looks like a normal monitor, "
            f"not the target 540×2560 LCD. "
            f"Pass --lcd-display-index to select the correct display."
        )
    phys = meta["physical_shape"]
    print(f"  LCD reported={reported} physical={phys}")
    return meta


def _temp_h5_path() -> Path:
    d = tempfile.mkdtemp(prefix="optsys_hw_")
    return Path(d) / "hardware_smoke.h5"


# ---------------------------------------------------------------------------
# Layer 1 — single-device smoke
# ---------------------------------------------------------------------------


def test_camera_capture_one_hardware() -> None:
    _ensure_tasks_module()
    from devices.camera_service import CameraServiceClient
    from devices.frame_stream import FrameStreamClient

    camera_index = int(os.environ.get("OPTIC_SYSTEM_CAMERA_INDEX", "0"))
    service = CameraServiceClient(timeout_ms=5000, auto_ensure=True)
    stream = FrameStreamClient(recv_timeout_ms=5000)
    try:
        reply = service.open_camera(index=camera_index, disable_trigger=True)
        assert reply["width"] > 0
        assert reply["height"] > 0
        print(f"  camera open: {reply.get('serial')} "
              f"{reply['width']}×{reply['height']}")

        service.start_stream()
        packet = stream.recv_frame()
        raw = packet.raw
        assert raw.ndim == 2
        assert raw.size > 0
        assert np.isfinite(raw).all()
        print(f"  frame: {raw.shape} {raw.dtype}")

        service.stop_stream()
        service.close_camera()
    finally:
        stream.close()
        service.close()


def test_lcd_show_mask_hardware() -> None:
    _ensure_tasks_module()
    from devices.lcd_service import LCDService

    display_index = int(os.environ.get("OPTIC_SYSTEM_LCD_DISPLAY_INDEX", "0"))
    lcd = LCDService(display_index=display_index)
    try:
        meta = _require_target_lcd(lcd)
        h, w = meta["physical_shape"]

        all_black = np.zeros((h, w), dtype=np.uint8)
        all_white = np.full((h, w), 255, dtype=np.uint8)
        stripes = np.zeros((h, w), dtype=np.uint8)
        stripe_w = max(w // 10, 3)
        stripe_w = (stripe_w // 3) * 3
        if stripe_w < 3:
            stripe_w = 3
        for x in range(0, w, stripe_w * 2):
            end = min(x + stripe_w, w)
            stripes[:, x:end] = 255

        lcd.show_mono_mask(all_black, mask_id="all_black")
        print("  shown: all_black")
        lcd.show_mono_mask(all_white, mask_id="all_white")
        print("  shown: all_white")
        lcd.show_mono_mask(stripes, mask_id="vertical_stripes")
        print("  shown: vertical_stripes")
    finally:
        lcd.close()


def test_tls_status_hardware() -> None:
    if not _TLS_ENABLED:
        pytest.skip("TLS hardware tests require OPTIC_SYSTEM_RUN_TLS_HARDWARE_TESTS=1")

    pytest.importorskip("tls_c1")

    serial = os.environ.get("TLS_C1_SERIAL")
    if not serial:
        pytest.skip("TLS_C1_SERIAL is required for TLS hardware smoke")

    safe_grating = int(os.environ.get("TLS_C1_SAFE_GRATING", "1"))
    safe_wavelength = float(os.environ.get("TLS_C1_SAFE_WAVELENGTH_NM", "550.0"))

    from devices.tls_service import TLSService
    service = TLSService(default_serial_number=serial)
    try:
        status = service.connect(serial_number=serial)
        assert status.connected is True
        print(f"  connected: device={status.device_id}")

        service.set_grating(safe_grating)
        print(f"  grating set to {safe_grating}")
        service.set_wavelength_nm(safe_wavelength)
        print(f"  target wavelength {safe_wavelength} nm")
        service.move(timeout_s=60.0)
        service.wait_until_idle(timeout_s=60.0)

        status = service.get_status()
        assert not status.moving
        print(f"  current={status.current_wavelength_nm} nm  "
              f"target={status.target_wavelength_nm} nm")
    finally:
        service.close()


# ---------------------------------------------------------------------------
# Layer 2 — two-device: camera + LCD
# ---------------------------------------------------------------------------


def test_capture_no_tls_hardware() -> None:
    _ensure_tasks_module()
    mod = importlib.import_module(_SYS_MODULE)

    from devices.camera_service import CameraServiceClient
    from devices.frame_stream import FrameStreamClient
    from devices.lcd_service import LCDService
    from capture.frame_capture import FrameCaptureHelper
    from tasks.capture_plan import CapturePlan

    camera_index = int(os.environ.get("OPTIC_SYSTEM_CAMERA_INDEX", "0"))
    display_index = int(os.environ.get("OPTIC_SYSTEM_LCD_DISPLAY_INDEX", "0"))

    plan_path = _repo_root() / "plans" / "hardware_smoke_no_tls.yaml"
    if not plan_path.exists():
        pytest.skip(f"plan not found: {plan_path}")
    plan = CapturePlan.load_yaml(str(plan_path))

    camera_service = CameraServiceClient(timeout_ms=5000, auto_ensure=True)
    frame_stream = FrameStreamClient(recv_timeout_ms=5000)
    lcd_service = LCDService(display_index=display_index)
    output = _temp_h5_path()

    try:
        reply = camera_service.open_camera(index=camera_index, disable_trigger=True)
        print(f"  camera: {reply.get('serial')} {reply['width']}×{reply['height']}")
        camera_service.start_stream()

        _require_target_lcd(lcd_service)

        capture_helper = FrameCaptureHelper(frame_stream)
        devices = _make_bundle(
            mod,
            camera=mod.CameraCaptureAdapter(capture_helper),
            lcd=mod.LCDAdapter(lcd_service),
            tls=None,
        )

        actual = mod.run_capture_forward_dataset(
            plan=plan,
            devices=devices,
            output_path=output,
            enable_tls=False,
            dry_run=False,
        )

        _verify_hdf5_structure(actual, plan, expect_tls=False)
        print(f"  OK: {actual}")
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
        frame_stream.close()
        lcd_service.close()


# ---------------------------------------------------------------------------
# Layer 3 — three-device: camera + LCD + TLS
# ---------------------------------------------------------------------------


def test_capture_with_tls_hardware() -> None:
    if not _TLS_ENABLED:
        pytest.skip("TLS hardware tests require OPTIC_SYSTEM_RUN_TLS_HARDWARE_TESTS=1")

    pytest.importorskip("tls_c1")

    serial = os.environ.get("TLS_C1_SERIAL")
    if not serial:
        pytest.skip("TLS_C1_SERIAL is required for TLS hardware smoke")

    _ensure_tasks_module()
    mod = importlib.import_module(_SYS_MODULE)

    from devices.camera_service import CameraServiceClient
    from devices.frame_stream import FrameStreamClient
    from devices.lcd_service import LCDService
    from devices.tls_service import TLSService
    from capture.frame_capture import FrameCaptureHelper
    from tasks.capture_plan import CapturePlan

    camera_index = int(os.environ.get("OPTIC_SYSTEM_CAMERA_INDEX", "0"))
    display_index = int(os.environ.get("OPTIC_SYSTEM_LCD_DISPLAY_INDEX", "0"))

    plan_path = _repo_root() / "plans" / "hardware_smoke_with_tls.yaml"
    if not plan_path.exists():
        pytest.skip(f"plan not found: {plan_path}")
    plan = CapturePlan.load_yaml(str(plan_path))

    camera_service = CameraServiceClient(timeout_ms=5000, auto_ensure=True)
    frame_stream = FrameStreamClient(recv_timeout_ms=5000)
    lcd_service = LCDService(display_index=display_index)
    tls_service = TLSService(default_serial_number=serial)
    output = _temp_h5_path()

    try:
        reply = camera_service.open_camera(index=camera_index, disable_trigger=True)
        print(f"  camera: {reply.get('serial')} {reply['width']}×{reply['height']}")
        camera_service.start_stream()

        _require_target_lcd(lcd_service)

        tls_status = tls_service.connect(serial_number=serial)
        assert tls_status.connected
        print(f"  tls: device={tls_status.device_id}")

        capture_helper = FrameCaptureHelper(frame_stream)
        devices = _make_bundle(
            mod,
            camera=mod.CameraCaptureAdapter(capture_helper),
            lcd=mod.LCDAdapter(lcd_service),
            tls=mod.TLSAdapter(tls_service),
        )

        actual = mod.run_capture_forward_dataset(
            plan=plan,
            devices=devices,
            output_path=output,
            enable_tls=True,
            dry_run=False,
        )

        _verify_hdf5_structure(actual, plan, expect_tls=True)
        print(f"  OK: {actual}")
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
        frame_stream.close()
        lcd_service.close()
        tls_service.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _ensure_tasks_module() -> None:
    root = str(_repo_root())
    if root not in __import__("sys").path:
        __import__("sys").path.insert(0, root)


def _make_bundle(mod, *, camera, lcd, tls):
    return type("_Bundle", (), {"camera": camera, "lcd": lcd, "tls": tls})()


def _verify_hdf5_structure(path: Path, plan, *, expect_tls: bool) -> None:
    with h5py.File(path, "r") as f:
        assert "raw/frames_avg" in f
        fa = f["raw/frames_avg"]
        assert fa.shape[0] == plan.n_captures
        assert fa.shape[1] > 0
        assert fa.shape[2] > 0

        assert "raw/frames" in f
        fb = f["raw/frames"]
        assert fb.shape[0] == plan.n_captures
        assert fb.shape[1] == plan.camera.frames_per_capture

        assert "masks/masks_physical" in f
        mp = f["masks/masks_physical"]
        assert mp.shape[0] == plan.n_masks

        assert "camera/timestamp_ns" in f
        assert "lcd/display_timestamp_ns" in f
        assert "capture/capture_index" in f
        assert "capture/completed" in f
        completed = f["capture/completed"][:]
        assert bool(completed.all())

        if expect_tls:
            assert "tls/wavelength_nm" in f
            assert "tls/grating" in f
            assert "tls/status_json" in f

        pf_raw = f["capture/processing_flags_json"][()]
        if isinstance(pf_raw, bytes):
            pf_raw = pf_raw.decode()
        assert "training_ready" in pf_raw
        assert "false" in pf_raw.lower()
