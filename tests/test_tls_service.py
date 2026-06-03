from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from devices.tls_service import TLSService, TLSServiceError, TLSServiceUnavailableError


@dataclass
class FakeBackendStatus:
    connected: bool = False
    device_id: int | None = None
    mono: str | None = None
    port_type: str | None = None
    serial_number: str | None = None
    current_wavelength_nm: float | None = None
    target_wavelength_nm: float | None = None
    grating: int | None = None
    moving: bool = False
    last_error: str | None = None


class FakeTLSC1Error(RuntimeError):
    pass


class FakeTLSC1ValidationError(FakeTLSC1Error):
    pass


class FakeTLSC1:
    def __init__(self):
        self.status = FakeBackendStatus()
        self.raise_on_set_grating = False
        self.pass_through_calls = 0

    def connect(self, Mono="Omni", port_type="USB", serial_number=None):
        self.status.connected = True
        self.status.device_id = 7
        self.status.mono = Mono
        self.status.port_type = port_type
        self.status.serial_number = serial_number
        self.status.current_wavelength_nm = 405.0
        self.status.target_wavelength_nm = 405.0
        self.status.grating = 1

    def disconnect(self):
        self.status.connected = False
        self.status.device_id = None
        self.status.current_wavelength_nm = None
        self.status.moving = False

    def set_grating(self, grating):
        if self.raise_on_set_grating:
            raise FakeTLSC1ValidationError("invalid grating")
        self.status.grating = int(grating)

    def set_wavelength(self, wavelength):
        self.status.target_wavelength_nm = float(wavelength)

    def set_pass_through(self, timeout=60.0):
        self.pass_through_calls += 1
        self.status.target_wavelength_nm = 0.0
        self.status.current_wavelength_nm = 0.0
        self.status.moving = False

    def move(self, timeout=60.0):
        self.status.moving = True
        self.status.current_wavelength_nm = self.status.target_wavelength_nm
        self.status.moving = False

    def wait_until_idle(self, timeout=60.0, poll_interval=0.2, tolerance_nm=0.5):
        self.status.moving = False

    def get_status(self):
        return FakeBackendStatus(**self.status.__dict__)


def make_fake_module():
    return SimpleNamespace(
        TLSC1=FakeTLSC1,
        tls_c1=FakeTLSC1,
        TLSC1Error=FakeTLSC1Error,
        TLSC1ValidationError=FakeTLSC1ValidationError,
    )


def test_tls_service_is_lazy_without_sdk(monkeypatch):
    service = TLSService()
    assert service.get_status().connected is False

    def fail_import(name):
        raise ModuleNotFoundError(name)

    monkeypatch.setattr("devices.tls_service.importlib.import_module", fail_import)

    with pytest.raises(TLSServiceUnavailableError):
        service.connect()


def test_tls_service_wraps_high_level_backend(monkeypatch):
    monkeypatch.setattr(
        "devices.tls_service.importlib.import_module",
        lambda name: make_fake_module(),
    )

    service = TLSService(default_serial_number="OM123")

    status = service.connect()
    assert status.connected is True
    assert status.serial_number == "OM123"
    assert status.current_wavelength_nm == 405.0

    status = service.set_wavelength_nm(532.5)
    assert status.target_wavelength_nm == 532.5
    assert status.current_wavelength_nm == 405.0

    status = service.move()
    assert status.current_wavelength_nm == 532.5
    assert status.target_wavelength_nm == 532.5
    assert status.moving is False

    status = service.disconnect()
    assert status.connected is False


def test_tls_service_pass_through_uses_backend_api(monkeypatch):
    monkeypatch.setattr(
        "devices.tls_service.importlib.import_module",
        lambda name: make_fake_module(),
    )

    service = TLSService(default_serial_number="OM123")
    service.connect()

    status = service.set_pass_through(timeout_s=12.0)

    assert status.target_wavelength_nm == 0.0
    assert status.current_wavelength_nm == 0.0
    assert service._device.pass_through_calls == 1


def test_tls_service_converts_backend_errors(monkeypatch):
    fake_module = make_fake_module()

    def import_fake_module(name):
        return fake_module

    monkeypatch.setattr("devices.tls_service.importlib.import_module", import_fake_module)

    service = TLSService()
    service.connect(serial_number="OM456")
    service._device.raise_on_set_grating = True

    with pytest.raises(TLSServiceError) as exc_info:
        service.set_grating(99)

    assert "invalid grating" in str(exc_info.value)
    assert "set_grating" in str(exc_info.value)
