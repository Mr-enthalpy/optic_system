from __future__ import annotations

from control.commands import (
    ConnectTLS,
    DisconnectTLS,
    MoveTLS,
    RefreshTLSStatus,
    SetTLSGrating,
    SetTLSWavelength,
)
from gui.bindings import (
    bind_tls_connect,
    bind_tls_disconnect,
    bind_tls_move,
    bind_tls_refresh_status,
    bind_tls_set_grating,
    bind_tls_set_wavelength,
)


class SpyController:
    def __init__(self):
        self.dispatched = []

    def dispatch(self, cmd):
        self.dispatched.append(cmd)


class FakeWindow:
    def __init__(self):
        self.controller = SpyController()


def test_bind_tls_connect_dispatches_connect_tls():
    w = FakeWindow()
    bind_tls_connect(w, serial_number="OM999", mono="Omni", port_type="USB")
    cmd = w.controller.dispatched[0]
    assert isinstance(cmd, ConnectTLS)
    assert cmd.serial_number == "OM999"
    assert cmd.mono == "Omni"
    assert cmd.port_type == "USB"


def test_bind_tls_connect_none_serial():
    w = FakeWindow()
    bind_tls_connect(w)
    cmd = w.controller.dispatched[0]
    assert isinstance(cmd, ConnectTLS)
    assert cmd.serial_number is None


def test_bind_tls_disconnect_dispatches_disconnect_tls():
    w = FakeWindow()
    bind_tls_disconnect(w)
    assert isinstance(w.controller.dispatched[0], DisconnectTLS)


def test_bind_tls_set_grating_dispatches_set_tls_grating():
    w = FakeWindow()
    bind_tls_set_grating(w, 2)
    cmd = w.controller.dispatched[0]
    assert isinstance(cmd, SetTLSGrating)
    assert cmd.grating == 2


def test_bind_tls_set_wavelength_dispatches_set_tls_wavelength():
    w = FakeWindow()
    bind_tls_set_wavelength(w, 532.0)
    cmd = w.controller.dispatched[0]
    assert isinstance(cmd, SetTLSWavelength)
    assert cmd.wavelength_nm == 532.0


def test_bind_tls_move_dispatches_move_tls():
    w = FakeWindow()
    bind_tls_move(w, timeout_s=30.0)
    cmd = w.controller.dispatched[0]
    assert isinstance(cmd, MoveTLS)
    assert cmd.timeout_s == 30.0


def test_bind_tls_move_default_timeout():
    w = FakeWindow()
    bind_tls_move(w)
    cmd = w.controller.dispatched[0]
    assert isinstance(cmd, MoveTLS)
    assert cmd.timeout_s == 60.0


def test_bind_tls_refresh_status_dispatches_refresh_tls_status():
    w = FakeWindow()
    bind_tls_refresh_status(w)
    assert isinstance(w.controller.dispatched[0], RefreshTLSStatus)
