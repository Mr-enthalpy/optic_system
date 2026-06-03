from __future__ import annotations

import json
from pathlib import Path

import h5py
import numpy as np
import pytest

from scripts.calibrate_psf_safe_exposure import _build_result, _GainResult, run_psf_safe_exposure
from tasks.psf_safe_exposure_h5 import PsfSafeExposureWriter


def _plan(tmp_path: Path) -> dict:
    return {
        "plan_id": "bishe_psf_safe_exposure",
        "output": {
            "raw_h5": str(tmp_path / "bishe_psf_safe_exposure.h5"),
            "camera_params_json": str(tmp_path / "camera_params_psf_safe.json"),
        },
        "wavelengths": [{"wavelength_nm": 550.0}],
        "camera_search": {
            "gain_db_min": 0.0,
            "gain_db_max": 0.0,
            "gain_db_step_db": 3.0,
            "frames_per_setting": 3,
        },
        "psf_safety": {},
        "signal": {
            "percentile": 99.0,
            "min_signal_fraction_threshold": 0.10,
            "min_dynamic_range_fraction": 0.08,
        },
    }


def test_camera_params_psf_safe_json_schema(tmp_path: Path) -> None:
    final_rows = [
        {
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
        },
    ]
    accepted = _GainResult(
        gain_db=0.0,
        exposure_us=5000.0,
        psf_safe=True,
        low_signal=False,
        per_wavelength_bounds={"550.0": 5000.0},
        final_rows=final_rows,
    )

    result = _build_result(
        _plan(tmp_path), accepted, [], [], 255,
    )

    assert result["schema_version"] == 2
    assert result["phase"] == "3.0.5b"
    assert result["task"] == "psf_safe_camera_catalog"
    assert result["plan_id"] == "bishe_psf_safe_exposure"
    assert result["frame_dtype_full_scale"] == 255
    assert result["frame_dtype_full_scale_source"] == "unknown"
    assert result["global_safe_camera"]["exposure_us"] == 5000.0
    assert result["global_safe_camera"]["gain_db"] == 0.0
    assert result["global_safe_camera"]["frames_per_capture"] == 3
    assert result["global_safe_camera"]["roi"] is None
    assert result["global_safe_camera"]["gain_elevated"] is False
    assert result["global_safe_camera"]["derived_from"] == (
        "minimum_safe_exposure_across_wavelengths_without_common_gain_fallback"
    )
    assert result["valid_pixel_domain"] == {"type": "full_frame"}
    assert result["camera_param_catalog"]["550.0"]["recommended"]["exposure_us"] == 5000.0
    assert result["derived_profiles"]["global_safe_camera"]["exposure_us"] == 5000.0
    assert result["psf_safety_policy"] == {
        "rule": "all_frames_all_pixels_strictly_below_full_scale",
        "evaluated_on": "raw_burst_frames",
        "evaluated_domain": "valid_camera_pixel_domain",
        "allow_full_scale_pixel": False,
        "allow_non_finite_pixel": False,
        "frame_dtype_full_scale": 255,
        "frame_dtype_full_scale_source": "unknown",
        "valid_pixel_domain": {"type": "full_frame"},
    }
    assert "saturation_policy" not in result

    wl_metrics = result["per_wavelength_metrics"]["550.0"]
    assert wl_metrics["psf_safe"] is True
    assert wl_metrics["peak_pixel_burst"] == 200.0
    assert wl_metrics["peak_pixel_avg"] == 190.0
    assert wl_metrics["peak_pixel_fraction_burst"] == pytest.approx(200.0 / 255.0)
    assert wl_metrics["peak_margin_to_full_scale"] == 55.0
    assert wl_metrics["unsafe_reason"] is None
    assert "saturated_pixel_count" not in wl_metrics
    assert "saturated_fraction" not in wl_metrics
    assert "safe" not in wl_metrics

    assert result["validity"]["psf_exposure_safe"] is True


def test_psf_safe_exposure_h5_new_schema_and_no_old_fields(tmp_path: Path) -> None:
    h5_path = tmp_path / "sweep.h5"
    with PsfSafeExposureWriter(h5_path, plan_id="bishe_psf_safe_exposure") as writer:
        writer.set_full_scale(255, source="frame_metadata.format")
        writer.write_plan_json(_plan(tmp_path))
        writer.write_valid_pixel_domain(
            {
                "type": "full_frame",
                "frame_shape": [10, 10],
                "valid_pixel_count": 100,
                "invalid_pixel_count": 0,
            }
        )
        writer.append_sweep_row(
            wavelength_nm=550.0,
            exposure_us=50000.0,
            gain_db=0.0,
            frames_avg=np.ones((10, 10), dtype=np.float64) * 128,
            peak_pixel_burst=128.0,
            peak_pixel_avg=128.0,
            peak_pixel_fraction_burst=0.5,
            peak_margin_to_full_scale=127.0,
            p99_0_avg=120.0,
            p99_9_avg=125.0,
            unsafe_reason=None,
            psf_safe=True,
            p_signal=120.0,
            dynamic_range=100.0,
            low_signal=False,
            valid_pixel_count=100,
            invalid_pixel_count=0,
            invalid_domain_peak_pixel_burst=None,
            invalid_domain_full_scale_pixel_count=0,
            invalid_domain_nonfinite_pixel_count=0,
        )

    with h5py.File(h5_path, "r") as f:
        assert f["sweep/peak_pixel_burst"].shape == (1,)
        assert f["sweep/peak_pixel_avg"].shape == (1,)
        assert f["sweep/peak_pixel_fraction_burst"].shape == (1,)
        assert f["sweep/peak_margin_to_full_scale"].shape == (1,)
        assert f["sweep/unsafe_reason"].shape == (1,)
        assert f["sweep/psf_safe"].shape == (1,)
        assert f["sweep/p_signal"].shape == (1,)
        assert f["sweep/dynamic_range"].shape == (1,)
        assert f["sweep/low_signal"].shape == (1,)
        assert f["sweep/valid_pixel_count"].shape == (1,)
        assert f["sweep/invalid_pixel_count"].shape == (1,)
        assert f["sweep/invalid_domain_peak_pixel_burst"].shape == (1,)
        assert f["sweep/invalid_domain_full_scale_pixel_count"].shape == (1,)
        assert f["sweep/invalid_domain_nonfinite_pixel_count"].shape == (1,)
        assert f["sweep"].attrs["frame_dtype_full_scale"] == 255
        assert f["sweep/frame_dtype_full_scale"][()] == 255
        assert f["sweep"].attrs["frame_dtype_full_scale_source"] == "frame_metadata.format"
        assert f["sweep/frame_dtype_full_scale_source"].asstr()[()] == "frame_metadata.format"
        assert "valid_pixel_domain" in f
        policy = json.loads(f["valid_pixel_domain/policy_json"][0])
        assert policy["type"] == "full_frame"
        assert policy["valid_pixel_count"] == 100
        assert policy["invalid_pixel_count"] == 0

        assert "saturated_fraction" not in f["sweep"]
        assert "saturated_pixel_count" not in f["sweep"]
        assert "saturated_pixel_count_avg" not in f["sweep"]
        assert "saturated_pixel_count_burst" not in f["sweep"]
        assert "saturated_fraction_avg" not in f["sweep"]
        assert "saturated_fraction_burst" not in f["sweep"]
        assert "max_pixel" not in f["sweep"]
        assert "max_pixel_avg" not in f["sweep"]
        assert "max_pixel_burst" not in f["sweep"]
        assert "safe" not in f["sweep"]


def test_camera_params_records_artifact_hash_when_exclusion_policy_uses_source_artifact(
    tmp_path: Path,
) -> None:
    artifact = tmp_path / "valid_pixel_artifact.json"
    artifact.write_text('{"evidence": "unit-test"}', encoding="utf-8")
    plan = {
        "plan_id": "artifact_hash_test",
        "wavelengths": [{"wavelength_nm": 550.0}],
        "lcd": {"mode": "all_transmissive", "settle_ms": 0, "display_index": -1},
        "camera_search": {
            "exposure_us_start": 50000.0,
            "exposure_us_min": 100.0,
            "exposure_us_step_factor": 0.5,
            "gain_db_min": 0.0,
            "gain_db_max": 0.0,
            "gain_db_step_db": 3.0,
            "frames_per_setting": 3,
            "binary_search_eps_us": 50.0,
        },
        "psf_safety": {"rule": "all_frames_all_pixels_strictly_below_full_scale"},
        "signal": {
            "percentile": 99.0,
            "min_signal_fraction_threshold": 0.05,
            "min_dynamic_range_fraction": 0.02,
        },
        "output": {
            "raw_h5": str(tmp_path / "artifact_hash.h5"),
            "camera_params_json": str(tmp_path / "artifact_hash.json"),
        },
        "lock_file": str(tmp_path / "lock.lock"),
    }
    plan["valid_pixel_domain"] = {
        "type": "exclude_top_rows",
        "top_rows": 1,
        "source": "unit_test",
        "source_artifact": str(artifact),
        "reason": "unit test provenance",
    }

    _h5, result = run_psf_safe_exposure(plan, None, None, None, dry_run=True)

    vpd = result["psf_safety_policy"]["valid_pixel_domain"]
    assert vpd["type"] == "exclude_top_rows"
    assert vpd["source_artifact_exists"] is True
    assert isinstance(vpd["artifact_hash"], str)
    assert len(vpd["artifact_hash"]) == 64


import pytest
