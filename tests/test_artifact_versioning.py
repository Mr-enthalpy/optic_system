from __future__ import annotations

import json
from pathlib import Path

import pytest

from tasks.artifact_versioning import (
    CURRENT_SCHEMA_VERSIONS,
    LegacyUnversionedArtifactError,
    SchemaCompatibilityError,
    check_validity,
    emit_schema_version,
    read_schema_version,
    schema_compat,
)
from tasks.profiles import CameraProfile, PupilProfile
from tasks.psf.build_full_frame_psf_survey import FullFramePSFSurveyManifest

from test_profile_artifacts import (
    per_band_camera_profile_dict,
    pupil_profile_dict,
)


def test_emit_and_read_roundtrip():
    data: dict = {}
    emit_schema_version(data, "pupil_profile")
    assert data["schema_version"] == CURRENT_SCHEMA_VERSIONS["pupil_profile"]
    assert read_schema_version(data, "pupil_profile") == data["schema_version"]


def test_missing_schema_version_requires_explicit_legacy_mode():
    with pytest.raises(LegacyUnversionedArtifactError, match="legacy_unversioned"):
        read_schema_version({}, "camera_profile")

    assert read_schema_version({}, "camera_profile", legacy_mode=True) == (
        CURRENT_SCHEMA_VERSIONS["camera_profile"]
    )


def test_newer_schema_version_rejected():
    current = schema_compat("camera_profile").current
    with pytest.raises(SchemaCompatibilityError, match="newer"):
        read_schema_version({"schema_version": current + 1}, "camera_profile")


def test_unknown_artifact_type_rejected():
    with pytest.raises(SchemaCompatibilityError, match="unknown"):
        schema_compat("nope")


@pytest.mark.parametrize("value", [True, 1.5, "1", None, "abc"])
def test_non_integer_schema_version_rejected(value):
    with pytest.raises(SchemaCompatibilityError):
        read_schema_version({"schema_version": value}, "camera_profile")


def test_camera_profile_emits_schema_version(tmp_path: Path):
    profile = CameraProfile.from_dict(per_band_camera_profile_dict())
    path = tmp_path / "camera_profile.json"
    profile.to_json(path)

    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["schema_version"] == CURRENT_SCHEMA_VERSIONS["camera_profile"]

    reloaded = CameraProfile.load_json(path)
    assert reloaded.camera_profile_id == profile.camera_profile_id


def test_pupil_profile_emits_schema_version(tmp_path: Path):
    profile = PupilProfile.from_dict(pupil_profile_dict())
    path = tmp_path / "pupil_profile.json"
    profile.to_json(path)

    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["schema_version"] == CURRENT_SCHEMA_VERSIONS["pupil_profile"]


def test_check_validity_ok(tmp_path: Path):
    profile = PupilProfile.from_dict(pupil_profile_dict())
    path = tmp_path / "pupil_profile.json"
    profile.to_json(path)

    result = check_validity("pupil_profile", path)

    assert result.ok
    assert result.schema_version == CURRENT_SCHEMA_VERSIONS["pupil_profile"]
    assert result.errors == ()


def test_check_validity_missing_file(tmp_path: Path):
    result = check_validity("pupil_profile", tmp_path / "nope.json")
    assert not result.ok
    assert any("does not exist" in e for e in result.errors)
    assert "path" not in result.__dataclass_fields__
    assert str(tmp_path) not in "\n".join(result.errors)


def test_check_validity_type_mismatch(tmp_path: Path):
    profile = PupilProfile.from_dict(pupil_profile_dict())
    path = tmp_path / "pupil_profile.json"
    profile.to_json(path)

    result = check_validity("camera_profile", path)

    assert not result.ok
    assert any("artifact_type mismatch" in e for e in result.errors)


def test_check_validity_incompatible_schema(tmp_path: Path):
    profile = PupilProfile.from_dict(pupil_profile_dict())
    data = profile.to_dict()
    data["schema_version"] = schema_compat("pupil_profile").current + 5
    path = tmp_path / "pupil_profile.json"
    path.write_text(json.dumps(data), encoding="utf-8")

    result = check_validity("pupil_profile", path)

    assert not result.ok
    assert any("newer" in e for e in result.errors)


def test_check_validity_rejects_unversioned_legacy_artifact(tmp_path: Path):
    profile = PupilProfile.from_dict(pupil_profile_dict())
    data = profile.to_dict()
    del data["schema_version"]
    path = tmp_path / "legacy_pupil_profile.json"
    path.write_text(json.dumps(data), encoding="utf-8")

    result = check_validity("pupil_profile", path)

    assert not result.ok
    assert any("legacy_unversioned" in error for error in result.errors)


def test_check_validity_requires_artifact_type(tmp_path: Path):
    profile = PupilProfile.from_dict(pupil_profile_dict())
    data = profile.to_dict()
    del data["artifact_type"]
    path = tmp_path / "missing_type.json"
    path.write_text(json.dumps(data), encoding="utf-8")

    result = check_validity("pupil_profile", path)

    assert not result.ok
    assert any("artifact_type is required" in error for error in result.errors)


@pytest.mark.parametrize("artifact_type", ["raw_capture"])
def test_check_validity_fails_closed_without_implemented_validator(
    tmp_path: Path,
    artifact_type: str,
):
    path = tmp_path / f"{artifact_type}.payload"
    path.write_bytes(b"placeholder")

    result = check_validity(artifact_type, path)

    assert not result.ok
    assert any("validator_not_implemented" in error for error in result.errors)


def test_check_validity_uses_registered_derived_manifest_adapter(
    tmp_path: Path,
):
    manifest = FullFramePSFSurveyManifest(
        survey_id="survey_001",
        source_raw_capture_artifact_id="raw_capture_001",
        pupil_profile_id=None,
        camera_profile_id=None,
        illumination_mode="monochromatic",
        entry_wavelengths_nm=[550.0],
        entry_illumination_json=['{"mode":"monochromatic"}'],
        entry_mask_ids=["mask_001"],
        unique_wavelengths_nm=[550.0],
        unique_mask_ids=["mask_001"],
        frame_shape=(2, 3),
        camera_frame_extent={
            "mode": "full_sensor",
            "origin_xy": [0, 0],
            "shape_hw": [2, 3],
            "sensor_shape_hw": [2, 3],
        },
        survey_policy={"background": "none"},
    )
    path = tmp_path / "survey.manifest.json"
    manifest.to_json(path)

    result = check_validity("full_frame_psf_survey", path)

    assert result.ok
    assert result.schema_version == 2
