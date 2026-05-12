from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from diagnostics.run_status import RunStatusPublisher, RunStatusReader


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


def test_reader_reads_state_back(tmp_path: Path) -> None:
    publisher = RunStatusPublisher(tmp_path, "run_002")
    publisher.update(
        plan_id="plan_b",
        phase="mask_shown",
        current_mask_id="mask_a",
        current_wavelength_nm=532.0,
        target_wavelength_nm=532.0,
        tls_grating=1,
        tls_moving=False,
        completed=False,
    )

    status = RunStatusReader(tmp_path).read()

    assert status is not None
    assert status.run_id == "run_002"
    assert status.plan_id == "plan_b"
    assert status.current_mask_id == "mask_a"
    assert status.current_wavelength_nm == 532.0
    assert status.tls_moving is False


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
    publisher = RunStatusPublisher(tmp_path, "run_004")
    publisher.update(current_mask_preview="missing.npy")

    assert RunStatusReader(tmp_path).read_mask_preview() is None


def test_mask_preview_write_read_npy(tmp_path: Path) -> None:
    publisher = RunStatusPublisher(tmp_path, "run_005")
    mask = np.arange(12, dtype=np.uint8).reshape(3, 4)

    path = publisher.write_mask_preview(mask, filename="current_mask_preview.npy")

    assert path.exists()
    status = RunStatusReader(tmp_path).read()
    assert status is not None
    assert status.current_mask_preview == "current_mask_preview.npy"
    loaded = RunStatusReader(tmp_path).read_mask_preview()
    assert loaded is not None
    np.testing.assert_array_equal(loaded, mask)

