from __future__ import annotations

import json
from pathlib import Path

import pytest

from tasks.artifacts.validation import (
    ArtifactRepresentation,
    SchemaAdapter,
    SerializedSchemaError,
    ValidityOutcome,
    check_validity,
    get_schema_adapter,
)
from test_profile_artifacts import (
    per_band_camera_profile_dict,
    pupil_profile_dict,
)


def test_registry_identity_includes_representation_and_version() -> None:
    adapter = get_schema_adapter(
        "camera_profile",
        ArtifactRepresentation.JSON,
        1,
    )

    assert adapter is not None
    assert adapter.key == (
        "camera_profile",
        ArtifactRepresentation.JSON,
        1,
    )
    assert (
        get_schema_adapter(
            "camera_profile",
            ArtifactRepresentation.HDF5,
            1,
        )
        is None
    )


def test_adapter_pipeline_constructs_and_validates_semantics_once() -> None:
    calls: list[str] = []

    def validate_serialized(mapping):
        calls.append("serialized")
        assert mapping["value"] == 3

    def construct(mapping):
        calls.append("construct")
        return {"value": mapping["value"]}

    def validate_semantics(artifact):
        calls.append("semantic")
        assert artifact == {"value": 3}

    adapter = SchemaAdapter(
        artifact_type="example",
        representation=ArtifactRepresentation.JSON,
        schema_version=1,
        allowed_fields=frozenset({"artifact_type", "schema_version", "value"}),
        required_fields=frozenset({"artifact_type", "schema_version", "value"}),
        validate_serialized=validate_serialized,
        construct=construct,
        validate_semantics=validate_semantics,
    )

    assert adapter.parse_and_validate(
        {"artifact_type": "example", "schema_version": 1, "value": 3}
    ) == {"value": 3}
    assert calls == ["serialized", "construct", "semantic"]


@pytest.mark.parametrize(
    ("mapping", "reason_code"),
    [
        (
            {
                "artifact_type": "example",
                "schema_version": 1,
                "value": 3,
                "future": True,
            },
            "schema.field.unknown",
        ),
        (
            {"artifact_type": "example", "schema_version": 1},
            "schema.field.missing",
        ),
    ],
)
def test_adapter_field_contract_is_exact(mapping, reason_code) -> None:
    adapter = SchemaAdapter(
        artifact_type="example",
        representation=ArtifactRepresentation.JSON,
        schema_version=1,
        allowed_fields=frozenset({"artifact_type", "schema_version", "value"}),
        required_fields=frozenset({"artifact_type", "schema_version", "value"}),
        construct=dict,
    )

    with pytest.raises(SerializedSchemaError) as exc_info:
        adapter.parse_and_validate(mapping)

    assert exc_info.value.reason_code == reason_code


def test_camera_profile_v1_preserves_historical_gain_default(
    tmp_path: Path,
) -> None:
    data = per_band_camera_profile_dict()
    data["artifact_type"] = "camera_profile"
    data["schema_version"] = 1
    del data["camera"]["per_wavelength"]["450"]["gain_db"]
    path = tmp_path / "camera-v1.json"
    path.write_text(json.dumps(data), encoding="utf-8")

    result = check_validity("camera_profile", path)

    assert result.outcome is ValidityOutcome.VALID
    assert result.schema_version == 1


def test_pupil_profile_v1_preserves_historical_xywh_window(
    tmp_path: Path,
) -> None:
    data = pupil_profile_dict()
    data["artifact_type"] = "pupil_profile"
    data["schema_version"] = 1
    data["aperture_window"] = [10, 20, 30, 40]
    path = tmp_path / "pupil-v1.json"
    path.write_text(json.dumps(data), encoding="utf-8")

    result = check_validity("pupil_profile", path)

    assert result.outcome is ValidityOutcome.VALID
    assert result.schema_version == 1


def test_hdf5_representation_fails_closed_without_adapter(
    tmp_path: Path,
) -> None:
    path = tmp_path / "profile.h5"
    path.write_bytes(b"\x89HDF\r\n\x1a\nplaceholder")

    result = check_validity("pupil_profile", path)

    assert result.outcome is ValidityOutcome.UNSUPPORTED
    assert result.representation is ArtifactRepresentation.HDF5
    assert result.reason_codes == ("representation.adapter_not_registered",)


def test_reader_limit_is_not_reported_as_schema_invalid(tmp_path: Path) -> None:
    path = tmp_path / "huge.json"
    path.write_text(
        '{"artifact_type":"pupil_profile","schema_version":'
        + ("9" * 5000)
        + "}",
        encoding="utf-8",
    )

    result = check_validity("pupil_profile", path)

    assert result.outcome is ValidityOutcome.UNSUPPORTED
    assert result.reason_codes == ("reader_limit.json_integer_digits",)


def test_result_never_retains_location(tmp_path: Path) -> None:
    result = check_validity("pupil_profile", tmp_path / "missing.json")

    assert "path" not in result.__dataclass_fields__
    assert str(tmp_path) not in "\n".join(result.errors)
