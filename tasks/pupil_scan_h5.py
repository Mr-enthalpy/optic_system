from __future__ import annotations

import json
import time
from pathlib import Path
from types import TracebackType
from typing import Any

import h5py
import numpy as np


class PupilScanWriteError(RuntimeError):
    pass


def _now_ns() -> int:
    return time.monotonic_ns()


def _json_str(obj: Any) -> str:
    return json.dumps(obj, indent=2, default=str)


class PupilScanWriter:
    """
    Raw HDF5 writer for Phase 3.1 procedural pupil scans.

    The writer stores averaged camera frames and exact procedural mask
    provenance. Full physical masks are optional and off by default because a
    dense scan can be large.
    """

    def __init__(
        self,
        output_path: str | Path,
        *,
        plan_id: str = "pupil_scan",
        store_physical_masks: bool = False,
    ):
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        self._path = output_path
        self._plan_id = plan_id
        self._store_physical_masks = bool(store_physical_masks)
        self._file: h5py.File | None = None
        self._n_written = 0
        self._closed = False
        self._created_at_ns = _now_ns()

    @property
    def path(self) -> Path:
        return self._path

    @property
    def n_written(self) -> int:
        return self._n_written

    def open(self) -> PupilScanWriter:
        if self._file is not None:
            return self
        self._file = h5py.File(str(self._path), "w")
        self._init_structure()
        return self

    def _init_structure(self) -> None:
        assert self._file is not None
        f = self._file

        f.attrs["plan_id"] = self._plan_id
        f.attrs["created_at_ns"] = self._created_at_ns
        f.attrs["software_version"] = "optic_system phase3"
        f.attrs["hdf5_writer_version"] = "1.0"

        raw = f.require_group("raw")
        raw.create_dataset(
            "frames_avg",
            shape=(0, 1, 1),
            maxshape=(None, None, None),
            dtype=np.float64,
            chunks=(1, 240, 240),
            compression="gzip",
            compression_opts=4,
        )

        scan = f.require_group("scan")
        string_dtype = h5py.string_dtype(encoding="utf-8")
        scan.create_dataset("mask_id", shape=(0,), maxshape=(None,), dtype=string_dtype)
        scan.create_dataset("mode", shape=(0,), maxshape=(None,), dtype=string_dtype)
        for name in ("x_min", "x_max", "y_min", "y_max", "row", "col"):
            scan.create_dataset(name, shape=(0,), maxshape=(None,), dtype=np.int64)
        for name in ("center_x", "center_y"):
            scan.create_dataset(name, shape=(0,), maxshape=(None,), dtype=np.float64)
        scan.create_dataset("mask_hash", shape=(0,), maxshape=(None,), dtype=string_dtype)
        scan.create_dataset("mask_recipe_json", shape=(0,), maxshape=(None,), dtype=string_dtype)

        camera = f.require_group("camera")
        camera.create_dataset("exposure_us", data=np.float64(np.nan))
        camera.create_dataset("gain_db", data=np.float64(np.nan))
        camera.create_dataset("frame_dtype_full_scale", data=np.float64(np.nan))
        camera.create_dataset("camera_params_source_json", data="", dtype=string_dtype)

        lcd = f.require_group("lcd")
        lcd.create_dataset("metadata_json", data="", dtype=string_dtype)

        tls = f.require_group("tls")
        tls.create_dataset("wavelength_nm", data=np.float64(np.nan))
        tls.create_dataset("grating", data=np.int64(-1))
        tls.create_dataset("status_json", data="", dtype=string_dtype)

        capture = f.require_group("capture")
        capture.create_dataset("plan_json", data="", dtype=string_dtype)
        capture.create_dataset("plan_id", data=self._plan_id, dtype=string_dtype)
        capture.create_dataset("processing_flags_json", data=_json_str(_processing_flags(False)), dtype=string_dtype)

        if self._store_physical_masks:
            masks = f.require_group("masks")
            masks.create_dataset(
                "masks_physical",
                shape=(0, 1, 1),
                maxshape=(None, None, None),
                dtype=np.uint8,
                chunks=(1, 240, 240),
                compression="gzip",
                compression_opts=4,
            )

    def write_plan_json(self, plan_dict: dict[str, Any]) -> None:
        _ensure_open(self._file)
        self._file["capture/plan_json"][()] = _json_str(plan_dict)

    def write_lcd_metadata(self, lcd_meta: dict[str, Any]) -> None:
        _ensure_open(self._file)
        self._file["lcd/metadata_json"][()] = _json_str(lcd_meta)

    def write_camera_metadata(
        self,
        *,
        exposure_us: float,
        gain_db: float,
        frame_dtype_full_scale: float,
        camera_params_source: dict[str, Any],
    ) -> None:
        _ensure_open(self._file)
        cam = self._file["camera"]
        cam["exposure_us"][()] = float(exposure_us)
        cam["gain_db"][()] = float(gain_db)
        cam["frame_dtype_full_scale"][()] = float(frame_dtype_full_scale)
        cam["camera_params_source_json"][()] = _json_str(camera_params_source)

    def write_tls_metadata(
        self,
        *,
        wavelength_nm: float | None,
        grating: int | None = None,
        status: dict[str, Any] | None = None,
    ) -> None:
        _ensure_open(self._file)
        tls = self._file["tls"]
        tls["wavelength_nm"][()] = float(wavelength_nm) if wavelength_nm is not None else np.nan
        tls["grating"][()] = int(grating) if grating is not None else -1
        tls["status_json"][()] = _json_str(status or {})

    def append_capture(
        self,
        *,
        mask_id: str,
        mask_metadata: dict[str, Any],
        frames_avg: np.ndarray,
        physical_mask: np.ndarray | None = None,
    ) -> None:
        _ensure_open(self._file)
        if self._closed:
            raise PupilScanWriteError("file already closed")

        f = self._file
        row = self._n_written
        avg = np.asarray(frames_avg, dtype=np.float64)
        if avg.ndim != 2:
            raise PupilScanWriteError(f"frames_avg must be 2D [H, W], got {avg.shape}")

        dset_avg: h5py.Dataset = f["raw/frames_avg"]
        if row == 0:
            dset_avg.resize((1, avg.shape[0], avg.shape[1]))
        else:
            if dset_avg.shape[1:] != avg.shape:
                raise PupilScanWriteError(
                    f"all frames_avg rows must have one shape; got {avg.shape}, "
                    f"expected {dset_avg.shape[1:]}"
                )
            dset_avg.resize((row + 1, avg.shape[0], avg.shape[1]))
        dset_avg[row] = avg
        f["raw"].attrs["frame_height"] = avg.shape[0]
        f["raw"].attrs["frame_width"] = avg.shape[1]

        _append_string(f["scan/mask_id"], mask_id)
        _append_string(f["scan/mode"], str(mask_metadata.get("mode", "")))
        for name in ("x_min", "x_max", "y_min", "y_max", "row", "col"):
            _append_scalar(f[f"scan/{name}"], _int_or_default(mask_metadata.get(name), -1))
        for name in ("center_x", "center_y"):
            _append_scalar(f[f"scan/{name}"], _float_or_nan(mask_metadata.get(name)))
        _append_string(f["scan/mask_hash"], str(mask_metadata.get("mask_hash", "")))
        recipe = mask_metadata.get("mask_recipe_json")
        if recipe is None:
            recipe = _json_str({k: v for k, v in mask_metadata.items() if k != "mask_hash"})
        _append_string(f["scan/mask_recipe_json"], str(recipe))

        if self._store_physical_masks:
            if physical_mask is None:
                raise PupilScanWriteError(
                    "physical_mask is required when store_physical_masks=True"
                )
            arr = np.asarray(physical_mask, dtype=np.uint8)
            if arr.ndim != 2:
                raise PupilScanWriteError(
                    f"physical_mask must be 2D [H_phys, W_phys], got {arr.shape}"
                )
            dset_masks: h5py.Dataset = f["masks/masks_physical"]
            if row == 0:
                dset_masks.resize((1, arr.shape[0], arr.shape[1]))
            else:
                if dset_masks.shape[1:] != arr.shape:
                    raise PupilScanWriteError(
                        f"all physical masks must have one shape; got {arr.shape}, "
                        f"expected {dset_masks.shape[1:]}"
                    )
                dset_masks.resize((row + 1, arr.shape[0], arr.shape[1]))
            dset_masks[row] = arr

        self._n_written += 1

    def finalize(self, *, completed: bool = True, error: str | None = None) -> None:
        if self._file is None or self._closed:
            return
        flags = _processing_flags(completed)
        flags["error"] = error
        flags["last_completed_capture_index"] = self._n_written - 1
        flags["n_captures_written"] = self._n_written
        self._file["capture/processing_flags_json"][()] = _json_str(flags)
        self._file.close()
        self._closed = True
        self._file = None

    def close(self) -> None:
        self.finalize(completed=True)

    def __enter__(self) -> PupilScanWriter:
        self.open()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if exc_type is not None:
            self.finalize(completed=False, error=str(exc_value))
        else:
            self.finalize(completed=True)


def _processing_flags(completed: bool) -> dict[str, Any]:
    return {
        "phase": "phase3_pupil_scan",
        "completed": bool(completed),
        "scientific_calibration_valid": False,
        "optical_alignment_validated": False,
        "training_ready": False,
    }


def _ensure_open(file: h5py.File | None) -> h5py.File:
    if file is None:
        raise PupilScanWriteError("file not open")
    return file


def _append_scalar(dset: h5py.Dataset, value: Any) -> None:
    n = dset.shape[0]
    dset.resize((n + 1,))
    dset[n] = value


def _append_string(dset: h5py.Dataset, value: str) -> None:
    n = dset.shape[0]
    dset.resize((n + 1,))
    dset[n] = value


def _int_or_default(value: Any, default: int) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _float_or_nan(value: Any) -> float:
    if value is None:
        return float("nan")
    try:
        return float(value)
    except (TypeError, ValueError):
        return float("nan")
