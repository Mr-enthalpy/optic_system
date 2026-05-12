from __future__ import annotations

from pathlib import Path

from app.monitor_gui import parse_args, resolve_status_dir
from gui.monitor_window import monitor_allowed_button_labels


def test_monitor_gui_parses_no_camera_missing_status_dir() -> None:
    args = parse_args([
        "--no-camera",
        "--status-dir",
        "outputs/run_status/missing",
        "--frame-timeout-ms",
        "250",
    ])

    assert args.no_camera is True
    assert args.frame_timeout_ms == 250
    assert resolve_status_dir(args) == Path("outputs/run_status/missing")


def test_monitor_gui_run_id_infers_status_dir() -> None:
    args = parse_args(["--no-camera", "--run-id", "repeatability_001"])

    assert resolve_status_dir(args) == Path("outputs") / "run_status" / "repeatability_001"


def test_monitor_code_does_not_import_control_or_hardware_services() -> None:
    files = [
        Path("app/monitor_gui.py"),
        Path("gui/monitor_window.py"),
        Path("gui/lcd_monitor_panel.py"),
        Path("gui/tls_monitor_panel.py"),
        Path("gui/camera_monitor_panel.py"),
    ]
    combined = "\n".join(p.read_text(encoding="utf-8") for p in files)

    assert "SessionController" not in combined
    assert ".dispatch" not in combined
    assert "LCDService" not in combined
    assert "TLSService" not in combined
    assert "from old" not in combined
    assert "LCD_forward" not in combined


def test_monitor_has_only_local_button_labels() -> None:
    allowed = monitor_allowed_button_labels()
    forbidden = {
        "Full Transparent",
        "Full Opaque",
        "Center Cross",
        "Vertical Bars",
        "Connect TLS",
        "Disconnect TLS",
        "Set Grating",
        "Set Target",
        "Move",
        "Apply Camera Settings",
        "Shutdown Camera",
    }

    assert allowed == {
        "Refresh status",
        "Open status directory",
        "Pause preview display",
    }
    assert allowed.isdisjoint(forbidden)
