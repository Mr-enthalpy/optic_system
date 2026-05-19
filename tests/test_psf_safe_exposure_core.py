from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

import numpy as np
import pytest

_repo_root = Path(__file__).resolve().parents[1]
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from scripts.calibrate_psf_safe_exposure import (
    compute_peak_safety_metrics,
    compute_signal_metrics,
    infer_full_scale,
    _estimate_safe_bound_for_wavelength,
    _resolve_frame_full_scale,
    _all_wavelengths_safe,
    _worst_signal_wavelength,
    _build_result,
)


FULL = 255.0


class TestPeakSafetyMetrics:
    def test_all_below_full_scale_psf_safe(self):
        burst = np.ones((3, 20, 20), dtype=np.float64) * 200
        result = compute_peak_safety_metrics(burst, FULL)
        assert result["psf_safe"] == True
        assert result["unsafe_reason"] is None

    def test_one_pixel_at_full_scale_fails(self):
        burst = np.ones((3, 20, 20), dtype=np.float64) * 200
        burst[0, 0, 0] = 255.0
        result = compute_peak_safety_metrics(burst, FULL)
        assert result["psf_safe"] == False
        assert result["unsafe_reason"] == "peak_pixel_at_or_above_full_scale_in_valid_domain"

    def test_one_pixel_above_full_scale_fails(self):
        burst = np.ones((3, 20, 20), dtype=np.float64) * 200
        burst[0, 0, 0] = 260.0
        result = compute_peak_safety_metrics(burst, FULL)
        assert result["psf_safe"] == False

    def test_nan_pixel_fails(self):
        burst = np.ones((3, 5, 5), dtype=np.float64) * 128
        burst[0, 0, 0] = float("nan")
        result = compute_peak_safety_metrics(burst, FULL)
        assert result["psf_safe"] == False
        assert result["unsafe_reason"] == "non_finite_pixel_in_valid_domain"

    def test_peak_pixel_burst_is_max_of_all_frames(self):
        burst = np.array([
            [[1, 2], [3, 4]],
            [[5, 6], [7, 100]],
        ], dtype=np.float64)
        result = compute_peak_safety_metrics(burst, FULL)
        assert result["peak_pixel_burst"] == 100.0

    def test_peak_pixel_avg_from_avg_frame(self):
        burst = np.ones((3, 20, 20), dtype=np.float64) * 200
        avg = np.ones((20, 20), dtype=np.float64) * 180
        avg[0, 0] = 250
        result = compute_peak_safety_metrics(burst, FULL, avg_frame=avg)
        assert result["peak_pixel_avg"] == 250.0

    def test_peak_margin_computed(self):
        burst = np.array([[[128.0]]], dtype=np.float64)
        result = compute_peak_safety_metrics(burst, FULL)
        assert result["peak_margin_to_full_scale"] == pytest.approx(127.0)

    def test_peak_fraction_computed(self):
        burst = np.array([[[128.0]]], dtype=np.float64)
        result = compute_peak_safety_metrics(burst, FULL)
        assert result["peak_pixel_fraction_burst"] == pytest.approx(128.0 / 255.0)

    def test_p99_0_and_p99_9_from_avg(self):
        burst = np.full((1, 10, 10), 128.0, dtype=np.float64)
        avg = np.arange(100, dtype=np.float64).reshape(10, 10)
        result = compute_peak_safety_metrics(
            burst, FULL, avg_frame=avg, diagnostic_percentiles=(99.0, 99.9),
        )
        assert result["p99_0_avg"] is not None
        assert result["p99_9_avg"] is not None

    def test_uint8_255_passes(self):
        burst = np.full((3, 20, 20), 254, dtype=np.uint8)
        result = compute_peak_safety_metrics(burst, FULL)
        assert result["psf_safe"] == True

    def test_uint16_65535_passes(self):
        burst = np.full((3, 20, 20), 65534, dtype=np.uint16)
        result = compute_peak_safety_metrics(burst, 65535.0)
        assert result["psf_safe"] == True

    def test_invalid_top_row_full_scale_passes_when_excluded(self):
        burst = np.zeros((2, 4, 4), dtype=np.float64)
        burst[:, 0, 1] = 255.0
        mask = np.ones((4, 4), dtype=bool)
        mask[0, :] = False
        result = compute_peak_safety_metrics(burst, FULL, valid_pixel_mask=mask)
        assert result["psf_safe"] is True
        assert result["invalid_domain_full_scale_pixel_count"] == 2

    def test_valid_domain_full_scale_fails_with_exclusion_policy(self):
        burst = np.zeros((2, 4, 4), dtype=np.float64)
        burst[:, 0, 1] = 255.0
        burst[:, 2, 2] = 255.0
        mask = np.ones((4, 4), dtype=bool)
        mask[0, :] = False
        result = compute_peak_safety_metrics(burst, FULL, valid_pixel_mask=mask)
        assert result["psf_safe"] is False
        assert result["unsafe_reason"] == "peak_pixel_at_or_above_full_scale_in_valid_domain"

    def test_nonfinite_in_invalid_domain_is_diagnostic_only(self):
        burst = np.zeros((2, 4, 4), dtype=np.float64)
        burst[:, 0, 1] = float("nan")
        mask = np.ones((4, 4), dtype=bool)
        mask[0, :] = False
        result = compute_peak_safety_metrics(burst, FULL, valid_pixel_mask=mask)
        assert result["psf_safe"] is True
        assert result["invalid_domain_nonfinite_pixel_count"] == 2

    def test_nonfinite_in_valid_domain_fails_with_exclusion_policy(self):
        burst = np.zeros((2, 4, 4), dtype=np.float64)
        burst[:, 2, 1] = float("nan")
        mask = np.ones((4, 4), dtype=bool)
        mask[0, :] = False
        result = compute_peak_safety_metrics(burst, FULL, valid_pixel_mask=mask)
        assert result["psf_safe"] is False
        assert result["unsafe_reason"] == "non_finite_pixel_in_valid_domain"


class TestAllWavelengthsSafe:
    def test_accepts(self):
        results = [
            {"psf_safe": True},
            {"psf_safe": True},
        ]
        assert _all_wavelengths_safe(results) is True

    def test_rejects_one_unsafe(self):
        results = [
            {"psf_safe": True},
            {"psf_safe": False},
        ]
        assert _all_wavelengths_safe(results) is False

    def test_worst_signal_identified(self):
        results = [
            {"p_signal": 20.0},
            {"p_signal": 100.0},
        ]
        worst = _worst_signal_wavelength(results)
        assert worst["p_signal"] == 20.0


def test_acquire_and_evaluate_keeps_invalid_artifact_out_of_low_signal():
    from types import SimpleNamespace

    from scripts.calibrate_psf_safe_exposure import _acquire_and_evaluate

    class FakeCamera:
        def acquire_burst(self, k: int):
            burst = np.full((k, 4, 4), 5.0, dtype=np.float64)
            burst[:, 0, :] = 255.0
            return SimpleNamespace(
                frames_avg=burst.mean(axis=0, dtype=np.float64),
                burst=burst,
            )

    mask = np.ones((4, 4), dtype=bool)
    mask[0, :] = False

    row = _acquire_and_evaluate(
        FakeCamera(),
        k=3,
        full_scale=255.0,
        diagnostics_cfg={},
        sig_cfg={
            "percentile": 99.0,
            "min_signal_fraction_threshold": 0.10,
            "min_dynamic_range_fraction": 0.01,
        },
        valid_pixel_mask=mask,
    )

    assert row["psf_safe"] is True
    assert row["low_signal"] is True
    assert row["p_signal"] == 5.0
    assert row["invalid_domain_full_scale_pixel_count"] == 12


def test_acquire_and_evaluate_returns_raw_failure_frame_when_avg_is_below_full_scale():
    from types import SimpleNamespace

    from scripts.calibrate_psf_safe_exposure import _acquire_and_evaluate

    class FakeCamera:
        def acquire_burst(self, k: int):
            burst = np.full((k, 4, 4), 20.0, dtype=np.float64)
            burst[1, 2, 3] = 255.0
            return SimpleNamespace(
                frames_avg=burst.mean(axis=0, dtype=np.float64),
                burst=burst,
            )

    row = _acquire_and_evaluate(
        FakeCamera(),
        k=3,
        full_scale=255.0,
        diagnostics_cfg={},
        sig_cfg={
            "percentile": 99.0,
            "min_signal_fraction_threshold": 0.01,
            "min_dynamic_range_fraction": 0.0,
        },
    )

    assert row["psf_safe"] is False
    assert row["peak_pixel_avg"] < 255.0
    assert row["valid_domain_peak_frame_index"] == 1
    assert row["valid_domain_peak_y"] == 2
    assert row["valid_domain_peak_x"] == 3
    assert row["failure_frame_kind"] == "raw_burst_peak_frame"
    assert row["failure_frame"][2, 3] == 255.0


def test_binary_search_returns_last_safe_row_not_last_probe():
    class FakeCamera:
        def __init__(self):
            self.exposure_us = 0.0

        def apply_camera_params(self, exposure_us=None, gain_db=None):
            self.exposure_us = float(exposure_us)

        def acquire_burst(self, k: int):
            value = 100.0 if self.exposure_us <= 500.0 else 255.0
            burst = np.full((k, 4, 4), value, dtype=np.float64)
            from types import SimpleNamespace

            return SimpleNamespace(frames_avg=burst.mean(axis=0), burst=burst)

    bound, row = _estimate_safe_bound_for_wavelength(
        FakeCamera(),
        k=2,
        full_scale=255.0,
        diagnostics_cfg={},
        sig_cfg={
            "percentile": 99.0,
            "min_signal_fraction_threshold": 0.01,
            "min_dynamic_range_fraction": 0.0,
        },
        L=100.0,
        R=1000.0,
        gain_db=0.0,
        eps_absolute=10.0,
    )

    assert bound <= 500.0
    assert row["psf_safe"] is True


def test_hardware_full_scale_requires_frame_metadata():
    from types import SimpleNamespace

    capture = SimpleNamespace(
        frames_avg=np.zeros((4, 4), dtype=np.uint8),
        metadata={},
    )

    with pytest.raises(RuntimeError, match="requires camera frame metadata"):
        _resolve_frame_full_scale(capture, {"camera": {"full_scale": 255}}, dry_run=False)


def test_hardware_full_scale_uses_frame_metadata():
    from types import SimpleNamespace

    capture = SimpleNamespace(
        frames_avg=np.zeros((4, 4), dtype=np.uint8),
        metadata={
            "frame_dtype_full_scale": 255,
            "frame_dtype_full_scale_source": "frame_metadata.format",
        },
    )

    full_scale, source = _resolve_frame_full_scale(capture, {}, dry_run=False)

    assert full_scale == 255.0
    assert source == "frame_metadata.format"


class TestPsfSafeExposureWriter:
    def test_writer_creates_h5(self):
        from tasks.psf_safe_exposure_h5 import PsfSafeExposureWriter
        d = tempfile.mkdtemp(prefix="optsys_sw_")
        p = Path(d) / "test_sweep.h5"
        try:
            with PsfSafeExposureWriter(p, plan_id="test") as w:
                w.write_plan_json({"plan_id": "test"})
                w.write_valid_pixel_domain(
                    {
                        "type": "full_frame",
                        "frame_shape": [100, 100],
                        "valid_pixel_count": 10000,
                        "invalid_pixel_count": 0,
                    }
                )
                frame = np.ones((100, 100), dtype=np.float64) * 128
                w.append_sweep_row(
                    wavelength_nm=550.0, exposure_us=50000.0, gain_db=0.0,
                    frames_avg=frame,
                    peak_pixel_burst=128.0, peak_pixel_avg=128.0,
                    peak_pixel_fraction_burst=0.5, peak_margin_to_full_scale=127.0,
                    p99_0_avg=127.0, p99_9_avg=127.0,
                    unsafe_reason=None, psf_safe=True,
                    p_signal=128.0, dynamic_range=100.0, low_signal=False,
                    valid_pixel_count=10000, invalid_pixel_count=0,
                    invalid_domain_peak_pixel_burst=None,
                    invalid_domain_full_scale_pixel_count=0,
                    invalid_domain_nonfinite_pixel_count=0,
                )
            import h5py
            with h5py.File(p, "r") as f:
                assert bool(f["sweep/psf_safe"][0]) is True
                assert f["sweep/peak_pixel_burst"][0] == 128.0
                assert f["sweep/unsafe_reason"][0] == b""
                assert "saturated_fraction" not in f["sweep"]
                assert "saturated_pixel_count" not in f["sweep"]
                assert "safe" not in f["sweep"]
                assert "max_pixel" not in f["sweep"]
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_writer_multiple_rows(self):
        from tasks.psf_safe_exposure_h5 import PsfSafeExposureWriter
        d = tempfile.mkdtemp(prefix="optsys_sw_")
        p = Path(d) / "test_multi.h5"
        try:
            with PsfSafeExposureWriter(p, plan_id="multi") as w:
                w.write_valid_pixel_domain(
                    {
                        "type": "full_frame",
                        "frame_shape": [50, 50],
                        "valid_pixel_count": 2500,
                        "invalid_pixel_count": 0,
                    }
                )
                for i in range(5):
                    frame = np.ones((50, 50), dtype=np.float64) * (i * 40)
                    w.append_sweep_row(
                        wavelength_nm=550.0, exposure_us=50000.0, gain_db=0.0,
                        frames_avg=frame,
                        peak_pixel_burst=i * 40.0, peak_pixel_avg=i * 40.0,
                        peak_pixel_fraction_burst=0.5, peak_margin_to_full_scale=255.0 - i * 40,
                        p99_0_avg=i * 35.0, p99_9_avg=i * 38.0,
                        unsafe_reason=None, psf_safe=True,
                        p_signal=i * 30.0, dynamic_range=i * 30.0, low_signal=False,
                        valid_pixel_count=2500, invalid_pixel_count=0,
                        invalid_domain_peak_pixel_burst=None,
                        invalid_domain_full_scale_pixel_count=0,
                        invalid_domain_nonfinite_pixel_count=0,
                    )
            import h5py
            with h5py.File(p, "r") as f:
                assert f["sweep/peak_pixel_burst"].shape == (5,)
                assert "saturated_fraction" not in f["sweep"]
                assert "max_pixel" not in f["sweep"]
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_processing_flags(self):
        from tasks.psf_safe_exposure_h5 import PsfSafeExposureWriter
        d = tempfile.mkdtemp(prefix="optsys_sw_")
        p = Path(d) / "test_pf.h5"
        try:
            with PsfSafeExposureWriter(p, plan_id="pf_test") as w:
                pass
            import h5py
            with h5py.File(p, "r") as f:
                pf = json.loads(f["capture/processing_flags_json"][()])
                assert pf["scientific_calibration_valid"] is False
                assert pf["training_ready"] is False
        finally:
            shutil.rmtree(d, ignore_errors=True)


class TestCameraParamsJSON:
    def test_psf_safety_policy_schema(self):
        from scripts.calibrate_psf_safe_exposure import _GainResult
        plan = {
            "plan_id": "test",
            "output": {"raw_h5": "data/raw/test.h5"},
            "wavelengths": [{"wavelength_nm": 550.0}],
            "camera_search": {"gain_db_min": 0.0, "gains_db": [0.0], "frames_per_setting": 3},
            "psf_safety": {},
            "signal": {
                "percentile": 99.0,
                "min_signal_fraction_threshold": 0.10,
                "min_dynamic_range_fraction": 0.08,
            },
        }
        final_rows = [{
            "wavelength_nm": 550.0,
            "exposure_us": 5000.0,
            "gain_db": 0.0,
            "peak_pixel_burst": 200.0,
            "peak_pixel_avg": 190.0,
            "peak_pixel_fraction_burst": 200.0 / 255.0,
            "peak_margin_to_full_scale": 55.0,
            "p99_0_avg": 180.0,
            "p99_9_avg": 195.0,
            "p_signal": 181.0,
            "dynamic_range": 150.0,
            "psf_safe": True,
            "unsafe_reason": None,
            "low_signal": False,
            "valid_pixel_count": 100,
            "invalid_pixel_count": 0,
            "invalid_domain_peak_pixel_burst": None,
            "invalid_domain_full_scale_pixel_count": 0,
            "invalid_domain_nonfinite_pixel_count": 0,
        }]
        accepted = _GainResult(
            gain_db=0.0,
            exposure_us=5000.0,
            psf_safe=True,
            low_signal=False,
            per_wavelength_bounds={"550.0": 5000.0},
            final_rows=final_rows,
        )
        all_results: list = []

        result = _build_result(plan, accepted, [accepted], all_results, 255.0, selection_reason="psf_safe")

        assert result["psf_safety_policy"]["rule"] == "all_frames_all_pixels_strictly_below_full_scale"
        assert result["psf_safety_policy"]["evaluated_domain"] == "valid_camera_pixel_domain"
        assert result["psf_safety_policy"]["allow_full_scale_pixel"] is False
        assert result["psf_safety_policy"]["valid_pixel_domain"]["type"] == "full_frame"
        wl_550 = result["per_wavelength_metrics"]["550.0"]
        assert wl_550["psf_safe"] is True
        assert wl_550["unsafe_reason"] is None
        assert wl_550["valid_pixel_count"] > 0
        assert "saturated_pixel_count" not in wl_550
        assert "saturated_fraction" not in wl_550
        assert "safe" not in wl_550
        assert "saturation_policy" not in result
        assert result["validity"]["psf_exposure_safe"] is True


def test_dry_run_produces_h5_and_json(tmp_path):
    plan_dict = {
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
            "raw_h5": str(tmp_path / "dry_test.h5"),
            "camera_params_json": str(tmp_path / "dry_test.json"),
        },
        "lock_file": str(tmp_path / "lock_test.lock"),
    }

    from scripts.calibrate_psf_safe_exposure import run_psf_safe_exposure
    h5_path, result = run_psf_safe_exposure(
        plan_dict, None, None, None, dry_run=True,
    )

    assert h5_path.exists()
    assert result["psf_safety_policy"]["rule"] == "all_frames_all_pixels_strictly_below_full_scale"
    assert result["psf_safety_policy"]["evaluated_domain"] == "valid_camera_pixel_domain"


class TestPerWavelengthBoundsAndFinalMetrics:
    def test_bounds_differ_global_is_min_all_metrics_at_global(self, tmp_path):
        from scripts.calibrate_psf_safe_exposure import _GainResult, _build_result

        final_rows = [
            {"wavelength_nm": 450.0, "exposure_us": 8000.0, "gain_db": 0.0,
             "peak_pixel_burst": 120.0, "peak_pixel_avg": 110.0,
             "peak_pixel_fraction_burst": 0.47, "peak_margin_to_full_scale": 135.0,
             "p99_0_avg": 100.0, "p99_9_avg": 115.0,
             "p_signal": 105.0, "dynamic_range": 80.0,
             "psf_safe": True, "unsafe_reason": None, "low_signal": False,
             "valid_pixel_count": 100, "invalid_pixel_count": 0,
             "invalid_domain_peak_pixel_burst": None,
             "invalid_domain_full_scale_pixel_count": 0,
             "invalid_domain_nonfinite_pixel_count": 0},
            {"wavelength_nm": 550.0, "exposure_us": 8000.0, "gain_db": 0.0,
             "peak_pixel_burst": 125.0, "peak_pixel_avg": 112.0,
             "peak_pixel_fraction_burst": 0.49, "peak_margin_to_full_scale": 130.0,
             "p99_0_avg": 102.0, "p99_9_avg": 118.0,
             "p_signal": 108.0, "dynamic_range": 85.0,
             "psf_safe": True, "unsafe_reason": None, "low_signal": False,
             "valid_pixel_count": 100, "invalid_pixel_count": 0,
             "invalid_domain_peak_pixel_burst": None,
             "invalid_domain_full_scale_pixel_count": 0,
             "invalid_domain_nonfinite_pixel_count": 0},
            {"wavelength_nm": 650.0, "exposure_us": 8000.0, "gain_db": 0.0,
             "peak_pixel_burst": 118.0, "peak_pixel_avg": 108.0,
             "peak_pixel_fraction_burst": 0.46, "peak_margin_to_full_scale": 137.0,
             "p99_0_avg": 98.0, "p99_9_avg": 113.0,
             "p_signal": 102.0, "dynamic_range": 78.0,
             "psf_safe": True, "unsafe_reason": None, "low_signal": False,
             "valid_pixel_count": 100, "invalid_pixel_count": 0,
             "invalid_domain_peak_pixel_burst": None,
             "invalid_domain_full_scale_pixel_count": 0,
             "invalid_domain_nonfinite_pixel_count": 0},
        ]

        accepted = _GainResult(
            gain_db=0.0, exposure_us=8000.0, psf_safe=True, low_signal=False,
            per_wavelength_bounds={"450.0": 20000.0, "550.0": 12000.0, "650.0": 8000.0},
            final_rows=final_rows,
        )
        all_results: list = []

        plan = {
            "plan_id": "bounds_test",
            "output": {"raw_h5": str(tmp_path / "bounds.h5")},
            "wavelengths": [{"wavelength_nm": 450.0}, {"wavelength_nm": 550.0}, {"wavelength_nm": 650.0}],
            "camera_search": {
                "gain_db_min": 0.0,
                "gains_db": [0.0],
                "frames_per_setting": 3,
                "binary_search_eps_us": 50.0,
            },
            "psf_safety": {},
            "signal": {"percentile": 99.0, "min_signal_fraction_threshold": 0.05,
                       "min_dynamic_range_fraction": 0.02},
        }

        result = _build_result(plan, accepted, [accepted], all_results, 255.0)

        metrics = result["per_wavelength_metrics"]
        global_exp = result["global_safe_camera"]["exposure_us"]
        assert global_exp == 8000.0, f"expected 8000.0, got {global_exp}"
        for wl_str in ("450.0", "550.0", "650.0"):
            assert wl_str in metrics
            assert metrics[wl_str]["measured_at_exposure_us"] == 8000.0
            assert metrics[wl_str]["measured_at_gain_db"] == 0.0
        bounds = result["search_diagnostics"]["per_wavelength_safe_upper_bounds"]
        assert bounds == {"450.0": 20000.0, "550.0": 12000.0, "650.0": 8000.0}

    def test_search_probe_rows_not_in_metrics(self, tmp_path, monkeypatch):
        from scripts.calibrate_psf_safe_exposure import _GainResult, _build_result

        final_rows = [
            {"wavelength_nm": 450.0, "exposure_us": 8000.0, "gain_db": 0.0,
             "peak_pixel_burst": 100.0, "peak_pixel_avg": 90.0,
             "peak_pixel_fraction_burst": 0.4, "peak_margin_to_full_scale": 155.0,
             "p99_0_avg": 80.0, "p99_9_avg": 95.0,
             "p_signal": 85.0, "dynamic_range": 70.0,
             "psf_safe": True, "unsafe_reason": None, "low_signal": False,
             "valid_pixel_count": 100, "invalid_pixel_count": 0,
             "invalid_domain_peak_pixel_burst": None,
             "invalid_domain_full_scale_pixel_count": 0,
             "invalid_domain_nonfinite_pixel_count": 0},
            {"wavelength_nm": 550.0, "exposure_us": 8000.0, "gain_db": 0.0,
             "peak_pixel_burst": 110.0, "peak_pixel_avg": 95.0,
             "peak_pixel_fraction_burst": 0.43, "peak_margin_to_full_scale": 145.0,
             "p99_0_avg": 85.0, "p99_9_avg": 100.0,
             "p_signal": 90.0, "dynamic_range": 75.0,
             "psf_safe": True, "unsafe_reason": None, "low_signal": False,
             "valid_pixel_count": 100, "invalid_pixel_count": 0,
             "invalid_domain_peak_pixel_burst": None,
             "invalid_domain_full_scale_pixel_count": 0,
             "invalid_domain_nonfinite_pixel_count": 0},
        ]
        probe_rows = [
            {"wavelength_nm": 450.0, "exposure_us": 20000.0, "gain_db": 0.0,
             "peak_pixel_burst": 200.0, "peak_pixel_avg": 180.0,
             "peak_pixel_fraction_burst": 0.8, "peak_margin_to_full_scale": 55.0,
             "p99_0_avg": 170.0, "p99_9_avg": 190.0,
             "p_signal": 175.0, "dynamic_range": 150.0,
             "psf_safe": True, "unsafe_reason": None, "low_signal": False,
             "valid_pixel_count": 100, "invalid_pixel_count": 0,
             "invalid_domain_peak_pixel_burst": None,
             "invalid_domain_full_scale_pixel_count": 0,
             "invalid_domain_nonfinite_pixel_count": 0},
        ]

        accepted = _GainResult(
            gain_db=0.0, exposure_us=8000.0, psf_safe=True, low_signal=False,
            per_wavelength_bounds={"450.0": 20000.0, "550.0": 12000.0},
            final_rows=final_rows,
        )
        all_results = probe_rows + final_rows

        plan = {
            "plan_id": "probes_test",
            "output": {"raw_h5": str(tmp_path / "probes.h5")},
            "wavelengths": [{"wavelength_nm": 450.0}, {"wavelength_nm": 550.0}],
            "camera_search": {
                "gain_db_min": 0.0,
                "gains_db": [0.0],
                "frames_per_setting": 3,
                "binary_search_eps_us": 50.0,
            },
            "psf_safety": {},
            "signal": {"percentile": 99.0, "min_signal_fraction_threshold": 0.05,
                       "min_dynamic_range_fraction": 0.02},
        }

        result = _build_result(plan, accepted, [accepted], all_results, 255.0)

        metrics = result["per_wavelength_metrics"]
        for wl_str in ("450.0", "550.0"):
            assert metrics[wl_str]["measured_at_exposure_us"] == 8000.0
            assert metrics[wl_str]["peak_pixel_burst"] <= 110.0
        assert result["search_diagnostics"]["per_wavelength_safe_upper_bounds"] == {
            "450.0": 20000.0, "550.0": 12000.0,
        }


class TestElevatedGainFallback:
    def test_gain_min_low_signal_fallback_selects_gain_min(self, tmp_path, monkeypatch):
        from scripts.calibrate_psf_safe_exposure import run_psf_safe_exposure

        class _LowSignalFakeCamera:
            def apply_camera_params(self, exposure_us=None, gain_db=None):
                self.exposure_us = float(exposure_us) if exposure_us else None
                self.gain_db = float(gain_db) if gain_db else None

            def acquire_burst(self, k: int):
                burst = np.full((k, 4, 4), 50.0, dtype=np.float64)
                avg = burst.mean(axis=0, dtype=np.float64)
                from types import SimpleNamespace
                return SimpleNamespace(
                    frames_avg=avg, burst=burst,
                    metadata={"frame_dtype_full_scale": 255},
                )

        monkeypatch.setattr(
            "scripts.calibrate_psf_safe_exposure._make_fake_adapter",
            lambda: _LowSignalFakeCamera(),
        )

        plan = {
            "plan_id": "fallback_test",
            "wavelengths": [{"wavelength_nm": 550.0}],
            "lcd": {"mode": "all_transmissive", "settle_ms": 0, "display_index": -1},
            "camera_search": {
                "exposure_us_start": 50000.0, "exposure_us_min": 100.0,
                "exposure_us_step_factor": 0.5,
                "gain_db_min": 0.0, "gain_db_max": 18.0, "gain_db_step_db": 6.0,
                "frames_per_setting": 3,
                "binary_search_eps_us": 50.0,
            },
            "psf_safety": {"rule": "all_frames_all_pixels_strictly_below_full_scale"},
            "signal": {"percentile": 99.0, "min_signal_fraction_threshold": 0.99,
                       "min_dynamic_range_fraction": 0.80},
            "output": {
                "raw_h5": str(tmp_path / "fallback.h5"),
                "camera_params_json": str(tmp_path / "fallback.json"),
            },
            "lock_file": str(tmp_path / "lock.lock"),
        }

        _h5, result = run_psf_safe_exposure(plan, None, None, None, dry_run=True)

        assert result["global_safe_camera"]["gain_db"] == 0.0
        assert result["selection_reason"] == "gain_min_psf_safe_low_signal_fallback"
        assert result["search_diagnostics"]["per_wavelength_safe_upper_bounds"] == {"550.0": 50000.0}


def test_verification_exposure_backoff_applies_to_recommended_exposure(tmp_path, monkeypatch):
    from scripts.calibrate_psf_safe_exposure import run_psf_safe_exposure

    class _ThresholdFakeCamera:
        def apply_camera_params(self, exposure_us=None, gain_db=None):
            self.exposure_us = float(exposure_us) if exposure_us is not None else None
            self.gain_db = float(gain_db) if gain_db is not None else None

        def acquire_burst(self, k: int):
            from types import SimpleNamespace

            if self.exposure_us is None:
                raise RuntimeError("exposure_us not set")
            peak = 255.0 if self.exposure_us >= 1000.0 else 200.0
            burst = np.full((k, 4, 4), peak, dtype=np.float64)
            avg = burst.mean(axis=0, dtype=np.float64)
            return SimpleNamespace(
                frames_avg=avg,
                burst=burst,
                metadata={"frame_dtype_full_scale": 255},
            )

    monkeypatch.setattr(
        "scripts.calibrate_psf_safe_exposure._make_fake_adapter",
        lambda: _ThresholdFakeCamera(),
    )

    plan = {
        "plan_id": "verification_backoff_test",
        "wavelengths": [{"wavelength_nm": 550.0}],
        "lcd": {"mode": "all_transmissive", "settle_ms": 0, "display_index": -1},
        "camera_search": {
            "exposure_us_start": 1200.0,
            "exposure_us_min": 100.0,
            "exposure_us_step_factor": 0.5,
            "gains_db": [0.0],
            "gain_db_min": 0.0,
            "gain_db_max": 0.0,
            "gain_db_step_db": 6.0,
            "frames_per_setting": 3,
            "binary_search_eps_us": 10.0,
            "verification_exposure_backoff_us": 50.0,
        },
        "psf_safety": {"rule": "all_frames_all_pixels_strictly_below_full_scale"},
        "signal": {
            "percentile": 99.0,
            "min_signal_fraction_threshold": 0.05,
            "min_dynamic_range_fraction": 0.02,
        },
        "output": {
            "raw_h5": str(tmp_path / "verification_backoff.h5"),
            "camera_params_json": str(tmp_path / "verification_backoff.json"),
        },
        "lock_file": str(tmp_path / "verification_backoff.lock"),
    }

    _h5, result = run_psf_safe_exposure(plan, None, None, None, dry_run=True)

    assert result["search_diagnostics"]["per_wavelength_safe_upper_bounds"] == {"550.0": 993.75}
    assert result["search_diagnostics"]["verification_exposure_backoff_us"] == 50.0
    assert result["global_safe_camera"]["exposure_us"] == 943.75
    assert result["camera_param_catalog"]["550.0"]["recommended"]["exposure_us"] == 943.75

    def test_fallback_bounds_match_accepted_gain(self, tmp_path, monkeypatch):
        from scripts.calibrate_psf_safe_exposure import run_psf_safe_exposure

        class _LowSigMultiWlCamera:
            def apply_camera_params(self, exposure_us=None, gain_db=None):
                self.exposure_us = float(exposure_us) if exposure_us else None
                self.gain_db = float(gain_db) if gain_db else None

            def acquire_burst(self, k: int):
                burst = np.full((k, 4, 4), 50.0, dtype=np.float64)
                avg = burst.mean(axis=0, dtype=np.float64)
                from types import SimpleNamespace
                return SimpleNamespace(
                    frames_avg=avg, burst=burst,
                    metadata={"frame_dtype_full_scale": 255},
                )

        monkeypatch.setattr(
            "scripts.calibrate_psf_safe_exposure._make_fake_adapter",
            lambda: _LowSigMultiWlCamera(),
        )

        plan = {
            "plan_id": "bounds_fallback_test",
            "wavelengths": [
                {"wavelength_nm": 450.0}, {"wavelength_nm": 550.0}, {"wavelength_nm": 650.0},
            ],
            "lcd": {"mode": "all_transmissive", "settle_ms": 0, "display_index": -1},
            "camera_search": {
                "exposure_us_start": 50000.0, "exposure_us_min": 100.0,
                "exposure_us_step_factor": 0.5,
                "gain_db_min": 0.0, "gain_db_max": 0.0, "gain_db_step_db": 3.0,
                "frames_per_setting": 3,
                "binary_search_eps_us": 50.0,
            },
            "psf_safety": {"rule": "all_frames_all_pixels_strictly_below_full_scale"},
            "signal": {"percentile": 99.0, "min_signal_fraction_threshold": 0.99,
                       "min_dynamic_range_fraction": 0.80},
            "output": {
                "raw_h5": str(tmp_path / "bfb.h5"),
                "camera_params_json": str(tmp_path / "bfb.json"),
            },
            "lock_file": str(tmp_path / "lock.lock"),
        }

        _h5, result = run_psf_safe_exposure(plan, None, None, None, dry_run=True)

        assert result["global_safe_camera"]["gain_db"] == 0.0
        bounds = result["search_diagnostics"]["per_wavelength_safe_upper_bounds"]
        assert len(bounds) == 3
        for wl_str in ("450.0", "550.0", "650.0"):
            assert wl_str in bounds
        assert result["per_wavelength_metrics"]["450.0"]["measured_at_gain_db"] == 0.0

    def test_higher_gain_accepted_when_signal_ok(self, tmp_path, monkeypatch):
        from scripts.calibrate_psf_safe_exposure import run_psf_safe_exposure

        class _GainAwareFakeCamera:
            def apply_camera_params(self, exposure_us=None, gain_db=None):
                self.exposure_us = float(exposure_us) if exposure_us else None
                self.gain_db = float(gain_db) if gain_db else None

            def acquire_burst(self, k: int):
                gain = float(self.gain_db or 0)
                rng = np.random.default_rng(42 + int(gain * 100))
                scale = 200.0 if gain >= 3.0 else 20.0
                burst = rng.normal(scale, 20.0, (k, 8, 8)).astype(np.float64)
                burst = np.clip(burst, 0.0, 240.0)
                avg = burst.mean(axis=0, dtype=np.float64)
                from types import SimpleNamespace
                return SimpleNamespace(
                    frames_avg=avg, burst=burst,
                    metadata={"frame_dtype_full_scale": 255},
                )

        monkeypatch.setattr(
            "scripts.calibrate_psf_safe_exposure._make_fake_adapter",
            lambda: _GainAwareFakeCamera(),
        )

        plan = {
            "plan_id": "high_gain_ok",
            "wavelengths": [{"wavelength_nm": 550.0}],
            "lcd": {"mode": "all_transmissive", "settle_ms": 0, "display_index": -1},
            "camera_search": {
                "exposure_us_start": 50000.0, "exposure_us_min": 100.0,
                "exposure_us_step_factor": 0.5,
                "gain_db_min": 0.0, "gain_db_max": 18.0, "gain_db_step_db": 6.0,
                "frames_per_setting": 3,
                "binary_search_eps_us": 50.0,
            },
            "psf_safety": {"rule": "all_frames_all_pixels_strictly_below_full_scale"},
            "signal": {"percentile": 99.0, "min_signal_fraction_threshold": 0.30,
                       "min_dynamic_range_fraction": 0.05},
            "output": {
                "raw_h5": str(tmp_path / "hi_gain.h5"),
                "camera_params_json": str(tmp_path / "hi_gain.json"),
            },
            "lock_file": str(tmp_path / "lock.lock"),
        }

        _h5, result = run_psf_safe_exposure(plan, None, None, None, dry_run=True)

        assert result["global_safe_camera"]["gain_db"] >= 3.0
        assert result["selection_reason"] == "elevated_gain_due_to_low_signal"
        assert result["global_safe_camera"]["gain_elevated"] is True

    def test_psf_unsafe_at_higher_gain_skips_to_next(self, tmp_path, monkeypatch):
        from scripts.calibrate_psf_safe_exposure import run_psf_safe_exposure

        gain_sequence: list[float] = []

        class _UnsafeGainFakeCamera:
            def apply_camera_params(self, exposure_us=None, gain_db=None):
                self.exposure_us = float(exposure_us) if exposure_us else None
                self.gain_db = float(gain_db) if gain_db else None

            def acquire_burst(self, k: int):
                gain = float(self.gain_db or 0)
                gain_sequence.append(gain)
                exp = float(self.exposure_us or 0)
                rng = np.random.default_rng(42)
                if 0.0 < gain < 12.0 and exp < 40000.0:
                    burst = np.full((k, 8, 8), 260.0, dtype=np.float64)
                else:
                    burst = rng.normal(190.0, 20.0, (k, 8, 8)).astype(np.float64)
                    burst = np.clip(burst, 0.0, 240.0)
                avg = burst.mean(axis=0, dtype=np.float64)
                from types import SimpleNamespace
                return SimpleNamespace(
                    frames_avg=avg, burst=burst,
                    metadata={"frame_dtype_full_scale": 255},
                )

        monkeypatch.setattr(
            "scripts.calibrate_psf_safe_exposure._make_fake_adapter",
            lambda: _UnsafeGainFakeCamera(),
        )

        plan = {
            "plan_id": "skip_unsafe_gain",
            "wavelengths": [{"wavelength_nm": 550.0}],
            "lcd": {"mode": "all_transmissive", "settle_ms": 0, "display_index": -1},
            "camera_search": {
                "exposure_us_start": 50000.0, "exposure_us_min": 100.0,
                "exposure_us_step_factor": 0.5,
                "gain_db_min": 0.0, "gain_db_max": 18.0, "gain_db_step_db": 6.0,
                "frames_per_setting": 3,
                "binary_search_eps_us": 50.0,
            },
            "psf_safety": {"rule": "all_frames_all_pixels_strictly_below_full_scale"},
            "signal": {"percentile": 99.0, "min_signal_fraction_threshold": 0.30,
                       "min_dynamic_range_fraction": 0.05},
            "output": {
                "raw_h5": str(tmp_path / "skip_unsafe.h5"),
                "camera_params_json": str(tmp_path / "skip_unsafe.json"),
            },
            "lock_file": str(tmp_path / "lock.lock"),
        }

        _h5, result = run_psf_safe_exposure(plan, None, None, None, dry_run=True)

        assert result["global_safe_camera"]["gain_db"] not in (6.0,)
        assert result["selection_reason"] != "no_safe_usable_setting_found"
