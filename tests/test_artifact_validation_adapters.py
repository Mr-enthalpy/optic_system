from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest
import tasks.artifacts.validation as validation_module

from tasks.artifacts.validation import (
    ArtifactRepresentation,
    ArtifactVersionSet,
    SchemaAdapter,
    SerializedSchemaError,
    ValidityOutcome,
    check_validity,
    get_schema_adapter,
)
from tasks.profiles.camera_profile import CameraProfile
from tasks.profiles.pupil_profile import PupilProfile
from test_profile_artifacts import per_band_camera_profile_dict, pupil_profile_dict


def _write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


def test_exact_lookup_includes_representation_and_version_set() -> None:
    adapter = get_schema_adapter(
        "camera_profile", ArtifactRepresentation.JSON, ArtifactVersionSet(manifest=1)
    )

    assert adapter is not None
    assert adapter.key == (
        "camera_profile",
        ArtifactRepresentation.JSON,
        ArtifactVersionSet(manifest=1),
    )
    assert (
        get_schema_adapter(
            "camera_profile", ArtifactRepresentation.HDF5, ArtifactVersionSet(payload=1)
        )
        is None
    )


def test_json_adapter_stages_receive_the_original_document_once() -> None:
    calls: list[str] = []
    document = {"artifact_type": "example", "schema_version": 1, "value": [3]}

    def serialized(value):
        calls.append("serialized")
        assert value is document

    def construct(value):
        calls.append("construct")
        assert value is document
        return value["value"]

    adapter = SchemaAdapter(
        artifact_type="example",
        representation=ArtifactRepresentation.JSON,
        versions=ArtifactVersionSet(manifest=1),
        allowed_fields=frozenset(document),
        required_fields=frozenset({"artifact_type", "schema_version", "value"}),
        validate_serialized=serialized,
        construct=construct,
        validate_semantics=lambda value: calls.append("semantic") or value == [3],
    )

    assert adapter.parse_and_validate(document) == [3]
    assert calls == ["serialized", "construct", "semantic"]
    assert not hasattr(validation_module, "_deep_freeze")


@pytest.mark.parametrize(
    ("artifact_type", "data"),
    [
        ("camera_profile", per_band_camera_profile_dict()),
        ("pupil_profile", pupil_profile_dict()),
    ],
)
def test_builtin_profile_bridges_do_not_mutate_documents(artifact_type: str, data: dict) -> None:
    data.update({"artifact_type": artifact_type, "schema_version": 1})
    snapshot = deepcopy(data)
    adapter = get_schema_adapter(
        artifact_type, ArtifactRepresentation.JSON, ArtifactVersionSet(manifest=1)
    )

    assert adapter is not None
    adapter.parse_and_validate(data)
    assert data == snapshot


@pytest.mark.parametrize(
    ("artifact_type", "factory"),
    [
        ("camera_profile", lambda: CameraProfile.from_dict(per_band_camera_profile_dict())),
        ("pupil_profile", lambda: PupilProfile.from_dict(pupil_profile_dict())),
    ],
)
def test_production_json_writer_round_trips_to_current_validator(
    tmp_path: Path, artifact_type: str, factory
) -> None:
    path = tmp_path / f"{artifact_type}.json"
    factory().to_json(path)

    result = check_validity(artifact_type, path)

    assert result.outcome is ValidityOutcome.VALID
    assert result.representation is ArtifactRepresentation.JSON


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_profile_writers_reject_nonfinite_json(value: float) -> None:
    profile = CameraProfile.from_dict(per_band_camera_profile_dict())
    profile.per_wavelength["450"].exposure_us = value

    with pytest.raises(Exception):
        profile.to_json()


def test_json_is_parsed_once_on_the_validation_happy_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "profile.json"
    CameraProfile.from_dict(per_band_camera_profile_dict()).to_json(path)
    real_parse = validation_module._parse_json_text_value
    calls = 0

    def counted_parse(text: str):
        nonlocal calls
        calls += 1
        return real_parse(text)

    monkeypatch.setattr(validation_module, "_parse_json_text_value", counted_parse)

    assert check_validity("camera_profile", path).outcome is ValidityOutcome.VALID
    assert calls == 1


def test_normal_json_is_dispatched_as_json(tmp_path: Path) -> None:
    path = tmp_path / "profile.json"
    PupilProfile.from_dict(pupil_profile_dict()).to_json(path)

    result = check_validity("pupil_profile", path)

    assert result.outcome is ValidityOutcome.VALID
    assert result.representation is ArtifactRepresentation.JSON


@pytest.mark.parametrize("offset", [0, 512])
def test_hdf5_signature_is_selected_before_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, offset: int
) -> None:
    path = tmp_path / f"hdf5-{offset}.bin"
    path.write_bytes(b"{" * offset + b"\x89HDF\r\n\x1a\n" + b"not-json")
    monkeypatch.setattr(
        validation_module,
        "_parse_json_text_value",
        lambda _text: pytest.fail("HDF5 must not be parsed as JSON"),
    )

    result = check_validity("pupil_profile", path)

    assert result.outcome is ValidityOutcome.UNSUPPORTED
    assert result.representation is ArtifactRepresentation.HDF5


def test_arbitrary_binary_fails_as_unsupported_json(tmp_path: Path) -> None:
    path = tmp_path / "binary.bin"
    path.write_bytes(b"\x00\xff\x10\x80")

    result = check_validity("pupil_profile", path)

    assert result.outcome is ValidityOutcome.UNSUPPORTED
    assert result.representation is ArtifactRepresentation.JSON


def test_directory_is_a_bundle_placeholder(tmp_path: Path) -> None:
    path = tmp_path / "bundle"
    path.mkdir()

    result = check_validity("pupil_profile", path)

    assert result.outcome is ValidityOutcome.UNSUPPORTED
    assert result.representation is ArtifactRepresentation.BUNDLE


def test_damaged_json_is_unreadable(tmp_path: Path) -> None:
    path = tmp_path / "damaged.json"
    path.write_text('{"artifact_type":"pupil_profile"', encoding="utf-8")

    result = check_validity("pupil_profile", path)

    assert result.outcome is ValidityOutcome.UNREADABLE
    assert result.reason_codes == ("representation.json.parse_error",)


def test_missing_path_is_unreadable(tmp_path: Path) -> None:
    result = check_validity("pupil_profile", tmp_path / "missing.json")

    assert result.outcome is ValidityOutcome.UNREADABLE
    assert result.reason_codes == ("location.missing",)


def test_duplicate_json_keys_are_rejected(tmp_path: Path) -> None:
    path = tmp_path / "duplicate.json"
    path.write_text(
        '{"artifact_type":"pupil_profile","artifact_type":"camera_profile"}',
        encoding="utf-8",
    )

    result = check_validity("pupil_profile", path)

    assert result.outcome is ValidityOutcome.UNREADABLE
    assert result.reason_codes == ("representation.json.duplicate_key",)


def test_legacy_profile_defaults_and_window_semantics_remain_readable(
    tmp_path: Path,
) -> None:
    camera = per_band_camera_profile_dict()
    camera.update({"artifact_type": "camera_profile", "schema_version": 1})
    del camera["camera"]["per_wavelength"]["450"]["gain_db"]
    camera_path = tmp_path / "camera.json"
    _write_json(camera_path, camera)
    pupil = pupil_profile_dict()
    pupil.update({"artifact_type": "pupil_profile", "schema_version": 1})
    pupil["aperture_window"] = [900, 700, 500, 480]
    pupil_path = tmp_path / "pupil.json"
    _write_json(pupil_path, pupil)

    assert check_validity("camera_profile", camera_path).outcome is ValidityOutcome.VALID
    assert check_validity("pupil_profile", pupil_path).outcome is ValidityOutcome.VALID


def test_wrong_version_and_wrong_type_fail_closed(tmp_path: Path) -> None:
    path = tmp_path / "profile.json"
    data = pupil_profile_dict()
    data.update({"artifact_type": "pupil_profile", "schema_version": 2})
    _write_json(path, data)

    assert check_validity("pupil_profile", path).outcome is ValidityOutcome.UNSUPPORTED
    assert check_validity("camera_profile", path).outcome is ValidityOutcome.INVALID
