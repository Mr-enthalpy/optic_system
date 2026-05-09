from __future__ import annotations

from control.commands import (
    ApplyCameraSettings,
    ConnectTLS,
    DisconnectTLS,
    MoveTLS,
    RefreshCameraSettings,
    RefreshTLSStatus,
    SetLCDAllOpaque,
    SetLCDAllTransmissive,
    SetTLSGrating,
    SetTLSWavelength,
    ShowLCDDebugPattern,
    Shutdown,
)


def bind_apply_settings(window) -> None:
    settings = window.camera_panel.collect_settings()
    window.controller.dispatch(ApplyCameraSettings(settings=settings))


def bind_refresh_settings(window) -> None:
    window.controller.dispatch(RefreshCameraSettings())


def bind_lcd_all_transmissive(window) -> None:
    window.controller.dispatch(SetLCDAllTransmissive())


def bind_lcd_all_opaque(window) -> None:
    window.controller.dispatch(SetLCDAllOpaque())


def bind_lcd_debug_pattern(window, pattern_name: str) -> None:
    window.controller.dispatch(ShowLCDDebugPattern(pattern_name=pattern_name))


def bind_shutdown(window, force: bool = False) -> None:
    window.controller.dispatch(Shutdown(force=force))
    if window.root.winfo_exists():
        window.root.destroy()


def bind_tls_connect(window, serial_number=None, mono=None, port_type=None) -> None:
    window.controller.dispatch(ConnectTLS(
        serial_number=serial_number,
        mono=mono,
        port_type=port_type,
    ))


def bind_tls_disconnect(window) -> None:
    window.controller.dispatch(DisconnectTLS())


def bind_tls_set_grating(window, grating: int) -> None:
    window.controller.dispatch(SetTLSGrating(grating=grating))


def bind_tls_set_wavelength(window, wavelength_nm: float) -> None:
    window.controller.dispatch(SetTLSWavelength(wavelength_nm=wavelength_nm))


def bind_tls_move(window, timeout_s: float = 60.0) -> None:
    window.controller.dispatch(MoveTLS(timeout_s=timeout_s))


def bind_tls_refresh_status(window) -> None:
    window.controller.dispatch(RefreshTLSStatus())
