from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk
from typing import Callable


class TLSPanel(ttk.LabelFrame):
    """Minimal TLS control panel.

    Dispatches user intent through control-command bindings.
    Does not import ``tls_c1`` and does not call ``TLSService`` directly.
    """

    def __init__(
        self,
        master,
        *,
        on_connect: Callable[[str | None], None],
        on_disconnect: Callable[[], None],
        on_set_grating: Callable[[int], None],
        on_set_wavelength: Callable[[float], None],
        on_move: Callable[[float], None],
        on_refresh_status: Callable[[], None],
    ):
        super().__init__(master, text="TLS Control", padding=8)

        self._on_connect = on_connect
        self._on_disconnect = on_disconnect
        self._on_set_grating = on_set_grating
        self._on_set_wavelength = on_set_wavelength
        self._on_move = on_move
        self._on_refresh_status = on_refresh_status

        self.connected_var = tk.StringVar(value="TLS: disconnected")
        self.wavelength_var = tk.StringVar(value="Wavelength: --")
        self.moving_var = tk.StringVar(value="Moving: --")

        ttk.Label(self, textvariable=self.connected_var, anchor="w").pack(fill="x", pady=2)
        ttk.Label(self, textvariable=self.wavelength_var, anchor="w").pack(fill="x", pady=2)
        ttk.Label(self, textvariable=self.moving_var, anchor="w").pack(fill="x", pady=2)

        conn_frame = ttk.Frame(self)
        conn_frame.pack(fill="x", pady=4)
        self.serial_entry = ttk.Entry(conn_frame, width=16)
        self.serial_entry.pack(side="left", padx=2)
        ttk.Button(conn_frame, text="Connect", command=self._handle_connect).pack(side="left", padx=2)
        ttk.Button(conn_frame, text="Disconnect", command=on_disconnect).pack(side="left", padx=2)

        grat_frame = ttk.Frame(self)
        grat_frame.pack(fill="x", pady=2)
        ttk.Label(grat_frame, text="Grating:").pack(side="left")
        self.grating_entry = ttk.Entry(grat_frame, width=6)
        self.grating_entry.insert(0, "1")
        self.grating_entry.pack(side="left", padx=4)
        ttk.Button(grat_frame, text="Set Grating", command=self._handle_set_grating).pack(side="left")

        wave_frame = ttk.Frame(self)
        wave_frame.pack(fill="x", pady=2)
        ttk.Label(wave_frame, text="Wavelength (nm):").pack(side="left")
        self.wavelength_entry = ttk.Entry(wave_frame, width=10)
        self.wavelength_entry.insert(0, "550.0")
        self.wavelength_entry.pack(side="left", padx=4)
        ttk.Button(wave_frame, text="Set Target", command=self._handle_set_wavelength).pack(side="left")

        act_frame = ttk.Frame(self)
        act_frame.pack(fill="x", pady=4)
        self.timeout_entry = ttk.Entry(act_frame, width=6)
        self.timeout_entry.insert(0, "60")
        self.timeout_entry.pack(side="left", padx=2)
        ttk.Label(act_frame, text="s timeout").pack(side="left")
        ttk.Button(act_frame, text="Move", command=self._handle_move).pack(side="left", padx=8)
        ttk.Button(act_frame, text="Refresh Status", command=on_refresh_status).pack(side="left")

    def _handle_connect(self) -> None:
        serial = self.serial_entry.get().strip() or None
        self._on_connect(serial)

    def _handle_set_grating(self) -> None:
        try:
            grating = int(self.grating_entry.get().strip())
        except ValueError:
            messagebox.showwarning("TLS", "Grating must be an integer")
            return
        self._on_set_grating(grating)

    def _handle_set_wavelength(self) -> None:
        try:
            wavelength = float(self.wavelength_entry.get().strip())
        except ValueError:
            messagebox.showwarning("TLS", "Wavelength must be a number (nm)")
            return
        self._on_set_wavelength(wavelength)

    def _handle_move(self) -> None:
        try:
            timeout = float(self.timeout_entry.get().strip())
        except ValueError:
            timeout = 60.0
        self._on_move(timeout)

    def update_from_state(self, state) -> None:
        self.connected_var.set(
            f"TLS: {'connected' if state.tls_connected else 'disconnected'}"
        )
        if state.tls_current_wavelength_nm is not None:
            wav_text = f"Wavelength: {state.tls_current_wavelength_nm:.3f} nm"
            if state.tls_target_wavelength_nm is not None:
                wav_text += f" (target: {state.tls_target_wavelength_nm:.3f} nm)"
            self.wavelength_var.set(wav_text)
        else:
            self.wavelength_var.set("Wavelength: --")
        self.moving_var.set(f"Moving: {'yes' if state.tls_moving else 'no'}")