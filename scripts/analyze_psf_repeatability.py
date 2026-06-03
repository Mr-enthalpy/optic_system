#!/usr/bin/env python3
"""Analyze Phase 3.2b PSF repeatability, mask diversity, and spectral diversity."""

from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
from pathlib import Path
from typing import Any

import h5py
import matplotlib
import numpy as np

matplotlib.use("Agg")
import matplotlib.pyplot as plt

plt.rcParams.update(
    {
        "font.family": "sans-serif",
        "font.sans-serif": [
            "Microsoft YaHei",
            "SimHei",
            "Noto Sans CJK SC",
            "Arial Unicode MS",
            "DejaVu Sans",
        ],
        "axes.unicode_minus": False,
    }
)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _ensure_sys_path() -> None:
    root = str(_repo_root())
    if root not in sys.path:
        sys.path.insert(0, root)


_ensure_sys_path()

from tasks.psf_phase3 import (  # noqa: E402
    analyze_repeatability_stack,
    crop_frame,
    json_dumps,
    mse,
    normalized_correlation,
    psnr,
    ssim,
    write_preview_png,
)


RAW_VARIANT = "raw_average_crop"
NORMALIZED_VARIANT = "background_subtracted_unit_energy"


def _resolve_repo_path(path: str | Path) -> Path:
    p = Path(path)
    return p if p.is_absolute() else _repo_root() / p


def analyze_psf_repeatability(raw_h5: str | Path, output_dir: str | Path) -> dict[str, Any]:
    raw_path = _resolve_repo_path(raw_h5)
    out_dir = _resolve_repo_path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    frames, crops, mask_ids, repeat_indices, wavelength_nms, camera_profile_used, plan = _load_repeatability_raw(raw_path)

    raw_payload = _build_analysis_payload(
        crops=crops,
        mask_ids=mask_ids,
        repeat_indices=repeat_indices,
        wavelength_nms=wavelength_nms,
        camera_profile_used=camera_profile_used,
        raw_path=raw_path,
        plan=plan,
        variant_name=RAW_VARIANT,
        preprocessing={"background_subtracted": False, "unit_energy_normalized": False},
        out_dir=out_dir,
        emit_primary_outputs=True,
    )

    normalized_crops, normalized_meta = _normalize_crops_for_shape_comparison(crops)
    normalized_dir = out_dir / "normalized_unit_energy"
    normalized_payload = _build_analysis_payload(
        crops=normalized_crops,
        mask_ids=mask_ids,
        repeat_indices=repeat_indices,
        wavelength_nms=wavelength_nms,
        camera_profile_used=camera_profile_used,
        raw_path=raw_path,
        plan=plan,
        variant_name=NORMALIZED_VARIANT,
        preprocessing=normalized_meta,
        out_dir=normalized_dir,
        emit_primary_outputs=False,
    )

    companion_manifest = {
        "phase": "3.2b",
        "task": "psf_repeatability_analysis_variants",
        "source_raw_h5": _repo_relative(raw_path),
        "primary_variant": RAW_VARIANT,
        "normalized_variant": NORMALIZED_VARIANT,
        "normalized_output_dir": _repo_relative(normalized_dir),
        "normalized_files": {
            "repeatability_metrics": "repeatability_metrics_normalized.json",
            "diversity_metrics": "diversity_metrics_normalized.json",
            "spectral_diversity_metrics": "spectral_diversity_metrics_normalized.json",
        },
        "normalized_preprocessing": normalized_meta,
    }
    (out_dir / "normalized_analysis_manifest.json").write_text(json_dumps(companion_manifest), encoding="utf-8")
    (out_dir / "repeatability_metrics_normalized.json").write_text(
        json_dumps(normalized_payload["repeatability"]), encoding="utf-8"
    )
    (out_dir / "diversity_metrics_normalized.json").write_text(
        json_dumps(normalized_payload["diversity"]), encoding="utf-8"
    )
    if normalized_payload["spectral"] is not None:
        (out_dir / "spectral_diversity_metrics_normalized.json").write_text(
            json_dumps(normalized_payload["spectral"]), encoding="utf-8"
        )

    _write_combined_report(
        out_dir / "repeatability_report.md",
        raw_payload["repeatability"],
        raw_payload["diversity"],
        normalized_payload["repeatability"],
        normalized_payload["diversity"],
    )
    _write_combined_report(
        out_dir / "report.md",
        raw_payload["repeatability"],
        raw_payload["diversity"],
        normalized_payload["repeatability"],
        normalized_payload["diversity"],
    )
    return {
        "repeatability": raw_payload["repeatability"],
        "diversity": raw_payload["diversity"],
        "normalized_repeatability": normalized_payload["repeatability"],
        "normalized_diversity": normalized_payload["diversity"],
    }


def _load_repeatability_raw(
    raw_path: Path,
) -> tuple[np.ndarray, np.ndarray, list[str], list[int], np.ndarray, list[str], dict[str, Any]]:
    with h5py.File(str(raw_path), "r") as f:
        frames = f["raw/frames_avg"][()]
        if "crops" in f["raw"]:
            crops = f["raw/crops"][()]
        else:
            psf_roi = _read_json_dataset(f, "provenance/psf_roi_source_json")
            crops = np.stack([crop_frame(frame, psf_roi["roi"]) for frame in frames], axis=0)
        mask_ids = [_decode(x) for x in f["raw/mask_id"][()]]
        repeat_indices = [int(x) for x in f["raw/repeat_index"][()]]
        wavelength_nms = _read_wavelength_vector(f, mask_ids)
        camera_profile_used = [_decode(x) for x in f["raw/camera_profile_used"][()]] if "camera_profile_used" in f["raw"] else []
        plan = _read_json_dataset(f, "capture/plan_json")
    return frames, crops, mask_ids, repeat_indices, wavelength_nms, camera_profile_used, plan


def _build_analysis_payload(
    *,
    crops: np.ndarray,
    mask_ids: list[str],
    repeat_indices: list[int],
    wavelength_nms: np.ndarray,
    camera_profile_used: list[str],
    raw_path: Path,
    plan: dict[str, Any],
    variant_name: str,
    preprocessing: dict[str, Any],
    out_dir: Path,
    emit_primary_outputs: bool,
) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    grouped = _group_indices_by_wavelength(wavelength_nms)
    per_wavelength_repeatability: dict[str, Any] = {}
    per_wavelength_diversity: dict[str, Any] = {}
    per_wavelength_metrics: dict[float, dict[str, Any]] = {}

    for wavelength_nm, indices in grouped:
        wl_crops = crops[indices]
        wl_mask_ids = [mask_ids[i] for i in indices]
        metrics = analyze_repeatability_stack(wl_crops, wl_mask_ids)
        per_wavelength_metrics[wavelength_nm] = metrics
        wl_dir = out_dir / _wavelength_dir_name(wavelength_nm)
        wl_dir.mkdir(parents=True, exist_ok=True)
        _write_wavelength_outputs(wl_dir, metrics, wl_crops, wl_mask_ids, wavelength_nm=wavelength_nm)
        per_wavelength_repeatability[_wavelength_key(wavelength_nm)] = {
            "wavelength_nm": float(wavelength_nm),
            "mask_ids": metrics["mask_ids"],
            "intra_mask": metrics["intra"],
            "summary": metrics["summary"],
        }
        per_wavelength_diversity[_wavelength_key(wavelength_nm)] = {
            "wavelength_nm": float(wavelength_nm),
            "mask_ids": metrics["mask_ids"],
            "mean_inter_mask_mse": metrics["summary"]["mean_inter_mask_mse"],
            "mean_intra_mask_mse": metrics["summary"]["mean_intra_mask_mse"],
            "inter_mask_distance_over_intra_noise": metrics["summary"]["inter_mask_distance_over_intra_noise"],
            "mask_induced_differences_larger_than_repeat_noise": metrics["summary"][
                "mask_induced_differences_larger_than_repeat_noise"
            ],
            "pairwise_correlation_matrix": metrics["pairwise_correlation_matrix"].tolist(),
            "fourier_magnitude_distance_matrix": metrics["fourier_magnitude_distance_matrix"].tolist(),
        }

    aggregated_summary = _aggregate_per_wavelength_summary(per_wavelength_metrics)
    cross_wavelength_same_mask = _analyze_cross_wavelength_same_mask(crops, mask_ids, wavelength_nms, aggregated_summary)
    wavelengths_nm = [float(wl) for wl, _ in grouped]
    multi_wavelength = len(wavelengths_nm) > 1
    task_name = "psf_repeatability_multi_wavelength" if multi_wavelength else "psf_repeatability"
    summary = dict(aggregated_summary)
    if cross_wavelength_same_mask is not None:
        summary.update(cross_wavelength_same_mask["summary"])

    repeatability = {
        "schema_version": 2 if multi_wavelength else 1,
        "phase": "3.2b",
        "task": task_name,
        "analysis_variant": variant_name,
        "source_raw_h5": _repo_relative(raw_path),
        "psf_roi_source": str(plan.get("psf_roi_source")),
        "pupil_window_source": str(plan.get("pupil_window_source")),
        "coordinate_system": "camera sensor coordinates for crops; LCD physical coordinates for masks",
        "wavelengths_nm": wavelengths_nm,
        "mask_ids": _ordered_unique(mask_ids),
        "repeat_indices": repeat_indices,
        "camera_profile_used": sorted({item for item in camera_profile_used if item}),
        "preprocessing": preprocessing,
        "intra_mask": per_wavelength_metrics[wavelengths_nm[0]]["intra"] if not multi_wavelength else None,
        "per_wavelength": per_wavelength_repeatability,
        "summary": summary,
        "validity": {
            "repeatability_analyzed": True,
            "multi_wavelength": multi_wavelength,
            "scientific_calibration_valid": False,
            "training_ready": False,
        },
        "analysis": {"script": "scripts/analyze_psf_repeatability.py", "git_commit": _git_commit()},
    }
    diversity = {
        "schema_version": 2 if multi_wavelength else 1,
        "phase": "3.2b",
        "task": "mask_induced_psf_diversity",
        "analysis_variant": variant_name,
        "source_raw_h5": _repo_relative(raw_path),
        "wavelengths_nm": wavelengths_nm,
        "mask_ids": _ordered_unique(mask_ids),
        "preprocessing": preprocessing,
        "mean_inter_mask_mse": aggregated_summary["mean_inter_mask_mse"],
        "mean_intra_mask_mse": aggregated_summary["mean_intra_mask_mse"],
        "inter_mask_distance_over_intra_noise": aggregated_summary["inter_mask_distance_over_intra_noise"],
        "mask_induced_differences_larger_than_repeat_noise": aggregated_summary[
            "mask_induced_differences_larger_than_repeat_noise"
        ],
        "per_wavelength": per_wavelength_diversity,
        "cross_wavelength_same_mask": cross_wavelength_same_mask,
        "validity": {
            "diversity_analyzed": True,
            "multi_wavelength": multi_wavelength,
            "scientific_calibration_valid": False,
            "training_ready": False,
        },
    }

    if not multi_wavelength:
        first_metrics = per_wavelength_metrics[wavelengths_nm[0]]
        np.save(out_dir / "pairwise_distance_matrix.npy", first_metrics["pairwise_distance_matrix"])
        np.save(out_dir / "ssim_matrix.npy", first_metrics["ssim_matrix"])
        np.save(out_dir / "psnr_matrix.npy", first_metrics["psnr_matrix"])
        np.save(out_dir / "psfs_mean.npy", first_metrics["mask_mean_psfs"])
        np.save(out_dir / "psfs_std.npy", _std_by_mask(crops, mask_ids, first_metrics["mask_ids"]))
        _write_contact_sheet(out_dir / "mask_mean_psfs.png", first_metrics["mask_mean_psfs"])
    else:
        _write_multi_wavelength_mean_psf_figure(out_dir, per_wavelength_metrics)
        _write_multi_wavelength_manifest(out_dir / "multi_wavelength_manifest.json", per_wavelength_repeatability, diversity)

    if emit_primary_outputs:
        (out_dir / "repeatability_metrics.json").write_text(json_dumps(repeatability), encoding="utf-8")
        (out_dir / "diversity_metrics.json").write_text(json_dumps(diversity), encoding="utf-8")
        (out_dir / "psf_diversity_metrics.json").write_text(json_dumps(diversity), encoding="utf-8")
        if cross_wavelength_same_mask is not None:
            (out_dir / "spectral_diversity_metrics.json").write_text(
                json_dumps(cross_wavelength_same_mask), encoding="utf-8"
            )

    return {
        "repeatability": repeatability,
        "diversity": diversity,
        "spectral": cross_wavelength_same_mask,
    }


def _normalize_crops_for_shape_comparison(crops: np.ndarray) -> tuple[np.ndarray, dict[str, Any]]:
    arr = np.asarray(crops, dtype=np.float64)
    normalized = np.zeros_like(arr, dtype=np.float64)
    background_levels: list[float] = []
    zero_energy_count = 0
    for index, crop in enumerate(arr):
        background = float(np.percentile(crop, 5.0))
        background_levels.append(background)
        corrected = np.maximum(crop - background, 0.0)
        energy = float(np.sum(corrected))
        if energy <= 0.0:
            zero_energy_count += 1
            normalized[index] = corrected
        else:
            normalized[index] = corrected / energy
    metadata = {
        "background_subtracted": True,
        "background_method": "per_crop_p5",
        "nonnegative_clip": True,
        "unit_energy_normalized": True,
        "unit_energy_definition": "sum(max(crop - p5_background, 0))",
        "zero_energy_crop_count": int(zero_energy_count),
        "background_level_mean": float(np.mean(background_levels)) if background_levels else float("nan"),
        "background_level_std": float(np.std(background_levels)) if background_levels else float("nan"),
    }
    return normalized, metadata


def _read_wavelength_vector(f: h5py.File, mask_ids: list[str]) -> np.ndarray:
    if "wavelength_nm" in f["raw"]:
        arr = np.asarray(f["raw/wavelength_nm"][()], dtype=np.float64)
        if arr.shape[0] == len(mask_ids) and np.all(np.isfinite(arr)):
            return arr
    plan = _read_json_dataset(f, "capture/plan_json")
    if isinstance(plan.get("wavelength"), dict):
        return np.full((len(mask_ids),), float(plan["wavelength"]["wavelength_nm"]), dtype=np.float64)
    wavelengths = plan.get("wavelengths")
    if isinstance(wavelengths, list) and len(wavelengths) == 1:
        return np.full((len(mask_ids),), float(wavelengths[0]["wavelength_nm"]), dtype=np.float64)
    raise ValueError("raw/wavelength_nm is required for multi-wavelength Phase 3.2b analysis")


def _group_indices_by_wavelength(wavelength_nms: np.ndarray) -> list[tuple[float, np.ndarray]]:
    ordered: list[float] = []
    for value in np.asarray(wavelength_nms, dtype=np.float64):
        if not np.isfinite(value):
            raise ValueError("wavelength metadata contains non-finite values")
        if not any(math.isclose(float(value), item, rel_tol=0.0, abs_tol=1e-9) for item in ordered):
            ordered.append(float(value))
    return [
        (wavelength_nm, np.where(np.isclose(wavelength_nms, wavelength_nm, rtol=0.0, atol=1e-9))[0])
        for wavelength_nm in ordered
    ]


def _write_wavelength_outputs(
    out_dir: Path,
    metrics: dict[str, Any],
    crops: np.ndarray,
    mask_ids: list[str],
    *,
    wavelength_nm: float | None = None,
) -> None:
    np.save(out_dir / "pairwise_distance_matrix.npy", metrics["pairwise_distance_matrix"])
    np.save(out_dir / "ssim_matrix.npy", metrics["ssim_matrix"])
    np.save(out_dir / "psnr_matrix.npy", metrics["psnr_matrix"])
    np.save(out_dir / "psfs_mean.npy", metrics["mask_mean_psfs"])
    np.save(out_dir / "psfs_std.npy", _std_by_mask(crops, mask_ids, metrics["mask_ids"]))
    _write_contact_sheet(
        out_dir / "mask_mean_psfs.png",
        metrics["mask_mean_psfs"],
        mask_ids=metrics["mask_ids"],
        wavelength_nm=wavelength_nm,
    )
    wl_repeatability = {
        "mask_ids": metrics["mask_ids"],
        "intra_mask": metrics["intra"],
        "summary": metrics["summary"],
    }
    wl_diversity = {
        "mask_ids": metrics["mask_ids"],
        "mean_inter_mask_mse": metrics["summary"]["mean_inter_mask_mse"],
        "mean_intra_mask_mse": metrics["summary"]["mean_intra_mask_mse"],
        "inter_mask_distance_over_intra_noise": metrics["summary"]["inter_mask_distance_over_intra_noise"],
        "mask_induced_differences_larger_than_repeat_noise": metrics["summary"][
            "mask_induced_differences_larger_than_repeat_noise"
        ],
        "pairwise_correlation_matrix": metrics["pairwise_correlation_matrix"].tolist(),
        "fourier_magnitude_distance_matrix": metrics["fourier_magnitude_distance_matrix"].tolist(),
    }
    (out_dir / "repeatability_metrics.json").write_text(json_dumps(wl_repeatability), encoding="utf-8")
    (out_dir / "diversity_metrics.json").write_text(json_dumps(wl_diversity), encoding="utf-8")


def _aggregate_per_wavelength_summary(per_wavelength_metrics: dict[float, dict[str, Any]]) -> dict[str, Any]:
    summaries = [metrics["summary"] for _, metrics in sorted(per_wavelength_metrics.items())]
    intra = [_finite_number(item.get("mean_intra_mask_mse")) for item in summaries]
    inter = [_finite_number(item.get("mean_inter_mask_mse")) for item in summaries]
    intra_mean = _mean_of_finite(intra)
    inter_mean = _mean_of_finite(inter)
    return {
        "mean_intra_mask_mse": intra_mean,
        "mean_inter_mask_mse": inter_mean,
        "inter_mask_distance_over_intra_noise": float(inter_mean / intra_mean) if intra_mean > 0 else float("inf"),
        "mask_induced_differences_larger_than_repeat_noise": bool(inter_mean > intra_mean)
        if np.isfinite(intra_mean) and np.isfinite(inter_mean)
        else False,
    }


def _analyze_cross_wavelength_same_mask(
    crops: np.ndarray,
    mask_ids: list[str],
    wavelength_nms: np.ndarray,
    aggregated_summary: dict[str, Any],
) -> dict[str, Any] | None:
    ids = np.asarray(mask_ids)
    wavelengths = np.asarray(wavelength_nms, dtype=np.float64)
    unique_masks = _ordered_unique(mask_ids)
    ordered_wavelengths = [float(x) for x in _ordered_unique([str(v) for v in wavelengths])]
    global_range = float(np.max(crops) - np.min(crops))
    global_range = max(global_range, 1e-12)

    per_mask: dict[str, Any] = {}
    all_pair_mses: list[float] = []
    for mask_id in unique_masks:
        wavelength_means: list[np.ndarray] = []
        wavelength_labels: list[float] = []
        for wavelength_nm in ordered_wavelengths:
            idx = np.where((ids == mask_id) & np.isclose(wavelengths, wavelength_nm, rtol=0.0, atol=1e-9))[0]
            if idx.size:
                wavelength_means.append(np.mean(crops[idx], axis=0))
                wavelength_labels.append(float(wavelength_nm))
        if len(wavelength_means) < 2:
            continue
        means = np.stack(wavelength_means, axis=0)
        matrices = _pairwise_metrics(means, global_range)
        upper = matrices["pairwise_distance_matrix"][np.triu_indices(len(wavelength_labels), k=1)]
        pair_mse_mean = float(np.mean(upper)) if upper.size else float("nan")
        if np.isfinite(pair_mse_mean):
            all_pair_mses.append(pair_mse_mean)
        per_mask[mask_id] = {
            "wavelengths_nm": wavelength_labels,
            "mean_pairwise_mse": pair_mse_mean,
            "pairwise_distance_matrix": matrices["pairwise_distance_matrix"].tolist(),
            "pairwise_correlation_matrix": matrices["pairwise_correlation_matrix"].tolist(),
            "psnr_matrix": matrices["psnr_matrix"].tolist(),
            "ssim_matrix": matrices["ssim_matrix"].tolist(),
            "fourier_magnitude_distance_matrix": matrices["fourier_magnitude_distance_matrix"].tolist(),
        }
    if not per_mask:
        return None

    intra_reference = float(aggregated_summary["mean_intra_mask_mse"])
    cross_mean = _mean_of_finite(all_pair_mses)
    return {
        "schema_version": 1,
        "task": "same_mask_cross_wavelength_psf_diversity",
        "per_mask": per_mask,
        "summary": {
            "mean_cross_wavelength_same_mask_mse": cross_mean,
            "mean_intra_mask_mse_reference": intra_reference,
            "cross_wavelength_same_mask_over_intra_noise": float(cross_mean / intra_reference)
            if intra_reference > 0
            else float("inf"),
            "wavelength_induced_differences_larger_than_repeat_noise": bool(cross_mean > intra_reference)
            if np.isfinite(cross_mean) and np.isfinite(intra_reference)
            else False,
        },
    }


def _pairwise_metrics(images: np.ndarray, data_range: float) -> dict[str, np.ndarray]:
    arr = np.asarray(images, dtype=np.float64)
    n = int(arr.shape[0])
    distance = np.zeros((n, n), dtype=np.float64)
    corr_mat = np.eye(n, dtype=np.float64)
    psnr_mat = np.full((n, n), np.inf, dtype=np.float64)
    ssim_mat = np.eye(n, dtype=np.float64)
    fourier_distance = np.zeros((n, n), dtype=np.float64)
    for i in range(n):
        for j in range(i + 1, n):
            distance[i, j] = distance[j, i] = mse(arr[i], arr[j])
            corr_mat[i, j] = corr_mat[j, i] = normalized_correlation(arr[i], arr[j])
            psnr_mat[i, j] = psnr_mat[j, i] = psnr(arr[i], arr[j], data_range)
            ssim_mat[i, j] = ssim_mat[j, i] = ssim(arr[i], arr[j], data_range)
            fa = np.abs(np.fft.rfft2(arr[i]))
            fb = np.abs(np.fft.rfft2(arr[j]))
            fourier_distance[i, j] = fourier_distance[j, i] = float(np.mean((fa - fb) ** 2))
    return {
        "pairwise_distance_matrix": distance,
        "pairwise_correlation_matrix": corr_mat,
        "psnr_matrix": psnr_mat,
        "ssim_matrix": ssim_mat,
        "fourier_magnitude_distance_matrix": fourier_distance,
    }


def _std_by_mask(crops: np.ndarray, mask_ids: list[str], unique_ids: list[str]) -> np.ndarray:
    ids = np.asarray(mask_ids)
    return np.stack([np.std(crops[ids == mid], axis=0) for mid in unique_ids], axis=0)


def _write_contact_sheet(
    path: Path,
    means: np.ndarray,
    *,
    mask_ids: list[str] | None = None,
    wavelength_nm: float | None = None,
) -> None:
    n, h, w = means.shape
    cols = min(4, n)
    rows = int(np.ceil(n / cols))
    fig, axes = plt.subplots(rows, cols, figsize=(2.25 * cols, 2.18 * rows), squeeze=False)
    labels = mask_ids or [f"mask_{i}" for i in range(n)]
    for i, ax in enumerate(axes.ravel()):
        ax.set_xticks([])
        ax.set_yticks([])
        if i >= n:
            ax.set_axis_off()
            continue
        r, c = divmod(i, cols)
        axes[r, c].imshow(_psf_log_preview(means[i]), cmap="magma", vmin=0.0, vmax=1.0)
        axes[r, c].set_title(_short_mask_label(labels[i]), fontsize=8.5)
    if wavelength_nm is None:
        title = "不同掩膜下的平均 PSF 形态"
    else:
        title = f"不同掩膜下的平均 PSF 形态（{float(wavelength_nm):.0f} nm）"
    fig.suptitle(title, fontsize=11, fontweight="bold")
    fig.tight_layout(pad=0.9)
    _save_matplotlib_figure(fig, path)


def _write_multi_wavelength_mean_psf_figure(out_dir: Path, per_wavelength_metrics: dict[float, dict[str, Any]]) -> None:
    if not per_wavelength_metrics:
        return
    wavelengths = [float(wl) for wl in sorted(per_wavelength_metrics)]
    mask_ids = list(per_wavelength_metrics[wavelengths[0]]["mask_ids"])
    n_rows = len(wavelengths)
    n_cols = len(mask_ids)
    fig, axes = plt.subplots(
        n_rows,
        n_cols,
        figsize=(1.55 * n_cols, 1.75 * n_rows),
        squeeze=False,
    )
    for row_idx, wavelength_nm in enumerate(wavelengths):
        metrics = per_wavelength_metrics[wavelength_nm]
        wl_mask_ids = list(metrics["mask_ids"])
        means = np.asarray(metrics["mask_mean_psfs"], dtype=np.float64)
        for col_idx, mask_id in enumerate(mask_ids):
            ax = axes[row_idx, col_idx]
            ax.set_xticks([])
            ax.set_yticks([])
            if mask_id not in wl_mask_ids:
                ax.set_axis_off()
                continue
            mean_idx = wl_mask_ids.index(mask_id)
            ax.imshow(_psf_log_preview(means[mean_idx]), cmap="magma", vmin=0.0, vmax=1.0)
            if row_idx == 0:
                ax.set_title(_short_mask_label(mask_id), fontsize=7.5)
            if col_idx == 0:
                ax.set_ylabel(f"{wavelength_nm:.0f} nm", fontsize=9, rotation=0, ha="right", va="center", labelpad=26)
    fig.suptitle("不同掩膜与波长下的平均 PSF 形态", fontsize=12, fontweight="bold")
    fig.tight_layout(rect=(0.0, 0.0, 1.0, 0.94), pad=0.55)
    _save_matplotlib_figure(fig, out_dir / "multi_wavelength_mask_mean_psfs.png")


def _psf_log_preview(image: np.ndarray) -> np.ndarray:
    arr = np.asarray(image, dtype=np.float64)
    finite = arr[np.isfinite(arr)]
    if finite.size == 0:
        return np.zeros(arr.shape, dtype=np.float64)
    baseline = float(np.percentile(finite, 1.0))
    corrected = np.maximum(arr - baseline, 0.0)
    scale = float(np.percentile(corrected[np.isfinite(corrected)], 99.8))
    if scale <= 0.0:
        return np.zeros(arr.shape, dtype=np.float64)
    normalized = np.clip(corrected / scale, 0.0, 1.0)
    return np.log1p(20.0 * normalized) / math.log1p(20.0)


def _short_mask_label(mask_id: str) -> str:
    replacements = {
        "all_open_window": "全开",
        "all_closed_window": "全闭",
        "vertical_stripes_lowfreq": "竖向\n低频条纹",
        "horizontal_stripes_lowfreq": "横向\n低频条纹",
        "checkerboard_lowfreq": "低频\n棋盘",
        "central_block": "中心块",
        "edge_block": "边缘块",
        "edge_block_left": "左边缘块",
        "edge_block_right": "右边缘块",
        "edge_block_top": "上边缘块",
        "edge_block_bottom": "下边缘块",
        "random_lowfreq_1": "随机\n低频 1",
        "random_lowfreq_2": "随机\n低频 2",
    }
    if mask_id in replacements:
        return replacements[mask_id]
    text = _translate_mask_label(str(mask_id))
    if len(text) <= 18:
        return text
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        proposed = f"{current} {word}".strip()
        if len(proposed) > 14 and current:
            lines.append(current)
            current = word
        else:
            current = proposed
    if current:
        lines.append(current)
    return "\n".join(lines[:2])


def _translate_mask_label(mask_id: str) -> str:
    text = str(mask_id)
    token_map = [
        ("all_open", "全开"),
        ("all_closed", "全闭"),
        ("vertical", "竖向"),
        ("horizontal", "横向"),
        ("stripes", "条纹"),
        ("stripe", "条纹"),
        ("checkerboard", "棋盘"),
        ("central", "中心"),
        ("edge", "边缘"),
        ("left", "左"),
        ("right", "右"),
        ("top", "上"),
        ("bottom", "下"),
        ("random", "随机"),
        ("lowfreq", "低频"),
        ("midfreq", "中频"),
        ("task", "任务"),
        ("related", "相关"),
        ("mask", "掩膜"),
        ("window", "窗口"),
        ("block", "块"),
    ]
    for src, dst in token_map:
        text = text.replace(src, dst)
    text = text.replace("_", " ").strip()
    return " ".join(text.split())


def _save_matplotlib_figure(fig: plt.Figure, png_path: Path) -> None:
    png_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(png_path, dpi=200, bbox_inches="tight")
    fig.savefig(png_path.with_suffix(".pdf"), bbox_inches="tight")
    plt.close(fig)


def _write_multi_wavelength_manifest(path: Path, per_wavelength: dict[str, Any], diversity: dict[str, Any]) -> None:
    payload = {
        "phase": "3.2b",
        "task": "multi_wavelength_psf_repeatability_manifest",
        "wavelengths": per_wavelength,
        "figures": {
            "multi_wavelength_mask_mean_psfs": "multi_wavelength_mask_mean_psfs.png",
            "per_wavelength_mask_mean_psfs": {
                key: f"{_wavelength_dir_name(float(value['wavelength_nm']))}/mask_mean_psfs.png"
                for key, value in per_wavelength.items()
            },
        },
        "cross_wavelength_same_mask": diversity.get("cross_wavelength_same_mask"),
    }
    path.write_text(json_dumps(payload), encoding="utf-8")


def _write_combined_report(
    path: Path,
    raw_repeatability: dict[str, Any],
    raw_diversity: dict[str, Any],
    normalized_repeatability: dict[str, Any],
    normalized_diversity: dict[str, Any],
) -> None:
    raw_summary = raw_repeatability["summary"]
    norm_summary = normalized_repeatability["summary"]
    lines = [
        "# Phase 3.2b PSF repeatability report",
        "",
        f"- Source raw HDF5: `{raw_repeatability['source_raw_h5']}`",
        f"- Wavelengths (nm): {', '.join(str(x) for x in raw_repeatability['wavelengths_nm'])}",
        f"- Masks: {', '.join(raw_repeatability['mask_ids'])}",
        "",
        "Raw averaged crops:",
        f"- Mean intra-mask MSE: {raw_summary['mean_intra_mask_mse']:.6g}",
        f"- Mean inter-mask MSE: {raw_summary['mean_inter_mask_mse']:.6g}",
        f"- Inter / intra ratio: {raw_summary['inter_mask_distance_over_intra_noise']:.6g}",
        f"- Mask-induced differences larger than repeat noise: {raw_summary['mask_induced_differences_larger_than_repeat_noise']}",
    ]
    raw_cross = raw_diversity.get("cross_wavelength_same_mask")
    if isinstance(raw_cross, dict):
        cs = raw_cross.get("summary", {})
        lines.extend(
            [
                f"- Mean same-mask cross-wavelength MSE: {float(cs.get('mean_cross_wavelength_same_mask_mse', float('nan'))):.6g}",
                f"- Cross-wavelength / intra ratio: {float(cs.get('cross_wavelength_same_mask_over_intra_noise', float('nan'))):.6g}",
                f"- Wavelength-induced differences larger than repeat noise: {cs.get('wavelength_induced_differences_larger_than_repeat_noise')}",
            ]
        )
    lines.extend(
        [
            "",
            "Background-subtracted + unit-energy normalized crops:",
            f"- Mean intra-mask MSE: {norm_summary['mean_intra_mask_mse']:.6g}",
            f"- Mean inter-mask MSE: {norm_summary['mean_inter_mask_mse']:.6g}",
            f"- Inter / intra ratio: {norm_summary['inter_mask_distance_over_intra_noise']:.6g}",
            f"- Mask-induced differences larger than repeat noise: {norm_summary['mask_induced_differences_larger_than_repeat_noise']}",
        ]
    )
    norm_cross = normalized_diversity.get("cross_wavelength_same_mask")
    if isinstance(norm_cross, dict):
        cs = norm_cross.get("summary", {})
        lines.extend(
            [
                f"- Mean same-mask cross-wavelength MSE: {float(cs.get('mean_cross_wavelength_same_mask_mse', float('nan'))):.6g}",
                f"- Cross-wavelength / intra ratio: {float(cs.get('cross_wavelength_same_mask_over_intra_noise', float('nan'))):.6g}",
                f"- Wavelength-induced differences larger than repeat noise: {cs.get('wavelength_induced_differences_larger_than_repeat_noise')}",
            ]
        )
    lines.extend(
        [
            "",
            "Interpretation:",
            "- Raw metrics mix shape differences with residual photometric scaling.",
            "- Normalized metrics suppress global energy-scale effects and are the stricter basis for cross-wavelength shape claims.",
            "",
            "This only checks the Phase 3.2 data prerequisite. It does not validate a forward model.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _read_json_dataset(f: h5py.File, path: str) -> dict[str, Any]:
    value = f[path][()]
    text = _decode(value)
    return json.loads(text) if text else {}


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


def _ordered_unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _wavelength_key(value: float) -> str:
    return f"{float(value):.1f}"


def _wavelength_dir_name(value: float) -> str:
    return f"wl_{str(float(value)).replace('.', 'p')}"


def _finite_number(value: Any) -> float:
    number = float(value)
    return number if np.isfinite(number) else float("nan")


def _mean_of_finite(values: list[float]) -> float:
    finite = np.asarray([item for item in values if np.isfinite(item)], dtype=np.float64)
    return float(np.mean(finite)) if finite.size else float("nan")


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze Phase 3.2b PSF repeatability")
    parser.add_argument("--raw-h5", default="data/raw/bishe_psf_repeatability.h5")
    parser.add_argument("--output-dir", default="outputs/psf_repeatability")
    args = parser.parse_args()
    result = analyze_psf_repeatability(args.raw_h5, args.output_dir)
    print(json_dumps(result["diversity"]))


if __name__ == "__main__":
    main()
