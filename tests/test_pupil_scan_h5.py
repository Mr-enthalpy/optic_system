from __future__ import annotations

import json

import h5py
import numpy as np

from tasks.pupil_scan_h5 import PupilScanWriter


def _meta(i: int = 0) -> dict:
    return {
        "mode": "bars_x",
        "x_min": i * 3,
        "x_max": i * 3 + 3,
        "y_min": 0,
        "y_max": 10,
        "row": -1,
        "col": -1,
        "center_x": i * 3 + 1.0,
        "center_y": 5.0,
        "mask_hash": f"hash-{i}",
        "mask_recipe_json": json.dumps({"i": i}, sort_keys=True),
    }


def test_writer_creates_required_groups(tmp_path) -> None:
    path = tmp_path / "pupil.h5"
    with PupilScanWriter(path, plan_id="test_plan") as writer:
        writer.write_plan_json({"plan_id": "test_plan"})
        writer.write_lcd_metadata({"physical_shape": [10, 30], "subpixel_axis": 1})
        writer.write_camera_metadata(
            exposure_us=50000.0,
            gain_db=0.0,
            frame_dtype_full_scale=255,
            camera_params_source={"source": "camera_params_psf_safe.json", "overridden": False},
        )
        writer.write_tls_metadata(wavelength_nm=550.0, grating=1, status={"connected": False})

    with h5py.File(path, "r") as f:
        for name in ("raw", "scan", "camera", "lcd", "tls", "capture"):
            assert name in f
        assert "frames_avg" in f["raw"]
        assert "mask_recipe_json" in f["scan"]
        assert f["camera/exposure_us"][()] == 50000.0
        assert f["camera/frame_dtype_full_scale"][()] == 255


def test_append_rows_and_mask_provenance(tmp_path) -> None:
    path = tmp_path / "rows.h5"
    with PupilScanWriter(path, plan_id="rows") as writer:
        for i in range(3):
            writer.append_capture(
                mask_id=f"mask_{i}",
                mask_metadata=_meta(i),
                frames_avg=np.full((8, 9), i, dtype=np.float64),
            )

    with h5py.File(path, "r") as f:
        assert f["raw/frames_avg"].shape == (3, 8, 9)
        assert f["scan/mask_id"].shape == (3,)
        assert f["scan/mask_hash"][1] == b"hash-1" or f["scan/mask_hash"][1] == "hash-1"
        raw_recipe = f["scan/mask_recipe_json"][2]
        if isinstance(raw_recipe, bytes):
            raw_recipe = raw_recipe.decode()
        assert json.loads(raw_recipe)["i"] == 2


def test_processing_flags_are_phase3_and_not_training_ready(tmp_path) -> None:
    path = tmp_path / "flags.h5"
    with PupilScanWriter(path, plan_id="flags"):
        pass

    with h5py.File(path, "r") as f:
        raw = f["capture/processing_flags_json"][()]
        if isinstance(raw, bytes):
            raw = raw.decode()
        flags = json.loads(raw)
        assert flags["phase"] == "phase3_pupil_scan"
        assert flags["completed"] is True
        assert flags["scientific_calibration_valid"] is False
        assert flags["optical_alignment_validated"] is False
        assert flags["training_ready"] is False


def test_optional_store_physical_masks(tmp_path) -> None:
    path = tmp_path / "masks.h5"
    mask = np.ones((10, 30), dtype=np.uint8) * 255
    with PupilScanWriter(path, plan_id="masks", store_physical_masks=True) as writer:
        writer.append_capture(
            mask_id="mask",
            mask_metadata=_meta(),
            frames_avg=np.ones((4, 5), dtype=np.float64),
            physical_mask=mask,
        )

    with h5py.File(path, "r") as f:
        assert f["masks/masks_physical"].shape == (1, 10, 30)
        assert np.all(f["masks/masks_physical"][0] == 255)

