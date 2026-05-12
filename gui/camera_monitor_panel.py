from __future__ import annotations

import time
import tkinter as tk
from tkinter import ttk

import numpy as np

try:
    import cv2
except Exception:
    cv2 = None

try:
    from PIL import Image, ImageTk

    _HAS_PIL = True
except Exception:
    _HAS_PIL = False


class CameraMonitorPanel(ttk.LabelFrame):
    def __init__(self, master):
        super().__init__(master, text="Camera Monitor", padding=8)
        self._photo = None

        self._image_label = ttk.Label(self, text="Camera stream unavailable", anchor="center")
        self._image_label.pack(fill="both", expand=True, pady=6)

        self.frame_seq_var = tk.StringVar(value="Frame sequence: --")
        self.max_pixel_var = tk.StringVar(value="Max pixel: --")
        self.shape_var = tk.StringVar(value="Size: --")
        self.pixel_format_var = tk.StringVar(value="Pixel format: --")
        self.timestamp_var = tk.StringVar(value="Last frame timestamp: --")
        self.message_var = tk.StringVar(value="")

        for var in (
            self.frame_seq_var,
            self.max_pixel_var,
            self.shape_var,
            self.pixel_format_var,
            self.timestamp_var,
            self.message_var,
        ):
            ttk.Label(self, textvariable=var, anchor="w").pack(fill="x", pady=1)

    def update_from_packet(self, packet) -> None:
        meta = packet.meta or {}
        preview = packet.preview_bgr
        raw = packet.raw

        self.update_preview(preview)
        self.frame_seq_var.set(f"Frame sequence: {meta.get('frame_seq', meta.get('seq', '--'))}")
        try:
            max_pixel = float(np.max(raw))
            self.max_pixel_var.set(f"Max pixel: {max_pixel:.0f}")
        except Exception:
            self.max_pixel_var.set("Max pixel: --")

        width = meta.get("width")
        height = meta.get("height")
        if width is None or height is None:
            height, width = raw.shape[:2]
        self.shape_var.set(f"Size: {width} x {height}")
        self.pixel_format_var.set(f"Pixel format: {meta.get('format', '--')}")
        self.timestamp_var.set(f"Last frame timestamp: {meta.get('timestamp_ns', '--')}")
        self.message_var.set("")

    def update_from_status(self, status) -> None:
        if status is None:
            return
        if status.camera_frame_seq is not None:
            self.frame_seq_var.set(f"Frame sequence: {status.camera_frame_seq}")
        if status.camera_max_pixel is not None:
            self.max_pixel_var.set(f"Max pixel: {status.camera_max_pixel:.0f}")

    def update_preview(self, preview_bgr: np.ndarray | None) -> None:
        if preview_bgr is None:
            return
        if not _HAS_PIL:
            self.message_var.set("Pillow not installed; preview disabled")
            return
        if preview_bgr.ndim != 3 or preview_bgr.shape[2] != 3:
            self.message_var.set("Invalid preview frame shape")
            return

        if cv2 is not None:
            rgb = cv2.cvtColor(preview_bgr, cv2.COLOR_BGR2RGB)
        else:
            rgb = preview_bgr[:, :, ::-1]
        image = Image.fromarray(rgb)
        max_w, max_h = 760, 520
        w, h = image.size
        scale = min(max_w / max(w, 1), max_h / max(h, 1), 1.0)
        if scale < 1.0:
            image = image.resize((max(1, int(w * scale)), max(1, int(h * scale))))
        self._photo = ImageTk.PhotoImage(image=image)
        self._image_label.configure(image=self._photo, text="")

    def show_message(self, message: str) -> None:
        self.message_var.set(message)
        if self._photo is None:
            self._image_label.configure(text=message or "Camera stream unavailable")

