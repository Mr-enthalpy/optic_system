#!/usr/bin/env python3
"""Analyze Phase 3.2b PSF repeatability and mask-induced diversity."""

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

from tasks.psf_phase3 import analyze_repeatability_stack, crop_frame, json_dumps, write_preview_png  # noqa: E402


def _resolve_repo_path(path: str | Path) -> Path:
    p = Path(path)
    return p if p.is_absolute() else _repo_root() / p


def analyze_psf_repeatability(raw_h5: str | Path, output_dir: str | Path) -> dict[str, Any]:
    raw_path = _resolve_repo_path(raw_h5)
    out_dir = _resolve_repo_path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    with h5py.File(str(raw_path), "r") as f:
        frames = f["raw/frames_avg"][()]
        if "crops" in f["raw"]:
            crops = f["raw/crops"][()]
        else:
            psf_roi = _read_json_dataset(f, "provenance/psf_roi_source_json")
            crops = np.stack([crop_frame(frame, psf_roi["roi"]) for frame in frames], axis=0)
        mask_ids = [_decode(x) for x in f["raw/mask_id"][()]]
        repeat_indices = [int(x) for x in f["raw/repeat_index"][()]]
        plan = _read_json_dataset(f, "capture/plan_json")
        psf_roi = _read_json_dataset(f, "provenance/psf_roi_source_json")
        pupil_window = _read_json_dataset(f, "provenance/pupil_window_source_json")

    metrics = analyze_repeatability_stack(crops, mask_ids)
    np.save(out_dir / "pairwise_distance_matrix.npy", metrics["pairwise_distance_matrix"])
    np.save(out_dir / "ssim_matrix.npy", metrics["ssim_matrix"])
    np.save(out_dir / "psnr_matrix.npy", metrics["psnr_matrix"])
    np.save(out_dir / "psfs_mean.npy", metrics["mask_mean_psfs"])
    np.save(out_dir / "psfs_std.npy", _std_by_mask(crops, mask_ids, metrics["mask_ids"]))
    repeatability = {
        "schema_version": 1,
        "phase": "3.2b",
        "task": "psf_repeatability",
        "source_raw_h5": _repo_relative(raw_path),
        "psf_roi_source": str(plan.get("psf_roi_source")),
        "pupil_window_source": str(plan.get("pupil_window_source")),
        "coordinate_system": "camera sensor coordinates for crops; LCD physical coordinates for masks",
        "mask_ids": metrics["mask_ids"],
        "repeat_indices": repeat_indices,
        "intra_mask": metrics["intra"],
        "summary": metrics["summary"],
        "validity": {
            "repeatability_analyzed": True,
            "scientific_calibration_valid": False,
            "training_ready": False,
        },
        "analysis": {"script": "scripts/analyze_psf_repeatability.py", "git_commit": _git_commit()},
    }
    diversity = {
        "schema_version": 1,
        "phase": "3.2b",
        "task": "mask_induced_psf_diversity",
        "source_raw_h5": _repo_relative(raw_path),
        "mask_ids": metrics["mask_ids"],
        "mean_inter_mask_mse": metrics["summary"]["mean_inter_mask_mse"],
        "mean_intra_mask_mse": metrics["summary"]["mean_intra_mask_mse"],
        "inter_mask_distance_over_intra_noise": metrics["summary"]["inter_mask_distance_over_intra_noise"],
        "mask_induced_differences_larger_than_repeat_noise": metrics["summary"]["mask_induced_differences_larger_than_repeat_noise"],
        "pairwise_correlation_matrix": metrics["pairwise_correlation_matrix"].tolist(),
        "fourier_magnitude_distance_matrix": metrics["fourier_magnitude_distance_matrix"].tolist(),
        "validity": {
            "diversity_analyzed": True,
            "scientific_calibration_valid": False,
            "training_ready": False,
        },
    }
    (out_dir / "repeatability_metrics.json").write_text(json_dumps(repeatability), encoding="utf-8")
    (out_dir / "diversity_metrics.json").write_text(json_dumps(diversity), encoding="utf-8")
    (out_dir / "psf_diversity_metrics.json").write_text(json_dumps(diversity), encoding="utf-8")
    _write_contact_sheet(out_dir / "mask_mean_psfs.png", metrics["mask_mean_psfs"], metrics["mask_ids"])
    _write_report(out_dir / "repeatability_report.md", repeatability, diversity)
    _write_report(out_dir / "report.md", repeatability, diversity)
    return {"repeatability": repeatability, "diversity": diversity}


def _std_by_mask(crops: np.ndarray, mask_ids: list[str], unique_ids: list[str]) -> np.ndarray:
    ids = np.asarray(mask_ids)
    return np.stack([np.std(crops[ids == mid], axis=0) for mid in unique_ids], axis=0)


def _write_contact_sheet(path: Path, means: np.ndarray, mask_ids: list[str]) -> None:
    n, h, w = means.shape
    cols = min(4, n)
    rows = int(np.ceil(n / cols))
    sheet = np.zeros((rows * h, cols * w), dtype=np.float64)
    for i in range(n):
        r, c = divmod(i, cols)
        sheet[r * h : (r + 1) * h, c * w : (c + 1) * w] = means[i]
    write_preview_png(path, sheet)


def _write_report(path: Path, repeatability: dict[str, Any], diversity: dict[str, Any]) -> None:
    s = repeatability["summary"]
    lines = [
        "# Phase 3.2b PSF repeatability report",
        "",
        f"- Source raw HDF5: `{repeatability['source_raw_h5']}`",
        f"- Masks: {', '.join(repeatability['mask_ids'])}",
        f"- Mean intra-mask MSE: {s['mean_intra_mask_mse']:.6g}",
        f"- Mean inter-mask MSE: {s['mean_inter_mask_mse']:.6g}",
        f"- Inter / intra ratio: {s['inter_mask_distance_over_intra_noise']:.6g}",
        f"- Mask-induced differences larger than repeat noise: {s['mask_induced_differences_larger_than_repeat_noise']}",
        "",
        "This only checks the Phase 3.2 data prerequisite. It does not validate a forward model.",
    ]
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


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze Phase 3.2b PSF repeatability")
    parser.add_argument("--raw-h5", default="data/raw/bishe_psf_repeatability.h5")
    parser.add_argument("--output-dir", default="outputs/psf_repeatability")
    args = parser.parse_args()
    result = analyze_psf_repeatability(args.raw_h5, args.output_dir)
    print(json_dumps(result["diversity"]))


if __name__ == "__main__":
    main()
