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
        assert result["unsafe_reason"] == "peak_pixel_at_or_above_full_scale"

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
        assert result["unsafe_reason"] == "non_finite_pixel"

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
        burst = np.array([[[128.0]]], dtype=np.float64)
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


class TestPsfSafeExposureWriter:
    def test_writer_creates_h5(self):
        from tasks.psf_safe_exposure_h5 import PsfSafeExposureWriter
        d = tempfile.mkdtemp(prefix="optsys_sw_")
        p = Path(d) / "test_sweep.h5"
        try:
            with PsfSafeExposureWriter(p, plan_id="test") as w:
                w.write_plan_json({"plan_id": "test"})
                frame = np.ones((100, 100), dtype=np.float64) * 128
                w.append_sweep_row(
                    wavelength_nm=550.0, exposure_us=50000.0, gain_db=0.0,
                    frames_avg=frame,
                    peak_pixel_burst=128.0, peak_pixel_avg=128.0,
                    peak_pixel_fraction_burst=0.5, peak_margin_to_full_scale=127.0,
                    p99_0_avg=127.0, p99_9_avg=127.0,
                    unsafe_reason=None, psf_safe=True,
                    p_signal=128.0, dynamic_range=100.0, low_signal=False,
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
        plan = {
            "plan_id": "test",
            "output": {"raw_h5": "data/raw/test.h5"},
            "wavelengths": [{"wavelength_nm": 550.0}],
            "camera_search": {"gain_db_min": 0.0, "frames_per_setting": 3},
            "psf_safety": {},
            "signal": {
                "percentile": 99.0,
                "min_signal_fraction_threshold": 0.10,
                "min_dynamic_range_fraction": 0.08,
            },
        }
        all_results = [{
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
        }]

        result = _build_result(plan, 5000.0, 0.0, all_results, 255.0, selection_reason="psf_safe")

        assert result["psf_safety_policy"]["rule"] == "all_frames_all_pixels_strictly_below_full_scale"
        assert result["psf_safety_policy"]["allow_full_scale_pixel"] is False
        wl_550 = result["per_wavelength_metrics"]["550.0"]
        assert wl_550["psf_safe"] is True
        assert wl_550["unsafe_reason"] is None
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
