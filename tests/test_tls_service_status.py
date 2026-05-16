from __future__ import annotations

import json
from pathlib import Path

import pytest

from diagnostics.run_status import read_tls_state, write_tls_state


class FakeTLSDevice:
    def __init__(self):
        self.connected = False
        self.device_id = 0
        self.mono = "Omni"
        self.port_type = "USB"
        self.serial_number = "FAKE-001"
        self.current_wavelength_nm = None
        self.target_wavelength_nm = None
        self.grating = None
        self.moving = False
        self._error: Exception | None = None

    def connect(self, *, Mono, port_type, serial_number):
        self.mono = Mono
        self.port_type = port_type
        self.serial_number = serial_number
        self.connected = True

    def disconnect(self):
        self.connected = False

    def set_grating(self, grating: int):
        if self._error:
            raise self._error
        self.grating = int(grating)

    def set_wavelength(self, wavelength_nm: float):
        if self._error:
            raise self._error
        self.target_wavelength_nm = float(wavelength_nm)

    def move(self, timeout: float = 60.0):
        if self._error:
            raise self._error
        self.current_wavelength_nm = self.target_wavelength_nm
        self.moving = False

    def wait_until_idle(self, *, timeout=60.0, poll_interval=0.2, tolerance_nm=0.5):
        self.current_wavelength_nm = self.target_wavelength_nm
        self.moving = False

    def get_status(self):
        return {
            "connected": self.connected,
            "device_id": self.device_id,
            "mono": self.mono,
            "port_type": self.port_type,
            "serial_number": self.serial_number,
            "current_wavelength_nm": self.current_wavelength_nm,
            "target_wavelength_nm": self.target_wavelength_nm,
            "grating": self.grating,
            "moving": self.moving,
        }

    def set_error(self, exc: Exception):
        self._error = exc


@pytest.fixture
def fake_device():
    return FakeTLSDevice()


@pytest.fixture
def status_dir(tmp_path):
    return tmp_path / "tls_status"


def test_tls_service_connect_writes_state(fake_device, status_dir):
    from devices.tls_service import TLSService

    svc = TLSService(device_factory=lambda: fake_device, status_dir=status_dir, default_serial_number="FAKE-001")
    svc.connect()

    state = read_tls_state(status_dir)
    assert state is not None
    assert state["connected"] is True
    assert state["serial_number"] == "FAKE-001"


def test_tls_service_set_grating_writes_state(fake_device, status_dir):
    from devices.tls_service import TLSService

    svc = TLSService(device_factory=lambda: fake_device, status_dir=status_dir, default_serial_number="FAKE-001")
    svc.connect()
    svc.set_grating(2)

    state = read_tls_state(status_dir)
    assert state["grating"] == 2


def test_tls_service_set_wavelength_nm_writes_target(fake_device, status_dir):
    from devices.tls_service import TLSService

    svc = TLSService(device_factory=lambda: fake_device, status_dir=status_dir, default_serial_number="FAKE-001")
    svc.connect()
    svc.set_wavelength_nm(532.0)

    state = read_tls_state(status_dir)
    assert state["target_wavelength_nm"] == 532.0


def test_tls_service_move_updates_current_wavelength(fake_device, status_dir):
    from devices.tls_service import TLSService

    svc = TLSService(device_factory=lambda: fake_device, status_dir=status_dir, default_serial_number="FAKE-001")
    svc.connect()
    svc.set_wavelength_nm(650.0)
    svc.move()

    state = read_tls_state(status_dir)
    assert state["current_wavelength_nm"] == 650.0
    assert state["moving"] is False


def test_tls_service_exception_publishes_last_error(fake_device, status_dir):
    from devices.tls_service import TLSService, TLSServiceTimeoutError

    svc = TLSService(device_factory=lambda: fake_device, status_dir=status_dir, default_serial_number="FAKE-001")
    svc.connect()
    fake_device.set_error(TimeoutError("simulated timeout"))

    with pytest.raises(TLSServiceTimeoutError):
        svc.set_wavelength_nm(500.0)

    state = read_tls_state(status_dir)
    assert state is not None
    assert "simulated timeout" in str(state.get("last_error", ""))


def test_tls_service_without_status_dir_does_not_write(fake_device, tmp_path):
    from devices.tls_service import TLSService

    svc = TLSService(device_factory=lambda: fake_device)
    svc.connect()

    assert not (tmp_path / "tls_state.json").exists()


def test_tls_service_set_status_dir(fake_device, tmp_path):
    from devices.tls_service import TLSService

    svc = TLSService(device_factory=lambda: fake_device)
    svc.connect()
    sd = tmp_path / "later_tls"
    svc.set_status_dir(sd)
    svc.set_wavelength_nm(400.0)

    assert (sd / "tls_state.json").exists()
    state = read_tls_state(sd)
    assert state["target_wavelength_nm"] == 400.0


def test_tls_service_disconnect_writes_state(fake_device, status_dir):
    from devices.tls_service import TLSService

    svc = TLSService(device_factory=lambda: fake_device, status_dir=status_dir, default_serial_number="FAKE-001")
    svc.connect()
    svc.disconnect()

    state = read_tls_state(status_dir)
    assert state["connected"] is False
