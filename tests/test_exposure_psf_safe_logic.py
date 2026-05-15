from __future__ import annotations

import numpy as np
import pytest

from scripts import calibrate_psf_safe_exposure as sweep


FULL = 255.0


def test_full_scale_single_pixel_fails():
    frame = np.zeros((10, 10), dtype=np.float64)
    frame[0, 0] = 255.0
    burst = frame[None, :, :]
    metrics = sweep.compute_peak_safety_metrics(burst, FULL)
    assert metrics["psf_safe"] == False
    assert metrics["unsafe_reason"] == "peak_pixel_at_or_above_full_scale_in_valid_domain"


def test_all_pixels_strictly_below_full_scale_passes():
    frame = np.full((10, 10), 254.0, dtype=np.float64)
    burst = frame[None, :, :]
    metrics = sweep.compute_peak_safety_metrics(burst, FULL)
    assert metrics["psf_safe"] == True
    assert metrics["unsafe_reason"] is None


def test_near_full_scale_but_below_passes():
    burst = np.array([[[254.999]]], dtype=np.float64)
    metrics = sweep.compute_peak_safety_metrics(burst, FULL)
    assert metrics["psf_safe"] == True


def test_burst_full_scale_fails_even_if_average_below_full_scale():
    burst = np.array([
        [[10, 10], [10, 255]],
        [[10, 10], [10, 10]],
    ], dtype=np.float64)
    avg = burst.mean(axis=0)
    assert avg.max() < FULL
    metrics = sweep.compute_peak_safety_metrics(burst, FULL, avg_frame=avg)
    assert metrics["psf_safe"] == False


def test_nonfinite_pixel_fails():
    burst = np.array([[[0.0]], [[float("nan")]]], dtype=np.float64)
    metrics = sweep.compute_peak_safety_metrics(burst, FULL)
    assert metrics["psf_safe"] == False
    assert metrics["unsafe_reason"] == "non_finite_pixel_in_valid_domain"


def test_invalid_top_row_full_scale_passes_when_excluded():
    burst = np.zeros((2, 4, 4), dtype=np.float64)
    burst[:, 0, 3] = 255.0
    mask = np.ones((4, 4), dtype=bool)
    mask[0, :] = False

    metrics = sweep.compute_peak_safety_metrics(burst, FULL, valid_pixel_mask=mask)

    assert metrics["psf_safe"] is True
    assert metrics["invalid_domain_full_scale_pixel_count"] == 2
    assert metrics["invalid_domain_peak_pixel_burst"] == 255.0


def test_valid_domain_full_scale_still_fails_when_top_row_excluded():
    burst = np.zeros((2, 4, 4), dtype=np.float64)
    burst[:, 0, 3] = 255.0
    burst[:, 2, 2] = 255.0
    mask = np.ones((4, 4), dtype=bool)
    mask[0, :] = False

    metrics = sweep.compute_peak_safety_metrics(burst, FULL, valid_pixel_mask=mask)

    assert metrics["psf_safe"] is False
    assert metrics["unsafe_reason"] == "peak_pixel_at_or_above_full_scale_in_valid_domain"
    assert metrics["valid_domain_full_scale_pixel_count"] == 2
    assert metrics["valid_domain_full_scale_sample_coords"][0]["y"] == 2
    assert metrics["valid_domain_full_scale_sample_coords"][0]["x"] == 2


def test_nonfinite_in_invalid_domain_is_diagnostic_only():
    burst = np.zeros((2, 4, 4), dtype=np.float64)
    burst[:, 0, 1] = float("nan")
    mask = np.ones((4, 4), dtype=bool)
    mask[0, :] = False

    metrics = sweep.compute_peak_safety_metrics(burst, FULL, valid_pixel_mask=mask)

    assert metrics["psf_safe"] is True
    assert metrics["invalid_domain_nonfinite_pixel_count"] == 2


def test_nonfinite_in_valid_domain_fails_even_with_exclusion_policy():
    burst = np.zeros((2, 4, 4), dtype=np.float64)
    burst[:, 2, 1] = float("nan")
    mask = np.ones((4, 4), dtype=bool)
    mask[0, :] = False

    metrics = sweep.compute_peak_safety_metrics(burst, FULL, valid_pixel_mask=mask)

    assert metrics["psf_safe"] is False
    assert metrics["unsafe_reason"] == "non_finite_pixel_in_valid_domain"


def test_signal_metrics_use_valid_domain_pixels():
    frame = np.array([
        [10.0, 200.0],
        [20.0, 210.0],
    ])
    left_mask = np.array([
        [True, False],
        [True, False],
    ])
    right_mask = ~left_mask

    left = sweep.compute_signal_metrics(frame, FULL, valid_pixel_mask=left_mask)
    right = sweep.compute_signal_metrics(frame, FULL, valid_pixel_mask=right_mask)

    assert left["p_signal"] < right["p_signal"]
    assert left["dynamic_range"] == pytest.approx(right["dynamic_range"])


def test_signal_metrics_ignore_invalid_domain_artifact():
    frame = np.full((4, 4), 5.0, dtype=np.float64)
    frame[0, :] = 255.0
    mask = np.ones((4, 4), dtype=bool)
    mask[0, :] = False

    metrics = sweep.compute_signal_metrics(
        frame,
        FULL,
        valid_pixel_mask=mask,
        signal_percentile=99.0,
        min_signal_fraction_threshold=0.10,
        min_dynamic_range_fraction=0.01,
    )

    assert metrics["p_signal"] == 5.0
    assert metrics["dynamic_range"] == 0.0
    assert metrics["usable"] is False


def test_p99_9_does_not_affect_psf_safety():
    burst = np.array([[[254.0]]], dtype=np.float64)
    metrics = sweep.compute_peak_safety_metrics(
        burst, FULL, avg_frame=burst[0], diagnostic_percentiles=(99.0, 99.9),
    )
    assert metrics["psf_safe"] == True


def test_all_wavelengths_must_be_psf_safe():
    safe_row = {"psf_safe": True}
    unsafe_row = {"psf_safe": False}
    assert sweep._all_wavelengths_safe([safe_row, safe_row]) is True
    assert sweep._all_wavelengths_safe([safe_row, unsafe_row]) is False


def test_peak_pixel_burst_equals_max_of_all_frames():
    burst = np.array([
        [[1, 2], [3, 4]],
        [[5, 6], [7, 100]],
    ], dtype=np.float64)
    metrics = sweep.compute_peak_safety_metrics(burst, FULL)
    assert metrics["peak_pixel_burst"] == 100.0


def test_peak_pixel_fraction_computed():
    burst = np.array([[[128.0]]], dtype=np.float64)
    metrics = sweep.compute_peak_safety_metrics(burst, FULL)
    assert metrics["peak_pixel_fraction_burst"] == pytest.approx(128.0 / 255.0)


def test_peak_margin_computed():
    burst = np.array([[[128.0]]], dtype=np.float64)
    metrics = sweep.compute_peak_safety_metrics(burst, FULL)
    assert metrics["peak_margin_to_full_scale"] == pytest.approx(255.0 - 128.0)


def test_dry_run_produces_h5_and_json(tmp_path):
    plan = {
        "plan_id": "test_dry",
        "wavelengths": [{"wavelength_nm": 550.0}],
        "lcd": {"mode": "all_transmissive", "settle_ms": 0, "display_index": -1},
        "camera_search": {
            "exposure_us_start": 10000.0,
            "exposure_us_min": 1000.0,
            "exposure_us_step_factor": 0.5,
            "gain_db_min": 0.0,
            "gain_db_max": 18.0,
            "gain_db_step_db": 6.0,
            "frames_per_setting": 3,
        },
        "psf_safety": {"rule": "all_frames_all_pixels_strictly_below_full_scale"},
        "signal": {
            "percentile": 99.0,
            "min_signal_fraction_threshold": 0.05,
            "min_dynamic_range_fraction": 0.02,
        },
        "output": {
            "raw_h5": str(tmp_path / "sweep.h5"),
            "camera_params_json": str(tmp_path / "params.json"),
        },
        "lock_file": str(tmp_path / "lock.lock"),
    }

    h5_path, result = sweep.run_psf_safe_exposure(
        plan, None, None, None, dry_run=True,
    )

    assert h5_path.exists()
    assert result["psf_safety_policy"]["rule"] == "all_frames_all_pixels_strictly_below_full_scale"


import pytest
