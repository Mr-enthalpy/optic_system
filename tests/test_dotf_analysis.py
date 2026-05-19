from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from scripts.analyze_dotf import analyze_dotf
from tasks.dotf_phase3 import DotfRawWriter


def _gaussian(center_x: float, center_y: float, shape: tuple[int, int] = (64, 64), amplitude: float = 100.0) -> object:
    import numpy as np

    yy, xx = np.mgrid[: shape[0], : shape[1]]
    return 3.0 + amplitude * np.exp(-(((xx - center_x) / 5.0) ** 2 + ((yy - center_y) / 4.5) ** 2))


def _write_raw_h5(raw_h5: Path, *, include_psf_roi_provenance: bool = True) -> dict:
    plan_path = Path(__file__).resolve().parents[1] / "plans" / "bishe_dotf_diagnostic.yaml"
    plan = yaml.safe_load(plan_path.read_text(encoding="utf-8"))
    plan["capture"]["repeats"] = 3
    plan["dotf"]["perturbation_set"] = ["edge_block_right"]
    plan["dotf"]["roi_keys"] = ["roi_256", "roi_512", "roi_1024"]
    plan["output"]["raw_h5"] = str(raw_h5)
    psf_roi = {
        "phase": "3.2a",
        "roi": {"x_min": 272, "x_max": 528, "y_min": 272, "y_max": 528, "width": 256, "height": 256},
        "rois": {
            "roi_256": {
                "x_min": 272,
                "x_max": 528,
                "y_min": 272,
                "y_max": 528,
                "width": 256,
                "height": 256,
                "fits_frame": True,
                "purpose": ["current_baseline", "preview", "legacy_compatibility"],
            },
            "roi_512": {
                "x_min": 144,
                "x_max": 656,
                "y_min": 144,
                "y_max": 656,
                "width": 512,
                "height": 512,
                "fits_frame": True,
                "purpose": ["dotf_candidate", "model_candidate"],
            },
            "roi_1024": {
                "x_min": -112,
                "x_max": 912,
                "y_min": -112,
                "y_max": 912,
                "width": 1024,
                "height": 1024,
                "fits_frame": False,
                "skip_reason": "ROI exceeds frame boundary",
                "purpose": ["dotf_candidate", "full_support_candidate"],
            },
        },
        "current_baseline_roi_key": "roi_256",
        "default_roi_key": "roi_256",
        "final_selected_roi_key": None,
    }
    writer = DotfRawWriter(raw_h5, plan_id=plan["plan_id"]).open()
    writer.write_json_sections(
        plan=plan,
        camera_metadata={"frame_dtype_full_scale": 255.0, "camera_profile_used": "test"},
        lcd_metadata={"physical_shape": [90, 270], "subpixel_axis": 1},
        tls_metadata={"current_wavelength_nm": 550.0},
        pupil_window_source={"phase": "3.1", "physical_shape": [90, 270], "center": {"x": 120.0, "y": 45.0}, "radius": 32.0},
        psf_roi_source=psf_roi if include_psf_roi_provenance else {},
        camera_params_source={"validity": {"psf_exposure_safe": True}},
    )
    for idx in range(3):
        ref = _gaussian(399.5, 399.0, shape=(800, 800), amplitude=120.0)
        pert = ref - 4.0 * _gaussian(437.0, 399.0, shape=(800, 800), amplitude=1.0)
        ref_crop = ref[272:528, 272:528]
        pert_crop = pert[272:528, 272:528]
        writer.append_capture(
            frame_avg=ref,
            crop=ref_crop,
            mask_id="dotf_reference",
            repeat_index=idx,
            capture_role="reference",
            perturbation_id="none",
            mask_metadata={"mask_id": "dotf_reference", "capture_role": "reference", "perturbation_id": "none"},
        )
        writer.append_capture(
            frame_avg=pert,
            crop=pert_crop,
            mask_id="dotf_right",
            repeat_index=idx,
            capture_role="perturbed",
            perturbation_id="edge_block_right",
            mask_metadata={
                "mask_id": "dotf_right",
                "capture_role": "perturbed",
                "perturbation_id": "edge_block_right",
            },
        )
    writer.finalize(completed=True)
    return plan


def test_analyze_dotf_outputs_files(tmp_path: Path) -> None:
    raw_h5 = tmp_path / "dotf_raw.h5"
    _write_raw_h5(raw_h5)
    out_dir = tmp_path / "out"
    result = analyze_dotf(raw_h5, out_dir)
    edge_dir = out_dir / "edge_block_right"
    roi_256_dir = out_dir / "roi_256" / "edge_block_right"
    roi_512_dir = out_dir / "roi_512" / "edge_block_right"
    assert (out_dir / "psf_reference.npy").exists()
    assert (out_dir / "psf_reference.png").exists() or (out_dir / "psf_reference.npy").exists()
    assert (edge_dir / "dotf_complex.npy").exists()
    assert (roi_256_dir / "dotf_complex.npy").exists()
    assert (roi_512_dir / "dotf_complex.npy").exists()
    assert (edge_dir / "dotf_abs.png").exists() or (edge_dir / "dotf_abs.npy").exists()
    assert (edge_dir / "dotf_phase.png").exists() or (edge_dir / "dotf_phase.npy").exists()
    assert (edge_dir / "dotf_real.png").exists() or (edge_dir / "dotf_real.npy").exists()
    assert (edge_dir / "dotf_imag.png").exists() or (edge_dir / "dotf_imag.npy").exists()
    metrics = json.loads((out_dir / "dotf_metrics.json").read_text(encoding="utf-8"))
    comparison_manifest = json.loads((out_dir / "dotf_roi_comparison_manifest.json").read_text(encoding="utf-8"))
    per_roi_metrics = json.loads((roi_512_dir / "dotf_metrics.json").read_text(encoding="utf-8"))
    report = (out_dir / "dotf_report.md").read_text(encoding="utf-8")
    assert result["validity"]["dotf_computed"] is True
    assert metrics["validity"]["pupil_stitching_performed"] is False
    assert metrics["validity"]["roi_selection_performed"] is False
    assert metrics["roi_key"] == "roi_256"
    assert comparison_manifest["rois"]["roi_256"]["analyzed"] is True
    assert comparison_manifest["rois"]["roi_512"]["analyzed"] is True
    assert comparison_manifest["rois"]["roi_1024"]["analyzed"] is False
    assert per_roi_metrics["roi_key"] == "roi_512"
    assert per_roi_metrics["validity"]["roi_selection_performed"] is False
    assert "edge_energy" in per_roi_metrics
    assert "pupil_stitching_performed=false" in report


def test_analyze_dotf_requires_psf_roi_provenance(tmp_path: Path) -> None:
    raw_h5 = tmp_path / "dotf_missing_roi.h5"
    _write_raw_h5(raw_h5, include_psf_roi_provenance=False)
    with pytest.raises(ValueError, match="psf_roi_source_json"):
        analyze_dotf(raw_h5, tmp_path / "out_missing")
