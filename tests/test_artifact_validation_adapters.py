from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest

from tasks.artifacts.adapter_catalog import validate_registry_completeness
from tasks.artifacts.validation import (
    AdditionalFieldsPolicy,
    ArtifactIdentity,
    ArtifactRepresentation,
    ArtifactVersionSet,
    LegacyCompatibilityBridge,
    ParsedRepresentation,
    RepresentationReader,
    RepresentationReaderRegistry,
    SchemaAdapter,
    SchemaAdapterRegistry,
    SemanticValidationError,
    SemanticValidationMode,
    SerializedSchemaError,
    ValidityOutcome,
    ValidityResult,
    check_validity,
    get_schema_adapter,
    register_representation_reader,
    register_schema_adapter,
)
from test_profile_artifacts import (
    per_band_camera_profile_dict,
    pupil_profile_dict,
)


def _register_exact(adapter: SchemaAdapter) -> SchemaAdapterRegistry:
    registry = SchemaAdapterRegistry()
    register_schema_adapter(adapter, registry=registry)
    registry.freeze()
    return registry


def _json_adapter(
    artifact_type: str,
    version: int = 1,
    *,
    construct=dict,
    validate_semantics=lambda _artifact: None,
) -> SchemaAdapter:
    return SchemaAdapter(
        artifact_type=artifact_type,
        representation=ArtifactRepresentation.JSON,
        versions=ArtifactVersionSet(manifest=version),
        allowed_fields=frozenset({"artifact_type", "schema_version", "value"}),
        required_fields=frozenset({"artifact_type", "schema_version"}),
        construct=construct,
        validate_semantics=validate_semantics,
    )


def test_registry_identity_includes_representation_and_version_set() -> None:
    adapter = get_schema_adapter(
        "camera_profile",
        ArtifactRepresentation.JSON,
        1,
    )

    assert adapter is not None
    assert adapter.key == (
        "camera_profile",
        ArtifactRepresentation.JSON,
        ArtifactVersionSet(manifest=1),
    )
    assert (
        get_schema_adapter(
            "camera_profile",
            ArtifactRepresentation.HDF5,
            ArtifactVersionSet(payload=1),
        )
        is None
    )


def test_adapter_pipeline_invokes_each_configured_stage_once() -> None:
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
        versions=ArtifactVersionSet(manifest=1),
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
    assert adapter.semantic_mode is SemanticValidationMode.EXPLICIT


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
def test_exact_adapter_enforces_declared_top_level_fields(
    mapping,
    reason_code,
) -> None:
    adapter = SchemaAdapter(
        artifact_type="example",
        representation=ArtifactRepresentation.JSON,
        versions=ArtifactVersionSet(manifest=1),
        allowed_fields=frozenset({"artifact_type", "schema_version", "value"}),
        required_fields=frozenset({"artifact_type", "schema_version", "value"}),
        construct=dict,
        validate_semantics=lambda _artifact: None,
    )

    with pytest.raises(SerializedSchemaError) as exc_info:
        adapter.parse_and_validate(mapping)

    assert exc_info.value.reason_code == reason_code


def test_adapter_validates_its_own_identity() -> None:
    adapter = _json_adapter("example")

    with pytest.raises(SerializedSchemaError, match="identity") as exc_info:
        adapter.parse_and_validate(
            {"artifact_type": "different", "schema_version": 1}
        )

    assert exc_info.value.reason_code == "schema.identity.mismatch"


def test_registered_non_json_reader_and_adapter_execute_without_core_branch(
    tmp_path: Path,
) -> None:
    calls: list[str] = []

    class FakeHDFReader(RepresentationReader):
        representation = ArtifactRepresentation.HDF5

        def detect(self, path: Path) -> bool:
            calls.append("detect")
            return path.read_bytes() == b"fake-hdf"

        def parse(
            self,
            path: Path,
            expected_artifact_type: str,
        ) -> ParsedRepresentation:
            calls.append("parse")
            return ParsedRepresentation(
                ArtifactIdentity(
                    expected_artifact_type,
                    self.representation,
                    ArtifactVersionSet(payload=3),
                ),
                {"payload": path.read_bytes()},
            )

    readers = RepresentationReaderRegistry()
    register_representation_reader(FakeHDFReader(), registry=readers)
    readers.freeze()
    adapters = _register_exact(
        SchemaAdapter(
            artifact_type="fake_payload",
            representation=ArtifactRepresentation.HDF5,
            versions=ArtifactVersionSet(payload=3),
            construct=lambda document: calls.append("construct") or document,
            validate_semantics=lambda _artifact: calls.append("semantic"),
        )
    )
    path = tmp_path / "payload.bin"
    path.write_bytes(b"fake-hdf")

    result = check_validity(
        "fake_payload",
        path,
        adapter_registry=adapters,
        reader_registry=readers,
    )

    assert result.outcome is ValidityOutcome.VALID
    assert result.versions == ArtifactVersionSet(payload=3)
    assert calls == ["detect", "parse", "construct", "semantic"]


def test_registered_bundle_reader_can_own_a_directory(tmp_path: Path) -> None:
    class FakeBundleReader(RepresentationReader):
        representation = ArtifactRepresentation.BUNDLE

        def detect(self, path: Path) -> bool:
            return path.is_dir()

        def parse(
            self,
            path: Path,
            expected_artifact_type: str,
        ) -> ParsedRepresentation:
            return ParsedRepresentation(
                ArtifactIdentity(
                    expected_artifact_type,
                    self.representation,
                    ArtifactVersionSet(bundle=2),
                ),
                path,
            )

    readers = RepresentationReaderRegistry()
    readers.register(FakeBundleReader())
    readers.freeze()
    adapters = _register_exact(
        SchemaAdapter(
            artifact_type="fake_bundle",
            representation=ArtifactRepresentation.BUNDLE,
            versions=ArtifactVersionSet(bundle=2),
            construct=lambda path: path,
            validate_semantics=lambda path: None,
        )
    )
    path = tmp_path / "bundle"
    path.mkdir()

    result = check_validity(
        "fake_bundle",
        path,
        adapter_registry=adapters,
        reader_registry=readers,
    )

    assert result.outcome is ValidityOutcome.VALID
    assert result.representation is ArtifactRepresentation.BUNDLE


def test_new_artifact_type_is_owned_by_registry_not_global_version_table(
    tmp_path: Path,
) -> None:
    registry = _register_exact(_json_adapter("new_artifact", 7))
    path = tmp_path / "new.json"
    path.write_text(
        json.dumps({"artifact_type": "new_artifact", "schema_version": 7}),
        encoding="utf-8",
    )

    result = check_validity("new_artifact", path, adapter_registry=registry)

    assert result.outcome is ValidityOutcome.VALID
    assert result.manifest_schema_version == 7


def test_camera_profile_v1_preserves_historical_gain_default(tmp_path: Path) -> None:
    data = per_band_camera_profile_dict()
    data["artifact_type"] = "camera_profile"
    data["schema_version"] = 1
    del data["camera"]["per_wavelength"]["450"]["gain_db"]
    path = tmp_path / "camera-v1.json"
    path.write_text(json.dumps(data), encoding="utf-8")

    result = check_validity("camera_profile", path)

    assert result.outcome is ValidityOutcome.VALID
    assert result.schema_version == 1


def test_pupil_profile_v1_preserves_historical_xywh_window(tmp_path: Path) -> None:
    data = pupil_profile_dict()
    data["artifact_type"] = "pupil_profile"
    data["schema_version"] = 1
    data["aperture_window"] = [10, 20, 30, 40]
    path = tmp_path / "pupil-v1.json"
    path.write_text(json.dumps(data), encoding="utf-8")

    result = check_validity("pupil_profile", path)

    assert result.outcome is ValidityOutcome.VALID


@pytest.mark.parametrize("artifact_type", ["camera_profile", "pupil_profile"])
def test_v1_profile_preserves_historical_unknown_field_policy(
    tmp_path: Path,
    artifact_type: str,
) -> None:
    data = (
        per_band_camera_profile_dict()
        if artifact_type == "camera_profile"
        else pupil_profile_dict()
    )
    data["artifact_type"] = artifact_type
    data["schema_version"] = 1
    data["historical_operator_extension"] = {"note": "preserved compatibility"}
    path = tmp_path / f"{artifact_type}.json"
    path.write_text(json.dumps(data), encoding="utf-8")

    result = check_validity(artifact_type, path)

    assert result.outcome is ValidityOutcome.VALID


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("subpixel_axis", 2),
        ("lcd_display_index", -1),
        ("lcd_physical_radius", -2),
        ("lcd_physical_center", ["not-a-number", 1]),
    ],
)
def test_profile_data_rejection_is_invalid_not_validator_failure(
    tmp_path: Path,
    field: str,
    value,
) -> None:
    data = pupil_profile_dict()
    data["artifact_type"] = "pupil_profile"
    data["schema_version"] = 1
    data[field] = value
    path = tmp_path / "invalid-pupil.json"
    path.write_text(json.dumps(data), encoding="utf-8")

    result = check_validity("pupil_profile", path)

    assert result.outcome is ValidityOutcome.INVALID
    assert result.reason_codes == ("schema.semantic.invalid",)


def test_unexpected_adapter_exception_is_validator_failed(tmp_path: Path) -> None:
    def fail(_artifact) -> None:
        raise RuntimeError("programming bug")

    registry = _register_exact(
        _json_adapter("broken_adapter", validate_semantics=fail)
    )
    path = tmp_path / "broken.json"
    path.write_text(
        json.dumps({"artifact_type": "broken_adapter", "schema_version": 1}),
        encoding="utf-8",
    )

    result = check_validity("broken_adapter", path, adapter_registry=registry)

    assert result.outcome is ValidityOutcome.VALIDATOR_FAILED
    assert result.reason_codes == ("validator.internal_failure",)


@pytest.mark.parametrize("token", ["NaN", "Infinity", "-Infinity"])
def test_json_reader_rejects_nonstandard_numeric_constants(
    tmp_path: Path,
    token: str,
) -> None:
    path = tmp_path / "nonfinite.json"
    path.write_text(
        '{"artifact_type":"pupil_profile","schema_version":1,'
        f'"extra":{{"value":{token}}}}}',
        encoding="utf-8",
    )

    result = check_validity("pupil_profile", path)

    assert result.outcome is ValidityOutcome.UNREADABLE
    assert result.reason_codes == ("representation.json.nonstandard_constant",)


def test_json_reader_classifies_float_overflow_as_reader_limit(tmp_path: Path) -> None:
    path = tmp_path / "overflow.json"
    path.write_text(
        '{"artifact_type":"pupil_profile","schema_version":1,'
        '"extra":{"value":1e999}}',
        encoding="utf-8",
    )

    result = check_validity("pupil_profile", path)

    assert result.outcome is ValidityOutcome.UNSUPPORTED
    assert result.reason_codes == ("reader_limit.json_float_range",)


def test_json_reader_rejects_duplicate_keys(tmp_path: Path) -> None:
    path = tmp_path / "duplicate.json"
    path.write_text(
        '{"artifact_type":"pupil_profile","schema_version":1,'
        '"schema_version":1}',
        encoding="utf-8",
    )

    result = check_validity("pupil_profile", path)

    assert result.outcome is ValidityOutcome.INVALID
    assert result.reason_codes == ("representation.json.duplicate_key",)


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


def test_hdf5_user_block_signature_is_detected(tmp_path: Path) -> None:
    path = tmp_path / "user-block.h5"
    path.write_bytes((b"\0" * 512) + b"\x89HDF\r\n\x1a\nplaceholder")

    result = check_validity("pupil_profile", path)

    assert result.outcome is ValidityOutcome.UNSUPPORTED
    assert result.representation is ArtifactRepresentation.HDF5
    assert result.reason_codes == ("representation.adapter_not_registered",)


def test_yaml_import_is_not_an_artifact_representation(tmp_path: Path) -> None:
    assert "YAML_IMPORT" not in ArtifactRepresentation.__members__
    path = tmp_path / "profile.yaml"
    path.write_text("artifact_type: pupil_profile\nschema_version: 1\n", encoding="utf-8")

    result = check_validity("pupil_profile", path)

    assert result.outcome is ValidityOutcome.UNREADABLE
    assert result.representation is ArtifactRepresentation.JSON


def test_registration_requires_complete_semantic_validation() -> None:
    registry = SchemaAdapterRegistry()
    adapter = SchemaAdapter(
        artifact_type="missing_semantics",
        representation=ArtifactRepresentation.JSON,
        versions=ArtifactVersionSet(manifest=1),
        allowed_fields=frozenset({"artifact_type", "schema_version"}),
        required_fields=frozenset({"artifact_type", "schema_version"}),
        construct=dict,
    )

    with pytest.raises(ValueError, match="requires validate_semantics"):
        register_schema_adapter(adapter, registry=registry)


def test_legacy_bridge_has_explicit_distinct_semantic_ownership() -> None:
    bridge = LegacyCompatibilityBridge(
        artifact_type="legacy_example",
        representation=ArtifactRepresentation.JSON,
        versions=ArtifactVersionSet(manifest=1),
        allowed_fields=frozenset({"artifact_type", "schema_version"}),
        required_fields=frozenset({"artifact_type", "schema_version"}),
        load_and_validate=dict,
    )

    assert bridge.semantic_mode is SemanticValidationMode.LEGACY_LOADER
    assert not hasattr(bridge, "validate_semantics")


def test_duplicate_contract_cannot_replace_existing() -> None:
    registry = SchemaAdapterRegistry()
    adapter = _json_adapter("duplicate")
    register_schema_adapter(adapter, registry=registry)

    with pytest.raises(ValueError, match="already registered"):
        register_schema_adapter(adapter, registry=registry)
    assert "replace" not in inspect.signature(register_schema_adapter).parameters


def test_frozen_registry_is_read_only() -> None:
    registry = _register_exact(_json_adapter("frozen"))

    with pytest.raises(RuntimeError, match="frozen"):
        register_schema_adapter(_json_adapter("another"), registry=registry)
    with pytest.raises(TypeError):
        registry.adapters[_json_adapter("frozen").key] = _json_adapter("changed")


def test_builtin_catalog_detects_version_window_gap() -> None:
    with pytest.raises(RuntimeError, match="adapter gap"):
        validate_registry_completeness(SchemaAdapterRegistry())


@pytest.mark.parametrize("axis", ["manifest", "payload", "bundle"])
@pytest.mark.parametrize("version", [True, False, 0, -1, 1.5, "1"])
def test_version_set_rejects_invalid_versions(axis: str, version) -> None:
    with pytest.raises(ValueError, match="positive integer"):
        ArtifactVersionSet(**{axis: version})


def test_validity_result_enforces_closed_state_invariants() -> None:
    valid = ValidityResult("example", ValidityOutcome.VALID)
    invalid = ValidityResult(
        "example",
        ValidityOutcome.INVALID,
        reason_codes=("schema.invalid",),
    )

    assert valid.ok
    assert not invalid.ok
    with pytest.raises(ValueError, match="VALID result"):
        ValidityResult(
            "example",
            ValidityOutcome.VALID,
            errors=("failure",),
        )
    with pytest.raises(ValueError, match="reason code"):
        ValidityResult("example", ValidityOutcome.INVALID)
    with pytest.raises(ValueError, match="cannot declare schema versions"):
        ValidityResult(
            "example",
            ValidityOutcome.LEGACY_UNVERSIONED,
            versions=ArtifactVersionSet(manifest=1),
            reason_codes=("schema.version.missing",),
        )


def test_result_sanitizes_paths_from_adapter_errors(tmp_path: Path) -> None:
    def reject(_artifact) -> None:
        raise SemanticValidationError(
            "semantic.invalid",
            f"invalid artifact at {tmp_path / 'private.json'}",
        )

    registry = _register_exact(
        _json_adapter("path_message", validate_semantics=reject)
    )
    path = tmp_path / "artifact.json"
    path.write_text(
        json.dumps({"artifact_type": "path_message", "schema_version": 1}),
        encoding="utf-8",
    )

    result = check_validity("path_message", path, adapter_registry=registry)

    assert result.outcome is ValidityOutcome.INVALID
    assert str(tmp_path) not in "\n".join(result.errors)


@pytest.mark.parametrize(
    ("registered", "requested", "reason_code"),
    [(2, 1, "schema.version.older"), (1, 2, "schema.version.newer")],
)
def test_versions_outside_registered_window_are_unsupported(
    tmp_path: Path,
    registered: int,
    requested: int,
    reason_code: str,
) -> None:
    registry = _register_exact(_json_adapter("window", registered))
    path = tmp_path / "window.json"
    path.write_text(
        json.dumps({"artifact_type": "window", "schema_version": requested}),
        encoding="utf-8",
    )

    result = check_validity("window", path, adapter_registry=registry)

    assert result.outcome is ValidityOutcome.UNSUPPORTED
    assert result.reason_codes == (reason_code,)


def test_result_never_retains_location(tmp_path: Path) -> None:
    result = check_validity("pupil_profile", tmp_path / "missing.json")

    assert "path" not in result.__dataclass_fields__
    assert str(tmp_path) not in "\n".join(result.errors)
