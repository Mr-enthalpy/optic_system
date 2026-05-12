from __future__ import annotations

from dataclasses import asdict, is_dataclass
from enum import Enum
from typing import Any

import numpy as np


class CameraServiceError(RuntimeError):
    recoverable = True


class CameraStateError(CameraServiceError):
    pass


class UnsupportedOperationError(CameraServiceError):
    pass


def enum_name(value: Any) -> str | None:
    name = getattr(value, "name", None)
    if name:
        return str(name)
    return None


def json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, Enum):
        return value.name
    if is_dataclass(value):
        return json_safe(asdict(value))
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_safe(item) for item in value]
    if hasattr(value, "_asdict"):
        return json_safe(value._asdict())
    return str(value)


def object_to_dict(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if is_dataclass(value):
        return json_safe(asdict(value))
    if isinstance(value, dict):
        return json_safe(value)
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
        result[name] = json_safe(item)
    return result


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
    name = enum_name(property_type) or str(property_type)
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
    name = enum_name(property_type) or str(property_type)
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
        return json_safe(snapshot)
    info = getattr(snapshot, "info", None)
    value = getattr(snapshot, "value", None)
    property_type = getattr(snapshot, "property_type", None)
    name = enum_name(property_type)
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
    if hasattr(snapshot, "display_value"):
        result["display_value"] = getattr(snapshot, "display_value")
    elif result["abs_val_supported"]:
        result["display_value"] = result["abs_value"]
    else:
        result["display_value"] = result["value_a"]

    if hasattr(snapshot, "display_range"):
        result["display_range"] = getattr(snapshot, "display_range")
    elif result["abs_val_supported"]:
        result["display_range"] = [result["abs_min"], result["abs_max"]]
    else:
        result["display_range"] = [result["min"], result["max"]]

    if hasattr(snapshot, "readback_policy"):
        result["readback_policy"] = getattr(snapshot, "readback_policy")
    else:
        result["readback_policy"] = "abs_value" if result["abs_val_supported"] else "value_a"
    return json_safe(result)


def trigger_mode_to_dict(mode: Any) -> dict[str, Any] | None:
    if mode is None:
        return None
    return {
        "on_off": bool(getattr(mode, "on_off", False)),
        "polarity": int(getattr(mode, "polarity", 0)),
        "source": int(getattr(mode, "source", 0)),
        "mode": int(getattr(mode, "mode", 0)),
        "parameter": int(getattr(mode, "parameter", 0)),
    }


def trigger_mode_info_to_dict(info: Any) -> dict[str, Any]:
    return object_to_dict(info)


def trigger_change_summary(
    *,
    requested: bool,
    before: Any = None,
    after: Any = None,
    error: BaseException | None = None,
) -> dict[str, Any]:
    before_dict = trigger_mode_to_dict(before)
    after_dict = trigger_mode_to_dict(after)
    return {
        "requested": bool(requested),
        "applied": after_dict is not None and error is None if requested else False,
        "changed": (before_dict != after_dict) if before_dict is not None and after_dict is not None else False,
        "before": before_dict,
        "after": after_dict,
        "error": str(error) if error is not None else None,
        "error_type": error.__class__.__name__ if error is not None else None,
    }


def format7_configuration_to_dict(config: Any) -> dict[str, Any]:
    result = object_to_dict(config)
    settings = getattr(config, "settings", None)
    if settings is not None:
        result["settings"] = object_to_dict(settings)
        pixel_format = getattr(settings, "pixel_format", None)
        if pixel_format is not None:
            result["settings"]["pixel_format"] = enum_name(pixel_format) or str(pixel_format)
    return result
