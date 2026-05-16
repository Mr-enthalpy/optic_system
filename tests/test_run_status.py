from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from diagnostics.run_status import (
    RunStatusPublisher,
    RunStatusReader,
    read_mask_preview,
    write_mask_preview,
)


def test_publisher_writes_state_json(tmp_path: Path) -> None:
    publisher = RunStatusPublisher(tmp_path, "run_001")

    publisher.update(
        plan_id="plan_a",
        phase="starting",
        capture_index=0,
        n_captures=3,
    )

    state_path = tmp_path / "state.json"
    assert state_path.exists()
    data = json.loads(state_path.read_text(encoding="utf-8"))
    assert data["run_id"] == "run_001"
    assert data["plan_id"] == "plan_a"
    assert data["phase"] == "starting"
    assert isinstance(data["last_update_ns"], int)


def test_reader_reads_task_state_back(tmp_path: Path) -> None:
    publisher = RunStatusPublisher(tmp_path, "run_002")
    publisher.update(
        plan_id="plan_b",
        phase="mask_shown",
        capture_index=1,
        n_captures=10,
        completed=False,
    )

    status = RunStatusReader(tmp_path).read()

    assert status is not None
    assert status.run_id == "run_002"
    assert status.plan_id == "plan_b"
    assert status.phase == "mask_shown"
    assert status.capture_index == 1
    assert status.n_captures == 10


def test_atomic_write_leaves_valid_json_under_normal_operation(tmp_path: Path) -> None:
    publisher = RunStatusPublisher(tmp_path, "run_003")

    for idx in range(10):
        publisher.update(phase="capture_appended", capture_index=idx, n_captures=10)
        json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))


def test_missing_status_dir_returns_none(tmp_path: Path) -> None:
    assert RunStatusReader(tmp_path / "missing").read() is None


def test_partially_written_state_returns_none(tmp_path: Path) -> None:
    tmp_path.mkdir(exist_ok=True)
    (tmp_path / "state.json").write_text("{not-json", encoding="utf-8")

    assert RunStatusReader(tmp_path).read() is None


def test_missing_mask_preview_returns_none(tmp_path: Path) -> None:
    sd = tmp_path
    sd.mkdir(parents=True, exist_ok=True)
    (sd / "lcd_state.json").write_text(
        json.dumps({"current_mode": "mono_mask", "mask_preview": "missing.npy"}),
        encoding="utf-8",
    )

    assert read_mask_preview(sd) is None


def test_mask_preview_write_read_npy(tmp_path: Path) -> None:
    sd = tmp_path
    mask = np.arange(12, dtype=np.uint8).reshape(3, 4)

    path = write_mask_preview(sd, mask, filename="current_mask_preview.npy")

    assert path.exists()
    loaded = read_mask_preview(sd)
    assert loaded is not None
    np.testing.assert_array_equal(loaded, mask)
