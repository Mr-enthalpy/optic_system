from __future__ import annotations

import json
from pathlib import Path

import h5py
import numpy as np
import yaml

from scripts.analyze_psf_dictionary import analyze_psf_dictionary
from tasks.psf_dictionary_phase3 import PSFDictionaryRawWriter, psf_dictionary_stats_by_mask_and_wavelength


def _frame(center_x: float, center_y: float, shape: tuple[int, int] = (64, 64), amplitude: float = 100.0, seed: int = 0) -> np.ndarray:
    yy, xx = np.mgrid[: shape[0], : shape[1]]
    rng = np.random.default_rng(seed)
    base = 3.0 + amplitude * np.exp(-(((xx - center_x) / 5.0) ** 2 + ((yy - center_y) / 4.5) ** 2))
    return base + rng.normal(0.0, 0.1, size=shape)


def _write_dictionary_raw_h5(raw_h5: Path) -> dict:
    plan_path = Path(__file__).resolve().parents[1] / "plans" / "bishe_psf_dictionary.yaml"
    plan = yaml.safe_load(plan_path.read_text(encoding="utf-8"))
    plan["capture"]["repeats_per_mask"] = 2
    plan["masks"]["include"] = ["all_open_window", "all_closed_window"]
    plan["masks"]["random"]["lowfreq_count"] = 2
    plan["masks"]["random"]["midfreq_count"] = 0
    plan["masks"]["random"]["task_related_count"] = 0
    plan["output"]["raw_h5"] = str(raw_h5)
    plan["psf_roi_key"] = "roi_512"
    writer = PSFDictionaryRawWriter(raw_h5, plan_id=plan["plan_id"]).open()
    writer.write_json_sections(
        plan=plan,
        camera_metadata={"frame_dtype_full_scale": 255.0, "camera_profile_used": "test"},
        lcd_metadata={"physical_shape": [90, 270], "subpixel_axis": 1},
        tls_metadata={"current_wavelength_nm": 450.0, "wavelength_sequence": plan["wavelengths"]},
        pupil_window_source={"phase": "3.1", "physical_shape": [90, 270], "center": {"x": 135.0, "y": 45.0}, "radius": 32.0},
        psf_roi_source={
            "phase": "3.2a",
            "roi": {"x_min": 8, "x_max": 56, "y_min": 8, "y_max": 56, "width": 48, "height": 48},
            "rois": {
                "roi_512": {
                    "x_min": 8,
                    "x_max": 56,
                    "y_min": 8,
                    "y_max": 56,
                    "width": 48,
                    "height": 48,
                    "fits_frame": True,
                }
            },
            "psf_roi_key_used": "roi_512",
            "psf_roi_record_used": {"x_min": 8, "x_max": 56, "y_min": 8, "y_max": 56, "width": 48, "height": 48},
        },
        camera_params_source={"psf_safety_policy": {"valid_pixel_domain": None}, "validity": {"psf_exposure_safe": True}},
    )
    masks = [
        ("all_open_window", "deterministic", np.full((1, 64, 64), 255, dtype=np.uint8), 31.0, 31.0),
        ("all_closed_window", "deterministic", np.zeros((1, 64, 64), dtype=np.uint8), 29.0, 30.5),
        ("random_lowfreq_001", "random_lowfreq", np.tri(64, 64, dtype=np.uint8)[None] * 255, 33.0, 32.0),
        ("random_lowfreq_002", "random_lowfreq", np.flip(np.tri(64, 64, dtype=np.uint8), axis=1)[None] * 255, 30.0, 33.0),
    ]
    for wavelength_index, wavelength_nm in enumerate((450.0, 550.0, 650.0)):
        for mask_index, (mask_id, family, lowres, cx, cy) in enumerate(masks):
            for repeat_index in range(2):
                frame = _frame(
                    cx + 0.1 * repeat_index + 0.02 * wavelength_index,
                    cy - 0.1 * repeat_index - 0.02 * wavelength_index,
                    seed=100 * wavelength_index + mask_index * 10 + repeat_index,
                )
                crop = frame[8:56, 8:56]
                writer.append_capture(
                    frame_avg=frame,
                    crop=crop,
                    lowres_mask=lowres,
                    mask_id=mask_id,
                    mask_family=family,
                    wavelength_nm=wavelength_nm,
                    wavelength_index=wavelength_index,
                    repeat_index=repeat_index,
                    mask_metadata={"mask_id": mask_id, "mask_family": family, "wavelength_nm": wavelength_nm},
                )
    writer.finalize(completed=True)
    return plan


def test_analyze_psf_dictionary_outputs_summary_and_export(tmp_path: Path) -> None:
    raw_h5 = tmp_path / "psf_dictionary.h5"
    _write_dictionary_raw_h5(raw_h5)
    out_dir = tmp_path / "out"
    summary = analyze_psf_dictionary(raw_h5, out_dir)
    assert (out_dir / "psf_dictionary_summary.json").exists()
    assert (out_dir / "psf_dictionary_manifest.json").exists()
    assert (out_dir / "mask_preview_contact_sheet.png").exists() or (out_dir / "mask_preview_contact_sheet.npy").exists()
    assert (out_dir / "psf_preview_contact_sheet.png").exists() or (out_dir / "psf_preview_contact_sheet.npy").exists()
    assert (out_dir / "psf_mean_stack.npy").exists()
    assert (out_dir / "psf_crop_stack.npy").exists()
    assert (out_dir / "mask_lowres_stack.npy").exists()
    assert (out_dir / "export_lcd_forward" / "train.h5").exists()
    assert (out_dir / "export_lcd_forward" / "val.h5").exists()
    assert (out_dir / "export_lcd_forward" / "test.h5").exists()
    assert summary["validity"]["training_ready"] is False
    assert summary["psf_roi_key_used"] == "roi_512"
    readme_text = (out_dir / "export_lcd_forward" / "README.md").read_text(encoding="utf-8")
    assert "single-wavelength" not in readme_text
    assert "- psfs:  [N, 1, L, Hp, Wp]" in readme_text

    split_ids: dict[str, set[str]] = {}
    for split_name in ("train", "val", "test"):
        with h5py.File(str(out_dir / "export_lcd_forward" / f"{split_name}.h5"), "r") as f:
            masks = f["masks"][()]
            psfs = f["psfs"][()]
            ids = {x.decode("utf-8") if isinstance(x, bytes) else str(x) for x in f["mask_id"][()]}
            split_ids[split_name] = ids
            assert masks.ndim == 5
            assert psfs.ndim == 5
            assert masks.shape[1:] == (1, 1, 64, 64)
            assert psfs.shape[1] == 1 and psfs.shape[2] == 3
            assert list(f["wavelengths_nm"][()]) == [450.0, 550.0, 650.0]
            metadata = json.loads((f["metadata_json"][()].decode("utf-8") if isinstance(f["metadata_json"][()], bytes) else str(f["metadata_json"][()])))
            assert metadata["normalization"] == "background_subtract_then_sum_normalize"
            assert metadata["L"] == 3
    assert split_ids["train"].isdisjoint(split_ids["val"])
    assert split_ids["train"].isdisjoint(split_ids["test"])
    assert split_ids["val"].isdisjoint(split_ids["test"])


def test_psf_dictionary_stats_groups_repeat_noise_within_wavelength_only() -> None:
    crop_a_450 = np.full((8, 8), 10.0, dtype=np.float64)
    crop_b_450 = np.full((8, 8), 10.0, dtype=np.float64)
    crop_a_650 = np.full((8, 8), 30.0, dtype=np.float64)
    crop_b_650 = np.full((8, 8), 30.0, dtype=np.float64)
    crops = np.stack([crop_a_450, crop_b_450, crop_a_650, crop_b_650], axis=0)
    mask_ids = ["all_open_window"] * 4
    wavelength_index = np.asarray([0, 0, 1, 1], dtype=np.int64)

    stats = psf_dictionary_stats_by_mask_and_wavelength(crops, mask_ids, wavelength_index)

    assert stats["psf_mean_stack"].shape == (1, 2, 8, 8)
    assert stats["quality"]["mean_repeat_mse"] == 0.0
    assert stats["quality"]["median_repeat_mse"] == 0.0
