from __future__ import annotations

import numpy as np


class CompactDenseExportError(ValueError):
    pass


def render_peak_patch_dense_view(
    peak_patches: np.ndarray,
    *,
    patch_origin_xy: np.ndarray,
    frame_shape: tuple[int, int],
) -> np.ndarray:
    """Render peak patches into a diagnostic dense canvas using recorded coordinates."""

    patches = np.asarray(peak_patches)
    origins = np.asarray(patch_origin_xy, dtype=np.int64)
    if patches.ndim != 4:
        raise CompactDenseExportError(
            f"peak_patches must have shape [N, K, Hp, Wp], got {patches.shape}"
        )
    if origins.shape != (patches.shape[1], 2):
        raise CompactDenseExportError(
            f"patch_origin_xy must have shape [K, 2], got {origins.shape}"
        )
    h, w = int(frame_shape[0]), int(frame_shape[1])
    if h <= 0 or w <= 0:
        raise CompactDenseExportError("frame_shape must be positive")
    dense = np.zeros((patches.shape[0], h, w), dtype=patches.dtype)
    for peak_idx, origin in enumerate(origins):
        x0, y0 = int(origin[0]), int(origin[1])
        ph, pw = patches.shape[2], patches.shape[3]
        if x0 < 0 or y0 < 0 or x0 + pw > w or y0 + ph > h:
            raise CompactDenseExportError("peak patch origin extends outside frame_shape")
        dense[:, y0 : y0 + ph, x0 : x0 + pw] += patches[:, peak_idx]
    return dense
