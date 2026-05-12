from __future__ import annotations

import pytest

from control.session_controller import SessionController, is_gui_editable_camera_setting


class FakeCameraService:
    def __init__(self, *, deprecated_gui_error: bool = False) -> None:
        self.deprecated_gui_error = deprecated_gui_error
        self.open_camera_gui_calls: list[dict] = []
        self.open_camera_calls: list[dict] = []
        self.started_stream = False
        self.closed = False

    def open_camera_gui(self, **kwargs):
        self.open_camera_gui_calls.append(kwargs)
        if self.deprecated_gui_error:
            raise RuntimeError("PreConfigGUI is deprecated. Use explicit camera configuration commands instead.")

    def open_camera(self, **kwargs):
        self.open_camera_calls.append(kwargs)
        return {
            "serial": 123,
            "width": 4,
            "height": 3,
            "stride": 4,
            "format": "raw8",
        }

    def get_connection_status(self):
        return {
            "sidecar_running": True,
            "own_sidecar": False,
            "sidecar_pid": None,
            "rep_addr": "tcp://127.0.0.1:6101",
        }

    def get_camera_info(self):
        return {
            "serial": 123,
            "width": 4,
            "height": 3,
            "pix_fmt": "raw8",
            "setting_names": [],
        }

    def start_stream(self):
        self.started_stream = True

    def stop_stream(self):
        self.started_stream = False

    def close_camera(self):
        pass

    def close(self):
        self.closed = True


class FakePreviewWorker:
    def __init__(self) -> None:
        self.on_frame = None
        self.on_error = None
        self.started = False

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.started = False


def test_session_controller_start_default_does_not_call_preconfigure_gui() -> None:
    camera_service = FakeCameraService()
    preview_worker = FakePreviewWorker()
    controller = SessionController(camera_service=camera_service, preview_worker=preview_worker)
    try:
        controller.start()

        assert camera_service.open_camera_gui_calls == []
        assert camera_service.open_camera_calls == [
            {"index": 0, "context_type": "IIDC", "disable_trigger": True}
        ]
        assert camera_service.started_stream is True
        assert preview_worker.started is True
    finally:
        controller.shutdown(force=True)


def test_session_controller_preconfigure_true_keeps_deprecated_compatibility_path() -> None:
    camera_service = FakeCameraService(deprecated_gui_error=True)
    preview_worker = FakePreviewWorker()
    controller = SessionController(
        camera_service=camera_service,
        preview_worker=preview_worker,
        preconfigure=True,
    )

    with pytest.raises(RuntimeError, match="PreConfigGUI is deprecated"):
        controller.start()

    assert camera_service.open_camera_gui_calls == [{"index": 0, "context_type": "IIDC"}]
    assert camera_service.open_camera_calls == []


def test_main_gui_build_controller_constructs_headless_startup_path() -> None:
    from app.main_gui import build_controller, parse_args

    args = parse_args(["--disable-lcd", "--no-auto-sidecar"])
    controller = build_controller(args)

    assert controller.preconfigure is False
    assert controller.camera_service.auto_ensure is False


def test_gui_camera_settings_are_allowlist_based() -> None:
    controller = SessionController(
        camera_service=FakeCameraService(),
        preview_worker=FakePreviewWorker(),
    )
    controller.state.update(
        camera_settings={
            "SHUTTER": 5.0,
            "GAIN": 1.5,
            "FRAME_RATE": 20.0044,
            "TRIGGER_MODE": 0.0,
        },
        camera_setting_ranges={
            "SHUTTER": (0.01, 1000.0),
            "GAIN": (0.0, 24.0),
            "FRAME_RATE": (1.0, 75.47),
            "TRIGGER_MODE": (0.0, 1.0),
        },
    )

    specs = controller.list_camera_settings()

    assert [spec.name for spec in specs] == ["GAIN", "SHUTTER"]
    assert is_gui_editable_camera_setting("FRAME_RATE") is False
    assert is_gui_editable_camera_setting("SHUTTER") is True
    assert is_gui_editable_camera_setting("GAIN") is True


def test_gui_rejects_disallowed_camera_setting_apply() -> None:
    controller = SessionController(
        camera_service=FakeCameraService(),
        preview_worker=FakePreviewWorker(),
    )

    with pytest.raises(RuntimeError, match="read-only setting\\(s\\): FRAME_RATE"):
        controller._apply_camera_settings({"FRAME_RATE": 30.0})
