from __future__ import annotations

import builtins
import json
from pathlib import Path

import h5py
import pytest

from scripts.capture_pupil_geometry import (
    load_pupil_geometry_plan,
    run_pupil_geometry_calibration,
)


def _camera_params(path: Path, *, include_profile: bool = True) -> Path:
    payload = {
        "schema_version": "1.0",
        "frame_dtype_full_scale": 255,
        "global_safe_camera": {
            "exposure_us": 50000.0,
            "gain_db": 0.0,
            "frames_per_capture": 3,
        },
        "verified_camera_profiles": {},
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
    if include_profile:
        payload["verified_camera_profiles"]["fast_pupil_scan"] = {
            "exposure_us": 40000.0,
            "gain_db": 1.0,
            "frames_per_capture": 2,
        }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _plan(tmp_path: Path, camera_params_path: Path) -> dict:
    return {
        "plan_id": "geom_dry",
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


def test_capture_pupil_geometry_dry_run_writes_raw_h5(tmp_path: Path) -> None:
    params = _camera_params(tmp_path / "camera_params.json")
    plan = _plan(tmp_path, params)

    out = run_pupil_geometry_calibration(plan, dry_run=True)

    assert out.exists()
    with h5py.File(out, "r") as f:
        assert f["references/bright_frame_avg"].shape == (64, 80)
        assert f["references/dark_frame_avg"].shape == (64, 80)
        assert f["bar_scan/x/positions"].shape[0] > 10
        assert f["bar_scan/y/positions"].shape[0] > 10
        assert f["radius_scan/radii"].shape[0] == 40
        raw = f["camera/camera_params_source_json"][()]
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        provenance = json.loads(raw)
        assert provenance["camera_profile_requested"] == "fast_pupil_scan"
        assert provenance["camera_profile_used"] == "fast_pupil_scan"
        assert provenance["fallback_used"] is False


def test_capture_pupil_geometry_status_fields(tmp_path: Path) -> None:
    params = _camera_params(tmp_path / "camera_params.json")
    plan = _plan(tmp_path, params)
    status_dir = tmp_path / "status"

    run_pupil_geometry_calibration(plan, dry_run=True, status_dir=status_dir, status_preview_every=100)

    state = json.loads((status_dir / "state.json").read_text(encoding="utf-8"))
    assert state["phase"] == "3.1"
    assert state["task"] == "pupil_geometry_calibration"
    assert state["current_stage"] == "complete"
    assert state["camera_profile_used"] == "fast_pupil_scan"
    assert "current_mask_id" in state
    assert "current_radius" in state


def test_capture_pupil_geometry_dry_run_imports_no_hardware(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    params = _camera_params(tmp_path / "camera_params.json")
    plan = _plan(tmp_path, params)
    original_import = builtins.__import__

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name.startswith("devices") or name.startswith("capture"):
            raise AssertionError(f"dry-run imported hardware module {name}")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    out = run_pupil_geometry_calibration(plan, dry_run=True)
    assert out.exists()


def test_capture_pupil_geometry_profile_fallback_is_recorded(tmp_path: Path) -> None:
    params = _camera_params(tmp_path / "camera_params.json", include_profile=False)
    plan = _plan(tmp_path, params)

    out = run_pupil_geometry_calibration(plan, dry_run=True)

    with h5py.File(out, "r") as f:
        raw = f["camera/camera_params_source_json"][()]
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        provenance = json.loads(raw)
        assert provenance["camera_profile_used"] == "global_safe_camera"
        assert provenance["fallback_used"] is True


def test_load_pupil_geometry_plan_requires_geometry_strategy(tmp_path: Path) -> None:
    params = _camera_params(tmp_path / "camera_params.json")
    plan = _plan(tmp_path, params)
    plan["calibration"]["strategy"] = "fixed_single_pass"
    path = tmp_path / "plan.yaml"
    import yaml

    path.write_text(yaml.safe_dump(plan), encoding="utf-8")
    with pytest.raises(ValueError, match="bar_profiles_plus_radius_scan"):
        load_pupil_geometry_plan(path)
