from __future__ import annotations

import sys

import pytest

from devices.camera_service import CameraServiceClient, _candidate_python_commands


class RecordingCameraServiceClient(CameraServiceClient):
    def __init__(self):
        super().__init__(auto_ensure=False, timeout_ms=1234)
        self.calls = []
        self.replies = {
            "OpenCamera": {"ok": True, "serial": 1, "width": 4, "height": 3, "stride": 4, "format": "raw8"},
            "GetBackendInfo": {"ok": True, "backend": "flycapture2_c"},
            "GetStreamStatus": {"ok": True, "running": False},
            "SnapshotProperties": {"ok": True, "properties": []},
            "GetPropertyInfo": {"ok": True, "info": {"name": "SHUTTER"}},
            "GetRange": {"ok": True, "range": [0.1, 10.0]},
            "GetValue": {"ok": True, "value": 5.0},
            "SetProperty": {"ok": True},
            "SetPropertyAuto": {"ok": True, "property": {"auto": True}},
            "GetTriggerMode": {"ok": True, "trigger": {"on_off": False}},
            "DisableTrigger": {"ok": True, "trigger": {"on_off": False}},
            "SetTriggerMode": {"ok": True, "trigger": {"on_off": True}},
            "GetFormat7Info": {"ok": True, "info": {}},
            "GetFormat7Configuration": {"ok": True, "configuration": {}},
            "ValidateFormat7": {"ok": True, "validation": {}},
            "SetPixelFormat": {"ok": True},
            "SetROI": {"ok": True},
            "SetGrabTimeout": {"ok": True},
            "ReconfigureCamera": {"ok": True},
            "CloseCamera": {"ok": True},
            "Shutdown": {"ok": True},
        }

    def _request(self, op, timeout_ms=object(), **kwargs):
        self.calls.append((op, timeout_ms, kwargs))
        if op == "PreConfigGUI":
            return {
                "ok": False,
                "err": "PreConfigGUI is deprecated. Use explicit camera configuration commands instead.",
                "replacement_ops": ["DisableTrigger"],
            }
        return self.replies.get(op, {"ok": True})


def test_open_camera_sends_explicit_configuration_payload() -> None:
    client = RecordingCameraServiceClient()

    client.open_camera(
        index=2,
        context_type="IIDC",
        disable_trigger=False,
        grab_timeout_ms=1000,
        pixel_format="RAW8",
        roi={"offset_x": 4, "offset_y": 6, "width": 320, "height": 240},
        properties=[{"name": "SHUTTER", "value": 5.0, "auto": False}],
    )

    op, _, payload = client.calls[-1]
    assert op == "OpenCamera"
    assert payload["index"] == 2
    assert payload["context_type"] == "IIDC"
    assert payload["disable_trigger"] is False
    assert payload["grab_timeout_ms"] == 1000
    assert payload["pixel_format"] == "RAW8"
    assert payload["roi"]["width"] == 320
    assert payload["properties"][0]["name"] == "SHUTTER"


def test_default_sidecar_python_command_uses_current_interpreter(monkeypatch) -> None:
    monkeypatch.delenv("OPTIC_SYSTEM_SIDECAR_PYTHON", raising=False)
    monkeypatch.setenv("PY38_BIN", "legacy-python38")

    candidates = _candidate_python_commands()

    assert candidates[0] == (sys.executable,)
    assert ("legacy-python38",) not in candidates


def test_sidecar_python_override_uses_generic_environment_variable(monkeypatch) -> None:
    monkeypatch.setenv("OPTIC_SYSTEM_SIDECAR_PYTHON", "py -3.12")
    monkeypatch.setenv("PY38_BIN", "legacy-python38")

    candidates = _candidate_python_commands()

    assert candidates[0] == ("py", "-3.12")
    assert (sys.executable,) in candidates
    assert ("legacy-python38",) not in candidates


def test_open_camera_omits_disable_trigger_unless_explicit() -> None:
    client = RecordingCameraServiceClient()

    client.open_camera(index=0)

    op, _, payload = client.calls[-1]
    assert op == "OpenCamera"
    assert "disable_trigger" not in payload


def test_open_camera_gui_is_deprecated_and_uses_finite_timeout() -> None:
    client = RecordingCameraServiceClient()

    with pytest.raises(RuntimeError) as exc_info:
        client.open_camera_gui()

    op, timeout_ms, _payload = client.calls[-1]
    assert op == "PreConfigGUI"
    assert timeout_ms == client.timeout_ms
    assert "PreConfigGUI is deprecated" in str(exc_info.value)


def test_client_exposes_new_protocol_operations() -> None:
    client = RecordingCameraServiceClient()

    client.get_backend_info()
    client.get_stream_status()
    client.snapshot_properties()
    client.get_property_info("SHUTTER")
    client.get_range_info("SHUTTER")
    client.get_range("SHUTTER")
    client.get_value("SHUTTER")
    client.set_value("SHUTTER", 5.0)
    client.set_property_auto("SHUTTER", False)
    client.get_trigger_mode()
    client.disable_trigger()
    client.set_trigger_mode(on_off=False, source=0, mode=0, polarity=1, parameter=0)
    client.get_format7_info(mode=0)
    client.get_format7_configuration()
    client.validate_format7(mode=0, width=320, height=240, pixel_format="RAW8")
    client.set_pixel_format("RAW8", mode=0)
    client.set_roi(offset_x=0, offset_y=0, width=320, height=240, mode=0)
    client.set_grab_timeout(1000)
    client.reconfigure_camera(pixel_format="RAW8", roi={"width": 320, "height": 240})
    client.close_camera()
    client.shutdown_sidecar()

    ops = [op for op, _timeout, _payload in client.calls]
    assert "GetBackendInfo" in ops
    assert "SnapshotProperties" in ops
    assert "SetPropertyAuto" in ops
    assert "DisableTrigger" in ops
    assert "SetROI" in ops
    assert "ReconfigureCamera" in ops
    assert ops[-2:] == ["CloseCamera", "Shutdown"]
