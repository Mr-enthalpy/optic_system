"""Explicit intensity operations on analysis copies, independent of file/device type."""
import numpy as np


def relative_response(values, *, exposure_us: float | None = None, normalize_sum: bool = False) -> np.ndarray:
    """Preserve signed values; scaling does not calibrate gain or source power."""
    result = np.asarray(values, dtype=np.float64).copy()
    if exposure_us is not None:
        if not np.isfinite(exposure_us) or exposure_us <= 0:
            raise ValueError("exposure_us must be finite and positive")
        result /= exposure_us
    if normalize_sum:
        total = float(result.sum())
        if not np.isfinite(total) or total <= 0:
            raise ValueError("normalization requires positive finite total intensity")
        result /= total
    return result
