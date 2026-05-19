from __future__ import annotations

from pathlib import Path

import pytest

from tasks.psf_phase3 import Phase32PlanError, load_yaml_plan, validate_phase32_plan


def test_dotf_plan_loads_and_validates() -> None:
    plan_path = Path(__file__).resolve().parents[1] / "plans" / "bishe_dotf_diagnostic.yaml"
    plan = load_yaml_plan(plan_path)
    validate_phase32_plan(plan, task="dotf", hardware=False)
    assert plan["plan_id"] == "bishe_dotf_diagnostic"
    assert plan["phase"] == "3.3"
    assert plan["dotf"]["perturbation_set"]
    assert plan["dotf"]["roi_keys"] == ["roi_256", "roi_512", "roi_768", "roi_1024"]
    assert plan["dotf"]["edge_energy"]["enabled"] is True


def test_dotf_plan_rejects_unsafe_lcd_settle_in_hardware() -> None:
    plan_path = Path(__file__).resolve().parents[1] / "plans" / "bishe_dotf_diagnostic.yaml"
    plan = load_yaml_plan(plan_path)
    plan["lcd"]["settle_ms"] = 50
    with pytest.raises(Phase32PlanError, match="lcd.settle_ms >= 100"):
        validate_phase32_plan(plan, task="dotf", hardware=True)
