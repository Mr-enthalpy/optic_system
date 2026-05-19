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

    roi_cfg = dict(plan.get("psf_roi", {}))
    candidate_crop_sizes = _candidate_crop_sizes(roi_cfg)
    candidate_roi_map = {_roi_key_for_crop_size(size): size for size in candidate_crop_sizes}
    current_baseline_roi_key = str(
        roi_cfg.get("current_baseline_roi_key") or next(iter(candidate_roi_map.keys()))
    )
    default_roi_key = str(roi_cfg.get("default_roi_key") or current_baseline_roi_key)
    baseline_crop_size = tuple(candidate_roi_map.get(current_baseline_roi_key, candidate_crop_sizes[0]))
    center_window_radius = int(roi_cfg.get("center_window_radius", 32))
    full_scale = camera_meta.get("frame_dtype_full_scale")
    valid_domain = camera_params_record.get("psf_safety_policy", {}).get("valid_pixel_domain")
    result = estimate_psf_roi(
        frames,
        crop_size=baseline_crop_size,
        center_window_radius=center_window_radius,
        full_scale=float(full_scale) if full_scale is not None else None,
        valid_pixel_domain=valid_domain,
    )

    frame_shape = list(result["mean_frame"].shape)
    rois = _build_candidate_rois(
        center=result["center"],
        candidate_roi_map=candidate_roi_map,
        frame_shape=tuple(frame_shape),
        current_baseline_roi_key=current_baseline_roi_key,
    )
    baseline_roi = dict(rois[current_baseline_roi_key])
    baseline_quality = dict(result["quality"])
    validity = {
        "psf_roi_candidates_estimated": True,
        "psf_roi_estimated": bool(baseline_roi.get("fits_frame", False))
        and not bool(baseline_quality["full_scale_in_avg_valid_domain"]),
        "final_roi_selected": False,
        "scientific_calibration_valid": False,
        "training_ready": False,
    }
    psf_roi = {
        "schema_version": 2,
        "phase": "3.2a",
        "task": "camera_frame_psf_roi_calibration",
        "source_raw_h5": _repo_relative(raw_path),
        "pupil_window_source": str(plan.get("pupil_window_source")),
        "camera_params_source": str(plan.get("camera_params_source")),
        "camera_profile_used": camera_meta.get("camera_profile_used"),
        "wavelength_nm": float(plan.get("wavelength", {}).get("wavelength_nm", float("nan"))),
        "frame_shape": frame_shape,
        "center": result["center"],
        "roi": {
            "x_min": int(baseline_roi["x_min"]),
            "x_max": int(baseline_roi["x_max"]),
            "y_min": int(baseline_roi["y_min"]),
            "y_max": int(baseline_roi["y_max"]),
            "width": int(baseline_roi["width"]),
            "height": int(baseline_roi["height"]),
        },
        "rois": rois,
        "current_baseline_roi_key": current_baseline_roi_key,
        "default_roi_key": default_roi_key,
        "final_selected_roi_key": None,
        "selection_policy": "manual_after_dotf_visual_inspection",
        "quality": {
            "peak_pixel": float(baseline_quality["peak_pixel"]),
            "mean_pixel": float(baseline_quality["mean_pixel"]),
            "background_level": float(baseline_quality["background_level"]),
            "roi_energy_fraction": float(baseline_quality["roi_energy_fraction"]),
            "full_scale_in_avg_valid_domain": bool(baseline_quality["full_scale_in_avg_valid_domain"]),
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

    (out_dir / "psf_roi.json").write_text(json_dumps(psf_roi), encoding="utf-8")
    for roi_key, roi_record in rois.items():
        write_preview_png(
            out_dir / f"psf_roi_preview_{roi_key}.png",
            result["mean_frame"],
            roi=_preview_roi(roi_record, tuple(frame_shape)),
        )
    write_preview_png(out_dir / "psf_roi_preview.png", result["mean_frame"], roi=psf_roi["roi"])
    _write_report(out_dir / "psf_roi_candidates_report.md", psf_roi)
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
        f"- Current frozen baseline ROI key: `{psf_roi['current_baseline_roi_key']}`",
        f"- Default ROI key: `{psf_roi['default_roi_key']}`",
        f"- Final selected ROI key: `{psf_roi['final_selected_roi_key']}`",
        f"- Baseline ROI: x=[{r['x_min']}, {r['x_max']}), y=[{r['y_min']}, {r['y_max']}), size={r['width']}x{r['height']}",
        f"- Peak pixel: {q['peak_pixel']:.3f}",
        f"- Background level: {q['background_level']:.3f}",
        f"- ROI energy fraction: {q['roi_energy_fraction']:.6f}",
        f"- Full-scale pixel in averaged valid domain: {q['full_scale_in_avg_valid_domain']}",
        f"- ROI candidates estimated: {psf_roi['validity']['psf_roi_candidates_estimated']}",
        f"- Final ROI selected: {psf_roi['validity']['final_roi_selected']}",
        "",
        "Candidate ROI summary:",
    ]
    for roi_key, roi_record in psf_roi["rois"].items():
        line = (
            f"- `{roi_key}`: size={roi_record['width']}x{roi_record['height']}, "
            f"fits_frame={roi_record['fits_frame']}, purpose={roi_record.get('purpose', [])}"
        )
        if roi_record.get("fits_frame"):
            line += (
                f", x=[{roi_record['x_min']}, {roi_record['x_max']}), "
                f"y=[{roi_record['y_min']}, {roi_record['y_max']})"
            )
        else:
            line += f", skip_reason={roi_record.get('skip_reason')}"
        lines.append(line)
    lines.extend(
        [
            "",
            "The current frozen baseline is roi_256 when present; additional ROI candidates are diagnostic only.",
            "These ROI candidates are not an automatic final selection.",
            "final_selected_roi_key remains null until manual dOTF visual inspection is complete.",
            "Preview PNGs are contrast-stretched visualization aids, not raw exposure judgment images.",
            "",
            "The full-scale flag is an averaged-frame quality diagnostic, not the Phase 3.0.5b raw-burst PSF safety criterion.",
            "This result is not a scientific calibration and is not training-ready.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _candidate_crop_sizes(roi_cfg: dict[str, Any]) -> list[tuple[int, int]]:
    candidate_sizes = roi_cfg.get("candidate_crop_sizes")
    if isinstance(candidate_sizes, list) and candidate_sizes:
        return [(int(item[0]), int(item[1])) for item in candidate_sizes]
    crop_size = roi_cfg.get("crop_size", [256, 256])
    return [(int(crop_size[0]), int(crop_size[1]))]


def _roi_key_for_crop_size(crop_size: tuple[int, int]) -> str:
    height, width = int(crop_size[0]), int(crop_size[1])
    if height == width:
        return f"roi_{width}"
    return f"roi_{width}x{height}"


def _build_candidate_rois(
    *,
    center: dict[str, Any],
    candidate_roi_map: dict[str, tuple[int, int]],
    frame_shape: tuple[int, int],
    current_baseline_roi_key: str,
) -> dict[str, Any]:
    rois: dict[str, Any] = {}
    for index, (roi_key, crop_size) in enumerate(candidate_roi_map.items()):
        roi_record = _centered_roi_candidate(center=center, crop_size=crop_size, frame_shape=frame_shape)
        roi_record["purpose"] = _roi_purpose(
            width=int(roi_record["width"]),
            is_current_baseline=roi_key == current_baseline_roi_key,
            candidate_index=index,
        )
        rois[roi_key] = roi_record
    return rois


def _centered_roi_candidate(
    *,
    center: dict[str, Any],
    crop_size: tuple[int, int],
    frame_shape: tuple[int, int],
) -> dict[str, Any]:
    frame_h, frame_w = int(frame_shape[0]), int(frame_shape[1])
    crop_h, crop_w = int(crop_size[0]), int(crop_size[1])
    center_x = float(center["x"])
    center_y = float(center["y"])
    x_min = int(round(center_x - crop_w / 2.0))
    y_min = int(round(center_y - crop_h / 2.0))
    x_max = x_min + crop_w
    y_max = y_min + crop_h
    fits_frame = x_min >= 0 and y_min >= 0 and x_max <= frame_w and y_max <= frame_h
    record = {
        "x_min": int(x_min),
        "x_max": int(x_max),
        "y_min": int(y_min),
        "y_max": int(y_max),
        "width": int(crop_w),
        "height": int(crop_h),
        "fits_frame": bool(fits_frame),
    }
    if not fits_frame:
        record["skip_reason"] = "ROI exceeds frame boundary"
    return record


def _roi_purpose(*, width: int, is_current_baseline: bool, candidate_index: int) -> list[str]:
    if is_current_baseline:
        return ["current_baseline", "preview", "legacy_compatibility"]
    if width >= 1024:
        return ["dotf_candidate", "full_support_candidate"]
    if width >= 768:
        return ["dotf_candidate", "support_candidate"]
    if width >= 512:
        return ["dotf_candidate", "model_candidate"]
    if candidate_index > 0:
        return ["dotf_candidate"]
    return ["candidate"]


def _preview_roi(roi: dict[str, Any], frame_shape: tuple[int, int]) -> dict[str, Any] | None:
    frame_h, frame_w = int(frame_shape[0]), int(frame_shape[1])
    x_min = max(0, min(frame_w, int(roi["x_min"])))
    x_max = max(0, min(frame_w, int(roi["x_max"])))
    y_min = max(0, min(frame_h, int(roi["y_min"])))
    y_max = max(0, min(frame_h, int(roi["y_max"])))
    if x_max <= x_min or y_max <= y_min:
        return None
    return {"x_min": x_min, "x_max": x_max, "y_min": y_min, "y_max": y_max}


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
    print(
        json_dumps(
            {
                "psf_roi": result["roi"],
                "current_baseline_roi_key": result["current_baseline_roi_key"],
                "validity": result["validity"],
            }
        )
    )


if __name__ == "__main__":
    main()
