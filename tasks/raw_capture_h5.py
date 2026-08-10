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


# Schema v3 makes the writer-emitted root identity and finalized capture-count
# flags mandatory. Existing schema-v2 files remain historical artifacts.
RAW_CAPTURE_SCHEMA_VERSION = 3
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
        self._committed_capture_indices: set[int] = set()
        self._committed_capture_combinations: set[tuple[int, int]] = set()
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
        f.attrs["artifact_type"] = "raw_capture"
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
        for index, entry in enumerate(self._plan.masks):
            masks_grp["mask_id"][index] = entry.mask_id
            masks_grp["family_id"][index] = entry.family_id or ""
            masks_grp["family_params_json"][index] = _json_str(entry.family_params or {})

        tls_grp = f.require_group("tls")
        tls_grp.create_dataset("grating", shape=(n_wl,), dtype=np.int64)
        tls_grp.create_dataset("settle_ms", shape=(n_wl,), dtype=np.int64)
        tls_grp.create_dataset("timestamp_ns", shape=(n_wl,), dtype=np.int64)
        tls_grp.create_dataset("status_json", shape=(n_wl,), dtype=h5py.string_dtype())

        illum_grp = f.require_group("illumination")
        illum_grp.create_dataset("illumination_json", shape=(n_wl,), dtype=h5py.string_dtype())
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
        lcd_grp["mapping_policy_json"][0] = _json_str({})
        lcd_grp["metadata_json"][0] = _json_str({})

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
            "capture_complete": False,
            "run_succeeded": False,
            "error": "capture not finalized",
            "last_completed_capture_index": -1,
            "n_captures_written": 0,
            "n_captures_total": n_cap,
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

        capture_index = _validated_index(
            capture_index,
            upper_bound=self._plan.n_captures,
            name="capture_index",
        )
        wavelength_index = _validated_index(
            wavelength_index,
            upper_bound=self._plan.n_wavelengths,
            name="wavelength_index",
        )
        mask_index = _validated_index(
            mask_index,
            upper_bound=self._plan.n_masks,
            name="mask_index",
        )
        expected_capture_index = (
            wavelength_index * self._plan.n_masks + mask_index
        )
        if capture_index != expected_capture_index:
            raise RawCaptureWriteError(
                "capture_index does not match the capture-plan Cartesian schedule: "
                f"expected {expected_capture_index} for wavelength_index="
                f"{wavelength_index}, mask_index={mask_index}, got {capture_index}"
            )
        combination = (wavelength_index, mask_index)
        if capture_index in self._committed_capture_indices:
            raise RawCaptureWriteError(
                f"capture_index {capture_index} has already been committed"
            )
        if combination in self._committed_capture_combinations:
            raise RawCaptureWriteError(
                "capture-plan combination has already been committed: "
                f"wavelength_index={wavelength_index}, mask_index={mask_index}"
            )
        if capture_index != row:
            raise RawCaptureWriteError(
                "captures must be committed in wavelength-major order: "
                f"expected capture_index {row}, got {capture_index}"
            )
        if not isinstance(camera_meta, dict):
            raise RawCaptureWriteError("camera_meta must be a mapping")
        if tls_status is not None and not isinstance(tls_status, dict):
            raise RawCaptureWriteError("tls_status must be a mapping or null")

        avg_input = np.asarray(frames_avg)
        avg = avg_input.astype(self._storage_policy.frames_avg_dtype(), copy=False)
        if avg.ndim != 2:
            raise RawCaptureWriteError(
                f"frames_avg must be 2D [H, W], got {avg.ndim}D shape {avg.shape}"
            )

        burst_input: np.ndarray | None = None
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
            expected_count = self._plan.camera.frames_per_capture
            if burst_input.shape[0] != expected_count:
                raise RawCaptureWriteError(
                    "frames burst count does not match plan.camera.frames_per_capture: "
                    f"expected {expected_count}, got {burst_input.shape[0]}"
                )
            if tuple(burst_input.shape[1:]) != tuple(avg.shape):
                raise RawCaptureWriteError(
                    "frames burst spatial shape must match frames_avg: "
                    f"expected {avg.shape}, got {burst_input.shape[1:]}"
                )

        requested_exposure = float(
            requested_exposure_us if requested_exposure_us is not None else -1
        )
        requested_gain = float(
            requested_gain_db if requested_gain_db is not None else -1
        )
        readback_exposure = (
            readback_exposure_us
            if readback_exposure_us is not None
            else camera_meta.get("exposure_us")
        )
        readback_gain = (
            readback_gain_db
            if readback_gain_db is not None
            else camera_meta.get("gain_db")
        )
        readback_exposure_value = float(
            readback_exposure if readback_exposure is not None else -1
        )
        readback_gain_value = float(
            readback_gain if readback_gain is not None else -1
        )
        frame_extent = camera_frame_extent_json_dict(
            camera_frame_extent_from_camera_metadata(
                camera_meta,
                fallback_shape=(int(avg.shape[0]), int(avg.shape[1])),
            )
        )
        frame_extent_json = _json_str(frame_extent)
        camera_timestamp_ns = int(camera_meta.get("timestamp_ns") or _now_ns())
        camera_status_json = _json_str(camera_meta.get("status", {}))
        lcd_timestamp_ns = int(lcd_display_timestamp_ns)

        wl = self._plan.wavelengths[wavelength_index]
        if tls_status:
            grating = int(tls_status.get("grating") or wl.grating or -1)
            tls_timestamp_ns = int(tls_status.get("timestamp_ns") or _now_ns())
        else:
            grating = int(wl.grating or -1)
            tls_timestamp_ns = _now_ns()
        illumination_data = _illumination_status_json(wl, tls_status)
        illumination_json = _json_str(illumination_data)
        tls_setpoint_nm = (
            float(illumination_data["tls_setpoint_nm"])
            if illumination_data.get("tls_setpoint_nm") is not None
            else float("nan")
        )
        effective_wavelength_nm = (
            float(illumination_data["effective_wavelength_nm"])
            if illumination_data.get("effective_wavelength_nm") is not None
            else float("nan")
        )
        tls_settle_ms = int(wl.settle_ms)
        tls_status_json = _json_str(tls_status or {})

        # A row is committed only after every frame and metadata field succeeds.
        dset_avg: h5py.Dataset = f["raw/frames_avg"]
        if dset_avg.shape[1:] != avg.shape:
            dset_avg.resize((self._plan.n_captures, avg.shape[0], avg.shape[1]))
        dset_avg[row] = avg

        if burst_input is not None:
            dset = self._require_burst_dataset(burst_input.dtype)
            burst = burst_input.astype(dset.dtype, copy=False)
            if dset.shape[2:] != burst.shape[1:]:
                dset.resize(
                    (
                        self._plan.n_captures,
                        burst.shape[0],
                        burst.shape[1],
                        burst.shape[2],
                    )
                )
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

        cam_grp = f["camera"]
        cam_grp["requested_exposure_us"][row] = requested_exposure
        cam_grp["requested_gain_db"][row] = requested_gain
        cam_grp["readback_exposure_us"][row] = readback_exposure_value
        cam_grp["readback_gain_db"][row] = readback_gain_value
        cam_grp["frame_extent_json"][row] = frame_extent_json
        cam_grp["timestamp_ns"][row] = camera_timestamp_ns
        cam_grp["status_json"][row] = camera_status_json

        lcd_grp = f["lcd"]
        lcd_grp["settle_ms"][row] = self._plan.lcd_settle_ms
        lcd_grp["display_timestamp_ns"][row] = lcd_timestamp_ns

        wl_grp = f["tls"]
        f["illumination"]["illumination_json"][wavelength_index] = illumination_json
        f["illumination"]["tls_setpoint_nm"][wavelength_index] = tls_setpoint_nm
        f["illumination"]["effective_wavelength_nm"][wavelength_index] = (
            effective_wavelength_nm
        )
        wl_grp["grating"][wavelength_index] = grating
        wl_grp["settle_ms"][wavelength_index] = tls_settle_ms
        wl_grp["timestamp_ns"][wavelength_index] = tls_timestamp_ns
        wl_grp["status_json"][wavelength_index] = tls_status_json

        cap_grp["completed"][row] = True
        self._committed_capture_indices.add(capture_index)
        self._committed_capture_combinations.add(combination)
        self._n_written += 1

    def finalize(
        self,
        error: str | None = None,
    ) -> None:
        if self._file is None or self._closed:
            return

        completed_bitmap = np.asarray(
            self._file["capture/completed"][()],
            dtype=bool,
        )
        completed_rows = np.flatnonzero(completed_bitmap)
        captures_written = int(completed_rows.size)
        capture_complete = _capture_schedule_is_complete(
            completed_bitmap,
            np.asarray(self._file["capture/capture_index"][()], dtype=np.int64),
            np.asarray(self._file["capture/wavelength_index"][()], dtype=np.int64),
            np.asarray(self._file["capture/mask_index"][()], dtype=np.int64),
            n_wavelengths=self._plan.n_wavelengths,
            n_masks=self._plan.n_masks,
        )
        run_succeeded = error is None
        if captures_written:
            last_row = int(completed_rows[-1])
            last_completed_capture_index = int(
                self._file["capture/capture_index"][last_row]
            )
        else:
            last_completed_capture_index = -1
        self._n_written = captures_written

        pf = {
            "scientific_calibration_valid": False,
            "optical_alignment_validated": False,
            "training_ready": False,
            "raw_capture_schema_version": RAW_CAPTURE_SCHEMA_VERSION,
            "capture_role": _capture_role(self._plan),
            "capture_complete": capture_complete,
            "run_succeeded": run_succeeded,
            "error": error,
            "last_completed_capture_index": last_completed_capture_index,
            "n_captures_written": captures_written,
            "n_captures_total": self._plan.n_captures,
        }
        self._file["capture/processing_flags_json"][()] = _json_str(pf)
        self._file.close()
        self._closed = True
        self._file = None

    def close(self) -> None:
        self.finalize()

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
            self.finalize(error=str(exc_value))
        else:
            self.finalize()

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


def _validated_index(value: Any, *, upper_bound: int, name: str) -> int:
    if isinstance(value, (bool, np.bool_)) or not isinstance(
        value,
        (int, np.integer),
    ):
        raise RawCaptureWriteError(f"{name} must be an integer")
    index = int(value)
    if index < 0 or index >= upper_bound:
        raise RawCaptureWriteError(
            f"{name} must be in [0, {upper_bound}), got {index}"
        )
    return index


def _capture_schedule_is_complete(
    completed: np.ndarray,
    capture_indices: np.ndarray,
    wavelength_indices: np.ndarray,
    mask_indices: np.ndarray,
    *,
    n_wavelengths: int,
    n_masks: int,
) -> bool:
    """Return whether committed rows exactly cover the capture-plan schedule."""
    planned_count = n_wavelengths * n_masks
    if completed.shape != (planned_count,) or not bool(np.all(completed)):
        return False
    rows = np.flatnonzero(completed)
    actual_capture_indices = {int(capture_indices[row]) for row in rows}
    actual_combinations = {
        (int(wavelength_indices[row]), int(mask_indices[row])) for row in rows
    }
    expected_capture_indices = set(range(planned_count))
    expected_combinations = {
        (wavelength_index, mask_index)
        for wavelength_index in range(n_wavelengths)
        for mask_index in range(n_masks)
    }
    if (
        actual_capture_indices != expected_capture_indices
        or actual_combinations != expected_combinations
    ):
        return False
    return all(
        int(capture_indices[row])
        == int(wavelength_indices[row]) * n_masks + int(mask_indices[row])
        for row in rows
    )


def _ensure_open(file: h5py.File | None) -> h5py.File:
    if file is None:
        raise RawCaptureWriteError("file not open")
    return file
