from __future__ import annotations

import json

import h5py
import numpy as np

from tasks.pupil_geometry_h5 import PupilGeometryWriter


def test_geometry_writer_records_references_and_scans(tmp_path) -> None:
    path = tmp_path / "geometry.h5"
    with PupilGeometryWriter(path, plan_id="geom") as writer:
        writer.write_plan_json({"plan_id": "geom", "calibration": {"strategy": "bar_profiles_plus_radius_scan"}})
        writer.write_lcd_metadata({"physical_shape": [12, 36], "subpixel_axis": 1})
        writer.write_camera_metadata(
            metadata={"exposure_us": 40000.0, "gain_db": 1.0},
            camera_params_source={
                "source": "camera_params_psf_safe.json",
                "camera_profile_requested": "fast_pupil_scan",
                "camera_profile_used": "fast_pupil_scan",
                "fallback_used": False,
                "camera_params": {"validity": {"psf_exposure_safe": True}},
            },
        )
        writer.write_tls_metadata({"target_wavelength_nm": 550.0, "grating": 1})
        bright_idx = writer.append_frame(
            mask_id="bright",
            mask_metadata={"mask_type": "solid"},
            frame_avg=np.ones((4, 5), dtype=np.float64) * 10.0,
        )
        dark_idx = writer.append_frame(
            mask_id="dark",
            mask_metadata={"mask_type": "solid"},
            frame_avg=np.ones((4, 5), dtype=np.float64),
        )
        writer.write_bright_reference(np.ones((4, 5)) * 10.0, frame_index=bright_idx)
        writer.write_dark_reference(np.ones((4, 5)), frame_index=dark_idx)
        writer.append_bar_scan(
            axis="x",
            position=2.0,
            energy=3.0,
            frame_index=bright_idx,
            mask_metadata={"mask_type": "dark_bar"},
        )
        writer.append_bar_scan(
            axis="y",
            position=4.0,
            energy=5.0,
            frame_index=dark_idx,
            mask_metadata={"mask_type": "dark_bar"},
        )
        writer.append_radius_scan(
            radius=6.0,
            energy=7.0,
            frame_index=dark_idx,
            mask_metadata={"mask_type": "circular_window"},
        )

    with h5py.File(path, "r") as f:
        assert f["raw/frames_avg"].shape == (2, 4, 5)
        assert f["references/bright_sum"][()] == 200.0
        assert f["references/dark_sum"][()] == 20.0
        assert f["bar_scan/x/positions"][0] == 2.0
        assert f["bar_scan/y/energies"][0] == 5.0
        assert f["radius_scan/radii"][0] == 6.0
        raw = f["capture/processing_flags_json"][()]
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        flags = json.loads(raw)
        assert flags["scientific_calibration_valid"] is False
        assert flags["training_ready"] is False
