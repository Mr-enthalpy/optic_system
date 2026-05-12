from __future__ import annotations

import time
import tkinter as tk
from tkinter import ttk

import numpy as np

try:
    from PIL import Image, ImageTk

    _HAS_PIL = True
except Exception:
    _HAS_PIL = False


class LCDMonitorPanel(ttk.LabelFrame):
    def __init__(self, master):
        super().__init__(master, text="LCD Mask Monitor", padding=8)
        self._photo = None

        self.mask_id_var = tk.StringVar(value="Mask id: --")
        self.shape_var = tk.StringVar(value="Preview shape: --")
        self.updated_var = tk.StringVar(value="Last update: --")

        ttk.Label(self, textvariable=self.mask_id_var, anchor="w").pack(fill="x")
        self._image_label = ttk.Label(self, text="Mask preview unavailable", anchor="center")
        self._image_label.pack(fill="both", expand=True, pady=6)
        ttk.Label(self, textvariable=self.shape_var, anchor="w").pack(fill="x")
        ttk.Label(self, textvariable=self.updated_var, anchor="w").pack(fill="x")

    def update_status(self, status) -> None:
        if status is None:
            self.mask_id_var.set("Mask id: --")
            self.updated_var.set("Last update: status unavailable")
            return
        self.mask_id_var.set(f"Mask id: {status.current_mask_id or '--'}")
        self.updated_var.set(f"Last update: {_format_age(status.last_update_ns)}")

    def update_preview(self, image: np.ndarray | None) -> None:
        if image is None:
            self._image_label.configure(image="", text="Mask preview unavailable")
            self._photo = None
            self.shape_var.set("Preview shape: --")
            return

        self.shape_var.set(f"Preview shape: {tuple(image.shape)}")
        if not _HAS_PIL:
            self._image_label.configure(image="", text="Pillow not installed")
            return

        arr = _to_uint8(image)
        if arr.ndim == 2:
            pil_image = Image.fromarray(arr, mode="L")
        elif arr.ndim == 3 and arr.shape[2] == 3:
            pil_image = Image.fromarray(arr[:, :, ::-1], mode="RGB")
        else:
            self._image_label.configure(image="", text="Unsupported preview shape")
            return

        max_w, max_h = 520, 360
        w, h = pil_image.size
        scale = min(max_w / max(w, 1), max_h / max(h, 1), 1.0)
        if scale < 1.0:
            pil_image = pil_image.resize((max(1, int(w * scale)), max(1, int(h * scale))))

        self._photo = ImageTk.PhotoImage(image=pil_image)
        self._image_label.configure(image=self._photo, text="")


def _to_uint8(image: np.ndarray) -> np.ndarray:
    arr = np.asarray(image)
    if arr.dtype == np.uint8:
        return arr
    if arr.size == 0:
        return arr.astype(np.uint8)
    mn = float(np.nanmin(arr))
    mx = float(np.nanmax(arr))
    if mx <= mn:
        return np.zeros(arr.shape, dtype=np.uint8)
    return np.clip((arr.astype(np.float64) - mn) * (255.0 / (mx - mn)), 0, 255).astype(np.uint8)


def _format_age(last_update_ns: int | None) -> str:
    if not last_update_ns:
        return "--"
    age_s = max(0.0, (time.monotonic_ns() - int(last_update_ns)) / 1_000_000_000)
    return f"{age_s:.1f}s ago"

