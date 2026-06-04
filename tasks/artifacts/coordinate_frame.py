from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


SUPPORTED_COORDINATE_FRAMES = {"sensor_full_frame", "acquired_frame"}


@dataclass(frozen=True)
class CameraFrameExtent:
    mode: str
    origin_xy: tuple[int, int]
    shape_hw: tuple[int, int]
    sensor_shape_hw: tuple[int, int] | None = None


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
    )


def camera_frame_extent_to_dict(extent: CameraFrameExtent) -> dict[str, Any]:
    return {
        "mode": str(extent.mode),
        "origin_xy": [int(extent.origin_xy[0]), int(extent.origin_xy[1])],
        "shape_hw": [int(extent.shape_hw[0]), int(extent.shape_hw[1])],
        "sensor_shape_hw": (
            [int(extent.sensor_shape_hw[0]), int(extent.sensor_shape_hw[1])]
            if extent.sensor_shape_hw is not None else None
        ),
    }


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
