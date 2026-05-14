from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

import numpy as np

from diagnostics.run_status import RunStatusPublisher, RunStatusReader


def test_publisher_writes_state_json(tmp_path: Path) -> None:
    publisher = RunStatusPublisher(tmp_path, "run_001")

    publisher.update(plan_id="plan_a", phase="starting", capture_index=0, n_captures=3)

    data = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert data["run_id"] == "run_001"
    assert data["plan_id"] == "plan_a"
    assert data["phase"] == "starting"
    assert isinstance(data["last_update_ns"], int)


def test_write_mask_preview_still_works(tmp_path: Path) -> None:
    publisher = RunStatusPublisher(tmp_path, "run_002")
    mask = np.arange(12, dtype=np.uint8).reshape(3, 4)

    path = publisher.write_mask_preview(mask, filename="current_mask_preview.npy")

    assert path.exists()
    status = RunStatusReader(tmp_path).read()
    assert status is not None
    assert status.current_mask_preview == "current_mask_preview.npy"
    loaded = RunStatusReader(tmp_path).read_mask_preview()
    assert loaded is not None
    np.testing.assert_array_equal(loaded, mask)


def test_write_frame_preview_and_reader(tmp_path: Path) -> None:
    publisher = RunStatusPublisher(tmp_path, "run_003")
    frame = np.arange(20, dtype=np.uint16).reshape(4, 5)

    path = publisher.write_frame_preview(frame, filename="latest_frame_preview.npy")

    assert path.exists()
    status = RunStatusReader(tmp_path).read()
    assert status is not None
    assert status.latest_frame_preview == "latest_frame_preview.npy"
    loaded = RunStatusReader(tmp_path).read_frame_preview()
    assert loaded is not None
    np.testing.assert_array_equal(loaded, frame)


def test_write_frame_stats_and_reader(tmp_path: Path) -> None:
    publisher = RunStatusPublisher(tmp_path, "run_004")
    stats = {
        "max_pixel": np.uint16(31),
        "p99_9": 28.5,
        "saturated_fraction": 0.0,
        "timestamp_ns": 123,
    }

    path = publisher.write_frame_stats(stats)

    assert path.exists()
    status = RunStatusReader(tmp_path).read()
    assert status is not None
    assert status.frame_stats == "frame_stats.json"
    loaded = RunStatusReader(tmp_path).read_frame_stats()
    assert loaded == {
        "max_pixel": 31,
        "p99_9": 28.5,
        "saturated_fraction": 0.0,
        "timestamp_ns": 123,
    }


def test_append_log_and_tail_log(tmp_path: Path) -> None:
    publisher = RunStatusPublisher(tmp_path, "run_005")

    publisher.append_log("info", "mask shown", source="capture", capture_index=1)
    publisher.append_log("warning", "burst dim", max_pixel=12)

    status = RunStatusReader(tmp_path).read()
    assert status is not None
    assert status.log_file == "log.jsonl"
    rows = RunStatusReader(tmp_path).tail_log(max_lines=1)
    assert len(rows) == 1
    assert rows[0]["level"] == "WARNING"
    assert rows[0]["message"] == "burst dim"
    assert rows[0]["max_pixel"] == 12


def test_reader_tolerates_missing_files(tmp_path: Path) -> None:
    reader = RunStatusReader(tmp_path / "missing")

    assert reader.read() is None
    assert reader.read_mask_preview() is None
    assert reader.read_frame_preview() is None
    assert reader.read_frame_stats() is None
    assert reader.tail_log() == []


def test_reader_tolerates_malformed_frame_stats(tmp_path: Path) -> None:
    publisher = RunStatusPublisher(tmp_path, "run_006")
    publisher.update(frame_stats="frame_stats.json")
    (tmp_path / "frame_stats.json").write_text("{bad json", encoding="utf-8")

    assert RunStatusReader(tmp_path).read_frame_stats() is None


def test_reader_skips_malformed_log_line(tmp_path: Path) -> None:
    publisher = RunStatusPublisher(tmp_path, "run_007")
    publisher.update(log_file="log.jsonl")
    (tmp_path / "log.jsonl").write_text(
        json.dumps({"level": "INFO", "message": "ok"}) + "\n"
        "{bad json\n"
        + json.dumps({"level": "INFO", "message": "also ok"}) + "\n",
        encoding="utf-8",
    )

    rows = RunStatusReader(tmp_path).tail_log(max_lines=10)

    assert [row["message"] for row in rows] == ["ok", "also ok"]


def test_monitor_run_status_imports_no_hardware_services() -> None:
    script = Path("scripts") / "monitor_run_status.py"
    tree = ast.parse(script.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)

    forbidden = {
        "devices.camera_service",
        "devices.lcd_service",
        "devices.tls_service",
        "devices.frame_stream",
        "capture.frame_capture",
    }
    assert imported.isdisjoint(forbidden)


def test_no_gui_once_mode_runs_against_temp_status_dir(tmp_path: Path) -> None:
    publisher = RunStatusPublisher(tmp_path, "run_008")
    publisher.update(plan_id="plan_a", phase="starting")
    publisher.append_log("info", "status ready")

    result = subprocess.run(
        [
            sys.executable,
            "scripts/monitor_run_status.py",
            "--status-dir",
            str(tmp_path),
            "--no-gui",
            "--once",
        ],
        cwd=Path.cwd(),
        text=True,
        capture_output=True,
        timeout=10,
    )

    assert result.returncode == 0
    assert "run_id: run_008" in result.stdout
    assert "[INFO] status ready" in result.stdout


def test_fit_size_downsamples_large_preview_without_crop() -> None:
    from scripts.monitor_run_status import _fit_size

    assert _fit_size(768, 642, 384, 321) == (384, 321)
    assert _fit_size(2048, 2448, 512, 512) == (428, 512)


def test_fit_size_does_not_upscale_small_preview() -> None:
    from scripts.monitor_run_status import _fit_size

    assert _fit_size(120, 80, 512, 512) == (120, 80)
