"""Array-only energy-center diagnostic; coordinates belong to the supplied frame."""
from dataclasses import dataclass
import numpy as np


class EnergyCenterError(ValueError):
    pass


@dataclass(frozen=True)
class EnergyCenterEstimate:
    center_xy: tuple[float, float]
    background_value: float
    total_corr_energy: float
    fallback_used: bool
    peak_xy: tuple[int, int]
    peak_value: float


def valid_mask(shape: tuple[int, int], valid_pixel_mask: np.ndarray | None) -> np.ndarray:
    if valid_pixel_mask is None:
        return np.ones(shape, dtype=bool)
    mask = np.asarray(valid_pixel_mask)
    if mask.dtype != np.bool_:
        raise EnergyCenterError("valid_pixel_mask must be boolean")
    if mask.shape != tuple(shape):
        raise EnergyCenterError(f"valid_pixel_mask shape {mask.shape} does not match {shape}")
    if not np.any(mask):
        raise EnergyCenterError("valid_pixel_mask leaves zero valid pixels")
    return mask


def estimate_frame_energy_center(frame, *, valid_pixel_mask=None, bg_percentile=5.0) -> EnergyCenterEstimate:
    arr = np.asarray(frame, dtype=np.float64)
    if arr.ndim != 2 or not arr.size:
        raise EnergyCenterError(f"frame must be nonempty 2D, got {arr.shape}")
    mask = valid_mask(arr.shape, valid_pixel_mask)
    if not (0.0 <= float(bg_percentile) <= 100.0):
        raise EnergyCenterError("bg_percentile must be in [0, 100]")
    if not np.all(np.isfinite(arr[mask])):
        raise EnergyCenterError("valid pixels must have finite intensity")
    bg = float(np.percentile(arr[mask], float(bg_percentile)))
    corr = np.maximum(arr - bg, 0.0)
    corr_valid = np.where(mask, corr, 0.0)
    peak_eval = np.where(mask, corr, -np.inf)
    peak_y, peak_x = np.unravel_index(int(np.argmax(peak_eval)), arr.shape)
    total = float(np.sum(corr_valid))
    if total <= 0.0:
        center = (float(peak_x), float(peak_y))
    else:
        yy, xx = np.mgrid[:arr.shape[0], :arr.shape[1]]
        center = (float(np.sum(xx * corr_valid) / total), float(np.sum(yy * corr_valid) / total))
    return EnergyCenterEstimate(center, bg, total, bool(total <= 0.0),
                                (int(peak_x), int(peak_y)), float(arr[peak_y, peak_x]))
