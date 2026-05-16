from __future__ import annotations

from typing import Any

import numpy as np


class PupilGeometryMaskError(ValueError):
    pass


def solid_mask(physical_shape: tuple[int, int], code: int) -> np.ndarray:
    h, w = _shape(physical_shape)
    return np.full((h, w), _code(code), dtype=np.uint8)


def vertical_bar_mask(
    physical_shape: tuple[int, int],
    *,
    x0: int,
    width: int,
    bg_code: int,
    bar_code: int,
) -> np.ndarray:
    h, w = _shape(physical_shape)
    x_start = max(0, int(x0))
    x_end = min(w, int(x0) + int(width))
    mask = solid_mask((h, w), bg_code)
    if x_end > x_start:
        mask[:, x_start:x_end] = _code(bar_code)
    return mask


def horizontal_bar_mask(
    physical_shape: tuple[int, int],
    *,
    y0: int,
    width: int,
    bg_code: int,
    bar_code: int,
) -> np.ndarray:
    h, w = _shape(physical_shape)
    y_start = max(0, int(y0))
    y_end = min(h, int(y0) + int(width))
    mask = solid_mask((h, w), bg_code)
    if y_end > y_start:
        mask[y_start:y_end, :] = _code(bar_code)
    return mask


def circular_window_mask(
    physical_shape: tuple[int, int],
    *,
    center: tuple[float, float],
    radius: float,
    bg_code: int,
    aperture_code: int,
) -> np.ndarray:
    h, w = _shape(physical_shape)
    xc, yc = float(center[0]), float(center[1])
    yy, xx = np.mgrid[:h, :w]
    inside = (xx - xc) ** 2 + (yy - yc) ** 2 <= float(radius) ** 2
    mask = solid_mask((h, w), bg_code)
    mask[inside] = _code(aperture_code)
    return mask


def elliptical_window_mask(
    physical_shape: tuple[int, int],
    *,
    center: tuple[float, float],
    a: float,
    b: float,
    bg_code: int,
    aperture_code: int,
    rotate_angle_deg: float = 0.0,
) -> np.ndarray:
    h, w = _shape(physical_shape)
    xc, yc = float(center[0]), float(center[1])
    angle = np.deg2rad(float(rotate_angle_deg))
    cos_a = float(np.cos(angle))
    sin_a = float(np.sin(angle))
    yy, xx = np.mgrid[:h, :w]
    x_centered = xx.astype(np.float64) - xc
    y_centered = yy.astype(np.float64) - yc
    x_rot = x_centered * cos_a + y_centered * sin_a
    y_rot = -x_centered * sin_a + y_centered * cos_a
    inside = (x_rot ** 2) / max(float(a) ** 2, 1e-12) + (y_rot ** 2) / max(float(b) ** 2, 1e-12) <= 1.0
    mask = solid_mask((h, w), bg_code)
    mask[inside] = _code(aperture_code)
    return mask


def solid_metadata(
    *,
    mask_id: str,
    physical_shape: tuple[int, int],
    subpixel_axis: int,
    code: int,
) -> dict[str, Any]:
    h, w = _shape(physical_shape)
    return {
        "mask_id": str(mask_id),
        "mask_type": "solid",
        "code": _code(code),
        "x_min": 0,
        "x_max": w,
        "y_min": 0,
        "y_max": h,
        "physical_shape": [h, w],
        "subpixel_axis": int(subpixel_axis),
    }


def bar_metadata(
    *,
    mask_id: str,
    axis: str,
    position: float,
    start: int,
    width: int,
    bg_code: int,
    bar_code: int,
    physical_shape: tuple[int, int],
    subpixel_axis: int,
) -> dict[str, Any]:
    h, w = _shape(physical_shape)
    axis = str(axis)
    if axis == "x":
        x_min, x_max = max(0, int(start)), min(w, int(start) + int(width))
        y_min, y_max = 0, h
    elif axis == "y":
        x_min, x_max = 0, w
        y_min, y_max = max(0, int(start)), min(h, int(start) + int(width))
    else:
        raise PupilGeometryMaskError("bar axis must be 'x' or 'y'")
    return {
        "mask_id": str(mask_id),
        "mask_type": "dark_bar",
        "axis": axis,
        "position": float(position),
        "bar_width": int(width),
        "bg_code": _code(bg_code),
        "bar_code": _code(bar_code),
        "x_min": x_min,
        "x_max": x_max,
        "y_min": y_min,
        "y_max": y_max,
        "physical_shape": [h, w],
        "subpixel_axis": int(subpixel_axis),
    }


def circular_window_metadata(
    *,
    mask_id: str,
    center: tuple[float, float],
    radius: float,
    bg_code: int,
    aperture_code: int,
    physical_shape: tuple[int, int],
    subpixel_axis: int,
) -> dict[str, Any]:
    h, w = _shape(physical_shape)
    xc, yc = float(center[0]), float(center[1])
    r = float(radius)
    return {
        "mask_id": str(mask_id),
        "mask_type": "circular_window",
        "center": [xc, yc],
        "radius": r,
        "bg_code": _code(bg_code),
        "aperture_code": _code(aperture_code),
        "x_min": max(0, int(np.floor(xc - r))),
        "x_max": min(w, int(np.ceil(xc + r + 1))),
        "y_min": max(0, int(np.floor(yc - r))),
        "y_max": min(h, int(np.ceil(yc + r + 1))),
        "physical_shape": [h, w],
        "subpixel_axis": int(subpixel_axis),
    }


def _shape(physical_shape: tuple[int, int]) -> tuple[int, int]:
    if len(physical_shape) != 2:
        raise PupilGeometryMaskError(f"physical_shape must be [H, W], got {physical_shape}")
    h, w = int(physical_shape[0]), int(physical_shape[1])
    if h <= 0 or w <= 0:
        raise PupilGeometryMaskError(f"physical_shape must be positive, got {physical_shape}")
    return h, w


def _code(value: int) -> int:
    code = int(value)
    if code < 0 or code > 255:
        raise PupilGeometryMaskError(f"mask code must be in [0, 255], got {value}")
    return code
