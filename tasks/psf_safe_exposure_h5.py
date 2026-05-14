from __future__ import annotations

import json
import time
from pathlib import Path
from types import TracebackType
from typing import Any

import h5py
import numpy as np


class PsfSafeExposureWriteError(RuntimeError):
    pass


def _now_ns() -> int:
    return time.monotonic_ns()


def _json_str(obj: Any) -> str:
    return json.dumps(obj, indent=2, default=str)


class PsfSafeExposureWriter:
    """
    Dedicated HDF5 writer for PSF-safe camera exposure/gain sweep.

    Records wavelength x exposure x gain test rows with strict peak-pixel
    PSF safety metrics.  A setting is PSF-safe only when every raw burst
    frame pixel is strictly below the dtype full scale.
    """

    def __init__(self, output_path: str | Path, plan_id: str = "psf_safe_exposure",
                 frames_per_capture: int = 5):
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        self._path = Path(output_path)
        self._plan_id = plan_id
        self._frames_per_capture = frames_per_capture
        self._file: h5py.File | None = None
        self._n_written: int = 0
        self._closed: bool = False
        self._created_at_ns: int = _now_ns()
        self._full_scale: int = 255

    @property
    def n_written(self) -> int:
        return self._n_written

    @property
    def path(self) -> Path:
        return self._path

    def open(self) -> PsfSafeExposureWriter:
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

        string_dtype = h5py.string_dtype()
        sweep = f.require_group("sweep")
        sweep.create_dataset("exposure_us", shape=(0,), maxshape=(None,), dtype=np.float64)
        sweep.create_dataset("gain_db", shape=(0,), maxshape=(None,), dtype=np.float64)
        sweep.create_dataset("wavelength_nm", shape=(0,), maxshape=(None,), dtype=np.float64)
        sweep.create_dataset("peak_pixel_burst", shape=(0,), maxshape=(None,), dtype=np.float64)
        sweep.create_dataset("peak_pixel_avg", shape=(0,), maxshape=(None,), dtype=np.float64)
        sweep.create_dataset("peak_pixel_fraction_burst", shape=(0,), maxshape=(None,), dtype=np.float64)
        sweep.create_dataset("peak_margin_to_full_scale", shape=(0,), maxshape=(None,), dtype=np.float64)
        sweep.create_dataset("p99_0_avg", shape=(0,), maxshape=(None,), dtype=np.float64)
        sweep.create_dataset("p99_9_avg", shape=(0,), maxshape=(None,), dtype=np.float64)
        sweep.create_dataset("unsafe_reason", shape=(0,), maxshape=(None,), dtype=string_dtype)
        sweep.create_dataset("psf_safe", shape=(0,), maxshape=(None,), dtype=bool)
        sweep.create_dataset("p_signal", shape=(0,), maxshape=(None,), dtype=np.float64)
        sweep.create_dataset("dynamic_range", shape=(0,), maxshape=(None,), dtype=np.float64)
        sweep.create_dataset("low_signal", shape=(0,), maxshape=(None,), dtype=bool)
        sweep.create_dataset("frame_dtype_full_scale", data=self._full_scale)
        sweep.attrs["frame_dtype_full_scale"] = self._full_scale

        lcd_grp = f.require_group("lcd")
        lcd_grp.create_dataset("metadata_json", shape=(1,), dtype=string_dtype)

        cap_grp = f.require_group("capture")
        cap_grp.create_dataset("plan_json", shape=(1,), dtype=string_dtype)
        cap_grp.create_dataset("plan_id", data=self._plan_id)

        pf = {
            "scientific_calibration_valid": False,
            "optical_alignment_validated": False,
            "training_ready": False,
            "phase": "phase3_0_5b_psf_safe_exposure",
            "completed": False,
            "error": None,
            "last_completed_sweep_index": -1,
        }
        cap_grp.create_dataset("processing_flags_json", data=_json_str(pf))

    def set_full_scale(self, full_scale: int) -> None:
        self._full_scale = int(full_scale)
        if self._file is not None and "sweep" in self._file:
            self._file["sweep"].attrs["frame_dtype_full_scale"] = self._full_scale
            self._file["sweep/frame_dtype_full_scale"][()] = self._full_scale

    def write_lcd_metadata(self, lcd_meta: dict[str, Any]) -> None:
        _ensure_open(self._file)
        self._file["lcd/metadata_json"][0] = _json_str(lcd_meta)

    def write_plan_json(self, plan_dict: dict[str, Any]) -> None:
        _ensure_open(self._file)
        self._file["capture/plan_json"][0] = _json_str(plan_dict)

    def append_sweep_row(
        self,
        wavelength_nm: float,
        exposure_us: float,
        gain_db: float,
        frames_avg: np.ndarray,
        peak_pixel_burst: float,
        peak_pixel_avg: float,
        peak_pixel_fraction_burst: float,
        peak_margin_to_full_scale: float,
        p99_0_avg: float | None,
        p99_9_avg: float | None,
        unsafe_reason: str | None,
        psf_safe: bool,
        p_signal: float,
        dynamic_range: float,
        low_signal: bool,
    ) -> None:
        _ensure_open(self._file)
        if self._closed:
            raise PsfSafeExposureWriteError("file already closed")

        f = self._file
        row = self._n_written

        avg = np.asarray(frames_avg, dtype=np.float64)
        if avg.ndim != 2:
            raise PsfSafeExposureWriteError(
                f"frames_avg must be 2D [H, W], got shape {avg.shape}"
            )

        dset_avg: h5py.Dataset = f["raw/frames_avg"]
        if row == 0:
            dset_avg.resize((1, avg.shape[0], avg.shape[1]))
        else:
            dset_avg.resize((row + 1, avg.shape[0], avg.shape[1]))
        dset_avg[row] = avg

        _append_scalar(f["sweep/exposure_us"], exposure_us)
        _append_scalar(f["sweep/gain_db"], gain_db)
        _append_scalar(f["sweep/wavelength_nm"], wavelength_nm)
        _append_scalar(f["sweep/peak_pixel_burst"], peak_pixel_burst)
        _append_scalar(f["sweep/peak_pixel_avg"], peak_pixel_avg)
        _append_scalar(f["sweep/peak_pixel_fraction_burst"], peak_pixel_fraction_burst)
        _append_scalar(f["sweep/peak_margin_to_full_scale"], peak_margin_to_full_scale)
        _append_scalar(f["sweep/p99_0_avg"], p99_0_avg if p99_0_avg is not None else np.nan)
        _append_scalar(f["sweep/p99_9_avg"], p99_9_avg if p99_9_avg is not None else np.nan)
        _append_scalar(f["sweep/unsafe_reason"], unsafe_reason or "")
        _append_scalar(f["sweep/psf_safe"], bool(psf_safe))
        _append_scalar(f["sweep/p_signal"], p_signal)
        _append_scalar(f["sweep/dynamic_range"], dynamic_range)
        _append_scalar(f["sweep/low_signal"], bool(low_signal))

        raw_grp = f["raw"]
        raw_grp.attrs["frame_height"] = avg.shape[0]
        raw_grp.attrs["frame_width"] = avg.shape[1]

        self._n_written += 1

    def finalize(
        self,
        completed: bool = True,
        error: str | None = None,
    ) -> None:
        if self._file is None or self._closed:
            return
        pf = {
            "scientific_calibration_valid": False,
            "optical_alignment_validated": False,
            "training_ready": False,
            "phase": "phase3_0_5b_psf_safe_exposure",
            "completed": completed,
            "error": error,
            "last_completed_sweep_index": self._n_written - 1,
            "n_sweeps_written": self._n_written,
        }
        self._file["capture/processing_flags_json"][()] = _json_str(pf)
        self._file.close()
        self._closed = True
        self._file = None

    def close(self) -> None:
        self.finalize(completed=True)

    def __enter__(self) -> PsfSafeExposureWriter:
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
            self.close()


def _ensure_open(file: h5py.File | None) -> h5py.File:
    if file is None:
        raise PsfSafeExposureWriteError("file not open")
    return file


def _append_scalar(dset: h5py.Dataset, value: Any) -> None:
    old_size = dset.shape[0]
    dset.resize((old_size + 1,))
    dset[old_size] = value
