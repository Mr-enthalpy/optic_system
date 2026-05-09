from __future__ import annotations

import time
from dataclasses import asdict, dataclass, is_dataclass
from enum import Enum
from typing import Any

import numpy as np

PROTOCOL_VERSION = 2
BACKEND_NAME = "flycapture2_c"
DEFAULT_SHM_NAME = "flycap2_ring_A"
DEFAULT_RING_SIZE = 8


@dataclass(frozen=True)
class CapturedFrame:
    array: np.ndarray
    width: int
    height: int
    stride: int
    pixel_format: str
    timestamp: Any = None
    metadata: Any = None


@dataclass(frozen=True)
class FrameLayout:
    width: int
    height: int
    stride: int
    row_bytes: int
    frame_nbytes: int
    dtype: str
    shape: list[int]
    pixel_format: str
    format: str
    sdk_stride: int | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        if payload["sdk_stride"] is None:
            payload.pop("sdk_stride")
        return payload


def _enum_name(value: Any) -> str | None:
    name = getattr(value, "name", None)
    if name:
        return str(name)
    return None


def _pixel_format_name(pixel_format: Any, array: np.ndarray) -> str:
    name = _enum_name(pixel_format)
    if name:
        return name.upper()
    if pixel_format is not None:
        return str(pixel_format).strip().upper()
    if array.ndim == 3 and array.shape[2] == 3 and array.dtype == np.uint8:
        return "RGB8"
    if array.dtype == np.uint16:
        return "RAW16"
    return "RAW8"


def _metadata_format(pixel_format: str, dtype: str, channels: int) -> str:
    normalized = pixel_format.strip().upper().replace("-", "_")
    if normalized == "RAW8":
        return "raw8"
    if normalized == "RAW16":
        return "raw16"
    if normalized == "MONO8":
        return "mono8"
    if normalized == "MONO16":
        return "mono16"
    if normalized in {"RGB", "RGB8"}:
        return "rgb8"
    if normalized in {"BGR", "BGR8"}:
        return "bgr8"
    if channels == 3 and dtype == "uint8":
        return "rgb8"
    if dtype == "uint16":
        return "raw16"
    return "raw8"


def frame_layout_from_array(
    array: np.ndarray,
    *,
    pixel_format: Any = None,
    sdk_stride: int | None = None,
) -> FrameLayout:
    arr = np.asarray(array)
    if arr.ndim not in {2, 3}:
        raise ValueError(f"Unsupported frame shape {arr.shape!r}; expected 2D or 3D image.")
    if arr.ndim == 3 and arr.shape[2] not in {3, 4}:
        raise ValueError(f"Unsupported channel count in frame shape {arr.shape!r}.")

    height = int(arr.shape[0])
    width = int(arr.shape[1])
    channels = int(arr.shape[2]) if arr.ndim == 3 else 1
    dtype = str(arr.dtype)
    row_bytes = int(width * channels * arr.dtype.itemsize)
    shape = [height, width] if channels == 1 else [height, width, channels]
    pixel_format_name = _pixel_format_name(pixel_format, arr)
    return FrameLayout(
        width=width,
        height=height,
        stride=row_bytes,
        row_bytes=row_bytes,
        frame_nbytes=int(row_bytes * height),
        dtype=dtype,
        shape=shape,
        pixel_format=pixel_format_name,
        format=_metadata_format(pixel_format_name, dtype, channels),
        sdk_stride=int(sdk_stride) if sdk_stride is not None else None,
    )


def frame_layout_from_frame(frame: Any) -> FrameLayout:
    array = getattr(frame, "array", frame)
    return frame_layout_from_array(
        array,
        pixel_format=getattr(frame, "pixel_format", None),
        sdk_stride=getattr(frame, "stride", None),
    )


def _timestamp_to_dict(timestamp: Any) -> Any:
    if timestamp is None:
        return None
    fields = ("seconds", "microSeconds", "cycleSeconds", "cycleCount", "cycleOffset")
    if all(hasattr(timestamp, name) for name in fields):
        return {name: int(getattr(timestamp, name)) for name in fields}
    return _json_safe(timestamp)


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, Enum):
        return value.name
    if is_dataclass(value):
        return _json_safe(asdict(value))
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if hasattr(value, "_asdict"):
        return _json_safe(value._asdict())
    return str(value)


def build_frame_metadata(
    layout: FrameLayout | dict[str, Any],
    *,
    index: int,
    seq: int,
    shm_name: str = DEFAULT_SHM_NAME,
    ring_size: int = DEFAULT_RING_SIZE,
    timestamp_sdk: Any = None,
    ts_ns: int | None = None,
    dropped_frames: int = 0,
    embedded_metadata: Any = None,
) -> dict[str, Any]:
    layout_dict = layout.to_dict() if isinstance(layout, FrameLayout) else dict(layout)
    meta = {
        "protocol_version": PROTOCOL_VERSION,
        "backend": BACKEND_NAME,
        "shm": shm_name,
        "ring_size": int(ring_size),
        "index": int(index),
        "seq": int(seq),
        **layout_dict,
        "timestamp_sdk": _timestamp_to_dict(timestamp_sdk),
        "embedded_metadata": _json_safe(embedded_metadata),
        "ts_ns": int(time.time_ns() if ts_ns is None else ts_ns),
        "dropped_frames": int(dropped_frames),
    }
    if meta["timestamp_sdk"] is None:
        meta.pop("timestamp_sdk")
    if meta["embedded_metadata"] is None:
        meta.pop("embedded_metadata")
    return meta
