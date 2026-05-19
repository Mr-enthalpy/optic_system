from __future__ import annotations

import json
import math
import time
from pathlib import Path
from typing import Any

import h5py
import numpy as np
import yaml

from tasks.pupil_geometry_masks import circular_window_mask, circular_window_metadata


class Phase32PlanError(ValueError):
    pass


def now_ns() -> int:
    return time.monotonic_ns()


def json_dumps(value: Any) -> str:
    return json.dumps(value, indent=2, default=_json_default)


def load_yaml_plan(path: str | Path) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as f:
        plan = yaml.safe_load(f)
    if not isinstance(plan, dict):
        raise Phase32PlanError(f"plan must be a YAML mapping: {path}")
    return plan


def validate_phase32_plan(
    plan: dict[str, Any],
    *,
    task: str,
    hardware: bool = False,
    allow_unsafe_lcd_settle: bool = False,
) -> None:
    required = ["plan_id", "phase", "camera_params_source", "pupil_window_source", "lcd", "capture", "output"]
    if task in {"dictionary", "target_capture"}:
        required.append("wavelengths")
    else:
        required.append("wavelength")
    for key in required:
        if not plan.get(key):
            raise Phase32PlanError(f"{key} is required")
    if task == "roi":
        if plan.get("phase") != "3.2a":
            raise Phase32PlanError("PSF ROI plan phase must be '3.2a'")
        if not plan.get("psf_roi"):
            raise Phase32PlanError("psf_roi section is required")
        roi_cfg = plan.get("psf_roi", {})
        candidate_sizes = roi_cfg.get("candidate_crop_sizes")
        if candidate_sizes is not None:
            if not isinstance(candidate_sizes, list) or not candidate_sizes:
                raise Phase32PlanError("psf_roi.candidate_crop_sizes must be a non-empty list")
            for item in candidate_sizes:
                if not isinstance(item, list) or len(item) != 2:
                    raise Phase32PlanError("each psf_roi.candidate_crop_sizes entry must be [H, W]")
                if int(item[0]) <= 0 or int(item[1]) <= 0:
                    raise Phase32PlanError("psf_roi candidate crop sizes must be > 0")
        else:
            crop_size = roi_cfg.get("crop_size")
            if not isinstance(crop_size, list) or len(crop_size) != 2:
                raise Phase32PlanError("psf_roi.crop_size must be [H, W]")
            if int(crop_size[0]) <= 0 or int(crop_size[1]) <= 0:
                raise Phase32PlanError("psf_roi.crop_size entries must be > 0")
        repeats = int(plan.get("capture", {}).get("repeats", 0))
        if repeats <= 0:
            raise Phase32PlanError("capture.repeats must be > 0")
    elif task == "repeatability":
        if plan.get("phase") != "3.2b":
            raise Phase32PlanError("PSF repeatability plan phase must be '3.2b'")
        if not plan.get("psf_roi_source"):
            raise Phase32PlanError("psf_roi_source is required")
        if not plan.get("masks", {}).get("include"):
            raise Phase32PlanError("masks.include must be non-empty")
        repeats = int(plan.get("capture", {}).get("repeats_per_mask", 0))
        if repeats <= 0:
            raise Phase32PlanError("capture.repeats_per_mask must be > 0")
    elif task == "dotf":
        if plan.get("phase") != "3.3":
            raise Phase32PlanError("dOTF diagnostic plan phase must be '3.3'")
        if not plan.get("psf_roi_source"):
            raise Phase32PlanError("psf_roi_source is required")
        perturbation_set = plan.get("dotf", {}).get("perturbation_set")
        if not isinstance(perturbation_set, list) or not perturbation_set:
            raise Phase32PlanError("dotf.perturbation_set must be a non-empty list")
        roi_keys = plan.get("dotf", {}).get("roi_keys")
        if roi_keys is not None:
            if not isinstance(roi_keys, list) or not roi_keys:
                raise Phase32PlanError("dotf.roi_keys must be a non-empty list when provided")
            if not all(isinstance(item, str) and item.strip() for item in roi_keys):
                raise Phase32PlanError("dotf.roi_keys entries must be non-empty strings")
        repeats = int(plan.get("capture", {}).get("repeats", 0))
        if repeats <= 0:
            raise Phase32PlanError("capture.repeats must be > 0")
        perturbation = plan.get("dotf", {}).get("perturbation", {})
        if str(perturbation.get("type", "")) != "local_edge_occlusion":
            raise Phase32PlanError("dotf.perturbation.type must be local_edge_occlusion")
        if int(perturbation.get("size_px", 0)) <= 0:
            raise Phase32PlanError("dotf.perturbation.size_px must be > 0")
        edge_energy = plan.get("dotf", {}).get("edge_energy", {})
        if edge_energy.get("enabled"):
            if int(edge_energy.get("edge_band_px", 0)) <= 0:
                raise Phase32PlanError("dotf.edge_energy.edge_band_px must be > 0 when enabled")
    elif task == "dictionary":
        if plan.get("phase") != "3.4":
            raise Phase32PlanError("PSF dictionary plan phase must be '3.4'")
        if not plan.get("psf_roi_source"):
            raise Phase32PlanError("psf_roi_source is required")
        if not isinstance(plan.get("psf_roi_key"), str) or not str(plan.get("psf_roi_key")).strip():
            raise Phase32PlanError("psf_roi_key is required for Phase 3.4")
        wavelengths = plan.get("wavelengths")
        if not isinstance(wavelengths, list) or not wavelengths:
            raise Phase32PlanError("wavelengths must be a non-empty list")
        masks = plan.get("masks", {})
        if str(masks.get("set", "")) != "psf_dictionary_representative":
            raise Phase32PlanError("masks.set must be psf_dictionary_representative")
        include = masks.get("include")
        if not isinstance(include, list) or not include:
            raise Phase32PlanError("masks.include must be a non-empty list")
        lowres_shape = masks.get("lowres_shape")
        if not isinstance(lowres_shape, list) or len(lowres_shape) != 2:
            raise Phase32PlanError("masks.lowres_shape must be [H, W]")
        if int(lowres_shape[0]) <= 0 or int(lowres_shape[1]) <= 0:
            raise Phase32PlanError("masks.lowres_shape entries must be > 0")
        repeats = int(plan.get("capture", {}).get("repeats_per_mask", 0))
        if repeats <= 0:
            raise Phase32PlanError("capture.repeats_per_mask must be > 0")
        export = plan.get("export", {}).get("lcd_forward", {})
        if bool(export.get("enabled", False)):
            split = export.get("split", {})
            total = float(split.get("train", 0.0)) + float(split.get("val", 0.0)) + float(split.get("test", 0.0))
            if not math.isclose(total, 1.0, rel_tol=1e-6, abs_tol=1e-6):
                raise Phase32PlanError("export.lcd_forward.split must sum to 1.0")
    elif task == "target_capture":
        if plan.get("phase") != "3.6":
            raise Phase32PlanError("target capture plan phase must be '3.6'")
        if not plan.get("psf_roi_source"):
            raise Phase32PlanError("psf_roi_source is required")
        if not isinstance(plan.get("psf_roi_key"), str) or not str(plan.get("psf_roi_key")).strip():
            raise Phase32PlanError("psf_roi_key is required for Phase 3.6")
        mask_source = plan.get("mask_source", {})
        if str(mask_source.get("type", "")) != "lcd_forward_export":
            raise Phase32PlanError("mask_source.type must be lcd_forward_export")
        h5_paths = mask_source.get("h5_paths")
        if not isinstance(h5_paths, list) or not h5_paths:
            raise Phase32PlanError("mask_source.h5_paths must be a non-empty list")
        wavelengths = plan.get("wavelengths")
        if not isinstance(wavelengths, list) or not wavelengths:
            raise Phase32PlanError("wavelengths must be a non-empty list")
        frames_per_capture = int(plan.get("capture", {}).get("frames_per_capture", 0))
        if frames_per_capture <= 0:
            raise Phase32PlanError("capture.frames_per_capture must be > 0")
        repeats = int(plan.get("capture", {}).get("repeats_per_condition", 0))
        if repeats <= 0:
            raise Phase32PlanError("capture.repeats_per_condition must be > 0")
    else:
        raise Phase32PlanError(f"unknown task: {task}")
    frames_per_capture = int(plan.get("capture", {}).get("frames_per_capture", 0))
    if frames_per_capture <= 0:
        raise Phase32PlanError("capture.frames_per_capture must be > 0")
    settle_ms = float(plan.get("lcd", {}).get("settle_ms", 0))
    if hardware and settle_ms < 100.0 and not allow_unsafe_lcd_settle:
        raise Phase32PlanError("hardware Phase 3 capture requires lcd.settle_ms >= 100")


def load_json_file(path: str | Path) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return data


def load_pupil_window(path: str | Path) -> dict[str, Any]:
    data = load_json_file(path)
    if data.get("phase") != "3.1":
        raise ValueError(f"{path}: expected phase '3.1' effective pupil window")
    center = data.get("center") or {}
    if center.get("x") is None or center.get("y") is None:
        raise ValueError(f"{path}: center.x and center.y are required")
    if data.get("radius") is None:
        raise ValueError(f"{path}: radius is required")
    if data.get("physical_shape") is None:
        raise ValueError(f"{path}: physical_shape is required")
    return data


def load_psf_roi(path: str | Path) -> dict[str, Any]:
    data = load_json_file(path)
    if data.get("phase") != "3.2a":
        raise ValueError(f"{path}: expected phase '3.2a' PSF ROI")
    roi = data.get("roi") or {}
    for key in ("x_min", "x_max", "y_min", "y_max", "width", "height"):
        if roi.get(key) is None:
            raise ValueError(f"{path}: roi.{key} is required")
    rois = data.get("rois")
    if rois is not None:
        if not isinstance(rois, dict) or not rois:
            raise ValueError(f"{path}: rois must be a non-empty object when present")
        for roi_key, roi_value in rois.items():
            if not isinstance(roi_value, dict):
                raise ValueError(f"{path}: rois.{roi_key} must be an object")
            for key in ("width", "height", "fits_frame"):
                if roi_value.get(key) is None:
                    raise ValueError(f"{path}: rois.{roi_key}.{key} is required")
    return data


def resolve_psf_roi_record(
    psf_roi_json: dict[str, Any],
    roi_key: str | None,
    *,
    allow_legacy_fallback: bool = False,
) -> dict[str, Any]:
    if roi_key is not None:
        roi_key = str(roi_key).strip()
    if roi_key:
        rois = psf_roi_json.get("rois")
        if not isinstance(rois, dict) or not rois:
            raise ValueError("psf_roi JSON does not define rois; cannot resolve explicit roi_key")
        if roi_key not in rois:
            raise ValueError(f"psf_roi JSON does not contain roi_key={roi_key!r}")
        roi = rois[roi_key]
        if not isinstance(roi, dict):
            raise ValueError(f"psf_roi JSON rois.{roi_key} must be an object")
        if roi.get("fits_frame") is not True:
            raise ValueError(f"psf_roi JSON roi_key={roi_key!r} is not usable because fits_frame is not true")
        for key in ("x_min", "x_max", "y_min", "y_max", "width", "height"):
            if roi.get(key) is None:
                raise ValueError(f"psf_roi JSON rois.{roi_key}.{key} is required")
        return dict(roi)
    if allow_legacy_fallback:
        roi = psf_roi_json.get("roi")
        if not isinstance(roi, dict):
            raise ValueError("psf_roi JSON legacy top-level roi is missing")
        for key in ("x_min", "x_max", "y_min", "y_max", "width", "height"):
            if roi.get(key) is None:
                raise ValueError(f"psf_roi JSON roi.{key} is required")
        return dict(roi)
    raise ValueError("roi_key is required unless allow_legacy_fallback=True")


def pupil_window_mask(
    physical_shape: tuple[int, int],
    pupil_window: dict[str, Any],
    *,
    subpixel_axis: int,
    mask_id: str = "all_open_window",
    bg_code: int = 0,
    aperture_code: int = 255,
) -> tuple[np.ndarray, dict[str, Any]]:
    center = (float(pupil_window["center"]["x"]), float(pupil_window["center"]["y"]))
    radius = float(pupil_window["radius"])
    mask = circular_window_mask(
        physical_shape,
        center=center,
        radius=radius,
        bg_code=bg_code,
        aperture_code=aperture_code,
    )
    meta = circular_window_metadata(
        mask_id=mask_id,
        center=center,
        radius=radius,
        bg_code=bg_code,
        aperture_code=aperture_code,
        physical_shape=physical_shape,
        subpixel_axis=subpixel_axis,
    )
    meta["coordinate_system"] = "LCD physical coordinates"
    meta["pupil_window_limited"] = True
    return mask, meta


def representative_pupil_masks(
    physical_shape: tuple[int, int],
    pupil_window: dict[str, Any],
    *,
    subpixel_axis: int,
    include: list[str],
    bg_code: int = 0,
    open_code: int = 255,
) -> list[tuple[str, np.ndarray, dict[str, Any]]]:
    h, w = int(physical_shape[0]), int(physical_shape[1])
    base, base_meta = pupil_window_mask(
        physical_shape,
        pupil_window,
        subpixel_axis=subpixel_axis,
        mask_id="all_open_window",
        bg_code=bg_code,
        aperture_code=open_code,
    )
    yy, xx = np.mgrid[:h, :w]
    inside = base == open_code
    xc = float(pupil_window["center"]["x"])
    yc = float(pupil_window["center"]["y"])
    r = float(pupil_window["radius"])
    period = max(8, int(round(r / 3.0)) * 2)
    block = max(8, int(round(r / 3.0)))

    out: list[tuple[str, np.ndarray, dict[str, Any]]] = []
    for name in include:
        mask = np.full((h, w), int(bg_code), dtype=np.uint8)
        if name == "all_open_window":
            pattern = np.ones((h, w), dtype=bool)
        elif name == "vertical_stripes_lowfreq":
            pattern = (((xx - int(xc - r)) // period) % 2) == 0
        elif name == "horizontal_stripes_lowfreq":
            pattern = (((yy - int(yc - r)) // period) % 2) == 0
        elif name == "checkerboard_lowfreq":
            pattern = ((((xx - int(xc - r)) // period) + ((yy - int(yc - r)) // period)) % 2) == 0
        elif name == "central_block":
            pattern = (np.abs(xx - xc) <= 0.45 * r) & (np.abs(yy - yc) <= 0.45 * r)
        elif name == "edge_block":
            pattern = np.ones((h, w), dtype=bool)
            pattern[(xx > xc + 0.15 * r) & (yy > yc - 0.45 * r) & (yy < yc + 0.45 * r)] = False
        elif name.startswith("random_lowfreq_"):
            seed = int(name.rsplit("_", 1)[-1]) if name.rsplit("_", 1)[-1].isdigit() else 1
            rng = np.random.default_rng(seed)
            gh = int(math.ceil(h / block))
            gw = int(math.ceil(w / block))
            coarse = rng.random((gh, gw)) > 0.5
            pattern = np.repeat(np.repeat(coarse, block, axis=0), block, axis=1)[:h, :w]
        else:
            raise ValueError(f"unknown representative mask id: {name}")
        mask[inside & pattern] = int(open_code)
        meta = dict(base_meta)
        meta.update(
            {
                "mask_id": name,
                "mask_type": "representative_low_frequency",
                "pattern": name,
                "outside_effective_pupil": "opaque",
                "inside_effective_pupil": "encoded_pattern",
                "period_px": int(period),
                "block_px": int(block),
            }
        )
        out.append((name, mask, meta))
    return out


def crop_frame(frame: np.ndarray, roi: dict[str, Any]) -> np.ndarray:
    arr = np.asarray(frame)
    y0, y1 = int(roi["y_min"]), int(roi["y_max"])
    x0, x1 = int(roi["x_min"]), int(roi["x_max"])
    if y0 < 0 or x0 < 0 or y1 > arr.shape[0] or x1 > arr.shape[1] or y1 <= y0 or x1 <= x0:
        raise ValueError(f"ROI {roi} is out of bounds for frame shape {arr.shape}")
    return np.asarray(arr[y0:y1, x0:x1])


def estimate_psf_roi(
    frames_avg: np.ndarray,
    *,
    crop_size: tuple[int, int],
    center_window_radius: int = 32,
    full_scale: float | None = None,
    valid_pixel_domain: dict[str, Any] | None = None,
) -> dict[str, Any]:
    stack = np.asarray(frames_avg, dtype=np.float64)
    if stack.ndim == 2:
        mean_frame = stack
    elif stack.ndim == 3:
        mean_frame = np.mean(stack, axis=0)
    else:
        raise ValueError(f"frames_avg must be 2D or 3D, got {stack.shape}")
    if mean_frame.ndim != 2:
        raise ValueError(f"mean frame must be 2D, got {mean_frame.shape}")

    valid_mask = valid_pixel_mask(mean_frame.shape, valid_pixel_domain)
    background = float(np.percentile(mean_frame[valid_mask], 5.0))
    corrected = np.maximum(mean_frame - background, 0.0)
    peak_eval = np.where(valid_mask, corrected, -np.inf)
    peak_y, peak_x = np.unravel_index(int(np.argmax(peak_eval)), mean_frame.shape)
    radius = max(1, int(center_window_radius))
    y0 = max(0, int(peak_y) - radius)
    y1 = min(mean_frame.shape[0], int(peak_y) + radius + 1)
    x0 = max(0, int(peak_x) - radius)
    x1 = min(mean_frame.shape[1], int(peak_x) + radius + 1)
    local = corrected[y0:y1, x0:x1]
    weights = local
    if float(np.sum(weights)) <= 0.0:
        center_x, center_y = float(peak_x), float(peak_y)
    else:
        ly, lx = np.mgrid[y0:y1, x0:x1]
        total = float(np.sum(weights))
        center_x = float(np.sum(lx * weights) / total)
        center_y = float(np.sum(ly * weights) / total)

    crop_h, crop_w = int(crop_size[0]), int(crop_size[1])
    x_min = int(round(center_x - crop_w / 2.0))
    y_min = int(round(center_y - crop_h / 2.0))
    x_max = x_min + crop_w
    y_max = y_min + crop_h
    roi_exceeds = x_min < 0 or y_min < 0 or x_max > mean_frame.shape[1] or y_max > mean_frame.shape[0]
    if roi_exceeds:
        x_min = min(max(0, x_min), max(0, mean_frame.shape[1] - crop_w))
        y_min = min(max(0, y_min), max(0, mean_frame.shape[0] - crop_h))
        x_max = x_min + crop_w
        y_max = y_min + crop_h
    roi = {
        "x_min": int(x_min),
        "x_max": int(x_max),
        "y_min": int(y_min),
        "y_max": int(y_max),
        "width": int(crop_w),
        "height": int(crop_h),
    }
    roi_energy = float(np.sum(corrected[y_min:y_max, x_min:x_max]))
    total_energy = float(np.sum(corrected[valid_mask]))
    full_scale_in_avg_valid_domain = False
    if full_scale is not None:
        full_scale_in_avg_valid_domain = bool(np.any(mean_frame[valid_mask] >= float(full_scale)))
    return {
        "mean_frame": mean_frame,
        "center": {"x": center_x, "y": center_y, "method": "peak_then_center_of_mass"},
        "roi": roi,
        "quality": {
            "peak_pixel": float(np.max(mean_frame[valid_mask])),
            "mean_pixel": float(np.mean(mean_frame[valid_mask])),
            "background_level": background,
            "roi_energy_fraction": float(roi_energy / total_energy) if total_energy > 0 else 0.0,
            "full_scale_in_avg_valid_domain": full_scale_in_avg_valid_domain,
            "peak_pixel_x": int(peak_x),
            "peak_pixel_y": int(peak_y),
            "roi_exceeds_frame_before_clamp": bool(roi_exceeds),
        },
    }


def valid_pixel_mask(shape: tuple[int, int], valid_pixel_domain: dict[str, Any] | None) -> np.ndarray:
    mask = np.ones((int(shape[0]), int(shape[1])), dtype=bool)
    if not valid_pixel_domain:
        return mask
    if valid_pixel_domain.get("type") == "exclude_top_rows":
        top_rows = int(valid_pixel_domain.get("top_rows", 0))
        if top_rows > 0:
            mask[:top_rows, :] = False
    return mask


def normalized_correlation(a: np.ndarray, b: np.ndarray) -> float:
    aa = np.asarray(a, dtype=np.float64).ravel()
    bb = np.asarray(b, dtype=np.float64).ravel()
    aa = aa - float(np.mean(aa))
    bb = bb - float(np.mean(bb))
    denom = float(np.linalg.norm(aa) * np.linalg.norm(bb))
    return float(np.dot(aa, bb) / denom) if denom > 0 else 0.0


def mse(a: np.ndarray, b: np.ndarray) -> float:
    d = np.asarray(a, dtype=np.float64) - np.asarray(b, dtype=np.float64)
    return float(np.mean(d * d))


def psnr(a: np.ndarray, b: np.ndarray, data_range: float | None = None) -> float:
    err = mse(a, b)
    if err <= 0:
        return float("inf")
    if data_range is None:
        arr = np.asarray([np.max(a), np.max(b), np.min(a), np.min(b)], dtype=np.float64)
        data_range = float(np.max(arr[:2]) - np.min(arr[2:]))
    data_range = max(float(data_range), 1e-12)
    return float(20.0 * math.log10(data_range) - 10.0 * math.log10(err))


def ssim(a: np.ndarray, b: np.ndarray, data_range: float | None = None) -> float:
    try:
        from skimage.metrics import structural_similarity
    except ImportError:
        return normalized_correlation(a, b)
    aa = np.asarray(a, dtype=np.float64)
    bb = np.asarray(b, dtype=np.float64)
    if data_range is None:
        data_range = float(max(np.max(aa), np.max(bb)) - min(np.min(aa), np.min(bb)))
    return float(structural_similarity(aa, bb, data_range=max(float(data_range), 1e-12)))


def center_of_mass(frame: np.ndarray) -> tuple[float, float]:
    arr = np.maximum(np.asarray(frame, dtype=np.float64) - float(np.percentile(frame, 5.0)), 0.0)
    total = float(np.sum(arr))
    if total <= 0:
        y, x = np.unravel_index(int(np.argmax(frame)), np.asarray(frame).shape)
        return float(x), float(y)
    yy, xx = np.mgrid[: arr.shape[0], : arr.shape[1]]
    return float(np.sum(xx * arr) / total), float(np.sum(yy * arr) / total)


def analyze_repeatability_stack(crops: np.ndarray, mask_ids: list[str]) -> dict[str, Any]:
    arr = np.asarray(crops, dtype=np.float64)
    if arr.ndim != 3:
        raise ValueError(f"crops must be [N,H,W], got {arr.shape}")
    ids = [str(x) for x in mask_ids]
    unique_ids = list(dict.fromkeys(ids))
    by_mask = {mid: np.where(np.asarray(ids) == mid)[0] for mid in unique_ids}
    means = np.stack([np.mean(arr[idx], axis=0) for idx in by_mask.values()], axis=0)
    global_range = float(np.max(arr) - np.min(arr))
    global_range = max(global_range, 1e-12)

    intra: dict[str, Any] = {}
    intra_mses: list[float] = []
    for mid, idx in by_mask.items():
        local = arr[idx]
        pair_mse: list[float] = []
        pair_corr: list[float] = []
        pair_psnr: list[float] = []
        pair_ssim: list[float] = []
        for i in range(len(local)):
            for j in range(i + 1, len(local)):
                pair_mse.append(mse(local[i], local[j]))
                pair_corr.append(normalized_correlation(local[i], local[j]))
                pair_psnr.append(psnr(local[i], local[j], global_range))
                pair_ssim.append(ssim(local[i], local[j], global_range))
        centers = np.asarray([center_of_mass(x) for x in local], dtype=np.float64)
        energies = np.asarray([float(np.sum(np.maximum(x, 0.0))) for x in local], dtype=np.float64)
        mean_energy = float(np.mean(energies)) if energies.size else 0.0
        drift = np.linalg.norm(centers - np.mean(centers, axis=0), axis=1) if len(centers) else np.asarray([])
        intra[mid] = {
            "n_repeats": int(len(local)),
            "mean_mse": _mean_or_nan(pair_mse),
            "mean_normalized_correlation": _mean_or_nan(pair_corr),
            "mean_psnr": _mean_or_nan(pair_psnr),
            "mean_ssim": _mean_or_nan(pair_ssim),
            "center_drift_px_mean": _mean_or_nan(drift),
            "center_drift_px_max": float(np.max(drift)) if drift.size else float("nan"),
            "total_energy_cv": float(np.std(energies) / mean_energy) if mean_energy > 0 else float("nan"),
        }
        intra_mses.extend(pair_mse)

    n = len(unique_ids)
    distance = np.zeros((n, n), dtype=np.float64)
    corr_mat = np.eye(n, dtype=np.float64)
    psnr_mat = np.full((n, n), np.inf, dtype=np.float64)
    ssim_mat = np.eye(n, dtype=np.float64)
    fourier_distance = np.zeros((n, n), dtype=np.float64)
    for i in range(n):
        for j in range(i + 1, n):
            distance[i, j] = distance[j, i] = mse(means[i], means[j])
            corr_mat[i, j] = corr_mat[j, i] = normalized_correlation(means[i], means[j])
            psnr_mat[i, j] = psnr_mat[j, i] = psnr(means[i], means[j], global_range)
            ssim_mat[i, j] = ssim_mat[j, i] = ssim(means[i], means[j], global_range)
            fa = np.abs(np.fft.rfft2(means[i]))
            fb = np.abs(np.fft.rfft2(means[j]))
            fourier_distance[i, j] = fourier_distance[j, i] = float(np.mean((fa - fb) ** 2))

    upper = distance[np.triu_indices(n, k=1)] if n > 1 else np.asarray([])
    intra_noise = float(np.mean(intra_mses)) if intra_mses else float("nan")
    inter_distance = float(np.mean(upper)) if upper.size else float("nan")
    return {
        "mask_ids": unique_ids,
        "mask_mean_psfs": means,
        "intra": intra,
        "summary": {
            "mean_intra_mask_mse": intra_noise,
            "mean_inter_mask_mse": inter_distance,
            "inter_mask_distance_over_intra_noise": float(inter_distance / intra_noise) if intra_noise > 0 else float("inf"),
            "mask_induced_differences_larger_than_repeat_noise": bool(inter_distance > intra_noise) if np.isfinite(intra_noise) and np.isfinite(inter_distance) else False,
        },
        "pairwise_distance_matrix": distance,
        "pairwise_correlation_matrix": corr_mat,
        "psnr_matrix": psnr_mat,
        "ssim_matrix": ssim_mat,
        "fourier_magnitude_distance_matrix": fourier_distance,
    }


class Phase32RawWriter:
    def __init__(self, output_path: str | Path, *, plan_id: str, phase: str, include_crops: bool = False):
        self.path = Path(output_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.plan_id = str(plan_id)
        self.phase = str(phase)
        self.include_crops = bool(include_crops)
        self._file: h5py.File | None = None
        self._n = 0

    def open(self) -> "Phase32RawWriter":
        self._file = h5py.File(str(self.path), "w")
        string_dtype = h5py.string_dtype(encoding="utf-8")
        f = self._file
        f.attrs["plan_id"] = self.plan_id
        f.attrs["phase"] = self.phase
        f.attrs["created_at_ns"] = now_ns()
        raw = f.require_group("raw")
        raw.create_dataset("frames_avg", shape=(0, 1, 1), maxshape=(None, None, None), dtype=np.float64, chunks=(1, 128, 128), compression="gzip", compression_opts=4)
        if self.include_crops:
            raw.create_dataset("crops", shape=(0, 1, 1), maxshape=(None, None, None), dtype=np.float64, chunks=(1, 64, 64), compression="gzip", compression_opts=4)
        raw.create_dataset("mask_id", shape=(0,), maxshape=(None,), dtype=string_dtype)
        raw.create_dataset("repeat_index", shape=(0,), maxshape=(None,), dtype=np.int64)
        raw.create_dataset("timestamp_ns", shape=(0,), maxshape=(None,), dtype=np.int64)
        raw.create_dataset("mask_metadata_json", shape=(0,), maxshape=(None,), dtype=string_dtype)
        cap = f.require_group("capture")
        cap.create_dataset("plan_json", data="", dtype=string_dtype)
        cap.create_dataset("processing_flags_json", data=json_dumps(processing_flags(self.phase, False)), dtype=string_dtype)
        f.require_group("camera").create_dataset("metadata_json", data="", dtype=string_dtype)
        f.require_group("lcd").create_dataset("metadata_json", data="", dtype=string_dtype)
        f.require_group("tls").create_dataset("metadata_json", data="", dtype=string_dtype)
        prov = f.require_group("provenance")
        prov.create_dataset("pupil_window_source_json", data="", dtype=string_dtype)
        prov.create_dataset("camera_params_source_json", data="", dtype=string_dtype)
        if self.include_crops:
            prov.create_dataset("psf_roi_source_json", data="", dtype=string_dtype)
        return self

    def write_json_sections(
        self,
        *,
        plan: dict[str, Any],
        camera_metadata: dict[str, Any],
        lcd_metadata: dict[str, Any],
        tls_metadata: dict[str, Any],
        pupil_window_source: dict[str, Any],
        camera_params_source: dict[str, Any],
        psf_roi_source: dict[str, Any] | None = None,
    ) -> None:
        f = self._ensure_open()
        f["capture/plan_json"][()] = json_dumps(plan)
        f["camera/metadata_json"][()] = json_dumps(camera_metadata)
        f["lcd/metadata_json"][()] = json_dumps(lcd_metadata)
        f["tls/metadata_json"][()] = json_dumps(tls_metadata)
        f["provenance/pupil_window_source_json"][()] = json_dumps(pupil_window_source)
        f["provenance/camera_params_source_json"][()] = json_dumps(camera_params_source)
        if psf_roi_source is not None and "psf_roi_source_json" in f["provenance"]:
            f["provenance/psf_roi_source_json"][()] = json_dumps(psf_roi_source)

    def append_capture(
        self,
        *,
        frame_avg: np.ndarray,
        mask_id: str,
        repeat_index: int,
        mask_metadata: dict[str, Any],
        crop: np.ndarray | None = None,
    ) -> int:
        f = self._ensure_open()
        row = self._n
        _append_frame(f["raw/frames_avg"], row, np.asarray(frame_avg, dtype=np.float64))
        if self.include_crops:
            if crop is None:
                raise ValueError("crop is required when include_crops=True")
            _append_frame(f["raw/crops"], row, np.asarray(crop, dtype=np.float64))
        _append_scalar(f["raw/mask_id"], str(mask_id))
        _append_scalar(f["raw/repeat_index"], int(repeat_index))
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


def processing_flags(phase: str, completed: bool) -> dict[str, Any]:
    return {
        "phase": str(phase),
        "completed": bool(completed),
        "scientific_calibration_valid": False,
        "optical_alignment_validated": False,
        "training_ready": False,
    }


def write_preview_png(path: str | Path, image: np.ndarray, *, roi: dict[str, Any] | None = None) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    arr = _as_uint8_preview(np.asarray(image))
    try:
        import cv2
    except ImportError:
        np.save(str(path.with_suffix(".npy")), arr)
        return
    if arr.ndim == 2:
        out = cv2.cvtColor(arr, cv2.COLOR_GRAY2BGR)
    else:
        out = arr
    if roi is not None:
        cv2.rectangle(
            out,
            (int(roi["x_min"]), int(roi["y_min"])),
            (int(roi["x_max"]) - 1, int(roi["y_max"]) - 1),
            (0, 0, 255),
            max(1, int(round(max(out.shape[:2]) / 512))),
        )
    cv2.imwrite(str(path), out)


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


def _append_scalar(dset: h5py.Dataset, value: Any) -> None:
    n = dset.shape[0]
    dset.resize((n + 1,))
    dset[n] = value


def _mean_or_nan(values: Any) -> float:
    arr = np.asarray(values, dtype=np.float64)
    return float(np.mean(arr)) if arr.size else float("nan")


def _as_uint8_preview(array: np.ndarray) -> np.ndarray:
    arr = np.asarray(array)
    finite = arr[np.isfinite(arr)]
    if finite.size == 0:
        return np.zeros(arr.shape, dtype=np.uint8)
    lo = float(np.percentile(finite, 1.0))
    hi = float(np.percentile(finite, 99.5))
    if hi <= lo:
        return np.zeros(arr.shape, dtype=np.uint8)
    return np.clip((arr.astype(np.float64) - lo) * (255.0 / (hi - lo)), 0, 255).astype(np.uint8)


def _json_default(value: Any) -> Any:
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"object of type {type(value).__name__} is not JSON serializable")
