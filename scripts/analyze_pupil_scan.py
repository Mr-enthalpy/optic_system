#!/usr/bin/env python3
"""
Analyze Phase 3.1 procedural pupil scan raw HDF5.

The ROI decision uses smoothed support in the procedural scan coordinates:
bar profiles and/or the largest connected component in a block response map.
Single-pixel camera peaks are recorded only as diagnostics.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import deque
from pathlib import Path
from typing import Any

import h5py
import numpy as np
from PIL import Image


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _ensure_sys_path() -> None:
    root = str(_repo_root())
    if root not in sys.path:
        sys.path.insert(0, root)


def analyze_pupil_scan(
    input_h5: str | Path,
    output_dir: str | Path,
    *,
    threshold_fraction: float = 0.5,
    margin_fraction: float = 0.05,
    smooth_window: int = 5,
    min_component_size: int = 3,
    spatial_stride: int = 4,
) -> dict[str, Any]:
    input_path = Path(input_h5)
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    data = _read_pupil_scan_h5(input_path)
    responses, diagnostics = _compute_responses(
        input_path,
        data["mask_ids"],
        data["modes"],
        spatial_stride=spatial_stride,
    )
    warnings: list[str] = []

    x_profile = _profile_from_mode(
        data,
        responses,
        mode="bars_x",
        axis="x",
        threshold_fraction=threshold_fraction,
        margin_fraction=margin_fraction,
        smooth_window=smooth_window,
        warnings=warnings,
    )
    y_profile = _profile_from_mode(
        data,
        responses,
        mode="bars_y",
        axis="y",
        threshold_fraction=threshold_fraction,
        margin_fraction=margin_fraction,
        smooth_window=smooth_window,
        warnings=warnings,
    )
    profile_roi = _combine_profile_roi(x_profile, y_profile, data["physical_shape"])

    block_result = _block_roi(
        data,
        responses,
        threshold_fraction=threshold_fraction,
        margin_fraction=margin_fraction,
        smooth_window=smooth_window,
        min_component_size=min_component_size,
        warnings=warnings,
    )
    response_map = block_result["response_map"]

    final_roi, profile_agreement = _combine_roi_candidates(
        profile_roi,
        block_result.get("roi"),
        data["physical_shape"],
        warnings,
    )

    contrast = _response_contrast(responses)
    component_area_fraction = float(block_result.get("component_area_fraction") or 0.0)
    confidence_level = _confidence_level(
        final_roi,
        contrast,
        profile_agreement,
        component_area_fraction,
        warnings,
    )

    if final_roi is None:
        final_roi = {
            "x_min": 0,
            "x_max": int(data["physical_shape"][1]),
            "y_min": 0,
            "y_max": int(data["physical_shape"][0]),
        }
        warnings.append("No reliable ROI support found; returned full physical LCD extent.")

    camera_params_source_text = str(data["camera_params_source"]).replace("\\", "/")
    if camera_params_source_text.endswith("camera_params.json"):
        warnings.append(
            "This analysis used the original Phase 3.0.5 coarse camera_params.json. "
            "PR #24 review observed clipping/local saturation, so this result is "
            "first-pass coarse active-region localization only; final fine scans, "
            "dOTF, PSF dictionary, and repeatability must use camera_params_psf_safe.json."
        )

    _write_profile_csv(out_dir / "x_profile.csv", x_profile)
    _write_profile_csv(out_dir / "y_profile.csv", y_profile)
    np.save(str(out_dir / "response_map.npy"), response_map)
    _write_response_map_png(out_dir / "response_map.png", response_map)

    result = {
        "schema_version": "1.0",
        "source_raw_capture_h5": str(input_h5),
        "capture_plan_id": data["plan_id"],
        "camera_params_source": data["camera_params_source"],
        "wavelength_nm": data["wavelength_nm"],
        "physical_shape": [int(data["physical_shape"][0]), int(data["physical_shape"][1])],
        "subpixel_axis": int(data["subpixel_axis"]),
        "roi_physical": {k: int(v) for k, v in final_roi.items()},
        "roi_center_physical": [
            (float(final_roi["x_min"]) + float(final_roi["x_max"])) / 2.0,
            (float(final_roi["y_min"]) + float(final_roi["y_max"])) / 2.0,
        ],
        "method": "robust_support_consensus",
        "response_metric": "robust_energy",
        "threshold_fraction": float(threshold_fraction),
        "margin_fraction": float(margin_fraction),
        "confidence": {
            "level": confidence_level,
            "contrast": float(contrast),
            "profile_agreement": float(profile_agreement),
            "component_area_fraction": float(component_area_fraction),
            "warnings": warnings,
        },
        "validity": {
            "effective_roi_estimated": confidence_level != "failed",
            "scientific_calibration_valid": False,
            "training_ready": False,
        },
        "diagnostics": {
            "max_pixel": diagnostics["max_pixel"],
            "p99_9": diagnostics["p99_9"],
            "centroid_x": diagnostics["centroid_x"],
            "centroid_y": diagnostics["centroid_y"],
            "second_moment_width": diagnostics["second_moment_width"],
        },
    }

    with open(out_dir / "effective_lcd_roi.json", "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, default=str)
    _write_report(out_dir / "pupil_scan_report.md", result, x_profile, y_profile, block_result)
    return result


def _read_pupil_scan_h5(path: Path) -> dict[str, Any]:
    with h5py.File(path, "r") as f:
        frames_shape = tuple(int(v) for v in f["raw/frames_avg"].shape)
        scan = f["scan"]
        mask_ids = _read_str_array(scan["mask_id"])
        modes = _read_str_array(scan["mode"])
        x_min = np.asarray(scan["x_min"], dtype=np.int64)
        x_max = np.asarray(scan["x_max"], dtype=np.int64)
        y_min = np.asarray(scan["y_min"], dtype=np.int64)
        y_max = np.asarray(scan["y_max"], dtype=np.int64)
        rows = np.asarray(scan["row"], dtype=np.int64)
        cols = np.asarray(scan["col"], dtype=np.int64)
        centers_x = np.asarray(scan["center_x"], dtype=np.float64)
        centers_y = np.asarray(scan["center_y"], dtype=np.float64)

        plan_id = _read_scalar_str(f["capture/plan_id"])
        lcd_meta = _json_dataset(f["lcd/metadata_json"])
        cam_source = _json_dataset(f["camera/camera_params_source_json"])
        camera_params_source = cam_source.get("source") if isinstance(cam_source, dict) else None
        wavelength_nm = _finite_or_none(float(f["tls/wavelength_nm"][()]))
        physical_shape = lcd_meta.get("physical_shape")
        if physical_shape is None:
            physical_shape = _physical_shape_from_scan(y_max, x_max)
        subpixel_axis = int(lcd_meta.get("subpixel_axis", 1))

    return {
        "frames_shape": frames_shape,
        "mask_ids": mask_ids,
        "modes": modes,
        "x_min": x_min,
        "x_max": x_max,
        "y_min": y_min,
        "y_max": y_max,
        "rows": rows,
        "cols": cols,
        "center_x": centers_x,
        "center_y": centers_y,
        "plan_id": plan_id,
        "lcd_metadata": lcd_meta,
        "camera_params_source": camera_params_source,
        "wavelength_nm": wavelength_nm,
        "physical_shape": (int(physical_shape[0]), int(physical_shape[1])),
        "subpixel_axis": subpixel_axis,
    }


def _compute_responses(
    input_path: Path,
    mask_ids: list[str],
    modes: list[str],
    *,
    spatial_stride: int = 4,
) -> tuple[np.ndarray, dict[str, list[float]]]:
    spatial_stride = max(1, int(spatial_stride))
    sample = np.s_[::spatial_stride, ::spatial_stride]
    bg_indices = [
        i for i, mid in enumerate(mask_ids)
        if mid == "baseline_all_closed" or (modes[i] == "baseline" and "closed" in mid)
    ]
    with h5py.File(input_path, "r") as f:
        dset = f["raw/frames_avg"]
        n_frames = int(dset.shape[0])
        if bg_indices:
            bg_rows = [np.asarray(dset[i][sample], dtype=np.float64) for i in bg_indices]
            background = np.mean(bg_rows, axis=0)
        else:
            probe_count = min(n_frames, 25)
            probe_idx = np.linspace(0, n_frames - 1, probe_count).astype(int)
            probe = np.stack([np.asarray(dset[int(i)][sample], dtype=np.float64) for i in probe_idx], axis=0)
            background = np.percentile(probe, 5.0, axis=0)

        responses = np.zeros((n_frames,), dtype=np.float64)
        diagnostics = {
            "max_pixel": [],
            "p99_9": [],
            "centroid_x": [],
            "centroid_y": [],
            "second_moment_width": [],
        }

        yy, xx = np.mgrid[:background.shape[0], :background.shape[1]]
        yy = yy.astype(np.float64) * spatial_stride
        xx = xx.astype(np.float64) * spatial_stride
        for i in range(n_frames):
            frame = np.asarray(dset[i][sample], dtype=np.float64)
            corr = np.maximum(frame - background, 0.0)
            p1 = float(np.percentile(corr, 1.0))
            p995 = float(np.percentile(corr, 99.5))
            clipped = np.clip(corr, p1, p995)
            responses[i] = float(np.sum(clipped)) * float(spatial_stride ** 2)

            total = float(np.sum(corr))
            if total > 0:
                cx = float(np.sum(corr * xx) / total)
                cy = float(np.sum(corr * yy) / total)
                width = float(np.sqrt(np.sum(corr * ((xx - cx) ** 2 + (yy - cy) ** 2)) / total))
            else:
                cx = cy = width = float("nan")
            diagnostics["max_pixel"].append(float(np.max(frame)))
            diagnostics["p99_9"].append(float(np.percentile(frame, 99.9)))
            diagnostics["centroid_x"].append(cx)
            diagnostics["centroid_y"].append(cy)
            diagnostics["second_moment_width"].append(width)

    return responses, diagnostics


def _profile_from_mode(
    data: dict[str, Any],
    responses: np.ndarray,
    *,
    mode: str,
    axis: str,
    threshold_fraction: float,
    margin_fraction: float,
    smooth_window: int,
    warnings: list[str],
) -> dict[str, Any]:
    idx = [i for i, m in enumerate(data["modes"]) if m == mode]
    if not idx:
        warnings.append(f"No {mode} masks found; {axis}-profile unavailable.")
        return {"axis": axis, "available": False, "rows": [], "roi": None}

    idx.sort(key=lambda i: data["center_x"][i] if axis == "x" else data["center_y"][i])
    coords = np.array([data["center_x"][i] if axis == "x" else data["center_y"][i] for i in idx], dtype=np.float64)
    values = responses[idx].astype(np.float64)
    smoothed = _moving_median(values, smooth_window)
    norm = _robust_normalize(smoothed)
    above = norm >= float(threshold_fraction)
    interval = _largest_true_interval(above)
    rows = [
        {
            "index": int(j),
            "coord": float(coords[j]),
            "response": float(values[j]),
            "smoothed_response": float(smoothed[j]),
            "normalized_response": float(norm[j]),
            "above_threshold": bool(above[j]),
        }
        for j in range(len(idx))
    ]

    roi = None
    if interval is None:
        warnings.append(f"{mode} profile has no support above threshold.")
    else:
        lo_i, hi_i = interval
        selected = idx[lo_i:hi_i + 1]
        if axis == "x":
            lo = int(np.min(data["x_min"][selected]))
            hi = int(np.max(data["x_max"][selected]))
            length = int(data["physical_shape"][1])
        else:
            lo = int(np.min(data["y_min"][selected]))
            hi = int(np.max(data["y_max"][selected]))
            length = int(data["physical_shape"][0])
        lo, hi = _apply_margin_1d(lo, hi, length, margin_fraction)
        roi = {"min": lo, "max": hi}

    return {
        "axis": axis,
        "mode": mode,
        "available": True,
        "rows": rows,
        "roi": roi,
        "threshold_fraction": float(threshold_fraction),
    }


def _combine_profile_roi(
    x_profile: dict[str, Any],
    y_profile: dict[str, Any],
    physical_shape: tuple[int, int],
) -> dict[str, int] | None:
    h, w = physical_shape
    if not x_profile.get("available") and not y_profile.get("available"):
        return None
    x_roi = x_profile.get("roi")
    y_roi = y_profile.get("roi")
    if x_roi is None and y_roi is None:
        return None
    return {
        "x_min": int(x_roi["min"]) if x_roi else 0,
        "x_max": int(x_roi["max"]) if x_roi else int(w),
        "y_min": int(y_roi["min"]) if y_roi else 0,
        "y_max": int(y_roi["max"]) if y_roi else int(h),
    }


def _block_roi(
    data: dict[str, Any],
    responses: np.ndarray,
    *,
    threshold_fraction: float,
    margin_fraction: float,
    smooth_window: int,
    min_component_size: int,
    warnings: list[str],
) -> dict[str, Any]:
    idx = [i for i, m in enumerate(data["modes"]) if m == "blocks"]
    if not idx:
        warnings.append("No block masks found; 2D component ROI unavailable.")
        return {"response_map": np.zeros((0, 0), dtype=np.float64), "roi": None}

    n_rows = int(np.max(data["rows"][idx])) + 1
    n_cols = int(np.max(data["cols"][idx])) + 1
    response_map = np.full((n_rows, n_cols), np.nan, dtype=np.float64)
    x_min_map = np.zeros((n_rows, n_cols), dtype=np.int64)
    x_max_map = np.zeros((n_rows, n_cols), dtype=np.int64)
    y_min_map = np.zeros((n_rows, n_cols), dtype=np.int64)
    y_max_map = np.zeros((n_rows, n_cols), dtype=np.int64)

    for i in idx:
        r = int(data["rows"][i])
        c = int(data["cols"][i])
        response_map[r, c] = float(responses[i])
        x_min_map[r, c] = int(data["x_min"][i])
        x_max_map[r, c] = int(data["x_max"][i])
        y_min_map[r, c] = int(data["y_min"][i])
        y_max_map[r, c] = int(data["y_max"][i])

    filled = np.nan_to_num(response_map, nan=float(np.nanmin(response_map)))
    smoothed = _median_filter2d(filled, smooth_window)
    norm = _robust_normalize(smoothed)
    thresholded = norm >= float(threshold_fraction)
    comp = _largest_component(thresholded)
    if comp is None or len(comp) < int(min_component_size):
        warnings.append("Block response map has no sufficiently large component.")
        return {
            "response_map": response_map,
            "normalized_map": norm,
            "roi": None,
            "component_area_fraction": 0.0,
        }

    rows = np.array([p[0] for p in comp], dtype=np.int64)
    cols = np.array([p[1] for p in comp], dtype=np.int64)
    r0, r1 = int(rows.min()), int(rows.max())
    c0, c1 = int(cols.min()), int(cols.max())
    x_min = int(np.min(x_min_map[r0:r1 + 1, c0:c1 + 1]))
    x_max = int(np.max(x_max_map[r0:r1 + 1, c0:c1 + 1]))
    y_min = int(np.min(y_min_map[r0:r1 + 1, c0:c1 + 1]))
    y_max = int(np.max(y_max_map[r0:r1 + 1, c0:c1 + 1]))
    x_min, x_max = _apply_margin_1d(x_min, x_max, data["physical_shape"][1], margin_fraction)
    y_min, y_max = _apply_margin_1d(y_min, y_max, data["physical_shape"][0], margin_fraction)
    component_area_fraction = len(comp) / float(max(1, n_rows * n_cols))
    return {
        "response_map": response_map,
        "normalized_map": norm,
        "component": comp,
        "roi": {"x_min": x_min, "x_max": x_max, "y_min": y_min, "y_max": y_max},
        "component_area_fraction": component_area_fraction,
    }


def _combine_roi_candidates(
    profile_roi: dict[str, int] | None,
    block_roi: dict[str, int] | None,
    physical_shape: tuple[int, int],
    warnings: list[str],
) -> tuple[dict[str, int] | None, float]:
    if profile_roi is None and block_roi is None:
        return None, 0.0
    if profile_roi is None:
        return block_roi, 0.0
    if block_roi is None:
        return profile_roi, 0.0

    agreement = _roi_iou(profile_roi, block_roi)
    if agreement < 0.30:
        warnings.append(
            f"Bar-profile ROI and block-map ROI disagree (IoU={agreement:.3f})."
        )
        return _roi_union(profile_roi, block_roi, physical_shape), agreement

    inter = _roi_intersection(profile_roi, block_roi)
    if inter is None:
        warnings.append("Bar-profile ROI and block-map ROI do not intersect.")
        return _roi_union(profile_roi, block_roi, physical_shape), agreement
    return inter, agreement


def _moving_median(values: np.ndarray, window: int) -> np.ndarray:
    window = max(1, int(window))
    if window <= 1 or len(values) == 0:
        return values.astype(np.float64, copy=True)
    radius = window // 2
    out = np.zeros_like(values, dtype=np.float64)
    for i in range(len(values)):
        lo = max(0, i - radius)
        hi = min(len(values), i + radius + 1)
        out[i] = float(np.median(values[lo:hi]))
    return out


def _median_filter2d(values: np.ndarray, window: int) -> np.ndarray:
    window = max(1, int(window))
    if window <= 1 or values.size == 0:
        return values.astype(np.float64, copy=True)
    radius = window // 2
    padded = np.pad(values, radius, mode="edge")
    out = np.zeros_like(values, dtype=np.float64)
    for r in range(values.shape[0]):
        for c in range(values.shape[1]):
            patch = padded[r:r + window, c:c + window]
            out[r, c] = float(np.median(patch))
    return out


def _robust_normalize(values: np.ndarray) -> np.ndarray:
    vals = np.asarray(values, dtype=np.float64)
    if vals.size == 0:
        return vals
    lo = float(np.percentile(vals, 10.0))
    hi = float(np.percentile(vals, 95.0))
    if not np.isfinite(hi - lo) or hi <= lo:
        lo = float(np.min(vals))
        hi = float(np.max(vals))
    if hi <= lo:
        return np.zeros_like(vals, dtype=np.float64)
    return np.clip((vals - lo) / (hi - lo), 0.0, 1.0)


def _largest_true_interval(mask: np.ndarray) -> tuple[int, int] | None:
    best: tuple[int, int] | None = None
    best_len = 0
    start = None
    for i, value in enumerate(mask):
        if value and start is None:
            start = i
        if (not value or i == len(mask) - 1) and start is not None:
            end = i if value and i == len(mask) - 1 else i - 1
            length = end - start + 1
            if length > best_len:
                best = (start, end)
                best_len = length
            start = None
    return best


def _largest_component(mask: np.ndarray) -> list[tuple[int, int]] | None:
    if mask.size == 0:
        return None
    seen = np.zeros(mask.shape, dtype=bool)
    best: list[tuple[int, int]] = []
    for r in range(mask.shape[0]):
        for c in range(mask.shape[1]):
            if seen[r, c] or not mask[r, c]:
                continue
            comp: list[tuple[int, int]] = []
            q: deque[tuple[int, int]] = deque([(r, c)])
            seen[r, c] = True
            while q:
                cr, cc = q.popleft()
                comp.append((cr, cc))
                for nr, nc in ((cr - 1, cc), (cr + 1, cc), (cr, cc - 1), (cr, cc + 1)):
                    if nr < 0 or nc < 0 or nr >= mask.shape[0] or nc >= mask.shape[1]:
                        continue
                    if seen[nr, nc] or not mask[nr, nc]:
                        continue
                    seen[nr, nc] = True
                    q.append((nr, nc))
            if len(comp) > len(best):
                best = comp
    return best or None


def _apply_margin_1d(lo: int, hi: int, length: int, margin_fraction: float) -> tuple[int, int]:
    width = max(1, hi - lo)
    margin = int(round(width * float(margin_fraction)))
    return max(0, lo - margin), min(int(length), hi + margin)


def _roi_iou(a: dict[str, int], b: dict[str, int]) -> float:
    inter = _roi_intersection(a, b)
    if inter is None:
        return 0.0
    inter_area = _roi_area(inter)
    union_area = _roi_area(a) + _roi_area(b) - inter_area
    return float(inter_area / union_area) if union_area > 0 else 0.0


def _roi_intersection(a: dict[str, int], b: dict[str, int]) -> dict[str, int] | None:
    x0 = max(int(a["x_min"]), int(b["x_min"]))
    x1 = min(int(a["x_max"]), int(b["x_max"]))
    y0 = max(int(a["y_min"]), int(b["y_min"]))
    y1 = min(int(a["y_max"]), int(b["y_max"]))
    if x1 <= x0 or y1 <= y0:
        return None
    return {"x_min": x0, "x_max": x1, "y_min": y0, "y_max": y1}


def _roi_union(
    a: dict[str, int],
    b: dict[str, int],
    physical_shape: tuple[int, int],
) -> dict[str, int]:
    h, w = physical_shape
    return {
        "x_min": max(0, min(int(a["x_min"]), int(b["x_min"]))),
        "x_max": min(int(w), max(int(a["x_max"]), int(b["x_max"]))),
        "y_min": max(0, min(int(a["y_min"]), int(b["y_min"]))),
        "y_max": min(int(h), max(int(a["y_max"]), int(b["y_max"]))),
    }


def _roi_area(roi: dict[str, int]) -> int:
    return max(0, int(roi["x_max"]) - int(roi["x_min"])) * max(
        0, int(roi["y_max"]) - int(roi["y_min"])
    )


def _response_contrast(responses: np.ndarray) -> float:
    vals = np.asarray(responses, dtype=np.float64)
    if vals.size == 0:
        return 0.0
    positive = vals[vals > 0]
    if positive.size >= 3:
        lo = float(np.percentile(positive, 10.0))
        hi = float(np.percentile(positive, 90.0))
        scale = max(abs(lo), 1e-9)
        return max(0.0, (hi - lo) / scale)
    lo = float(np.percentile(vals, 10.0))
    hi = float(np.percentile(vals, 95.0))
    scale = max(abs(lo), 1e-9)
    return max(0.0, (hi - lo) / scale)


def _confidence_level(
    roi: dict[str, int] | None,
    contrast: float,
    agreement: float,
    component_area_fraction: float,
    warnings: list[str],
) -> str:
    if roi is None:
        return "failed"
    if contrast < 0.05:
        warnings.append("Low response contrast across scan masks.")
        return "low"
    if any("disagree" in w or "No " in w for w in warnings):
        return "low"
    if agreement >= 0.70 and contrast >= 2.0 and component_area_fraction > 0.05:
        return "high"
    return "medium"


def _write_profile_csv(path: Path, profile: dict[str, Any]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "index",
                "coord",
                "response",
                "smoothed_response",
                "normalized_response",
                "above_threshold",
            ],
        )
        writer.writeheader()
        for row in profile.get("rows", []):
            writer.writerow(row)


def _write_response_map_png(path: Path, response_map: np.ndarray) -> None:
    arr = np.asarray(response_map, dtype=np.float64)
    if arr.size == 0:
        img = np.zeros((1, 1), dtype=np.uint8)
    else:
        norm = _robust_normalize(np.nan_to_num(arr, nan=0.0))
        img = (norm * 255.0).astype(np.uint8)
    Image.fromarray(img, mode="L").save(path)


def _write_report(
    path: Path,
    result: dict[str, Any],
    x_profile: dict[str, Any],
    y_profile: dict[str, Any],
    block_result: dict[str, Any],
) -> None:
    lines = [
        "# Pupil Scan Report",
        "",
        f"- source_raw_capture_h5: `{result['source_raw_capture_h5']}`",
        f"- capture_plan_id: `{result['capture_plan_id']}`",
        f"- method: `{result['method']}`",
        f"- confidence: `{result['confidence']['level']}`",
        f"- roi_physical: `{result['roi_physical']}`",
        "",
        "## Warnings",
    ]
    warnings = result["confidence"].get("warnings", [])
    if warnings:
        lines.extend([f"- {w}" for w in warnings])
    else:
        lines.append("- none")
    lines.extend(
        [
            "",
            "## Inputs",
            f"- x_profile_available: {x_profile.get('available')}",
            f"- y_profile_available: {y_profile.get('available')}",
            f"- response_map_shape: {list(np.asarray(block_result.get('response_map')).shape)}",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _read_str_array(dataset: h5py.Dataset) -> list[str]:
    values = dataset[()]
    return [_decode_str(v) for v in values]


def _read_scalar_str(dataset: h5py.Dataset) -> str:
    return _decode_str(dataset[()])


def _json_dataset(dataset: h5py.Dataset) -> dict[str, Any]:
    text = _read_scalar_str(dataset)
    if not text:
        return {}
    return json.loads(text)


def _decode_str(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8")
    if isinstance(value, np.bytes_):
        return bytes(value).decode("utf-8")
    return str(value)


def _finite_or_none(value: float) -> float | None:
    return float(value) if np.isfinite(value) else None


def _physical_shape_from_scan(y_max: np.ndarray, x_max: np.ndarray) -> tuple[int, int]:
    return int(np.max(y_max)), int(np.max(x_max))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Analyze Phase 3.1 pupil scan raw HDF5",
    )
    parser.add_argument("--input", required=True, help="Raw pupil scan HDF5")
    parser.add_argument("--output-dir", default="outputs/pupil_scan")
    parser.add_argument("--threshold-fraction", type=float, default=0.5)
    parser.add_argument("--margin-fraction", type=float, default=0.05)
    parser.add_argument("--smooth-window", type=int, default=5)
    parser.add_argument("--min-component-size", type=int, default=3)
    parser.add_argument(
        "--spatial-stride",
        type=int,
        default=4,
        help="Subsample camera frames by this stride for robust response analysis",
    )
    args = parser.parse_args()

    result = analyze_pupil_scan(
        args.input,
        args.output_dir,
        threshold_fraction=args.threshold_fraction,
        margin_fraction=args.margin_fraction,
        smooth_window=args.smooth_window,
        min_component_size=args.min_component_size,
        spatial_stride=args.spatial_stride,
    )
    out = Path(args.output_dir) / "effective_lcd_roi.json"
    print(f"effective LCD ROI written to {out}")
    print(f"confidence: {result['confidence']['level']}")


if __name__ == "__main__":
    main()
