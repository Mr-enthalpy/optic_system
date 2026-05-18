#!/usr/bin/env python3
"""Analyze Phase 3.4 measured PSF dictionary and export LCD_forward HDF5."""

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

from tasks.psf_dictionary_phase3 import normalize_psf_for_export, psf_dictionary_stats  # noqa: E402
from tasks.psf_phase3 import json_dumps, valid_pixel_mask, write_preview_png  # noqa: E402


def _resolve_repo_path(path: str | Path) -> Path:
    p = Path(path)
    return p if p.is_absolute() else _repo_root() / p


def analyze_psf_dictionary(raw_h5: str | Path, output_dir: str | Path) -> dict[str, Any]:
    raw_path = _resolve_repo_path(raw_h5)
    out_dir = _resolve_repo_path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    with h5py.File(str(raw_path), "r") as f:
        frames = f["raw/frames_avg"][()]
        crops = f["raw/crops"][()]
        masks_lowres = f["raw/masks_lowres"][()]
        mask_ids = [_decode(x) for x in f["raw/mask_id"][()]]
        mask_families = [_decode(x) for x in f["raw/mask_family"][()]]
        repeat_indices = [int(x) for x in f["raw/repeat_index"][()]]
        plan = _require_json_dataset(f, "capture/plan_json")
        pupil_window = _require_json_dataset(f, "provenance/pupil_window_source_json")
        psf_roi = _require_json_dataset(f, "provenance/psf_roi_source_json")
        camera_params_source = _require_json_dataset(f, "provenance/camera_params_source_json")
        camera_meta = _require_json_dataset(f, "camera/metadata_json")

    _validate_required_provenance(plan, pupil_window, psf_roi, camera_params_source)
    stats = psf_dictionary_stats(crops, mask_ids)
    unique_ids = stats["mask_ids"]
    ids_arr = np.asarray(mask_ids)
    family_by_id = {mid: mask_families[np.where(ids_arr == mid)[0][0]] for mid in unique_ids}
    mean_crops = stats["psf_mean_stack"]
    np.save(out_dir / "psf_mean_stack.npy", mean_crops)
    np.save(out_dir / "psf_crop_stack.npy", crops)
    unique_lowres = np.stack([masks_lowres[np.where(ids_arr == mid)[0][0]] for mid in unique_ids], axis=0)
    np.save(out_dir / "mask_lowres_stack.npy", unique_lowres)
    _write_mask_contact_sheet(out_dir / "mask_preview_contact_sheet.png", unique_lowres, unique_ids)
    _write_psf_contact_sheet(out_dir / "psf_preview_contact_sheet.png", mean_crops, unique_ids)

    repeats_per_mask = int(plan["capture"]["repeats_per_mask"])
    family_counts: dict[str, int] = {}
    for family in family_by_id.values():
        family_counts[family] = family_counts.get(family, 0) + 1
    full_scale_count = _count_full_scale_frames(
        frames,
        frame_dtype_full_scale=float(camera_meta.get("frame_dtype_full_scale", float("inf"))),
        valid_pixel_domain=camera_params_source.get("psf_safety_policy", {}).get("valid_pixel_domain"),
    )
    summary = {
        "schema_version": 1,
        "phase": "3.4",
        "task": "measured_psf_dictionary",
        "source_raw_h5": _repo_relative(raw_path),
        "pupil_window_source": str(plan.get("pupil_window_source")),
        "psf_roi_source": str(plan.get("psf_roi_source")),
        "camera_params_source": str(plan.get("camera_params_source")),
        "wavelength_nm": float(plan.get("wavelength", {}).get("wavelength_nm", float("nan"))),
        "n_masks": int(len(unique_ids)),
        "repeats_per_mask": repeats_per_mask,
        "lowres_shape": list(unique_lowres.shape[-2:]),
        "psf_crop_shape": list(mean_crops.shape[-2:]),
        "mask_families": family_counts,
        "quality": {
            **stats["quality"],
            "full_scale_in_avg_valid_domain_count": int(full_scale_count),
        },
        "validity": {
            "psf_dictionary_acquired": True,
            "training_ready": False,
            "scientific_calibration_valid": False,
        },
        "analysis": {
            "script": "scripts/analyze_psf_dictionary.py",
            "git_commit": _git_commit(),
        },
    }
    manifest = {
        "schema_version": 1,
        "phase": "3.4",
        "task": "measured_psf_dictionary_manifest",
        "source_raw_h5": _repo_relative(raw_path),
        "masks": [
            {
                "mask_id": mid,
                "mask_family": family_by_id[mid],
                "repeat_count": int(np.sum(ids_arr == mid)),
            }
            for mid in unique_ids
        ],
    }
    (out_dir / "psf_dictionary_summary.json").write_text(json_dumps(summary), encoding="utf-8")
    (out_dir / "psf_dictionary_manifest.json").write_text(json_dumps(manifest), encoding="utf-8")
    export_result = _export_lcd_forward(
        out_dir=out_dir,
        plan=plan,
        unique_ids=unique_ids,
        family_by_id=family_by_id,
        unique_lowres=unique_lowres,
        mean_crops=mean_crops,
        source_raw_h5=_repo_relative(raw_path),
    )
    summary["export_lcd_forward"] = export_result
    (out_dir / "psf_dictionary_summary.json").write_text(json_dumps(summary), encoding="utf-8")
    _write_report(out_dir / "psf_dictionary_report.md", summary)
    return summary


def _export_lcd_forward(
    *,
    out_dir: Path,
    plan: dict[str, Any],
    unique_ids: list[str],
    family_by_id: dict[str, str],
    unique_lowres: np.ndarray,
    mean_crops: np.ndarray,
    source_raw_h5: str,
) -> dict[str, Any]:
    export_cfg = plan.get("export", {}).get("lcd_forward", {})
    if not bool(export_cfg.get("enabled", False)):
        return {"enabled": False}
    split_cfg = export_cfg.get("split", {})
    configured_export_dir = Path(str(export_cfg.get("output_dir", "export_lcd_forward")))
    export_dir = configured_export_dir if configured_export_dir.is_absolute() else out_dir / configured_export_dir.name
    export_dir.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(int(plan["capture"].get("random_seed", 0)))
    indices = np.arange(len(unique_ids))
    rng.shuffle(indices)
    n = len(indices)
    n_train = int(np.floor(n * float(split_cfg["train"])))
    n_val = int(np.floor(n * float(split_cfg["val"])))
    n_test = max(0, n - n_train - n_val)
    if n > 0 and n_train == 0:
        n_train = 1
        if n_val > 0:
            n_val -= 1
        elif n_test > 0:
            n_test -= 1
    splits = {
        "train": indices[:n_train],
        "val": indices[n_train : n_train + n_val],
        "test": indices[n_train + n_val : n_train + n_val + n_test],
    }
    result: dict[str, Any] = {"enabled": True, "splits": {}}
    normalization_summary: dict[str, Any] = {}
    for split_name, split_idx in splits.items():
        path = export_dir / f"{split_name}.h5"
        masks = unique_lowres[split_idx][:, np.newaxis, :, :, :]
        psf_list = []
        norm_meta = []
        for idx in split_idx:
            normalized, meta = normalize_psf_for_export(mean_crops[idx])
            psf_list.append(normalized)
            norm_meta.append(meta)
        if psf_list:
            psfs = np.stack(psf_list, axis=0)[:, np.newaxis, np.newaxis, :, :]
        else:
            psfs = np.zeros((0, 1, 1, mean_crops.shape[-2], mean_crops.shape[-1]), dtype=np.float64)
        with h5py.File(str(path), "w") as f:
            string_dtype = h5py.string_dtype(encoding="utf-8")
            f.create_dataset("masks", data=masks, compression="gzip", compression_opts=4)
            f.create_dataset("psfs", data=psfs, compression="gzip", compression_opts=4)
            f.create_dataset("mask_id", data=np.asarray([unique_ids[i] for i in split_idx], dtype=object), dtype=string_dtype)
            f.create_dataset("mask_family", data=np.asarray([family_by_id[unique_ids[i]] for i in split_idx], dtype=object), dtype=string_dtype)
            metadata = {
                "source_raw_h5": source_raw_h5,
                "pupil_window_source": str(plan.get("pupil_window_source")),
                "psf_roi_source": str(plan.get("psf_roi_source")),
                "camera_params_source": str(plan.get("camera_params_source")),
                "wavelength_nm": float(plan.get("wavelength", {}).get("wavelength_nm", float("nan"))),
                "T": 1,
                "L": 1,
                "mask_shape": list(masks.shape[2:]),
                "psf_shape": list(psfs.shape[-2:]),
                "normalization": "background_subtract_then_sum_normalize",
                "split_name": split_name,
            }
            f.create_dataset("metadata_json", data=json_dumps(metadata), dtype=string_dtype)
        normalization_summary[split_name] = norm_meta
        result["splits"][split_name] = [unique_ids[i] for i in split_idx]
    readme_lines = [
        "# LCD_forward export",
        "",
        "This directory contains derived single-wavelength measured PSF dictionary exports.",
        "Shapes:",
        "- masks: [N, 1, 1, 64, 64]",
        "- psfs:  [N, 1, 1, Hp, Wp]",
        "",
        "Normalization: background_subtract_then_sum_normalize.",
        "Raw HDF5 remains the source of truth.",
    ]
    (export_dir / "README.md").write_text("\n".join(readme_lines) + "\n", encoding="utf-8")
    result["output_dir"] = _repo_relative(export_dir)
    result["normalization"] = "background_subtract_then_sum_normalize"
    return result


def _count_full_scale_frames(frames: np.ndarray, *, frame_dtype_full_scale: float, valid_pixel_domain: dict[str, Any] | None) -> int:
    count = 0
    for frame in np.asarray(frames):
        mask = valid_pixel_mask(frame.shape, valid_pixel_domain)
        if np.any(np.asarray(frame)[mask] >= float(frame_dtype_full_scale)):
            count += 1
    return count


def _write_mask_contact_sheet(path: Path, masks_lowres: np.ndarray, mask_ids: list[str]) -> None:
    sheet = _tile_images(np.asarray(masks_lowres)[:, 0, :, :])
    write_preview_png(path, sheet)


def _write_psf_contact_sheet(path: Path, mean_crops: np.ndarray, mask_ids: list[str]) -> None:
    sheet = _tile_images(np.asarray(mean_crops))
    write_preview_png(path, sheet)


def _tile_images(images: np.ndarray) -> np.ndarray:
    arr = np.asarray(images, dtype=np.float64)
    n, h, w = arr.shape
    cols = min(8, max(1, int(np.ceil(np.sqrt(n)))))
    rows = int(np.ceil(n / cols))
    sheet = np.zeros((rows * h, cols * w), dtype=np.float64)
    for i in range(n):
        r, c = divmod(i, cols)
        sheet[r * h : (r + 1) * h, c * w : (c + 1) * w] = arr[i]
    return sheet


def _validate_required_provenance(
    plan: dict[str, Any],
    pupil_window: dict[str, Any],
    psf_roi: dict[str, Any],
    camera_params_source: dict[str, Any],
) -> None:
    if plan.get("phase") != "3.4":
        raise ValueError("capture/plan_json must describe a Phase 3.4 PSF dictionary plan")
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


def _write_report(path: Path, summary: dict[str, Any]) -> None:
    q = summary["quality"]
    lines = [
        "# Phase 3.4 measured PSF dictionary report",
        "",
        f"- Source raw HDF5: `{summary['source_raw_h5']}`",
        f"- Number of masks: {summary['n_masks']}",
        f"- Repeats per mask: {summary['repeats_per_mask']}",
        f"- Lowres shape: {summary['lowres_shape']}",
        f"- PSF crop shape: {summary['psf_crop_shape']}",
        f"- Mean repeat MSE: {q['mean_repeat_mse']:.6g}",
        f"- Median repeat MSE: {q['median_repeat_mse']:.6g}",
        f"- Mean total energy CV: {q['mean_total_energy_cv']:.6g}",
        f"- Max center drift (px): {q['max_center_drift_px']:.6g}",
        f"- Full-scale count in averaged valid domain: {q['full_scale_in_avg_valid_domain_count']}",
        "",
        "measured PSF dictionary acquired and exported",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


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
    parser = argparse.ArgumentParser(description="Analyze Phase 3.4 measured PSF dictionary")
    parser.add_argument("--raw-h5", default="data/raw/bishe_psf_dictionary.h5")
    parser.add_argument("--output-dir", default="outputs/psf_dictionary")
    args = parser.parse_args()
    result = analyze_psf_dictionary(args.raw_h5, args.output_dir)
    print(json_dumps({"validity": result["validity"], "export_lcd_forward": result.get("export_lcd_forward", {})}))


if __name__ == "__main__":
    main()
