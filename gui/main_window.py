from __future__ import annotations

import logging
import queue
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Callable

from control.events import (
    CameraError,
    CameraSettingsApplied,
    CameraSettingsRefreshed,
    Event,
    LCDError,
    PreviewFrameUpdated,
    PreviewStatsUpdated,
    StatusMessage,
    TLSError,
)

from .bindings import (
    bind_apply_settings,
    bind_lcd_all_opaque,
    bind_lcd_all_transmissive,
    bind_lcd_debug_pattern,
    bind_refresh_settings,
    bind_shutdown,
    bind_tls_connect,
    bind_tls_disconnect,
    bind_tls_move,
    bind_tls_refresh_status,
    bind_tls_set_grating,
    bind_tls_set_wavelength,
)
from .camera_panel import CameraPanel, CameraSettingSpec
from .lcd_panel import LCDPanel
from .preview_panel import PreviewPanel
from .status_panel import StatusPanel
from .tls_panel import TLSPanel


class MainWindow:
    """
    Minimal camera + LCD GUI.

    It renders preview, camera settings, LCD debug actions, and runtime status
    while routing user actions back through the controller.
    """

    def __init__(self, controller, title: str = "Camera Preview", logger: logging.Logger | None = None):
        self.controller = controller
        self.logger = logger or logging.getLogger("optic_system.gui")
        self.root = tk.Tk()
        self.root.title(title)

        self._event_queue: queue.Queue[Event] = queue.Queue()
        self._latest_frame_event: PreviewFrameUpdated | None = None
        self._poll_interval_ms: int = 16
        self.controller.bus.subscribe(self._enqueue_event)

        self.root.report_callback_exception = self._report_callback_exception

        setting_specs = self._build_camera_setting_specs()

        left_frame = ttk.Frame(self.root)
        left_frame.pack(side="left", fill="y", padx=8, pady=8)

        self.camera_panel = CameraPanel(
            left_frame,
            setting_specs=setting_specs,
            on_apply=lambda: self._safe_call(bind_apply_settings),
            on_refresh=lambda: self._safe_call(bind_refresh_settings),
            on_shutdown=lambda: self._safe_call(bind_shutdown),
        )
        self.camera_panel.pack(fill="x", pady=4)

        self.lcd_panel = LCDPanel(
            left_frame,
            on_all_transmissive=lambda: self._safe_call(bind_lcd_all_transmissive),
            on_all_opaque=lambda: self._safe_call(bind_lcd_all_opaque),
            on_center_cross=lambda: self._safe_call(
                lambda window: bind_lcd_debug_pattern(window, "center_cross")
            ),
            on_vertical_bars=lambda: self._safe_call(
                lambda window: bind_lcd_debug_pattern(window, "vertical_bars")
            ),
        )
        self.lcd_panel.pack(fill="x", pady=4)

        if self.controller.tls_service is not None:
            self.tls_panel = TLSPanel(
                left_frame,
                on_connect=lambda serial=None: self._safe_call(
                    lambda w: bind_tls_connect(w, serial_number=serial)
                ),
                on_disconnect=lambda: self._safe_call(bind_tls_disconnect),
                on_set_grating=lambda grating: self._safe_call(
                    lambda w: bind_tls_set_grating(w, grating)
                ),
                on_set_wavelength=lambda wl: self._safe_call(
                    lambda w: bind_tls_set_wavelength(w, wl)
                ),
                on_move=lambda timeout: self._safe_call(
                    lambda w: bind_tls_move(w, timeout_s=timeout)
                ),
                on_refresh_status=lambda: self._safe_call(bind_tls_refresh_status),
            )
            self.tls_panel.pack(fill="x", pady=4)
        else:
            self.tls_panel = None

        right_frame = ttk.Frame(self.root)
        right_frame.pack(side="right", fill="both", expand=True, padx=8, pady=8)

        self.preview_panel = PreviewPanel(
            right_frame,
            on_bayer_change=lambda pattern: self.controller.set_bayer_pattern(pattern),
        )
        self.preview_panel.pack(fill="both", expand=True, pady=4)

        self.status_panel = StatusPanel(right_frame)
        self.status_panel.pack(fill="both", expand=True, pady=4)

        self.root.protocol("WM_DELETE_WINDOW", self._on_window_close)
        self.root.after(self._poll_interval_ms, self._poll_event_queue)
        self.root.after(100, self._refresh_state)

    def _build_camera_setting_specs(self) -> list[CameraSettingSpec]:
        specs: list[CameraSettingSpec] = []
        for setting in self.controller.list_camera_settings():
            specs.append(
                CameraSettingSpec(
                    name=setting.name,
                    min_val=setting.min_value,
                    max_val=setting.max_value,
                    current_val=setting.value,
                )
            )
        return specs

    def _enqueue_event(self, event: Event) -> None:
        if isinstance(event, PreviewFrameUpdated):
            self._latest_frame_event = event
        else:
            self._event_queue.put(event)

    def _poll_event_queue(self) -> None:
        while True:
            try:
                event = self._event_queue.get_nowait()
            except queue.Empty:
                break
            try:
                self._handle_event(event)
            except Exception:
                self.logger.exception("_handle_event crashed for %s", type(event).__name__)

        frame = self._latest_frame_event
        if frame is not None:
            self._latest_frame_event = None
            try:
                self._handle_event(frame)
            except Exception:
                self.logger.exception("_handle_event crashed for PreviewFrameUpdated")

        if self.root.winfo_exists():
            self.root.after(self._poll_interval_ms, self._poll_event_queue)

    def _handle_event(self, event: Event) -> None:
        if isinstance(event, StatusMessage):
            self.status_panel.show_message(event.level, event.message)
        elif isinstance(event, CameraError):
            self.status_panel.show_message("error", f"{event.source}: {event.message}")
        elif isinstance(event, LCDError):
            self.status_panel.show_message("error", f"{event.source}: {event.message}")
        elif isinstance(event, TLSError):
            self.status_panel.show_message("error", f"{event.source}: {event.message}")
        elif isinstance(event, PreviewFrameUpdated):
            self.preview_panel.update_preview(event.preview_bgr)
        elif isinstance(event, PreviewStatsUpdated):
            self.preview_panel.update_stats(
                max_pixel=event.max_pixel,
                frame_seq=event.frame_seq,
                timestamp_ns=event.timestamp_ns,
                width=event.width,
                height=event.height,
                stride=event.stride,
                pixel_format=event.pixel_format,
            )
        elif isinstance(event, CameraSettingsApplied):
            self.camera_panel.refresh_setting_values(event.applied_settings)
        elif isinstance(event, CameraSettingsRefreshed):
            self.camera_panel.refresh_setting_values(event.settings)

    def _refresh_state(self) -> None:
        state = self.controller.state.get()
        self.status_panel.update_from_state(state)
        self.lcd_panel.update_from_state(state)

        if self.tls_panel is not None:
            self.tls_panel.update_from_state(state)

        self.preview_panel.update_stats(
            max_pixel=state.latest_max_pixel,
            frame_seq=state.latest_frame_seq,
            timestamp_ns=state.latest_frame_timestamp_ns,
            width=state.frame_width,
            height=state.frame_height,
            stride=state.frame_stride,
            pixel_format=state.pixel_format,
        )

        if self.root.winfo_exists():
            self.root.after(100, self._refresh_state)

    def _safe_call(self, fn: Callable[["MainWindow"], None]) -> None:
        try:
            fn(self)
        except Exception as exc:
            self.logger.exception("GUI callback failed")
            messagebox.showerror("Camera Settings Error", str(exc))
            self.status_panel.show_message("error", str(exc))

    def _report_callback_exception(self, exc_type, exc, tb):
        self.logger.error(
            "Unhandled Tk callback exception",
            exc_info=(exc_type, exc, tb),
        )
        messagebox.showerror("Error", str(exc))
        self.status_panel.show_message("error", str(exc))

    def _on_window_close(self) -> None:
        try:
            bind_shutdown(self, force=True)
        except Exception as exc:
            self.logger.exception("Error during window close shutdown")
            if self.root.winfo_exists():
                self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()
