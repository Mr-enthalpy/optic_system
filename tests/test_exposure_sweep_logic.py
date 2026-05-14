from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest

_repo_root = Path(__file__).resolve().parents[1]
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from scripts.calibrate_camera_exposure_sweep import (
    compute_saturation_metrics,
    compute_signal_metrics,
    infer_full_scale,
)


class TestSaturationMetrics:
    def test_all_black_not_saturated(self):
        frame = np.zeros((100, 100), dtype=np.float64)
        result = compute_saturation_metrics(frame, full_scale=255.0)
        assert result["saturated"] is False
        assert result["max_pixel"] == 0.0
        assert result["p99_9"] == 0.0
        assert result["saturated_fraction"] == 0.0

    def test_all_white_saturated(self):
        frame = np.full((100, 100), 255.0, dtype=np.float64)
        result = compute_saturation_metrics(frame, full_scale=255.0)
        assert result["saturated"] is True
        assert result["max_pixel"] == 255.0
        assert result["saturated_fraction"] == 1.0

    def test_near_max_but_safe(self):
        frame = np.full((100, 100), 200.0, dtype=np.float64)
        result = compute_saturation_metrics(frame, full_scale=255.0)
        assert result["saturated"] is False
        assert result["p99_9"] == 200.0

    def test_single_hot_pixel_fails_psf_safe(self):
        frame = np.full((100, 100), 100.0, dtype=np.float64)
        frame[0, 0] = 255.0
        result = compute_saturation_metrics(frame, full_scale=255.0)
        assert result["saturated"] is True, (
            "a single full-scale PSF core pixel must reject the setting"
        )
        assert result["psf_safe"] is False
        assert result["max_pixel"] == 255.0
        assert result["p99_9"] == 100.0
        assert result["saturated_pixel_count"] == 1
        assert result["saturated_fraction"] < 0.001

    def test_many_saturated_pixels_triggers(self):
        frame = np.full((100, 100), 100.0, dtype=np.float64)
        frame[:5, :] = 255.0
        result = compute_saturation_metrics(frame, full_scale=255.0)
        assert result["saturated"] is True
        assert result["saturated_fraction"] == pytest.approx(0.05, abs=0.01)

    def test_p99_9_is_diagnostic_for_psf_safe_mode(self):
        frame = np.full((100, 100), 220.0, dtype=np.float64)
        result = compute_saturation_metrics(
            frame, full_scale=255.0, max_pixel_fraction_threshold=0.90,
        )
        assert result["saturated"] is False
        assert result["p99_9"] == 220.0

    def test_16bit_full_scale(self):
        frame = np.full((100, 100), 40000.0, dtype=np.float64)
        result = compute_saturation_metrics(frame, full_scale=65535.0)
        assert result["saturated"] is False
        assert result["max_pixel"] == 40000.0

    def test_16bit_near_full(self):
        frame = np.full((100, 100), 60000.0, dtype=np.float64)
        result = compute_saturation_metrics(
            frame, full_scale=65535.0, max_pixel_fraction_threshold=0.90,
        )
        is_near_full = 60000.0 >= 65535.0 * 0.90
        assert result["saturated"] == is_near_full


class TestSignalMetrics:
    def test_bright_signal_usable(self):
        rng = np.random.default_rng(42)
        frame = np.clip(rng.normal(128, 30, (100, 100)), 0, 255).astype(np.float64)
        result = compute_signal_metrics(frame, full_scale=255.0)
        assert result["usable"] is True
        assert result["p_signal"] > 50

    def test_dim_signal_not_usable(self):
        frame = np.full((100, 100), 5.0, dtype=np.float64)
        result = compute_signal_metrics(
            frame, full_scale=255.0,
            min_signal_fraction_threshold=0.10,
        )
        assert result["usable"] is False
        assert result["p_signal"] == 5.0

    def test_dynamic_range_too_small(self):
        frame = np.full((100, 100), 100.0, dtype=np.float64)
        result = compute_signal_metrics(
            frame, full_scale=255.0,
            min_dynamic_range_fraction=0.50,
        )
        assert result["usable"] is False

    def test_good_dynamic_range_usable(self):
        frame = np.random.default_rng(42).normal(128, 40, (100, 100))
        frame = np.clip(frame, 0, 255)
        result = compute_signal_metrics(
            frame, full_scale=255.0,
            min_signal_fraction_threshold=0.10,
            min_dynamic_range_fraction=0.05,
        )
        assert result["usable"] is True


class TestFullScaleInference:
    def test_uint8(self):
        assert infer_full_scale(np.zeros((10, 10), dtype=np.uint8)) == 255

    def test_uint16(self):
        assert infer_full_scale(np.zeros((10, 10), dtype=np.uint16)) == 65535

    def test_float64(self):
        with pytest.raises(ValueError, match="float"):
            infer_full_scale(np.zeros((10, 10), dtype=np.float64))


class TestSweepDecisionLogic:
    def test_all_safe_accepts(self):
        results = [
            {"saturated": False, "p_signal": 150.0, "low_signal": False},
            {"saturated": False, "p_signal": 140.0, "low_signal": False},
        ]
        from scripts.calibrate_camera_exposure_sweep import _all_wavelengths_safe
        assert _all_wavelengths_safe(results) is True

    def test_one_saturated_rejects(self):
        results = [
            {"saturated": False, "p_signal": 150.0, "low_signal": False},
            {"saturated": True, "p_signal": 255.0, "low_signal": False},
        ]
        from scripts.calibrate_camera_exposure_sweep import _all_wavelengths_safe
        assert _all_wavelengths_safe(results) is False

    def test_worst_signal_identified(self):
        results = [
            {"saturated": False, "p_signal": 150.0},
            {"saturated": False, "p_signal": 20.0},
            {"saturated": False, "p_signal": 100.0},
        ]
        from scripts.calibrate_camera_exposure_sweep import _worst_signal_wavelength
        worst = _worst_signal_wavelength(results)
        assert worst["p_signal"] == 20.0


class TestExposureSweepWriter:
    def test_writer_creates_h5(self):
        from tasks.exposure_sweep_h5 import ExposureSweepWriter
        d = tempfile.mkdtemp(prefix="optsys_sw_")
        p = Path(d) / "test_sweep.h5"
        try:
            with ExposureSweepWriter(p, plan_id="test") as w:
                w.write_plan_json({"plan_id": "test"})
                frame = np.ones((100, 100), dtype=np.float64) * 128
                w.append_sweep_row(
                    wavelength_nm=550.0, exposure_us=5000.0, gain_db=0.0,
                    frames_avg=frame, max_pixel=128.0, p99_9=128.0,
                    saturated_pixel_count=0,
                    saturated_fraction=0.0, safe=True, psf_safe=True,
                    p_signal=128.0, low_signal=False,
                )
            import h5py
            with h5py.File(p, "r") as f:
                assert f["sweep/exposure_us"].shape == (1,)
                assert bool(f["sweep/safe"][0]) is True
                assert bool(f["sweep/psf_safe"][0]) is True
                assert int(f["sweep/saturated_pixel_count"][0]) == 0
                assert f["raw/frames_avg"].shape[0] == 1
                assert f["capture/plan_id"][()] == b"test" or f["capture/plan_id"][()] == "test"
        finally:
            import shutil
            shutil.rmtree(d, ignore_errors=True)

    def test_writer_multiple_rows(self):
        from tasks.exposure_sweep_h5 import ExposureSweepWriter
        d = tempfile.mkdtemp(prefix="optsys_sw_")
        p = Path(d) / "test_multi.h5"
        try:
            with ExposureSweepWriter(p, plan_id="multi") as w:
                for i in range(5):
                    frame = np.ones((50, 50), dtype=np.float64) * (i * 40)
                    w.append_sweep_row(
                        wavelength_nm=float(500 + i * 10),
                        exposure_us=float(1000 * (i + 1)),
                        gain_db=float(i),
                        frames_avg=frame,
                        max_pixel=float(i * 40),
                        p99_9=float(i * 40),
                        saturated_pixel_count=0,
                        saturated_fraction=0.0,
                        safe=(i < 4),
                        psf_safe=(i < 4),
                        p_signal=float(i * 40),
                        low_signal=(i == 0),
                    )
            import h5py
            with h5py.File(p, "r") as f:
                assert f["sweep/exposure_us"].shape == (5,)
                assert f["sweep/safe"].shape == (5,)
                assert f["sweep/psf_safe"].shape == (5,)
                assert not f["sweep/safe"][4]
                assert not f["sweep/psf_safe"][4]
                assert f["sweep/low_signal"][0]
        finally:
            import shutil
            shutil.rmtree(d, ignore_errors=True)

    def test_processing_flags(self):
        from tasks.exposure_sweep_h5 import ExposureSweepWriter
        d = tempfile.mkdtemp(prefix="optsys_sw_")
        p = Path(d) / "test_pf.h5"
        try:
            with ExposureSweepWriter(p, plan_id="pf_test") as w:
                pass
            import h5py
            with h5py.File(p, "r") as f:
                pf_raw = f["capture/processing_flags_json"][()]
                if isinstance(pf_raw, bytes):
                    pf_raw = pf_raw.decode()
                pf = json.loads(pf_raw)
                assert pf["scientific_calibration_valid"] is False
                assert pf["training_ready"] is False
                assert pf["phase"] == "phase3_0_5b_psf_safe_exposure"
                assert pf["completed"] is True
        finally:
            import shutil
            shutil.rmtree(d, ignore_errors=True)

    def test_full_scale_setting(self):
        from tasks.exposure_sweep_h5 import ExposureSweepWriter
        d = tempfile.mkdtemp(prefix="optsys_sw_")
        p = Path(d) / "test_fs.h5"
        try:
            with ExposureSweepWriter(p, plan_id="fs_test") as w:
                w.set_full_scale(65535)
            import h5py
            with h5py.File(p, "r") as f:
                assert f["sweep"].attrs["frame_dtype_full_scale"] == 65535
                assert f["sweep/frame_dtype_full_scale"][()] == 65535
        finally:
            import shutil
            shutil.rmtree(d, ignore_errors=True)


class TestCameraParamsJSON:
    def test_schema_complete(self):
        from scripts.calibrate_camera_exposure_sweep import _build_result
        plan = {
            "plan_id": "test",
            "output": {"raw_h5": "data/raw/test.h5"},
            "wavelengths": [
                {"wavelength_nm": 450.0},
                {"wavelength_nm": 550.0},
                {"wavelength_nm": 650.0},
            ],
            "saturation": {
                "percentile": 99.9,
                "max_pixel_fraction_threshold": 0.90,
                "hard_max_pixel_fraction_threshold": 0.98,
                "saturated_pixel_count_threshold": 0,
            },
            "signal": {
                "percentile": 99.0,
                "min_signal_fraction_threshold": 0.10,
                "min_dynamic_range_fraction": 0.08,
            },
            "camera_search": {
                "frames_per_setting": 5,
                "gain_db_min": 0.0,
            },
        }
        all_results = [
            {"wavelength_nm": 450.0, "max_pixel": 100, "p99_9": 90,
             "saturated_pixel_count": 0, "saturated_fraction": 0.0,
             "psf_safe": True, "safe": True, "saturated": False,
             "p_signal": 85, "low_signal": False},
        ]
        result = _build_result(
            plan, exposure_us=3125.0, gain_db=0.0,
            all_results=all_results, full_scale=255.0,
            selection_reason="psf_safe_max_pixel_headroom",
        )

        assert result["schema_version"] == "1.0"
        assert result["frame_dtype_full_scale"] == 255
        assert result["global_safe_camera"]["exposure_us"] == 3125.0
        assert result["global_safe_camera"]["gain_db"] == 0.0
        assert result["global_safe_camera"]["gain_elevated"] is False
        assert result["selection_reason"] == "psf_safe_max_pixel_headroom"
        assert result["validity"]["exposure_safety_valid"] is True
        assert result["validity"]["psf_exposure_safe"] is True
        assert result["validity"]["scientific_calibration_valid"] is False
        assert result["validity"]["training_ready"] is False
        assert "450.0" in result["per_wavelength_metrics"]

    def test_gain_elevated_true(self):
        from scripts.calibrate_camera_exposure_sweep import _build_result
        plan = {
            "plan_id": "test",
            "output": {"raw_h5": "data/raw/test.h5"},
            "wavelengths": [{"wavelength_nm": 550.0}],
            "saturation": {
                "percentile": 99.9,
                "max_pixel_fraction_threshold": 0.90,
                "hard_max_pixel_fraction_threshold": 0.98,
                "saturated_pixel_count_threshold": 0,
            },
            "signal": {
                "percentile": 99.0,
                "min_signal_fraction_threshold": 0.10,
                "min_dynamic_range_fraction": 0.08,
            },
            "camera_search": {
                "frames_per_setting": 5,
                "gain_db_min": 0.0,
            },
        }
        result = _build_result(
            plan, exposure_us=5000.0, gain_db=6.0,
            all_results=[], full_scale=255.0,
            selection_reason="elevated_gain_due_to_low_signal",
        )
        assert result["global_safe_camera"]["gain_elevated"] is True
        assert result["selection_reason"] == "elevated_gain_due_to_low_signal"

    def test_failure_no_safe_setting(self):
        from scripts.calibrate_camera_exposure_sweep import _build_result
        plan = {
            "plan_id": "test",
            "output": {"raw_h5": "data/raw/test.h5"},
            "wavelengths": [{"wavelength_nm": 550.0}],
            "saturation": {
                "percentile": 99.9,
                "max_pixel_fraction_threshold": 0.90,
                "hard_max_pixel_fraction_threshold": 0.98,
                "saturated_pixel_count_threshold": 0,
            },
            "signal": {
                "percentile": 99.0,
                "min_signal_fraction_threshold": 0.10,
                "min_dynamic_range_fraction": 0.08,
            },
            "camera_search": {
                "frames_per_setting": 5,
                "gain_db_min": 0.0,
            },
        }
        result = _build_result(
            plan, exposure_us=None, gain_db=None,
            all_results=[], full_scale=255.0,
            selection_reason="no_safe_usable_setting_found",
            error="even min exposure saturated",
        )
        assert result["global_safe_camera"]["exposure_us"] is None
        assert result["validity"]["exposure_safety_valid"] is False
        assert result["error"] is not None


class TestDryRun:
    def test_dry_run_produces_h5_and_json(self, tmp_path):
        plan_dict = {
            "plan_id": "dry_test",
            "wavelengths": [
                {"wavelength_nm": 550.0, "grating": None, "settle_ms": 0},
            ],
            "lcd": {"mode": "all_open", "mask_path": None, "settle_ms": 0},
            "camera_search": {
                "exposure_us_start": 10000,
                "exposure_us_min": 100,
                "exposure_us_step_factor": 0.5,
                "gain_db_min": 0.0,
                "gain_db_max": 24.0,
                "gain_db_step_db": 3.0,
                "frames_per_setting": 3,
            },
            "saturation": {
                "percentile": 99.9,
                "max_pixel_fraction_threshold": 0.90,
                "hard_max_pixel_fraction_threshold": 0.98,
                "saturated_pixel_count_threshold": 0,
            },
            "signal": {
                "percentile": 99.0,
                "min_signal_fraction_threshold": 0.10,
                "min_dynamic_range_fraction": 0.08,
            },
            "output": {
                "raw_h5": str(tmp_path / "dry_test.h5"),
                "camera_params_json": str(tmp_path / "dry_test.json"),
            },
            "lock_file": str(tmp_path / "lock_test.lock"),
        }

        from scripts.calibrate_camera_exposure_sweep import run_exposure_sweep
        h5_path, result = run_exposure_sweep(
            plan_dict, None, None, None, dry_run=True,
        )

        assert h5_path.exists()
        import h5py
        with h5py.File(h5_path, "r") as f:
            assert f["sweep/exposure_us"].shape[0] > 0
            assert f["capture/plan_id"][()] is not None

        assert "global_safe_camera" in result
        assert "selection_reason" in result
        assert result["validity"]["scientific_calibration_valid"] is False
        assert result["validity"]["training_ready"] is False
