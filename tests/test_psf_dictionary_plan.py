from __future__ import annotations

from pathlib import Path

import pytest

from tasks.psf_phase3 import Phase32PlanError, load_yaml_plan, validate_phase32_plan


def test_psf_dictionary_plan_loads_and_validates() -> None:
    plan_path = Path(__file__).resolve().parents[1] / "plans" / "bishe_psf_dictionary.yaml"
    plan = load_yaml_plan(plan_path)
    validate_phase32_plan(plan, task="dictionary", hardware=False)
    assert plan["plan_id"] == "bishe_psf_dictionary"
    assert plan["phase"] == "3.4"
    assert plan["psf_roi_key"] == "roi_512"
    assert plan["masks"]["lowres_shape"] == [64, 64]
    assert [entry["wavelength_nm"] for entry in plan["wavelengths"]] == [450.0, 550.0, 650.0]


def test_psf_dictionary_plan_rejects_unsafe_lcd_settle_in_hardware() -> None:
    plan_path = Path(__file__).resolve().parents[1] / "plans" / "bishe_psf_dictionary.yaml"
    plan = load_yaml_plan(plan_path)
    plan["lcd"]["settle_ms"] = 50
    with pytest.raises(Phase32PlanError, match="lcd.settle_ms >= 100"):
        validate_phase32_plan(plan, task="dictionary", hardware=True)
