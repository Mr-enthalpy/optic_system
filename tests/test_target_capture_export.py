from __future__ import annotations

import json
from pathlib import Path

import h5py
import numpy as np
import yaml

from scripts.capture_target_multiframe import run_capture_target_multiframe
from scripts.export_target_lcd_forward import export_target_lcd_forward
from tasks.target_capture_phase3 import TargetCaptureRawWriter, load_selected_masks_from_exports


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _write_camera_params(path: Path) -> None:
    _write_json(
        path,
        {
            "frame_dtype_full_scale": 4095.0,
            "validity": {
                "exposure_safety_valid": True,
                "psf_exposure_safe": True,
            },
            "global_safe_camera": {
                "exposure_us": 5000.0,
                "gain_db": 10.0,
                "frames_per_capture": 5,
            },
            "per_gain_safe_params": {
                "10.0": {
                    "exposure_us": 5000.0,
                    "gain_db": 10.0,
                    "frames_per_capture": 5,
                }
            },
            "psf_safety_policy": {
                "rule": "all_frames_all_pixels_strictly_below_full_scale",
                "evaluated_on": "raw_burst_frames",
                "evaluated_domain": "valid_camera_pixel_domain",
                "allow_full_scale_pixel": False,
                "allow_non_finite_pixel": False,
                "valid_pixel_domain": {
                    "type": "full_frame",
                    "valid_pixel_count": 65536,
                    "invalid_pixel_count": 0,
                },
            },
        },
    )


def _write_pupil_window(path: Path) -> None:
    _write_json(
        path,
        {
            "phase": "3.1",
            "center": {"x": 96.0, "y": 48.0},
            "radius": 32.0,
            "physical_shape": [96, 192],
        },
    )


def _write_psf_roi(path: Path) -> None:
    _write_json(
        path,
        {
            "phase": "3.2a",
            "roi": {
                "x_min": 32,
                "x_max": 224,
                "y_min": 32,
                "y_max": 160,
                "width": 192,
                "height": 128,
            },
        },
    )


def _write_mask_export(path: Path, *, wavelengths_nm: list[float] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    masks = np.zeros((2, 1, 1, 64, 64), dtype=np.uint8)
    masks[0, 0, 0] = 255
    masks[1, 0, 0, :, :32] = 255
    wavelengths_nm = wavelengths_nm or [450.0, 550.0]
    string_dtype = h5py.string_dtype(encoding="utf-8")
    with h5py.File(str(path), "w") as f:
        f.create_dataset("masks", data=masks, compression="gzip", compression_opts=4)
        f.create_dataset("wavelengths_nm", data=np.asarray(wavelengths_nm, dtype=np.float64))
        f.create_dataset("mask_id", data=np.asarray(["all_open_window", "vertical_stripes_lowfreq"], dtype=object), dtype=string_dtype)
        f.create_dataset("mask_family", data=np.asarray(["deterministic", "deterministic"], dtype=object), dtype=string_dtype)
        f.create_dataset(
            "metadata_json",
            data=json.dumps({"normalization": "uint8_code_value", "code_range": [0, 255], "wavelengths_nm": wavelengths_nm, "L": len(wavelengths_nm)}),
            dtype=string_dtype,
        )


def _make_temp_plan(tmp_path: Path) -> Path:
    plan = {
        "plan_id": "bishe_target_capture_test",
        "phase": "3.6",
        "camera_params_source": str(tmp_path / "camera_params_psf_safe.json"),
        "camera_gain_selection": "10.0",
        "pupil_window_source": str(tmp_path / "effective_pupil_window.json"),
        "psf_roi_source": str(tmp_path / "psf_roi.json"),
        "mask_source": {
            "type": "lcd_forward_export",
            "h5_paths": [str(tmp_path / "mask_export.h5")],
            "selected_mask_ids": ["all_open_window", "vertical_stripes_lowfreq"],
            "max_masks": 2,
        },
        "target": {
            "target_id": "bishe_demo_target_001",
            "description": "synthetic dry-run target",
            "notes": "test only",
        },
        "wavelengths": [
            {"wavelength_nm": 450.0, "grating": 1},
            {"wavelength_nm": 550.0, "grating": 1},
        ],
        "tls": {"settle_ms": 500},
        "lcd": {
            "display_index": 1,
            "subpixel_axis": 1,
            "settle_ms": 200,
            "logical_shape": [96, 64],
        },
        "capture": {
            "frames_per_capture": 4,
            "repeats_per_condition": 2,
            "include_reference_open": True,
            "include_reference_closed": False,
            "interleave_reference": True,
        },
        "export": {
            "lcd_forward": {
                "enabled": True,
                "output_dir": str(tmp_path / "export_lcd_forward"),
            }
        },
        "output": {
            "raw_h5": str(tmp_path / "bishe_target_capture.h5"),
            "output_dir": str(tmp_path / "outputs_target_capture"),
        },
        "lock_file": str(tmp_path / "capture_hardware.lock"),
    }
    plan_path = tmp_path / "bishe_target_capture.yaml"
    plan_path.write_text(yaml.safe_dump(plan, sort_keys=False), encoding="utf-8")
    return plan_path


def test_load_selected_masks_from_synthetic_export(tmp_path: Path) -> None:
    export_h5 = tmp_path / "mask_export.h5"
    _write_mask_export(export_h5)
    selected, meta = load_selected_masks_from_exports(
        [export_h5],
        selected_mask_ids=["all_open_window", "vertical_stripes_lowfreq"],
        max_masks=2,
        required_wavelengths_nm=[450.0, 550.0],
    )
    assert [item["mask_id"] for item in selected] == ["all_open_window", "vertical_stripes_lowfreq"]
    assert meta["missing_mask_ids"] == []
    assert selected[0]["lowres_mask"].shape == (1, 64, 64)
    assert meta["available_wavelengths_nm"] == [450.0, 550.0]


def test_target_capture_writer_and_export_roundtrip(tmp_path: Path) -> None:
    raw_h5 = tmp_path / "raw_target_capture.h5"
    writer = TargetCaptureRawWriter(raw_h5, plan_id="bishe_target_capture_test").open()
    plan = {
        "plan_id": "bishe_target_capture_test",
        "phase": "3.6",
        "camera_params_source": "camera.json",
        "pupil_window_source": "pupil.json",
        "psf_roi_source": "roi.json",
    }
    writer.write_json_sections(
        plan=plan,
        camera_metadata={"frame_dtype_full_scale": 4095.0},
        lcd_metadata={"physical_shape": [96, 192], "subpixel_axis": 1},
        tls_metadata={"wavelength_sequence": [{"wavelength_nm": 450.0}, {"wavelength_nm": 550.0}]},
        pupil_window_source={"phase": "3.1", "center": {"x": 96.0, "y": 48.0}, "radius": 32.0, "physical_shape": [96, 192]},
        psf_roi_source={"phase": "3.2a", "roi": {"x_min": 0, "x_max": 16, "y_min": 0, "y_max": 16, "width": 16, "height": 16}},
        camera_params_source={"validity": {"psf_exposure_safe": True}},
        mask_source_metadata={"mask_source_type": "lcd_forward_export"},
        target_metadata={"target_id": "bishe_demo_target_001"},
    )
    lowres_open = np.full((1, 64, 64), 255, dtype=np.uint8)
    lowres_vert = np.zeros((1, 64, 64), dtype=np.uint8)
    lowres_vert[:, :, :32] = 255
    for wavelength_index, wavelength_nm in enumerate((450.0, 550.0)):
        for repeat_index in range(2):
            for mask_id, family, lowres, amplitude in (
                ("all_open_window", "deterministic", lowres_open, 100.0),
                ("vertical_stripes_lowfreq", "deterministic", lowres_vert, 120.0),
            ):
                frame = np.full((32, 32), amplitude + wavelength_index + repeat_index, dtype=np.float64)
                crop = frame[:16, :16]
                writer.append_capture(
                    frame_avg=frame,
                    crop=crop,
                    lowres_mask=lowres,
                    mask_id=mask_id,
                    mask_family=family,
                    wavelength_nm=wavelength_nm,
                    wavelength_index=wavelength_index,
                    repeat_index=repeat_index,
                    capture_role="encoded_target",
                    mask_metadata={"mask_id": mask_id, "mask_family": family},
                )
    writer.finalize(completed=True)

    out_dir = tmp_path / "exported"
    result = export_target_lcd_forward(raw_h5, out_dir)
    assert result["frames_shape"] == [1, 2, 2, 1, 16, 16]
    assert result["masks_shape"] == [1, 2, 1, 64, 64]
    assert (out_dir / "target_frames.h5").exists()
    assert (out_dir / "README.md").exists()
    with h5py.File(str(out_dir / "target_frames.h5"), "r") as f:
        assert f["frames"].shape == (1, 2, 2, 1, 16, 16)
        assert f["masks"].shape == (1, 2, 1, 64, 64)
        assert list(f["wavelengths_nm"][()]) == [450.0, 550.0]
        metadata = json.loads(f["metadata_json"][()].decode("utf-8") if isinstance(f["metadata_json"][()], bytes) else str(f["metadata_json"][()]))
        assert metadata["phase"] == "3.6"
        assert metadata["has_ground_truth_objects"] is False


def test_capture_target_multiframe_dry_run_writes_raw_h5(tmp_path: Path) -> None:
    _write_camera_params(tmp_path / "camera_params_psf_safe.json")
    _write_pupil_window(tmp_path / "effective_pupil_window.json")
    _write_psf_roi(tmp_path / "psf_roi.json")
    _write_mask_export(tmp_path / "mask_export.h5")
    plan_path = _make_temp_plan(tmp_path)
    plan = yaml.safe_load(plan_path.read_text(encoding="utf-8"))

    raw_h5 = run_capture_target_multiframe(
        plan,
        dry_run=True,
        lcd_subpixel_axis=1,
        status_dir=tmp_path / "status",
        status_preview_every=1,
    )
    assert raw_h5.exists()
    with h5py.File(str(raw_h5), "r") as f:
        assert "raw/frames_avg" in f
        assert "raw/crops" in f
        assert "raw/masks_lowres" in f
        assert "raw/wavelength_nm" in f
        assert "provenance/mask_source_metadata_json" in f
        assert f["raw/masks_lowres"].shape[1:] == (1, 64, 64)
        roles = {x.decode("utf-8") if isinstance(x, bytes) else str(x) for x in f["raw/capture_role"][()]}
        assert "encoded_target" in roles
        assert "reference_open" in roles


def test_target_capture_rejects_mask_source_without_requested_wavelength_coverage(tmp_path: Path) -> None:
    _write_camera_params(tmp_path / "camera_params_psf_safe.json")
    _write_pupil_window(tmp_path / "effective_pupil_window.json")
    _write_psf_roi(tmp_path / "psf_roi.json")
    _write_mask_export(tmp_path / "mask_export.h5", wavelengths_nm=[550.0])
    plan_path = _make_temp_plan(tmp_path)
    plan = yaml.safe_load(plan_path.read_text(encoding="utf-8"))
    try:
        run_capture_target_multiframe(
            plan,
            dry_run=True,
            lcd_subpixel_axis=1,
            status_dir=tmp_path / "status_missing_wl",
            status_preview_every=1,
        )
    except ValueError as exc:
        assert "not covered by the measured PSF dictionary export" in str(exc)
    else:
        raise AssertionError("expected wavelength coverage validation failure")
