from __future__ import annotations

from types import SimpleNamespace

import numpy as np

from scripts import calibrate_camera_exposure_sweep as sweep


def _psf_safe_plan(tmp_path, *, wavelengths=None) -> dict:
    if wavelengths is None:
        wavelengths = [
            {"wavelength_nm": 550.0, "grating": None, "settle_ms": 0},
            {"wavelength_nm": 650.0, "grating": None, "settle_ms": 0},
        ]
    return {
        "plan_id": "psf_safe_test",
        "wavelengths": wavelengths,
        "lcd": {"mode": "all_open", "mask_path": None, "settle_ms": 0},
        "camera_search": {
            "exposure_us_start": 200.0,
            "exposure_us_min": 100.0,
            "exposure_us_step_factor": 0.5,
            "gain_db_min": 0.0,
            "gain_db_max": 6.0,
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
            "raw_h5": str(tmp_path / "sweep.h5"),
            "camera_params_json": str(tmp_path / "camera_params_psf_safe.json"),
        },
        "lock_file": str(tmp_path / "lock"),
    }


def test_single_saturated_psf_core_pixel_fails_even_if_global_fraction_small():
    frame = np.full((1000, 1000), 64.0, dtype=np.float64)
    frame[500, 500] = 255.0

    metrics = sweep.compute_saturation_metrics(frame, full_scale=255.0)

    assert metrics["max_pixel"] == 255.0
    assert metrics["saturated_pixel_count"] == 1
    assert metrics["saturated_fraction"] < 0.00001
    assert metrics["psf_safe"] is False
    assert metrics["safe"] is False
    assert metrics["saturated"] is True


def test_hard_max_pixel_threshold_fails_before_full_scale():
    frame = np.full((64, 64), 32.0, dtype=np.float64)
    frame[10, 10] = 250.0

    metrics = sweep.compute_saturation_metrics(frame, full_scale=255.0)

    assert metrics["max_pixel"] == 250.0
    assert metrics["saturated_pixel_count"] == 0
    assert "max_pixel_at_or_above_hard_threshold" in metrics["saturation_reasons"]
    assert metrics["psf_safe"] is False


def test_p99_9_safe_but_max_pixel_saturated_is_unsafe():
    frame = np.full((1000, 1000), 80.0, dtype=np.float64)
    frame[0, 0] = 255.0

    metrics = sweep.compute_saturation_metrics(frame, full_scale=255.0)

    assert metrics["p99_9"] == 80.0
    assert metrics["saturated_fraction"] < 0.00001
    assert metrics["psf_safe"] is False


def test_saturated_fraction_is_diagnostic_and_cannot_override_hard_fail():
    frame = np.full((1000, 1000), 20.0, dtype=np.float64)
    frame[1, 1] = 255.0

    metrics = sweep.compute_saturation_metrics(
        frame,
        full_scale=255.0,
        saturated_pixel_count_threshold=100,
    )

    assert metrics["saturated_fraction"] < 0.00001
    assert metrics["saturated_pixel_count"] == 1
    assert metrics["psf_safe"] is False


def test_psf_safe_threshold_fails_at_90_percent_headroom():
    frame = np.full((32, 32), 10.0, dtype=np.float64)
    frame[0, 0] = 230.0

    metrics = sweep.compute_saturation_metrics(frame, full_scale=255.0)

    assert metrics["saturated_pixel_count"] == 0
    assert "max_pixel_at_or_above_psf_safe_threshold" in metrics["saturation_reasons"]
    assert metrics["psf_safe"] is False


def test_all_wavelengths_must_satisfy_max_pixel_headroom():
    results = [
        {"wavelength_nm": 550.0, "psf_safe": True, "low_signal": False},
        {"wavelength_nm": 650.0, "psf_safe": False, "low_signal": False},
    ]

    assert sweep._all_wavelengths_safe(results) is False


class _RecordingAdapter:
    def __init__(self, frame_factory):
        self._frame_factory = frame_factory
        self.exposure_us = None
        self.gain_db = None
        self.applied: list[tuple[float | None, float | None]] = []

    def apply_camera_params(self, exposure_us=None, gain_db=None):
        if exposure_us is not None:
            self.exposure_us = float(exposure_us)
        if gain_db is not None:
            self.gain_db = float(gain_db)
        self.applied.append((self.exposure_us, self.gain_db))

    def acquire_burst(self, k: int):
        frame = np.asarray(self._frame_factory(self.exposure_us, self.gain_db), dtype=np.float64)
        return SimpleNamespace(
            frames_avg=frame,
            burst=np.repeat(frame[None, :, :], k, axis=0),
            metadata={"frame_dtype_full_scale": 255},
        )


def test_gain_min_exposure_min_overexposed_fails_without_higher_gain(tmp_path, monkeypatch):
    adapter = _RecordingAdapter(lambda exposure_us, gain_db: np.full((20, 20), 255.0))
    monkeypatch.setattr(sweep, "_make_fake_adapter", lambda: adapter)

    _h5_path, result = sweep.run_exposure_sweep(
        _psf_safe_plan(tmp_path),
        None,
        None,
        None,
        dry_run=True,
    )

    assert result["global_safe_camera"]["exposure_us"] is None
    assert result["validity"]["psf_exposure_safe"] is False
    assert result["selection_reason"] == "no_safe_usable_setting_found"
    assert {gain for _exp, gain in adapter.applied} == {0.0}


def test_low_signal_is_required_before_elevating_gain(tmp_path, monkeypatch):
    def frame_factory(_exposure_us, gain_db):
        if gain_db == 0.0:
            return np.full((20, 20), 5.0, dtype=np.float64)
        return np.linspace(20.0, 120.0, 400, dtype=np.float64).reshape(20, 20)

    adapter = _RecordingAdapter(frame_factory)
    monkeypatch.setattr(sweep, "_make_fake_adapter", lambda: adapter)

    _h5_path, result = sweep.run_exposure_sweep(
        _psf_safe_plan(tmp_path),
        None,
        None,
        None,
        dry_run=True,
    )

    assert result["selection_reason"] == "elevated_gain_due_to_low_signal"
    assert result["global_safe_camera"]["gain_db"] == 3.0
    assert result["global_safe_camera"]["gain_elevated"] is True
    assert result["validity"]["psf_exposure_safe"] is True
