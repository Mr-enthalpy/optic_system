from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable


class LCDPanel(ttk.LabelFrame):
    """Minimal LCD debug control panel."""

    def __init__(
        self,
        master,
        on_all_transmissive: Callable[[], None],
        on_all_opaque: Callable[[], None],
        on_center_cross: Callable[[], None],
        on_vertical_bars: Callable[[], None],
    ):
        super().__init__(master, text="LCD Debug", padding=8)

        self.connected_var = tk.StringVar(value="LCD: disconnected")
        self.mode_var = tk.StringVar(value="Mode: --")
        self.mask_var = tk.StringVar(value="Mask: --")

        ttk.Label(self, textvariable=self.connected_var, anchor="w").pack(fill="x", pady=2)
        ttk.Label(self, textvariable=self.mode_var, anchor="w").pack(fill="x", pady=2)
        ttk.Label(self, textvariable=self.mask_var, anchor="w").pack(fill="x", pady=2)

        buttons = ttk.Frame(self)
        buttons.pack(fill="x", pady=8)

        ttk.Button(buttons, text="Full Transparent", command=on_all_transmissive).pack(fill="x", pady=2)
        ttk.Button(buttons, text="Full Opaque", command=on_all_opaque).pack(fill="x", pady=2)
        ttk.Button(buttons, text="Center Cross", command=on_center_cross).pack(fill="x", pady=2)
        ttk.Button(buttons, text="Vertical Bars", command=on_vertical_bars).pack(fill="x", pady=2)

    def update_from_state(self, state) -> None:
        self.connected_var.set(f"LCD: {'connected' if state.lcd_connected else 'disconnected'}")
        self.mode_var.set(f"Mode: {state.lcd_current_mode or '--'}")
        self.mask_var.set(f"Mask: {state.lcd_current_mask_id or '--'}")
