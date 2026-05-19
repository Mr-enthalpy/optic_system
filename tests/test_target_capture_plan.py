from __future__ import annotations

from pathlib import Path

import pytest

from tasks.psf_phase3 import Phase32PlanError, load_yaml_plan, validate_phase32_plan


def test_target_capture_plan_loads_and_validates() -> None:
    plan_path = Path(__file__).resolve().parents[1] / "plans" / "bishe_target_capture.yaml"
    plan = load_yaml_plan(plan_path)
    validate_phase32_plan(plan, task="target_capture", hardware=False)
    assert plan["plan_id"] == "bishe_target_capture"
    assert plan["phase"] == "3.6"
    assert plan["psf_roi_key"] == "roi_512"
    assert plan["mask_source"]["type"] == "lcd_forward_export"
    assert plan["wavelengths"]
    assert plan["mask_source"]["max_masks"] == len(plan["mask_source"]["selected_mask_ids"])


def test_target_capture_plan_rejects_unsafe_lcd_settle_in_hardware() -> None:
    plan_path = Path(__file__).resolve().parents[1] / "plans" / "bishe_target_capture.yaml"
    plan = load_yaml_plan(plan_path)
    plan["lcd"]["settle_ms"] = 50
    with pytest.raises(Phase32PlanError, match="lcd.settle_ms >= 100"):
        validate_phase32_plan(plan, task="target_capture", hardware=True)
