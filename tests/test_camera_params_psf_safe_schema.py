from __future__ import annotations

import json
from pathlib import Path

import h5py
import numpy as np

from scripts.calibrate_psf_safe_exposure import _build_result
from tasks.psf_safe_exposure_h5 import PsfSafeExposureWriter


def _plan(tmp_path: Path) -> dict:
    return {
        "plan_id": "bishe_psf_safe_exposure",
        "output": {
            "raw_h5": str(tmp_path / "bishe_psf_safe_exposure.h5"),
            "camera_params_json": str(tmp_path / "camera_params_psf_safe.json"),
        },
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


def test_camera_params_psf_safe_json_schema(tmp_path: Path) -> None:
    result = _build_result(
        _plan(tmp_path),
        exposure_us=5000.0,
        gain_db=0.0,
        all_results=[
            {
                "wavelength_nm": 550.0,
                "exposure_us": 5000.0,
                "gain_db": 0.0,
                "max_pixel": 218.0,
                "max_pixel_avg": 180.0,
                "max_pixel_burst": 218.0,
                "p99_9": 120.0,
                "saturated_pixel_count": 0,
                "saturated_pixel_count_avg": 0,
                "saturated_pixel_count_burst": 0,
                "saturated_fraction": 0.0,
                "saturated_fraction_avg": 0.0,
                "saturated_fraction_burst": 0.0,
                "p_signal": 110.0,
                "safe": True,
                "psf_safe": True,
                "saturated": False,
                "low_signal": False,
            }
        ],
        full_scale=255.0,
        selection_reason="psf_safe_max_pixel_headroom",
    )

    assert result["schema_version"] == "1.0"
    assert result["plan_id"] == "bishe_psf_safe_exposure"
    assert result["source_raw_capture_h5"].endswith("bishe_psf_safe_exposure.h5")
    assert result["frame_dtype_full_scale"] == 255
    assert result["global_safe_camera"] == {
        "exposure_us": 5000.0,
        "gain_db": 0.0,
        "frames_per_capture": 5,
        "roi": None,
        "gain_elevated": False,
    }
    assert result["saturation_policy"] == {
        "percentile": 99.9,
        "max_pixel_fraction_threshold": 0.90,
        "hard_max_pixel_fraction_threshold": 0.98,
        "saturated_pixel_count_threshold": 0,
        "saturated_fraction_diagnostic_only": True,
        "psf_safe_uses_burst_max_pixel": True,
        "bad_pixel_mask": None,
        "bad_pixel_mask_policy": "none; any full-scale burst pixel is unsafe",
    }
    wl = result["per_wavelength_metrics"]["550.0"]
    assert wl["max_pixel"] == 218.0
    assert wl["max_pixel_avg"] == 180.0
    assert wl["max_pixel_burst"] == 218.0
    assert wl["p99_9"] == 120.0
    assert wl["saturated_pixel_count"] == 0
    assert wl["saturated_pixel_count_avg"] == 0
    assert wl["saturated_pixel_count_burst"] == 0
    assert wl["saturated_fraction"] == 0.0
    assert wl["saturated_fraction_avg"] == 0.0
    assert wl["saturated_fraction_burst"] == 0.0
    assert wl["safe"] is True
    assert wl["psf_safe"] is True
    assert wl["low_signal"] is False
    assert result["selection_reason"] == "psf_safe_max_pixel_headroom"
    assert result["validity"] == {
        "exposure_safety_valid": True,
        "psf_exposure_safe": True,
        "scientific_calibration_valid": False,
        "training_ready": False,
    }


def test_psf_safe_exposure_h5_psf_safe_schema_and_processing_flags(tmp_path: Path) -> None:
    h5_path = tmp_path / "sweep.h5"
    with PsfSafeExposureWriter(h5_path, plan_id="bishe_psf_safe_exposure") as writer:
        writer.set_full_scale(255)
        writer.write_plan_json(_plan(tmp_path))
        writer.append_sweep_row(
            wavelength_nm=550.0,
            exposure_us=5000.0,
            gain_db=0.0,
            frames_avg=np.full((8, 8), 42.0, dtype=np.float64),
            max_pixel=42.0,
            p99_9=42.0,
            saturated_pixel_count=0,
            saturated_fraction=0.0,
            safe=True,
            psf_safe=True,
            p_signal=42.0,
            low_signal=False,
        )

    with h5py.File(h5_path, "r") as f:
        assert f["sweep"].attrs["frame_dtype_full_scale"] == 255
        assert f["sweep/frame_dtype_full_scale"][()] == 255
        assert f["sweep/max_pixel"].shape == (1,)
        assert f["sweep/max_pixel_avg"].shape == (1,)
        assert f["sweep/max_pixel_burst"].shape == (1,)
        assert f["sweep/p99_9"].shape == (1,)
        assert f["sweep/saturated_pixel_count"].shape == (1,)
        assert f["sweep/saturated_pixel_count_avg"].shape == (1,)
        assert f["sweep/saturated_pixel_count_burst"].shape == (1,)
        assert f["sweep/saturated_fraction"].shape == (1,)
        assert f["sweep/saturated_fraction_avg"].shape == (1,)
        assert f["sweep/saturated_fraction_burst"].shape == (1,)
        assert f["sweep/psf_safe"].shape == (1,)
        assert f["sweep/safe"].shape == (1,)
        assert f["sweep/exposure_us"].shape == (1,)
        assert f["sweep/gain_db"].shape == (1,)
        assert f["sweep/wavelength_nm"].shape == (1,)
        assert f["capture/plan_json"][()] is not None

        flags_raw = f["capture/processing_flags_json"][()]
        if isinstance(flags_raw, bytes):
            flags_raw = flags_raw.decode()
        flags = json.loads(flags_raw)
        assert flags["phase"] == "phase3_0_5b_psf_safe_exposure"
        assert flags["completed"] is True
        assert flags["scientific_calibration_valid"] is False
        assert flags["training_ready"] is False
