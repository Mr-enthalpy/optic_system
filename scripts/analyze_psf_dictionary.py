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

from tasks.psf_dictionary_phase3 import (  # noqa: E402
    normalize_psf_for_export,
    psf_dictionary_stats_by_mask_and_wavelength,
)
from tasks.psf_phase3 import json_dumps, valid_pixel_mask, write_preview_png  # noqa: E402


def _resolve_repo_path(path: str | Path) -> Path:
    p = Path(path)
    return p if p.is_absolute() else _repo_root() / p


def analyze_psf_dictionary(raw_h5: str | Path, output_dir: str | Path) -> dict[str, Any]:
    raw_path = _resolve_repo_path(raw_h5)
    out_dir = _resolve_repo_path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    with h5py.File(str(raw_path), "r") as f:
        crops = f["raw/crops"][()]
        masks_lowres = f["raw/masks_lowres"][()]
        mask_ids = [_decode(x) for x in f["raw/mask_id"][()]]
        mask_families = [_decode(x) for x in f["raw/mask_family"][()]]
        wavelength_nm = np.asarray(f["raw/wavelength_nm"][()], dtype=np.float64) if "raw/wavelength_nm" in f else None
        wavelength_index = np.asarray(f["raw/wavelength_index"][()], dtype=np.int64) if "raw/wavelength_index" in f else None
        exposure_us = np.asarray(f["raw/exposure_us"][()], dtype=np.float64) if "raw/exposure_us" in f else None
        gain_db = np.asarray(f["raw/gain_db"][()], dtype=np.float64) if "raw/gain_db" in f else None
        camera_profile_id = [_decode(x) for x in f["raw/camera_profile_id"][()]] if "raw/camera_profile_id" in f else None
        repeat_indices = [int(x) for x in f["raw/repeat_index"][()]]
        plan = _require_json_dataset(f, "capture/plan_json")
        pupil_window = _require_json_dataset(f, "provenance/pupil_window_source_json")
        psf_roi = _require_json_dataset(f, "provenance/psf_roi_source_json")
        camera_params_source = _require_json_dataset(f, "provenance/camera_params_source_json")
        camera_meta = _require_json_dataset(f, "camera/metadata_json")

    _validate_required_provenance(plan, pupil_window, psf_roi, camera_params_source)
    if wavelength_nm is None or wavelength_index is None:
        default_wl = float(plan.get("wavelength", {}).get("wavelength_nm", float("nan")))
        wavelength_nm = np.full((len(mask_ids),), default_wl, dtype=np.float64)
        wavelength_index = np.zeros((len(mask_ids),), dtype=np.int64)
    unique_wavelength_index = list(dict.fromkeys(int(x) for x in wavelength_index.tolist()))
    unique_wavelength_nm = [float(wavelength_nm[np.where(wavelength_index == idx)[0][0]]) for idx in unique_wavelength_index]
    exposure_by_wavelength = _constant_value_by_wavelength(exposure_us, wavelength_index, unique_wavelength_index, "exposure_us")
    gain_by_wavelength = _constant_value_by_wavelength(gain_db, wavelength_index, unique_wavelength_index, "gain_db")
    profile_by_wavelength = _constant_string_by_wavelength(camera_profile_id, wavelength_index, unique_wavelength_index, "camera_profile_id")
    stats = psf_dictionary_stats_by_mask_and_wavelength(crops, mask_ids, wavelength_index)
    unique_ids = stats["mask_ids"]
    ids_arr = np.asarray(mask_ids)
    family_by_id = {mid: mask_families[np.where(ids_arr == mid)[0][0]] for mid in unique_ids}
    mean_crops = stats["psf_mean_stack"]
    np.save(out_dir / "psf_mean_stack.npy", mean_crops)
    np.save(out_dir / "psf_crop_stack.npy", crops)
    unique_lowres = np.stack([masks_lowres[np.where(ids_arr == mid)[0][0]] for mid in unique_ids], axis=0)
    np.save(out_dir / "mask_lowres_stack.npy", unique_lowres)
    _write_mask_contact_sheet(out_dir / "mask_preview_contact_sheet.png", unique_lowres, unique_ids)
    _write_psf_contact_sheet(out_dir / "psf_preview_contact_sheet.png", mean_crops, unique_ids, unique_wavelength_nm)

    repeats_per_mask = int(plan["capture"]["repeats_per_mask"])
    family_counts: dict[str, int] = {}
    for family in family_by_id.values():
        family_counts[family] = family_counts.get(family, 0) + 1
    summary = {
        "schema_version": 1,
        "phase": "3.4",
        "task": "measured_psf_dictionary",
        "source_raw_h5": _repo_relative(raw_path),
        "pupil_window_source": str(plan.get("pupil_window_source")),
        "psf_roi_source": str(plan.get("psf_roi_source")),
        "camera_params_source": str(plan.get("camera_params_source")),
        "wavelength_nm": unique_wavelength_nm[0] if len(unique_wavelength_nm) == 1 else None,
        "wavelengths_nm": unique_wavelength_nm,
        "n_wavelengths": int(len(unique_wavelength_nm)),
        "n_masks": int(len(unique_ids)),
        "repeats_per_mask": repeats_per_mask,
        "lowres_shape": list(unique_lowres.shape[-2:]),
        "psf_crop_shape": list(mean_crops.shape[-2:]),
        "mask_families": family_counts,
        "quality": {
            **stats["quality"],
            "full_scale_in_avg_valid_domain_count": None,
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
        "camera_profile_policy": str(plan.get("camera_profile_policy", "global_safe_camera")),
        "psf_crop_source": "raw/crops",
        "full_frame_saved": False,
        "exposure_us_by_wavelength": {
            _format_wavelength_key(unique_wavelength_nm[i]): float(exposure_by_wavelength[i])
            for i in range(len(unique_wavelength_nm))
        },
        "gain_db_by_wavelength": {
            _format_wavelength_key(unique_wavelength_nm[i]): float(gain_by_wavelength[i])
            for i in range(len(unique_wavelength_nm))
        },
        "camera_profile_id_by_wavelength": {
            _format_wavelength_key(unique_wavelength_nm[i]): str(profile_by_wavelength[i])
            for i in range(len(unique_wavelength_nm))
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
        unique_wavelength_nm=unique_wavelength_nm,
        exposure_by_wavelength=exposure_by_wavelength,
        gain_by_wavelength=gain_by_wavelength,
        profile_by_wavelength=profile_by_wavelength,
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
    unique_wavelength_nm: list[float],
    exposure_by_wavelength: list[float],
    gain_by_wavelength: list[float],
    profile_by_wavelength: list[str],
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
    export_wavelengths_nm = [float(x) for x in unique_wavelength_nm]
    for split_name, split_idx in splits.items():
        path = export_dir / f"{split_name}.h5"
        masks = unique_lowres[split_idx][:, np.newaxis, :, :, :]
        psf_list = []
        norm_meta = []
        for idx in split_idx:
            wavelength_psfs = []
            wavelength_meta = []
            for wl_psf in mean_crops[idx]:
                normalized, meta = normalize_psf_for_export(wl_psf)
                wavelength_psfs.append(normalized)
                wavelength_meta.append(meta)
            psf_list.append(np.stack(wavelength_psfs, axis=0))
            norm_meta.append(wavelength_meta)
        if psf_list:
            psfs = np.stack(psf_list, axis=0)[:, np.newaxis, :, :, :]
        else:
            psfs = np.zeros((0, 1, len(export_wavelengths_nm), mean_crops.shape[-2], mean_crops.shape[-1]), dtype=np.float64)
        with h5py.File(str(path), "w") as f:
            string_dtype = h5py.string_dtype(encoding="utf-8")
            f.create_dataset("masks", data=masks, compression="gzip", compression_opts=4)
            f.create_dataset("psfs", data=psfs, compression="gzip", compression_opts=4)
            f.create_dataset("wavelengths_nm", data=np.asarray(export_wavelengths_nm, dtype=np.float64))
            f.create_dataset("mask_id", data=np.asarray([unique_ids[i] for i in split_idx], dtype=object), dtype=string_dtype)
            f.create_dataset("mask_family", data=np.asarray([family_by_id[unique_ids[i]] for i in split_idx], dtype=object), dtype=string_dtype)
            metadata = {
                "source_raw_h5": source_raw_h5,
                "pupil_window_source": str(plan.get("pupil_window_source")),
                "psf_roi_source": str(plan.get("psf_roi_source")),
                "camera_params_source": str(plan.get("camera_params_source")),
                "wavelengths_nm": export_wavelengths_nm,
                "T": 1,
                "L": int(len(export_wavelengths_nm)),
                "mask_shape": list(masks.shape[2:]),
                "psf_shape": list(psfs.shape[-2:]),
                "camera_profile_policy": str(plan.get("camera_profile_policy", "global_safe_camera")),
                "psf_crop_source": "raw/crops",
                "full_frame_saved": False,
                "exposure_us_by_wavelength": {
                    _format_wavelength_key(export_wavelengths_nm[i]): float(exposure_by_wavelength[i])
                    for i in range(len(export_wavelengths_nm))
                },
                "gain_db_by_wavelength": {
                    _format_wavelength_key(export_wavelengths_nm[i]): float(gain_by_wavelength[i])
                    for i in range(len(export_wavelengths_nm))
                },
                "camera_profile_id_by_wavelength": {
                    _format_wavelength_key(export_wavelengths_nm[i]): str(profile_by_wavelength[i])
                    for i in range(len(export_wavelengths_nm))
                },
                "normalization": "background_subtract_then_sum_normalize",
                "split_name": split_name,
            }
            f.create_dataset("metadata_json", data=json_dumps(metadata), dtype=string_dtype)
        normalization_summary[split_name] = norm_meta
        result["splits"][split_name] = [unique_ids[i] for i in split_idx]
    readme_lines = [
        "# LCD_forward export",
        "",
        "This directory contains measured PSF dictionary exports.",
        "Shapes:",
        "- masks: [N, 1, 1, 64, 64]",
        "- psfs:  [N, 1, L, Hp, Wp]",
        f"- wavelengths_nm: {export_wavelengths_nm}",
        "",
        "Normalization: background_subtract_then_sum_normalize.",
        "Raw HDF5 remains the source of truth.",
    ]
    (export_dir / "README.md").write_text("\n".join(readme_lines) + "\n", encoding="utf-8")
    result["output_dir"] = _repo_relative(export_dir)
    result["normalization"] = "background_subtract_then_sum_normalize"
    result["wavelengths_nm"] = export_wavelengths_nm
    return result


def _constant_value_by_wavelength(
    values: np.ndarray | None,
    wavelength_index: np.ndarray,
    unique_wavelength_index: list[int],
    label: str,
) -> list[float]:
    if values is None:
        raise ValueError(f"raw/{label} is required for Phase 3.4 analysis")
    out: list[float] = []
    for idx in unique_wavelength_index:
        match = np.where(wavelength_index == int(idx))[0]
        unique = np.unique(np.asarray(values)[match])
        if unique.size != 1:
            raise ValueError(f"raw/{label} is not constant within wavelength_index={idx}")
        out.append(float(unique[0]))
    return out


def _constant_string_by_wavelength(
    values: list[str] | None,
    wavelength_index: np.ndarray,
    unique_wavelength_index: list[int],
    label: str,
) -> list[str]:
    if values is None:
        raise ValueError(f"raw/{label} is required for Phase 3.4 analysis")
    values_arr = np.asarray(values, dtype=object)
    out: list[str] = []
    for idx in unique_wavelength_index:
        match = np.where(wavelength_index == int(idx))[0]
        unique = list(dict.fromkeys(str(values_arr[i]) for i in match))
        if len(unique) != 1:
            raise ValueError(f"raw/{label} is not constant within wavelength_index={idx}")
        out.append(unique[0])
    return out


def _write_mask_contact_sheet(path: Path, masks_lowres: np.ndarray, mask_ids: list[str]) -> None:
    sheet = _tile_images(np.asarray(masks_lowres)[:, 0, :, :])
    write_preview_png(path, sheet)


def _write_psf_contact_sheet(
    path: Path,
    mean_crops: np.ndarray,
    mask_ids: list[str],
    wavelengths_nm: list[float] | None = None,
) -> None:
    arr = np.asarray(mean_crops, dtype=np.float64)
    if arr.ndim == 4:
        arr = arr.reshape(arr.shape[0] * arr.shape[1], arr.shape[2], arr.shape[3])
    sheet = _tile_images(arr)
    write_preview_png(path, sheet)


def _mean_crops_by_mask_and_wavelength(
    crops: np.ndarray,
    mask_ids: list[str],
    wavelength_index: np.ndarray,
    unique_ids: list[str],
    unique_wavelength_index: list[int],
) -> np.ndarray:
    arr = np.asarray(crops, dtype=np.float64)
    ids_arr = np.asarray(mask_ids)
    out = np.zeros((len(unique_ids), len(unique_wavelength_index), arr.shape[-2], arr.shape[-1]), dtype=np.float64)
    for i, mask_id in enumerate(unique_ids):
        for j, wl_idx in enumerate(unique_wavelength_index):
            match = np.where((ids_arr == mask_id) & (wavelength_index == int(wl_idx)))[0]
            if match.size == 0:
                raise ValueError(f"missing captures for mask_id={mask_id} wavelength_index={wl_idx}")
            out[i, j] = np.mean(arr[match], axis=0)
    return out


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


def _format_wavelength_key(wavelength_nm: float) -> str:
    return format(float(wavelength_nm), ".1f")


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
