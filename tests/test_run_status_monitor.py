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


def test_write_frame_preview_default_preserves_raw_npy(tmp_path: Path) -> None:
    publisher = RunStatusPublisher(tmp_path, "run_raw")
    frame = np.arange(20, dtype=np.uint16).reshape(4, 5)

    path = publisher.write_frame_preview(frame)

    assert path.name == "latest_frame_preview.npy"
    status = RunStatusReader(tmp_path).read()
    assert status is not None
    assert status.latest_frame_preview == "latest_frame_preview.npy"
    loaded = RunStatusReader(tmp_path).read_frame_preview()
    assert loaded is not None
    assert loaded.dtype == np.uint16
    np.testing.assert_array_equal(loaded, frame)


def test_write_frame_stats_and_reader(tmp_path: Path) -> None:
    publisher = RunStatusPublisher(tmp_path, "run_004")
    stats = {
        "max_pixel": np.uint16(31),
        "p99_9": 28.5,
        "peak_pixel_fraction_burst": 31.0 / 255.0,
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
        "peak_pixel_fraction_burst": 31.0 / 255.0,
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


def test_monitor_loads_raw_npy_as_mono_preview(tmp_path: Path) -> None:
    from scripts.monitor_run_status import RAW_MONO_ENCODING, _load_preview_image

    path = tmp_path / "latest_frame_preview.npy"
    np.save(path, np.arange(16, dtype=np.uint8).reshape(4, 4))

    image = _load_preview_image(path, frame_encoding=RAW_MONO_ENCODING)

    assert image.mode == "L"
    assert image.size == (4, 4)


def test_monitor_can_debayer_raw_npy_preview(tmp_path: Path) -> None:
    import pytest

    cv2 = pytest.importorskip("cv2")
    assert hasattr(cv2, "COLOR_BayerRGGB2RGB")

    from scripts.monitor_run_status import BAYER_RGGB_ENCODING, _load_preview_image

    path = tmp_path / "latest_frame_preview.npy"
    np.save(path, np.arange(64, dtype=np.uint8).reshape(8, 8))

    image = _load_preview_image(path, frame_encoding=BAYER_RGGB_ENCODING)

    assert image.mode == "RGB"
    assert image.size == (8, 8)


def test_monitor_prefers_fast_frame_preview_file(tmp_path: Path) -> None:
    from scripts.monitor_run_status import _frame_preview_path

    publisher = RunStatusPublisher(tmp_path, "run_fast")
    publisher.write_frame_preview(np.zeros((4, 4), dtype=np.uint8))
    fast_path = tmp_path / "latest_frame_preview_fast.npy"
    np.save(fast_path, np.ones((4, 4), dtype=np.uint8))
    status = RunStatusReader(tmp_path).read()

    assert _frame_preview_path(tmp_path, status) == fast_path


def test_new_publisher_resets_transient_run_files(tmp_path: Path) -> None:
    (tmp_path / "log.jsonl").write_text("old\n", encoding="utf-8")
    np.save(tmp_path / "latest_frame_preview_fast.npy", np.zeros((2, 2), dtype=np.uint8))

    RunStatusPublisher(tmp_path, "run_new")

    assert not (tmp_path / "log.jsonl").exists()
    assert not (tmp_path / "latest_frame_preview_fast.npy").exists()
