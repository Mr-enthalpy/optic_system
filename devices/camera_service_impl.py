from __future__ import annotations

import json
import sys
import threading
import time
from dataclasses import asdict, dataclass, field, is_dataclass
from enum import Enum
from multiprocessing import shared_memory
from typing import Any, Callable, Optional

import numpy as np
import zmq

PROTOCOL_VERSION = 2
BACKEND_NAME = "flycapture2_c"

RING = 8
PORT_PUB = 6100
PORT_REP = 6101
SHM_NAME = "flycap2_ring_A"


try:
    from flycapture2_c import Camera as FlyCapture2Camera
except Exception as exc:  # pragma: no cover - exercised through monkeypatch tests
    FlyCapture2Camera = None
    _fc2_interpret_pixel_format_bitfield = None
    _fc2_pixel_format_support = None
    _fc2_support_for_pixel_format = None
    _FLYCAPTURE2_IMPORT_ERROR: Exception | None = exc
    print(
        "[camera-service] Failed to import flycapture2_c. Install "
        "Mr-enthalpy/flycapture2_c as package 'flycapture2_c' in the sidecar "
        f"environment. Import error: {exc}",
        file=sys.stderr,
        flush=True,
    )
else:
    _FLYCAPTURE2_IMPORT_ERROR = None
    try:
        from flycapture2_c.pixel_format import (
            PIXEL_FORMAT_SUPPORT as _fc2_pixel_format_support,
            interpret_pixel_format_bitfield as _fc2_interpret_pixel_format_bitfield,
            support_for_pixel_format as _fc2_support_for_pixel_format,
        )
    except Exception:
        _fc2_interpret_pixel_format_bitfield = None
        _fc2_pixel_format_support = None
        _fc2_support_for_pixel_format = None


class CameraServiceError(RuntimeError):
    recoverable = True


class CameraStateError(CameraServiceError):
    pass


class UnsupportedOperationError(CameraServiceError):
    pass


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


def _flycapture2_import_error_message() -> str:
    if _FLYCAPTURE2_IMPORT_ERROR is None:
        return "flycapture2_c Camera backend is unavailable."
    return (
        "Unable to import flycapture2_c. Install Mr-enthalpy/flycapture2_c "
        "as package 'flycapture2_c' in the sidecar environment. "
        f"Original import error: {_FLYCAPTURE2_IMPORT_ERROR}"
    )


def is_backend_package_available() -> bool:
    return FlyCapture2Camera is not None and _FLYCAPTURE2_IMPORT_ERROR is None


def _enum_name(value: Any) -> str | None:
    name = getattr(value, "name", None)
    if name:
        return str(name)
    return None


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


def _object_to_dict(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if is_dataclass(value):
        return _json_safe(asdict(value))
    if isinstance(value, dict):
        return _json_safe(value)
    result: dict[str, Any] = {}
    for name in dir(value):
        if name.startswith("_"):
            continue
        try:
            item = getattr(value, name)
        except Exception:
            continue
        if callable(item):
            continue
        result[name] = _json_safe(item)
    return result


def pixel_format_support_to_dict(pixel_format: Any) -> dict[str, Any] | None:
    if _fc2_support_for_pixel_format is None:
        return None
    try:
        support = _fc2_support_for_pixel_format(pixel_format)
    except Exception:
        return None
    if support is None:
        return None
    return _json_safe(asdict(support) if is_dataclass(support) else _object_to_dict(support))


def read_frame_decodable_pixel_format_names() -> list[str]:
    if _fc2_pixel_format_support is None:
        return []
    names: list[str] = []
    for name, support in _fc2_pixel_format_support.items():
        if bool(getattr(support, "read_frame_decodable", False)):
            names.append(str(name))
    return sorted(names)


def _require_read_frame_decodable_pixel_format(pixel_format: Any) -> None:
    support = pixel_format_support_to_dict(pixel_format)
    if support is None:
        return
    if not bool(support.get("read_frame_decodable")):
        name = support.get("name") or str(pixel_format)
        raise UnsupportedOperationError(
            f"Pixel format {name} is known to flycapture2_c but is not decodable by read_frame(). "
            f"raw_copy_only={support.get('raw_copy_only')}, "
            f"compressed_or_unsupported={support.get('compressed_or_unsupported')}. "
            "Choose a read_frame-decodable format such as MONO8, MONO16, RAW8, RAW16, or RGB8."
        )


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


def _legacy_format(pixel_format: str, dtype: str, channels: int) -> str:
    normalized = pixel_format.strip().upper().replace("-", "_")
    if normalized in {"RAW8"}:
        return "raw8"
    if normalized in {"RAW16"}:
        return "raw16"
    if normalized in {"MONO8"}:
        return "mono8"
    if normalized in {"MONO16"}:
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
        format=_legacy_format(pixel_format_name, dtype, channels),
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


def build_frame_metadata(
    layout: FrameLayout | dict[str, Any],
    *,
    index: int,
    seq: int,
    shm_name: str = SHM_NAME,
    ring_size: int = RING,
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


def deprecated_preconfig_gui_reply() -> dict[str, Any]:
    return {
        "ok": False,
        "err": "PreConfigGUI is deprecated. Use explicit camera configuration commands instead.",
        "error_type": "DeprecatedOperation",
        "op": "PreConfigGUI",
        "recoverable": True,
        "replacement_ops": [
            "DisableTrigger",
            "SetPixelFormat",
            "SetROI",
            "SetProperty",
            "SetPropertyAuto",
            "SnapshotProperties",
        ],
    }


def error_reply(
    op: str,
    exc: BaseException | str,
    *,
    recoverable: bool = True,
    **extra: Any,
) -> dict[str, Any]:
    if isinstance(exc, BaseException):
        message = str(exc)
        error_type = exc.__class__.__name__
        recoverable = bool(getattr(exc, "recoverable", recoverable))
    else:
        message = str(exc)
        error_type = "CameraServiceError"
    return {
        "ok": False,
        "err": message,
        "error_type": error_type,
        "op": op,
        "recoverable": recoverable,
        **extra,
    }


def property_info_to_dict(info: Any) -> dict[str, Any]:
    if info is None:
        return {}
    property_type = getattr(info, "property_type", None)
    name = _enum_name(property_type) or str(property_type)
    return {
        "name": name,
        "present": bool(getattr(info, "present", False)),
        "readable": bool(getattr(info, "read_out_supported", False)),
        "writable": bool(
            getattr(info, "writable", False)
            or getattr(info, "manual_supported", False)
            or getattr(info, "auto_supported", False)
            or getattr(info, "on_off_supported", False)
        ),
        "auto_supported": bool(getattr(info, "auto_supported", False)),
        "manual_supported": bool(getattr(info, "manual_supported", False)),
        "on_off_supported": bool(getattr(info, "on_off_supported", False)),
        "one_push_supported": bool(getattr(info, "one_push_supported", False)),
        "abs_val_supported": bool(getattr(info, "abs_val_supported", False)),
        "read_out_supported": bool(getattr(info, "read_out_supported", False)),
        "min": int(getattr(info, "min_value", 0)),
        "max": int(getattr(info, "max_value", 0)),
        "abs_min": float(getattr(info, "abs_min", 0.0)),
        "abs_max": float(getattr(info, "abs_max", 0.0)),
        "units": str(getattr(info, "units", "")),
        "unit_abbr": str(getattr(info, "unit_abbr", "")),
    }


def property_value_to_dict(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    property_type = getattr(value, "property_type", None)
    name = _enum_name(property_type) or str(property_type)
    return {
        "name": name,
        "present": bool(getattr(value, "present", False)),
        "abs_control": bool(getattr(value, "abs_control", False)),
        "one_push": bool(getattr(value, "one_push", False)),
        "on": bool(getattr(value, "on_off", False)),
        "auto": bool(getattr(value, "auto_manual_mode", False)),
        "value_a": int(getattr(value, "value_a", 0)),
        "value_b": int(getattr(value, "value_b", 0)),
        "abs_value": float(getattr(value, "abs_value", 0.0)),
    }


def property_snapshot_to_dict(snapshot: Any) -> dict[str, Any]:
    if isinstance(snapshot, dict):
        return _json_safe(snapshot)
    info = getattr(snapshot, "info", None)
    value = getattr(snapshot, "value", None)
    property_type = getattr(snapshot, "property_type", None)
    name = _enum_name(property_type)
    info_dict = property_info_to_dict(info) if info is not None else {}
    value_dict = property_value_to_dict(value) if value is not None else {}
    result = {
        "name": name or info_dict.get("name") or value_dict.get("name"),
        "present": bool(getattr(snapshot, "present", info_dict.get("present", False))),
        "readable": bool(info_dict.get("readable", False)),
        "writable": bool(info_dict.get("writable", False)),
        "auto_supported": bool(info_dict.get("auto_supported", False)),
        "manual_supported": bool(info_dict.get("manual_supported", False)),
        "on_off_supported": bool(info_dict.get("on_off_supported", False)),
        "one_push_supported": bool(info_dict.get("one_push_supported", False)),
        "abs_val_supported": bool(info_dict.get("abs_val_supported", False)),
        "read_out_supported": bool(info_dict.get("read_out_supported", False)),
        "min": info_dict.get("min"),
        "max": info_dict.get("max"),
        "abs_min": info_dict.get("abs_min"),
        "abs_max": info_dict.get("abs_max"),
        "units": info_dict.get("units", ""),
        "unit_abbr": info_dict.get("unit_abbr", ""),
        "value_a": value_dict.get("value_a"),
        "value_b": value_dict.get("value_b"),
        "abs_value": value_dict.get("abs_value"),
        "auto": value_dict.get("auto"),
        "on": value_dict.get("on"),
        "error": getattr(snapshot, "error", None),
    }
    return _json_safe(result)


def trigger_mode_to_dict(mode: Any) -> dict[str, Any]:
    return {
        "on_off": bool(getattr(mode, "on_off", False)),
        "polarity": int(getattr(mode, "polarity", 0)),
        "source": int(getattr(mode, "source", 0)),
        "mode": int(getattr(mode, "mode", 0)),
        "parameter": int(getattr(mode, "parameter", 0)),
    }


def trigger_mode_info_to_dict(info: Any) -> dict[str, Any]:
    return _object_to_dict(info)


def format7_info_to_dict(info: Any) -> dict[str, Any]:
    result = _object_to_dict(info)
    supported = getattr(info, "supported_pixel_formats", None)
    if supported is not None:
        supported_names = [_enum_name(item) or str(item) for item in supported]
        result["supported_pixel_formats"] = supported_names
        details: dict[str, Any] = {}
        for name, item in zip(supported_names, supported):
            support = pixel_format_support_to_dict(item)
            if support is not None:
                details[name] = support
        result["supported_pixel_format_details"] = details
    bitfield = getattr(info, "pixel_format_bit_field", None)
    if bitfield is not None and _fc2_interpret_pixel_format_bitfield is not None:
        try:
            result["pixel_format_summary"] = _json_safe(_fc2_interpret_pixel_format_bitfield(int(bitfield)))
        except Exception as exc:
            result["pixel_format_summary_error"] = str(exc)
    return result


def format7_configuration_to_dict(config: Any) -> dict[str, Any]:
    result = _object_to_dict(config)
    settings = getattr(config, "settings", None)
    if settings is not None:
        result["settings"] = _object_to_dict(settings)
        pixel_format = getattr(settings, "pixel_format", None)
        if pixel_format is not None:
            result["settings"]["pixel_format"] = _enum_name(pixel_format) or str(pixel_format)
    return result


class MyCamLite:
    def __init__(self, cam: Any, *, index: int, context_type: str = "IIDC") -> None:
        self.cam = cam
        self.index = int(index)
        self.context_type = context_type
        self.camera_info: Any = None
        self.capabilities: dict[str, Any] = {}
        self.layout: FrameLayout | None = None
        self.setting_names: list[str] = []

    @classmethod
    def open(
        cls,
        *,
        index: int = 0,
        context_type: str = "IIDC",
        disable_trigger: bool = True,
        grab_timeout_ms: int | None = None,
        pixel_format: str | int | None = None,
        roi: dict[str, Any] | None = None,
        properties: list[dict[str, Any]] | None = None,
        camera_cls: Any = None,
    ) -> "MyCamLite":
        camera_cls = camera_cls or FlyCapture2Camera
        if camera_cls is None:
            raise ImportError(_flycapture2_import_error_message())

        cam = camera_cls.open(index=int(index))
        backend = cls(cam, index=index, context_type=context_type)
        try:
            backend.camera_info = backend.get_camera_info(refresh=True)
            backend.capabilities = backend._read_capabilities()
            if disable_trigger:
                backend.disable_trigger()
            backend.apply_config(
                grab_timeout_ms=grab_timeout_ms,
                pixel_format=pixel_format,
                roi=roi,
                properties=properties,
            )
            backend.start_capture()
            first_frame = backend._read_frame()
            backend.layout = frame_layout_from_frame(first_frame)
            backend.setting_names = backend._discover_setting_names()
            return backend
        except Exception:
            try:
                cam.close()
            except Exception:
                pass
            raise

    @property
    def is_open(self) -> bool:
        return bool(getattr(self.cam, "is_open", True))

    @property
    def is_capturing(self) -> bool:
        return bool(getattr(self.cam, "is_capturing", False))

    def _read_capabilities(self) -> dict[str, Any]:
        capabilities: dict[str, Any] = {}
        for name, fn in (
            ("trigger_mode_info", self.get_trigger_mode_info),
            ("configuration", self.get_configuration),
        ):
            try:
                capabilities[name] = _json_safe(fn())
            except Exception as exc:
                capabilities[name] = {"error": str(exc), "error_type": exc.__class__.__name__}
        try:
            capabilities["format7_info"] = format7_info_to_dict(self.get_format7_info(mode=0))
        except Exception as exc:
            capabilities["format7_info"] = {"error": str(exc), "error_type": exc.__class__.__name__}
        return capabilities

    def _discover_setting_names(self) -> list[str]:
        try:
            snapshots = self.snapshot_properties()
        except Exception:
            return []
        names = []
        for snapshot in snapshots:
            item = property_snapshot_to_dict(snapshot)
            if item.get("present") and item.get("name"):
                names.append(str(item["name"]))
        return sorted(set(names))

    def _read_frame(self) -> Any:
        if hasattr(self.cam, "read_frame_with_info"):
            return self.cam.read_frame_with_info()
        array = self.cam.read_frame()
        arr = np.asarray(array)
        pixel_format = "RAW16" if arr.dtype == np.uint16 else "RAW8"
        layout = frame_layout_from_array(arr, pixel_format=pixel_format)
        return CapturedFrame(
            array=arr,
            width=layout.width,
            height=layout.height,
            stride=layout.stride,
            pixel_format=layout.pixel_format,
        )

    def capture(self) -> tuple[np.ndarray, Any, FrameLayout]:
        frame = self._read_frame()
        array = np.ascontiguousarray(getattr(frame, "array", frame))
        layout = frame_layout_from_frame(frame)
        return array, frame, layout

    def apply_config(
        self,
        *,
        grab_timeout_ms: int | None = None,
        pixel_format: str | int | None = None,
        pixel_format_mode: int = 0,
        roi: dict[str, Any] | None = None,
        properties: list[dict[str, Any]] | None = None,
        disable_trigger: bool | None = None,
    ) -> None:
        if disable_trigger is True:
            self.disable_trigger()
        if grab_timeout_ms is not None:
            self.set_grab_timeout(grab_timeout_ms)
        if pixel_format is not None:
            _require_read_frame_decodable_pixel_format(pixel_format)
            self.set_pixel_format(pixel_format, mode=int(pixel_format_mode))
        if roi:
            self.set_roi(**roi)
        for item in properties or []:
            self.apply_property_config(item)

    def apply_property_config(self, item: dict[str, Any]) -> Any:
        name = item["name"]
        auto = item.get("auto")
        if "value" in item:
            return self.set_property_abs(name, float(item["value"]), auto=bool(auto) if auto is not None else False)
        if "value_a" in item or "value_b" in item:
            return self.set_property_integer(
                name,
                value_a=item.get("value_a"),
                value_b=item.get("value_b"),
                auto=bool(auto) if auto is not None else False,
            )
        if auto is not None:
            return self.set_property_auto(name, auto=bool(auto))
        raise ValueError(f"Property config for {name!r} must include value, value_a/value_b, or auto.")

    def get_camera_info(self, *, refresh: bool = False) -> Any:
        if hasattr(self.cam, "get_camera_info"):
            return self.cam.get_camera_info(refresh=refresh)
        return getattr(self.cam, "camera_info", None)

    def snapshot_properties(self) -> tuple[Any, ...]:
        if not hasattr(self.cam, "snapshot_properties"):
            return ()
        return tuple(self.cam.snapshot_properties())

    def get_property_info(self, name: str) -> Any:
        return self.cam.get_property_info(name)

    def get_property_value(self, name: str) -> Any:
        return self.cam.get_property(name)

    def set_property_abs(self, name: str, value: float, *, auto: bool = False) -> Any:
        return self.cam.set_property_abs(name, float(value), auto=bool(auto))

    def set_property_integer(
        self,
        name: str,
        *,
        value_a: int | None = None,
        value_b: int | None = None,
        auto: bool = False,
    ) -> Any:
        kwargs = {"auto": bool(auto)}
        if value_a is not None:
            kwargs["value_a"] = int(value_a)
        if value_b is not None:
            kwargs["value_b"] = int(value_b)
        return self.cam.set_property_integer(name, **kwargs)

    def set_property_auto(self, name: str, *, auto: bool) -> Any:
        return self.cam.set_property_auto(name, auto=bool(auto))

    def get_trigger_mode_info(self) -> Any:
        return self.cam.get_trigger_mode_info()

    def get_trigger_mode(self) -> Any:
        return self.cam.get_trigger_mode()

    def disable_trigger(self) -> Any:
        return self.cam.disable_trigger()

    def set_trigger_mode(self, **kwargs: Any) -> Any:
        return self.cam.set_trigger_mode(**kwargs)

    def get_configuration(self) -> Any:
        return self.cam.get_configuration()

    def set_grab_timeout(self, ms: int) -> Any:
        return self.cam.set_grab_timeout(int(ms))

    def get_format7_info(self, mode: int = 0) -> Any:
        return self.cam.get_format7_info(mode=int(mode))

    def get_format7_configuration(self) -> Any:
        return self.cam.get_format7_configuration()

    def validate_format7(self, **kwargs: Any) -> Any:
        return self.cam.validate_format7(**kwargs)

    def set_pixel_format(self, pixel_format: str | int, *, mode: int = 0) -> Any:
        return self.cam.set_pixel_format(pixel_format, mode=int(mode))

    def set_roi(
        self,
        *,
        offset_x: int = 0,
        offset_y: int = 0,
        width: int | None = None,
        height: int | None = None,
        mode: int = 0,
    ) -> Any:
        return self.cam.set_roi(
            offset_x=int(offset_x),
            offset_y=int(offset_y),
            width=None if width is None else int(width),
            height=None if height is None else int(height),
            mode=int(mode),
        )

    def start_capture(self) -> None:
        self.cam.start()

    def stop_capture(self) -> None:
        if hasattr(self.cam, "stop"):
            self.cam.stop()

    def close(self) -> None:
        try:
            self.stop_capture()
        finally:
            self.cam.close()


@dataclass
class CameraServiceState:
    camera_cls: Any = None
    cam: Optional[MyCamLite] = None
    shm: Optional[shared_memory.SharedMemory] = None
    layout: Optional[FrameLayout] = None
    running: bool = False
    widx: int = 0
    seq: int = 0
    dropped_frames: int = 0
    last_frame_ts_ns: int | None = None
    last_error: str | None = None
    lock: Any = field(default_factory=threading.RLock)
    stop_event: threading.Event = field(default_factory=threading.Event)

    def package_available(self) -> bool:
        return self.camera_cls is not None or is_backend_package_available()


def _release_shm(shm: Any) -> None:
    if shm is None:
        return
    try:
        shm.close()
    except Exception:
        pass
    try:
        shm.unlink()
    except FileNotFoundError:
        pass
    except Exception:
        pass


def _create_shm(size: int) -> shared_memory.SharedMemory:
    try:
        return shared_memory.SharedMemory(create=True, size=int(size), name=SHM_NAME)
    except FileExistsError:
        stale = shared_memory.SharedMemory(name=SHM_NAME)
        try:
            stale.close()
            stale.unlink()
        finally:
            pass
        return shared_memory.SharedMemory(create=True, size=int(size), name=SHM_NAME)


def _replace_shm_locked(state: CameraServiceState, layout: FrameLayout) -> None:
    old = state.shm
    state.shm = None
    _release_shm(old)
    state.shm = _create_shm(RING * layout.frame_nbytes)
    state.layout = layout
    state.widx = 0


def _close_camera_locked(state: CameraServiceState) -> None:
    state.running = False
    if state.cam is not None:
        try:
            state.cam.close()
        finally:
            state.cam = None
    _release_shm(state.shm)
    state.shm = None
    state.layout = None


def _require_camera(state: CameraServiceState) -> MyCamLite:
    if state.cam is None:
        raise CameraStateError("camera not opened")
    return state.cam


def _stream_status_locked(state: CameraServiceState) -> dict[str, Any]:
    layout = state.layout
    return {
        "ok": True,
        "running": bool(state.running),
        "camera_open": state.cam is not None,
        "capturing": bool(state.cam and state.cam.is_capturing),
        "seq": int(state.seq),
        "last_frame_ts_ns": state.last_frame_ts_ns,
        "last_error": state.last_error,
        "shm": SHM_NAME if state.shm is not None else None,
        "ring_size": RING if state.shm is not None else None,
        "width": layout.width if layout else 0,
        "height": layout.height if layout else 0,
        "stride": layout.stride if layout else 0,
        "row_bytes": layout.row_bytes if layout else 0,
        "frame_nbytes": layout.frame_nbytes if layout else 0,
        "dtype": layout.dtype if layout else None,
        "shape": layout.shape if layout else None,
        "pixel_format": layout.pixel_format if layout else None,
        "format": layout.format if layout else None,
    }


def _camera_info_payload(cam: MyCamLite) -> dict[str, Any]:
    raw_info = cam.get_camera_info(refresh=False)
    info_dict = _object_to_dict(raw_info)
    layout = cam.layout
    serial = info_dict.get("serial_number", info_dict.get("serial"))
    payload = {
        **info_dict,
        "serial": serial,
        "serial_number": serial,
        "model_name": info_dict.get("model_name", info_dict.get("modelName", "")),
        "vendor_name": info_dict.get("vendor_name", info_dict.get("vendorName", "")),
        "sensor_info": info_dict.get("sensor_info", ""),
        "sensor_resolution": info_dict.get("sensor_resolution", ""),
        "firmware_version": info_dict.get("firmware_version", ""),
        "interface_type": info_dict.get("interface_type"),
        "setting_names": list(cam.setting_names),
        "capabilities": _json_safe(cam.capabilities),
    }
    if layout is not None:
        payload.update(layout.to_dict())
        payload["pix_fmt"] = layout.format
    return _json_safe(payload)


def _get_property_range(cam: MyCamLite, name: str) -> dict[str, Any]:
    info = property_info_to_dict(cam.get_property_info(name))
    if not info.get("present"):
        raise UnsupportedOperationError(f"Property {name} is not present on this camera.")
    abs_supported = bool(info.get("abs_val_supported"))
    if abs_supported:
        range_values = [float(info["abs_min"]), float(info["abs_max"])]
    else:
        range_values = [int(info["min"]), int(info["max"])]
    return {
        "ok": True,
        "range": range_values,
        "units": info.get("units", ""),
        "integer_range": [int(info["min"]), int(info["max"])],
        "abs_supported": abs_supported,
        "info": info,
    }


def _get_property_value(cam: MyCamLite, name: str) -> dict[str, Any]:
    value = property_value_to_dict(cam.get_property_value(name))
    result_value = value["abs_value"] if value.get("abs_control") else value["value_a"]
    return {"ok": True, "value": result_value, "property": value}


def _set_property_abs(cam: MyCamLite, name: str, value: float, *, auto: bool = False) -> dict[str, Any]:
    info = property_info_to_dict(cam.get_property_info(name))
    if not info.get("present"):
        raise UnsupportedOperationError(f"Property {name} is not present on this camera.")
    if not info.get("abs_val_supported"):
        raise UnsupportedOperationError(
            f"Property {name} does not support absolute values. Use property-specific integer controls."
        )
    updated = cam.set_property_abs(name, float(value), auto=bool(auto))
    return {"ok": True, "property": property_value_to_dict(updated)}


def _reconfigure_locked(state: CameraServiceState, req: dict[str, Any]) -> dict[str, Any]:
    cam = _require_camera(state)
    if req.get("pixel_format") is not None:
        _require_read_frame_decodable_pixel_format(req["pixel_format"])
    was_running = bool(state.running)
    state.running = False
    cam.stop_capture()
    try:
        cam.apply_config(
            disable_trigger=req.get("disable_trigger"),
            grab_timeout_ms=req.get("grab_timeout_ms"),
            pixel_format=req.get("pixel_format"),
            pixel_format_mode=int(req.get("pixel_format_mode", req.get("mode", 0))),
            roi=req.get("roi"),
            properties=req.get("properties"),
        )
        cam.start_capture()
        first_frame = cam._read_frame()
        new_layout = frame_layout_from_frame(first_frame)
        cam.layout = new_layout
        cam.setting_names = cam._discover_setting_names()
        if state.layout != new_layout:
            _replace_shm_locked(state, new_layout)
        else:
            state.layout = new_layout
        state.running = was_running
        state.last_error = None
        return {"ok": True, "restarted": was_running, "layout": new_layout.to_dict(), "status": _stream_status_locked(state)}
    except Exception:
        state.running = False
        try:
            cam.start_capture()
        except Exception:
            pass
        raise


def handle_request(
    state: CameraServiceState,
    req: dict[str, Any],
    *,
    publish_status: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    op = str(req.get("op") or "")
    try:
        if op == "Ping":
            return {
                "ok": True,
                "ts_ns": time.time_ns(),
                "backend": BACKEND_NAME,
                "protocol_version": PROTOCOL_VERSION,
                "package_available": state.package_available(),
            }

        if op == "GetBackendInfo":
            with state.lock:
                return {
                    "ok": True,
                    "backend": BACKEND_NAME,
                    "protocol_version": PROTOCOL_VERSION,
                    "package_available": state.package_available(),
                    "camera_open": state.cam is not None,
                    "capturing": bool(state.cam and state.cam.is_capturing),
                    "running": bool(state.running),
                    "import_error": None if state.package_available() else _flycapture2_import_error_message(),
                    "pixel_format_support_matrix": _fc2_pixel_format_support is not None,
                    "read_frame_decodable_pixel_formats": read_frame_decodable_pixel_format_names(),
                }

        if op == "PreConfigGUI":
            return deprecated_preconfig_gui_reply()

        if op == "OpenCamera":
            with state.lock:
                _close_camera_locked(state)
                cam = MyCamLite.open(
                    index=int(req.get("index", 0)),
                    context_type=str(req.get("context_type", "IIDC")),
                    disable_trigger=bool(req.get("disable_trigger", True)),
                    grab_timeout_ms=req.get("grab_timeout_ms"),
                    pixel_format=req.get("pixel_format"),
                    roi=req.get("roi"),
                    properties=req.get("properties") or [],
                    camera_cls=state.camera_cls,
                )
                if cam.layout is None:
                    raise CameraStateError("camera opened but frame layout is unavailable")
                state.cam = cam
                state.seq = 0
                state.widx = 0
                state.dropped_frames = 0
                state.last_frame_ts_ns = None
                state.last_error = None
                _replace_shm_locked(state, cam.layout)
                info = _camera_info_payload(cam)
                reply = {
                    "ok": True,
                    "backend": BACKEND_NAME,
                    "protocol_version": PROTOCOL_VERSION,
                    "serial": info.get("serial"),
                    "width": cam.layout.width,
                    "height": cam.layout.height,
                    "stride": cam.layout.stride,
                    "format": cam.layout.format,
                    "pixel_format": cam.layout.pixel_format,
                    "dtype": cam.layout.dtype,
                    "shape": cam.layout.shape,
                    "shm": SHM_NAME,
                    "ring_size": RING,
                    "setting_names": list(cam.setting_names),
                    "info": info,
                }
                return reply

        if op == "GetCameraInfo":
            with state.lock:
                cam = _require_camera(state)
                return {"ok": True, "info": _camera_info_payload(cam)}

        if op == "StartStream":
            with state.lock:
                _require_camera(state)
                if state.shm is None or state.layout is None:
                    raise CameraStateError("shared memory is not ready")
                state.running = True
                state.last_error = None
                return _stream_status_locked(state)

        if op == "StopStream":
            with state.lock:
                state.running = False
                return _stream_status_locked(state)

        if op == "GetStreamStatus":
            with state.lock:
                return _stream_status_locked(state)

        if op == "SnapshotProperties":
            with state.lock:
                cam = _require_camera(state)
                return {"ok": True, "properties": [property_snapshot_to_dict(item) for item in cam.snapshot_properties()]}

        if op == "GetPropertyInfo":
            with state.lock:
                cam = _require_camera(state)
                name = str(req["name"])
                return {"ok": True, "info": property_info_to_dict(cam.get_property_info(name))}

        if op == "GetRange":
            with state.lock:
                return _get_property_range(_require_camera(state), str(req["name"]))

        if op == "GetValue":
            with state.lock:
                return _get_property_value(_require_camera(state), str(req["name"]))

        if op == "SetProperty":
            with state.lock:
                return _set_property_abs(
                    _require_camera(state),
                    str(req["name"]),
                    float(req["value"]),
                    auto=bool(req.get("auto", False)),
                )

        if op == "SetPropertyAuto":
            with state.lock:
                cam = _require_camera(state)
                updated = cam.set_property_auto(str(req["name"]), auto=bool(req.get("auto", True)))
                return {"ok": True, "property": property_value_to_dict(updated)}

        if op == "GetTriggerMode":
            with state.lock:
                cam = _require_camera(state)
                return {
                    "ok": True,
                    "trigger": trigger_mode_to_dict(cam.get_trigger_mode()),
                    "info": trigger_mode_info_to_dict(cam.get_trigger_mode_info()),
                }

        if op == "DisableTrigger":
            with state.lock:
                mode = _require_camera(state).disable_trigger()
                return {"ok": True, "trigger": trigger_mode_to_dict(mode)}

        if op == "SetTriggerMode":
            with state.lock:
                kwargs = {
                    key: req[key]
                    for key in ("on_off", "source", "mode", "polarity", "parameter")
                    if key in req
                }
                mode = _require_camera(state).set_trigger_mode(**kwargs)
                return {"ok": True, "trigger": trigger_mode_to_dict(mode)}

        if op == "GetFormat7Info":
            with state.lock:
                info = _require_camera(state).get_format7_info(mode=int(req.get("mode", 0)))
                return {"ok": True, "info": format7_info_to_dict(info)}

        if op == "GetFormat7Configuration":
            with state.lock:
                config = _require_camera(state).get_format7_configuration()
                return {"ok": True, "configuration": format7_configuration_to_dict(config)}

        if op == "ValidateFormat7":
            with state.lock:
                kwargs = {
                    "mode": int(req.get("mode", 0)),
                    "offset_x": int(req.get("offset_x", 0)),
                    "offset_y": int(req.get("offset_y", 0)),
                    "width": req.get("width"),
                    "height": req.get("height"),
                    "pixel_format": req.get("pixel_format", "MONO8"),
                }
                validation = _require_camera(state).validate_format7(**kwargs)
                return {"ok": True, "validation": _json_safe(validation)}

        if op == "SetPixelFormat":
            with state.lock:
                req2 = {
                    "pixel_format": req["pixel_format"],
                    "pixel_format_mode": int(req.get("mode", 0)),
                }
                reply = _reconfigure_locked(state, req2)
                if publish_status:
                    publish_status(reply["status"])
                return reply

        if op == "SetROI":
            with state.lock:
                roi = {
                    key: req[key]
                    for key in ("offset_x", "offset_y", "width", "height", "mode")
                    if key in req
                }
                reply = _reconfigure_locked(state, {"roi": roi})
                if publish_status:
                    publish_status(reply["status"])
                return reply

        if op == "SetGrabTimeout":
            with state.lock:
                reply = _reconfigure_locked(state, {"grab_timeout_ms": int(req["grab_timeout_ms"])})
                if publish_status:
                    publish_status(reply["status"])
                return reply

        if op == "ReconfigureCamera":
            with state.lock:
                reply = _reconfigure_locked(state, req)
                if publish_status:
                    publish_status(reply["status"])
                return reply

        if op == "CloseCamera":
            with state.lock:
                _close_camera_locked(state)
                state.last_error = None
                return {"ok": True, "service_running": True}

        if op == "Shutdown":
            with state.lock:
                _close_camera_locked(state)
                state.stop_event.set()
                return {"ok": True, "service_running": False}

        return error_reply(op, "unknown op", error_type="UnknownOperation")
    except Exception as exc:
        with state.lock:
            state.last_error = str(exc)
        return error_reply(op, exc)


def stream_loop(state: CameraServiceState, pub: zmq.Socket) -> None:
    last_error_report_ns = 0
    while not state.stop_event.is_set():
        with state.lock:
            should_run = bool(state.running and state.cam is not None and state.shm is not None and state.layout is not None)
        if not should_run:
            time.sleep(0.01)
            continue

        try:
            with state.lock:
                if not (state.running and state.cam is not None and state.shm is not None and state.layout is not None):
                    continue
                array, frame, observed_layout = state.cam.capture()
                if observed_layout != state.layout:
                    raise CameraStateError(
                        "Frame layout changed while streaming. Stop stream or use ReconfigureCamera first."
                    )

                slot_nbytes = state.layout.frame_nbytes
                start = state.widx * slot_nbytes
                end = start + slot_nbytes
                frame_bytes = np.ascontiguousarray(array).tobytes()
                if len(frame_bytes) != slot_nbytes:
                    raise CameraStateError(
                        f"Frame byte size {len(frame_bytes)} does not match shared memory slot {slot_nbytes}."
                    )
                mv = memoryview(state.shm.buf)[start:end]
                try:
                    mv[:] = frame_bytes
                finally:
                    mv.release()

                meta = build_frame_metadata(
                    state.layout,
                    index=state.widx,
                    seq=state.seq,
                    timestamp_sdk=getattr(frame, "timestamp", None),
                    embedded_metadata=getattr(frame, "metadata", None),
                    dropped_frames=state.dropped_frames,
                )
                state.widx = (state.widx + 1) % RING
                state.seq += 1
                state.last_frame_ts_ns = int(meta["ts_ns"])
                state.last_error = None

            pub.send_multipart([b"frame", json.dumps(meta).encode("utf-8")])
        except Exception as exc:
            now = time.time_ns()
            with state.lock:
                state.last_error = str(exc)
                state.dropped_frames += 1
            if now - last_error_report_ns > 1_000_000_000:
                last_error_report_ns = now
                payload = error_reply("StreamLoop", exc)
                payload["ts_ns"] = now
                try:
                    pub.send_multipart([b"status", json.dumps(payload).encode("utf-8")])
                except Exception:
                    pass
            time.sleep(0.2)


def _publish_status(pub: zmq.Socket, payload: dict[str, Any]) -> None:
    status = {"protocol_version": PROTOCOL_VERSION, "backend": BACKEND_NAME, "ts_ns": time.time_ns(), **payload}
    pub.send_multipart([b"status", json.dumps(_json_safe(status)).encode("utf-8")])


def control_loop(state: CameraServiceState, rep: zmq.Socket, pub: zmq.Socket) -> None:
    rep.RCVTIMEO = 200
    while not state.stop_event.is_set():
        try:
            req = rep.recv_json(flags=0)
        except zmq.Again:
            continue
        except zmq.ZMQError:
            break
        reply = handle_request(state, req, publish_status=lambda payload: _publish_status(pub, payload))
        try:
            rep.send_json(reply)
        except zmq.ZMQError:
            break


def main() -> None:
    ctx = zmq.Context.instance()
    pub = ctx.socket(zmq.PUB)
    rep = ctx.socket(zmq.REP)
    try:
        pub.set_hwm(1)
        pub.bind(f"tcp://127.0.0.1:{PORT_PUB}")
        rep.bind(f"tcp://127.0.0.1:{PORT_REP}")
    except zmq.ZMQError as exc:
        print(f"[FATAL] Camera service port bind failed: {exc}", file=sys.stderr, flush=True)
        pub.close(0)
        rep.close(0)
        ctx.term()
        sys.exit(1)

    state = CameraServiceState()
    t_stream = threading.Thread(target=stream_loop, args=(state, pub), daemon=True)
    t_ctrl = threading.Thread(target=control_loop, args=(state, rep, pub), daemon=True)
    t_stream.start()
    t_ctrl.start()

    try:
        while not state.stop_event.is_set():
            time.sleep(0.2)
    except KeyboardInterrupt:
        state.stop_event.set()
    finally:
        state.stop_event.set()
        with state.lock:
            _close_camera_locked(state)
        t_stream.join(1.0)
        t_ctrl.join(1.0)
        pub.close(0)
        rep.close(0)
        ctx.term()


if __name__ == "__main__":
    main()
