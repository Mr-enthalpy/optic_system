from __future__ import annotations

import json
from pathlib import Path

import h5py
import numpy as np
import pytest

from scripts.export_thesis_calibration_figures import (
    _check_required_files,
    _compute_energy_decomposition,
    _compute_roi_energy_coverage,
    _locate_artifacts,
    _load_exposure_data,
    _load_lcd_pupil_data,
    _load_psf_roi_data,
    export_thesis_calibration_figures,
)


def _make_release_fixture(tmp_path: Path) -> Path:
    root = tmp_path / "release"
    (root / "common" / "provenance" / "raw_h5").mkdir(parents=True)
    (root / "thesis" / "metrics" / "pupil_geometry").mkdir(parents=True)
    (root / "thesis" / "metrics" / "psf_roi").mkdir(parents=True)
    (root / "thesis" / "metrics" / "exposure_calibration").mkdir(parents=True)

    _write_pupil_window_json(root / "thesis" / "metrics" / "pupil_geometry" / "effective_pupil_window.json")
    _write_pupil_h5(root / "common" / "provenance" / "raw_h5" / "bishe_pupil_geometry.h5")
    _write_psf_roi_json(root / "thesis" / "metrics" / "psf_roi" / "psf_roi.json")
    _write_psf_roi_h5(root / "common" / "provenance" / "raw_h5" / "bishe_psf_roi.h5")
    _write_camera_params(root / "thesis" / "metrics" / "exposure_calibration" / "camera_params_psf_safe.json")
    return root


def _write_pupil_window_json(path: Path) -> None:
    data = {
        "schema_version": 1,
        "phase": "3.1",
        "task": "pupil_geometry_calibration",
        "strategy": "bar_profiles_plus_radius_scan",
        "source_raw_h5": "data/raw/bishe_pupil_geometry.h5",
        "capture_plan_id": "bishe_pupil_geometry_calibration",
        "camera_params_source": "outputs/exposure_calibration/camera_params_psf_safe.json",
        "camera_profile_requested": None,
        "camera_profile_used": "global_safe_camera",
        "fallback_used": False,
        "wavelength_nm": 550.0,
        "physical_shape": [2560, 1620],
        "subpixel_axis": 1,
        "center": {"x": 1065.2462265532397, "y": 1871.5352061814938},
        "radius": 52.79722598558055,
        "radius_source": "factor_of_ellipse_semi_minor",
        "radius_factor_of_b": 0.9,
        "ellipse": {
            "a": 115.51445902470007,
            "b": 58.66358442842284,
            "k": 12.179085803872354,
            "r_squared": 0.999178591231608,
            "pearson": 0.9996135827182211,
            "rmse": 2801.373711102217,
        },
        "validity": {
            "effective_window_estimated": True,
            "scientific_calibration_valid": False,
            "training_ready": False,
        },
    }
    path.write_text(json.dumps(data), encoding="utf-8")


def _write_pupil_h5(path: Path) -> None:
    with h5py.File(str(path), "w") as f:
        _write_json_dataset(f, "capture/plan_json", {"plan_id": "test_geom"})
        _write_json_dataset(f, "tls/metadata_json", {"target_wavelength_nm": 550.0})
        _write_json_dataset(f, "lcd/metadata_json", {"physical_shape": [2560, 1620], "subpixel_axis": 1})
        _write_json_dataset(f, "capture/processing_flags_json", {})
        _write_array(f, "bar_scan/x/positions", np.linspace(900, 1200, 50, dtype=np.float64))
        _write_array(f, "bar_scan/x/energies", np.random.default_rng(42).uniform(1e8, 2e8, 50).astype(np.float64))
        _write_array(f, "bar_scan/y/positions", np.linspace(1700, 2000, 50, dtype=np.float64))
        _write_array(f, "bar_scan/y/energies", np.random.default_rng(43).uniform(1e8, 2e8, 50).astype(np.float64))
        _write_array(f, "radius_scan/radii", np.linspace(10, 120, 40, dtype=np.float64))
        _write_array(f, "radius_scan/energies", np.random.default_rng(44).uniform(0, 5e3, 40).astype(np.float64))
        _write_array(f, "references/dark_frame_avg", np.full((2048, 2448), 25.0, dtype=np.float64))
        _write_json_dataset(f, "camera/camera_params_source_json", {"source": "camera_params.json", "camera_profile_used": "test"})
        _write_json_dataset(f, "camera/metadata_json", {})
        _write_scalar_str(f, "capture/plan_id", "test_geom")


def _write_psf_roi_json(path: Path) -> None:
    data = {
        "schema_version": 2,
        "phase": "3.2a",
        "center": {"x": 1149.128, "y": 934.509, "method": "peak_then_center_of_mass"},
        "frame_shape": [2048, 2448],
        "roi": {"x_min": 1021, "x_max": 1277, "y_min": 807, "y_max": 1063, "width": 256, "height": 256},
        "rois": {
            "roi_256": {"x_min": 1021, "x_max": 1277, "y_min": 807, "y_max": 1063, "width": 256, "height": 256, "fits_frame": True},
            "roi_512": {"x_min": 893, "x_max": 1405, "y_min": 679, "y_max": 1191, "width": 512, "height": 512, "fits_frame": True},
            "roi_768": {"x_min": 765, "x_max": 1533, "y_min": 551, "y_max": 1319, "width": 768, "height": 768, "fits_frame": True},
            "roi_1024": {"x_min": 637, "x_max": 1661, "y_min": 423, "y_max": 1447, "width": 1024, "height": 1024, "fits_frame": True},
        },
        "current_baseline_roi_key": "roi_256",
        "default_roi_key": "roi_256",
        "final_selected_roi_key": "roi_512",
        "selection_policy": "manual_after_dotf_visual_inspection",
        "quality": {"roi_energy_fraction": 0.44846083647854107, "full_scale_in_avg_valid_domain": False},
        "wavelength_nm": 550.0,
        "coordinate_system": "camera sensor coordinates",
    }
    path.write_text(json.dumps(data), encoding="utf-8")


def _write_psf_roi_h5(path: Path) -> None:
    rng = np.random.default_rng(99)
    frames = rng.normal(25, 5, (3, 2048, 2448)).astype(np.float64)
    gy, gx = np.mgrid[0:2048, 0:2448].astype(np.float64)
    gaussian = 200 * np.exp(-(((gx - 1149) ** 2 + (gy - 934) ** 2) / (2 * 80**2)))
    frames += gaussian[np.newaxis, :, :]
    with h5py.File(str(path), "w") as f:
        f.create_dataset("raw/frames_avg", data=frames, dtype=np.float64)
        _write_json_dataset(f, "capture/plan_json", {"wavelength": {"wavelength_nm": 550.0}})
        _write_json_dataset(f, "tls/metadata_json", {"target_wavelength_nm": 550.0})
        _write_json_dataset(f, "lcd/metadata_json", {})
        _write_json_dataset(f, "camera/metadata_json", {})
        _write_json_dataset(f, "capture/processing_flags_json", {})
        _write_json_dataset(f, "provenance/camera_params_source_json", {})
        _write_json_dataset(f, "provenance/pupil_window_source_json", {})
        f.create_dataset("raw/mask_id", data=np.array([b"test_mask"] * 3, dtype="S20"))
        f.create_dataset("raw/repeat_index", data=np.arange(3, dtype=np.int64))
        f.create_dataset("raw/timestamp_ns", data=np.arange(3, dtype=np.int64))
        f.create_dataset("raw/mask_metadata_json", data=np.array([b"{}"] * 3, dtype="S5"))


def _write_camera_params(path: Path) -> None:
    data = {
        "schema_version": 2,
        "frame_dtype_full_scale": 255,
        "camera_param_catalog": {
            "450.0": {"recommended": {"exposure_us": 779.6875, "gain_db": 0.0}},
            "550.0": {"recommended": {"exposure_us": 487.3046875, "gain_db": 0.0}},
            "650.0": {"recommended": {"exposure_us": 2241.6015625, "gain_db": 0.0}},
        },
    }
    path.write_text(json.dumps(data), encoding="utf-8")


def _write_json_dataset(f: h5py.File, key: str, data: dict) -> None:
    text = json.dumps(data)
    f.create_dataset(key, data=np.bytes_(text.encode("utf-8")))


def _write_array(f: h5py.File, key: str, arr: np.ndarray) -> None:
    f.create_dataset(key, data=arr)


def _write_scalar_str(f: h5py.File, key: str, value: str) -> None:
    f.create_dataset(key, data=np.bytes_(value.encode("utf-8")))


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------


def test_locate_artifacts_finds_all_files(tmp_path: Path) -> None:
    root = _make_release_fixture(tmp_path)
    artifacts = _locate_artifacts(root)
    for key, path in artifacts.items():
        assert path.exists(), f"{key} not found: {path}"


def test_missing_artifacts_fails_clearly(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    artifacts = _locate_artifacts(empty)
    with pytest.raises(FileNotFoundError, match="not found"):
        _check_required_files(artifacts)


def test_load_lcd_pupil_data(tmp_path: Path) -> None:
    root = _make_release_fixture(tmp_path)
    artifacts = _locate_artifacts(root)
    data = _load_lcd_pupil_data(root, artifacts)
    assert "center" in data
    assert len(data["center"]) == 2
    assert data["center"][0] > 0
    assert data["effective_radius"] > 0
    assert "r_squared" in data["ellipse"]
    assert data["bar_x_positions"].size > 0
    assert data["bar_y_positions"].size > 0
    assert data["radii"].size > 0


def test_load_psf_roi_data(tmp_path: Path) -> None:
    root = _make_release_fixture(tmp_path)
    artifacts = _locate_artifacts(root)
    data = _load_psf_roi_data(root, artifacts)
    assert "center" in data
    assert data["mean_frame"].shape == (2048, 2448)
    assert data["selected_roi_key"] == "roi_512"
    assert "roi_256" in data["candidate_rois"]
    assert data["roi_256_energy_frac"] > 0
    assert "energy_coverage" in data
    for rk in ["roi_256", "roi_512", "roi_768", "roi_1024"]:
        assert rk in data["energy_coverage"], f"{rk} missing from energy_coverage"
        assert 0 < data["energy_coverage"][rk] < 1.0
    assert data["background"] > 0
    assert data["peak_pixel"] > data["background"]


def test_compute_roi_energy_coverage() -> None:
    rng = np.random.default_rng(99)
    frames = rng.normal(25, 5, (2048, 2448)).astype(np.float64)
    gy, gx = np.mgrid[0:2048, 0:2448].astype(np.float64)
    gaussian = 200 * np.exp(-(((gx - 1149) ** 2 + (gy - 934) ** 2) / (2 * 80**2)))
    mean_frame = frames + gaussian
    rois = {
        "roi_256": {"x_min": 1021, "x_max": 1277, "y_min": 807, "y_max": 1063, "width": 256, "height": 256, "fits_frame": True},
        "roi_512": {"x_min": 893, "x_max": 1405, "y_min": 679, "y_max": 1191, "width": 512, "height": 512, "fits_frame": True},
        "roi_768": {"x_min": 765, "x_max": 1533, "y_min": 551, "y_max": 1319, "width": 768, "height": 768, "fits_frame": True},
        "roi_1024": {"x_min": 637, "x_max": 1661, "y_min": 423, "y_max": 1447, "width": 1024, "height": 1024, "fits_frame": True},
    }
    cov = _compute_roi_energy_coverage(mean_frame, rois)
    for rk in rois:
        assert rk in cov
        assert 0 < cov[rk] < 1.0
        leakage = 1.0 - cov[rk]
        assert 0 < leakage < 1.0
    assert cov["roi_256"] < cov["roi_512"] < cov["roi_768"] < cov["roi_1024"]


def test_load_exposure_data(tmp_path: Path) -> None:
    root = _make_release_fixture(tmp_path)
    artifacts = _locate_artifacts(root)
    data = _load_exposure_data(root, artifacts)
    assert "450.0" in data["exposure"]
    assert data["exposure"]["450.0"]["exposure_us"] > 0
    assert data["exposure"]["550.0"]["exposure_us"] > 0
    assert data["exposure"]["650.0"]["exposure_us"] > 0


def test_generates_output_files(tmp_path: Path) -> None:
    root = _make_release_fixture(tmp_path)
    out = tmp_path / "out"
    out.mkdir()

    manifest = export_thesis_calibration_figures(
        phase3_release=root,
        out_dir=out,
        dpi=72,
        fmt="both",
    )

    assert (out / "appendix_lcd_effective_pupil_annotated.pdf").exists()
    assert (out / "appendix_lcd_effective_pupil_annotated.png").exists()
    assert (out / "appendix_psf_roi_comparison.pdf").exists()
    assert (out / "appendix_psf_roi_comparison.png").exists()
    assert (out / "appendix_calibration_summary.csv").exists()
    assert (out / "thesis_optic_system_figures_manifest.json").exists()

    assert "figures" in manifest
    assert "lcd_effective_pupil" in manifest["figures"]
    assert "psf_roi_comparison" in manifest["figures"]
    assert manifest["hardware_required"] is False


def test_manifest_contains_center_and_roi(tmp_path: Path) -> None:
    root = _make_release_fixture(tmp_path)
    out = tmp_path / "out"
    out.mkdir()

    manifest = export_thesis_calibration_figures(
        phase3_release=root,
        out_dir=out,
        dpi=72,
        fmt="png",
    )

    lcd = manifest["figures"]["lcd_effective_pupil"]
    assert len(lcd["center_xy"]) == 2
    assert lcd["center_xy"][0] > 0
    assert lcd["effective_radius_px"] > 0
    assert lcd["fit_r2"] > 0.9

    psf = manifest["figures"]["psf_roi_comparison"]
    assert len(psf["psf_center_xy"]) == 2
    assert psf["selected_roi"] == 512
    assert 256 in psf["roi_candidates"]
    assert 512 in psf["roi_candidates"]
    energy = psf["known_energy_coverage"]
    for rk in ["roi_256", "roi_512", "roi_768", "roi_1024"]:
        assert rk in energy
        assert 0 < energy[rk] < 1.0


def test_csv_contains_required_fields(tmp_path: Path) -> None:
    import csv

    root = _make_release_fixture(tmp_path)
    out = tmp_path / "out"
    out.mkdir()

    export_thesis_calibration_figures(phase3_release=root, out_dir=out, dpi=72, fmt="png")

    with open(out / "appendix_calibration_summary.csv", "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    items = {row["item"] for row in rows}
    required = {
        "lcd_center_x", "lcd_center_y", "lcd_effective_radius", "lcd_fit_r2",
        "psf_center_x", "psf_center_y", "selected_roi",
        "roi_256_energy_coverage", "roi_512_energy_coverage",
        "roi_768_energy_coverage", "roi_1024_energy_coverage",
        "exposure_450nm", "exposure_550nm", "exposure_650nm",
        "gain_450nm", "gain_550nm", "gain_650nm",
    }
    assert required.issubset(items), f"missing items: {required - items}"

    for rk in ["roi_256", "roi_512", "roi_768", "roi_1024"]:
        row = next(r for r in rows if r["item"] == f"{rk}_energy_coverage")
        assert row["value"] != "N/A"
        val = float(row["value"])
        assert 0 < val < 1.0, f"{rk} energy coverage {val} out of range"


def test_no_hardware_import_at_module_level() -> None:
    mod = "scripts.export_thesis_calibration_figures"
    with open(Path(__file__).resolve().parent.parent / "scripts" / "export_thesis_calibration_figures.py", "r", encoding="utf-8") as f:
        source = f.read()
    hardware_imports = [
        "from devices.camera",
        "from devices.tls",
        "from devices.lcd",
        "from capture",
        "from camera_sdk",
        "from tls_c1",
    ]
    for imp in hardware_imports:
        assert imp not in source, f"hardware import found: {imp}"


def test_png_only_format(tmp_path: Path) -> None:
    root = _make_release_fixture(tmp_path)
    out = tmp_path / "out"
    out.mkdir()

    export_thesis_calibration_figures(phase3_release=root, out_dir=out, dpi=72, fmt="png")

    assert (out / "appendix_lcd_effective_pupil_annotated.png").exists()
    assert not (out / "appendix_lcd_effective_pupil_annotated.pdf").exists()
    assert (out / "appendix_psf_roi_comparison.png").exists()
    assert not (out / "appendix_psf_roi_comparison.pdf").exists()


def test_energy_decomposition_computation(tmp_path: Path) -> None:
    root = _make_release_fixture(tmp_path)
    artifacts = _locate_artifacts(root)
    psf_data = _load_psf_roi_data(root, artifacts)

    decomp = _compute_energy_decomposition(psf_data, root)

    assert "roi_enclosed" in decomp
    assert "far_field_thresholds" in decomp
    assert "support_totals" in decomp
    assert "metadata" in decomp

    for rk in ["roi_256", "roi_512", "roi_768", "roi_1024"]:
        assert rk in decomp["roi_enclosed"]
        for domain in ["r<200", "r<300", "r<500", "full"]:
            assert domain in decomp["roi_enclosed"][rk]
            assert decomp["roi_enclosed"][rk][domain] > 0

    assert len(decomp["far_field_thresholds"]) == 6
    assert decomp["far_field_thresholds"][0]["threshold"] == 0.0
    assert decomp["far_field_thresholds"][0]["pct_total"] > decomp["far_field_thresholds"][2]["pct_total"]
    assert decomp["metadata"]["noise_floor_artifact_pp"] > 0
    assert decomp["metadata"]["genuine_diffraction_pp"] > 0

    assert decomp["support_totals"]["full"] >= decomp["support_totals"]["r<200"]


def test_u2b_figure_and_csv_generate(tmp_path: Path) -> None:
    root = _make_release_fixture(tmp_path)
    out = tmp_path / "out"
    out.mkdir()

    export_thesis_calibration_figures(phase3_release=root, out_dir=out, dpi=72, fmt="both")

    assert (out / "appendix_roi_energy_decomposition.pdf").exists()
    assert (out / "appendix_roi_energy_decomposition.png").exists()
    assert (out / "appendix_roi_energy_decomposition.csv").exists()


def test_energy_decomposition_csv_fields(tmp_path: Path) -> None:
    import csv

    root = _make_release_fixture(tmp_path)
    out = tmp_path / "out"
    out.mkdir()

    export_thesis_calibration_figures(phase3_release=root, out_dir=out, dpi=72, fmt="png")

    with open(out / "appendix_roi_energy_decomposition.csv", "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    sections = {row["section"] for row in rows}
    assert "support_domain" in sections
    assert "far_field" in sections
    assert "metadata" in sections

    items = {row["item"] for row in rows}
    assert "r<200_total_signal" in items
    assert "noise_floor_artifact_pp" in items
    assert "genuine_diffraction_pp" in items
    for rk in ["roi_256", "roi_512"]:
        assert f"{rk}_enclosed_r<300" in items
    for t in ["0.0", "0.1", "0.5", "1.0", "5.0", "10.0"]:
        assert f"threshold_{t}_pct_total" in items


def test_manifest_includes_decomposition_fields(tmp_path: Path) -> None:
    import json

    root = _make_release_fixture(tmp_path)
    out = tmp_path / "out"
    out.mkdir()

    export_thesis_calibration_figures(phase3_release=root, out_dir=out, dpi=72, fmt="png")

    manifest = json.loads((out / "thesis_optic_system_figures_manifest.json").read_text(encoding="utf-8"))
    psf_fig = manifest["figures"]["psf_roi_comparison"]
    assert "roi_energy_decomposition" in psf_fig
    rde = psf_fig["roi_energy_decomposition"]
    assert "full_frame_denominator" in rde
    assert "noise_floor_artifact_pp" in rde
    assert "genuine_diffraction_pp" in rde
    assert "data_provenance" in rde
    assert rde["data_provenance"]["psf_source"] == "bishe_psf_roi.h5/raw/frames_avg"


def test_u2c_tail_enhanced_generates(tmp_path: Path) -> None:
    root = _make_release_fixture(tmp_path)
    out = tmp_path / "out"
    out.mkdir()

    export_thesis_calibration_figures(phase3_release=root, out_dir=out, dpi=72, fmt="both")

    assert (out / "appendix_psf_tail_enhanced.pdf").exists()
    assert (out / "appendix_psf_tail_enhanced.png").exists()


def test_wavelength_psf_scale_skips_gracefully(tmp_path: Path) -> None:
    root = _make_release_fixture(tmp_path)
    out = tmp_path / "out"
    out.mkdir()

    # the fixture doesn't have bishe_psf_repeatability.h5 at the D: drive path,
    # so Fig 3 should skip gracefully and the rest should still work
    manifest = export_thesis_calibration_figures(phase3_release=root, out_dir=out, dpi=72, fmt="png")
    assert "figures" in manifest  # all other figures still generated
