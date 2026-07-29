from __future__ import annotations

import json
from pathlib import Path

import h5py
import numpy as np
import pytest

from tasks.artifacts.validation import ValidityOutcome, check_validity
from tasks.psf.analyze_diffraction_support import (
    DiffractionSupportAnalysisError,
    PeakSupportAnalysisManifest,
    analyze_diffraction_support,
)


def _decode(value) -> str:
    return value.decode("utf-8") if isinstance(value, bytes) else str(value)


def _write_synthetic_survey(path: Path) -> None:
    frame = np.zeros((64, 64), dtype=np.float64)
    frame += 0.02
    frame[20:24, 20:24] += 4.0
    frame[5:7, 56:58] += 3.0
    frame[40, 3] += 0.04
    with h5py.File(str(path), "w") as f:
        f.attrs["survey_id"] = "synthetic_survey"
        string_dtype = h5py.string_dtype(encoding="utf-8")
        g = f.require_group("full_frame_survey")
        g.create_dataset("frames_avg", data=frame[np.newaxis, :, :])
        g.create_dataset("entry_wavelength_nm", data=np.asarray([550.0], dtype=np.float64))
        g.create_dataset("entry_mask_ids", data=np.asarray(["mask_a"], dtype=object), dtype=string_dtype)
        g.create_dataset(
            "entry_illumination_json",
            data=np.asarray([
                json.dumps({"mode": "monochromatic", "effective_wavelength_nm": 550.0}),
            ], dtype=object),
            dtype=string_dtype,
        )
        g.create_dataset(
            "camera_frame_extent_json",
            data=json.dumps({
                "mode": "full_sensor",
                "origin_xy": [0, 0],
                "shape_hw": [64, 64],
                "sensor_shape_hw": [64, 64],
            }),
            dtype=string_dtype,
        )


def _write_broadband_synthetic_survey(path: Path) -> None:
    frame = np.full((64, 64), 0.02, dtype=np.float64)
    frame[20:24, 20:24] += 4.0
    illumination = json.dumps(
        {
            "mode": "broadband_passthrough",
            "effective_wavelength_nm": None,
            "tls_setpoint_nm": 0.0,
            "wavelength_label_nm": None,
        },
        sort_keys=True,
    )
    with h5py.File(str(path), "w") as f:
        f.attrs["survey_id"] = "broadband_synthetic_survey"
        string_dtype = h5py.string_dtype(encoding="utf-8")
        g = f.require_group("full_frame_survey")
        g.create_dataset("frames_avg", data=np.stack([frame, frame]))
        g.create_dataset(
            "entry_wavelength_nm",
            data=np.asarray([float("nan"), float("nan")], dtype=np.float64),
        )
        g.create_dataset(
            "entry_mask_ids",
            data=np.asarray(["mask_a", "mask_b"], dtype=object),
            dtype=string_dtype,
        )
        g.create_dataset(
            "entry_illumination_json",
            data=np.asarray([illumination, illumination], dtype=object),
            dtype=string_dtype,
        )
        g.create_dataset(
            "camera_frame_extent_json",
            data=json.dumps({
                "mode": "full_sensor",
                "origin_xy": [0, 0],
                "shape_hw": [64, 64],
                "sensor_shape_hw": [64, 64],
            }),
            dtype=string_dtype,
        )


def _write_raw_frames_h5(path: Path) -> None:
    frame = np.full((16, 16), 0.02, dtype=np.float64)
    frame[4:6, 4:6] += 3.0
    with h5py.File(str(path), "w") as f:
        string_dtype = h5py.string_dtype(encoding="utf-8")
        raw = f.require_group("raw")
        raw.create_dataset("frames_avg", data=frame[np.newaxis, :, :])
        raw.create_dataset("mask_id", data=np.asarray(["raw_mask"], dtype=object), dtype=string_dtype)
        raw.create_dataset("wavelength_nm", data=np.asarray([550.0], dtype=np.float64))
        camera = f.require_group("camera")
        camera.create_dataset(
            "frame_extent_json",
            data=[json.dumps({
                "mode": "full_sensor",
                "origin_xy": [0, 0],
                "shape_hw": [16, 16],
                "sensor_shape_hw": [16, 16],
            })],
            dtype=string_dtype,
        )


def test_builds_peak_support_analysis_report_from_synthetic_survey(tmp_path: Path) -> None:
    survey_h5 = tmp_path / "survey.h5"
    report_h5 = tmp_path / "support.h5"
    _write_synthetic_survey(survey_h5)

    manifest = analyze_diffraction_support(
        survey_h5,
        report_h5,
        report_id="support_v1",
        tau_values=[0.5],
        support_radii=[10, 30],
        far_field_radius=20,
        min_component_area=2,
    )

    assert manifest.report_id == "support_v1"
    assert manifest.source_survey_artifact_id == "synthetic_survey"
    assert manifest.frame_shape == (64, 64)
    assert manifest.coordinate_frame == "sensor_full_frame"
    assert manifest.entry_mask_ids == ["mask_a"]
    assert report_h5.exists()
    assert check_validity(
        "peak_support_analysis_report", report_h5
    ).outcome is ValidityOutcome.VALID

    with h5py.File(str(report_h5), "r") as f:
        assert f.attrs["manifest_schema_version"] == 2
        assert f.attrs["payload_schema_version"] == 1
        assert f["support_analysis/frame_shape"][()].tolist() == [64, 64]
        assert f["support_analysis/background_value"].shape == (1,)
        assert f["support_analysis/compact_support_energy"].shape == (1, 2)
        assert f["support_analysis/far_field_significant_energy"].shape == (1, 1)
        assert f["components/bbox_xyxy"].shape[1] == 4
        assert _decode(f["source/survey_artifact_id"][()]) == "synthetic_survey"


def test_component_table_policy_requires_components_group(tmp_path: Path) -> None:
    survey_h5 = tmp_path / "survey.h5"
    report_h5 = tmp_path / "support_without_components.h5"
    _write_synthetic_survey(survey_h5)
    analyze_diffraction_support(
        survey_h5,
        report_h5,
        tau_values=[0.5],
        support_radii=[10],
        min_component_area=2,
    )
    with h5py.File(report_h5, "r+") as h5:
        del h5["components"]

    result = check_validity("peak_support_analysis_report", report_h5)

    assert result.outcome is ValidityOutcome.INVALID
    assert result.reason_codes == ("component_table_presence_mismatch",)


def test_uses_fifth_percentile_background_and_corr_clip(tmp_path: Path) -> None:
    survey_h5 = tmp_path / "survey.h5"
    report_h5 = tmp_path / "support.h5"
    _write_synthetic_survey(survey_h5)

    analyze_diffraction_support(
        survey_h5,
        report_h5,
        tau_values=[0.5],
        support_radii=[100],
        far_field_radius=20,
    )

    with h5py.File(str(report_h5), "r") as f:
        bg = float(f["support_analysis/background_value"][0])
        total = float(f["support_analysis/total_corr_energy"][0])
    assert np.isclose(bg, 0.02)
    assert np.isclose(total, 16 * 4.0 + 4 * 3.0 + 0.04)


def test_threshold_sweep_splits_noise_floor_and_significant_far_field(tmp_path: Path) -> None:
    survey_h5 = tmp_path / "survey.h5"
    report_h5 = tmp_path / "support.h5"
    _write_synthetic_survey(survey_h5)

    analyze_diffraction_support(
        survey_h5,
        report_h5,
        tau_values=[0.5],
        support_radii=[100],
        far_field_radius=20,
        min_component_area=2,
    )

    with h5py.File(str(report_h5), "r") as f:
        sig_energy = float(f["support_analysis/far_field_significant_energy"][0, 0])
        noise_energy = float(f["support_analysis/far_field_noise_energy"][0, 0])
        sig_count = int(f["support_analysis/far_field_significant_pixel_count"][0, 0])
        noise_count = int(f["support_analysis/far_field_noise_pixel_count"][0, 0])
    assert np.isclose(sig_energy, 12.0)
    assert np.isclose(noise_energy, 0.04)
    assert sig_count == 4
    assert noise_count > sig_count


def test_component_table_detects_far_field_significant_component(tmp_path: Path) -> None:
    survey_h5 = tmp_path / "survey.h5"
    report_h5 = tmp_path / "support.h5"
    _write_synthetic_survey(survey_h5)

    analyze_diffraction_support(
        survey_h5,
        report_h5,
        tau_values=[0.5],
        support_radii=[100],
        far_field_radius=20,
        min_component_area=2,
    )

    with h5py.File(str(report_h5), "r") as f:
        boxes = f["components/bbox_xyxy"][()]
        areas = f["components/area"][()]
        energies = f["components/energy"][()]
        far = f["components/is_far_field"][()]
        centroids = f["components/centroid_xy"][()]
    assert any(tuple(row) == (56, 5, 58, 7) for row in boxes)
    assert 1 not in set(int(x) for x in areas)
    assert np.max(energies) >= 12.0
    assert np.any(far)
    assert centroids.shape[1] == 2


def test_broadband_survey_component_table_validates_nan_wavelengths(
    tmp_path: Path,
) -> None:
    survey_h5 = tmp_path / "broadband_survey.h5"
    report_h5 = tmp_path / "broadband_support.h5"
    _write_broadband_synthetic_survey(survey_h5)

    manifest = analyze_diffraction_support(
        survey_h5,
        report_h5,
        tau_values=[0.5],
        support_radii=[100],
        far_field_radius=20,
        min_component_area=2,
    )

    assert len(manifest.entry_wavelengths_nm) == 2
    assert manifest.entry_wavelengths_nm == [None, None]
    with h5py.File(str(report_h5), "r") as f:
        assert f["components/entry_index"].shape[0] > 0
        assert np.isnan(f["components/wavelength_nm"][()]).all()
        manifest_text = _decode(f["metadata/manifest_json"][()])
        assert "NaN" not in manifest_text
        assert json.loads(manifest_text)["entry_wavelengths_nm"] == [None, None]
    assert (
        check_validity("peak_support_analysis_report", report_h5).outcome
        is ValidityOutcome.VALID
    )


def test_manifest_round_trips_and_p99_is_visualization_only(tmp_path: Path) -> None:
    survey_h5 = tmp_path / "survey.h5"
    report_h5 = tmp_path / "support.h5"
    _write_synthetic_survey(survey_h5)

    analyze_diffraction_support(survey_h5, report_h5, tau_values=[0.5], support_radii=[100])

    with h5py.File(str(report_h5), "r") as f:
        manifest_json = json.loads(_decode(f["metadata/manifest_json"][()]))
    manifest = PeakSupportAnalysisManifest.from_dict(manifest_json)
    assert manifest.to_dict()["artifact_type"] == "peak_support_analysis_report"
    text = json.dumps(manifest.to_dict())
    assert "p=0.99" not in text
    assert manifest.corr_policy["p99_display_tail_normalization_is_visualization_only"] is True


def test_manifest_records_resolved_valid_pixel_domain(tmp_path: Path) -> None:
    survey_h5 = tmp_path / "survey.h5"
    report_h5 = tmp_path / "support.h5"
    _write_synthetic_survey(survey_h5)

    manifest = analyze_diffraction_support(
        survey_h5,
        report_h5,
        tau_values=[0.5],
        support_radii=[100],
        valid_pixel_domain={"type": "exclude_xyxy", "xyxy": [0, 0, 4, 4]},
    )

    record = manifest.valid_pixel_domain
    assert record is not None
    assert record["resolved_policy"] == {"type": "exclude_xyxy", "xyxy": [0, 0, 4, 4]}
    assert record["frame_shape_hw"] == [64, 64]
    assert record["excluded_pixel_count"] == 16
    assert record["mask_digest"].startswith("sha256:")

    with h5py.File(str(report_h5), "r") as f:
        manifest_json = json.loads(_decode(f["metadata/manifest_json"][()]))
    reloaded = PeakSupportAnalysisManifest.from_dict(manifest_json)
    assert reloaded.valid_pixel_domain == record


def test_acquired_frame_extent_maps_to_acquired_frame_coordinate_frame(tmp_path: Path) -> None:
    survey_h5 = tmp_path / "survey_acquired.h5"
    report_h5 = tmp_path / "support.h5"
    _write_synthetic_survey(survey_h5)
    with h5py.File(str(survey_h5), "a") as f:
        del f["full_frame_survey/camera_frame_extent_json"]
        f["full_frame_survey"].create_dataset(
            "camera_frame_extent_json",
            data=json.dumps({
                "mode": "sensor_roi",
                "origin_xy": [100, 50],
                "shape_hw": [64, 64],
                "sensor_shape_hw": [2048, 2448],
            }),
            dtype=h5py.string_dtype(encoding="utf-8"),
        )

    manifest = analyze_diffraction_support(survey_h5, report_h5, tau_values=[0.5], support_radii=[100])

    assert manifest.coordinate_frame == "acquired_frame"
    assert manifest.camera_frame_extent["mode"] == "sensor_roi"


def test_rejects_raw_frames_avg_input(tmp_path: Path) -> None:
    raw_h5 = tmp_path / "raw.h5"
    report_h5 = tmp_path / "support.h5"
    _write_raw_frames_h5(raw_h5)

    with pytest.raises(
        DiffractionSupportAnalysisError,
        match="PeakSupportAnalysisReport requires FullFramePSFSurvey",
    ):
        analyze_diffraction_support(raw_h5, report_h5, tau_values=[0.5], support_radii=[10])


def test_energy_only_report_skips_component_table_but_keeps_energy_metrics(tmp_path: Path) -> None:
    survey_h5 = tmp_path / "survey.h5"
    report_h5 = tmp_path / "support_energy_only.h5"
    _write_synthetic_survey(survey_h5)

    manifest = analyze_diffraction_support(
        survey_h5,
        report_h5,
        tau_values=[0.5],
        support_radii=[10, 30],
        far_field_radius=20,
        energy_only=True,
    )

    assert manifest.component_policy["analysis_mode"] == "energy_only"
    assert manifest.component_policy["component_table_written"] is False
    assert manifest.component_policy["frame_read_policy"] == "hdf5_entry_streaming"
    with h5py.File(str(report_h5), "r") as f:
        assert "components" not in f
        assert f["support_analysis/background_value"].shape == (1,)
        assert f["support_analysis/compact_support_energy"].shape == (1, 2)
        assert f["support_analysis/far_field_noise_energy"].shape == (1, 1)
        assert f["support_analysis/far_field_significant_pixel_count"].shape == (1, 1)

    with pytest.raises(DiffractionSupportAnalysisError, match="energy-only"):
        from tasks.psf.analyze_diffraction_support import propose_peak_supports_from_report
        propose_peak_supports_from_report(report_h5, tau=0.5)


def test_measured_full_frame_preset_records_min_component_area(tmp_path: Path) -> None:
    survey_h5 = tmp_path / "survey.h5"
    report_h5 = tmp_path / "support_preset.h5"
    _write_synthetic_survey(survey_h5)

    manifest = analyze_diffraction_support(
        survey_h5,
        report_h5,
        tau_values=[0.5],
        support_radii=[100],
        far_field_radius=20,
        preset_name="measured_full_frame_2048",
    )

    assert manifest.component_policy["preset_name"] == "measured_full_frame_2048"
    assert manifest.component_policy["min_component_area"] == 8
    with h5py.File(str(report_h5), "r") as f:
        areas = f["components/area"][()]
        manifest_json = json.loads(_decode(f["metadata/manifest_json"][()]))
    assert all(int(x) >= 8 for x in areas)
    assert manifest_json["component_policy"]["preset_name"] == "measured_full_frame_2048"
