from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest


def test_mask_id_flows_through_adapter_to_lcd_service(monkeypatch):
    """mask_id reaches LCDService.show_mono_mask() via adapter chain."""
    from devices.lcd_service import LCDService
    from tasks.capture_forward_dataset import LCDAdapter

    calls = []
    original = LCDService.show_mono_mask

    def _track(self, mask, *, mask_id=None, mode="mono_mask"):
        calls.append(mask_id)
        return None

    monkeypatch.setattr(LCDService, "initialize", lambda self: None)
    monkeypatch.setattr(LCDService, "show_mono_mask", _track)

    from tests.test_lcd_service_status import FakeLCDBackend

    svc = LCDService(backend=FakeLCDBackend(), subpixel_axis=1, display_index=1)
    svc._backend = svc._backend
    adapter = LCDAdapter(svc)
    mask = np.zeros((3, 60), dtype=np.uint8)
    adapter.show_physical_mask(mask, mask_id="alpha")

    assert calls == ["alpha"]


def test_mask_id_appears_in_lcd_state(monkeypatch, tmp_path: Path):
    """Via real LCDService, show_physical_mask writes mask_id into lcd_state.json."""
    from devices.lcd_service import LCDService
    from tasks.capture_forward_dataset import LCDAdapter
    from diagnostics.run_status import read_lcd_state

    monkeypatch.setattr(LCDService, "initialize", lambda self: None)

    from tests.test_lcd_service_status import FakeLCDBackend

    sd = tmp_path / "status"
    svc = LCDService(backend=FakeLCDBackend(), subpixel_axis=1, display_index=1, status_dir=sd)
    svc._backend = svc._backend
    adapter = LCDAdapter(svc)

    mask = np.zeros((3, 60), dtype=np.uint8)
    adapter.show_physical_mask(mask, mask_id="bars_0234")

    state = read_lcd_state(sd)
    assert state is not None
    assert state["current_mask_id"] == "bars_0234"


def test_fake_lcd_stores_mask_id():
    from tasks.testing import FakeLCD

    lcd = FakeLCD()
    mask = np.zeros((60, 180), dtype=np.uint8)
    lcd.show_physical_mask(mask, mask_id="fake_id")
    assert lcd.last_mask_id == "fake_id"


def test_hardware_lcd_service_gets_status_dir(monkeypatch, tmp_path: Path):
    """Hardware CLI path constructs LCDService(status_dir=status_dir)."""
    sd = tmp_path / "status"
    passed = {}

    class _CapturingLCD:
        def __init__(self, *, display_index=None, subpixel_axis=None, status_dir=None, **kwargs):
            passed["status_dir"] = status_dir
            passed["called"] = True

    monkeypatch.setattr("devices.lcd_service.LCDService", _CapturingLCD)

    from scripts.capture_forward_dataset import _run_hardware

    class FakeArgs:
        plan = str(tmp_path / "plan.yaml")
        output = str(tmp_path / "out.h5")
        enable_tls = False
        dry_run = False
        no_auto_sidecar = True
        camera_index = 0
        lcd_display_index = 1
        lcd_subpixel_axis = 1
        tls_serial_number = ""

    from tasks.capture_plan import CapturePlan
    plan = CapturePlan.from_dict({
        "plan_id": "wire_test",
        "wavelengths": [{
            "illumination": {
                "mode": "monochromatic",
                "effective_wavelength_nm": 550.0,
                "tls_setpoint_nm": None,
            }
        }],
        "masks": [{"mask_id": "m1"}],
        "camera": {"height": 60, "width": 60, "frames_per_capture": 2},
        "lcd_settle_ms": 0,
    })

    monkeypatch.setattr("devices.camera_service.CameraServiceClient", lambda **kw: _FakeCC())
    monkeypatch.setattr("devices.frame_stream.FrameStreamClient", lambda: None)
    monkeypatch.setattr("capture.frame_capture.FrameCaptureHelper", lambda fs: None)

    with pytest.raises(Exception):
        _run_hardware(FakeArgs(), plan, tmp_path / "out.h5", status_dir=sd, run_id="test")

    assert passed.get("called") is True
    assert str(passed.get("status_dir")) == str(sd)


def test_hardware_camera_adapter_gets_camera_service(monkeypatch, tmp_path: Path):
    """Hardware CLI path wires CameraServiceClient into CameraCaptureAdapter."""
    passed = {}

    class _FakeLCD:
        def __init__(self, **kw): pass
        def get_metadata(self):
            return {
                "display_index": 1,
                "reported_shape": (60, 60, 3),
                "physical_shape": (60, 180),
                "logical_shape": (60, 60),
                "subpixel_axis": 1,
            }

    class _CapturingAdapter:
        def __init__(self, helper, camera_service=None):
            passed["helper"] = helper
            passed["camera_service"] = camera_service

    def _stop_before_capture(**kwargs):
        raise RuntimeError("stop after wiring")

    fake_camera_service = _FakeCC()
    monkeypatch.setattr("devices.camera_service.CameraServiceClient", lambda **kw: fake_camera_service)
    monkeypatch.setattr("devices.lcd_service.LCDService", _FakeLCD)
    monkeypatch.setattr("devices.frame_stream.FrameStreamClient", lambda: object())
    monkeypatch.setattr("capture.frame_capture.FrameCaptureHelper", lambda fs: "helper")
    monkeypatch.setattr("tasks.capture_forward_dataset.CameraCaptureAdapter", _CapturingAdapter)
    monkeypatch.setattr("tasks.capture_forward_dataset.run_capture_forward_dataset", _stop_before_capture)

    from scripts.capture_forward_dataset import _run_hardware

    class FakeArgs:
        enable_tls = False
        no_auto_sidecar = True
        camera_index = 0
        lcd_display_index = 1
        lcd_subpixel_axis = 1
        tls_serial_number = ""
        runtime_mode = None

    from tasks.capture_plan import CapturePlan
    plan = CapturePlan.from_dict({
        "plan_id": "camera_wire_test",
        "wavelengths": [{
            "illumination": {
                "mode": "monochromatic",
                "effective_wavelength_nm": 550.0,
                "tls_setpoint_nm": None,
            }
        }],
        "masks": [{"mask_id": "m1"}],
        "camera": {"height": 60, "width": 60, "frames_per_capture": 2},
        "lcd_settle_ms": 0,
    })

    with pytest.raises(RuntimeError, match="stop after wiring"):
        _run_hardware(FakeArgs(), plan, tmp_path / "out.h5", status_dir=None, run_id=None)

    assert passed["helper"] == "helper"
    assert passed["camera_service"] is fake_camera_service


def test_hardware_tls_service_gets_status_dir(monkeypatch, tmp_path: Path):
    """Hardware CLI path constructs TLSService(status_dir=status_dir)."""
    sd = tmp_path / "status"
    passed = {}

    class _CapturingTLS:
        def __init__(self, *, default_serial_number=None, status_dir=None, **kwargs):
            passed["status_dir"] = status_dir
            passed["called"] = True

    class _FakeLCD:
        def __init__(self, **kw): pass
        def get_metadata(self):
            return {"display_index": 1, "reported_shape": (60, 60, 3),
                    "physical_shape": (60, 180), "logical_shape": (60, 60), "subpixel_axis": 1}

    monkeypatch.setattr("devices.lcd_service.LCDService", _FakeLCD)
    monkeypatch.setattr("devices.tls_service.TLSService", _CapturingTLS)
    monkeypatch.setattr("devices.camera_service.CameraServiceClient", lambda **kw: _FakeCC())
    monkeypatch.setattr("devices.frame_stream.FrameStreamClient", lambda: None)
    monkeypatch.setattr("capture.frame_capture.FrameCaptureHelper", lambda fs: None)

    from scripts.capture_forward_dataset import _run_hardware

    class FakeArgs:
        plan = str(tmp_path / "plan.yaml")
        output = str(tmp_path / "out.h5")
        enable_tls = True
        dry_run = False
        no_auto_sidecar = True
        camera_index = 0
        lcd_display_index = 1
        lcd_subpixel_axis = 1
        tls_serial_number = "FAKE"

    from tasks.capture_plan import CapturePlan
    plan = CapturePlan.from_dict({
        "plan_id": "tls_wire_test",
        "wavelengths": [{
            "illumination": {
                "mode": "monochromatic",
                "effective_wavelength_nm": 550.0,
                "tls_setpoint_nm": 550.0,
            }
        }],
        "masks": [{"mask_id": "m1"}],
        "camera": {"height": 60, "width": 60, "frames_per_capture": 2},
        "lcd_settle_ms": 0,
    })

    with pytest.raises(Exception):
        _run_hardware(FakeArgs(), plan, tmp_path / "out.h5", status_dir=sd, run_id="test")

    assert passed.get("called") is True
    assert str(passed.get("status_dir")) == str(sd)


class _FakeCC:
    def __init__(self, **kw): pass
    def open_camera(self, **kw): return {"serial": "fake", "width": 60, "height": 60}
    def start_stream(self): pass
    def __enter__(self): return self
    def __exit__(self, *a): pass
