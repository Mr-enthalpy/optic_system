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

from tasks.dotf_phase3 import compute_dotf, recompute_crops_from_frames, save_grayscale_preview, save_phase_preview  # noqa: E402
from tasks.psf_phase3 import json_dumps, write_preview_png  # noqa: E402


def _resolve_repo_path(path: str | Path) -> Path:
    p = Path(path)
    return p if p.is_absolute() else _repo_root() / p


def analyze_dotf(raw_h5: str | Path, output_dir: str | Path) -> dict[str, Any]:
    raw_path = _resolve_repo_path(raw_h5)
    out_dir = _resolve_repo_path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    with h5py.File(str(raw_path), "r") as f:
        frames = f["raw/frames_avg"][()]
        if "crops" in f["raw"]:
            crops = f["raw/crops"][()]
        else:
            psf_roi = _require_json_dataset(f, "provenance/psf_roi_source_json")
            crops = recompute_crops_from_frames(frames, psf_roi)
        plan = _require_json_dataset(f, "capture/plan_json")
        pupil_window = _require_json_dataset(f, "provenance/pupil_window_source_json")
        psf_roi = _require_json_dataset(f, "provenance/psf_roi_source_json")
        camera_params_source = _require_json_dataset(f, "provenance/camera_params_source_json")
        capture_roles = [_decode(x) for x in f["raw/capture_role"][()]]
        perturbation_ids = [_decode(x) for x in f["raw/perturbation_id"][()]]
        repeat_indices = [int(x) for x in f["raw/repeat_index"][()]]
        mask_ids = [_decode(x) for x in f["raw/mask_id"][()]]

    _validate_required_provenance(plan, pupil_window, psf_roi, camera_params_source)

    capture_roles_arr = np.asarray(capture_roles)
    perturbation_ids_arr = np.asarray(perturbation_ids)
    reference_idx = np.where(capture_roles_arr == "reference")[0]
    if reference_idx.size == 0:
        raise ValueError("raw HDF5 does not contain any reference captures")
    reference_mean = np.mean(np.asarray(crops)[reference_idx], axis=0)
    np.save(out_dir / "psf_reference.npy", reference_mean)
    write_preview_png(out_dir / "psf_reference.png", reference_mean)

    unique_perturbations = [pid for pid in dict.fromkeys(perturbation_ids) if pid != "none"]
    if not unique_perturbations:
        raise ValueError("raw HDF5 does not contain any perturbed captures")

    normalize_energy = bool(plan.get("dotf", {}).get("normalize_energy", True))
    align_before_fft = bool(plan.get("dotf", {}).get("align_before_fft", True))
    perturbation_metrics: dict[str, Any] = {}

    for perturbation_id in unique_perturbations:
        local_dir = out_dir / perturbation_id
        local_dir.mkdir(parents=True, exist_ok=True)
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

        np.save(local_dir / "psf_perturbed.npy", result["psf_perturbed"])
        np.save(local_dir / "otf_reference.npy", result["otf_reference"])
        np.save(local_dir / "otf_perturbed.npy", result["otf_perturbed"])
        np.save(local_dir / "dotf_complex.npy", dotf)

        write_preview_png(local_dir / "psf_perturbed.png", result["psf_perturbed"])
        save_grayscale_preview(local_dir / "psf_difference.png", psf_diff, mode="signed")
        save_grayscale_preview(local_dir / "dotf_abs.png", np.abs(dotf), mode="linear")
        save_grayscale_preview(local_dir / "dotf_log_abs.png", np.abs(dotf), mode="log")
        save_phase_preview(local_dir / "dotf_phase.png", dotf)
        save_grayscale_preview(local_dir / "dotf_real.png", np.real(dotf), mode="signed")
        save_grayscale_preview(local_dir / "dotf_imag.png", np.imag(dotf), mode="signed")

        ref_count = int(reference_idx.size)
        pert_count = int(pert_idx.size)
        diff_l2 = float(np.linalg.norm(psf_diff))
        ref_l2 = float(np.linalg.norm(result["psf_reference"]))
        perturbation_metrics[perturbation_id] = {
            "reference_repeats": ref_count,
            "perturbed_repeats": pert_count,
            "psf_difference_l2": diff_l2,
            "psf_difference_relative_l2": float(diff_l2 / ref_l2) if ref_l2 > 0.0 else 0.0,
            "dotf_energy": float(np.sum(np.abs(dotf) ** 2)),
            "dotf_peak_abs": float(np.max(np.abs(dotf))),
            "alignment_shift": result["alignment_shift"],
            "output_dir": _repo_relative(local_dir),
        }

    metrics = {
        "schema_version": 1,
        "phase": "3.3",
        "task": "dotf_diagnostic_visualization",
        "source_raw_h5": _repo_relative(raw_path),
        "pupil_window_source": str(plan.get("pupil_window_source")),
        "psf_roi_source": str(plan.get("psf_roi_source")),
        "camera_params_source": str(plan.get("camera_params_source")),
        "wavelength_nm": float(plan.get("wavelength", {}).get("wavelength_nm", float("nan"))),
        "mask_ids": mask_ids,
        "repeat_indices": repeat_indices,
        "perturbations": perturbation_metrics,
        "validity": {
            "dotf_computed": True,
            "pupil_stitching_performed": False,
            "scientific_calibration_valid": False,
            "training_ready": False,
        },
        "analysis": {
            "script": "scripts/analyze_dotf.py",
            "git_commit": _git_commit(),
            "normalize_energy": normalize_energy,
            "align_before_fft": align_before_fft,
            "window_before_fft": bool(plan.get("dotf", {}).get("window_before_fft", False)),
        },
    }
    (out_dir / "dotf_metrics.json").write_text(json_dumps(metrics), encoding="utf-8")
    _write_report(
        out_dir / "dotf_report.md",
        metrics=metrics,
        pupil_window=pupil_window,
        psf_roi=psf_roi,
        camera_params_source=camera_params_source,
    )
    return metrics


def _write_report(
    path: Path,
    *,
    metrics: dict[str, Any],
    pupil_window: dict[str, Any],
    psf_roi: dict[str, Any],
    camera_params_source: dict[str, Any],
) -> None:
    lines = [
        "# Phase 3.3 dOTF diagnostic report",
        "",
        f"- Source raw HDF5: `{metrics['source_raw_h5']}`",
        f"- Wavelength (nm): {metrics['wavelength_nm']}",
        f"- Pupil window source: `{metrics['pupil_window_source']}`",
        f"- PSF ROI source: `{metrics['psf_roi_source']}`",
        f"- Camera params source: `{metrics['camera_params_source']}`",
        f"- pupil_stitching_performed=false",
        "",
        "dOTF is used here as a diagnostic visualization of structured pupil-domain response.",
        "The result is not stitched into a full complex pupil.",
        "The result is not a final pupil reconstruction.",
        "",
        f"- Effective pupil physical shape: {pupil_window.get('physical_shape')}",
        f"- PSF ROI: {psf_roi.get('roi')}",
        f"- Camera parameter validity: {camera_params_source.get('validity', {}).get('psf_exposure_safe')}",
        "",
        "Per-perturbation summaries:",
    ]
    for perturbation_id, item in metrics["perturbations"].items():
        lines.extend(
            [
                f"- `{perturbation_id}`: reference_repeats={item['reference_repeats']}, "
                f"perturbed_repeats={item['perturbed_repeats']}, "
                f"psf_difference_relative_l2={item['psf_difference_relative_l2']:.6g}, "
                f"dotf_peak_abs={item['dotf_peak_abs']:.6g}",
            ]
        )
    lines.extend(
        [
            "",
            "dOTF diagnostic visualization completed.",
            "Structured pupil-domain response is observable.",
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
    print(json_dumps({"validity": result["validity"], "perturbations": result["perturbations"]}))


if __name__ == "__main__":
    main()
