from __future__ import annotations

import json

import h5py
import numpy as np
import pytest

from tasks.artifacts.errors import ArtifactIOError
from tasks.artifacts.frame_source import open_survey_or_raw_frame_source


def test_reads_full_frame_survey_3d(tmp_path):
    path = tmp_path / "survey.h5"
    with h5py.File(path, "w") as f:
        group = f.create_group("full_frame_survey")
        group.create_dataset("frames_avg", data=np.zeros((2, 3, 4), dtype=np.float64))
        group.create_dataset(
            "camera_frame_extent_json",
            data=json.dumps({
                "mode": "full_sensor",
                "origin_xy": [0, 0],
                "shape_hw": [3, 4],
                "sensor_shape_hw": [3, 4],
            }),
        )
        group.create_dataset("entry_mask_id", data=np.asarray(["m0", "m1"], dtype=object), dtype=h5py.string_dtype())
        group.create_dataset("entry_wavelength_nm", data=np.asarray([500.0, 600.0]))

    with h5py.File(path, "r") as f:
        source = open_survey_or_raw_frame_source(f, path, allow_raw_fallback=False)

        assert source.descriptor.source_kind == "full_frame_survey"
        assert source.descriptor.frame_count == 2
        assert source.descriptor.frame_shape == (3, 4)
        assert source.descriptor.coordinate_frame == "sensor_full_frame"
        assert source.descriptor.mask_ids == ("m0", "m1")
        assert source.read_frame(1).shape == (3, 4)


def test_reads_full_frame_survey_2d_as_one_entry(tmp_path):
    path = tmp_path / "survey_2d.h5"
    with h5py.File(path, "w") as f:
        group = f.create_group("full_frame_survey")
        group.create_dataset("frames_avg", data=np.zeros((3, 4), dtype=np.float64))
        group.create_dataset(
            "manifest_json",
            data=json.dumps({
                "coordinate_frame": "acquired_frame",
                "camera_frame_extent": {
                    "mode": "unknown",
                    "origin_xy": [0, 0],
                    "shape_hw": [3, 4],
                    "sensor_shape_hw": None,
                },
            }),
        )

    with h5py.File(path, "r") as f:
        source = open_survey_or_raw_frame_source(f, path, allow_raw_fallback=False)

        assert source.descriptor.frame_count == 1
        assert source.descriptor.frame_shape == (3, 4)
        assert source.read_frame(0).shape == (3, 4)
        with pytest.raises(ArtifactIOError):
            source.read_frame(1)


def test_rejects_raw_fallback_without_opt_in(tmp_path):
    path = tmp_path / "raw.h5"
    with h5py.File(path, "w") as f:
        raw = f.create_group("raw")
        raw.create_dataset("frames_avg", data=np.zeros((2, 3, 4), dtype=np.float64))

    with h5py.File(path, "r") as f:
        with pytest.raises(ArtifactIOError):
            open_survey_or_raw_frame_source(f, path, allow_raw_fallback=False)


def test_reads_raw_fallback_with_opt_in(tmp_path):
    path = tmp_path / "raw.h5"
    with h5py.File(path, "w") as f:
        raw = f.create_group("raw")
        raw.create_dataset("frames_avg", data=np.zeros((2, 3, 4), dtype=np.float64))
        raw.create_dataset("mask_id", data=np.asarray(["a", "b"], dtype=object), dtype=h5py.string_dtype())
        raw.create_dataset("wavelength_nm", data=np.asarray([510.0, 520.0]))

    with h5py.File(path, "r") as f:
        source = open_survey_or_raw_frame_source(f, path, allow_raw_fallback=True)

        assert source.descriptor.source_kind == "raw_frames_avg"
        assert source.descriptor.coordinate_frame == "acquired_frame"
        assert source.descriptor.frame_count == 2
        assert source.descriptor.mask_ids == ("a", "b")
        assert source.descriptor.wavelengths_nm == (510.0, 520.0)
