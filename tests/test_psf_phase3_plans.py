from __future__ import annotations

import copy

import pytest

from scripts.capture_psf_roi import run_capture_psf_roi
from tasks.psf_phase3 import Phase32PlanError, load_yaml_plan, validate_phase32_plan


def test_phase32_plans_load_and_validate():
    roi_plan = load_yaml_plan("plans/bishe_psf_roi.yaml")
    repeat_plan = load_yaml_plan("plans/bishe_psf_repeatability.yaml")
    validate_phase32_plan(roi_plan, task="roi", hardware=False)
    validate_phase32_plan(repeat_plan, task="repeatability", hardware=False)
    assert roi_plan["lcd"]["settle_ms"] >= 100
    assert repeat_plan["lcd"]["settle_ms"] >= 100


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
