from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Any

import h5py
import numpy as np

from .artifacts.coordinate_frame import (
    camera_frame_extent_from_camera_metadata,
    camera_frame_extent_json_dict,
)
from .capture_plan import CapturePlan


RAW_CAPTURE_SCHEMA_VERSION = 2
SOFTWARE_NAME = "optic_system"
DEFAULT_CAPTURE_ROLE = "minimal_capture"


class RawCaptureWriteError(RuntimeError):
    pass


@dataclass(frozen=True)
class RawFrameStoragePolicy:
    """
    Storage policy for raw camera frame datasets.

    ``burst_stored_dtype=None`` means preserve the first burst input dtype.
    ``average_compute_dtype`` documents the intended averaging accumulator
    precision even when the caller provides pre-averaged frames.
    """

    raw_input_dtype: str = "preserve"
    average_compute_dtype: str = "float64"
    frames_avg_stored_dtype: str = "float32"
    burst_stored_dtype: str | None = None
    compression: str | None = "gzip"
    compression_opts: int | None = 4
    frames_avg_chunk_shape: tuple[int, int, int] = (1, 240, 240)
    burst_chunk_shape_hw: tuple[int, int] = (240, 240)

    def frames_avg_dtype(self) -> np.dtype:
        return np.dtype(self.frames_avg_stored_dtype)

    def burst_dtype(self, input_dtype: np.dtype) -> np.dtype:
        if self.burst_stored_dtype is None:
            return np.dtype(input_dtype)
        return np.dtype(self.burst_stored_dtype)

    def compression_kwargs(self) -> dict[str, Any]:
        if self.compression is None:
            return {}
        result: dict[str, Any] = {"compression": self.compression}
        if self.compression_opts is not None:
            result["compression_opts"] = self.compression_opts
        return result

    def to_dict(self) -> dict[str, Any]:
        return {
            "raw_input_dtype": self.raw_input_dtype,
            "average_compute_dtype": self.average_compute_dtype,
            "frames_avg_stored_dtype": str(np.dtype(self.frames_avg_stored_dtype)),
            "burst_stored_dtype": (
                None if self.burst_stored_dtype is None
                else str(np.dtype(self.burst_stored_dtype))
            ),
            "compression": self.compression,
            "compression_opts": self.compression_opts,
            "frames_avg_chunk_shape": [int(v) for v in self.frames_avg_chunk_shape],
            "burst_chunk_shape_hw": [int(v) for v in self.burst_chunk_shape_hw],
        }


def _now_ns() -> int:
    return time.monotonic_ns()


def _json_str(obj: Any) -> str:
    return json.dumps(obj, indent=2, default=str)


def _capture_role(plan: CapturePlan) -> str:
    value = plan.extra.get("capture_role") if isinstance(plan.extra, dict) else None
    if value is None:
        value = plan.extra.get("role") if isinstance(plan.extra, dict) else None
    text = str(value or DEFAULT_CAPTURE_ROLE).strip()
    allowed = {"minimal_capture", "profile_capture", "psf_capture", "survey_capture"}
    return text if text in allowed else DEFAULT_CAPTURE_ROLE


def _illumination_status_json(wavelength_entry: Any, tls_status: dict[str, Any] | None) -> dict[str, Any]:
    if tls_status and isinstance(tls_status.get("illumination"), dict):
        data = dict(tls_status["illumination"])
    else:
        data = wavelength_entry.illumination.to_dict()
    data.setdefault("nominal_wavelength_nm", float(wavelength_entry.nominal_wavelength_nm))
    if tls_status and tls_status.get("tls_action") is not None:
        data.setdefault("tls_action", tls_status.get("tls_action"))
    return data


class RawCaptureWriter:
    """
    Incremental raw capture HDF5 writer.

    Creates resizable datasets pre-allocated to ``plan.n_captures`` rows and
    appends one row per completed capture (wavelength x mask).
    """

    def __init__(
        self,
        output_path: str | Path,
        plan: CapturePlan,
        *,
        storage_policy: RawFrameStoragePolicy | None = None,
    ):
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        self._path = output_path
        self._plan = plan
        self._storage_policy = storage_policy or RawFrameStoragePolicy()
        self._file: h5py.File | None = None
        self._n_written: int = 0
        self._mask_arrays_written: bool = False
        self._closed: bool = False
        self._created_at_ns: int = _now_ns()

    def open(self) -> RawCaptureWriter:
        if self._file is not None:
            return self
        self._file = h5py.File(str(self._path), "w")
        self._init_structure()
        return self

    def _init_structure(self) -> None:
        assert self._file is not None
        f = self._file
        n_cap = self._plan.n_captures
        n_mask = self._plan.n_masks
        n_wl = self._plan.n_wavelengths
        k = self._plan.camera.frames_per_capture
        store_burst = self._plan.store_burst

        f.attrs["plan_id"] = self._plan.plan_id
        f.attrs["created_at_ns"] = self._created_at_ns
        f.attrs["software_version"] = SOFTWARE_NAME
        f.attrs["raw_capture_schema_version"] = RAW_CAPTURE_SCHEMA_VERSION
        f.attrs["capture_role"] = _capture_role(self._plan)
        f.attrs["hdf5_writer_version"] = "1.0"

        raw = f.require_group("raw")
        raw.attrs["store_burst"] = store_burst
        raw.attrs["frames_per_capture"] = k
        raw.attrs["storage_policy_json"] = _json_str(self._storage_policy.to_dict())
        raw.attrs["average_compute_dtype"] = self._storage_policy.average_compute_dtype
        raw.attrs["frames_avg_stored_dtype"] = str(self._storage_policy.frames_avg_dtype())
        raw.attrs["burst_stored_dtype"] = (
            "preserve_input"
            if self._storage_policy.burst_stored_dtype is None
            else str(np.dtype(self._storage_policy.burst_stored_dtype))
        )

        raw.create_dataset(
            "frames_avg",
            shape=(n_cap, 1, 1),
            maxshape=(n_cap, None, None),
            dtype=self._storage_policy.frames_avg_dtype(),
            chunks=self._storage_policy.frames_avg_chunk_shape,
            **self._storage_policy.compression_kwargs(),
        )

        masks_grp = f.require_group("masks")
        masks_grp.create_dataset(
            "masks_physical",
            shape=(n_mask, 1, 1),
            maxshape=(n_mask, None, None),
            dtype=np.uint8,
            chunks=(1, 240, 240),
            compression="gzip",
            compression_opts=4,
        )
        masks_grp.create_dataset("mask_id", shape=(n_mask,), dtype=h5py.string_dtype())
        masks_grp.create_dataset("family_id", shape=(n_mask,), dtype=h5py.string_dtype())
        masks_grp.create_dataset("family_params_json", shape=(n_mask,), dtype=h5py.string_dtype())
        masks_grp.create_dataset("has_mask_array", shape=(n_mask,), dtype=bool)
        masks_grp.attrs["mask_count"] = n_mask

        tls_grp = f.require_group("tls")
        tls_grp.create_dataset("grating", shape=(n_wl,), dtype=np.int64)
        tls_grp.create_dataset("settle_ms", shape=(n_wl,), dtype=np.int64)
        tls_grp.create_dataset("timestamp_ns", shape=(n_wl,), dtype=np.int64)
        tls_grp.create_dataset("status_json", shape=(n_wl,), dtype=h5py.string_dtype())

        illum_grp = f.require_group("illumination")
        illum_grp.create_dataset("illumination_json", shape=(n_wl,), dtype=h5py.string_dtype())
        illum_grp.create_dataset("nominal_wavelength_nm", shape=(n_wl,), dtype=np.float64)
        illum_grp.create_dataset("tls_setpoint_nm", shape=(n_wl,), dtype=np.float64)
        illum_grp.create_dataset("effective_wavelength_nm", shape=(n_wl,), dtype=np.float64)

        cam_grp = f.require_group("camera")
        cam_grp.create_dataset("requested_exposure_us", shape=(n_cap,), dtype=np.float64)
        cam_grp.create_dataset("requested_gain_db", shape=(n_cap,), dtype=np.float64)
        cam_grp.create_dataset("readback_exposure_us", shape=(n_cap,), dtype=np.float64)
        cam_grp.create_dataset("readback_gain_db", shape=(n_cap,), dtype=np.float64)
        cam_grp.create_dataset("frame_extent_json", shape=(n_cap,), dtype=h5py.string_dtype())
        cam_grp.create_dataset("timestamp_ns", shape=(n_cap,), dtype=np.int64)
        cam_grp.create_dataset("status_json", shape=(n_cap,), dtype=h5py.string_dtype())

        lcd_grp = f.require_group("lcd")
        lcd_grp.create_dataset("settle_ms", shape=(n_cap,), dtype=np.int64)
        lcd_grp.create_dataset("display_timestamp_ns", shape=(n_cap,), dtype=np.int64)
        lcd_grp.create_dataset("mapping_policy_json", shape=(1,), dtype=h5py.string_dtype())
        lcd_grp.create_dataset("metadata_json", shape=(1,), dtype=h5py.string_dtype())

        cap_grp = f.require_group("capture")
        cap_grp.create_dataset("capture_index", shape=(n_cap,), dtype=np.int64, fillvalue=-1)
        cap_grp.create_dataset("wavelength_index", shape=(n_cap,), dtype=np.int64, fillvalue=-1)
        cap_grp.create_dataset("mask_index", shape=(n_cap,), dtype=np.int64, fillvalue=-1)
        cap_grp.create_dataset("burst_count", shape=(n_cap,), dtype=np.int64, fillvalue=0)
        cap_grp.create_dataset("completed", shape=(n_cap,), dtype=bool, fillvalue=False)
        cap_grp.create_dataset("plan_json", data=_json_str(self._plan.to_dict()))
        cap_grp.create_dataset("plan_id", data=self._plan.plan_id)
        cap_grp.create_dataset("runtime_mode", data="")
        cap_grp.create_dataset("runtime_policy_json", data=_json_str({}))

        profile_grp = f.require_group("profiles")
        profile_requires = self._plan.requires
        profile_grp.create_dataset(
            "requirements_json",
            data=_json_str(profile_requires),
        )
        profile_grp.create_dataset(
            "pupil_profile_id",
            data=str(profile_requires.get("pupil_profile_id") or ""),
        )
        profile_grp.create_dataset(
            "camera_profile_id",
            data=str(profile_requires.get("camera_profile_id") or ""),
        )

        pf = {
            "scientific_calibration_valid": False,
            "optical_alignment_validated": False,
            "training_ready": False,
            "raw_capture_schema_version": RAW_CAPTURE_SCHEMA_VERSION,
            "capture_role": _capture_role(self._plan),
            "completed": False,
            "error": None,
            "last_completed_capture_index": -1,
        }
        cap_grp.create_dataset("processing_flags_json", data=_json_str(pf))

    def write_physical_masks(self, masks: list[np.ndarray]) -> None:
        if self._mask_arrays_written:
            return
        _ensure_open(self._file)
        masks_grp = self._file["masks"]

        arrays = [np.asarray(m, dtype=np.uint8) for m in masks]
        shapes = {arr.shape for arr in arrays}
        if len(shapes) != 1:
            raise RawCaptureWriteError(
                f"all physical masks must have the same shape, got {sorted(shapes)}"
            )

        arr0 = arrays[0]
        if arr0.ndim != 2:
            raise RawCaptureWriteError(
                f"physical masks must be 2D [H, 3W], got shape {arr0.shape}"
            )

        h, w = arr0.shape

        dset: h5py.Dataset = masks_grp["masks_physical"]
        dset.resize((self._plan.n_masks, h, w))

        for i, arr in enumerate(arrays):
            if i >= self._plan.n_masks:
                break
            dset[i] = arr
            masks_grp["has_mask_array"][i] = True

        for i, entry in enumerate(self._plan.masks):
            masks_grp["mask_id"][i] = entry.mask_id
            masks_grp["family_id"][i] = entry.family_id or ""
            masks_grp["family_params_json"][i] = _json_str(entry.family_params or {})

        self._mask_arrays_written = True

    def write_lcd_metadata(self, lcd_meta: dict[str, Any]) -> None:
        _ensure_open(self._file)
        lcd_grp = self._file["lcd"]

        axis = int(lcd_meta.get("subpixel_axis", 1))
        if axis == 0:
            mapping = {
                "display_rgb": "[H, W, 3]",
                "subpixel_axis": 0,
                "physical_mono": "[3H, W]",
            }
        else:
            mapping = {
                "display_rgb": "[H, W, 3]",
                "subpixel_axis": 1,
                "physical_mono": "[H, 3W]",
            }
        lcd_grp["mapping_policy_json"][0] = _json_str(mapping)
        lcd_grp["metadata_json"][0] = _json_str(lcd_meta)

    def write_runtime_metadata(self, runtime_policy: dict[str, Any]) -> None:
        _ensure_open(self._file)
        cap_grp = self._file["capture"]
        mode = str(runtime_policy.get("mode") or "")
        cap_grp["runtime_mode"][()] = mode
        cap_grp["runtime_policy_json"][()] = _json_str(runtime_policy)

    def append_capture(
        self,
        capture_index: int,
        wavelength_index: int,
        mask_index: int,
        frames: np.ndarray | None,
        frames_avg: np.ndarray,
        camera_meta: dict[str, Any],
        tls_status: dict[str, Any] | None = None,
        lcd_display_timestamp_ns: int = 0,
        requested_exposure_us: float | None = None,
        requested_gain_db: float | None = None,
        readback_exposure_us: float | None = None,
        readback_gain_db: float | None = None,
    ) -> None:
        _ensure_open(self._file)
        if self._closed:
            raise RawCaptureWriteError("file already closed")
        if self._n_written >= self._plan.n_captures:
            raise RawCaptureWriteError("all capture slots filled")

        f = self._file
        row = self._n_written
        store_burst = self._plan.store_burst

        avg_input = np.asarray(frames_avg)
        avg = avg_input.astype(self._storage_policy.frames_avg_dtype(), copy=False)
        if avg.ndim != 2:
            raise RawCaptureWriteError(
                f"frames_avg must be 2D [H, W], got {avg.ndim}D shape {avg.shape}"
            )

        dset_avg: h5py.Dataset = f["raw/frames_avg"]
        if dset_avg.shape[1:] != avg.shape:
            dset_avg.resize((self._plan.n_captures, avg.shape[0], avg.shape[1]))
        dset_avg[row] = avg

        if store_burst:
            if frames is None:
                raise RawCaptureWriteError(
                    "frames is required when plan.store_burst=True"
                )
            burst_input = np.asarray(frames)
            if burst_input.ndim != 3:
                raise RawCaptureWriteError(
                    "frames burst must be 3D [K, H, W], got "
                    f"{burst_input.ndim}D shape {burst_input.shape}"
                )
            dset = self._require_burst_dataset(burst_input.dtype)
            burst = burst_input.astype(dset.dtype, copy=False)
            if dset.shape[2:] != burst.shape[1:]:
                dset.resize((self._plan.n_captures, burst.shape[0], burst.shape[1], burst.shape[2]))
            dset[row] = burst

        raw_grp = f["raw"]
        raw_grp.attrs["frame_height"] = avg.shape[0]
        raw_grp.attrs["frame_width"] = avg.shape[1]
        raw_grp.attrs["frames_avg_input_dtype"] = str(avg_input.dtype)
        if store_burst and frames is not None:
            raw_grp.attrs["burst_input_dtype"] = str(np.asarray(frames).dtype)

        cap_grp = f["capture"]
        cap_grp["capture_index"][row] = capture_index
        cap_grp["wavelength_index"][row] = wavelength_index
        cap_grp["mask_index"][row] = mask_index
        cap_grp["burst_count"][row] = self._plan.camera.frames_per_capture
        cap_grp["completed"][row] = True

        cam_grp = f["camera"]
        cam_grp["requested_exposure_us"][row] = float(requested_exposure_us if requested_exposure_us is not None else -1)
        cam_grp["requested_gain_db"][row] = float(requested_gain_db if requested_gain_db is not None else -1)
        _readback_exposure = readback_exposure_us if readback_exposure_us is not None else camera_meta.get("exposure_us")
        _readback_gain = readback_gain_db if readback_gain_db is not None else camera_meta.get("gain_db")
        cam_grp["readback_exposure_us"][row] = float(_readback_exposure if _readback_exposure is not None else -1)
        cam_grp["readback_gain_db"][row] = float(_readback_gain if _readback_gain is not None else -1)
        frame_extent = camera_frame_extent_json_dict(
            camera_frame_extent_from_camera_metadata(
                camera_meta,
                fallback_shape=(int(avg.shape[0]), int(avg.shape[1])),
            )
        )
        frame_extent_json = _json_str(frame_extent)
        cam_grp["frame_extent_json"][row] = frame_extent_json
        cam_grp["timestamp_ns"][row] = int(camera_meta.get("timestamp_ns") or _now_ns())
        cam_grp["status_json"][row] = _json_str(camera_meta.get("status", {}))

        lcd_grp = f["lcd"]
        lcd_grp["settle_ms"][row] = self._plan.lcd_settle_ms
        lcd_grp["display_timestamp_ns"][row] = lcd_display_timestamp_ns

        wl = self._plan.wavelengths[wavelength_index]
        wl_grp = f["tls"]
        if tls_status:
            _wl_nm = float(
                tls_status.get("current_wavelength_nm")
                or tls_status.get("wavelength_nm")
                or wl.nominal_wavelength_nm
            )
            _grat = int(
                tls_status.get("grating") or wl.grating or -1
            )
            _tls_ts = int(
                tls_status.get("timestamp_ns") or _now_ns()
            )
        else:
            _wl_nm = float(wl.nominal_wavelength_nm)
            _grat = int(wl.grating or -1)
            _tls_ts = _now_ns()

        illum_json_str = _json_str(_illumination_status_json(wl, tls_status))
        illum_data = _illumination_status_json(wl, tls_status)
        f["illumination"]["illumination_json"][wavelength_index] = illum_json_str
        f["illumination"]["nominal_wavelength_nm"][wavelength_index] = _wl_nm
        f["illumination"]["tls_setpoint_nm"][wavelength_index] = (
            float(illum_data.get("tls_setpoint_nm"))
            if illum_data.get("tls_setpoint_nm") is not None
            else float("nan")
        )
        f["illumination"]["effective_wavelength_nm"][wavelength_index] = (
            float(illum_data.get("effective_wavelength_nm"))
            if illum_data.get("effective_wavelength_nm") is not None
            else float("nan")
        )
        wl_grp["grating"][wavelength_index] = _grat
        wl_grp["settle_ms"][wavelength_index] = wl.settle_ms
        wl_grp["timestamp_ns"][wavelength_index] = _tls_ts
        wl_grp["status_json"][wavelength_index] = _json_str(tls_status or {})

        self._n_written += 1

    def finalize(
        self,
        completed: bool = True,
        error: str | None = None,
        last_completed_capture_index: int | None = None,
    ) -> None:
        if self._file is None or self._closed:
            return

        pf = {
            "scientific_calibration_valid": False,
            "optical_alignment_validated": False,
            "training_ready": False,
            "raw_capture_schema_version": RAW_CAPTURE_SCHEMA_VERSION,
            "capture_role": _capture_role(self._plan),
            "completed": completed,
            "error": error,
            "last_completed_capture_index": (
                last_completed_capture_index
                if last_completed_capture_index is not None
                else self._n_written - 1
            ),
            "n_captures_written": self._n_written,
            "n_captures_total": self._plan.n_captures,
        }
        self._file["capture/processing_flags_json"][()] = _json_str(pf)
        self._file.close()
        self._closed = True
        self._file = None

    def close(self) -> None:
        self.finalize(completed=True)

    def __enter__(self) -> RawCaptureWriter:
        self.open()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if exc_type is not None:
            self.finalize(
                completed=False,
                error=str(exc_value),
                last_completed_capture_index=self._n_written - 1,
            )
        else:
            self.finalize(completed=True)

    @property
    def n_written(self) -> int:
        return self._n_written

    @property
    def path(self) -> Path:
        return self._path

    def _require_burst_dataset(self, input_dtype: np.dtype) -> h5py.Dataset:
        assert self._file is not None
        raw = self._file["raw"]
        if "frames" in raw:
            return raw["frames"]

        k = self._plan.camera.frames_per_capture
        chunk_h, chunk_w = self._storage_policy.burst_chunk_shape_hw
        return raw.create_dataset(
            "frames",
            shape=(self._plan.n_captures, k, 1, 1),
            maxshape=(self._plan.n_captures, k, None, None),
            dtype=self._storage_policy.burst_dtype(np.dtype(input_dtype)),
            chunks=(1, k, int(chunk_h), int(chunk_w)),
            **self._storage_policy.compression_kwargs(),
        )


def _ensure_open(file: h5py.File | None) -> h5py.File:
    if file is None:
        raise RawCaptureWriteError("file not open")
    return file
