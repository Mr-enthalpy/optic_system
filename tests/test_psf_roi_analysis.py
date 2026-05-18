from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from scripts.analyze_psf_roi import analyze_psf_roi
from tasks.psf_phase3 import Phase32RawWriter, estimate_psf_roi


def _gaussian_frame(shape=(96, 128), center=(72.4, 43.7), amp=180.0):
    h, w = shape
    yy, xx = np.mgrid[:h, :w]
    return 8.0 + amp * np.exp(-(((xx - center[0]) / 5.0) ** 2 + ((yy - center[1]) / 4.0) ** 2))


def test_synthetic_psf_center_is_recovered():
    frame = _gaussian_frame()
    result = estimate_psf_roi(frame, crop_size=(32, 32), center_window_radius=12, full_scale=255)
    assert abs(result["center"]["x"] - 72.4) < 0.5
    assert abs(result["center"]["y"] - 43.7) < 0.5
    roi = result["roi"]
    assert roi["x_min"] >= 0
    assert roi["y_min"] >= 0
    assert roi["x_max"] <= frame.shape[1]
    assert roi["y_max"] <= frame.shape[0]


def test_analyze_psf_roi_writes_schema(tmp_path: Path):
    raw_h5 = tmp_path / "raw_roi.h5"
    plan = {
        "plan_id": "test_roi",
        "phase": "3.2a",
        "camera_params_source": "missing_camera_params.json",
        "pupil_window_source": "pupil.json",
        "wavelength": {"wavelength_nm": 550.0},
        "lcd": {"settle_ms": 200},
        "capture": {"repeats": 3, "frames_per_capture": 2},
        "psf_roi": {"crop_size": [32, 32], "center_window_radius": 12},
        "output": {"raw_h5": str(raw_h5), "output_dir": str(tmp_path)},
    }
    writer = Phase32RawWriter(raw_h5, plan_id="test_roi", phase="3.2a").open()
    writer.write_json_sections(
        plan=plan,
        camera_metadata={"frame_dtype_full_scale": 255, "camera_profile_used": "test_profile"},
        lcd_metadata={"physical_shape": [90, 270], "subpixel_axis": 1},
        tls_metadata={"current_wavelength_nm": 550.0},
        pupil_window_source={"phase": "3.1", "physical_shape": [90, 270], "center": {"x": 45, "y": 45}, "radius": 20},
        camera_params_source={"psf_safety_policy": {"valid_pixel_domain": {"type": "full_frame"}}},
    )
    for i in range(3):
        writer.append_capture(
            frame_avg=_gaussian_frame(center=(72.4 + 0.1 * i, 43.7)),
            mask_id="effective_pupil_window_all_open",
            repeat_index=i,
            mask_metadata={"mask_id": "effective_pupil_window_all_open"},
        )
    writer.finalize(completed=True)

    result = analyze_psf_roi(raw_h5, tmp_path / "out")
    path = tmp_path / "out" / "psf_roi.json"
    assert path.exists()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert result["phase"] == "3.2a"
    assert data["coordinate_system"] == "camera sensor coordinates"
    assert data["roi"]["width"] == 32
    assert data["roi"]["height"] == 32
    assert data["validity"]["scientific_calibration_valid"] is False
    assert "full_scale_in_avg_valid_domain" in data["quality"]


def test_analyze_psf_roi_uses_h5_camera_params_provenance(tmp_path: Path):
    raw_h5 = tmp_path / "raw_roi.h5"
    plan = {
        "plan_id": "test_roi",
        "phase": "3.2a",
        "camera_params_source": str(tmp_path / "does_not_exist.json"),
        "pupil_window_source": "pupil.json",
        "wavelength": {"wavelength_nm": 550.0},
        "lcd": {"settle_ms": 200},
        "capture": {"repeats": 1, "frames_per_capture": 1},
        "psf_roi": {"crop_size": [32, 32], "center_window_radius": 12},
        "output": {"raw_h5": str(raw_h5), "output_dir": str(tmp_path)},
    }
    frame = _gaussian_frame()
    frame[0, :] = 255.0
    writer = Phase32RawWriter(raw_h5, plan_id="test_roi", phase="3.2a").open()
    writer.write_json_sections(
        plan=plan,
        camera_metadata={"frame_dtype_full_scale": 255, "camera_profile_used": "test_profile"},
        lcd_metadata={"physical_shape": [90, 270], "subpixel_axis": 1},
        tls_metadata={"current_wavelength_nm": 550.0},
        pupil_window_source={"phase": "3.1", "physical_shape": [90, 270], "center": {"x": 45, "y": 45}, "radius": 20},
        camera_params_source={
            "psf_safety_policy": {
                "valid_pixel_domain": {"type": "exclude_top_rows", "top_rows": 1}
            }
        },
    )
    writer.append_capture(
        frame_avg=frame,
        mask_id="effective_pupil_window_all_open",
        repeat_index=0,
        mask_metadata={"mask_id": "effective_pupil_window_all_open"},
    )
    writer.finalize(completed=True)

    data = analyze_psf_roi(raw_h5, tmp_path / "out")
    assert data["quality"]["full_scale_in_avg_valid_domain"] is False
