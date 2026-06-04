from __future__ import annotations

import pytest

from tasks.artifacts.coordinate_frame import (
    CoordinateFrameDescriptor,
    camera_frame_extent_from_dict,
    resolve_coordinate_frame,
    validate_coordinate_frame_descriptor,
)


def test_resolves_sensor_full_frame_from_full_sensor_extent():
    extent = camera_frame_extent_from_dict({
        "mode": "full_sensor",
        "origin_xy": [0, 0],
        "shape_hw": [10, 20],
        "sensor_shape_hw": [10, 20],
    })

    assert resolve_coordinate_frame(extent) == "sensor_full_frame"


def test_resolves_acquired_frame_otherwise():
    extent = camera_frame_extent_from_dict({
        "mode": "sensor_roi",
        "origin_xy": [5, 6],
        "shape_hw": [10, 20],
        "sensor_shape_hw": [30, 40],
    })

    assert resolve_coordinate_frame(extent) == "acquired_frame"


def test_validates_matching_descriptor():
    extent = camera_frame_extent_from_dict({
        "mode": "full_sensor",
        "origin_xy": [0, 0],
        "shape_hw": [10, 20],
        "sensor_shape_hw": [10, 20],
    })
    actual = CoordinateFrameDescriptor("sensor_full_frame", extent, (10, 20))
    expected = CoordinateFrameDescriptor("sensor_full_frame", extent, (10, 20))

    validate_coordinate_frame_descriptor(actual, expected)


def test_rejects_mismatched_extent():
    actual_extent = camera_frame_extent_from_dict({
        "mode": "full_sensor",
        "origin_xy": [0, 0],
        "shape_hw": [10, 20],
        "sensor_shape_hw": [10, 20],
    })
    expected_extent = camera_frame_extent_from_dict({
        "mode": "sensor_roi",
        "origin_xy": [1, 0],
        "shape_hw": [10, 20],
        "sensor_shape_hw": [10, 20],
    })

    with pytest.raises(ValueError, match="camera_frame_extent"):
        validate_coordinate_frame_descriptor(
            CoordinateFrameDescriptor("sensor_full_frame", actual_extent, (10, 20)),
            CoordinateFrameDescriptor("sensor_full_frame", expected_extent, (10, 20)),
        )
