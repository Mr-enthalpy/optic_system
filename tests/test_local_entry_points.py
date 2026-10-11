import pytest
from scripts.capture_dataset import parse_args
from devices.lcd_service import LCDService
from pathlib import Path
from tasks.capture_plan import CapturePlan


def test_capture_mode_is_explicit():
    with pytest.raises(SystemExit): parse_args(["--plan", "plan.json"])
    with pytest.raises(SystemExit): parse_args(["--plan", "plan.json", "--dry-run", "--hardware"])
    assert parse_args(["--plan", "plan.json", "--dry-run"]).dry_run
    with pytest.raises(SystemExit): parse_args(["--plan", "plan.json", "--hardware", "--camera-profile", "bands.json"])


def test_real_lcd_cannot_silently_select_monitor(monkeypatch):
    monkeypatch.delenv("OPTIC_SYSTEM_LCD_DISPLAY_INDEX", raising=False)
    monkeypatch.delenv("OPTIC_SYSTEM_LCD_SUBPIXEL_AXIS", raising=False)
    def never_initialize(**kw): raise AssertionError("must reject before opening hardware")
    monkeypatch.setattr("devices.lcd_service.LCDBackend", never_initialize)
    for service in (LCDService(), LCDService(display_index=1), LCDService(subpixel_axis=0)):
        with pytest.raises(ValueError, match="explicitly"): service.initialize()


def test_environment_selects_lcd_identity(monkeypatch):
    monkeypatch.setenv("OPTIC_SYSTEM_LCD_DISPLAY_INDEX", "2")
    monkeypatch.setenv("OPTIC_SYSTEM_LCD_SUBPIXEL_AXIS", "0")
    seen = []
    monkeypatch.setattr("devices.lcd_service.LCDBackend", lambda **kw: seen.append(kw) or object())
    service = LCDService()
    service.initialize()
    assert seen == [{"display_index": 2}]
    assert service.subpixel_axis == 0


def test_shipped_capture_plans_use_current_contract():
    root = Path(__file__).resolve().parents[1]
    for path in (root / "plans").glob("*.yaml"):
        plan = CapturePlan.load_yaml(path)
        assert plan.n_captures > 0
