from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping

import h5py


SUPPORTED_COORDINATE_FRAMES = {"sensor_full_frame", "acquired_frame"}
CAMERA_FRAME_EXTENT_DATASET_PRIORITY = (
    "frame_extent_json",
    "acquired_frame_extent_json",
)


@dataclass(frozen=True)
class CameraFrameExtent:
    mode: str
    origin_xy: tuple[int, int]
    shape_hw: tuple[int, int]
    sensor_shape_hw: tuple[int, int] | None = None
    source: str | None = None


@dataclass(frozen=True)
class CoordinateFrameDescriptor:
    coordinate_frame: str
    camera_frame_extent: CameraFrameExtent
    frame_shape: tuple[int, int]


def camera_frame_extent_from_dict(
    data: Mapping[str, Any],
    *,
    fallback_shape: tuple[int, int] | None = None,
) -> CameraFrameExtent:
    shape = data.get("shape_hw")
    if shape is None:
        shape = fallback_shape
    if shape is None:
        raise ValueError("camera_frame_extent.shape_hw is required")
    origin = data.get("origin_xy", [0, 0])
    sensor_shape = data.get("sensor_shape_hw")
    return CameraFrameExtent(
        mode=str(data.get("mode") or "unknown"),
        origin_xy=_int_pair(origin, "camera_frame_extent.origin_xy"),
        shape_hw=_int_pair(shape, "camera_frame_extent.shape_hw"),
        sensor_shape_hw=(
            _int_pair(sensor_shape, "camera_frame_extent.sensor_shape_hw")
            if sensor_shape is not None else None
        ),
        source=_optional_str(data.get("source")),
    )


def camera_frame_extent_to_dict(extent: CameraFrameExtent) -> dict[str, Any]:
    result = {
        "mode": str(extent.mode),
        "origin_xy": [int(extent.origin_xy[0]), int(extent.origin_xy[1])],
        "shape_hw": [int(extent.shape_hw[0]), int(extent.shape_hw[1])],
        "sensor_shape_hw": (
            [int(extent.sensor_shape_hw[0]), int(extent.sensor_shape_hw[1])]
            if extent.sensor_shape_hw is not None else None
        ),
    }
    if extent.source is not None:
        result["source"] = extent.source
    return result


def camera_frame_extent_json_dict(extent: CameraFrameExtent) -> dict[str, Any]:
    return camera_frame_extent_to_dict(extent)


def camera_frame_extent_from_camera_metadata(
    metadata: Mapping[str, Any],
    *,
    fallback_shape: tuple[int, int] | None = None,
) -> CameraFrameExtent:
    for key in (
        "camera_frame_extent",
        "frame_extent",
        "acquired_frame_extent",
        "frame_extent_json",
        "acquired_frame_extent_json",
    ):
        extent = _extent_from_possible_json(metadata.get(key))
        if extent is not None:
            extent.setdefault("source", "camera_metadata")
            return camera_frame_extent_from_dict(extent, fallback_shape=fallback_shape)

    status = metadata.get("status")
    if isinstance(status, Mapping):
        for key in ("camera_frame_extent", "frame_extent", "acquired_frame_extent"):
            extent = _extent_from_possible_json(status.get(key))
            if extent is not None:
                extent.setdefault("source", "camera_status_metadata")
                return camera_frame_extent_from_dict(
                    extent,
                    fallback_shape=fallback_shape,
                )

    shape = _shape_from_metadata(metadata) or fallback_shape
    if shape is None:
        raise ValueError("camera_frame_extent.shape_hw is required")

    sensor_shape = _sensor_shape_from_metadata(metadata)
    if sensor_shape is None and isinstance(status, Mapping):
        sensor_shape = _sensor_shape_from_metadata(status)
    mode = (
        "full_sensor"
        if sensor_shape is not None and tuple(sensor_shape) == tuple(shape)
        else "unknown"
    )
    source = (
        "camera_status_metadata"
        if sensor_shape is not None and isinstance(status, Mapping)
        else "fallback_from_frame_shape"
    )
    return CameraFrameExtent(
        mode=mode,
        origin_xy=(0, 0),
        shape_hw=(int(shape[0]), int(shape[1])),
        sensor_shape_hw=(
            (int(sensor_shape[0]), int(sensor_shape[1]))
            if sensor_shape is not None else None
        ),
        source=source,
    )


def read_camera_frame_extent_from_group(
    group: h5py.Group,
    *,
    fallback_shape: tuple[int, int] | None = None,
) -> CameraFrameExtent:
    for name in CAMERA_FRAME_EXTENT_DATASET_PRIORITY:
        if name not in group:
            continue
        for data in _iter_json_dataset_objects(group[name]):
            extent = _normalize_extent_payload(
                data,
                source=f"camera/{name}",
                fallback_shape=fallback_shape,
            )
            if extent is not None:
                return camera_frame_extent_from_dict(
                    extent,
                    fallback_shape=fallback_shape,
                )

    if "status_json" in group:
        for status in _iter_json_dataset_objects(group["status_json"]):
            if isinstance(status, Mapping):
                try:
                    return camera_frame_extent_from_camera_metadata(
                        {"status": status},
                        fallback_shape=fallback_shape,
                    )
                except ValueError:
                    continue

    raise ValueError(
        "camera frame extent metadata not found; expected /camera/frame_extent_json"
    )


def resolve_coordinate_frame(
    camera_frame_extent: CameraFrameExtent,
    manifest: Mapping[str, Any] | None = None,
) -> str:
    if manifest is not None:
        value = manifest.get("coordinate_frame")
        if isinstance(value, str) and value in SUPPORTED_COORDINATE_FRAMES:
            return value
    if camera_frame_extent.mode == "full_sensor":
        return "sensor_full_frame"
    return "acquired_frame"


def validate_coordinate_frame_descriptor(
    actual: CoordinateFrameDescriptor,
    expected: CoordinateFrameDescriptor,
    *,
    require_same_frame_shape: bool = True,
) -> None:
    if actual.coordinate_frame != expected.coordinate_frame:
        raise ValueError("coordinate_frame does not match")
    if actual.camera_frame_extent != expected.camera_frame_extent:
        raise ValueError("camera_frame_extent does not match")
    if require_same_frame_shape and tuple(actual.frame_shape) != tuple(expected.frame_shape):
        raise ValueError("frame_shape does not match")


def _int_pair(value: Any, name: str) -> tuple[int, int]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError(f"{name} must contain two integers")
    return (int(value[0]), int(value[1]))


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _extent_from_possible_json(value: Any) -> dict[str, Any] | None:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, bytes):
        value = value.decode("utf-8")
    if isinstance(value, str) and value.strip():
        try:
            data = json.loads(value)
        except json.JSONDecodeError:
            return None
        return dict(data) if isinstance(data, Mapping) else None
    return None


def camera_frame_extent_from_hdf5(
    src: h5py.File,
    *,
    frame_shape: tuple[int, int] | None,
) -> dict[str, Any]:
    """Read camera frame extent from a raw capture HDF5 file."""
    if "camera" not in src:
        raise ValueError(
            "missing /camera group in raw capture; cannot determine frame extent"
        )
    extent = read_camera_frame_extent_from_group(
        src["camera"],
        fallback_shape=frame_shape,
    )
    return camera_frame_extent_to_dict(extent)


def require_full_sensor_extent(
    extent: dict[str, Any],
    *,
    allow_acquired_frame_only: bool,
    artifact_name: str,
) -> None:
    if extent.get("mode") == "full_sensor":
        return
    if allow_acquired_frame_only:
        return
    raise ValueError(
        f"cannot confirm full-sensor acquisition for {artifact_name}; pass "
        "allow_acquired_frame_only to record acquired-frame coordinates explicitly"
    )


def _shape_from_metadata(metadata: Mapping[str, Any]) -> tuple[int, int] | None:
    for key in ("shape_hw", "frame_shape", "acquired_shape_hw"):
        value = metadata.get(key)
        if isinstance(value, (list, tuple)) and len(value) == 2:
            return (int(value[0]), int(value[1]))
    return None


def _sensor_shape_from_metadata(metadata: Mapping[str, Any]) -> tuple[int, int] | None:
    value = metadata.get("sensor_shape_hw")
    if isinstance(value, (list, tuple)) and len(value) == 2:
        return (int(value[0]), int(value[1]))
    if {"sensor_height", "sensor_width"} <= set(metadata):
        return (int(metadata["sensor_height"]), int(metadata["sensor_width"]))
    return None


def _iter_json_dataset_objects(dataset: h5py.Dataset) -> list[Any]:
    raw = dataset[()]
    if getattr(dataset, "shape", ()) == ():
        return [_loads_json_h5_string(raw)]
    if not isinstance(raw, (list, tuple)) and getattr(raw, "ndim", 0) == 0:
        return [_loads_json_h5_string(raw)]
    return [_loads_json_h5_string(item) for item in raw]


def _loads_json_h5_string(value: Any) -> Any:
    if isinstance(value, bytes):
        text = value.decode("utf-8")
    elif hasattr(value, "decode"):
        text = value.decode("utf-8")
    else:
        text = str(value)
    if not text.strip():
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _normalize_extent_payload(
    data: Any,
    *,
    source: str,
    fallback_shape: tuple[int, int] | None,
) -> dict[str, Any] | None:
    if isinstance(data, Mapping):
        extent = dict(data)
        extent.setdefault("source", source)
        return extent
    return None
