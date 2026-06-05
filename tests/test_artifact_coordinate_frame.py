from __future__ import annotations

import json

import h5py
import pytest

from tasks.artifacts.coordinate_frame import (
    CoordinateFrameDescriptor,
    camera_frame_extent_from_camera_metadata,
    camera_frame_extent_from_dict,
    read_camera_frame_extent_from_group,
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


def test_camera_metadata_roi_normalizes_to_frame_extent():
    extent = camera_frame_extent_from_camera_metadata(
        {
            "roi": {"offset_x": 2, "offset_y": 3, "width": 20, "height": 10},
            "status": {"sensor_shape_hw": [30, 40]},
        },
        fallback_shape=(10, 20),
    )

    assert extent.mode == "sensor_roi"
    assert extent.origin_xy == (2, 3)
    assert extent.shape_hw == (10, 20)
    assert extent.sensor_shape_hw == (30, 40)
    assert extent.source == "camera_metadata"


def test_camera_group_prefers_frame_extent_over_legacy_roi(tmp_path):
    path = tmp_path / "extent_priority.h5"
    with h5py.File(path, "w") as f:
        camera = f.create_group("camera")
        camera.create_dataset(
            "frame_extent_json",
            data=[
                json.dumps({
                    "mode": "full_sensor",
                    "origin_xy": [0, 0],
                    "shape_hw": [10, 20],
                    "sensor_shape_hw": [10, 20],
                })
            ],
            dtype=h5py.string_dtype(),
        )
        camera.create_dataset(
            "roi_json",
            data=[json.dumps([5, 6, 7, 8])],
            dtype=h5py.string_dtype(),
        )

    with h5py.File(path, "r") as f:
        extent = read_camera_frame_extent_from_group(
            f["camera"],
            fallback_shape=(10, 20),
        )

    assert extent.mode == "full_sensor"
    assert extent.origin_xy == (0, 0)
    assert extent.shape_hw == (10, 20)


def test_camera_group_reads_legacy_roi_json(tmp_path):
    path = tmp_path / "legacy_roi.h5"
    with h5py.File(path, "w") as f:
        camera = f.create_group("camera")
        camera.create_dataset(
            "roi_json",
            data=[json.dumps([5, 6, 7, 8])],
            dtype=h5py.string_dtype(),
        )

    with h5py.File(path, "r") as f:
        extent = read_camera_frame_extent_from_group(
            f["camera"],
            fallback_shape=(8, 7),
        )

    assert extent.mode == "sensor_roi"
    assert extent.origin_xy == (5, 6)
    assert extent.shape_hw == (8, 7)
    assert extent.source == "legacy_roi_json"


def test_camera_group_falls_back_to_frame_shape(tmp_path):
    path = tmp_path / "no_extent.h5"
    with h5py.File(path, "w") as f:
        f.create_group("camera")

    with h5py.File(path, "r") as f:
        extent = read_camera_frame_extent_from_group(
            f["camera"],
            fallback_shape=(8, 7),
        )

    assert extent.mode == "unknown"
    assert extent.origin_xy == (0, 0)
    assert extent.shape_hw == (8, 7)
    assert extent.sensor_shape_hw is None
    assert extent.source == "fallback_from_frame_shape"
