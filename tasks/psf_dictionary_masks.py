from __future__ import annotations

import math
from typing import Any

import numpy as np


def generate_psf_dictionary_masks(
    *,
    lowres_shape: tuple[int, int],
    physical_shape: tuple[int, int],
    pupil_window: dict[str, Any],
    subpixel_axis: int,
    include: list[str],
    random_lowfreq_count: int,
    random_midfreq_count: int,
    task_related_count: int,
    random_seed: int,
    bg_code: int = 0,
    open_code: int = 255,
) -> list[dict[str, Any]]:
    masks: list[dict[str, Any]] = []
    seen: set[str] = set()
    rng = np.random.default_rng(int(random_seed))

    for name in include:
        mask_id = str(name)
        family, lowres = _deterministic_mask(mask_id, lowres_shape)
        masks.append(
            _build_mask_record(
                mask_id=mask_id,
                mask_family=family,
                lowres=lowres,
                physical_shape=physical_shape,
                pupil_window=pupil_window,
                subpixel_axis=subpixel_axis,
                bg_code=bg_code,
                open_code=open_code,
            )
        )
        seen.add(mask_id)

    for i in range(int(random_lowfreq_count)):
        mask_id = f"random_lowfreq_{i + 1:03d}"
        if mask_id in seen:
            continue
        lowres = _random_frequency_mask(lowres_shape, rng=rng, coarse_shape=(8, 8))
        masks.append(
            _build_mask_record(
                mask_id=mask_id,
                mask_family="random_lowfreq",
                lowres=lowres,
                physical_shape=physical_shape,
                pupil_window=pupil_window,
                subpixel_axis=subpixel_axis,
                bg_code=bg_code,
                open_code=open_code,
            )
        )
        seen.add(mask_id)

    for i in range(int(random_midfreq_count)):
        mask_id = f"random_midfreq_{i + 1:03d}"
        if mask_id in seen:
            continue
        lowres = _random_frequency_mask(lowres_shape, rng=rng, coarse_shape=(16, 16))
        masks.append(
            _build_mask_record(
                mask_id=mask_id,
                mask_family="random_midfreq",
                lowres=lowres,
                physical_shape=physical_shape,
                pupil_window=pupil_window,
                subpixel_axis=subpixel_axis,
                bg_code=bg_code,
                open_code=open_code,
            )
        )
        seen.add(mask_id)

    for i in range(int(task_related_count)):
        mask_id = f"task_related_{i + 1:03d}"
        if mask_id in seen:
            continue
        lowres = _task_related_mask(lowres_shape, variant_index=i)
        masks.append(
            _build_mask_record(
                mask_id=mask_id,
                mask_family="task_related",
                lowres=lowres,
                physical_shape=physical_shape,
                pupil_window=pupil_window,
                subpixel_axis=subpixel_axis,
                bg_code=bg_code,
                open_code=open_code,
            )
        )
        seen.add(mask_id)
    return masks


def lowres_mask_to_physical_mask(
    lowres_mask: np.ndarray,
    *,
    physical_shape: tuple[int, int],
    pupil_window: dict[str, Any],
    bg_code: int = 0,
    open_code: int = 255,
) -> np.ndarray:
    lowres = np.asarray(lowres_mask, dtype=np.uint8)
    if lowres.ndim == 3:
        if lowres.shape[0] != 1:
            raise ValueError(f"lowres_mask must have channel size 1, got {lowres.shape}")
        lowres = lowres[0]
    if lowres.ndim != 2:
        raise ValueError(f"lowres_mask must be 2D or [1,H,W], got {lowres.shape}")
    h, w = int(physical_shape[0]), int(physical_shape[1])
    out = np.full((h, w), int(bg_code), dtype=np.uint8)
    inside = _physical_pupil_inside_mask(physical_shape, pupil_window)
    x_min, x_max, y_min, y_max = _pupil_bounds(physical_shape, pupil_window)
    region_h = max(1, y_max - y_min)
    region_w = max(1, x_max - x_min)
    y_idx = np.floor(np.arange(region_h) * (lowres.shape[0] / region_h)).astype(np.int32)
    x_idx = np.floor(np.arange(region_w) * (lowres.shape[1] / region_w)).astype(np.int32)
    y_idx = np.clip(y_idx, 0, lowres.shape[0] - 1)
    x_idx = np.clip(x_idx, 0, lowres.shape[1] - 1)
    upsampled = lowres[y_idx[:, None], x_idx[None, :]]
    local_inside = inside[y_min:y_max, x_min:x_max]
    out[y_min:y_max, x_min:x_max][local_inside & (upsampled > 0)] = int(open_code)
    return out


def _build_mask_record(
    *,
    mask_id: str,
    mask_family: str,
    lowres: np.ndarray,
    physical_shape: tuple[int, int],
    pupil_window: dict[str, Any],
    subpixel_axis: int,
    bg_code: int,
    open_code: int,
) -> dict[str, Any]:
    lowres_u8 = (np.asarray(lowres, dtype=np.float64) > 0.5).astype(np.uint8) * int(open_code)
    lowres_u8 = lowres_u8[np.newaxis, :, :]
    physical = lowres_mask_to_physical_mask(
        lowres_u8,
        physical_shape=physical_shape,
        pupil_window=pupil_window,
        bg_code=bg_code,
        open_code=open_code,
    )
    meta = {
        "mask_id": str(mask_id),
        "mask_family": str(mask_family),
        "lowres_shape": [int(lowres_u8.shape[1]), int(lowres_u8.shape[2])],
        "physical_shape": [int(physical_shape[0]), int(physical_shape[1])],
        "subpixel_axis": int(subpixel_axis),
        "upsampling": "nearest_block",
        "outside_effective_pupil": "opaque",
        "inside_effective_pupil": "encoded_pattern",
        "code_min": int(bg_code),
        "code_max": int(open_code),
        "pupil_window_limited": True,
    }
    return {
        "mask_id": str(mask_id),
        "mask_family": str(mask_family),
        "lowres_mask": lowres_u8,
        "physical_mask": physical,
        "mask_metadata": meta,
    }


def _deterministic_mask(mask_id: str, lowres_shape: tuple[int, int]) -> tuple[str, np.ndarray]:
    h, w = int(lowres_shape[0]), int(lowres_shape[1])
    yy, xx = np.mgrid[:h, :w]
    cx = (w - 1) / 2.0
    cy = (h - 1) / 2.0
    rx = max(6, w // 6)
    ry = max(6, h // 6)
    period = max(4, w // 8)
    if mask_id == "all_open_window":
        return "deterministic", np.ones((h, w), dtype=np.uint8)
    if mask_id == "all_closed_window":
        return "deterministic", np.zeros((h, w), dtype=np.uint8)
    if mask_id == "vertical_stripes_lowfreq":
        return "deterministic", ((((xx // period) % 2) == 0)).astype(np.uint8)
    if mask_id == "horizontal_stripes_lowfreq":
        return "deterministic", ((((yy // period) % 2) == 0)).astype(np.uint8)
    if mask_id == "checkerboard_lowfreq":
        return "deterministic", ((((xx // period) + (yy // period)) % 2) == 0).astype(np.uint8)
    if mask_id == "central_block":
        return "deterministic", ((np.abs(xx - cx) <= rx) & (np.abs(yy - cy) <= ry)).astype(np.uint8)
    if mask_id == "edge_block_left":
        base = np.ones((h, w), dtype=np.uint8)
        base[:, : max(2, w // 8)] = 0
        return "deterministic", base
    if mask_id == "edge_block_right":
        base = np.ones((h, w), dtype=np.uint8)
        base[:, w - max(2, w // 8) :] = 0
        return "deterministic", base
    if mask_id == "edge_block_top":
        base = np.ones((h, w), dtype=np.uint8)
        base[: max(2, h // 8), :] = 0
        return "deterministic", base
    if mask_id == "edge_block_bottom":
        base = np.ones((h, w), dtype=np.uint8)
        base[h - max(2, h // 8) :, :] = 0
        return "deterministic", base
    if mask_id == "annular_pattern":
        rr = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2)
        r0 = min(h, w) * 0.18
        r1 = min(h, w) * 0.34
        return "deterministic", ((rr >= r0) & (rr <= r1)).astype(np.uint8)
    raise ValueError(f"unknown deterministic PSF dictionary mask id: {mask_id}")


def _random_frequency_mask(
    lowres_shape: tuple[int, int],
    *,
    rng: np.random.Generator,
    coarse_shape: tuple[int, int],
) -> np.ndarray:
    coarse = (rng.random(coarse_shape) > 0.5).astype(np.uint8)
    h, w = int(lowres_shape[0]), int(lowres_shape[1])
    rep_y = int(math.ceil(h / coarse_shape[0]))
    rep_x = int(math.ceil(w / coarse_shape[1]))
    up = np.repeat(np.repeat(coarse, rep_y, axis=0), rep_x, axis=1)[:h, :w]
    if not np.any(up):
        up[h // 2, w // 2] = 1
    return up


def _task_related_mask(lowres_shape: tuple[int, int], *, variant_index: int) -> np.ndarray:
    h, w = int(lowres_shape[0]), int(lowres_shape[1])
    yy, xx = np.mgrid[:h, :w]
    variant = int(variant_index) % 4
    if variant == 0:
        return ((xx + yy) >= (h + w) * 0.5).astype(np.uint8)
    if variant == 1:
        return (np.abs(xx - yy) <= max(1, min(h, w) // 10)).astype(np.uint8)
    if variant == 2:
        return (((xx > w * 0.25) & (xx < w * 0.75)) | ((yy > h * 0.25) & (yy < h * 0.75))).astype(np.uint8)
    return ((((xx // max(2, w // 12)) % 2) == 0) & (yy >= h * 0.33) & (yy <= h * 0.67)).astype(np.uint8)


def _physical_pupil_inside_mask(physical_shape: tuple[int, int], pupil_window: dict[str, Any]) -> np.ndarray:
    h, w = int(physical_shape[0]), int(physical_shape[1])
    cx = float(pupil_window["center"]["x"])
    cy = float(pupil_window["center"]["y"])
    radius = float(pupil_window["radius"])
    yy, xx = np.mgrid[:h, :w]
    return (xx - cx) ** 2 + (yy - cy) ** 2 <= radius ** 2


def _pupil_bounds(physical_shape: tuple[int, int], pupil_window: dict[str, Any]) -> tuple[int, int, int, int]:
    h, w = int(physical_shape[0]), int(physical_shape[1])
    cx = float(pupil_window["center"]["x"])
    cy = float(pupil_window["center"]["y"])
    radius = float(pupil_window["radius"])
    x_min = max(0, int(math.floor(cx - radius)))
    x_max = min(w, int(math.ceil(cx + radius + 1)))
    y_min = max(0, int(math.floor(cy - radius)))
    y_max = min(h, int(math.ceil(cy + radius + 1)))
    return x_min, x_max, y_min, y_max
