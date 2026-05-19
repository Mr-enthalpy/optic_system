from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import h5py
import numpy as np

from tasks.psf_phase3 import center_of_mass, json_dumps, now_ns, processing_flags


class PSFDictionaryRawWriter:
    def __init__(self, output_path: str | Path, *, plan_id: str, phase: str = "3.4"):
        self.path = Path(output_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.plan_id = str(plan_id)
        self.phase = str(phase)
        self._file: h5py.File | None = None
        self._n = 0

    def open(self) -> "PSFDictionaryRawWriter":
        self._file = h5py.File(str(self.path), "w")
        string_dtype = h5py.string_dtype(encoding="utf-8")
        f = self._file
        f.attrs["plan_id"] = self.plan_id
        f.attrs["phase"] = self.phase
        f.attrs["created_at_ns"] = now_ns()
        raw = f.require_group("raw")
        raw.create_dataset("crops", shape=(0, 1, 1), maxshape=(None, None, None), dtype=np.float64, chunks=(1, 64, 64), compression="gzip", compression_opts=4)
        raw.create_dataset("masks_lowres", shape=(0, 1, 1, 1), maxshape=(None, 1, None, None), dtype=np.uint8, chunks=(1, 1, 64, 64), compression="gzip", compression_opts=4)
        raw.create_dataset("mask_id", shape=(0,), maxshape=(None,), dtype=string_dtype)
        raw.create_dataset("mask_family", shape=(0,), maxshape=(None,), dtype=string_dtype)
        raw.create_dataset("wavelength_nm", shape=(0,), maxshape=(None,), dtype=np.float64)
        raw.create_dataset("wavelength_index", shape=(0,), maxshape=(None,), dtype=np.int64)
        raw.create_dataset("repeat_index", shape=(0,), maxshape=(None,), dtype=np.int64)
        raw.create_dataset("exposure_us", shape=(0,), maxshape=(None,), dtype=np.float64)
        raw.create_dataset("gain_db", shape=(0,), maxshape=(None,), dtype=np.float64)
        raw.create_dataset("camera_profile_id", shape=(0,), maxshape=(None,), dtype=string_dtype)
        raw.create_dataset("timestamp_ns", shape=(0,), maxshape=(None,), dtype=np.int64)
        raw.create_dataset("mask_metadata_json", shape=(0,), maxshape=(None,), dtype=string_dtype)
        capture = f.require_group("capture")
        capture.create_dataset("plan_json", data="", dtype=string_dtype)
        capture.create_dataset("processing_flags_json", data=json_dumps(processing_flags(self.phase, False)), dtype=string_dtype)
        f.require_group("camera").create_dataset("metadata_json", data="", dtype=string_dtype)
        f.require_group("lcd").create_dataset("metadata_json", data="", dtype=string_dtype)
        f.require_group("tls").create_dataset("metadata_json", data="", dtype=string_dtype)
        provenance = f.require_group("provenance")
        provenance.create_dataset("pupil_window_source_json", data="", dtype=string_dtype)
        provenance.create_dataset("psf_roi_source_json", data="", dtype=string_dtype)
        provenance.create_dataset("camera_params_source_json", data="", dtype=string_dtype)
        return self

    def write_json_sections(
        self,
        *,
        plan: dict[str, Any],
        camera_metadata: dict[str, Any],
        lcd_metadata: dict[str, Any],
        tls_metadata: dict[str, Any],
        pupil_window_source: dict[str, Any],
        psf_roi_source: dict[str, Any],
        camera_params_source: dict[str, Any],
    ) -> None:
        f = self._ensure_open()
        f["capture/plan_json"][()] = json_dumps(plan)
        f["camera/metadata_json"][()] = json_dumps(camera_metadata)
        f["lcd/metadata_json"][()] = json_dumps(lcd_metadata)
        f["tls/metadata_json"][()] = json_dumps(tls_metadata)
        f["provenance/pupil_window_source_json"][()] = json_dumps(pupil_window_source)
        f["provenance/psf_roi_source_json"][()] = json_dumps(psf_roi_source)
        f["provenance/camera_params_source_json"][()] = json_dumps(camera_params_source)

    def append_capture(
        self,
        *,
        frame_avg: np.ndarray | None = None,
        crop: np.ndarray,
        lowres_mask: np.ndarray,
        mask_id: str,
        mask_family: str,
        wavelength_nm: float,
        wavelength_index: int,
        repeat_index: int,
        exposure_us: float = 0.0,
        gain_db: float = 0.0,
        camera_profile_id: str = "unknown",
        mask_metadata: dict[str, Any],
    ) -> int:
        f = self._ensure_open()
        _ = frame_avg  # Phase 3.4 raw dictionary stores ROI crops only.
        row = self._n
        _append_frame(f["raw/crops"], row, np.asarray(crop, dtype=np.float64))
        _append_lowres_mask(f["raw/masks_lowres"], row, np.asarray(lowres_mask, dtype=np.uint8))
        _append_scalar(f["raw/mask_id"], str(mask_id))
        _append_scalar(f["raw/mask_family"], str(mask_family))
        _append_scalar(f["raw/wavelength_nm"], float(wavelength_nm))
        _append_scalar(f["raw/wavelength_index"], int(wavelength_index))
        _append_scalar(f["raw/repeat_index"], int(repeat_index))
        _append_scalar(f["raw/exposure_us"], float(exposure_us))
        _append_scalar(f["raw/gain_db"], float(gain_db))
        _append_scalar(f["raw/camera_profile_id"], str(camera_profile_id))
        _append_scalar(f["raw/timestamp_ns"], int(time.time_ns()))
        _append_scalar(f["raw/mask_metadata_json"], json_dumps(mask_metadata))
        self._n += 1
        return row

    def finalize(self, *, completed: bool, error: str | None = None, analysis_valid: bool | None = None) -> None:
        if self._file is None:
            return
        flags = processing_flags(self.phase, completed)
        flags["error"] = error
        flags["n_captures_written"] = self._n
        if analysis_valid is not None:
            flags["analysis_valid"] = bool(analysis_valid)
        self._file["capture/processing_flags_json"][()] = json_dumps(flags)
        self._file.close()
        self._file = None

    def _ensure_open(self) -> h5py.File:
        if self._file is None:
            raise RuntimeError("HDF5 writer is not open")
        return self._file


def psf_dictionary_stats(crops: np.ndarray, mask_ids: list[str]) -> dict[str, Any]:
    arr = np.asarray(crops, dtype=np.float64)
    ids = [str(x) for x in mask_ids]
    unique_ids = list(dict.fromkeys(ids))
    by_mask = {mid: np.where(np.asarray(ids) == mid)[0] for mid in unique_ids}
    means = np.stack([np.mean(arr[idx], axis=0) for idx in by_mask.values()], axis=0)
    stds = np.stack([np.std(arr[idx], axis=0) for idx in by_mask.values()], axis=0)
    repeat_mse: list[float] = []
    total_energy_cv: list[float] = []
    center_drift_max = 0.0
    for idx in by_mask.values():
        local = arr[idx]
        energies = np.asarray([float(np.sum(np.maximum(item, 0.0))) for item in local], dtype=np.float64)
        mean_energy = float(np.mean(energies)) if energies.size else 0.0
        if mean_energy > 0.0:
            total_energy_cv.append(float(np.std(energies) / mean_energy))
        centers = np.asarray([center_of_mass(item) for item in local], dtype=np.float64)
        if centers.size:
            drift = np.linalg.norm(centers - np.mean(centers, axis=0), axis=1)
            center_drift_max = max(center_drift_max, float(np.max(drift)))
        for i in range(len(local)):
            for j in range(i + 1, len(local)):
                diff = local[i] - local[j]
                repeat_mse.append(float(np.mean(diff * diff)))
    return {
        "mask_ids": unique_ids,
        "psf_mean_stack": means,
        "psf_std_stack": stds,
        "quality": {
            "mean_repeat_mse": float(np.mean(repeat_mse)) if repeat_mse else 0.0,
            "median_repeat_mse": float(np.median(repeat_mse)) if repeat_mse else 0.0,
            "mean_total_energy_cv": float(np.mean(total_energy_cv)) if total_energy_cv else 0.0,
            "max_center_drift_px": float(center_drift_max),
        },
    }


def psf_dictionary_stats_by_mask_and_wavelength(
    crops: np.ndarray,
    mask_ids: list[str],
    wavelength_index: np.ndarray,
) -> dict[str, Any]:
    arr = np.asarray(crops, dtype=np.float64)
    ids = np.asarray([str(x) for x in mask_ids], dtype=object)
    wl_idx = np.asarray(wavelength_index, dtype=np.int64)
    if arr.ndim != 3:
        raise ValueError(f"crops must be [N,H,W], got {arr.shape}")
    if ids.shape[0] != arr.shape[0] or wl_idx.shape[0] != arr.shape[0]:
        raise ValueError("crops, mask_ids, and wavelength_index must share the same length")

    unique_ids = list(dict.fromkeys(ids.tolist()))
    unique_wavelength_index = list(dict.fromkeys(int(x) for x in wl_idx.tolist()))
    means = np.zeros((len(unique_ids), len(unique_wavelength_index), arr.shape[-2], arr.shape[-1]), dtype=np.float64)
    stds = np.zeros_like(means)
    repeat_mse: list[float] = []
    total_energy_cv: list[float] = []
    center_drift_max = 0.0

    for i, mask_id in enumerate(unique_ids):
        for j, wavelength in enumerate(unique_wavelength_index):
            group_idx = np.where((ids == mask_id) & (wl_idx == int(wavelength)))[0]
            if group_idx.size == 0:
                raise ValueError(f"missing group for mask_id={mask_id} wavelength_index={wavelength}")
            local = arr[group_idx]
            means[i, j] = np.mean(local, axis=0)
            stds[i, j] = np.std(local, axis=0)
            energies = np.asarray([float(np.sum(np.maximum(item, 0.0))) for item in local], dtype=np.float64)
            mean_energy = float(np.mean(energies)) if energies.size else 0.0
            if mean_energy > 0.0:
                total_energy_cv.append(float(np.std(energies) / mean_energy))
            centers = np.asarray([center_of_mass(item) for item in local], dtype=np.float64)
            if centers.size:
                drift = np.linalg.norm(centers - np.mean(centers, axis=0), axis=1)
                center_drift_max = max(center_drift_max, float(np.max(drift)))
            for a in range(len(local)):
                for b in range(a + 1, len(local)):
                    diff = local[a] - local[b]
                    repeat_mse.append(float(np.mean(diff * diff)))

    return {
        "mask_ids": unique_ids,
        "wavelength_index": unique_wavelength_index,
        "psf_mean_stack": means,
        "psf_std_stack": stds,
        "quality": {
            "mean_repeat_mse": float(np.mean(repeat_mse)) if repeat_mse else 0.0,
            "median_repeat_mse": float(np.median(repeat_mse)) if repeat_mse else 0.0,
            "mean_total_energy_cv": float(np.mean(total_energy_cv)) if total_energy_cv else 0.0,
            "max_center_drift_px": float(center_drift_max),
        },
    }


def normalize_psf_for_export(psf: np.ndarray) -> tuple[np.ndarray, dict[str, float]]:
    arr = np.asarray(psf, dtype=np.float64)
    background = float(np.percentile(arr, 5.0))
    corrected = np.maximum(arr - background, 0.0)
    psf_sum = float(np.sum(corrected))
    if psf_sum > 0.0:
        normalized = corrected / psf_sum
    else:
        normalized = corrected
    return normalized, {"background_level": background, "pre_normalization_sum": psf_sum}


def _append_frame(dset: h5py.Dataset, row: int, frame: np.ndarray) -> None:
    if frame.ndim != 2:
        raise ValueError(f"frame must be 2D, got {frame.shape}")
    if row == 0:
        dset.resize((1, frame.shape[0], frame.shape[1]))
    else:
        if dset.shape[1:] != frame.shape:
            raise ValueError(f"all frames must share shape {dset.shape[1:]}, got {frame.shape}")
        dset.resize((row + 1, frame.shape[0], frame.shape[1]))
    dset[row] = frame


def _append_lowres_mask(dset: h5py.Dataset, row: int, lowres_mask: np.ndarray) -> None:
    if lowres_mask.ndim != 3 or lowres_mask.shape[0] != 1:
        raise ValueError(f"lowres_mask must be [1,H,W], got {lowres_mask.shape}")
    if row == 0:
        dset.resize((1, lowres_mask.shape[0], lowres_mask.shape[1], lowres_mask.shape[2]))
    else:
        if dset.shape[1:] != lowres_mask.shape:
            raise ValueError(f"all lowres masks must share shape {dset.shape[1:]}, got {lowres_mask.shape}")
        dset.resize((row + 1, lowres_mask.shape[0], lowres_mask.shape[1], lowres_mask.shape[2]))
    dset[row] = lowres_mask


def _append_scalar(dset: h5py.Dataset, value: Any) -> None:
    n = dset.shape[0]
    dset.resize((n + 1,))
    dset[n] = value
