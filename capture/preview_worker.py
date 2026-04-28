from __future__ import annotations

from threading import Event, Lock, Thread
from typing import Callable, Optional

import numpy as np
import zmq

from devices.frame_stream import FramePacket, FrameStreamClient


FrameCallback = Callable[[FramePacket], None]
ErrorCallback = Callable[[Exception], None]


class PreviewWorker:
    """
    Background preview consumer.

    It only pulls frames from the frame stream and forwards them to callbacks.
    It does not own GUI logic or experiment orchestration.
    """

    def __init__(
        self,
        stream: FrameStreamClient,
        on_frame: Optional[FrameCallback] = None,
        on_error: Optional[ErrorCallback] = None,
        timeout_warning_count: int = 6,
    ):
        self.stream = stream
        self.on_frame = on_frame
        self.on_error = on_error
        self.timeout_warning_count = max(1, int(timeout_warning_count))

        self._stop = Event()
        self._thread: Optional[Thread] = None
        self._lock = Lock()

        self._latest_packet: Optional[FramePacket] = None
        self._max_pixels: float = 0.0

    @property
    def max_pixels(self) -> float:
        with self._lock:
            return float(self._max_pixels)

    @property
    def latest_packet(self) -> Optional[FramePacket]:
        with self._lock:
            return self._latest_packet

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = Thread(target=self._run, name="PreviewWorker", daemon=True)
        self._thread.start()

    def stop(self, join_timeout: float = 1.0) -> None:
        self._stop.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=join_timeout)

    def _run(self) -> None:
        timeout_count = 0
        try:
            while not self._stop.is_set():
                try:
                    packet = self.stream.recv_frame()
                except zmq.Again:
                    if self._stop.is_set():
                        break
                    timeout_count += 1
                    if timeout_count >= self.timeout_warning_count and self.on_error is not None:
                        timeout_count = 0
                        self.on_error(TimeoutError("Timed out waiting for preview frames"))
                    continue
                except Exception as exc:
                    if self._stop.is_set():
                        break
                    timeout_count = 0
                    if self.on_error is not None:
                        self.on_error(exc)
                    continue

                timeout_count = 0
                max_pixel = float(np.max(packet.raw))
                with self._lock:
                    self._latest_packet = packet
                    self._max_pixels = max_pixel

                if self.on_frame is not None:
                    try:
                        self.on_frame(packet)
                    except Exception as exc:
                        if self.on_error is not None:
                            self.on_error(exc)
        finally:
            self.stream.close()
