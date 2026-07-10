from __future__ import annotations

import json
from pathlib import Path

import pytest

from tasks.artifact_versioning import (
    CURRENT_SCHEMA_VERSIONS,
    SchemaCompatibilityError,
    check_validity,
    emit_schema_version,
    read_schema_version,
    schema_compat,
)
from tasks.profiles import CameraProfile, PupilProfile

from test_profile_artifacts import (
    per_band_camera_profile_dict,
    pupil_profile_dict,
)


def test_emit_and_read_roundtrip():
    data: dict = {}
    emit_schema_version(data, "pupil_profile")
    assert data["schema_version"] == CURRENT_SCHEMA_VERSIONS["pupil_profile"]
    assert read_schema_version(data, "pupil_profile") == data["schema_version"]


def test_missing_schema_version_defaults_to_current():
    assert read_schema_version({}, "camera_profile") == (
        CURRENT_SCHEMA_VERSIONS["camera_profile"]
    )


def test_newer_schema_version_rejected():
    current = schema_compat("camera_profile").current
    with pytest.raises(SchemaCompatibilityError, match="newer"):
        read_schema_version({"schema_version": current + 1}, "camera_profile")


def test_unknown_artifact_type_rejected():
    with pytest.raises(SchemaCompatibilityError, match="unknown"):
        schema_compat("nope")


def test_non_integer_schema_version_rejected():
    with pytest.raises(SchemaCompatibilityError):
        read_schema_version({"schema_version": "abc"}, "camera_profile")


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
