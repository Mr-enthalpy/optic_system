from __future__ import annotations

import copy

import pytest

from scripts.capture_psf_roi import run_capture_psf_roi
from tasks.psf_phase3 import Phase32PlanError, load_yaml_plan, resolve_psf_roi_record, validate_phase32_plan


def test_phase32_plans_load_and_validate():
    roi_plan = load_yaml_plan("plans/bishe_psf_roi.yaml")
    repeat_plan = load_yaml_plan("plans/bishe_psf_repeatability.yaml")
    validate_phase32_plan(roi_plan, task="roi", hardware=False)
    validate_phase32_plan(repeat_plan, task="repeatability", hardware=False)
    assert roi_plan["lcd"]["settle_ms"] >= 100
    assert roi_plan["psf_roi"]["candidate_crop_sizes"] == [[256, 256], [512, 512], [768, 768], [1024, 1024]]
    assert repeat_plan["lcd"]["settle_ms"] >= 100
    assert repeat_plan["camera_profile_policy"] == "wavelength_recommended"
    assert [entry["wavelength_nm"] for entry in repeat_plan["wavelengths"]] == [450.0, 550.0, 650.0]


def test_hardware_validation_rejects_unsafe_lcd_settle():
    roi_plan = load_yaml_plan("plans/bishe_psf_roi.yaml")
    bad = copy.deepcopy(roi_plan)
    bad["lcd"]["settle_ms"] = 2
    with pytest.raises(Phase32PlanError, match="lcd.settle_ms >= 100"):
        validate_phase32_plan(bad, task="roi", hardware=True)


def test_dry_run_validation_allows_no_hardware_imports():
    repeat_plan = load_yaml_plan("plans/bishe_psf_repeatability.yaml")
    validate_phase32_plan(repeat_plan, task="repeatability", hardware=False)


def test_hardware_capture_refuses_existing_raw_h5(tmp_path):
    raw_h5 = tmp_path / "existing.h5"
    raw_h5.write_bytes(b"existing")
    plan = load_yaml_plan("plans/bishe_psf_roi.yaml")
    plan["output"]["raw_h5"] = str(raw_h5)
    with pytest.raises(FileExistsError, match="Refusing to overwrite existing hardware raw HDF5"):
        run_capture_psf_roi(plan, dry_run=False)


def test_resolve_psf_roi_record_uses_explicit_key_without_legacy_fallback():
    psf_roi = {
        "phase": "3.2a",
        "roi": {"x_min": 1, "x_max": 3, "y_min": 2, "y_max": 4, "width": 2, "height": 2},
        "rois": {
            "roi_256": {"x_min": 1, "x_max": 3, "y_min": 2, "y_max": 4, "width": 2, "height": 2, "fits_frame": True},
            "roi_512": {"x_min": 5, "x_max": 9, "y_min": 6, "y_max": 10, "width": 4, "height": 4, "fits_frame": True},
        },
    }
    resolved = resolve_psf_roi_record(psf_roi, "roi_512")
    assert resolved["width"] == 4
    assert resolved["x_min"] == 5


def test_resolve_psf_roi_record_rejects_missing_key_without_legacy_fallback():
    psf_roi = {
        "phase": "3.2a",
        "roi": {"x_min": 1, "x_max": 3, "y_min": 2, "y_max": 4, "width": 2, "height": 2},
        "rois": {"roi_512": {"x_min": 5, "x_max": 9, "y_min": 6, "y_max": 10, "width": 4, "height": 4, "fits_frame": True}},
    }
    with pytest.raises(ValueError, match="roi_key"):
        resolve_psf_roi_record(psf_roi, None)
