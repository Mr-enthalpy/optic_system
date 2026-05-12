from __future__ import annotations

import json
import time
from pathlib import Path
from types import TracebackType
from typing import Any

import h5py
import numpy as np

from .capture_plan import CapturePlan


class RawCaptureWriteError(RuntimeError):
    pass


def _now_ns() -> int:
    return time.monotonic_ns()


def _json_str(obj: Any) -> str:
    return json.dumps(obj, indent=2, default=str)


class RawCaptureWriter:
    """
    Incremental raw capture HDF5 writer.

    Creates resizable datasets pre-allocated to ``plan.n_captures`` rows and
    appends one row per completed capture (wavelength × mask).
    """

    def __init__(self, output_path: str | Path, plan: CapturePlan):
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        self._path = output_path
        self._plan = plan
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
        f.attrs["software_version"] = "optic_system phase2"
        f.attrs["hdf5_writer_version"] = "1.0"

        raw = f.require_group("raw")
        raw.attrs["store_burst"] = store_burst
        raw.attrs["frames_per_capture"] = k

        raw.create_dataset(
            "frames_avg",
            shape=(n_cap, 1, 1),
            maxshape=(n_cap, None, None),
            dtype=np.float64,
            chunks=(1, 240, 240),
            compression="gzip",
            compression_opts=4,
        )
        if store_burst:
            raw.create_dataset(
                "frames",
                shape=(n_cap, k, 1, 1),
                maxshape=(n_cap, k, None, None),
                dtype=np.float64,
                chunks=(1, k, 240, 240),
                compression="gzip",
                compression_opts=4,
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
        tls_grp.create_dataset("wavelength_nm", shape=(n_wl,), dtype=np.float64)
        tls_grp.create_dataset("grating", shape=(n_wl,), dtype=np.int64)
        tls_grp.create_dataset("settle_ms", shape=(n_wl,), dtype=np.int64)
        tls_grp.create_dataset("timestamp_ns", shape=(n_wl,), dtype=np.int64)
        tls_grp.create_dataset("status_json", shape=(n_wl,), dtype=h5py.string_dtype())

        cam_grp = f.require_group("camera")
        cam_grp.create_dataset("requested_exposure_us", shape=(n_cap,), dtype=np.float64)
        cam_grp.create_dataset("requested_gain_db", shape=(n_cap,), dtype=np.float64)
        cam_grp.create_dataset("readback_exposure_us", shape=(n_cap,), dtype=np.float64)
        cam_grp.create_dataset("readback_gain_db", shape=(n_cap,), dtype=np.float64)
        cam_grp.create_dataset("roi_json", shape=(n_cap,), dtype=h5py.string_dtype())
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

        pf = {
            "scientific_calibration_valid": False,
            "optical_alignment_validated": False,
            "training_ready": False,
            "phase": "phase2_minimal_capture",
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

        avg = np.asarray(frames_avg, dtype=np.float64)
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
            burst = np.asarray(frames, dtype=np.float64)
            if burst.ndim != 3:
                raise RawCaptureWriteError(
                    f"frames burst must be 3D [K, H, W], got {burst.ndim}D shape {burst.shape}"
                )
            dset = f["raw/frames"]
            if dset.shape[2:] != burst.shape[1:]:
                dset.resize((self._plan.n_captures, burst.shape[0], burst.shape[1], burst.shape[2]))
            dset[row] = burst

        raw_grp = f["raw"]
        raw_grp.attrs["frame_height"] = avg.shape[0]
        raw_grp.attrs["frame_width"] = avg.shape[1]

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
        cam_grp["roi_json"][row] = _json_str(camera_meta.get("roi"))
        cam_grp["timestamp_ns"][row] = int(camera_meta.get("timestamp_ns") or _now_ns())
        cam_grp["status_json"][row] = _json_str(camera_meta.get("status", {}))

        lcd_grp = f["lcd"]
        lcd_grp["settle_ms"][row] = self._plan.lcd_settle_ms
        lcd_grp["display_timestamp_ns"][row] = lcd_display_timestamp_ns

        wl = self._plan.wavelengths[wavelength_index]
        tls_grp = f["tls"]
        if tls_status:
            _wl_nm = float(
                tls_status.get("current_wavelength_nm")
                or tls_status.get("wavelength_nm")
                or wl.wavelength_nm
            )
            _grat = int(
                tls_status.get("grating") or wl.grating or -1
            )
            _tls_ts = int(
                tls_status.get("timestamp_ns") or _now_ns()
            )
        else:
            _wl_nm = float(wl.wavelength_nm)
            _grat = int(wl.grating or -1)
            _tls_ts = _now_ns()

        tls_grp["wavelength_nm"][wavelength_index] = _wl_nm
        tls_grp["grating"][wavelength_index] = _grat
        tls_grp["settle_ms"][wavelength_index] = wl.settle_ms
        tls_grp["timestamp_ns"][wavelength_index] = _tls_ts
        tls_grp["status_json"][wavelength_index] = _json_str(tls_status or {})

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
            "phase": "phase2_minimal_capture",
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


def _ensure_open(file: h5py.File | None) -> h5py.File:
    if file is None:
        raise RawCaptureWriteError("file not open")
    return file
