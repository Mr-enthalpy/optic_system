from __future__ import annotations

import json
from enum import Enum
from types import SimpleNamespace

import numpy as np
import pytest

from devices import camera_backend_flycapture2 as backend_mod
from devices import camera_frame_layout as layout_mod
from devices import camera_protocol as protocol_mod
from devices import camera_service_impl as impl


class PropertyName(Enum):
    SHUTTER = 12
    FRAME_RATE = 15


def _fake_property_snapshot():
    info = SimpleNamespace(
        property_type=PropertyName.SHUTTER,
        present=True,
        read_out_supported=True,
        manual_supported=True,
        auto_supported=True,
        on_off_supported=True,
        one_push_supported=False,
        abs_val_supported=True,
        writable=True,
        min_value=0,
        max_value=4095,
        abs_min=0.01,
        abs_max=1000.0,
        units="ms",
        unit_abbr="ms",
    )
    value = SimpleNamespace(
        property_type=PropertyName.SHUTTER,
        present=True,
        abs_control=True,
        one_push=False,
        on_off=True,
        auto_manual_mode=False,
        value_a=0,
        value_b=0,
        abs_value=5.0,
    )
    return SimpleNamespace(property_type=PropertyName.SHUTTER, info=info, value=value, present=True, error=None)


def test_preconfig_gui_is_deprecated_json_reply() -> None:
    reply = protocol_mod.deprecated_preconfig_gui_reply()

    assert reply["ok"] is False
    assert "deprecated" in reply["err"]
    assert "DisableTrigger" in reply["replacement_ops"]
    json.dumps(reply)


def test_property_snapshot_conversion_is_json_safe() -> None:
    payload = protocol_mod.property_snapshot_to_dict(_fake_property_snapshot())

    assert payload["name"] == "SHUTTER"
    assert payload["present"] is True
    assert payload["abs_val_supported"] is True
    assert payload["abs_value"] == 5.0
    assert payload["display_value"] == 5.0
    assert payload["display_range"] == [0.01, 1000.0]
    assert payload["readback_policy"] == "abs_value"
    assert payload["auto"] is False
    json.dumps(payload)


def test_frame_metadata_builder_raw8_raw16_and_rgb() -> None:
    raw8 = layout_mod.frame_layout_from_array(np.zeros((3, 4), dtype=np.uint8), pixel_format="RAW8")
    raw8_meta = layout_mod.build_frame_metadata(raw8, index=0, seq=1, ts_ns=10)

    assert raw8_meta["protocol_version"] == 2
    assert raw8_meta["backend"] == "flycapture2_c"
    assert raw8_meta["shape"] == [3, 4]
    assert raw8_meta["dtype"] == "uint8"
    assert raw8_meta["row_bytes"] == 4
    assert raw8_meta["frame_nbytes"] == 12
    assert raw8_meta["format"] == "raw8"

    raw16 = layout_mod.frame_layout_from_array(np.zeros((3, 4), dtype=np.uint16), pixel_format="RAW16")
    raw16_meta = layout_mod.build_frame_metadata(raw16, index=1, seq=2, ts_ns=20)
    assert raw16_meta["shape"] == [3, 4]
    assert raw16_meta["dtype"] == "uint16"
    assert raw16_meta["row_bytes"] == 8
    assert raw16_meta["frame_nbytes"] == 24
    assert raw16_meta["format"] == "raw16"

    rgb = layout_mod.frame_layout_from_array(np.zeros((2, 3, 3), dtype=np.uint8), pixel_format="RGB8")
    rgb_meta = layout_mod.build_frame_metadata(rgb, index=2, seq=3, ts_ns=30)
    assert rgb_meta["shape"] == [2, 3, 3]
    assert rgb_meta["dtype"] == "uint8"
    assert rgb_meta["row_bytes"] == 9
    assert rgb_meta["frame_nbytes"] == 18
    assert rgb_meta["format"] == "rgb8"
    json.dumps(rgb_meta)

    inferred_rgb = layout_mod.frame_layout_from_array(np.zeros((2, 3, 3), dtype=np.uint8))
    assert inferred_rgb.pixel_format == "RGB8"
    assert inferred_rgb.format == "rgb8"


def test_close_camera_and_shutdown_have_distinct_service_semantics() -> None:
    state = impl.CameraServiceState()

    close_reply = impl.handle_request(state, {"op": "CloseCamera"})
    assert close_reply["ok"] is True
    assert close_reply["service_running"] is True
    assert close_reply["shm_released"] is True
    assert close_reply["cleanup_errors"] == []
    assert not state.stop_event.is_set()

    shutdown_reply = impl.handle_request(state, {"op": "Shutdown"})
    assert shutdown_reply["ok"] is True
    assert shutdown_reply["service_running"] is False
    assert shutdown_reply["shm_released"] is True
    assert shutdown_reply["cleanup_errors"] == []
    assert state.stop_event.is_set()


def test_start_stop_stream_are_idempotent() -> None:
    state = impl.CameraServiceState()
    state.cam = SimpleNamespace(is_capturing=True)
    state.shm = object()
    state.layout = layout_mod.frame_layout_from_array(np.zeros((2, 2), dtype=np.uint8), pixel_format="RAW8")

    first_start = impl.handle_request(state, {"op": "StartStream"})
    second_start = impl.handle_request(state, {"op": "StartStream"})
    first_stop = impl.handle_request(state, {"op": "StopStream"})
    second_stop = impl.handle_request(state, {"op": "StopStream"})

    assert first_start["ok"] is True
    assert second_start["ok"] is True
    assert first_stop["ok"] is True
    assert second_stop["ok"] is True
    assert state.running is False


def test_frame_rate_get_value_uses_absolute_readback_when_abs_control_is_false() -> None:
    class Backend:
        def get_property_info(self, name: str):
            assert name == "FRAME_RATE"
            return SimpleNamespace(
                property_type=PropertyName.FRAME_RATE,
                present=True,
                read_out_supported=True,
                manual_supported=True,
                auto_supported=True,
                on_off_supported=False,
                one_push_supported=False,
                abs_val_supported=True,
                writable=True,
                min_value=480,
                max_value=4095,
                abs_min=1.0,
                abs_max=75.47169494628906,
                units="Frames Per Second",
                unit_abbr="fps",
            )

        def get_property_value(self, name: str):
            assert name == "FRAME_RATE"
            return SimpleNamespace(
                property_type=PropertyName.FRAME_RATE,
                present=True,
                abs_control=False,
                one_push=False,
                on_off=True,
                auto_manual_mode=False,
                value_a=1811,
                value_b=0,
                abs_value=20.004396438598633,
            )

    state = impl.CameraServiceState()
    state.cam = Backend()

    value_reply = impl.handle_request(state, {"op": "GetValue", "name": "FRAME_RATE"})
    range_reply = impl.handle_request(state, {"op": "GetRange", "name": "FRAME_RATE"})

    assert value_reply["ok"] is True
    assert value_reply["value"] == pytest.approx(20.004396438598633)
    assert value_reply["display_value"] == pytest.approx(20.004396438598633)
    assert value_reply["property"]["value_a"] == 1811
    assert value_reply["property"]["abs_control"] is False
    assert value_reply["readback_policy"] == "abs_value"
    assert value_reply["units"] == "Frames Per Second"

    assert range_reply["ok"] is True
    assert range_reply["range"] == pytest.approx([1.0, 75.47169494628906])
    assert range_reply["display_range"] == pytest.approx([1.0, 75.47169494628906])
    assert range_reply["integer_range"] == [480, 4095]
    assert range_reply["readback_policy"] == "abs_value"


def test_import_failure_open_camera_error_is_readable(monkeypatch) -> None:
    monkeypatch.setenv("OPTIC_SYSTEM_SIDECAR_PYTHON", "custom-python")
    monkeypatch.setenv("FLYCAPTURE2_SDK_DIR", "C:\\FlyCapture2")
    monkeypatch.setenv("FLYCAPTURE2_DLL_DIR", "C:\\FlyCapture2\\bin64")
    monkeypatch.setattr(backend_mod, "FlyCapture2Camera", None)
    monkeypatch.setattr(backend_mod, "_FLYCAPTURE2_IMPORT_ERROR", ImportError("No module named flycapture2_c"))
    state = impl.CameraServiceState(camera_cls=None)

    reply = impl.handle_request(state, {"op": "OpenCamera", "index": 0})

    assert reply["ok"] is False
    assert reply["primary_error_type"] == "ImportError"
    assert "flycapture2_c" in reply["primary_error"]
    assert "Mr-enthalpy/flycapture2_c" in reply["primary_error"]
    assert "sys.executable" in reply["primary_error"]
    assert "sys.version" in reply["primary_error"]
    assert "sys.path" in reply["primary_error"]
    assert "PYTHONPATH" in reply["primary_error"]
    assert "OPTIC_SYSTEM_SIDECAR_PYTHON='custom-python'" in reply["primary_error"]
    assert "FLYCAPTURE2_SDK_DIR='C:\\\\FlyCapture2'" in reply["primary_error"]
    assert "FLYCAPTURE2_DLL_DIR='C:\\\\FlyCapture2\\\\bin64'" in reply["primary_error"]
    assert "flycapture2_c import error" in reply["primary_error"]
    assert "PY38_BIN" not in reply["primary_error"]


def test_sdk_error_reply_includes_diagnostics_by_class_name(monkeypatch) -> None:
    monkeypatch.setenv("FLYCAPTURE2_SDK_DIR", "D:\\FlyCapture2")
    monkeypatch.setenv("FLYCAPTURE2_DLL_DIR", "D:\\FlyCapture2\\bin64\\vs2015")

    class SDKNotFoundError(Exception):
        pass

    class FakeCamera:
        calls = []

        @classmethod
        def open(cls, index: int = 0):
            cls.calls.append(("open", index))
            raise SDKNotFoundError("FlyCapture2 SDK headers were not found.")

    state = impl.CameraServiceState(camera_cls=FakeCamera)
    reply = impl.handle_request(state, {"op": "OpenCamera", "index": 0})

    assert reply["ok"] is False
    assert reply["primary_error_type"] == "SDKNotFoundError"
    assert "sdk_diagnostics" in reply
    diag = reply["sdk_diagnostics"]
    assert diag["FLYCAPTURE2_SDK_DIR"] == "D:\\FlyCapture2"
    assert diag["FLYCAPTURE2_DLL_DIR"] == "D:\\FlyCapture2\\bin64\\vs2015"
    assert isinstance(diag["suggested_sdk_dir_examples"], list)
    assert len(diag["suggested_sdk_dir_examples"]) >= 2
    assert "D:" in str(diag["suggested_sdk_dir_examples"][0])
    assert "FlyCapture2" in str(diag["suggested_sdk_dir_examples"][0])


def test_sdk_error_detected_by_message_substring(monkeypatch) -> None:
    monkeypatch.setenv("FLYCAPTURE2_SDK_DIR", "D:\\FlyCapture2")

    class GenericError(Exception):
        pass

    class FakeCamera:
        calls = []

        @classmethod
        def open(cls, index: int = 0):
            cls.calls.append(("open", index))
            raise GenericError("FLYCAPTURE2_SDK_DIR is not set correctly. FlyCapture2 SDK headers were not found.")

    state = impl.CameraServiceState(camera_cls=FakeCamera)
    reply = impl.handle_request(state, {"op": "OpenCamera", "index": 0})

    assert reply["ok"] is False
    assert "sdk_diagnostics" in reply
    diag = reply["sdk_diagnostics"]
    assert diag["FLYCAPTURE2_SDK_DIR"] == "D:\\FlyCapture2"


def test_dll_load_error_reply_includes_diagnostics(monkeypatch) -> None:
    monkeypatch.setenv("FLYCAPTURE2_DLL_DIR", "D:\\FlyCapture2\\bin64\\vs2015")

    class DLLLoadError(Exception):
        pass

    class FakeCamera:
        calls = []

        @classmethod
        def open(cls, index: int = 0):
            cls.calls.append(("open", index))
            raise DLLLoadError("FlyCapture2 DLL load failed.")

    state = impl.CameraServiceState(camera_cls=FakeCamera)
    reply = impl.handle_request(state, {"op": "OpenCamera", "index": 0})

    assert reply["ok"] is False
    assert reply["primary_error_type"] == "DLLLoadError"
    assert "sdk_diagnostics" in reply
    diag = reply["sdk_diagnostics"]
    assert diag["FLYCAPTURE2_DLL_DIR"] == "D:\\FlyCapture2\\bin64\\vs2015"
    assert isinstance(diag["suggested_dll_dir_examples"], list)


def test_non_sdk_error_does_not_include_diagnostics(monkeypatch) -> None:
    monkeypatch.setenv("FLYCAPTURE2_SDK_DIR", "D:\\FlyCapture2")

    class UnrelatedError(Exception):
        pass

    class FakeCamera:
        calls = []

        @classmethod
        def open(cls, index: int = 0):
            cls.calls.append(("open", index))
            raise UnrelatedError("Something else went wrong.")

    state = impl.CameraServiceState(camera_cls=FakeCamera)
    reply = impl.handle_request(state, {"op": "OpenCamera", "index": 0})

    assert reply["ok"] is False
    assert "sdk_diagnostics" not in reply


def test_known_but_undecoded_pixel_format_is_rejected_before_configuration(monkeypatch) -> None:
    def fake_support(pixel_format):
        return SimpleNamespace(
            name=str(pixel_format),
            read_frame_decodable=False,
            raw_copy_only=True,
            compressed_or_unsupported=False,
        )

    monkeypatch.setattr(backend_mod, "_fc2_support_for_pixel_format", fake_support)

    class Backend:
        changed = False
        layout = layout_mod.frame_layout_from_array(np.zeros((2, 2), dtype=np.uint8), pixel_format="RAW8")

        def validate_config(self, *, pixel_format=None, roi=None):
            backend_mod.require_read_frame_decodable_pixel_format(pixel_format)
            return {}

        def stop_capture(self):
            pass

        def start_capture(self):
            pass

        def apply_config(self, **kwargs):
            self.changed = True
            raise AssertionError("should not configure")

        def _read_frame(self):
            return layout_mod.CapturedFrame(
                array=np.zeros((2, 2), dtype=np.uint8),
                width=2,
                height=2,
                stride=2,
                pixel_format="RAW8",
            )

        def _discover_setting_names(self):
            return []

    state = impl.CameraServiceState()
    backend = Backend()
    state.cam = backend
    state.layout = backend.layout
    state.shm = object()

    reply = impl.handle_request(state, {"op": "SetPixelFormat", "pixel_format": "BGR"})

    assert reply["ok"] is False
    assert reply["error_type"] == "UnsupportedOperationError"
    assert "not decodable by read_frame" in reply["err"]
    assert backend.changed is False


class FakeCamera:
    calls = []

    def __init__(self) -> None:
        self.is_open = True
        self.is_capturing = False

    @classmethod
    def open(cls, index: int = 0):
        cls.calls.append(("open", index))
        return cls()

    def get_camera_info(self, refresh: bool = False):
        self.calls.append(("get_camera_info", refresh))
        return SimpleNamespace(
            serial_number=123,
            model_name="FakeCam",
            vendor_name="FakeVendor",
            sensor_info="FakeSensor",
            sensor_resolution="4x3",
            firmware_version="1.0",
            interface_type="mock",
        )

    def get_trigger_mode_info(self):
        self.calls.append(("get_trigger_mode_info",))
        return SimpleNamespace(present=True)

    def get_configuration(self):
        self.calls.append(("get_configuration",))
        return SimpleNamespace(grab_timeout=500)

    def get_format7_info(self, mode: int = 0):
        self.calls.append(("get_format7_info", mode))
        return SimpleNamespace(supported=True, supported_pixel_formats=())

    def get_format7_configuration(self):
        self.calls.append(("get_format7_configuration",))
        return SimpleNamespace(
            settings=SimpleNamespace(
                mode=0,
                offset_x=0,
                offset_y=0,
                width=4,
                height=3,
                pixel_format="RAW8",
            )
        )

    def validate_format7(self, **kwargs):
        self.calls.append(("validate_format7", kwargs))
        return SimpleNamespace(settings_are_valid=True, settings=kwargs)

    def get_trigger_mode(self):
        self.calls.append(("get_trigger_mode",))
        return SimpleNamespace(on_off=True, polarity=0, source=0, mode=0, parameter=0)

    def disable_trigger(self):
        self.calls.append(("disable_trigger",))
        return SimpleNamespace(on_off=False, polarity=0, source=0, mode=0, parameter=0)

    def set_grab_timeout(self, ms: int):
        self.calls.append(("set_grab_timeout", ms))

    def set_pixel_format(self, pixel_format, mode: int = 0):
        self.calls.append(("set_pixel_format", pixel_format, mode))

    def set_roi(self, **kwargs):
        self.calls.append(("set_roi", kwargs))

    def set_property_abs(self, name, value, auto=False, on=None):
        self.calls.append(("set_property_abs", name, value, auto, on))
        return SimpleNamespace(
            property_type=PropertyName.SHUTTER,
            present=True,
            abs_control=True,
            one_push=False,
            on_off=True,
            auto_manual_mode=auto,
            value_a=0,
            value_b=0,
            abs_value=value,
        )

    def snapshot_properties(self):
        self.calls.append(("snapshot_properties",))
        return (_fake_property_snapshot(),)

    def start(self):
        self.calls.append(("start",))
        self.is_capturing = True

    def read_frame_with_info(self):
        self.calls.append(("read_frame_with_info",))
        return layout_mod.CapturedFrame(
            array=np.zeros((3, 4), dtype=np.uint8),
            width=4,
            height=3,
            stride=4,
            pixel_format="RAW8",
        )

    def stop(self):
        self.calls.append(("stop",))
        self.is_capturing = False

    def close(self):
        self.calls.append(("close",))
        self.is_open = False


def test_open_camera_with_fake_backend_applies_scriptable_configuration(monkeypatch) -> None:
    created: list[FakeSharedMemory] = []
    released: list[FakeSharedMemory] = []

    def fake_create(size: int):
        shm = FakeSharedMemory(size)
        created.append(shm)
        return shm

    def fake_release(shm):
        if shm is not None:
            released.append(shm)
            shm.close()
            shm.unlink()

    monkeypatch.setattr(impl, "_create_shm", fake_create)
    monkeypatch.setattr(impl, "_release_shm", fake_release)

    FakeCamera.calls = []
    state = impl.CameraServiceState(camera_cls=FakeCamera)
    try:
        reply = impl.handle_request(
            state,
            {
                "op": "OpenCamera",
                "index": 0,
                "disable_trigger": True,
                "grab_timeout_ms": 1000,
                "pixel_format": "RAW8",
                "roi": {"offset_x": 0, "offset_y": 0, "width": 4, "height": 3},
                "properties": [{"name": "SHUTTER", "value": 5.0, "auto": False}],
            },
        )

        assert reply["ok"] is True
        assert reply["backend"] == "flycapture2_c"
        assert reply["pixel_format"] == "RAW8"
        assert reply["shape"] == [3, 4]
        assert ("disable_trigger",) in FakeCamera.calls
        assert ("set_grab_timeout", 1000) in FakeCamera.calls
        assert ("set_pixel_format", "RAW8", 0) in FakeCamera.calls
        assert any(call[0] == "set_roi" for call in FakeCamera.calls)
        assert ("set_property_abs", "SHUTTER", 5.0, False, None) in FakeCamera.calls
        assert ("start",) in FakeCamera.calls
        assert ("read_frame_with_info",) in FakeCamera.calls
    finally:
        impl.handle_request(state, {"op": "CloseCamera"})


def test_open_camera_without_explicit_disable_trigger_does_not_change_trigger(monkeypatch) -> None:
    monkeypatch.setattr(impl, "_create_shm", lambda size: FakeSharedMemory(size))
    monkeypatch.setattr(impl, "_release_shm", lambda shm: None)

    FakeCamera.calls = []
    state = impl.CameraServiceState(camera_cls=FakeCamera)
    try:
        reply = impl.handle_request(state, {"op": "OpenCamera", "index": 0})

        assert reply["ok"] is True
        assert ("disable_trigger",) not in FakeCamera.calls
        assert reply["configuration_applied"]["disable_trigger"]["requested"] is False
        assert reply["configuration_applied"]["disable_trigger"]["applied"] is False
    finally:
        impl.handle_request(state, {"op": "CloseCamera"})


class FakeSharedMemory:
    def __init__(self, size: int = 0) -> None:
        self.buf = bytearray(size)
        self.closed = False
        self.unlinked = False

    def close(self) -> None:
        self.closed = True

    def unlink(self) -> None:
        self.unlinked = True


def test_reconfigure_layout_change_reports_old_new_layout_and_recreates_shm(monkeypatch) -> None:
    old_layout = layout_mod.frame_layout_from_array(np.zeros((2, 2), dtype=np.uint8), pixel_format="RAW8")
    new_frame = layout_mod.CapturedFrame(
        array=np.zeros((3, 4), dtype=np.uint16),
        width=4,
        height=3,
        stride=8,
        pixel_format="RAW16",
    )
    created: list[FakeSharedMemory] = []
    released: list[FakeSharedMemory] = []

    def fake_create(size: int):
        shm = FakeSharedMemory(size)
        created.append(shm)
        return shm

    def fake_release(shm):
        if shm is not None:
            released.append(shm)
            shm.close()
            shm.unlink()

    class Backend:
        is_capturing = True
        layout = old_layout

        def validate_config(self, *, pixel_format=None, roi=None):
            return {"format7": {"settings_are_valid": True}}

        def stop_capture(self):
            self.is_capturing = False

        def start_capture(self):
            self.is_capturing = True

        def apply_config(self, **kwargs):
            self.applied = kwargs

        def _read_frame(self):
            return new_frame

        def _discover_setting_names(self):
            return []

    monkeypatch.setattr(impl, "_create_shm", fake_create)
    monkeypatch.setattr(impl, "_release_shm", fake_release)

    state = impl.CameraServiceState()
    state.cam = Backend()
    state.layout = old_layout
    state.shm = FakeSharedMemory(old_layout.frame_nbytes * impl.RING)
    state.running = True
    published: list[dict] = []

    reply = impl.handle_request(
        state,
        {"op": "ReconfigureCamera", "pixel_format": "RAW16"},
        publish_status=published.append,
    )

    assert reply["ok"] is True
    assert reply["old_layout"]["format"] == "raw8"
    assert reply["new_layout"]["format"] == "raw16"
    assert reply["layout_changed"] is True
    assert reply["shm_recreated"] is True
    assert created and state.shm is created[-1]
    assert released and released[0].unlinked is True
    assert published and published[-1]["event"] == "stream_layout_changed"


def test_close_camera_releases_shared_memory_and_shutdown_sets_stop_event() -> None:
    class Backend:
        is_capturing = True
        closed = False
        cleanup_errors = ()

        def close(self):
            self.closed = True

    state = impl.CameraServiceState()
    shm = FakeSharedMemory(16)
    backend = Backend()
    state.cam = backend
    state.shm = shm
    state.layout = layout_mod.frame_layout_from_array(np.zeros((2, 2), dtype=np.uint8), pixel_format="RAW8")
    state.running = True

    close_reply = impl.handle_request(state, {"op": "CloseCamera"})

    assert close_reply["ok"] is True
    assert close_reply["service_running"] is True
    assert close_reply["shm_released"] is True
    assert close_reply["cleanup_errors"] == []
    assert shm.closed is True
    assert shm.unlinked is True
    assert state.shm is None
    assert state.cam is None
    assert not state.stop_event.is_set()

    shutdown_reply = impl.handle_request(state, {"op": "Shutdown"})
    assert shutdown_reply["ok"] is True
    assert shutdown_reply["service_running"] is False
    assert shutdown_reply["shm_released"] is True
    assert shutdown_reply["cleanup_errors"] == []
    assert state.stop_event.is_set()


#  New tests for lifecycle hardening


def test_mycam_lite_close_does_not_call_stop() -> None:
    closed = False
    stop_called = False

    class FakeCam:
        is_capturing = False
        cleanup_errors = ()

        def close(self):
            nonlocal closed
            closed = True

        def stop(self):
            nonlocal stop_called
            stop_called = True

    cam = backend_mod.MyCamLite(FakeCam(), index=0)
    cam.close()

    assert closed
    assert not stop_called
    assert cam.cleanup_errors == []


def test_mycam_lite_close_collects_cleanup_errors() -> None:
    class FakeCam:
        is_capturing = False
        cleanup_errors = (ValueError("stop failed"), RuntimeError("disconnect failed"))

        def close(self):
            pass

    cam = backend_mod.MyCamLite(FakeCam(), index=0)
    cam.close()

    assert len(cam.cleanup_errors) == 2
    assert "stop failed" in cam.cleanup_errors[0]
    assert "disconnect failed" in cam.cleanup_errors[1]


def test_mycam_lite_close_noops_when_cam_is_none() -> None:
    cam = backend_mod.MyCamLite(None, index=0)
    cam.close()
    assert cam.cleanup_errors == []


def test_stop_capture_noops_when_not_capturing() -> None:
    stop_called = False

    class FakeCam:
        is_capturing = False

        def stop(self):
            nonlocal stop_called
            stop_called = True

    cam = backend_mod.MyCamLite(FakeCam(), index=0)
    cam.stop_capture()

    assert not stop_called


def test_stop_capture_noops_when_cam_is_none() -> None:
    cam = backend_mod.MyCamLite(None, index=0)
    cam.stop_capture()


def test_stop_capture_calls_stop_when_capturing() -> None:
    stop_called = False

    class FakeCam:
        is_capturing = True

        def stop(self):
            nonlocal stop_called
            stop_called = True

    cam = backend_mod.MyCamLite(FakeCam(), index=0)
    cam.stop_capture()

    assert stop_called


def test_close_camera_locked_suppresses_cleanup_exceptions() -> None:
    class FakeCam:
        cleanup_errors = ("cleanup warning from camera",)

        def close(self):
            raise RuntimeError("cleanup failure in close")

    state = impl.CameraServiceState()
    state.cam = FakeCam()
    state.shm = FakeSharedMemory(16)
    state.layout = layout_mod.frame_layout_from_array(np.zeros((2, 2), dtype=np.uint8), pixel_format="RAW8")
    state.running = True

    cleanup_errors = impl._close_camera_locked(state)

    assert len(cleanup_errors) >= 2
    assert any("cleanup failure in close" in e for e in cleanup_errors)
    assert any("cleanup warning from camera" in e for e in cleanup_errors)
    assert state.cam is None
    assert state.shm is None
    assert state.layout is None
    assert not state.running


def test_close_camera_locked_returns_camera_cleanup_errors() -> None:
    class FakeCam:
        is_capturing = False
        cleanup_errors = ("fc2StopCapture failed: INVALID_GENERATION (20)",)

        def close(self):
            pass

    state = impl.CameraServiceState()
    state.cam = FakeCam()
    state.shm = FakeSharedMemory(16)

    cleanup_errors = impl._close_camera_locked(state)

    assert "INVALID_GENERATION" in " ".join(cleanup_errors)
    assert state.cam is None


def test_close_camera_response_includes_cleanup_errors() -> None:
    class FakeCam:
        is_capturing = False
        cleanup_errors = ("cleanup warning",)

        def close(self):
            pass

    state = impl.CameraServiceState()
    state.cam = FakeCam()
    state.shm = FakeSharedMemory(16)

    reply = impl.handle_request(state, {"op": "CloseCamera"})

    assert reply["ok"] is True
    assert reply["shm_released"] is True
    assert "cleanup_errors" in reply
    assert "cleanup warning" in " ".join(reply["cleanup_errors"])


def test_shutdown_response_includes_cleanup_errors() -> None:
    class FakeCam:
        is_capturing = False
        cleanup_errors = ("cleanup warning",)

        def close(self):
            pass

    state = impl.CameraServiceState()
    state.cam = FakeCam()
    state.shm = FakeSharedMemory(16)

    reply = impl.handle_request(state, {"op": "Shutdown"})

    assert reply["ok"] is True
    assert reply["shm_released"] is True
    assert reply["service_running"] is False
    assert "cleanup_errors" in reply
    assert "cleanup warning" in " ".join(reply["cleanup_errors"])


def test_open_camera_pre_cleanup_warning_not_primary_error(monkeypatch) -> None:
    monkeypatch.setattr(backend_mod, "FlyCapture2Camera", None)

    class FakeCleanupCam:
        is_capturing = True
        is_open = True
        cleanup_errors = ("fc2StopCapture failed: INVALID_GENERATION (20) - Invalid generation error",)

        def close(self):
            pass

    original_open = backend_mod.MyCamLite.open

    @classmethod
    def fake_open(cls, **kwargs):
        raise RuntimeError("primary failure")

    monkeypatch.setattr(backend_mod.MyCamLite, "open", fake_open)

    state = impl.CameraServiceState()
    state.cam = FakeCleanupCam()
    state.shm = FakeSharedMemory(16)

    reply = impl.handle_request(state, {"op": "OpenCamera", "index": 0})

    assert reply["ok"] is False
    assert reply["primary_error"] == "primary failure"
    assert "INVALID_GENERATION" in " ".join(reply["cleanup_errors"])
    assert "primary_error_type" in reply


def test_open_camera_success_with_cleanup_warnings(monkeypatch) -> None:
    monkeypatch.setattr(backend_mod, "FlyCapture2Camera", None)

    class FakeCleanupCam:
        is_capturing = False
        is_open = False
        cleanup_errors = ("previous cleanup warning",)

        def close(self):
            pass

    class FakeCam:
        is_capturing = True
        is_open = True
        cleanup_errors = ()

        @classmethod
        def open(cls, index: int = 0):
            return cls()

        def get_camera_info(self, refresh=False):
            return SimpleNamespace(serial_number=999, model_name="TestCam")

        def get_trigger_mode_info(self):
            return SimpleNamespace(present=True)

        def get_configuration(self):
            return SimpleNamespace(grab_timeout=500)

        def get_format7_info(self, mode=0):
            return SimpleNamespace(supported=True, supported_pixel_formats=())

        def get_format7_configuration(self):
            return SimpleNamespace(
                settings=SimpleNamespace(mode=0, offset_x=0, offset_y=0, width=4, height=3, pixel_format="RAW8")
            )

        def get_trigger_mode(self):
            return SimpleNamespace(on_off=False, polarity=0, source=0, mode=0, parameter=0)

        def disable_trigger(self):
            return SimpleNamespace(on_off=False, polarity=0, source=0, mode=0, parameter=0)

        def start(self):
            self.is_capturing = True

        def read_frame_with_info(self):
            return layout_mod.CapturedFrame(
                array=np.zeros((3, 4), dtype=np.uint8),
                width=4,
                height=3,
                stride=4,
                pixel_format="RAW8",
            )

        def snapshot_properties(self):
            return ()

        def close(self):
            pass

    monkeypatch.setattr(impl, "_create_shm", lambda size: FakeSharedMemory(size))
    monkeypatch.setattr(impl, "_release_shm", lambda shm: None)

    state = impl.CameraServiceState(camera_cls=FakeCam)
    state.cam = FakeCleanupCam()
    state.shm = FakeSharedMemory(16)
    state.layout = layout_mod.frame_layout_from_array(np.zeros((2, 2), dtype=np.uint8), pixel_format="RAW8")

    reply = impl.handle_request(state, {"op": "OpenCamera", "index": 0})

    assert reply["ok"] is True
    assert "cleanup_warnings" in reply
    assert "previous cleanup warning" in " ".join(reply["cleanup_warnings"])
    assert reply["cleanup_errors"] == []
    assert reply["width"] == 4
    assert reply["height"] == 3


def test_open_camera_structured_failure_response(monkeypatch) -> None:
    monkeypatch.setattr(backend_mod, "FlyCapture2Camera", None)
    monkeypatch.setattr(backend_mod, "_FLYCAPTURE2_IMPORT_ERROR", ImportError("No module named flycapture2_c"))

    state = impl.CameraServiceState(camera_cls=None)
    reply = impl.handle_request(state, {"op": "OpenCamera", "index": 0})

    assert reply["ok"] is False
    assert reply["stage"] == "open_device"
    assert "primary_error" in reply
    assert "primary_error_type" in reply
    assert reply["primary_error_type"] == "ImportError"
    assert "cleanup_errors" in reply
    assert "camera_state" in reply
    assert "flycapture2_c_file" in reply
    assert "camera_class_file" in reply
    assert "has_cleanup_errors" in reply


def test_ping_includes_backend_diagnostics() -> None:
    state = impl.CameraServiceState()
    reply = impl.handle_request(state, {"op": "Ping"})

    assert reply["ok"] is True
    assert reply["backend"] == "flycapture2_c"
    assert "service_file" in reply
    assert "python_executable" in reply
    assert "flycapture2_c_file" in reply
    assert "camera_class_file" in reply
    assert "has_cleanup_errors" in reply


def test_reconfigure_locked_does_not_restart_after_failure() -> None:
    start_calls = []

    class Backend:
        is_capturing = True
        layout = layout_mod.frame_layout_from_array(np.zeros((2, 2), dtype=np.uint8), pixel_format="RAW8")

        def validate_config(self, **kwargs):
            return {}

        def stop_capture(self):
            pass

        def start_capture(self):
            start_calls.append(True)

        def apply_config(self, **kwargs):
            raise RuntimeError("config apply failed")

        def _read_frame(self):
            pass

    state = impl.CameraServiceState()
    state.cam = Backend()
    state.layout = Backend.layout
    state.shm = FakeSharedMemory(16)
    state.running = True

    with pytest.raises(RuntimeError, match="config apply failed"):
        impl._reconfigure_locked(state, {"pixel_format": "RAW16"})

    assert not state.running
    assert len(start_calls) == 0


def test_reconfigure_stop_only_when_capturing(monkeypatch) -> None:
    monkeypatch.setattr(impl, "_create_shm", lambda size: FakeSharedMemory(size))
    monkeypatch.setattr(impl, "_release_shm", lambda shm: None)

    stop_calls = []

    class Backend:
        is_capturing = False
        layout = layout_mod.frame_layout_from_array(np.zeros((2, 2), dtype=np.uint8), pixel_format="RAW8")

        def validate_config(self, **kwargs):
            return {}

        def stop_capture(self):
            stop_calls.append(True)

        def apply_config(self, **kwargs):
            pass

        def start_capture(self):
            pass

        def _read_frame(self):
            return layout_mod.CapturedFrame(
                array=np.zeros((2, 2), dtype=np.uint8),
                width=2,
                height=2,
                stride=2,
                pixel_format="RAW8",
            )

        def _discover_setting_names(self):
            return []

    state = impl.CameraServiceState()
    state.cam = Backend()
    state.shm = FakeSharedMemory(16)
    state.layout = Backend.layout

    impl._reconfigure_locked(state, {"pixel_format": "RAW8"})

    assert len(stop_calls) == 0


def test_regression_invalid_generation_cleanup_not_primary(monkeypatch) -> None:
    monkeypatch.setattr(backend_mod, "FlyCapture2Camera", None)

    class FakeCleanupCam:
        is_capturing = True
        is_open = True
        cleanup_errors = ("fc2StopCapture failed: INVALID_GENERATION (20) - Invalid generation error",)

        def close(self):
            pass

    @classmethod
    def fake_open(cls, **kwargs):
        raise RuntimeError("primary failure at read_first_frame")

    monkeypatch.setattr(backend_mod.MyCamLite, "open", fake_open)

    state = impl.CameraServiceState()
    state.cam = FakeCleanupCam()
    state.shm = FakeSharedMemory(16)
    state.layout = layout_mod.frame_layout_from_array(np.zeros((2, 2), dtype=np.uint8), pixel_format="RAW8")

    reply = impl.handle_request(state, {"op": "OpenCamera", "index": 0})

    assert reply["ok"] is False
    assert reply["primary_error"] == "primary failure at read_first_frame"
    assert "INVALID_GENERATION" in " ".join(reply["cleanup_errors"])


def test_mycam_lite_init_has_cleanup_errors_field() -> None:
    class FakeCam:
        is_capturing = False
        cleanup_errors = ()

        def close(self):
            pass

        def stop(self):
            pass

    cam = backend_mod.MyCamLite(FakeCam(), index=0)
    assert hasattr(cam, "cleanup_errors")
    assert cam.cleanup_errors == []
