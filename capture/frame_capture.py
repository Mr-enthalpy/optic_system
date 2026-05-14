from __future__ import annotations

import time
from typing import Optional

import numpy as np

from devices.frame_stream import FramePacket, FrameStreamClient


class FrameCaptureHelper:
    """
    Capture semantics layer.

    Important:
    - It should use its own FrameStreamClient instance.
    - Do not reuse the same SUB socket used by PreviewWorker.
    """

    def __init__(self, stream: FrameStreamClient):
        self.stream = stream

    def capture_one(self, timeout_s: Optional[float] = 5.0) -> tuple[np.ndarray, np.ndarray]:
        packet = self.capture_one_packet(timeout_s=timeout_s)
        return packet.raw, packet.preview_bgr

    def capture_one_packet(self, timeout_s: Optional[float] = 5.0) -> FramePacket:
        t0 = time.time()
        while True:
            try:
                packet = self.stream.recv_frame()
                return packet
            except Exception as e:
                if timeout_s is not None and (time.time() - t0) > timeout_s:
                    raise RuntimeError(f"获取图像超时: {e}") from e

    def capture_average(
        self,
        n: int = 100,
        timeout_s: Optional[float] = 10.0,
    ) -> tuple[np.ndarray, np.ndarray]:
        if n <= 0:
            raise ValueError("n must be positive")

        raw_acc: Optional[np.ndarray] = None
        rgb_acc: Optional[np.ndarray] = None

        t0 = time.time()
        got = 0
        while got < n:
            try:
                packet = self.stream.recv_frame()
            except Exception as e:
                if timeout_s is not None and (time.time() - t0) > timeout_s:
                    raise RuntimeError(f"获取平均图像超时: {e}") from e
                continue

            raw = packet.raw.astype(np.float64, copy=False)
            rgb = packet.preview_bgr.astype(np.float64, copy=False)

            if raw_acc is None:
                raw_acc = np.zeros_like(raw, dtype=np.float64)
            if rgb_acc is None:
                rgb_acc = np.zeros_like(rgb, dtype=np.float64)

            raw_acc += raw
            rgb_acc += rgb
            got += 1

        raw_mean = raw_acc / n
        rgb_mean = rgb_acc / n

        # 保持和旧 cam.py 接近的返回语义
        if packet.meta["format"] == "raw16":
            rgb_mean = rgb_mean / 256.0

        rgb_mean = np.clip(rgb_mean, 0, 255).astype(np.uint8)
        return raw_mean, rgb_mean
