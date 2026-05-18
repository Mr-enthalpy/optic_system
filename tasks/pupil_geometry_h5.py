from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import h5py
import numpy as np


class PupilGeometryWriteError(RuntimeError):
    pass


def _now_ns() -> int:
    return time.monotonic_ns()


def _json_str(value: Any) -> str:
    return json.dumps(value, indent=2, default=str)


class PupilGeometryWriter:
    def __init__(self, output_path: str | Path, *, plan_id: str):
        self._path = Path(output_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._plan_id = str(plan_id)
        self._file: h5py.File | None = None
        self._closed = False
        self._n_frames = 0

    @property
    def path(self) -> Path:
        return self._path

    @property
    def n_frames(self) -> int:
        return self._n_frames

    def open(self) -> PupilGeometryWriter:
        if self._file is not None:
            return self
        self._file = h5py.File(str(self._path), "w")
        self._init_structure()
        return self

    def _init_structure(self) -> None:
        f = _ensure_open(self._file)
        string_dtype = h5py.string_dtype(encoding="utf-8")
        f.attrs["plan_id"] = self._plan_id
        f.attrs["created_at_ns"] = _now_ns()
        f.attrs["hdf5_writer_version"] = "1.0"

        raw = f.require_group("raw")
        raw.create_dataset(
            "frames_avg",
            shape=(0, 1, 1),
            maxshape=(None, None, None),
            dtype=np.float64,
            chunks=(1, 128, 128),
            compression="gzip",
            compression_opts=4,
        )
        raw.create_dataset("mask_id", shape=(0,), maxshape=(None,), dtype=string_dtype)
        raw.create_dataset("mask_metadata_json", shape=(0,), maxshape=(None,), dtype=string_dtype)

        capture = f.require_group("capture")
        capture.create_dataset("plan_id", data=self._plan_id, dtype=string_dtype)
        capture.create_dataset("plan_json", data="", dtype=string_dtype)
        capture.create_dataset("processing_flags_json", data=_json_str(_processing_flags(False)), dtype=string_dtype)

        f.require_group("camera").create_dataset("metadata_json", data="", dtype=string_dtype)
        f["camera"].create_dataset("camera_params_source_json", data="", dtype=string_dtype)
        f.require_group("lcd").create_dataset("metadata_json", data="", dtype=string_dtype)
        f.require_group("tls").create_dataset("metadata_json", data="", dtype=string_dtype)

        refs = f.require_group("references")
        refs.create_dataset("bright_frame_avg", shape=(0, 0), maxshape=(None, None), dtype=np.float64)
        refs.create_dataset("dark_frame_avg", shape=(0, 0), maxshape=(None, None), dtype=np.float64)
        refs.create_dataset("bright_sum", data=np.float64(np.nan))
        refs.create_dataset("dark_sum", data=np.float64(np.nan))
        refs.create_dataset("bright_frame_index", data=np.int64(-1))
        refs.create_dataset("dark_frame_index", data=np.int64(-1))

        for axis in ("x", "y"):
            grp = f.require_group(f"bar_scan/{axis}")
            grp.create_dataset("positions", shape=(0,), maxshape=(None,), dtype=np.float64)
            grp.create_dataset("energies", shape=(0,), maxshape=(None,), dtype=np.float64)
            grp.create_dataset("frame_indices", shape=(0,), maxshape=(None,), dtype=np.int64)
            grp.create_dataset("mask_metadata_json", shape=(0,), maxshape=(None,), dtype=string_dtype)

        radius = f.require_group("radius_scan")
        radius.create_dataset("radii", shape=(0,), maxshape=(None,), dtype=np.float64)
        radius.create_dataset("energies", shape=(0,), maxshape=(None,), dtype=np.float64)
        radius.create_dataset("frame_indices", shape=(0,), maxshape=(None,), dtype=np.int64)
        radius.create_dataset("mask_metadata_json", shape=(0,), maxshape=(None,), dtype=string_dtype)

    def write_plan_json(self, plan: dict[str, Any]) -> None:
        f = _ensure_open(self._file)
        f["capture/plan_json"][()] = _json_str(plan)

    def write_camera_metadata(
        self,
        *,
        metadata: dict[str, Any],
        camera_params_source: dict[str, Any],
    ) -> None:
        f = _ensure_open(self._file)
        f["camera/metadata_json"][()] = _json_str(metadata)
        f["camera/camera_params_source_json"][()] = _json_str(camera_params_source)

    def write_lcd_metadata(self, metadata: dict[str, Any]) -> None:
        f = _ensure_open(self._file)
        f["lcd/metadata_json"][()] = _json_str(metadata)

    def write_tls_metadata(self, metadata: dict[str, Any]) -> None:
        f = _ensure_open(self._file)
        f["tls/metadata_json"][()] = _json_str(metadata)

    def append_frame(
        self,
        *,
        mask_id: str,
        mask_metadata: dict[str, Any],
        frame_avg: np.ndarray,
    ) -> int:
        f = _ensure_open(self._file)
        if self._closed:
            raise PupilGeometryWriteError("file already closed")
        arr = np.asarray(frame_avg, dtype=np.float64)
        if arr.ndim != 2:
            raise PupilGeometryWriteError(f"frame_avg must be 2D, got {arr.shape}")
        row = self._n_frames
        dset: h5py.Dataset = f["raw/frames_avg"]
        if row == 0:
            dset.resize((1, arr.shape[0], arr.shape[1]))
        else:
            if dset.shape[1:] != arr.shape:
                raise PupilGeometryWriteError(
                    f"all frames must share shape {dset.shape[1:]}, got {arr.shape}"
                )
            dset.resize((row + 1, arr.shape[0], arr.shape[1]))
        dset[row] = arr
        _append_string(f["raw/mask_id"], str(mask_id))
        _append_string(f["raw/mask_metadata_json"], _json_str(mask_metadata))
        self._n_frames += 1
        return row

    def write_bright_reference(self, frame_avg: np.ndarray, *, frame_index: int) -> None:
        self._write_reference("bright", frame_avg, frame_index=frame_index)

    def write_dark_reference(self, frame_avg: np.ndarray, *, frame_index: int) -> None:
        self._write_reference("dark", frame_avg, frame_index=frame_index)

    def _write_reference(self, name: str, frame_avg: np.ndarray, *, frame_index: int) -> None:
        f = _ensure_open(self._file)
        arr = np.asarray(frame_avg, dtype=np.float64)
        if arr.ndim != 2:
            raise PupilGeometryWriteError(f"{name} reference must be 2D, got {arr.shape}")
        dset: h5py.Dataset = f[f"references/{name}_frame_avg"]
        dset.resize(arr.shape)
        dset[...] = arr
        f[f"references/{name}_sum"][()] = float(np.sum(arr))
        f[f"references/{name}_frame_index"][()] = int(frame_index)

    def append_bar_scan(
        self,
        *,
        axis: str,
        position: float,
        energy: float,
        frame_index: int,
        mask_metadata: dict[str, Any],
    ) -> None:
        if axis not in {"x", "y"}:
            raise PupilGeometryWriteError("bar scan axis must be 'x' or 'y'")
        f = _ensure_open(self._file)
        grp = f[f"bar_scan/{axis}"]
        _append_scalar(grp["positions"], float(position))
        _append_scalar(grp["energies"], float(energy))
        _append_scalar(grp["frame_indices"], int(frame_index))
        _append_string(grp["mask_metadata_json"], _json_str(mask_metadata))

    def append_radius_scan(
        self,
        *,
        radius: float,
        energy: float,
        frame_index: int,
        mask_metadata: dict[str, Any],
    ) -> None:
        f = _ensure_open(self._file)
        grp = f["radius_scan"]
        _append_scalar(grp["radii"], float(radius))
        _append_scalar(grp["energies"], float(energy))
        _append_scalar(grp["frame_indices"], int(frame_index))
        _append_string(grp["mask_metadata_json"], _json_str(mask_metadata))

    def flush(self) -> None:
        if self._file is not None and not self._closed:
            self._file.file.flush()

    def finalize(self, *, completed: bool = True, error: str | None = None) -> None:
        if self._file is None or self._closed:
            return
        flags = _processing_flags(completed)
        flags["error"] = error
        flags["n_frames_written"] = self._n_frames
        self._file["capture/processing_flags_json"][()] = _json_str(flags)
        self._file.close()
        self._closed = True
        self._file = None

    def close(self) -> None:
        self.finalize(completed=True)

    def __enter__(self) -> PupilGeometryWriter:
        return self.open()

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        if exc_type is not None:
            self.finalize(completed=False, error=str(exc_value))
        else:
            self.finalize(completed=True)


def _processing_flags(completed: bool) -> dict[str, Any]:
    return {
        "phase": "phase3_pupil_geometry",
        "completed": bool(completed),
        "scientific_calibration_valid": False,
        "optical_alignment_validated": False,
        "training_ready": False,
    }


def _ensure_open(file: h5py.File | None) -> h5py.File:
    if file is None:
        raise PupilGeometryWriteError("file not open")
    return file


def _append_scalar(dset: h5py.Dataset, value: Any) -> None:
    n = dset.shape[0]
    dset.resize((n + 1,))
    dset[n] = value


def _append_string(dset: h5py.Dataset, value: str) -> None:
    n = dset.shape[0]
    dset.resize((n + 1,))
    dset[n] = value
