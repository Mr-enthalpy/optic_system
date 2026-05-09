from __future__ import annotations

import os

import pytest

from devices.tls_service import TLSService


pytestmark = pytest.mark.skipif(
    os.environ.get("TLS_C1_RUN_HARDWARE_TESTS") != "1",
    reason="TLS hardware smoke tests are opt-in only",
)


def test_tls_service_hardware_smoke():
    pytest.importorskip("tls_c1")

    serial_number = os.environ.get("TLS_C1_SERIAL")
    if not serial_number:
        pytest.skip("TLS_C1_SERIAL is required for hardware smoke")

    safe_grating = int(os.environ.get("TLS_C1_SAFE_GRATING", "1"))
    safe_wavelength_nm = float(os.environ.get("TLS_C1_SAFE_WAVELENGTH_NM", "550.0"))

    service = TLSService(default_serial_number=serial_number)
    try:
        status = service.connect(serial_number=serial_number)
        assert status.connected is True

        service.set_grating(safe_grating)
        service.set_wavelength_nm(safe_wavelength_nm)
        status = service.move(timeout_s=60.0)

        assert status.current_wavelength_nm is not None
        assert status.target_wavelength_nm == pytest.approx(safe_wavelength_nm)
    finally:
        service.close()
