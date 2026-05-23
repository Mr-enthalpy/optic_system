from __future__ import annotations

import csv
from pathlib import Path

import h5py
import numpy as np
import yaml

from scripts.export_closed_lcd_residuals import export_closed_lcd_residual_release
from scripts.inspect_closed_lcd_residuals import inspect_closed_lcd_residuals
from scripts.validate_closed_lcd_residual_release import validate_closed_lcd_residual_release
from tasks.psf_dictionary_phase3 import PSFDictionaryRawWriter


def _write_raw_dictionary_with_closed_lcd(raw_h5: Path) -> None:
    plan_path = Path(__file__).resolve().parents[1] / "plans" / "bishe_psf_dictionary.yaml"
    plan = yaml.safe_load(plan_path.read_text(encoding="utf-8"))
    plan["capture"]["repeats_per_mask"] = 5
    plan["capture"]["frames_per_capture"] = 10
    plan["masks"]["include"] = ["all_closed_window"]
    plan["psf_roi_key"] = "roi_512"
    exposures = [779.6875, 487.3046875, 2241.6015625]
    wavelengths = [450.0, 550.0, 650.0]
    plan["wavelengths"] = [{"wavelength_nm": wl, "grating": 1} for wl in wavelengths]
    writer = PSFDictionaryRawWriter(raw_h5, plan_id=plan["plan_id"]).open()
    writer.write_json_sections(
        plan=plan,
        camera_metadata={"frame_dtype_full_scale": 255.0, "camera_profile_used": "test"},
        lcd_metadata={"physical_shape": [2560, 1620], "subpixel_axis": 1},
        tls_metadata={"wavelength_sequence": plan["wavelengths"]},
        pupil_window_source={"phase": "3.1", "physical_shape": [2560, 1620], "center": {"x": 100.0, "y": 100.0}, "radius": 50.0},
        psf_roi_source={
            "phase": "3.2a",
            "roi": {"x_min": 0, "x_max": 512, "y_min": 0, "y_max": 512, "width": 512, "height": 512},
            "rois": {
                "roi_512": {
                    "x_min": 0,
                    "x_max": 512,
                    "y_min": 0,
                    "y_max": 512,
                    "width": 512,
                    "height": 512,
                    "fits_frame": True,
                }
            },
            "psf_roi_key_used": "roi_512",
            "psf_roi_record_used": {"x_min": 0, "x_max": 512, "y_min": 0, "y_max": 512, "width": 512, "height": 512},
        },
        camera_params_source={"validity": {"psf_exposure_safe": True}},
    )
    yy, xx = np.mgrid[:512, :512]
    base = 25.0 + 0.001 * np.sin(xx / 13.0) + 0.001 * np.cos(yy / 17.0)
    for wavelength_index, wavelength_nm in enumerate(wavelengths):
        for repeat_index in range(5):
            repeat_pattern = 0.01 * (repeat_index - 2) + 0.0001 * wavelength_index * np.sin((xx + yy) / 29.0)
            crop = base + repeat_pattern
            writer.append_capture(
                crop=crop,
                lowres_mask=np.zeros((1, 64, 64), dtype=np.uint8),
                mask_id="all_closed_window",
                mask_family="deterministic",
                wavelength_nm=wavelength_nm,
                wavelength_index=wavelength_index,
                repeat_index=repeat_index,
                exposure_us=exposures[wavelength_index],
                gain_db=0.0,
                camera_profile_id=f"wl{int(wavelength_nm)}_test",
                mask_metadata={"mask_id": "all_closed_window", "mask_family": "deterministic"},
            )
    writer.finalize(completed=True)


def test_export_closed_lcd_residual_release_contract(tmp_path: Path) -> None:
    raw_h5 = tmp_path / "psf_dictionary.h5"
    release_dir = tmp_path / "closed_lcd_release"
    _write_raw_dictionary_with_closed_lcd(raw_h5)

    manifest = export_closed_lcd_residual_release(source=raw_h5, release_dir=release_dir, release_name="test_closed_lcd_release")

    assert manifest["release_type"] == "closed_lcd_avg10_roi_residual"
    assert manifest["is_sensor_dark"] is False
    assert manifest["is_single_frame_burst"] is False
    assert (release_dir / "closed_lcd_roi512_avg10_residuals.h5").exists()
    assert (release_dir / "closed_lcd_roi512_avg10_stats.csv").exists()
    assert (release_dir / "figures" / "residual_histograms.png").exists()
    assert (release_dir / "provenance" / "source_files.json").exists()
    assert (release_dir / "scripts" / "validate_closed_lcd_residual_release.py").exists()

    with h5py.File(str(release_dir / "closed_lcd_roi512_avg10_residuals.h5"), "r") as f:
        crops = f["closed_lcd/crops_avg10"][()]
        mean = f["closed_lcd/mean_avg10"][()]
        residuals = f["closed_lcd/residuals_avg10"][()]
        assert crops.shape == (3, 5, 512, 512)
        assert mean.shape == (3, 512, 512)
        assert f["closed_lcd/residuals_256"].shape == (3, 5, 256, 256)
        assert np.allclose(residuals, crops - mean[:, np.newaxis, :, :], atol=1e-5)
        assert bool(f["metadata/is_sensor_dark"][()]) is False
        assert bool(f["metadata/is_closed_lcd_residual"][()]) is True

    validation = validate_closed_lcd_residual_release(release_dir=release_dir)
    assert validation["ok"], validation
    inspection = inspect_closed_lcd_residuals(release_dir / "closed_lcd_roi512_avg10_residuals.h5")
    assert inspection["n_avg_frames"] == 10
    assert inspection["source_mask_id"] == "all_closed_window"

    rows = list(csv.DictReader((release_dir / "closed_lcd_roi512_avg10_stats.csv").open(encoding="utf-8")))
    assert [float(row["wavelength_nm"]) for row in rows] == [450.0, 550.0, 650.0]
    assert all(float(row["mean_count"]) > 24.0 for row in rows)
