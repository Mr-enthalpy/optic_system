from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import h5py
import numpy as np

from tasks.psf_dictionary_masks import lowres_mask_to_physical_mask
from tasks.psf_phase3 import json_dumps, now_ns, processing_flags


class TargetCaptureRawWriter:
    def __init__(self, output_path: str | Path, *, plan_id: str, phase: str = "3.6"):
        self.path = Path(output_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.plan_id = str(plan_id)
        self.phase = str(phase)
        self._file: h5py.File | None = None
        self._n = 0

    def open(self) -> "TargetCaptureRawWriter":
        self._file = h5py.File(str(self.path), "w")
        string_dtype = h5py.string_dtype(encoding="utf-8")
        f = self._file
        f.attrs["plan_id"] = self.plan_id
        f.attrs["phase"] = self.phase
        f.attrs["created_at_ns"] = now_ns()
        raw = f.require_group("raw")
        raw.create_dataset("frames_avg", shape=(0, 1, 1), maxshape=(None, None, None), dtype=np.float64, chunks=(1, 128, 128), compression="gzip", compression_opts=4)
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
        raw.create_dataset("capture_role", shape=(0,), maxshape=(None,), dtype=string_dtype)
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
        provenance.create_dataset("mask_source_metadata_json", data="", dtype=string_dtype)
        f.require_group("target").create_dataset("target_metadata_json", data="", dtype=string_dtype)
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
        mask_source_metadata: dict[str, Any],
        target_metadata: dict[str, Any],
    ) -> None:
        f = self._ensure_open()
        f["capture/plan_json"][()] = json_dumps(plan)
        f["camera/metadata_json"][()] = json_dumps(camera_metadata)
        f["lcd/metadata_json"][()] = json_dumps(lcd_metadata)
        f["tls/metadata_json"][()] = json_dumps(tls_metadata)
        f["provenance/pupil_window_source_json"][()] = json_dumps(pupil_window_source)
        f["provenance/psf_roi_source_json"][()] = json_dumps(psf_roi_source)
        f["provenance/camera_params_source_json"][()] = json_dumps(camera_params_source)
        f["provenance/mask_source_metadata_json"][()] = json_dumps(mask_source_metadata)
        f["target/target_metadata_json"][()] = json_dumps(target_metadata)

    def append_capture(
        self,
        *,
        frame_avg: np.ndarray,
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
        capture_role: str,
        mask_metadata: dict[str, Any],
    ) -> int:
        f = self._ensure_open()
        row = self._n
        _append_frame(f["raw/frames_avg"], row, np.asarray(frame_avg, dtype=np.float64))
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
        _append_scalar(f["raw/capture_role"], str(capture_role))
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


def load_selected_masks_from_exports(
    h5_paths: list[str | Path],
    *,
    selected_mask_ids: list[str],
    max_masks: int | None,
    required_wavelengths_nm: list[float] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    selected = [str(x) for x in selected_mask_ids]
    selected_set = set(selected)
    found: dict[str, dict[str, Any]] = {}
    sources: dict[str, str] = {}
    export_wavelengths: list[float] | None = None
    for path in h5_paths:
        h5_path = Path(path)
        with h5py.File(str(h5_path), "r") as f:
            masks = f["masks"][()]
            mask_ids = [_decode(x) for x in f["mask_id"][()]]
            mask_families = [_decode(x) for x in f["mask_family"][()]] if "mask_family" in f else ["unknown"] * len(mask_ids)
            metadata_json = _decode(f["metadata_json"][()]) if "metadata_json" in f else ""
            wavelengths_nm = [float(x) for x in f["wavelengths_nm"][()]] if "wavelengths_nm" in f else None
            if wavelengths_nm is None and metadata_json:
                try:
                    metadata = json.loads(metadata_json)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"{h5_path}: metadata_json is not valid JSON") from exc
                meta_wls = metadata.get("wavelengths_nm")
                if isinstance(meta_wls, list):
                    wavelengths_nm = [float(x) for x in meta_wls]
                elif metadata.get("wavelength_nm") is not None:
                    wavelengths_nm = [float(metadata["wavelength_nm"])]
        if wavelengths_nm is None or not wavelengths_nm:
            raise ValueError(f"{h5_path}: LCD_forward export is missing wavelength provenance")
        rounded = [round(float(x), 6) for x in wavelengths_nm]
        if export_wavelengths is None:
            export_wavelengths = rounded
        elif export_wavelengths != rounded:
            raise ValueError(
                f"inconsistent export wavelengths across mask-source HDF5 files: "
                f"{export_wavelengths} vs {rounded} from {h5_path}"
            )
        for idx, mask_id in enumerate(mask_ids):
            if selected_set and mask_id not in selected_set:
                continue
            if mask_id in found:
                continue
            lowres = np.asarray(masks[idx], dtype=np.uint8)
            if lowres.ndim == 4:
                lowres = lowres[0]
            if lowres.ndim != 3 or lowres.shape[0] != 1:
                raise ValueError(f"{h5_path}: expected lowres mask [1,H,W], got {lowres.shape}")
            found[mask_id] = {
                "mask_id": mask_id,
                "mask_family": str(mask_families[idx]),
                "lowres_mask": lowres,
                "source_h5": str(h5_path),
                "source_metadata_json": metadata_json,
            }
            sources[mask_id] = str(h5_path)
            if max_masks is not None and len(found) >= int(max_masks):
                break
        if max_masks is not None and len(found) >= int(max_masks):
            break
    ordered = [found[mid] for mid in selected if mid in found]
    if max_masks is not None:
        ordered = ordered[: int(max_masks)]
    if not ordered:
        raise ValueError("no selected mask_ids were found in the provided LCD_forward export HDF5 files")
    missing = [mid for mid in selected if mid not in found]
    requested_wavelengths = [round(float(x), 6) for x in (required_wavelengths_nm or [])]
    available_wavelengths = export_wavelengths or []
    missing_wavelengths = [wl for wl in requested_wavelengths if wl not in available_wavelengths]
    if missing_wavelengths:
        raise ValueError(
            "target capture wavelengths are not covered by the measured PSF dictionary export: "
            f"requested={requested_wavelengths}, available={available_wavelengths}, "
            f"missing={missing_wavelengths}"
        )
    meta = {
        "mask_source_type": "lcd_forward_export",
        "source_h5_paths": [str(Path(p)) for p in h5_paths],
        "selected_mask_ids_requested": selected,
        "selected_mask_ids_found": [item["mask_id"] for item in ordered],
        "missing_mask_ids": missing,
        "available_wavelengths_nm": available_wavelengths,
        "requested_wavelengths_nm": requested_wavelengths,
        "max_masks": None if max_masks is None else int(max_masks),
        "mask_sources_by_id": sources,
    }
    return ordered, meta


def lowres_record_to_physical_mask(
    record: dict[str, Any],
    *,
    physical_shape: tuple[int, int],
    pupil_window: dict[str, Any],
    bg_code: int,
    open_code: int,
) -> tuple[np.ndarray, dict[str, Any]]:
    mask = lowres_mask_to_physical_mask(
        record["lowres_mask"],
        physical_shape=physical_shape,
        pupil_window=pupil_window,
        bg_code=bg_code,
        open_code=open_code,
    )
    meta = {
        "mask_id": record["mask_id"],
        "mask_family": record["mask_family"],
        "lowres_shape": [int(record["lowres_mask"].shape[1]), int(record["lowres_mask"].shape[2])],
        "physical_shape": [int(physical_shape[0]), int(physical_shape[1])],
        "upsampling": "nearest_block",
        "outside_effective_pupil": "opaque",
        "inside_effective_pupil": "encoded_pattern",
        "source_h5": record["source_h5"],
    }
    return mask, meta


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


def _decode(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return str(value)
