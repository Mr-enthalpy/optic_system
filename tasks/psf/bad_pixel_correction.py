from __future__ import annotations

"""Deterministic bad-pixel correction primitives for processed PSF data.

This module is intentionally artifact-agnostic.  It accepts an already resolved
boolean valid-pixel mask and never reads or writes raw capture data.  Persisted
mask provenance and task-level integration follow the storage/versioning work.
"""

import numpy as np

try:  # SciPy is optional for lightweight installations.
    from scipy import ndimage as _scipy_ndimage
except Exception:  # pragma: no cover - exercised through the explicit error path.
    _scipy_ndimage = None


NEAREST_VALID_V1 = "nearest_valid_v1"


class BadPixelCorrectionError(ValueError):
    """Raised when bad-pixel correction inputs or dependencies are invalid."""


def correct_bad_pixels(
    frame: np.ndarray,
    valid_mask: np.ndarray,
    method: str = NEAREST_VALID_V1,
) -> np.ndarray:
    """Return a corrected copy of one 2D PSF frame.

    ``valid_mask`` must be a two-dimensional boolean array with the same shape
    as ``frame``.  ``True`` pixels retain their original values exactly.  Each
    ``False`` pixel is filled from the nearest valid pixel in Euclidean sensor
    coordinates.  Source values are always read from the original frame, so the
    result is deterministic and correction never cascades through other invalid
    pixels.

    ``nearest_valid_v1`` requires SciPy's Euclidean distance transform when at
    least one invalid pixel is present.  The function returns a new array and
    never mutates either input.
    """
    arr = np.asarray(frame)
    if arr.ndim != 2:
        raise BadPixelCorrectionError(
            f"frame must be a 2D numeric array, got shape {arr.shape}"
        )
    if not np.issubdtype(arr.dtype, np.number):
        raise BadPixelCorrectionError(
            f"frame must have a numeric dtype, got {arr.dtype}"
        )

    mask = np.asarray(valid_mask)
    if mask.ndim != 2:
        raise BadPixelCorrectionError(
            f"valid_mask must be a 2D boolean array, got shape {mask.shape}"
        )
    if mask.dtype != np.bool_:
        raise BadPixelCorrectionError(
            f"valid_mask must have boolean dtype, got {mask.dtype}"
        )
    if mask.shape != arr.shape:
        raise BadPixelCorrectionError(
            f"valid_mask shape {mask.shape} does not match frame shape {arr.shape}"
        )
    if not np.any(mask):
        raise BadPixelCorrectionError("valid_mask must contain at least one valid pixel")
    if method != NEAREST_VALID_V1:
        raise BadPixelCorrectionError(
            f"unsupported bad-pixel correction method {method!r}; "
            f"expected {NEAREST_VALID_V1!r}"
        )

    corrected = np.array(arr, copy=True)
    invalid = ~mask
    if not np.any(invalid):
        return corrected

    if _scipy_ndimage is None:
        raise BadPixelCorrectionError(
            f"{NEAREST_VALID_V1} requires scipy.ndimage.distance_transform_edt; "
            "install scipy to correct invalid pixels"
        )

    nearest_indices = _scipy_ndimage.distance_transform_edt(
        invalid,
        return_distances=False,
        return_indices=True,
    )
    corrected[invalid] = arr[tuple(nearest_indices[:, invalid])]
    return corrected
