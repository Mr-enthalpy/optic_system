from __future__ import annotations

import numpy as np

from tasks.psf_dictionary_masks import generate_psf_dictionary_masks


def test_psf_dictionary_masks_generate_lowres_and_pupil_limited_physical_masks() -> None:
    pupil_window = {
        "phase": "3.1",
        "physical_shape": [90, 270],
        "center": {"x": 135.0, "y": 45.0},
        "radius": 32.0,
    }
    masks = generate_psf_dictionary_masks(
        lowres_shape=(64, 64),
        physical_shape=(90, 270),
        pupil_window=pupil_window,
        subpixel_axis=1,
        include=["all_open_window", "edge_block_left"],
        random_lowfreq_count=1,
        random_midfreq_count=1,
        task_related_count=1,
        random_seed=123,
    )
    assert len(masks) == 5
    first = masks[0]
    assert first["lowres_mask"].shape == (1, 64, 64)
    assert first["physical_mask"].shape == (90, 270)
    inside = (np.mgrid[:90, :270][1] - 135.0) ** 2 + (np.mgrid[:90, :270][0] - 45.0) ** 2 <= 32.0 ** 2
    assert np.all(first["physical_mask"][~inside] == 0)
    assert first["mask_metadata"]["outside_effective_pupil"] == "opaque"
