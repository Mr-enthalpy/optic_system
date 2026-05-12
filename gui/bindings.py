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
from control.events import StatusMessage


def bind_apply_settings(window) -> None:
    suspicious = window.camera_panel.get_suspicious_names()
    if suspicious:
        for name in suspicious:
            spec = window.camera_panel._setting_widgets[name]
            min_val, max_val = spec[2], spec[3]
            entry_text = spec[0].get().strip()
            try:
                current_val = float(entry_text)
            except ValueError:
                current_val = float("nan")
            msg = (
                f"Camera setting {name} has out-of-range readback "
                f"{current_val:.2f} not in [{min_val:.2f}, {max_val:.2f}]; excluded from apply."
            )
            window.logger.warning(msg)
            window.controller.bus.publish(StatusMessage("warning", msg))

    settings = window.camera_panel.collect_settings()
    if not settings:
        window.controller.bus.publish(StatusMessage("info", "No valid camera settings to apply"))
        return
    try:
        window.controller.dispatch(ApplyCameraSettings(settings=settings))
    except Exception as exc:
        window.logger.exception("ApplyCameraSettings failed; forcing refresh")
        window.controller.dispatch(RefreshCameraSettings())


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
