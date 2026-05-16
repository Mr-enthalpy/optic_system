from __future__ import annotations

import numpy as np

from tasks.pupil_geometry_masks import (
    bar_metadata,
    circular_window_mask,
    circular_window_metadata,
    horizontal_bar_mask,
    solid_mask,
    vertical_bar_mask,
)


def test_vertical_bar_mask_and_metadata() -> None:
    mask = vertical_bar_mask((10, 30), x0=6, width=4, bg_code=255, bar_code=0)
    meta = bar_metadata(
        mask_id="bar_x_0000",
        axis="x",
        position=8.0,
        start=6,
        width=4,
        bg_code=255,
        bar_code=0,
        physical_shape=(10, 30),
        subpixel_axis=1,
    )

    assert mask.shape == (10, 30)
    assert np.all(mask[:, 6:10] == 0)
    assert np.all(mask[:, :6] == 255)
    assert meta["mask_type"] == "dark_bar"
    assert meta["axis"] == "x"
    assert meta["position"] == 8.0
    assert meta["bar_width"] == 4
    assert meta["physical_shape"] == [10, 30]


def test_horizontal_bar_mask_and_metadata() -> None:
    mask = horizontal_bar_mask((10, 30), y0=2, width=3, bg_code=255, bar_code=0)
    meta = bar_metadata(
        mask_id="bar_y_0000",
        axis="y",
        position=3.5,
        start=2,
        width=3,
        bg_code=255,
        bar_code=0,
        physical_shape=(10, 30),
        subpixel_axis=1,
    )

    assert np.all(mask[2:5, :] == 0)
    assert np.all(mask[:2, :] == 255)
    assert meta["axis"] == "y"
    assert meta["y_min"] == 2
    assert meta["y_max"] == 5


def test_circular_window_mask_area_and_metadata() -> None:
    mask = circular_window_mask(
        (21, 21),
        center=(10.0, 10.0),
        radius=5.0,
        bg_code=0,
        aperture_code=255,
    )
    meta = circular_window_metadata(
        mask_id="radius_0000",
        center=(10.0, 10.0),
        radius=5.0,
        bg_code=0,
        aperture_code=255,
        physical_shape=(21, 21),
        subpixel_axis=1,
    )

    assert mask[10, 10] == 255
    assert mask[0, 0] == 0
    assert 70 <= int(np.sum(mask == 255)) <= 90
    assert meta["mask_type"] == "circular_window"
    assert meta["center"] == [10.0, 10.0]
    assert meta["radius"] == 5.0


def test_solid_mask_is_physical_mono() -> None:
    mask = solid_mask((6, 18), 128)
    assert mask.shape == (6, 18)
    assert mask.dtype == np.uint8
    assert np.all(mask == 128)
