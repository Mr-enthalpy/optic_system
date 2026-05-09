from __future__ import annotations

import pytest

from control.session_controller import SessionController
from devices.tls_service import TLSServiceUnavailableError


class FakeTLSService:
    def __init__(self, *, default_serial_number=None):
        self.default_serial_number = default_serial_number


def test_build_without_enable_tls_does_not_construct_tls_service(monkeypatch):
    from app.main_gui import build_controller, parse_args

    args = parse_args(["--disable-lcd", "--no-auto-sidecar"])
    controller = build_controller(args)

    assert isinstance(controller, SessionController)
    assert controller.tls_service is None


def test_build_with_enable_tls_constructs_and_injects_tls_service(monkeypatch):
    from app.main_gui import build_controller, parse_args

    monkeypatch.setattr(
        "app.main_gui.TLSService",
        FakeTLSService,
    )

    args = parse_args([
        "--disable-lcd",
        "--no-auto-sidecar",
        "--enable-tls",
        "--tls-serial-number", "OM999",
    ])
    controller = build_controller(args)

    assert isinstance(controller, SessionController)
    assert controller.tls_service is not None
    assert isinstance(controller.tls_service, FakeTLSService)
    assert controller.tls_service.default_serial_number == "OM999"


def test_build_with_enable_tls_but_sdk_unavailable_exits_cleanly(monkeypatch):
    from app.main_gui import build_controller, parse_args

    def fail_constructor(*, default_serial_number=None):
        raise TLSServiceUnavailableError("import", "module 'tls_c1' is not installed")

    monkeypatch.setattr("app.main_gui.TLSService", fail_constructor)

    args = parse_args([
        "--disable-lcd",
        "--no-auto-sidecar",
        "--enable-tls",
        "--tls-serial-number", "OM999",
    ])

    with pytest.raises(SystemExit) as exc_info:
        build_controller(args)

    assert exc_info.value.code == 1
