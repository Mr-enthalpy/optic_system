from __future__ import annotations

import time
from dataclasses import dataclass

from control.commands import ConnectTLS, MoveTLS, SetTLSPassThrough, SetTLSWavelength
from control.events import TLSError, TLSConnected, TLSMoveFinished, TLSMoveStarted, TLSPassThroughSet, TLSWavelengthTargetSet
from control.session_controller import SessionController
from devices.tls_service import TLSServiceError, TLSStatus


@dataclass
class DummyPreviewWorker:
    on_frame: object = None
    on_error: object = None

    def start(self) -> None:
        pass

    def stop(self, join_timeout: float = 1.0) -> None:
        pass


class DummyCameraService:
    def get_connection_status(self) -> dict[str, object]:
        return {
            "rep_addr": "tcp://127.0.0.1:6101",
            "sidecar_running": False,
            "own_sidecar": False,
            "sidecar_pid": None,
        }

    def close(self) -> None:
        pass

    def close_camera(self) -> None:
        pass

    def stop_stream(self) -> None:
        pass


class FakeControllerTLSService:
    def __init__(self, *, fail_on_set_wavelength: bool = False):
        self.fail_on_set_wavelength = fail_on_set_wavelength
        self.connected = False
        self.device_id = None
        self.current = None
        self.target = None
        self.grating = None
        self.pass_through_calls = 0

    def connect(self, *, mono=None, port_type=None, serial_number=None) -> TLSStatus:
        self.connected = True
        self.device_id = 9
        self.current = 450.0
        self.target = 450.0
        self.grating = 1
        return self.get_status()

    def disconnect(self) -> TLSStatus:
        self.connected = False
        self.device_id = None
        self.current = None
        return self.get_status()

    def set_grating(self, grating: int) -> TLSStatus:
        self.grating = int(grating)
        return self.get_status()

    def set_wavelength_nm(self, wavelength_nm: float) -> TLSStatus:
        if self.fail_on_set_wavelength:
            raise TLSServiceError("set_wavelength", "fake target failure")
        self.target = float(wavelength_nm)
        return self.get_status()

    def set_pass_through(self, timeout_s: float = 60.0) -> TLSStatus:
        self.pass_through_calls += 1
        self.target = 0.0
        self.current = 0.0
        return self.get_status()

    def move(self, timeout_s: float = 60.0) -> TLSStatus:
        time.sleep(0.01)
        self.current = self.target
        return self.get_status()

    def wait_until_idle(self, **kwargs) -> TLSStatus:
        return self.get_status()

    def get_status(self) -> TLSStatus:
        return TLSStatus(
            connected=self.connected,
            device_id=self.device_id,
            current_wavelength_nm=self.current,
            target_wavelength_nm=self.target,
            grating=self.grating,
            moving=False,
            last_error=None,
        )

    def close(self) -> None:
        self.connected = False


def build_controller(tls_service) -> SessionController:
    return SessionController(
        camera_service=DummyCameraService(),
        preview_worker=DummyPreviewWorker(),
        tls_service=tls_service,
        preconfigure=False,
    )


def wait_for_event(events, event_type, timeout_s: float = 1.0):
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        for event in events:
            if isinstance(event, event_type):
                return event
        time.sleep(0.01)
    raise AssertionError(f"Timed out waiting for {event_type.__name__}")


def test_tls_controller_connect_target_and_move_flow():
    controller = build_controller(FakeControllerTLSService())
    events = []
    controller.bus.subscribe(events.append)

    try:
        controller.dispatch(ConnectTLS(serial_number="OM999"))
        assert any(isinstance(event, TLSConnected) for event in events)
        assert controller.state.get().tls_connected is True

        controller.dispatch(SetTLSWavelength(532.0))
        state = controller.state.get()
        assert state.tls_target_wavelength_nm == 532.0
        assert state.tls_current_wavelength_nm == 450.0
        assert any(isinstance(event, TLSWavelengthTargetSet) for event in events)

        controller.dispatch(MoveTLS(timeout_s=0.5))
        wait_for_event(events, TLSMoveStarted)
        wait_for_event(events, TLSMoveFinished)

        state = controller.state.get()
        assert state.tls_current_wavelength_nm == 532.0
        assert state.tls_target_wavelength_nm == 532.0
        assert state.tls_moving is False
    finally:
        controller.shutdown(force=True)


def test_tls_controller_captures_error_into_state_and_events():
    controller = build_controller(FakeControllerTLSService(fail_on_set_wavelength=True))
    events = []
    controller.bus.subscribe(events.append)

    try:
        controller.dispatch(ConnectTLS(serial_number="OM999"))
        controller.dispatch(SetTLSWavelength(610.0))

        error_event = wait_for_event(events, TLSError)
        assert "fake target failure" in error_event.message

        state = controller.state.get()
        assert "fake target failure" in (state.tls_last_error or "")
        assert "fake target failure" in (state.last_error or "")
    finally:
        controller.shutdown(force=True)


def test_tls_controller_pass_through_flow():
    tls = FakeControllerTLSService()
    controller = build_controller(tls)
    events = []
    controller.bus.subscribe(events.append)

    try:
        controller.dispatch(ConnectTLS(serial_number="OM999"))
        controller.dispatch(SetTLSPassThrough(timeout_s=0.5))

        assert tls.pass_through_calls == 1
        assert any(isinstance(event, TLSPassThroughSet) for event in events)
        state = controller.state.get()
        assert state.tls_target_wavelength_nm == 0.0
        assert state.tls_current_wavelength_nm == 0.0
    finally:
        controller.shutdown(force=True)
