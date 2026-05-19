#!/usr/bin/env python3
"""Analyze Phase 3.3 dOTF diagnostic raw capture."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import h5py
import numpy as np


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _ensure_sys_path() -> None:
    root = str(_repo_root())
    if root not in sys.path:
        sys.path.insert(0, root)


_ensure_sys_path()

from tasks.dotf_phase3 import compute_dotf, save_grayscale_preview, save_phase_preview  # noqa: E402
from tasks.psf_phase3 import crop_frame, json_dumps, write_preview_png  # noqa: E402


def _resolve_repo_path(path: str | Path) -> Path:
    p = Path(path)
    return p if p.is_absolute() else _repo_root() / p


def analyze_dotf(raw_h5: str | Path, output_dir: str | Path) -> dict[str, Any]:
    raw_path = _resolve_repo_path(raw_h5)
    out_dir = _resolve_repo_path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    with h5py.File(str(raw_path), "r") as f:
        frames = f["raw/frames_avg"][()] if "frames_avg" in f["raw"] else None
        stored_crops = f["raw/crops"][()] if "crops" in f["raw"] else None
        wavelength_nm_ds = np.asarray(f["raw/wavelength_nm"][()], dtype=np.float64) if "raw/wavelength_nm" in f["raw"] else None
        plan = _require_json_dataset(f, "capture/plan_json")
        pupil_window = _require_json_dataset(f, "provenance/pupil_window_source_json")
        psf_roi = _require_json_dataset(f, "provenance/psf_roi_source_json")
        camera_params_source = _require_json_dataset(f, "provenance/camera_params_source_json")
        capture_roles = [_decode(x) for x in f["raw/capture_role"][()]]
        perturbation_ids = [_decode(x) for x in f["raw/perturbation_id"][()]]
        repeat_indices = [int(x) for x in f["raw/repeat_index"][()]]
        mask_ids = [_decode(x) for x in f["raw/mask_id"][()]]

    _validate_required_provenance(plan, pupil_window, psf_roi, camera_params_source)

    capture_wavelength_nm = _resolve_capture_wavelengths(
        plan=plan,
        capture_roles=capture_roles,
        wavelength_nm_ds=wavelength_nm_ds,
    )
    capture_roles_arr = np.asarray(capture_roles)
    perturbation_ids_arr = np.asarray(perturbation_ids)
    if np.where(capture_roles_arr == "reference")[0].size == 0:
        raise ValueError("raw HDF5 does not contain any reference captures")
    unique_perturbations = [pid for pid in dict.fromkeys(perturbation_ids) if pid != "none"]
    if not unique_perturbations:
        raise ValueError("raw HDF5 does not contain any perturbed captures")
    wavelength_order = _ordered_wavelengths(capture_wavelength_nm)
    multi_wavelength = len(wavelength_order) > 1

    dotf_cfg = dict(plan.get("dotf", {}))
    normalize_energy = bool(dotf_cfg.get("normalize_energy", True))
    align_before_fft = bool(dotf_cfg.get("align_before_fft", True))
    edge_energy_cfg = dict(dotf_cfg.get("edge_energy", {}))
    edge_energy_enabled = bool(edge_energy_cfg.get("enabled", False))
    edge_band_px = int(edge_energy_cfg.get("edge_band_px", 10))

    requested_roi_keys = _resolve_requested_roi_keys(plan, psf_roi)
    baseline_roi_key = _resolve_baseline_roi_key(psf_roi, requested_roi_keys)
    full_frame_available = frames is not None
    manifest_wavelengths: dict[str, Any] = {}
    per_wavelength_summary: dict[str, Any] = {}
    legacy_baseline_summary: dict[str, Any] | None = None
    legacy_baseline_wavelength_nm: float | None = None

    for wavelength_nm in wavelength_order:
        wl_key = _format_wavelength_key(wavelength_nm)
        wl_mask = np.asarray(
            [_format_wavelength_key(value) == wl_key for value in capture_wavelength_nm],
            dtype=bool,
        )
        wl_indices = np.where(wl_mask)[0]
        wl_capture_roles = capture_roles_arr[wl_indices]
        wl_perturbation_ids = perturbation_ids_arr[wl_indices]
        wl_reference_idx = np.where(wl_capture_roles == "reference")[0]
        if wl_reference_idx.size == 0:
            raise ValueError(f"raw HDF5 does not contain any reference captures for wavelength {wl_key}")
        wavelength_dir = out_dir if not multi_wavelength else out_dir / _wavelength_dir_name(wavelength_nm)
        wavelength_dir.mkdir(parents=True, exist_ok=True)
        manifest_rois: dict[str, Any] = {}
        wavelength_baseline_summary: dict[str, Any] | None = None
        for roi_key in requested_roi_keys:
            roi_record = _resolve_roi_record(psf_roi, roi_key)
            if not bool(roi_record.get("fits_frame", True)):
                manifest_rois[roi_key] = {
                    "roi": roi_record,
                    "analyzed": False,
                    "skip_reason": roi_record.get("skip_reason", "ROI exceeds frame boundary"),
                }
                continue
            crops, crop_source = _roi_crops(
                frames=frames[wl_indices] if frames is not None else None,
                stored_crops=stored_crops[wl_indices] if stored_crops is not None else None,
                roi_record=roi_record,
                roi_key=roi_key,
                baseline_roi_key=baseline_roi_key,
            )
            if crops is None:
                manifest_rois[roi_key] = {
                    "roi": roi_record,
                    "analyzed": False,
                    "skip_reason": (
                        "Multi-ROI analysis requires full-frame raw frames or a stored baseline crop "
                        "with matching shape"
                    ),
                }
                continue
            roi_dir = wavelength_dir / roi_key
            roi_dir.mkdir(parents=True, exist_ok=True)
            roi_summary = _analyze_single_roi(
                roi_key=roi_key,
                roi_record=roi_record,
                crops=crops,
                capture_roles_arr=wl_capture_roles,
                perturbation_ids_arr=wl_perturbation_ids,
                reference_idx=wl_reference_idx,
                unique_perturbations=unique_perturbations,
                normalize_energy=normalize_energy,
                align_before_fft=align_before_fft,
                edge_energy_enabled=edge_energy_enabled,
                edge_band_px=edge_band_px,
                output_dir=roi_dir,
                legacy_output_dir=(out_dir if (not multi_wavelength and roi_key == baseline_roi_key) else None),
                source_raw_h5=_repo_relative(raw_path),
                plan=plan,
                psf_roi=psf_roi,
                mask_ids=[mask_ids[idx] for idx in wl_indices],
                repeat_indices=[repeat_indices[idx] for idx in wl_indices],
            )
            manifest_rois[roi_key] = {
                "roi": roi_record,
                "analyzed": True,
                "crop_source": crop_source,
                "perturbations": roi_summary["perturbations"],
                "output_dir": _repo_relative(roi_dir),
            }
            if roi_key == baseline_roi_key:
                wavelength_baseline_summary = roi_summary
        if wavelength_baseline_summary is None:
            raise ValueError(f"baseline ROI key {baseline_roi_key!r} was not analyzable for wavelength {wl_key}")
        manifest_wavelengths[wl_key] = {
            "wavelength_nm": float(wavelength_nm),
            "requested_roi_keys": requested_roi_keys,
            "current_baseline_roi_key": baseline_roi_key,
            "rois": manifest_rois,
            "output_dir": _repo_relative(wavelength_dir),
        }
        per_wavelength_summary[wl_key] = {
            "wavelength_nm": float(wavelength_nm),
            "roi_key": baseline_roi_key,
            "roi": wavelength_baseline_summary["roi"],
            "perturbations": wavelength_baseline_summary["perturbations"],
            "mask_ids": [mask_ids[idx] for idx in wl_indices],
            "repeat_indices": [repeat_indices[idx] for idx in wl_indices],
            "output_dir": _repo_relative(wavelength_dir),
        }
        if not multi_wavelength and legacy_baseline_summary is None:
            legacy_baseline_summary = wavelength_baseline_summary
            legacy_baseline_wavelength_nm = float(wavelength_nm)

    manifest = {
        "schema_version": 2,
        "phase": "3.3",
        "task": "dotf_multi_wavelength_multi_roi_diagnostic_visualization" if multi_wavelength else "dotf_multi_roi_diagnostic_visualization",
        "source_raw_h5": _repo_relative(raw_path),
        "pupil_window_source": str(plan.get("pupil_window_source")),
        "psf_roi_source": str(plan.get("psf_roi_source")),
        "camera_params_source": str(plan.get("camera_params_source")),
        "wavelengths_nm": [float(item) for item in wavelength_order],
        "current_baseline_roi_key": baseline_roi_key,
        "final_selected_roi_key": None,
        "selection_policy": "manual_after_dotf_visual_inspection",
        "full_frame_available": bool(full_frame_available),
        "requested_roi_keys": requested_roi_keys,
        "wavelengths": manifest_wavelengths,
        "analysis": {
            "script": "scripts/analyze_dotf.py",
            "git_commit": _git_commit(),
            "normalize_energy": normalize_energy,
            "align_before_fft": align_before_fft,
            "window_before_fft": bool(dotf_cfg.get("window_before_fft", False)),
            "edge_energy_enabled": edge_energy_enabled,
            "edge_band_px": edge_band_px,
        },
        "validity": {
            "dotf_computed": True,
            "pupil_stitching_performed": False,
            "roi_selection_performed": False,
            "scientific_calibration_valid": False,
            "training_ready": False,
        },
    }
    if not multi_wavelength:
        single_wl_key = _format_wavelength_key(wavelength_order[0])
        manifest["wavelength_nm"] = float(wavelength_order[0])
        manifest["rois"] = manifest_wavelengths[single_wl_key]["rois"]
    (out_dir / "dotf_roi_comparison_manifest.json").write_text(json_dumps(manifest), encoding="utf-8")
    _write_comparison_report(out_dir / "dotf_roi_comparison_report.md", manifest=manifest)

    if multi_wavelength:
        baseline_metrics = {
            "schema_version": 3,
            "phase": "3.3",
            "task": "dotf_multi_wavelength_diagnostic_visualization",
            "source_raw_h5": _repo_relative(raw_path),
            "pupil_window_source": str(plan.get("pupil_window_source")),
            "psf_roi_source": str(plan.get("psf_roi_source")),
            "camera_params_source": str(plan.get("camera_params_source")),
            "wavelengths_nm": [float(item) for item in wavelength_order],
            "roi_key": baseline_roi_key,
            "per_wavelength": per_wavelength_summary,
            "validity": {
                "dotf_computed": True,
                "pupil_stitching_performed": False,
                "roi_selection_performed": False,
                "scientific_calibration_valid": False,
                "training_ready": False,
            },
            "analysis": {
                "script": "scripts/analyze_dotf.py",
                "git_commit": _git_commit(),
                "normalize_energy": normalize_energy,
                "align_before_fft": align_before_fft,
                "window_before_fft": bool(dotf_cfg.get("window_before_fft", False)),
                "multi_roi_manifest": "outputs/dotf/dotf_roi_comparison_manifest.json",
            },
        }
    else:
        if legacy_baseline_summary is None or legacy_baseline_wavelength_nm is None:
            raise ValueError("single-wavelength baseline summary was not produced")
        baseline_metrics = {
            "schema_version": 2,
            "phase": "3.3",
            "task": "dotf_diagnostic_visualization",
            "source_raw_h5": _repo_relative(raw_path),
            "pupil_window_source": str(plan.get("pupil_window_source")),
            "psf_roi_source": str(plan.get("psf_roi_source")),
            "camera_params_source": str(plan.get("camera_params_source")),
            "wavelength_nm": float(legacy_baseline_wavelength_nm),
            "mask_ids": per_wavelength_summary[_format_wavelength_key(legacy_baseline_wavelength_nm)]["mask_ids"],
            "repeat_indices": per_wavelength_summary[_format_wavelength_key(legacy_baseline_wavelength_nm)]["repeat_indices"],
            "roi_key": baseline_roi_key,
            "roi": legacy_baseline_summary["roi"],
            "perturbations": legacy_baseline_summary["perturbations"],
            "validity": {
                "dotf_computed": True,
                "pupil_stitching_performed": False,
                "roi_selection_performed": False,
                "scientific_calibration_valid": False,
                "training_ready": False,
            },
            "analysis": {
                "script": "scripts/analyze_dotf.py",
                "git_commit": _git_commit(),
                "normalize_energy": normalize_energy,
                "align_before_fft": align_before_fft,
                "window_before_fft": bool(dotf_cfg.get("window_before_fft", False)),
                "multi_roi_manifest": "outputs/dotf/dotf_roi_comparison_manifest.json",
            },
        }
    (out_dir / "dotf_metrics.json").write_text(json_dumps(baseline_metrics), encoding="utf-8")
    _write_report(
        out_dir / "dotf_report.md",
        metrics=baseline_metrics,
        pupil_window=pupil_window,
        psf_roi=psf_roi,
        camera_params_source=camera_params_source,
    )
    return baseline_metrics


def _analyze_single_roi(
    *,
    roi_key: str,
    roi_record: dict[str, Any],
    crops: np.ndarray,
    capture_roles_arr: np.ndarray,
    perturbation_ids_arr: np.ndarray,
    reference_idx: np.ndarray,
    unique_perturbations: list[str],
    normalize_energy: bool,
    align_before_fft: bool,
    edge_energy_enabled: bool,
    edge_band_px: int,
    output_dir: Path,
    legacy_output_dir: Path | None,
    source_raw_h5: str,
    plan: dict[str, Any],
    psf_roi: dict[str, Any],
    mask_ids: list[str],
    repeat_indices: list[int],
) -> dict[str, Any]:
    reference_mean = np.mean(np.asarray(crops)[reference_idx], axis=0)
    np.save(output_dir / "psf_reference.npy", reference_mean)
    write_preview_png(output_dir / "psf_reference.png", reference_mean)
    if legacy_output_dir is not None:
        np.save(legacy_output_dir / "psf_reference.npy", reference_mean)
        write_preview_png(legacy_output_dir / "psf_reference.png", reference_mean)

    perturbation_metrics: dict[str, Any] = {}
    for perturbation_id in unique_perturbations:
        pert_idx = np.where((capture_roles_arr == "perturbed") & (perturbation_ids_arr == perturbation_id))[0]
        if pert_idx.size == 0:
            raise ValueError(f"no perturbed captures found for {perturbation_id}")
        pert_mean = np.mean(np.asarray(crops)[pert_idx], axis=0)
        result = compute_dotf(
            reference_mean,
            pert_mean,
            normalize_energy=normalize_energy,
            align_before_fft=align_before_fft,
        )
        dotf = result["dotf"]
        psf_diff = result["psf_perturbed"] - result["psf_reference"]
        edge_energy = _edge_energy_metrics(
            result["psf_reference"],
            result["psf_perturbed"],
            psf_diff,
            edge_band_px=edge_band_px,
            enabled=edge_energy_enabled,
        )

        local_dir = output_dir / perturbation_id
        local_dir.mkdir(parents=True, exist_ok=True)
        _write_perturbation_outputs(local_dir, result=result, psf_diff=psf_diff)
        if legacy_output_dir is not None:
            legacy_dir = legacy_output_dir / perturbation_id
            legacy_dir.mkdir(parents=True, exist_ok=True)
            _write_perturbation_outputs(legacy_dir, result=result, psf_diff=psf_diff)

        ref_count = int(reference_idx.size)
        pert_count = int(pert_idx.size)
        diff_l2 = float(np.linalg.norm(psf_diff))
        ref_l2 = float(np.linalg.norm(result["psf_reference"]))
        perturbation_metric = {
            "schema_version": 1,
            "phase": "3.3",
            "task": "dotf_diagnostic_visualization",
            "source_raw_h5": source_raw_h5,
            "roi_key": roi_key,
            "roi": roi_record,
            "perturbation_id": perturbation_id,
            "reference_repeats": ref_count,
            "perturbed_repeats": pert_count,
            "psf_difference_l2": diff_l2,
            "psf_difference_relative_l2": float(diff_l2 / ref_l2) if ref_l2 > 0.0 else 0.0,
            "dotf_energy": float(np.sum(np.abs(dotf) ** 2)),
            "dotf_peak_abs": float(np.max(np.abs(dotf))),
            "alignment_shift": result["alignment_shift"],
            "edge_energy": edge_energy,
            "validity": {
                "dotf_computed": True,
                "pupil_stitching_performed": False,
                "roi_selection_performed": False,
            },
            "analysis": {
                "script": "scripts/analyze_dotf.py",
                "git_commit": _git_commit(),
                "normalize_energy": normalize_energy,
                "align_before_fft": align_before_fft,
            },
            "output_dir": _repo_relative(local_dir),
        }
        (local_dir / "dotf_metrics.json").write_text(json_dumps(perturbation_metric), encoding="utf-8")
        perturbation_metrics[perturbation_id] = {
            "reference_repeats": ref_count,
            "perturbed_repeats": pert_count,
            "psf_difference_l2": diff_l2,
            "psf_difference_relative_l2": perturbation_metric["psf_difference_relative_l2"],
            "dotf_energy": perturbation_metric["dotf_energy"],
            "dotf_peak_abs": perturbation_metric["dotf_peak_abs"],
            "alignment_shift": result["alignment_shift"],
            "edge_energy": edge_energy,
            "output_dir": _repo_relative(local_dir),
        }

    return {"roi": roi_record, "perturbations": perturbation_metrics}


def _write_perturbation_outputs(path: Path, *, result: dict[str, Any], psf_diff: np.ndarray) -> None:
    np.save(path / "psf_perturbed.npy", result["psf_perturbed"])
    np.save(path / "otf_reference.npy", result["otf_reference"])
    np.save(path / "otf_perturbed.npy", result["otf_perturbed"])
    np.save(path / "dotf_complex.npy", result["dotf"])
    write_preview_png(path / "psf_reference.png", result["psf_reference"])
    write_preview_png(path / "psf_perturbed.png", result["psf_perturbed"])
    save_grayscale_preview(path / "psf_difference.png", psf_diff, mode="signed")
    save_grayscale_preview(path / "dotf_abs.png", np.abs(result["dotf"]), mode="linear")
    save_grayscale_preview(path / "dotf_log_abs.png", np.abs(result["dotf"]), mode="log")
    save_phase_preview(path / "dotf_phase.png", result["dotf"])
    save_grayscale_preview(path / "dotf_real.png", np.real(result["dotf"]), mode="signed")
    save_grayscale_preview(path / "dotf_imag.png", np.imag(result["dotf"]), mode="signed")


def _edge_energy_metrics(
    reference_crop: np.ndarray,
    perturbed_crop: np.ndarray,
    difference_crop: np.ndarray,
    *,
    edge_band_px: int,
    enabled: bool,
) -> dict[str, Any]:
    if not enabled:
        return {
            "edge_band_px": int(edge_band_px),
            "reference_edge_energy_fraction": 0.0,
            "perturbed_edge_energy_fraction": 0.0,
            "difference_edge_fraction": 0.0,
        }
    edge_mask = _edge_band_mask(reference_crop.shape, edge_band_px=edge_band_px)
    return {
        "edge_band_px": int(edge_band_px),
        "reference_edge_energy_fraction": _fraction_in_mask(reference_crop, edge_mask),
        "perturbed_edge_energy_fraction": _fraction_in_mask(perturbed_crop, edge_mask),
        "difference_edge_fraction": _fraction_in_mask(difference_crop, edge_mask),
    }


def _edge_band_mask(shape: tuple[int, int], *, edge_band_px: int) -> np.ndarray:
    h, w = int(shape[0]), int(shape[1])
    band = max(1, int(edge_band_px))
    mask = np.zeros((h, w), dtype=bool)
    mask[:band, :] = True
    mask[-band:, :] = True
    mask[:, :band] = True
    mask[:, -band:] = True
    return mask


def _fraction_in_mask(image: np.ndarray, mask: np.ndarray) -> float:
    arr = np.abs(np.asarray(image, dtype=np.float64))
    denom = float(np.sum(arr))
    if denom <= 0.0:
        return 0.0
    return float(np.sum(arr[mask]) / denom)


def _roi_crops(
    *,
    frames: np.ndarray | None,
    stored_crops: np.ndarray | None,
    roi_record: dict[str, Any],
    roi_key: str,
    baseline_roi_key: str,
) -> tuple[np.ndarray | None, str]:
    if frames is not None:
        return np.stack([crop_frame(frame, roi_record) for frame in np.asarray(frames)], axis=0), "recomputed_from_full_frame"
    if stored_crops is not None and roi_key == baseline_roi_key:
        crop_shape = tuple(int(v) for v in np.asarray(stored_crops).shape[1:])
        roi_shape = (int(roi_record["height"]), int(roi_record["width"]))
        if crop_shape == roi_shape:
            return np.asarray(stored_crops), "stored_baseline_crop"
    return None, "unavailable"


def _resolve_requested_roi_keys(plan: dict[str, Any], psf_roi: dict[str, Any]) -> list[str]:
    roi_keys = plan.get("dotf", {}).get("roi_keys")
    if isinstance(roi_keys, list) and roi_keys:
        return [str(item) for item in roi_keys]
    rois = psf_roi.get("rois")
    if isinstance(rois, dict) and rois:
        return [str(key) for key, value in rois.items() if bool(value.get("fits_frame", False))]
    baseline_key = psf_roi.get("current_baseline_roi_key") or psf_roi.get("default_roi_key") or "roi"
    return [str(baseline_key)]


def _resolve_capture_wavelengths(
    *,
    plan: dict[str, Any],
    capture_roles: list[str],
    wavelength_nm_ds: np.ndarray | None,
) -> np.ndarray:
    if wavelength_nm_ds is not None:
        if len(wavelength_nm_ds) != len(capture_roles):
            raise ValueError("raw/wavelength_nm length does not match capture rows")
        return np.asarray(wavelength_nm_ds, dtype=np.float64)
    wavelength_entries = plan.get("wavelengths")
    if isinstance(wavelength_entries, list) and wavelength_entries:
        values = [float(item["wavelength_nm"]) for item in wavelength_entries]
        block = len(capture_roles) // len(values) if values else 0
        if block * len(values) != len(capture_roles):
            raise ValueError("raw capture rows do not match plan.wavelengths and no raw/wavelength_nm dataset is present")
        return np.asarray([values[idx // block] for idx in range(len(capture_roles))], dtype=np.float64)
    wavelength = plan.get("wavelength", {})
    wavelength_nm = float(wavelength.get("wavelength_nm", float("nan")))
    return np.full((len(capture_roles),), wavelength_nm, dtype=np.float64)


def _ordered_wavelengths(wavelength_nm: np.ndarray) -> list[float]:
    ordered: list[float] = []
    seen: set[str] = set()
    for value in np.asarray(wavelength_nm, dtype=np.float64).tolist():
        key = _format_wavelength_key(value)
        if key not in seen:
            seen.add(key)
            ordered.append(float(value))
    return ordered


def _format_wavelength_key(wavelength_nm: float) -> str:
    return format(float(wavelength_nm), ".1f")


def _wavelength_dir_name(wavelength_nm: float) -> str:
    return f"wl_{_format_wavelength_key(wavelength_nm).replace('.', 'p')}"


def _resolve_baseline_roi_key(psf_roi: dict[str, Any], requested_roi_keys: list[str]) -> str:
    baseline = psf_roi.get("current_baseline_roi_key") or psf_roi.get("default_roi_key")
    if isinstance(baseline, str) and baseline:
        return baseline
    return requested_roi_keys[0]


def _resolve_roi_record(psf_roi: dict[str, Any], roi_key: str) -> dict[str, Any]:
    rois = psf_roi.get("rois")
    if isinstance(rois, dict) and roi_key in rois:
        return dict(rois[roi_key])
    if roi_key in {psf_roi.get("current_baseline_roi_key"), psf_roi.get("default_roi_key"), "roi"}:
        roi = dict(psf_roi.get("roi") or {})
        roi["fits_frame"] = True
        return roi
    raise ValueError(f"requested ROI key not found in psf_roi provenance: {roi_key}")


def _write_report(
    path: Path,
    *,
    metrics: dict[str, Any],
    pupil_window: dict[str, Any],
    psf_roi: dict[str, Any],
    camera_params_source: dict[str, Any],
) -> None:
    if "per_wavelength" in metrics:
        lines = [
            "# Phase 3.3 dOTF diagnostic report",
            "",
            f"- Source raw HDF5: `{metrics['source_raw_h5']}`",
            f"- Wavelengths (nm): {metrics['wavelengths_nm']}",
            f"- Pupil window source: `{metrics['pupil_window_source']}`",
            f"- PSF ROI source: `{metrics['psf_roi_source']}`",
            f"- Camera params source: `{metrics['camera_params_source']}`",
            f"- Baseline ROI key: `{metrics['roi_key']}`",
            "- pupil_stitching_performed=false",
            "- roi_selection_performed=false",
            "",
            "This Phase 3.3 run compares dOTF diagnostics across multiple wavelengths.",
            "Per-wavelength outputs live under `outputs/dotf/wl_*`.",
            "",
            f"- Effective pupil physical shape: {pupil_window.get('physical_shape')}",
            f"- Baseline PSF ROI: {psf_roi.get('roi')}",
            f"- Camera parameter validity: {camera_params_source.get('validity', {}).get('psf_exposure_safe')}",
            "",
            "Per-wavelength baseline summaries:",
        ]
        for wl_key, entry in metrics["per_wavelength"].items():
            lines.append(f"- `{wl_key} nm` -> `{entry['output_dir']}`")
            for perturbation_id, item in entry["perturbations"].items():
                lines.append(
                    f"  - `{perturbation_id}`: psf_difference_relative_l2={item['psf_difference_relative_l2']:.6g}, "
                    f"dotf_peak_abs={item['dotf_peak_abs']:.6g}, "
                    f"difference_edge_fraction={item['edge_energy']['difference_edge_fraction']:.6g}"
                )
    else:
        lines = [
            "# Phase 3.3 dOTF diagnostic report",
            "",
            f"- Source raw HDF5: `{metrics['source_raw_h5']}`",
            f"- Wavelength (nm): {metrics['wavelength_nm']}",
            f"- Pupil window source: `{metrics['pupil_window_source']}`",
            f"- PSF ROI source: `{metrics['psf_roi_source']}`",
            f"- Camera params source: `{metrics['camera_params_source']}`",
            f"- Baseline ROI key: `{metrics['roi_key']}`",
            "- pupil_stitching_performed=false",
            "- roi_selection_performed=false",
            "",
            "dOTF is used here as a diagnostic visualization of structured pupil-domain response.",
            "The result is not stitched into a full complex pupil.",
            "The result is not a final pupil reconstruction.",
            "",
            f"- Effective pupil physical shape: {pupil_window.get('physical_shape')}",
            f"- Baseline PSF ROI: {psf_roi.get('roi')}",
            f"- Camera parameter validity: {camera_params_source.get('validity', {}).get('psf_exposure_safe')}",
            "",
            "Per-perturbation baseline summaries:",
        ]
        for perturbation_id, item in metrics["perturbations"].items():
            lines.append(
                f"- `{perturbation_id}`: reference_repeats={item['reference_repeats']}, "
                f"perturbed_repeats={item['perturbed_repeats']}, "
                f"psf_difference_relative_l2={item['psf_difference_relative_l2']:.6g}, "
                f"dotf_peak_abs={item['dotf_peak_abs']:.6g}, "
                f"difference_edge_fraction={item['edge_energy']['difference_edge_fraction']:.6g}"
            )
    lines.extend(
        [
            "",
            "Multi-ROI comparison results are recorded separately in `outputs/dotf/dotf_roi_comparison_manifest.json`.",
            "The final Phase 3.4 ROI remains a manual choice after visual inspection.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_comparison_report(path: Path, *, manifest: dict[str, Any]) -> None:
    lines = [
        "# Phase 3.3 multi-ROI dOTF comparison",
        "",
        f"- Source raw HDF5: `{manifest['source_raw_h5']}`",
        f"- Baseline ROI key: `{manifest['current_baseline_roi_key']}`",
        f"- Final selected ROI key: `{manifest['final_selected_roi_key']}`",
        f"- Full-frame raw available: {manifest['full_frame_available']}",
        f"- Requested ROI keys: {manifest['requested_roi_keys']}",
        f"- Wavelengths (nm): {manifest['wavelengths_nm']}",
        "",
        "This report compares dOTF behavior under multiple centered PSF support windows.",
        "No automatic ROI selection is performed here.",
        "",
        "Per-wavelength / per-ROI status:",
    ]
    for wl_key, wl_entry in manifest["wavelengths"].items():
        lines.append(f"- `{wl_key} nm` -> `{wl_entry['output_dir']}`")
        for roi_key, item in wl_entry["rois"].items():
            if item["analyzed"]:
                lines.append(
                    f"  - `{roi_key}`: analyzed, crop_source={item['crop_source']}, output_dir=`{item['output_dir']}`"
                )
                for perturbation_id, metric in item["perturbations"].items():
                    lines.append(
                        f"    - `{perturbation_id}`: dotf_peak_abs={metric['dotf_peak_abs']:.6g}, "
                        f"difference_edge_fraction={metric['edge_energy']['difference_edge_fraction']:.6g}"
                    )
            else:
                lines.append(f"  - `{roi_key}`: skipped, reason={item['skip_reason']}")
    lines.extend(
        [
            "",
            "roi_256 remains the frozen baseline until a manual modelling ROI is selected.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _validate_required_provenance(
    plan: dict[str, Any],
    pupil_window: dict[str, Any],
    psf_roi: dict[str, Any],
    camera_params_source: dict[str, Any],
) -> None:
    if plan.get("phase") != "3.3":
        raise ValueError("capture/plan_json must describe a Phase 3.3 dOTF plan")
    if pupil_window.get("phase") != "3.1" or "center" not in pupil_window or "radius" not in pupil_window:
        raise ValueError("provenance/pupil_window_source_json must contain a Phase 3.1 effective pupil window")
    roi = psf_roi.get("roi")
    if psf_roi.get("phase") != "3.2a" or not isinstance(roi, dict):
        raise ValueError("provenance/psf_roi_source_json must contain a Phase 3.2a PSF ROI")
    for key in ("x_min", "x_max", "y_min", "y_max", "width", "height"):
        if roi.get(key) is None:
            raise ValueError(f"provenance/psf_roi_source_json is missing roi.{key}")
    if not isinstance(camera_params_source, dict) or not camera_params_source:
        raise ValueError("provenance/camera_params_source_json must not be empty")


def _require_json_dataset(f: h5py.File, path: str) -> dict[str, Any]:
    value = f[path][()]
    text = _decode(value)
    if not text:
        raise ValueError(f"required provenance dataset is empty: {path}")
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError(f"required provenance dataset must decode to a JSON object: {path}")
    return data


def _decode(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return str(value)


def _repo_relative(path: Path) -> str:
    try:
        return str(path.relative_to(_repo_root())).replace("\\", "/")
    except ValueError:
        return str(path)


def _git_commit() -> str | None:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=_repo_root(), text=True).strip()
    except Exception:
        return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze Phase 3.3 dOTF diagnostic capture")
    parser.add_argument("--raw-h5", default="data/raw/bishe_dotf_diagnostic.h5")
    parser.add_argument("--output-dir", default="outputs/dotf")
    args = parser.parse_args()
    result = analyze_dotf(args.raw_h5, args.output_dir)
    print(
        json_dumps(
            {
                "validity": result["validity"],
                "roi_key": result["roi_key"],
                "wavelengths_nm": result.get("wavelengths_nm"),
            }
        )
    )


if __name__ == "__main__":
    main()
