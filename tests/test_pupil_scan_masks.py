from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from tasks.pupil_scan_masks import (
    PupilScanMaskError,
    ScanMaskSpec,
    iter_pupil_scan_masks,
    mask_hash,
)


def test_scan_mask_spec_validation() -> None:
    with pytest.raises(PupilScanMaskError):
        ScanMaskSpec(physical_shape=(20, 20), subpixel_axis=2, scan_modes=["bars_x"])
    with pytest.raises(PupilScanMaskError):
        ScanMaskSpec(physical_shape=(20, 20), subpixel_axis=1, scan_modes=["unknown"])
    with pytest.raises(PupilScanMaskError):
        ScanMaskSpec(physical_shape=(20, 20), subpixel_axis=1, scan_modes=["bars_x"])
    with pytest.raises(PupilScanMaskError):
        ScanMaskSpec(physical_shape=(20, 21), subpixel_axis=0, scan_modes=["bars_y"])


def test_bars_x_mask_count_shape_and_alignment() -> None:
    spec = ScanMaskSpec(
        physical_shape=(30, 90),
        subpixel_axis=1,
        scan_modes=["bars_x"],
        bar_count=5,
        include_baselines=False,
    )
    rows = list(iter_pupil_scan_masks(spec))
    assert len(rows) == 5
    for _mask_id, mask, meta in rows:
        assert mask.shape == (30, 90)
        assert mask.dtype == np.uint8
        assert meta["mode"] == "bars_x"
        assert (meta["x_max"] - meta["x_min"]) % 3 == 0
        assert set(np.unique(mask)).issubset({0, 255})


def test_bars_y_mask_count_shape_and_axis0_alignment() -> None:
    spec = ScanMaskSpec(
        physical_shape=(90, 30),
        subpixel_axis=0,
        scan_modes=["bars_y"],
        bar_count=5,
        include_baselines=False,
    )
    rows = list(iter_pupil_scan_masks(spec))
    assert len(rows) == 5
    for _mask_id, mask, meta in rows:
        assert mask.shape == (90, 30)
        assert meta["mode"] == "bars_y"
        assert (meta["y_max"] - meta["y_min"]) % 3 == 0


def test_blocks_count_and_metadata() -> None:
    spec = ScanMaskSpec(
        physical_shape=(40, 120),
        subpixel_axis=1,
        scan_modes=["blocks"],
        block_rows=4,
        block_cols=5,
        include_baselines=False,
    )
    rows = list(iter_pupil_scan_masks(spec))
    assert len(rows) == 20
    last_id, last_mask, last_meta = rows[-1]
    assert last_id == "block_r003_c004"
    assert last_mask.shape == (40, 120)
    assert last_meta["row"] == 3
    assert last_meta["col"] == 4
    assert last_meta["x_min"] < last_meta["x_max"]
    assert last_meta["y_min"] < last_meta["y_max"]


def test_baseline_masks() -> None:
    spec = ScanMaskSpec(
        physical_shape=(12, 36),
        subpixel_axis=1,
        scan_modes=["bars_x"],
        bar_count=1,
        include_baselines=True,
    )
    rows = list(iter_pupil_scan_masks(spec))
    assert rows[0][0] == "baseline_all_open"
    assert rows[1][0] == "baseline_all_closed"
    assert np.all(rows[0][1] == 255)
    assert np.all(rows[1][1] == 0)


def test_subpixel_axis_0_and_1() -> None:
    axis0 = ScanMaskSpec((30, 10), 0, ["blocks"], block_rows=2, block_cols=2)
    axis1 = ScanMaskSpec((10, 30), 1, ["blocks"], block_rows=2, block_cols=2)
    assert next(iter_pupil_scan_masks(axis0))[1].shape == (30, 10)
    assert next(iter_pupil_scan_masks(axis1))[1].shape == (10, 30)


def test_mask_hash_reproducibility() -> None:
    spec = ScanMaskSpec(
        physical_shape=(20, 60),
        subpixel_axis=1,
        scan_modes=["bars_x"],
        bar_count=3,
        include_baselines=False,
    )
    first = list(iter_pupil_scan_masks(spec))
    second = list(iter_pupil_scan_masks(spec))
    assert first[0][2]["mask_hash"] == second[0][2]["mask_hash"]
    assert first[0][2]["mask_hash"] == mask_hash(second[0][1])
    assert first[0][2]["mask_recipe_json"] == second[0][2]["mask_recipe_json"]


def test_no_pre_generated_files_by_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    spec = ScanMaskSpec((12, 36), 1, ["bars_x", "blocks"], bar_count=2, block_rows=2, block_cols=2)
    _ = list(iter_pupil_scan_masks(spec))
    assert list(tmp_path.rglob("*")) == []
