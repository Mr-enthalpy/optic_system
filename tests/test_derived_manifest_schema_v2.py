from __future__ import annotations

import json
from pathlib import Path

import pytest

from tasks.artifact_versioning import (
    CURRENT_BUNDLE_SCHEMA_VERSION,
    CURRENT_MANIFEST_SCHEMA_VERSIONS,
    CURRENT_PAYLOAD_SCHEMA_VERSIONS,
)
from tasks.artifacts.validation import ValidityOutcome, check_validity
from tasks.psf import (
    FullFramePSFSurveyError,
    FullFramePSFSurveyManifest,
    PeakPatchPSFDictionaryManifest,
    SensorEnergyCenterError,
    SensorEnergyCenterProfile,
    migrate_full_frame_psf_survey_v1_to_v2,
    migrate_peak_patch_psf_dictionary_v1_to_v2,
    migrate_sensor_energy_center_profile_v1_to_v2,
)


def _survey_v1() -> dict:
    return {
        "artifact_type": "full_frame_psf_survey",
        "schema_version": 1,
        "survey_id": "survey_legacy",
        "source_raw_capture_h5": r"C:\legacy\raw.h5",
        "pupil_profile_id": None,
        "camera_profile_id": None,
        "illumination_mode": "monochromatic",
        "entry_wavelengths_nm": [550.0],
        "entry_illumination_json": ['{"mode":"monochromatic"}'],
        "entry_mask_ids": ["mask_a"],
        "unique_wavelengths_nm": [550.0],
        "unique_mask_ids": ["mask_a"],
        "frame_shape": [2, 3],
        "camera_frame_extent": {
            "mode": "full_sensor",
            "origin_xy": [0, 0],
            "shape_hw": [2, 3],
            "sensor_shape_hw": [2, 3],
        },
        "survey_policy": {"background": "none"},
        "full_frame_role": "scout",
        "notes": None,
    }


def _center_v1() -> dict:
    return {
        "artifact_type": "sensor_energy_center_profile",
        "schema_version": 1,
        "center_profile_id": "center_legacy",
        "source_survey_h5": "legacy-survey.h5",
        "coordinate_frame": "sensor_full_frame",
        "camera_frame_extent": {},
        "center_xy": [1.0, 2.0],
        "estimator_name": "legacy",
        "bg_policy": {},
        "corr_policy": {},
        "aggregation_policy": {},
        "per_entry_center_xy": [[1.0, 2.0]],
        "per_entry_mask_ids": ["mask_a"],
        "per_entry_wavelengths_nm": [550.0],
        "per_wavelength_mean_center_xy": {"550": [1.0, 2.0]},
        "per_wavelength_center_std_xy": {"550": [0.0, 0.0]},
        "global_center_std_xy": [0.0, 0.0],
        "max_center_deviation_px": 0.0,
    }


def _dictionary_v1() -> dict:
    return {
        "artifact_type": "peak_patch_psf_dictionary",
        "schema_version": 1,
        "dictionary_id": "dictionary_legacy",
        "source_raw_capture_h5": "raw.h5",
        "peak_layout_profile": "layout.json",
        "pupil_profile_id": None,
        "camera_profile_id": None,
        "illumination_mode": "monochromatic",
        "entry_wavelengths_nm": [550.0],
        "entry_mask_ids": ["mask_a"],
        "unique_wavelengths_nm": [550.0],
        "unique_mask_ids": ["mask_a"],
        "frame_shape": [2, 3],
        "camera_frame_extent": {},
        "peak_layout_coordinate_frame": "sensor_full_frame",
        "peak_layout_camera_frame_extent": {},
        "peak_ids": ["peak_0"],
        "patch_shape_hw": [[1, 1]],
        "patch_origin_xy": [[0, 0]],
        "applied_background_policy": "none",
        "applied_normalization_policy": "none",
        "notes": None,
    }


def test_manifest_payload_and_bundle_versions_are_independent() -> None:
    assert CURRENT_MANIFEST_SCHEMA_VERSIONS["full_frame_psf_survey"] == 2
    assert CURRENT_PAYLOAD_SCHEMA_VERSIONS["full_frame_psf_survey"] == 1
    assert "raw_capture" not in CURRENT_MANIFEST_SCHEMA_VERSIONS
    assert CURRENT_PAYLOAD_SCHEMA_VERSIONS["raw_capture"] == 3
    assert CURRENT_BUNDLE_SCHEMA_VERSION == 1


def test_v1_read_cannot_be_written_without_explicit_migration() -> None:
    legacy = FullFramePSFSurveyManifest.from_dict(_survey_v1())
    assert legacy.source_schema_version == 1
    assert legacy.source_raw_capture_artifact_id is None
    assert legacy.legacy_source_raw_capture_h5 == r"C:\legacy\raw.h5"

    with pytest.raises(FullFramePSFSurveyError, match="cannot be written"):
        legacy.to_dict()

    migrated = migrate_full_frame_psf_survey_v1_to_v2(
        legacy, source_raw_capture_artifact_id="raw_capture_001"
    )
    assert migrated.to_dict()["schema_version"] == 2
    assert "source_raw_capture_h5" not in migrated.to_dict()


def test_each_version_rejects_fields_owned_by_the_other_version() -> None:
    v1 = _survey_v1()
    v1["source_raw_capture_artifact_id"] = "raw_capture_001"
    with pytest.raises(ValueError, match="unknown serialized field"):
        FullFramePSFSurveyManifest.from_dict(v1)

    v2 = migrate_full_frame_psf_survey_v1_to_v2(
        FullFramePSFSurveyManifest.from_dict(_survey_v1()),
        source_raw_capture_artifact_id="raw_capture_001",
    ).to_dict()
    v2["source_raw_capture_h5"] = "raw.h5"
    with pytest.raises(ValueError, match="unknown serialized field"):
        FullFramePSFSurveyManifest.from_dict(v2)


def test_v2_artifact_reference_rejects_machine_path(tmp_path: Path) -> None:
    mapping = migrate_full_frame_psf_survey_v1_to_v2(
        FullFramePSFSurveyManifest.from_dict(_survey_v1()),
        source_raw_capture_artifact_id="raw_capture_001",
    ).to_dict()
    mapping["source_raw_capture_artifact_id"] = r"C:\capture\raw.h5"
    path = tmp_path / "survey.json"
    path.write_text(json.dumps(mapping), encoding="utf-8")

    result = check_validity("full_frame_psf_survey", path)
    assert result.outcome is ValidityOutcome.INVALID
    assert result.reason_codes == (
        "provenance.source_raw_capture_artifact_id.invalid",
    )


def test_deterministic_constructor_error_is_invalid_not_validator_failure(
    tmp_path: Path,
) -> None:
    mapping = migrate_full_frame_psf_survey_v1_to_v2(
        FullFramePSFSurveyManifest.from_dict(_survey_v1()),
        source_raw_capture_artifact_id="raw_capture_001",
    ).to_dict()
    mapping["frame_shape"] = ["not-an-integer", 3]
    path = tmp_path / "malformed-survey.json"
    path.write_text(json.dumps(mapping), encoding="utf-8")

    result = check_validity("full_frame_psf_survey", path)
    assert result.outcome is ValidityOutcome.INVALID
    assert result.reason_codes == (
        "semantic.full_frame_psf_survey.construction_invalid",
    )


def test_unknown_v1_center_diagnostics_are_not_synthesized() -> None:
    legacy = SensorEnergyCenterProfile.from_dict(_center_v1())
    assert legacy.per_entry_background_value == [None]
    assert legacy.per_entry_total_corr_energy == [None]
    with pytest.raises(SensorEnergyCenterError, match="explicit background"):
        migrate_sensor_energy_center_profile_v1_to_v2(
            legacy, source_survey_artifact_id="survey_001"
        )


def test_dictionary_migration_requires_explicit_extent_evidence() -> None:
    legacy = PeakPatchPSFDictionaryManifest.from_dict(_dictionary_v1())
    assert legacy.extent_compatibility is None
    migrated = migrate_peak_patch_psf_dictionary_v1_to_v2(
        legacy,
        source_raw_capture_artifact_id="raw_capture_001",
        peak_layout_artifact_id="layout_001",
        extent_compatibility={
            "matches": True,
            "mismatch_override": False,
            "reason": None,
        },
    )
    assert migrated.extent_compatibility == {
        "matches": True,
        "mismatch_override": False,
        "reason": None,
    }
