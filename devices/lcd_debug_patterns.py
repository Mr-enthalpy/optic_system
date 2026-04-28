from __future__ import annotations

import numpy as np


def make_all_transmissive(
    height: int,
    width_phys: int,
    transmissive_code: int,
) -> np.ndarray:
    return np.full((height, width_phys), transmissive_code, dtype=np.uint8)


def make_all_opaque(
    height: int,
    width_phys: int,
    opaque_code: int,
) -> np.ndarray:
    return np.full((height, width_phys), opaque_code, dtype=np.uint8)


def make_center_cross(
    height: int,
    width_phys: int,
    transmissive_code: int,
    opaque_code: int,
    thickness: int | None = None,
) -> np.ndarray:
    mask = make_all_opaque(height, width_phys, opaque_code)
    thickness = thickness or max(2, min(height, width_phys) // 128)
    cy = height // 2
    cx = width_phys // 2
    half = max(1, thickness // 2)
    mask[max(0, cy - half): min(height, cy + half + 1), :] = transmissive_code
    mask[:, max(0, cx - half): min(width_phys, cx + half + 1)] = transmissive_code
    return mask


def make_vertical_bars(
    height: int,
    width_phys: int,
    transmissive_code: int,
    opaque_code: int,
    bar_width: int = 12,
) -> np.ndarray:
    mask = make_all_opaque(height, width_phys, opaque_code)
    for x0 in range(0, width_phys, bar_width * 2):
        x1 = min(width_phys, x0 + bar_width)
        mask[:, x0:x1] = transmissive_code
    return mask


def make_horizontal_bars(
    height: int,
    width_phys: int,
    transmissive_code: int,
    opaque_code: int,
    bar_height: int = 24,
) -> np.ndarray:
    mask = make_all_opaque(height, width_phys, opaque_code)
    for y0 in range(0, height, bar_height * 2):
        y1 = min(height, y0 + bar_height)
        mask[y0:y1, :] = transmissive_code
    return mask


def make_corner_markers(
    height: int,
    width_phys: int,
    transmissive_code: int,
    opaque_code: int,
    marker_size: int | None = None,
    thickness: int | None = None,
) -> np.ndarray:
    mask = make_all_opaque(height, width_phys, opaque_code)
    marker_size = marker_size or max(8, min(height, width_phys) // 12)
    thickness = thickness or max(2, marker_size // 8)

    def draw_corner(y0: int, x0: int, y_dir: int, x_dir: int) -> None:
        y1 = y0 + y_dir * marker_size
        x1 = x0 + x_dir * marker_size
        ys = slice(min(y0, y1), max(y0, y1))
        xs = slice(min(x0, x1), max(x0, x1))
        if y_dir > 0:
            mask[y0:y0 + thickness, xs] = transmissive_code
        else:
            mask[y0 - thickness:y0, xs] = transmissive_code
        if x_dir > 0:
            mask[ys, x0:x0 + thickness] = transmissive_code
        else:
            mask[ys, x0 - thickness:x0] = transmissive_code

    draw_corner(0, 0, 1, 1)
    draw_corner(0, width_phys, 1, -1)
    draw_corner(height, 0, -1, 1)
    draw_corner(height, width_phys, -1, -1)
    return mask


def make_checkerboard(
    height: int,
    width_phys: int,
    transmissive_code: int,
    opaque_code: int,
    cell_size: int = 48,
) -> np.ndarray:
    yy, xx = np.indices((height, width_phys))
    cells = ((yy // cell_size) + (xx // cell_size)) % 2
    return np.where(cells == 0, transmissive_code, opaque_code).astype(np.uint8)


def make_subpixel_stripes(
    height: int,
    width_phys: int,
    transmissive_code: int,
    opaque_code: int,
) -> np.ndarray:
    mask = make_all_opaque(height, width_phys, opaque_code)
    mask[:, 0::2] = transmissive_code
    return mask


def build_debug_pattern(
    pattern_name: str,
    *,
    height: int,
    width_phys: int,
    transmissive_code: int,
    opaque_code: int,
) -> np.ndarray:
    name = pattern_name.strip().lower()
    if name == "all_transmissive":
        return make_all_transmissive(height, width_phys, transmissive_code)
    if name == "all_opaque":
        return make_all_opaque(height, width_phys, opaque_code)
    if name == "center_cross":
        return make_center_cross(height, width_phys, transmissive_code, opaque_code)
    if name == "vertical_bars":
        return make_vertical_bars(height, width_phys, transmissive_code, opaque_code)
    if name == "horizontal_bars":
        return make_horizontal_bars(height, width_phys, transmissive_code, opaque_code)
    if name == "corner_markers":
        return make_corner_markers(height, width_phys, transmissive_code, opaque_code)
    if name == "checkerboard":
        return make_checkerboard(height, width_phys, transmissive_code, opaque_code)
    if name == "subpixel_stripes":
        return make_subpixel_stripes(height, width_phys, transmissive_code, opaque_code)
    raise ValueError(f"Unknown LCD debug pattern {pattern_name!r}")
