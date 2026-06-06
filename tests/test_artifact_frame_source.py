from __future__ import annotations

import json

import h5py
import numpy as np
import pytest

from tasks.artifacts.errors import ArtifactIOError
from tasks.artifacts.frame_source import open_full_frame_survey_source


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
        group.create_dataset("entry_mask_ids", data=np.asarray(["m0", "m1"], dtype=object), dtype=h5py.string_dtype())
        group.create_dataset("entry_wavelength_nm", data=np.asarray([500.0, 600.0]))
        group.create_dataset(
            "entry_illumination_json",
            data=np.asarray([
                json.dumps({"mode": "monochromatic", "effective_wavelength_nm": 500.0}),
                json.dumps({"mode": "broadband_passthrough", "effective_wavelength_nm": None}),
            ], dtype=object),
            dtype=h5py.string_dtype(),
        )

    with h5py.File(path, "r") as f:
        source = open_full_frame_survey_source(f, path)

        assert source.descriptor.source_kind == "full_frame_survey"
        assert source.descriptor.frame_count == 2
        assert source.descriptor.frame_shape == (3, 4)
        assert source.descriptor.coordinate_frame == "sensor_full_frame"
        assert source.descriptor.mask_ids == ("m0", "m1")
        assert json.loads(source.descriptor.entry_illumination_json[1])["mode"] == "broadband_passthrough"
        assert source.read_frame(1).shape == (3, 4)


def test_reads_full_frame_survey_2d_as_one_entry(tmp_path):
    path = tmp_path / "survey_2d.h5"
    with h5py.File(path, "w") as f:
        group = f.create_group("full_frame_survey")
        group.create_dataset("frames_avg", data=np.zeros((3, 4), dtype=np.float64))
        group.create_dataset(
            "entry_mask_ids",
            data=np.asarray(["entry_0000"], dtype=object),
            dtype=h5py.string_dtype(),
        )
        group.create_dataset("entry_wavelength_nm", data=np.asarray([float("nan")]))
        group.create_dataset(
            "entry_illumination_json",
            data=np.asarray([
                json.dumps({"mode": "unknown"})
            ], dtype=object),
            dtype=h5py.string_dtype(),
        )
        group.create_dataset(
            "camera_frame_extent_json",
            data=json.dumps({
                "mode": "unknown",
                "origin_xy": [0, 0],
                "shape_hw": [3, 4],
                "sensor_shape_hw": None,
            }),
            dtype=h5py.string_dtype(),
        )
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
                "entry_mask_ids": ["entry_0000"],
                "entry_wavelengths_nm": [float("nan")],
                "entry_illumination_json": [json.dumps({"mode": "unknown"})],
            }),
        )

    with h5py.File(path, "r") as f:
        source = open_full_frame_survey_source(f, path)

        assert source.descriptor.frame_count == 1
        assert source.descriptor.frame_shape == (3, 4)
        assert source.read_frame(0).shape == (3, 4)
        with pytest.raises(ArtifactIOError):
            source.read_frame(1)


def test_rejects_raw_frames_avg_input(tmp_path):
    path = tmp_path / "raw.h5"
    with h5py.File(path, "w") as f:
        raw = f.create_group("raw")
        raw.create_dataset("frames_avg", data=np.zeros((2, 3, 4), dtype=np.float64))

    with h5py.File(path, "r") as f:
        with pytest.raises(ArtifactIOError, match="full_frame_survey/frames_avg"):
            open_full_frame_survey_source(f, path)
