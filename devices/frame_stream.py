from __future__ import annotations

import json
import os
from dataclasses import dataclass
from multiprocessing import shared_memory
from threading import get_ident
from typing import Optional

import cv2
import numpy as np
import zmq

from .camera_service import DEFAULT_PORT_PUB, DEFAULT_SHM_NAME


_RAW_DEFAULT_BAYER_PATTERN = os.environ.get("CAM_BAYER_PATTERN", "").strip().upper()
DEFAULT_BAYER_PATTERN: str | None = _RAW_DEFAULT_BAYER_PATTERN or None
_BAYER_TO_CV_CODE = {
    "BG": cv2.COLOR_BayerBG2BGR,
    "GB": cv2.COLOR_BayerGB2BGR,
    "RG": cv2.COLOR_BayerRG2BGR,
    "GR": cv2.COLOR_BayerGR2BGR,
}


@dataclass
class FramePacket:
    raw: np.ndarray
    preview_bgr: np.ndarray
    meta: dict


class FrameStreamClient:
    """
    Subscriber + shared-memory reader for the frame stream.

    Important:
    - One FrameStreamClient per consumer thread/process.
    - The underlying ZMQ socket is created lazily in the consuming thread.
    """

    def __init__(
        self,
        pub_addr: str = f"tcp://127.0.0.1:{DEFAULT_PORT_PUB}",
        shm_name: str = DEFAULT_SHM_NAME,
        topic: bytes = b"frame",
        recv_timeout_ms: Optional[int] = None,
        bayer_pattern: str | None = DEFAULT_BAYER_PATTERN,
    ):
        self.pub_addr = pub_addr
        self.default_shm_name = shm_name
        self.topic = topic
        self.recv_timeout_ms = recv_timeout_ms
        self.bayer_pattern = self._normalize_bayer_pattern(bayer_pattern) if bayer_pattern else None

        self._ctx = zmq.Context.instance()
        self._sub: Optional[zmq.Socket] = None
        self._sub_thread_id: Optional[int] = None

        self._shm_name: Optional[str] = None
        self._shm: Optional[shared_memory.SharedMemory] = None
        self._last_packet: Optional[FramePacket] = None

    @staticmethod
    def _normalize_bayer_pattern(pattern: str) -> str:
        normalized = str(pattern).strip().upper()
        if normalized not in _BAYER_TO_CV_CODE:
            supported = ", ".join(sorted(_BAYER_TO_CV_CODE))
            raise ValueError(f"Unsupported Bayer pattern {pattern!r}; expected one of {supported}")
        return normalized

    @staticmethod
    def _resolve_bayer_pattern(meta: dict, default_bayer_pattern: str | None) -> tuple[str | None, str | None]:
        candidate = meta.get("bayer_pattern", default_bayer_pattern)
        if candidate is None or str(candidate).strip() == "":
            return None, "No Bayer pattern provided; raw preview uses default BayerGB."
        try:
            return FrameStreamClient._normalize_bayer_pattern(str(candidate)), None
        except ValueError:
            return None, f"Unsupported Bayer pattern {candidate!r}; raw preview uses default BayerGB."

    def _ensure_sub(self) -> None:
        current_thread_id = get_ident()
        if self._sub is not None:
            if self._sub_thread_id != current_thread_id:
                raise RuntimeError("FrameStreamClient cannot be used from multiple threads")
            return

        sub = self._ctx.socket(zmq.SUB)
        sub.set_hwm(1)
        sub.connect(self.pub_addr)
        sub.setsockopt(zmq.SUBSCRIBE, self.topic)
        sub.setsockopt(zmq.SUBSCRIBE, b"status")
        if self.recv_timeout_ms is not None:
            sub.RCVTIMEO = self.recv_timeout_ms

        self._sub = sub
        self._sub_thread_id = current_thread_id

    def _ensure_shm(self, shm_name: str) -> None:
        if self._shm is not None and self._shm_name == shm_name:
            return

        if self._shm is not None:
            try:
                self._shm.close()
            except Exception:
                pass

        self._shm = shared_memory.SharedMemory(name=shm_name)
        self._shm_name = shm_name

    @staticmethod
    def _decode_from_meta(
        shm: shared_memory.SharedMemory,
        meta: dict,
        default_bayer_pattern: str | None,
    ) -> tuple[np.ndarray, np.ndarray]:
        idx = int(meta["index"])
        width = int(meta["width"])
        height = int(meta["height"])
        stride = int(meta["stride"])
        pix_fmt = str(meta["format"]).strip().lower()
        bayer_pattern, bayer_warning = FrameStreamClient._resolve_bayer_pattern(meta, default_bayer_pattern)
        if bayer_warning:
            meta["preview_warning"] = bayer_warning
        _default_bayer_code = _BAYER_TO_CV_CODE.get("GB")
        bayer_code = _BAYER_TO_CV_CODE[bayer_pattern] if bayer_pattern else _default_bayer_code

        start = idx * stride * height
        end = start + stride * height
        mv = memoryview(shm.buf)[start:end]

        try:
            if pix_fmt == "rgb":
                img = np.ndarray((height, width, 3), dtype=np.uint8, buffer=mv).copy()
                raw = img
                preview_bgr = img
            elif pix_fmt == "rgb8":
                raw = np.ndarray((height, width, 3), dtype=np.uint8, buffer=mv).copy()
                preview_bgr = cv2.cvtColor(raw, cv2.COLOR_RGB2BGR)
            elif pix_fmt == "bgr8":
                raw = np.ndarray((height, width, 3), dtype=np.uint8, buffer=mv).copy()
                preview_bgr = raw
            elif pix_fmt == "raw8":
                raw = np.ndarray((height, width), dtype=np.uint8, buffer=mv).copy()
                preview_bgr = cv2.cvtColor(raw, bayer_code)
            elif pix_fmt == "mono8":
                raw = np.ndarray((height, width), dtype=np.uint8, buffer=mv).copy()
                preview_bgr = cv2.cvtColor(raw, cv2.COLOR_GRAY2BGR)
            elif pix_fmt == "raw16":
                raw = np.ndarray((height, width), dtype=np.uint16, buffer=mv).copy()
                preview16 = cv2.cvtColor(raw, bayer_code)
                preview_bgr = np.clip(preview16 / 256, 0, 255).astype(np.uint8)
            elif pix_fmt == "mono16":
                raw = np.ndarray((height, width), dtype=np.uint16, buffer=mv).copy()
                mono8 = np.clip(raw / 256, 0, 255).astype(np.uint8)
                preview_bgr = cv2.cvtColor(mono8, cv2.COLOR_GRAY2BGR)
            else:
                raise RuntimeError(f"Unsupported pixel format: {pix_fmt}")
        finally:
            mv.release()

        return raw, preview_bgr

    def recv_frame(self) -> FramePacket:
        self._ensure_sub()
        assert self._sub is not None

        while True:
            topic, payload = self._sub.recv_multipart()
            if topic == b"status":
                status = json.loads(payload)
                message = status.get("err") or status.get("message") or repr(status)
                raise RuntimeError(f"Camera sidecar reported stream status: {message}")

            if topic != self.topic:
                raise RuntimeError(f"Received unexpected topic {topic!r}")

            meta = json.loads(payload)
            shm_name = meta.get("shm", self.default_shm_name)
            self._ensure_shm(shm_name)
            assert self._shm is not None

            raw, preview_bgr = self._decode_from_meta(
                self._shm,
                meta,
                self.bayer_pattern,
            )
            packet = FramePacket(raw=raw, preview_bgr=preview_bgr, meta=meta)
            self._last_packet = packet
            return packet

    def latest_frame(self) -> Optional[FramePacket]:
        return self._last_packet

    def close(self) -> None:
        if self._sub is not None:
            try:
                self._sub.close(0)
            except Exception:
                pass
            self._sub = None
            self._sub_thread_id = None

        if self._shm is not None:
            try:
                self._shm.close()
            except Exception:
                pass
            self._shm = None
            self._shm_name = None

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass
