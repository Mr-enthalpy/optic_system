from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np

from diagnostics.run_status import (
    RunStatusPublisher,
    RunStatusReader,
    read_lcd_state,
    read_mask_preview,
    read_tls_state,
    write_lcd_state,
    write_mask_preview,
    write_tls_state,
)


def test_monitor_reads_independent_lcd_state(tmp_path: Path):
    sd = tmp_path

    task = RunStatusPublisher(sd, "run_001")
    task.update(phase="starting", capture_index=0, n_captures=5)

    write_lcd_state(sd, {
        "connected": True,
        "display_index": 1,
        "current_mode": "mono_mask",
        "current_mask_id": "mask_A",
        "physical_shape": [1080, 5760],
        "logical_shape": [1080, 1920],
        "subpixel_axis": 1,
    })

    lcd = read_lcd_state(sd)
    assert lcd is not None
    assert lcd["current_mask_id"] == "mask_A"

    task_status = RunStatusReader(sd).read()
    assert task_status is not None
    assert task_status.phase == "starting"


def test_monitor_lcd_mask_preview_reads_from_lcd_state(tmp_path: Path):
    sd = tmp_path
    mask = np.arange(12, dtype=np.uint8).reshape(3, 4)
    preview_path = write_mask_preview(sd, mask)

    write_lcd_state(sd, {
        "connected": True,
        "current_mode": "mono_mask",
        "current_mask_id": "bars",
        "mask_preview": str(preview_path.relative_to(sd)),
    })

    loaded = read_mask_preview(sd)
    assert loaded is not None
    np.testing.assert_array_equal(loaded, mask)


def test_monitor_tls_state_reads_from_tls_state(tmp_path: Path):
    sd = tmp_path

    write_tls_state(sd, {
        "connected": True,
        "current_wavelength_nm": 550.0,
        "target_wavelength_nm": 550.0,
        "grating": 1,
        "moving": False,
    })

    tls = read_tls_state(sd)
    assert tls is not None
    assert tls["current_wavelength_nm"] == 550.0


def test_monitor_merges_task_lcd_tls_sources(tmp_path: Path):
    sd = tmp_path

    task = RunStatusPublisher(sd, "run_002")
    task.update(phase="capturing", capture_index=3, n_captures=10)

    write_lcd_state(sd, {
        "connected": True,
        "current_mask_id": "mask_alpha",
        "current_mode": "mono_mask",
    })
    write_tls_state(sd, {
        "connected": True,
        "current_wavelength_nm": 600.0,
        "target_wavelength_nm": 600.0,
        "grating": 1,
        "moving": False,
    })

    reader = RunStatusReader(sd)
    task_status = reader.read()
    lcd = read_lcd_state(sd)
    tls = read_tls_state(sd)

    assert task_status is not None and task_status.phase == "capturing"
    assert lcd is not None and lcd["current_mask_id"] == "mask_alpha"
    assert tls is not None and tls["current_wavelength_nm"] == 600.0


def test_lcd_state_newer_than_stale_task_state(tmp_path: Path):
    """Reproduces Issue #46: task stops, LCD updates independently."""
    sd = tmp_path

    task = RunStatusPublisher(sd, "run_003")
    task.update(phase="capturing", capture_index=5, n_captures=20)

    write_lcd_state(sd, {
        "connected": True,
        "current_mask_id": "mask_stale",
        "current_mode": "mono_mask",
    })

    # task stops updating; LCD service shows a new mask
    time.sleep(0.01)
    write_lcd_state(sd, {
        "connected": True,
        "current_mask_id": "mask_fresh",
        "current_mode": "mono_mask",
    })

    lcd = read_lcd_state(sd)
    task_status = RunStatusReader(sd).read()

    assert lcd is not None
    assert lcd["current_mask_id"] == "mask_fresh"
    assert task_status is not None
    # task state should NOT contain the fresh LCD field
    assert not hasattr(task_status, "current_mask_id") or task_status.current_mask_id is None


def test_monitor_tolerates_missing_lcd_state(tmp_path: Path):
    sd = tmp_path
    task = RunStatusPublisher(sd, "run_004")
    task.update(phase="running")

    lcd = read_lcd_state(sd)
    assert lcd is None

    tls = read_tls_state(sd)
    assert tls is None

    status = RunStatusReader(sd).read()
    assert status is not None


def test_monitor_tolerates_corrupt_lcd_state(tmp_path: Path):
    sd = tmp_path
    (sd / "lcd_state.json").write_text("{not valid json", encoding="utf-8")

    lcd = read_lcd_state(sd)
    assert lcd is None


def test_monitor_tolerates_corrupt_tls_state(tmp_path: Path):
    sd = tmp_path
    (sd / "tls_state.json").write_text("", encoding="utf-8")

    tls = read_tls_state(sd)
    assert tls is None
