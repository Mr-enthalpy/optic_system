from __future__ import annotations

import json
from enum import Enum
from types import SimpleNamespace

import numpy as np

from devices import camera_backend_flycapture2 as backend_mod
from devices import camera_frame_layout as layout_mod
from devices import camera_protocol as protocol_mod
from devices import camera_service_impl as impl


class PropertyName(Enum):
    SHUTTER = 12


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
    assert not state.stop_event.is_set()

    shutdown_reply = impl.handle_request(state, {"op": "Shutdown"})
    assert shutdown_reply["ok"] is True
    assert shutdown_reply["service_running"] is False
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


def test_import_failure_open_camera_error_is_readable(monkeypatch) -> None:
    monkeypatch.setattr(backend_mod, "FlyCapture2Camera", None)
    monkeypatch.setattr(backend_mod, "_FLYCAPTURE2_IMPORT_ERROR", ImportError("No module named flycapture2_c"))
    state = impl.CameraServiceState(camera_cls=None)

    reply = impl.handle_request(state, {"op": "OpenCamera", "index": 0})

    assert reply["ok"] is False
    assert reply["error_type"] == "ImportError"
    assert "flycapture2_c" in reply["err"]
    assert "Mr-enthalpy/flycapture2_c" in reply["err"]


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

    def set_property_abs(self, name, value, auto=False):
        self.calls.append(("set_property_abs", name, value, auto))
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


def test_open_camera_with_fake_backend_applies_scriptable_configuration() -> None:
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
        assert ("set_property_abs", "SHUTTER", 5.0, False) in FakeCamera.calls
        assert ("start",) in FakeCamera.calls
        assert ("read_frame_with_info",) in FakeCamera.calls
    finally:
        impl.handle_request(state, {"op": "CloseCamera"})


def test_open_camera_without_explicit_disable_trigger_does_not_change_trigger() -> None:
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
    assert shm.closed is True
    assert shm.unlinked is True
    assert state.shm is None
    assert state.cam is None
    assert not state.stop_event.is_set()

    shutdown_reply = impl.handle_request(state, {"op": "Shutdown"})
    assert shutdown_reply["ok"] is True
    assert shutdown_reply["service_running"] is False
    assert state.stop_event.is_set()
