from __future__ import annotations

import sys
from dataclasses import asdict, is_dataclass
from typing import Any

import numpy as np

try:
    from .camera_frame_layout import CapturedFrame, FrameLayout, frame_layout_from_array, frame_layout_from_frame
    from .camera_protocol import (
        UnsupportedOperationError,
        enum_name,
        json_safe,
        object_to_dict,
        property_snapshot_to_dict,
        trigger_change_summary,
    )
except ImportError:  # pragma: no cover - direct script execution path
    from camera_frame_layout import CapturedFrame, FrameLayout, frame_layout_from_array, frame_layout_from_frame
    from camera_protocol import (
        UnsupportedOperationError,
        enum_name,
        json_safe,
        object_to_dict,
        property_snapshot_to_dict,
        trigger_change_summary,
    )

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


def flycapture2_import_error_message() -> str:
    if _FLYCAPTURE2_IMPORT_ERROR is None:
        return "flycapture2_c Camera backend is unavailable."
    return (
        "Unable to import flycapture2_c. Install Mr-enthalpy/flycapture2_c "
        "as package 'flycapture2_c' in the sidecar environment. "
        f"Original import error: {_FLYCAPTURE2_IMPORT_ERROR}"
    )


def is_backend_package_available() -> bool:
    return FlyCapture2Camera is not None and _FLYCAPTURE2_IMPORT_ERROR is None


def pixel_format_support_to_dict(pixel_format: Any) -> dict[str, Any] | None:
    if _fc2_support_for_pixel_format is None:
        return None
    try:
        support = _fc2_support_for_pixel_format(pixel_format)
    except Exception:
        return None
    if support is None:
        return None
    return json_safe(asdict(support) if is_dataclass(support) else object_to_dict(support))


def read_frame_decodable_pixel_format_names() -> list[str]:
    if _fc2_pixel_format_support is None:
        return []
    names: list[str] = []
    for name, support in _fc2_pixel_format_support.items():
        if bool(getattr(support, "read_frame_decodable", False)):
            names.append(str(name))
    return sorted(names)


def has_pixel_format_support_matrix() -> bool:
    return _fc2_pixel_format_support is not None


def require_read_frame_decodable_pixel_format(pixel_format: Any) -> None:
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


def format7_info_to_dict(info: Any) -> dict[str, Any]:
    result = object_to_dict(info)
    supported = getattr(info, "supported_pixel_formats", None)
    if supported is not None:
        supported_names = [enum_name(item) or str(item) for item in supported]
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
            result["pixel_format_summary"] = json_safe(_fc2_interpret_pixel_format_bitfield(int(bitfield)))
        except Exception as exc:
            result["pixel_format_summary_error"] = str(exc)
    return result


def _format7_validation_payload(validation: Any) -> dict[str, Any]:
    payload = json_safe(validation)
    valid = bool(getattr(validation, "settings_are_valid", True))
    if not valid:
        raise UnsupportedOperationError(f"Format7 settings are not valid: {payload!r}")
    return payload


class MyCamLite:
    def __init__(self, cam: Any, *, index: int, context_type: str = "IIDC") -> None:
        self.cam = cam
        self.index = int(index)
        self.context_type = context_type
        self.camera_info: Any = None
        self.capabilities: dict[str, Any] = {}
        self.layout: FrameLayout | None = None
        self.setting_names: list[str] = []
        self.configuration_applied: dict[str, Any] = {}

    @classmethod
    def open(
        cls,
        *,
        index: int = 0,
        context_type: str = "IIDC",
        disable_trigger: bool = False,
        grab_timeout_ms: int | None = None,
        pixel_format: str | int | None = None,
        roi: dict[str, Any] | None = None,
        properties: list[dict[str, Any]] | None = None,
        camera_cls: Any = None,
    ) -> "MyCamLite":
        camera_cls = camera_cls or FlyCapture2Camera
        if camera_cls is None:
            raise ImportError(flycapture2_import_error_message())

        cam = camera_cls.open(index=int(index))
        backend = cls(cam, index=index, context_type=context_type)
        try:
            backend.camera_info = backend.get_camera_info(refresh=True)
            backend.capabilities = backend._read_capabilities()
            backend.validate_config(pixel_format=pixel_format, roi=roi)

            trigger_summary = backend.apply_disable_trigger_if_requested(disable_trigger)
            backend.apply_config(
                grab_timeout_ms=grab_timeout_ms,
                pixel_format=pixel_format,
                roi=roi,
                properties=properties,
                validate=False,
            )
            backend.configuration_applied = {
                "disable_trigger": trigger_summary,
                "grab_timeout_ms": grab_timeout_ms,
                "pixel_format": pixel_format,
                "roi": roi,
                "properties": list(properties or []),
            }
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

    def apply_disable_trigger_if_requested(self, requested: bool) -> dict[str, Any]:
        if not requested:
            return trigger_change_summary(requested=False)
        before = after = None
        try:
            before = self.get_trigger_mode()
            after = self.disable_trigger()
            return trigger_change_summary(requested=True, before=before, after=after)
        except Exception as exc:
            return trigger_change_summary(requested=True, before=before, after=after, error=exc)

    def _read_capabilities(self) -> dict[str, Any]:
        capabilities: dict[str, Any] = {
            "pixel_format_support_matrix": has_pixel_format_support_matrix(),
            "read_frame_decodable_pixel_formats": read_frame_decodable_pixel_format_names(),
        }
        for name, fn in (
            ("trigger_mode_info", self.get_trigger_mode_info),
            ("configuration", self.get_configuration),
        ):
            try:
                capabilities[name] = json_safe(fn())
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
        pixel_format = "RGB8" if arr.ndim == 3 else "RAW16" if arr.dtype == np.uint16 else "RAW8"
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

    def validate_config(
        self,
        *,
        pixel_format: str | int | None = None,
        roi: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        validation: dict[str, Any] = {}
        if pixel_format is not None:
            require_read_frame_decodable_pixel_format(pixel_format)

        if pixel_format is None and not roi:
            return validation

        format7_kwargs = self._format7_validation_kwargs(pixel_format=pixel_format, roi=roi)
        if format7_kwargs is not None:
            validation["format7"] = _format7_validation_payload(self.validate_format7(**format7_kwargs))
        return validation

    def _format7_validation_kwargs(
        self,
        *,
        pixel_format: str | int | None,
        roi: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        roi = dict(roi or {})
        mode = int(roi.get("mode", 0))
        current = None
        try:
            current = self.get_format7_configuration()
        except Exception:
            pass

        settings = getattr(current, "settings", None)
        current_pixel_format = getattr(settings, "pixel_format", None)
        if pixel_format is None and current_pixel_format is None:
            pixel_format = "MONO8"
        elif pixel_format is None:
            pixel_format = current_pixel_format

        return {
            "mode": mode,
            "offset_x": int(roi.get("offset_x", getattr(settings, "offset_x", 0) if settings else 0)),
            "offset_y": int(roi.get("offset_y", getattr(settings, "offset_y", 0) if settings else 0)),
            "width": roi.get("width", getattr(settings, "width", None) if settings else None),
            "height": roi.get("height", getattr(settings, "height", None) if settings else None),
            "pixel_format": pixel_format,
        }

    def apply_config(
        self,
        *,
        grab_timeout_ms: int | None = None,
        pixel_format: str | int | None = None,
        pixel_format_mode: int = 0,
        roi: dict[str, Any] | None = None,
        properties: list[dict[str, Any]] | None = None,
        disable_trigger: bool | None = None,
        validate: bool = True,
    ) -> None:
        if validate:
            self.validate_config(pixel_format=pixel_format, roi=roi)
        if disable_trigger is True:
            self.disable_trigger()
        if grab_timeout_ms is not None:
            self.set_grab_timeout(grab_timeout_ms)
        if pixel_format is not None:
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
