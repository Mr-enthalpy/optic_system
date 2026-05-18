from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from scripts.analyze_psf_repeatability import analyze_psf_repeatability
from tasks.psf_phase3 import Phase32RawWriter, analyze_repeatability_stack


def _frame(center_x: float, center_y: float, shape=(64, 64), noise_seed=0):
    yy, xx = np.mgrid[: shape[0], : shape[1]]
    rng = np.random.default_rng(noise_seed)
    return 5.0 + 100.0 * np.exp(-(((xx - center_x) / 4.0) ** 2 + ((yy - center_y) / 5.0) ** 2)) + rng.normal(0, 0.2, shape)


def test_repeatability_metrics_separate_intra_and_inter():
    crops = []
    ids = []
    for i in range(6):
        crops.append(_frame(24.0, 32.0, noise_seed=i))
        ids.append("mask_a")
    for i in range(6):
        crops.append(_frame(40.0, 30.0, noise_seed=100 + i))
        ids.append("mask_b")
    metrics = analyze_repeatability_stack(np.asarray(crops), ids)
    assert metrics["summary"]["mean_inter_mask_mse"] > metrics["summary"]["mean_intra_mask_mse"] * 10
    assert metrics["summary"]["mask_induced_differences_larger_than_repeat_noise"] is True


def test_analyze_psf_repeatability_outputs_files(tmp_path: Path):
    raw_h5 = tmp_path / "repeat.h5"
    plan = {
        "plan_id": "test_repeat",
        "phase": "3.2b",
        "camera_params_source": "camera.json",
        "pupil_window_source": "pupil.json",
        "psf_roi_source": "psf_roi.json",
        "wavelength": {"wavelength_nm": 550.0},
        "lcd": {"settle_ms": 200},
        "capture": {"repeats_per_mask": 4, "frames_per_capture": 2},
        "masks": {"include": ["mask_a", "mask_b"]},
        "output": {"raw_h5": str(raw_h5), "output_dir": str(tmp_path)},
    }
    psf_roi = {
        "phase": "3.2a",
        "roi": {"x_min": 0, "x_max": 64, "y_min": 0, "y_max": 64, "width": 64, "height": 64},
    }
    writer = Phase32RawWriter(raw_h5, plan_id="test_repeat", phase="3.2b", include_crops=True).open()
    writer.write_json_sections(
        plan=plan,
        camera_metadata={"frame_dtype_full_scale": 255},
        lcd_metadata={"physical_shape": [90, 270], "subpixel_axis": 1},
        tls_metadata={"current_wavelength_nm": 550.0},
        pupil_window_source={"phase": "3.1", "physical_shape": [90, 270]},
        camera_params_source={"camera_profile_used": "test"},
        psf_roi_source=psf_roi,
    )
    for i in range(4):
        frame = _frame(24.0, 32.0, noise_seed=i)
        writer.append_capture(frame_avg=frame, crop=frame, mask_id="mask_a", repeat_index=i, mask_metadata={"mask_id": "mask_a"})
    for i in range(4):
        frame = _frame(40.0, 30.0, noise_seed=100 + i)
        writer.append_capture(frame_avg=frame, crop=frame, mask_id="mask_b", repeat_index=i, mask_metadata={"mask_id": "mask_b"})
    writer.finalize(completed=True)

    result = analyze_psf_repeatability(raw_h5, tmp_path / "out")
    assert (tmp_path / "out" / "repeatability_metrics.json").exists()
    assert (tmp_path / "out" / "diversity_metrics.json").exists()
    assert (tmp_path / "out" / "psf_diversity_metrics.json").exists()
    assert (tmp_path / "out" / "report.md").exists()
    assert (tmp_path / "out" / "ssim_matrix.npy").exists()
    diversity = json.loads((tmp_path / "out" / "diversity_metrics.json").read_text(encoding="utf-8"))
    assert result["diversity"]["mask_induced_differences_larger_than_repeat_noise"] is True
    assert diversity["validity"]["training_ready"] is False
