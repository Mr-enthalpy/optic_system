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
    read_camera_frame_extent_from_group,
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
    wavelengths_nm: tuple[float, ...]
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
    if not extent_data and isinstance(manifest.get("camera_frame_extent"), dict):
        extent_data = dict(manifest["camera_frame_extent"])
    extent = camera_frame_extent_from_dict(
        _fallback_extent(extent_data, frame_shape),
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
        wavelengths_nm=tuple(_read_survey_wavelengths(h5, group, frame_count)),
        source_kind="full_frame_survey",
    )
    _validate_lengths(descriptor)
    return FrameSource(h5=h5, dataset=frames, descriptor=descriptor)


def open_raw_frames_avg_source(h5: h5py.File, source_path: str | Path) -> FrameSource:
    if "raw/frames_avg" not in h5:
        raise ArtifactIOError("missing raw/frames_avg")
    frames = h5["raw/frames_avg"]
    frame_count, frame_shape = frame_dataset_count_and_shape(frames)
    if "camera" in h5:
        extent = read_camera_frame_extent_from_group(
            h5["camera"],
            fallback_shape=frame_shape,
        )
    else:
        extent = camera_frame_extent_from_dict(
            _fallback_extent({}, frame_shape),
            fallback_shape=frame_shape,
        )
    descriptor = FrameSourceDescriptor(
        source_path=str(source_path),
        dataset_path="raw/frames_avg",
        frame_count=frame_count,
        frame_shape=frame_shape,
        coordinate_frame=resolve_coordinate_frame(extent),
        camera_frame_extent=extent,
        mask_ids=tuple(_read_dataset_strings(h5, "raw/mask_id", frame_count, "entry")),
        wavelengths_nm=tuple(_read_raw_wavelengths(h5, frame_count)),
        source_kind="raw_frames_avg",
    )
    _validate_lengths(descriptor)
    return FrameSource(h5=h5, dataset=frames, descriptor=descriptor)


def open_survey_or_raw_frame_source(
    h5: h5py.File,
    source_path: str | Path,
    *,
    allow_raw_fallback: bool,
) -> FrameSource:
    if "full_frame_survey/frames_avg" in h5:
        return open_full_frame_survey_source(h5, source_path)
    if allow_raw_fallback and "raw/frames_avg" in h5:
        return open_raw_frames_avg_source(h5, source_path)
    raise ArtifactIOError(
        "frame source requires full_frame_survey/frames_avg; pass "
        "allow_raw_fallback=True only for legacy/dev raw/frames_avg inputs"
    )


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
    for path in (
        "full_frame_survey/entry_mask_id",
        "full_frame_survey/entry_mask_ids",
        "full_frame_survey/mask_id",
    ):
        if path in h5:
            return _read_dataset_strings(h5, path, n, "entry")
    for index_name in ("mask_index", "entry_mask_index"):
        if index_name in group and "unique_mask_ids" in group:
            indices = np.asarray(group[index_name], dtype=np.int64)
            unique = [decode_h5_string(x) for x in group["unique_mask_ids"][()]]
            return [unique[int(i)] for i in indices[:n]]
    if "capture/mask_index" in h5 and "masks/mask_id" in h5:
        indices = np.asarray(h5["capture/mask_index"], dtype=np.int64)
        unique = [decode_h5_string(x) for x in h5["masks/mask_id"][()]]
        return [unique[int(i)] for i in indices[:n]]
    manifest = read_json_dataset_or_attr(group, "manifest_json")
    if isinstance(manifest.get("entry_mask_ids"), list):
        return [str(x) for x in manifest["entry_mask_ids"][:n]]
    return [f"entry_{i:04d}" for i in range(n)]


def _read_survey_wavelengths(h5: h5py.File, group: h5py.Group, n: int) -> list[float]:
    for name in ("entry_wavelength_nm", "entry_wavelengths_nm", "wavelength_nm"):
        if name in group:
            arr = np.asarray(group[name], dtype=np.float64)
            return [float(x) for x in arr[:n]]
    if "wavelength_index" in group and "unique_wavelength_nm" in group:
        indices = np.asarray(group["wavelength_index"], dtype=np.int64)
        unique = np.asarray(group["unique_wavelength_nm"], dtype=np.float64)
        return [float(unique[int(i)]) for i in indices[:n]]
    if "capture/wavelength_index" in h5 and "tls/wavelength_nm" in h5:
        indices = np.asarray(h5["capture/wavelength_index"], dtype=np.int64)
        unique = np.asarray(h5["tls/wavelength_nm"], dtype=np.float64)
        return [float(unique[int(i)]) for i in indices[:n]]
    manifest = read_json_dataset_or_attr(group, "manifest_json")
    if isinstance(manifest.get("entry_wavelengths_nm"), list):
        return [float(x) for x in manifest["entry_wavelengths_nm"][:n]]
    return [float("nan") for _ in range(n)]


def _read_raw_wavelengths(h5: h5py.File, n: int) -> list[float]:
    if "raw/wavelength_nm" in h5:
        return [float(x) for x in np.asarray(h5["raw/wavelength_nm"], dtype=np.float64)[:n]]
    if "raw/wavelength_index" in h5 and "capture/plan_json" in h5:
        plan = read_json_dataset_or_attr(h5["capture"], "plan_json")
        wavelengths = plan.get("wavelengths")
        if isinstance(wavelengths, list) and wavelengths:
            unique = [float(item["wavelength_nm"]) for item in wavelengths]
            indices = np.asarray(h5["raw/wavelength_index"], dtype=np.int64)
            return [unique[int(i)] for i in indices[:n]]
        wavelength = plan.get("wavelength")
        if isinstance(wavelength, dict) and wavelength.get("wavelength_nm") is not None:
            return [float(wavelength["wavelength_nm"]) for _ in range(n)]
    return [float("nan") for _ in range(n)]


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


def _fallback_extent(data: dict[str, Any], frame_shape: tuple[int, int]) -> dict[str, Any]:
    if data:
        return dict(data)
    return {
        "mode": "unknown",
        "origin_xy": [0, 0],
        "shape_hw": [int(frame_shape[0]), int(frame_shape[1])],
        "sensor_shape_hw": None,
        "source": "fallback_from_frame_shape",
    }


def _validate_lengths(descriptor: FrameSourceDescriptor) -> None:
    if len(descriptor.mask_ids) != int(descriptor.frame_count):
        raise ArtifactIOError("mask id count does not match frame count")
    if len(descriptor.wavelengths_nm) != int(descriptor.frame_count):
        raise ArtifactIOError("wavelength count does not match frame count")
