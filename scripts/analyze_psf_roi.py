#!/usr/bin/env python3
"""Analyze Phase 3.2a raw PSF ROI capture and write camera-frame psf_roi.json."""

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

from tasks.psf_phase3 import estimate_psf_roi, json_dumps, write_preview_png  # noqa: E402


def _resolve_repo_path(path: str | Path) -> Path:
    p = Path(path)
    return p if p.is_absolute() else _repo_root() / p


def analyze_psf_roi(raw_h5: str | Path, output_dir: str | Path) -> dict[str, Any]:
    raw_path = _resolve_repo_path(raw_h5)
    out_dir = _resolve_repo_path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    with h5py.File(str(raw_path), "r") as f:
        frames = f["raw/frames_avg"][()]
        plan = _read_json_dataset(f, "capture/plan_json")
        pupil_window = _read_json_dataset(f, "provenance/pupil_window_source_json")
        camera_params_record = _read_json_dataset(f, "provenance/camera_params_source_json")
        camera_meta = _read_json_dataset(f, "camera/metadata_json")
        mask_ids = [_decode(x) for x in f["raw/mask_id"][()]]

    crop_size = tuple(int(v) for v in plan.get("psf_roi", {}).get("crop_size", [256, 256]))
    center_window_radius = int(plan.get("psf_roi", {}).get("center_window_radius", 32))
    full_scale = camera_meta.get("frame_dtype_full_scale")
    valid_domain = camera_params_record.get("psf_safety_policy", {}).get("valid_pixel_domain")
    result = estimate_psf_roi(
        frames,
        crop_size=crop_size,
        center_window_radius=center_window_radius,
        full_scale=float(full_scale) if full_scale is not None else None,
        valid_pixel_domain=valid_domain,
    )
    frame_shape = list(result["mean_frame"].shape)
    roi = result["roi"]
    validity = {
        "psf_roi_estimated": not bool(result["quality"]["roi_exceeds_frame_before_clamp"])
        and not bool(result["quality"]["full_scale_in_avg_valid_domain"]),
        "scientific_calibration_valid": False,
        "training_ready": False,
    }
    psf_roi = {
        "schema_version": 1,
        "phase": "3.2a",
        "task": "camera_frame_psf_roi_calibration",
        "source_raw_h5": _repo_relative(raw_path),
        "pupil_window_source": str(plan.get("pupil_window_source")),
        "camera_params_source": str(plan.get("camera_params_source")),
        "camera_profile_used": camera_meta.get("camera_profile_used"),
        "wavelength_nm": float(plan.get("wavelength", {}).get("wavelength_nm", float("nan"))),
        "frame_shape": frame_shape,
        "center": result["center"],
        "roi": roi,
        "quality": {
            "peak_pixel": float(result["quality"]["peak_pixel"]),
            "mean_pixel": float(result["quality"]["mean_pixel"]),
            "background_level": float(result["quality"]["background_level"]),
            "roi_energy_fraction": float(result["quality"]["roi_energy_fraction"]),
            "full_scale_in_avg_valid_domain": bool(result["quality"]["full_scale_in_avg_valid_domain"]),
        },
        "validity": validity,
        "coordinate_system": "camera sensor coordinates",
        "analysis": {
            "script": "scripts/analyze_psf_roi.py",
            "git_commit": _git_commit(),
            "mask_ids": mask_ids,
            "method": "peak_then_center_of_mass",
        },
    }
    roi_path = out_dir / "psf_roi.json"
    roi_path.write_text(json_dumps(psf_roi), encoding="utf-8")
    write_preview_png(out_dir / "psf_roi_preview.png", result["mean_frame"], roi=roi)
    _write_report(out_dir / "psf_roi_report.md", psf_roi)
    return psf_roi


def _write_report(path: Path, psf_roi: dict[str, Any]) -> None:
    q = psf_roi["quality"]
    r = psf_roi["roi"]
    lines = [
        "# Phase 3.2a PSF ROI report",
        "",
        f"- Source raw HDF5: `{psf_roi['source_raw_h5']}`",
        "- Coordinate system: camera sensor coordinates",
        f"- Center: x={psf_roi['center']['x']:.3f}, y={psf_roi['center']['y']:.3f}",
        f"- ROI: x=[{r['x_min']}, {r['x_max']}), y=[{r['y_min']}, {r['y_max']}), size={r['width']}x{r['height']}",
        f"- Peak pixel: {q['peak_pixel']:.3f}",
        f"- Background level: {q['background_level']:.3f}",
        f"- ROI energy fraction: {q['roi_energy_fraction']:.6f}",
        f"- Full-scale pixel in averaged valid domain: {q['full_scale_in_avg_valid_domain']}",
        f"- PSF ROI estimated: {psf_roi['validity']['psf_roi_estimated']}",
        "",
        "The full-scale flag is an averaged-frame quality diagnostic, not the Phase 3.0.5b raw-burst PSF safety criterion.",
        "This result is not a scientific calibration and is not training-ready.",
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
    parser = argparse.ArgumentParser(description="Analyze Phase 3.2a PSF ROI")
    parser.add_argument("--raw-h5", default="data/raw/bishe_psf_roi.h5")
    parser.add_argument("--output-dir", default="outputs/psf_roi")
    args = parser.parse_args()
    result = analyze_psf_roi(args.raw_h5, args.output_dir)
    print(json_dumps({"psf_roi": result["roi"], "validity": result["validity"]}))


if __name__ == "__main__":
    main()
