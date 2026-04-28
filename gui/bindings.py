from __future__ import annotations

from control.commands import (
    ApplyCameraSettings,
    RefreshCameraSettings,
    SetLCDAllOpaque,
    SetLCDAllTransmissive,
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
