from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import h5py
import numpy as np

from .coordinate_frame import (
    CameraFrameExtent,
    camera_frame_extent_from_dict,
    camera_frame_extent_to_dict,
    resolve_coordinate_frame,
)
from .errors import ArtifactIOError
from .json_io import decode_h5_string, read_json_dataset_or_attr


@dataclass(frozen=True)
class FrameSourceDescriptor:
    source_path: str
    dataset_path: str
    frame_count: int
    frame_shape: tuple[int, int]
    coordinate_frame: str
    camera_frame_extent: CameraFrameExtent
    mask_ids: tuple[str, ...]
    entry_wavelengths_nm: tuple[float, ...]
    entry_illumination_json: tuple[str, ...]
    source_kind: str

    def camera_frame_extent_dict(self) -> dict[str, Any]:
        return camera_frame_extent_to_dict(self.camera_frame_extent)


@dataclass
class FrameSource:
    h5: h5py.File
    dataset: h5py.Dataset
    descriptor: FrameSourceDescriptor

    def read_frame(self, entry_index: int) -> np.ndarray:
        return read_frame_entry(self.dataset, entry_index)


def open_full_frame_survey_source(h5: h5py.File, source_path: str | Path) -> FrameSource:
    if "full_frame_survey/frames_avg" not in h5:
        raise ArtifactIOError("missing full_frame_survey/frames_avg")
    frames = h5["full_frame_survey/frames_avg"]
    group = h5["full_frame_survey"]
    frame_count, frame_shape = frame_dataset_count_and_shape(frames)
    manifest = read_json_dataset_or_attr(group, "manifest_json")
    extent_data = read_json_dataset_or_attr(group, "camera_frame_extent_json")
    if not extent_data:
        raise ArtifactIOError("missing camera_frame_extent_json in survey artifact")
    extent = camera_frame_extent_from_dict(
        extent_data,
        fallback_shape=frame_shape,
    )
    descriptor = FrameSourceDescriptor(
        source_path=str(source_path),
        dataset_path="full_frame_survey/frames_avg",
        frame_count=frame_count,
        frame_shape=frame_shape,
        coordinate_frame=resolve_coordinate_frame(extent, manifest),
        camera_frame_extent=extent,
        mask_ids=tuple(_read_survey_mask_ids(h5, group, frame_count)),
        entry_wavelengths_nm=tuple(_read_survey_wavelengths(h5, group, frame_count)),
        entry_illumination_json=tuple(
            _read_survey_illumination_json(h5, group, frame_count)
        ),
        source_kind="full_frame_survey",
    )
    _validate_lengths(descriptor)
    return FrameSource(h5=h5, dataset=frames, descriptor=descriptor)


def frame_dataset_count_and_shape(dataset: h5py.Dataset) -> tuple[int, tuple[int, int]]:
    if dataset.ndim == 2:
        return 1, (int(dataset.shape[0]), int(dataset.shape[1]))
    if dataset.ndim == 3:
        return int(dataset.shape[0]), (int(dataset.shape[1]), int(dataset.shape[2]))
    raise ArtifactIOError(f"frames must be 2D or 3D, got {dataset.shape}")


def read_frame_entry(dataset: h5py.Dataset, entry_index: int) -> np.ndarray:
    if dataset.ndim == 2:
        if int(entry_index) != 0:
            raise ArtifactIOError("2D frame source has only one entry")
        return np.asarray(dataset[()], dtype=np.float64)
    if dataset.ndim == 3:
        return np.asarray(dataset[int(entry_index), :, :], dtype=np.float64)
    raise ArtifactIOError(f"frames must be 2D or 3D, got {dataset.shape}")


def _read_survey_mask_ids(h5: h5py.File, group: h5py.Group, n: int) -> list[str]:
    if "full_frame_survey/entry_mask_ids" in h5:
        return _read_dataset_strings(h5, "full_frame_survey/entry_mask_ids", n, "entry")
    raise ArtifactIOError("missing full_frame_survey/entry_mask_ids")


def _read_survey_wavelengths(h5: h5py.File, group: h5py.Group, n: int) -> list[float]:
    if "entry_wavelength_nm" in group:
        arr = np.asarray(group["entry_wavelength_nm"], dtype=np.float64)
        return [float(x) for x in arr[:n]]
    raise ArtifactIOError("missing full_frame_survey/entry_wavelength_nm")


def _read_survey_illumination_json(h5: h5py.File, group: h5py.Group, n: int) -> list[str]:
    if "entry_illumination_json" in group:
        return _read_dataset_strings(h5, "full_frame_survey/entry_illumination_json", n, "illumination")
    raise ArtifactIOError("missing full_frame_survey/entry_illumination_json")


def _read_dataset_strings(
    h5: h5py.File,
    path: str,
    n: int,
    fallback_prefix: str,
) -> list[str]:
    if path not in h5:
        return [f"{fallback_prefix}_{i:04d}" for i in range(n)]
    values = h5[path][()]
    return [decode_h5_string(x) for x in values[:n]]

def _validate_lengths(descriptor: FrameSourceDescriptor) -> None:
    if len(descriptor.mask_ids) != int(descriptor.frame_count):
        raise ArtifactIOError("mask id count does not match frame count")
    if len(descriptor.entry_wavelengths_nm) != int(descriptor.frame_count):
        raise ArtifactIOError("wavelength count does not match frame count")
    if len(descriptor.entry_illumination_json) != int(descriptor.frame_count):
        raise ArtifactIOError("illumination count does not match frame count")
