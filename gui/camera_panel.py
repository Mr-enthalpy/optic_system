from __future__ import annotations

import tkinter as tk
from dataclasses import dataclass
from tkinter import ttk
from typing import Callable


_SUSPICIOUS_THRESHOLD = 0.01


def _is_suspicious(value: float, min_val: float, max_val: float) -> bool:
    if min_val > max_val:
        return True
    if value < min_val or value > max_val:
        return True
    if min_val == max_val:
        return False
    relative_deviation = abs(value) / max(abs(min_val), abs(max_val), 1.0)
    return relative_deviation < _SUSPICIOUS_THRESHOLD and abs(value - min_val) > (max_val - min_val) * 0.01


@dataclass(frozen=True)
class CameraSettingSpec:
    name: str
    min_val: float
    max_val: float
    current_val: float


class CameraPanel(ttk.LabelFrame):
    """
    Camera settings panel.

    The GUI only collects user edits and triggers callbacks. It does not touch
    device clients directly.
    """

    def __init__(
        self,
        master,
        setting_specs: list[CameraSettingSpec],
        on_apply: Callable[[], None],
        on_refresh: Callable[[], None],
        on_shutdown: Callable[[], None],
    ):
        super().__init__(master, text="Camera Settings", padding=8)

        self._setting_widgets: dict[str, tuple[ttk.Entry, ttk.Label, float, float]] = {}
        self._suspicious_settings: set[str] = set()
        self._on_apply = on_apply
        self._on_refresh = on_refresh
        self._on_shutdown = on_shutdown

        self._build_settings(setting_specs)
        self._build_buttons()

    def get_suspicious_names(self) -> list[str]:
        return sorted(self._suspicious_settings)

    def _build_settings(self, setting_specs: list[CameraSettingSpec]) -> None:
        settings_container = ttk.Frame(self)
        settings_container.pack(fill="x", expand=True)

        if not setting_specs:
            ttk.Label(
                settings_container,
                text="No adjustable camera settings",
                anchor="w",
            ).pack(fill="x", pady=2)
            return

        for spec in setting_specs:
            row = ttk.Frame(settings_container)
            row.pack(fill="x", pady=2)

            name_label = ttk.Label(row, text=f"{spec.name}:", width=16, anchor="w")
            name_label.pack(side="left")

            entry = ttk.Entry(row, width=12)
            entry.insert(0, f"{spec.current_val:.2f}")
            entry.pack(side="left", padx=4)

            suspicious = _is_suspicious(spec.current_val, spec.min_val, spec.max_val)
            range_text = f"[{spec.min_val:.2f}, {spec.max_val:.2f}]"
            if suspicious:
                range_text += " (suspicious)"
                entry.configure(state="disabled")
                self._suspicious_settings.add(spec.name)
            range_label = ttk.Label(row, text=range_text)
            range_label.pack(side="left", padx=4)

            self._setting_widgets[spec.name] = (entry, range_label, spec.min_val, spec.max_val)

    def _build_buttons(self) -> None:
        button_row = ttk.Frame(self)
        button_row.pack(fill="x", pady=8)

        ttk.Button(button_row, text="Refresh", command=self._on_refresh).pack(side="left", padx=4)
        ttk.Button(button_row, text="Shutdown", command=self._on_shutdown).pack(side="right", padx=4)
        ttk.Button(button_row, text="Apply", command=self._on_apply).pack(side="right", padx=4)

    def collect_settings(self) -> dict[str, float]:
        settings: dict[str, float] = {}
        for name, (entry, _range_label, min_val, max_val) in self._setting_widgets.items():
            if name in self._suspicious_settings:
                continue
            raw_value = entry.get().strip()
            try:
                value = float(raw_value)
            except ValueError as exc:
                raise ValueError(f"{name}: please enter a numeric value") from exc

            if not min_val <= value <= max_val:
                raise ValueError(f"{name}: value must be within [{min_val:.2f}, {max_val:.2f}]")

            settings[name] = value
        return settings

    def refresh_setting_values(self, values: dict[str, float]) -> None:
        self._suspicious_settings.clear()
        for name, value in values.items():
            if name not in self._setting_widgets:
                continue
            entry, range_label, min_val, max_val = self._setting_widgets[name]
            entry.configure(state="normal")
            entry.delete(0, tk.END)
            entry.insert(0, f"{value:.2f}")
            suspicious = _is_suspicious(value, min_val, max_val)
            range_text = f"[{min_val:.2f}, {max_val:.2f}]"
            if suspicious:
                range_text += " (suspicious)"
                entry.configure(state="disabled")
                self._suspicious_settings.add(name)
            range_label.configure(text=range_text)
