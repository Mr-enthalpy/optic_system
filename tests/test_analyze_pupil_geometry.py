from __future__ import annotations

import json
from pathlib import Path

import h5py

from scripts.analyze_pupil_geometry import analyze_pupil_geometry
from scripts.capture_pupil_geometry import run_pupil_geometry_calibration


def _camera_params(path: Path) -> Path:
    payload = {
        "schema_version": "1.0",
        "frame_dtype_full_scale": 255,
        "global_safe_camera": {
            "exposure_us": 50000.0,
            "gain_db": 0.0,
            "frames_per_capture": 3,
        },
        "verified_camera_profiles": {
            "fast_pupil_scan": {
                "exposure_us": 40000.0,
                "gain_db": 1.0,
                "frames_per_capture": 2,
            }
        },
        "psf_safety_policy": {
            "rule": "all_frames_all_pixels_strictly_below_full_scale",
            "evaluated_on": "raw_burst_frames",
            "evaluated_domain": "valid_camera_pixel_domain",
            "allow_full_scale_pixel": False,
            "allow_non_finite_pixel": False,
            "frame_dtype_full_scale": 255,
            "valid_pixel_domain": {
                "type": "full_frame",
                "valid_pixel_count": 16,
                "invalid_pixel_count": 0,
            },
        },
        "validity": {
            "exposure_safety_valid": True,
            "psf_exposure_safe": True,
            "scientific_calibration_valid": False,
            "training_ready": False,
        },
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _plan(tmp_path: Path, camera_params_path: Path) -> dict:
    return {
        "plan_id": "geom_analysis",
        "phase": "3.1",
        "camera_params_source": str(camera_params_path),
        "camera_profile": "fast_pupil_scan",
        "allow_global_safe_camera_fallback": True,
        "wavelength": {"wavelength_nm": 550.0, "grating": 1, "settle_ms": 0},
        "lcd": {
            "display_index": 1,
            "subpixel_axis": 1,
            "settle_ms": 0,
            "physical_shape": [90, 270],
        },
        "calibration": {
            "strategy": "bar_profiles_plus_radius_scan",
            "bar_scan": {
                "bar_width": 8,
                "step": 4,
                "bg_code": 255,
                "bar_code": 0,
                "avg_n": 2,
                "scan_range": None,
            },
            "radius_scan": {
                "r_min_factor": 0.0,
                "r_max_factor": 2.0,
                "steps": 40,
                "aperture_code": 255,
                "background_code": 0,
                "avg_n": 2,
            },
            "effective_window": {"shape": "circle", "radius_factor_of_b": 0.9},
        },
        "output": {
            "raw_h5": str(tmp_path / "geometry.h5"),
            "output_dir": str(tmp_path / "out"),
        },
        "lock_file": str(tmp_path / "capture.lock"),
    }


def test_analyze_pupil_geometry_outputs_effective_window(tmp_path: Path) -> None:
    params = _camera_params(tmp_path / "camera_params.json")
    plan = _plan(tmp_path, params)
    raw_h5 = run_pupil_geometry_calibration(plan, dry_run=True)
    out_dir = tmp_path / "out"

    result = analyze_pupil_geometry(raw_h5, out_dir)

    assert result["task"] == "pupil_geometry_calibration"
    assert result["strategy"] == "bar_profiles_plus_radius_scan"
    assert result["window_type"] == "circle"
    assert result["radius_source"] == "factor_of_ellipse_semi_minor"
    assert abs(result["radius"] - 0.9 * result["ellipse"]["b"]) < 1e-9
    assert result["validity"]["scientific_calibration_valid"] is False
    assert result["validity"]["training_ready"] is False
    assert (out_dir / "effective_pupil_window.json").exists()
    assert (out_dir / "effective_pupil_window.npy").exists()
    assert (out_dir / "effective_pupil_window.png").exists()
    assert (out_dir / "x_profile.csv").exists()
    assert (out_dir / "y_profile.csv").exists()
    assert (out_dir / "radius_scan.csv").exists()
    assert (out_dir / "bar_profile_fit.png").exists()
    assert (out_dir / "radius_overlap_fit.png").exists()
    assert (out_dir / "pupil_geometry_report.md").exists()


def test_effective_window_json_contains_center_ellipse_and_camera_profile(tmp_path: Path) -> None:
    params = _camera_params(tmp_path / "camera_params.json")
    plan = _plan(tmp_path, params)
    raw_h5 = run_pupil_geometry_calibration(plan, dry_run=True)
    out_dir = tmp_path / "out"

    analyze_pupil_geometry(raw_h5, out_dir)

    saved = json.loads((out_dir / "effective_pupil_window.json").read_text(encoding="utf-8"))
    assert saved["center"]["x"] > 0
    assert saved["center"]["y"] > 0
    assert saved["ellipse"]["a"] >= saved["ellipse"]["b"] > 0
    assert saved["camera_profile_requested"] == "fast_pupil_scan"
    assert saved["camera_profile_used"] == "fast_pupil_scan"
    assert saved["fallback_used"] is False


def test_effective_window_npy_matches_lcd_physical_shape(tmp_path: Path) -> None:
    import numpy as np

    params = _camera_params(tmp_path / "camera_params.json")
    plan = _plan(tmp_path, params)
    raw_h5 = run_pupil_geometry_calibration(plan, dry_run=True)
    out_dir = tmp_path / "out"

    analyze_pupil_geometry(raw_h5, out_dir)

    with h5py.File(raw_h5, "r") as f:
        lcd = json.loads(f["lcd/metadata_json"][()].decode("utf-8"))
    window = np.load(out_dir / "effective_pupil_window.npy")
    assert list(window.shape) == lcd["physical_shape"]
    assert window.dtype == np.uint8
    assert window.max() == 255


def test_report_states_not_scientific_or_training_ready(tmp_path: Path) -> None:
    params = _camera_params(tmp_path / "camera_params.json")
    plan = _plan(tmp_path, params)
    raw_h5 = run_pupil_geometry_calibration(plan, dry_run=True)
    out_dir = tmp_path / "out"

    analyze_pupil_geometry(raw_h5, out_dir)

    report = (out_dir / "pupil_geometry_report.md").read_text(encoding="utf-8")
    assert "effective pupil window" in report
    assert "not final scientific calibration" in report
    assert "not training-ready data" in report
