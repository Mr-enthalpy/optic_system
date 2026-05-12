from __future__ import annotations

import time
import tkinter as tk
from tkinter import ttk


class TLSMonitorPanel(ttk.LabelFrame):
    def __init__(self, master):
        super().__init__(master, text="TLS Monitor", padding=8)

        self.current_var = tk.StringVar(value="Current wavelength: --")
        self.target_var = tk.StringVar(value="Target wavelength: --")
        self.grating_var = tk.StringVar(value="Grating: --")
        self.moving_var = tk.StringVar(value="Moving: --")
        self.updated_var = tk.StringVar(value="Last update: --")

        for var in (
            self.current_var,
            self.target_var,
            self.grating_var,
            self.moving_var,
            self.updated_var,
        ):
            ttk.Label(self, textvariable=var, anchor="w").pack(fill="x", pady=1)

    def update_status(self, status) -> None:
        if status is None:
            self.current_var.set("Current wavelength: --")
            self.target_var.set("Target wavelength: --")
            self.grating_var.set("Grating: --")
            self.moving_var.set("Moving: --")
            self.updated_var.set("Last update: status unavailable")
            return

        self.current_var.set(f"Current wavelength: {_fmt_nm(status.current_wavelength_nm)}")
        self.target_var.set(f"Target wavelength: {_fmt_nm(status.target_wavelength_nm)}")
        self.grating_var.set(f"Grating: {status.tls_grating if status.tls_grating is not None else '--'}")
        self.moving_var.set(f"Moving: {_fmt_bool(status.tls_moving)}")
        self.updated_var.set(f"Last update: {_format_age(status.last_update_ns)}")


def _fmt_nm(value: float | None) -> str:
    return "--" if value is None else f"{value:.3f} nm"


def _fmt_bool(value: bool | None) -> str:
    if value is None:
        return "--"
    return "yes" if value else "no"


def _format_age(last_update_ns: int | None) -> str:
    if not last_update_ns:
        return "--"
    age_s = max(0.0, (time.monotonic_ns() - int(last_update_ns)) / 1_000_000_000)
    return f"{age_s:.1f}s ago"

