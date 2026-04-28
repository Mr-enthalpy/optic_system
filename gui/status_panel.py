from __future__ import annotations

import tkinter as tk
from tkinter import ttk


class StatusPanel(ttk.LabelFrame):
    """Show session status and hardware-debug context."""

    def __init__(self, master):
        super().__init__(master, text="Status", padding=8)

        self.camera_var = tk.StringVar(value="Camera: closed")
        self.stream_var = tk.StringVar(value="Stream: stopped")
        self.sidecar_var = tk.StringVar(value="Sidecar: --")
        self.camera_info_var = tk.StringVar(value="Camera info: --")
        self.lcd_var = tk.StringVar(value="LCD: --")
        self.lcd_mode_var = tk.StringVar(value="LCD mode: --")
        self.lcd_codes_var = tk.StringVar(value="LCD codes: --")
        self.error_var = tk.StringVar(value="Last error: --")
        self.message_var = tk.StringVar(value="Ready")

        ttk.Label(self, textvariable=self.camera_var, anchor="w").pack(fill="x", pady=2)
        ttk.Label(self, textvariable=self.stream_var, anchor="w").pack(fill="x", pady=2)
        ttk.Label(self, textvariable=self.sidecar_var, anchor="w").pack(fill="x", pady=2)
        ttk.Label(self, textvariable=self.camera_info_var, anchor="w").pack(fill="x", pady=2)
        ttk.Label(self, textvariable=self.lcd_var, anchor="w").pack(fill="x", pady=2)
        ttk.Label(self, textvariable=self.lcd_mode_var, anchor="w").pack(fill="x", pady=2)
        ttk.Label(self, textvariable=self.lcd_codes_var, anchor="w").pack(fill="x", pady=2)
        ttk.Label(self, textvariable=self.error_var, anchor="w").pack(fill="x", pady=2)

        ttk.Separator(self, orient="horizontal").pack(fill="x", pady=6)

        ttk.Label(self, text="Latest Message:", anchor="w").pack(fill="x")
        self.message_label = ttk.Label(self, textvariable=self.message_var, anchor="w")
        self.message_label.pack(fill="x", pady=2)

    def update_from_state(self, state) -> None:
        self.camera_var.set(f"Camera: {'open' if state.camera_open else 'closed'}")
        self.stream_var.set(f"Stream: {'running' if state.stream_running else 'stopped'}")

        sidecar_status = "running" if state.sidecar_running else "offline"
        sidecar_mode = "owned" if state.sidecar_owned else "external"
        sidecar_parts = [state.sidecar_rep_addr, sidecar_mode]
        if state.sidecar_pid is not None:
            sidecar_parts.append(f"pid {state.sidecar_pid}")
        self.sidecar_var.set(f"Sidecar: {sidecar_status} | {' | '.join(sidecar_parts)}")

        camera_parts: list[str] = []
        if state.camera_serial:
            camera_parts.append(f"serial {state.camera_serial}")
        if state.frame_width and state.frame_height:
            camera_parts.append(f"{state.frame_width}x{state.frame_height}")
        if state.pixel_format:
            camera_parts.append(state.pixel_format)
        if state.latest_frame_seq is not None and state.latest_frame_seq >= 0:
            camera_parts.append(f"seq {state.latest_frame_seq}")
        self.camera_info_var.set(f"Camera info: {' | '.join(camera_parts) if camera_parts else '--'}")

        lcd_status = "connected" if state.lcd_connected else "disconnected"
        lcd_parts: list[str] = [lcd_status]
        if state.lcd_display_index is not None:
            lcd_parts.append(f"display {state.lcd_display_index}")
        if state.lcd_reported_shape is not None:
            lcd_parts.append(f"rgb {state.lcd_reported_shape}")
        if state.lcd_physical_shape is not None:
            lcd_parts.append(f"mono {state.lcd_physical_shape}")
        self.lcd_var.set(f"LCD: {' | '.join(lcd_parts)}")
        self.lcd_mode_var.set(
            f"LCD mode: {state.lcd_current_mode or '--'} | mask {state.lcd_current_mask_id or '--'}"
        )
        if state.lcd_transmissive_code is not None and state.lcd_opaque_code is not None:
            self.lcd_codes_var.set(
                f"LCD codes: transparent {state.lcd_transmissive_code} | opaque {state.lcd_opaque_code}"
            )
        else:
            self.lcd_codes_var.set("LCD codes: --")

        combined_error = state.last_error or state.lcd_last_error
        self.error_var.set(f"Last error: {combined_error or '--'}")

    def show_message(self, level: str, message: str) -> None:
        self.message_var.set(message)

        if level == "error":
            self.message_label.configure(foreground="red")
        elif level == "success":
            self.message_label.configure(foreground="green")
        elif level == "warning":
            self.message_label.configure(foreground="orange")
        else:
            self.message_label.configure(foreground="black")
