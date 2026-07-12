from __future__ import annotations

import numpy as np
import pytest

import tasks.psf.bad_pixel_correction as bad_pixel_correction
from tasks.psf import BadPixelCorrectionError, correct_bad_pixels


requires_scipy = pytest.mark.skipif(
    bad_pixel_correction._scipy_ndimage is None,
    reason="nearest_valid_v1 behavior tests require optional scipy",
)


@requires_scipy
def test_valid_pixels_are_preserved_exactly() -> None:
    frame = np.arange(25, dtype=np.float64).reshape(5, 5)
    valid_mask = np.ones((5, 5), dtype=bool)
    valid_mask[2, 2] = False

    corrected = correct_bad_pixels(frame, valid_mask)

    assert np.array_equal(corrected[valid_mask], frame[valid_mask])
    assert corrected[2, 2] in set(frame[valid_mask])


@requires_scipy
def test_isolated_bad_pixel_uses_nearest_valid_value() -> None:
    frame = np.zeros((5, 5), dtype=np.float64)
    frame[1, 2] = 17.0
    frame[2, 1] = 17.0
    frame[2, 3] = 17.0
    frame[3, 2] = 17.0
    valid_mask = np.ones((5, 5), dtype=bool)
    valid_mask[2, 2] = False
    frame[2, 2] = np.nan

    corrected = correct_bad_pixels(frame, valid_mask)

    assert corrected[2, 2] == 17.0


@requires_scipy
def test_top_bad_row_is_filled_from_the_next_valid_row() -> None:
    frame = np.arange(20, dtype=np.float64).reshape(4, 5)
    valid_mask = np.ones((4, 5), dtype=bool)
    valid_mask[0, :] = False
    frame[0, :] = np.nan

    corrected = correct_bad_pixels(frame, valid_mask)

    assert np.array_equal(corrected[0, :], frame[1, :])
    assert np.array_equal(corrected[1:, :], frame[1:, :])


@requires_scipy
def test_rectangular_bad_region_is_filled_without_nonfinite_values() -> None:
    frame = np.arange(42, dtype=np.float64).reshape(6, 7)
    valid_mask = np.ones((6, 7), dtype=bool)
    valid_mask[2:5, 2:5] = False
    frame[~valid_mask] = np.nan

    corrected = correct_bad_pixels(frame, valid_mask)

    assert np.array_equal(corrected[valid_mask], frame[valid_mask])
    assert np.isfinite(corrected).all()


@requires_scipy
def test_correction_does_not_modify_input_arrays() -> None:
    frame = np.arange(16, dtype=np.float64).reshape(4, 4)
    valid_mask = np.ones((4, 4), dtype=bool)
    valid_mask[1, 1] = False
    frame[1, 1] = np.nan
    original_frame = frame.copy()
    original_mask = valid_mask.copy()

    corrected = correct_bad_pixels(frame, valid_mask)

    assert corrected is not frame
    assert np.array_equal(frame, original_frame, equal_nan=True)
    assert np.array_equal(valid_mask, original_mask)


@requires_scipy
def test_same_input_produces_same_output() -> None:
    frame = np.arange(36, dtype=np.float32).reshape(6, 6)
    valid_mask = np.ones((6, 6), dtype=bool)
    valid_mask[2:4, 2:4] = False

    first = correct_bad_pixels(frame, valid_mask)
    second = correct_bad_pixels(frame, valid_mask)

    assert np.array_equal(first, second)
    assert first.dtype == frame.dtype


def test_all_invalid_mask_is_rejected() -> None:
    with pytest.raises(BadPixelCorrectionError, match="at least one valid pixel"):
        correct_bad_pixels(np.ones((3, 3)), np.zeros((3, 3), dtype=bool))


def test_non_boolean_mask_is_rejected() -> None:
    with pytest.raises(BadPixelCorrectionError, match="boolean dtype"):
        correct_bad_pixels(np.ones((3, 3)), np.ones((3, 3), dtype=np.uint8))


def test_mask_shape_mismatch_is_rejected() -> None:
    with pytest.raises(BadPixelCorrectionError, match="does not match frame shape"):
        correct_bad_pixels(np.ones((3, 3)), np.ones((2, 3), dtype=bool))


def test_non_2d_frame_is_rejected() -> None:
    with pytest.raises(BadPixelCorrectionError, match="2D numeric"):
        correct_bad_pixels(np.ones((1, 3, 3)), np.ones((3, 3), dtype=bool))


def test_unknown_correction_method_is_rejected() -> None:
    with pytest.raises(BadPixelCorrectionError, match="unsupported bad-pixel correction method"):
        correct_bad_pixels(
            np.ones((3, 3)),
            np.ones((3, 3), dtype=bool),
            method="median_v1",
        )


def test_missing_scipy_is_a_clear_error_when_correction_is_needed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frame = np.ones((3, 3), dtype=np.float64)
    valid_mask = np.ones((3, 3), dtype=bool)
    valid_mask[1, 1] = False
    monkeypatch.setattr(bad_pixel_correction, "_scipy_ndimage", None)

    with pytest.raises(BadPixelCorrectionError, match="requires scipy.ndimage"):
        correct_bad_pixels(frame, valid_mask)


def test_all_valid_frame_does_not_need_scipy(monkeypatch: pytest.MonkeyPatch) -> None:
    frame = np.arange(9, dtype=np.float64).reshape(3, 3)
    valid_mask = np.ones((3, 3), dtype=bool)
    monkeypatch.setattr(bad_pixel_correction, "_scipy_ndimage", None)

    corrected = correct_bad_pixels(frame, valid_mask)

    assert np.array_equal(corrected, frame)
    assert corrected is not frame
