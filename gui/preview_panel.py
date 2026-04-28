from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Optional

import cv2
import numpy as np

try:
    from PIL import Image, ImageTk

    _HAS_PIL = True
except Exception:
    _HAS_PIL = False


class PreviewPanel(ttk.LabelFrame):
    """
    Live preview + lightweight frame diagnostics.
    """

    def __init__(self, master, max_pixel_max: int = 65535):
        super().__init__(master, text="Live Preview", padding=8)

        self._photo = None
        self._img_label = ttk.Label(self, text="No frame yet", anchor="center")
        self._img_label.pack(fill="both", expand=True, pady=4)

        stats_row = ttk.Frame(self)
        stats_row.pack(fill="x", pady=6)

        ttk.Label(stats_row, text="Max Pixel:").pack(side="left")

        self.max_pixel_var = tk.StringVar(value="--")
        ttk.Label(stats_row, textvariable=self.max_pixel_var, width=8).pack(side="left", padx=6)

        self.max_pixel_bar = ttk.Progressbar(
            stats_row,
            length=240,
            mode="determinate",
            maximum=max_pixel_max,
        )
        self.max_pixel_bar.pack(side="left", padx=6)

        self.display_info_var = tk.StringVar(value="Display: --")
        self.frame_info_var = tk.StringVar(value="Frame: --")

        ttk.Label(self, textvariable=self.display_info_var, anchor="w").pack(fill="x")
        ttk.Label(self, textvariable=self.frame_info_var, anchor="w").pack(fill="x")

    def update_preview(self, preview_bgr: Optional[np.ndarray]) -> None:
        if preview_bgr is None:
            return

        if not _HAS_PIL:
            self.display_info_var.set("Display: Pillow not installed; preview disabled")
            return

        if preview_bgr.ndim != 3 or preview_bgr.shape[2] != 3:
            self.display_info_var.set("Display: invalid preview frame shape")
            return

        rgb = cv2.cvtColor(preview_bgr, cv2.COLOR_BGR2RGB)
        image = Image.fromarray(rgb)

        max_w, max_h = 800, 500
        w, h = image.size
        scale = min(max_w / max(w, 1), max_h / max(h, 1), 1.0)
        if scale < 1.0:
            image = image.resize((int(w * scale), int(h * scale)))

        self._photo = ImageTk.PhotoImage(image=image)
        self._img_label.configure(image=self._photo, text="")
        self.display_info_var.set(f"Display: {preview_bgr.shape[1]} x {preview_bgr.shape[0]}")

    def update_stats(
        self,
        *,
        max_pixel: float,
        frame_seq: int | None,
        timestamp_ns: int | None,
        width: int,
        height: int,
        stride: int,
        pixel_format: str | None,
    ) -> None:
        self.update_max_pixel(max_pixel)

        dims = f"{width} x {height}" if width and height else "--"
        seq_text = "--" if frame_seq is None or frame_seq < 0 else str(frame_seq)
        ts_text = "--" if timestamp_ns in (None, 0) else str(timestamp_ns)
        stride_text = str(stride) if stride else "--"
        pix_fmt_text = pixel_format or "--"

        self.frame_info_var.set(
            f"Frame: {dims} | stride {stride_text} | {pix_fmt_text} | seq {seq_text} | ts {ts_text}"
        )

    def update_max_pixel(self, max_pixel: float) -> None:
        self.max_pixel_var.set(f"{max_pixel:.0f}")
        vmax = int(self.max_pixel_bar["maximum"])
        self.max_pixel_bar["value"] = max(0, min(vmax, int(max_pixel)))
