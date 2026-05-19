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
        writer.append_capture(
            frame_avg=frame,
            crop=frame,
            mask_id="mask_a",
            repeat_index=i,
            wavelength_nm=550.0,
            wavelength_index=0,
            exposure_us=1000.0,
            gain_db=0.0,
            camera_profile_used="wl550p0_gain0.0_near_full_scale",
            mask_metadata={"mask_id": "mask_a"},
        )
    for i in range(4):
        frame = _frame(40.0, 30.0, noise_seed=100 + i)
        writer.append_capture(
            frame_avg=frame,
            crop=frame,
            mask_id="mask_b",
            repeat_index=i,
            wavelength_nm=550.0,
            wavelength_index=0,
            exposure_us=1000.0,
            gain_db=0.0,
            camera_profile_used="wl550p0_gain0.0_near_full_scale",
            mask_metadata={"mask_id": "mask_b"},
        )
    writer.finalize(completed=True)

    result = analyze_psf_repeatability(raw_h5, tmp_path / "out")
    assert (tmp_path / "out" / "repeatability_metrics.json").exists()
    assert (tmp_path / "out" / "diversity_metrics.json").exists()
    assert (tmp_path / "out" / "psf_diversity_metrics.json").exists()
    assert (tmp_path / "out" / "repeatability_metrics_normalized.json").exists()
    assert (tmp_path / "out" / "diversity_metrics_normalized.json").exists()
    assert (tmp_path / "out" / "normalized_analysis_manifest.json").exists()
    assert (tmp_path / "out" / "report.md").exists()
    assert (tmp_path / "out" / "ssim_matrix.npy").exists()
    diversity = json.loads((tmp_path / "out" / "diversity_metrics.json").read_text(encoding="utf-8"))
    assert result["diversity"]["mask_induced_differences_larger_than_repeat_noise"] is True
    assert diversity["validity"]["training_ready"] is False


def test_analyze_psf_repeatability_multi_wavelength_outputs_files(tmp_path: Path):
    raw_h5 = tmp_path / "repeat_multi.h5"
    plan = {
        "plan_id": "test_repeat_multi",
        "phase": "3.2b",
        "camera_params_source": "camera.json",
        "pupil_window_source": "pupil.json",
        "psf_roi_source": "psf_roi.json",
        "wavelengths": [{"wavelength_nm": 450.0}, {"wavelength_nm": 650.0}],
        "lcd": {"settle_ms": 200},
        "capture": {"repeats_per_mask": 3, "frames_per_capture": 2},
        "masks": {"include": ["mask_a", "mask_b"]},
        "output": {"raw_h5": str(raw_h5), "output_dir": str(tmp_path)},
    }
    psf_roi = {
        "phase": "3.2a",
        "roi": {"x_min": 0, "x_max": 64, "y_min": 0, "y_max": 64, "width": 64, "height": 64},
    }
    writer = Phase32RawWriter(raw_h5, plan_id="test_repeat_multi", phase="3.2b", include_crops=True).open()
    writer.write_json_sections(
        plan=plan,
        camera_metadata={"frame_dtype_full_scale": 255},
        lcd_metadata={"physical_shape": [90, 270], "subpixel_axis": 1},
        tls_metadata={"wavelength_sequence": [450.0, 650.0]},
        pupil_window_source={"phase": "3.1", "physical_shape": [90, 270]},
        camera_params_source={"camera_profile_used": "test"},
        psf_roi_source=psf_roi,
    )
    for wavelength_index, wavelength_nm in enumerate((450.0, 650.0)):
        wl_shift = -2.0 if wavelength_nm < 550.0 else 2.5
        for i in range(3):
            frame_a = _frame(24.0 + wl_shift, 32.0 - 0.5 * wl_shift, noise_seed=10 * wavelength_index + i)
            writer.append_capture(
                frame_avg=frame_a,
                crop=frame_a,
                mask_id="mask_a",
                repeat_index=i,
                wavelength_nm=wavelength_nm,
                wavelength_index=wavelength_index,
                exposure_us=1000.0 + wavelength_index,
                gain_db=0.0,
                camera_profile_used=f"wl{str(wavelength_nm).replace('.', 'p')}_gain0.0_near_full_scale",
                mask_metadata={"mask_id": "mask_a"},
            )
        for i in range(3):
            frame_b = _frame(40.0 + 0.7 * wl_shift, 30.0 + wl_shift, noise_seed=100 + 10 * wavelength_index + i)
            writer.append_capture(
                frame_avg=frame_b,
                crop=frame_b,
                mask_id="mask_b",
                repeat_index=i,
                wavelength_nm=wavelength_nm,
                wavelength_index=wavelength_index,
                exposure_us=1000.0 + wavelength_index,
                gain_db=0.0,
                camera_profile_used=f"wl{str(wavelength_nm).replace('.', 'p')}_gain0.0_near_full_scale",
                mask_metadata={"mask_id": "mask_b"},
            )
    writer.finalize(completed=True)

    result = analyze_psf_repeatability(raw_h5, tmp_path / "out_multi")
    repeatability = json.loads((tmp_path / "out_multi" / "repeatability_metrics.json").read_text(encoding="utf-8"))
    diversity = json.loads((tmp_path / "out_multi" / "diversity_metrics.json").read_text(encoding="utf-8"))
    spectral = json.loads((tmp_path / "out_multi" / "spectral_diversity_metrics.json").read_text(encoding="utf-8"))
    assert result["repeatability"]["task"] == "psf_repeatability_multi_wavelength"
    assert repeatability["wavelengths_nm"] == [450.0, 650.0]
    assert sorted(repeatability["per_wavelength"].keys()) == ["450.0", "650.0"]
    assert sorted(diversity["per_wavelength"].keys()) == ["450.0", "650.0"]
    assert "mask_a" in spectral["per_mask"]
    assert spectral["summary"]["wavelength_induced_differences_larger_than_repeat_noise"] is True
    assert (tmp_path / "out_multi" / "wl_450p0" / "repeatability_metrics.json").exists()
    assert (tmp_path / "out_multi" / "wl_650p0" / "diversity_metrics.json").exists()
    assert (tmp_path / "out_multi" / "normalized_unit_energy" / "wl_450p0" / "repeatability_metrics.json").exists()
    assert (tmp_path / "out_multi" / "spectral_diversity_metrics_normalized.json").exists()


def test_normalized_cross_wavelength_analysis_reduces_pure_amplitude_difference(tmp_path: Path):
    raw_h5 = tmp_path / "repeat_amp.h5"
    plan = {
        "plan_id": "test_repeat_amp",
        "phase": "3.2b",
        "camera_params_source": "camera.json",
        "pupil_window_source": "pupil.json",
        "psf_roi_source": "psf_roi.json",
        "wavelengths": [{"wavelength_nm": 450.0}, {"wavelength_nm": 650.0}],
        "lcd": {"settle_ms": 200},
        "capture": {"repeats_per_mask": 3, "frames_per_capture": 2},
        "masks": {"include": ["mask_a"]},
        "output": {"raw_h5": str(raw_h5), "output_dir": str(tmp_path)},
    }
    psf_roi = {
        "phase": "3.2a",
        "roi": {"x_min": 0, "x_max": 64, "y_min": 0, "y_max": 64, "width": 64, "height": 64},
    }
    writer = Phase32RawWriter(raw_h5, plan_id="test_repeat_amp", phase="3.2b", include_crops=True).open()
    writer.write_json_sections(
        plan=plan,
        camera_metadata={"frame_dtype_full_scale": 255},
        lcd_metadata={"physical_shape": [90, 270], "subpixel_axis": 1},
        tls_metadata={"wavelength_sequence": [450.0, 650.0]},
        pupil_window_source={"phase": "3.1", "physical_shape": [90, 270]},
        camera_params_source={"camera_profile_used": "test"},
        psf_roi_source=psf_roi,
    )
    for wavelength_index, (wavelength_nm, amplitude) in enumerate(((450.0, 80.0), (650.0, 160.0))):
        for i in range(3):
            frame = 5.0 + (amplitude / 100.0) * (_frame(24.0, 32.0, noise_seed=50 + i) - 5.0)
            writer.append_capture(
                frame_avg=frame,
                crop=frame,
                mask_id="mask_a",
                repeat_index=i,
                wavelength_nm=wavelength_nm,
                wavelength_index=wavelength_index,
                exposure_us=1000.0 + wavelength_index,
                gain_db=0.0,
                camera_profile_used=f"wl{str(wavelength_nm).replace('.', 'p')}_gain0.0_near_full_scale",
                mask_metadata={"mask_id": "mask_a"},
            )
    writer.finalize(completed=True)

    analyze_psf_repeatability(raw_h5, tmp_path / "out_amp")
    raw_spectral = json.loads((tmp_path / "out_amp" / "spectral_diversity_metrics.json").read_text(encoding="utf-8"))
    normalized_spectral = json.loads(
        (tmp_path / "out_amp" / "spectral_diversity_metrics_normalized.json").read_text(encoding="utf-8")
    )
    raw_mse = raw_spectral["summary"]["mean_cross_wavelength_same_mask_mse"]
    normalized_mse = normalized_spectral["summary"]["mean_cross_wavelength_same_mask_mse"]
    assert normalized_mse < raw_mse * 0.05
