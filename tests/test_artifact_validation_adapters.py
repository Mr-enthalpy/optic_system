from __future__ import annotations

import inspect
import io
import json
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path

import pytest
import tasks.artifacts.validation as validation_module

from tasks.artifacts.adapter_catalog import (
    SchemaAdapterProvider,
    validate_registry_completeness,
)
from tasks.artifacts.reader_catalog import (
    BUILTIN_READER_PROVIDERS,
    RepresentationReaderProvider,
    build_representation_reader_registry,
)
from tasks.artifacts.validation import (
    AdditionalFieldsPolicy,
    ArtifactIdentity,
    ArtifactRepresentation,
    ArtifactVersionSet,
    ConstructionValidationError,
    IdentityValidationError,
    LegacyCompatibilityBridge,
    OpenedRepresentation,
    ParsedRepresentation,
    ProbeOutcome,
    ProbeFailureScope,
    ProbeResult,
    ReaderLimitError,
    RepresentationReader,
    RepresentationReaderRegistry,
    RepresentationUnreadableError,
    SchemaAdapter,
    SchemaAdapterRegistry,
    SemanticValidationError,
    SemanticValidationMode,
    SerializedSchemaError,
    ValidatorFailureError,
    ValidityOutcome,
    ValidityResult,
    check_validity,
    get_schema_adapter,
    register_representation_reader,
    register_schema_adapter,
)
from tasks.profiles.camera_profile import CameraProfile
from tasks.profiles.pupil_profile import PupilProfile
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
        ArtifactVersionSet(manifest=1),
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
        adapter.parse_and_validate({"artifact_type": "different", "schema_version": 1})

    assert exc_info.value.reason_code == "schema.identity.mismatch"


def test_registered_non_json_reader_and_adapter_execute_without_core_branch(
    tmp_path: Path,
) -> None:
    calls: list[str] = []

    class FakeHDFReader(RepresentationReader):
        representation = ArtifactRepresentation.HDF5

        @contextmanager
        def open(self, path: Path):
            calls.append("open")
            with path.open("rb") as stream:
                header = stream.read().decode("ascii")
                stream.seek(0)
                calls.append("probe")

                def parse() -> ParsedRepresentation:
                    calls.append("parse")
                    artifact_type, raw_version = header.split("|")
                    return ParsedRepresentation(
                        ArtifactIdentity(
                            artifact_type,
                            self.representation,
                            ArtifactVersionSet(payload=int(raw_version)),
                        ),
                        stream,
                    )

                yield OpenedRepresentation(
                    self.representation,
                    ProbeResult.match(),
                    parse,
                )
            calls.append("close")

    readers = RepresentationReaderRegistry()
    register_representation_reader(FakeHDFReader(), registry=readers)
    readers.freeze()
    adapters = _register_exact(
        SchemaAdapter(
            artifact_type="fake_payload",
            representation=ArtifactRepresentation.HDF5,
            versions=ArtifactVersionSet(payload=3),
            construct=lambda stream: calls.append("construct") or stream,
            validate_semantics=lambda stream: calls.append("semantic")
            or (not stream.closed),
        )
    )
    path = tmp_path / "payload.bin"
    path.write_bytes(b"fake_payload|3")

    result = check_validity(
        "fake_payload",
        path,
        adapter_registry=adapters,
        reader_registry=readers,
    )

    assert result.outcome is ValidityOutcome.VALID
    assert result.versions == ArtifactVersionSet(payload=3)
    assert calls == ["open", "probe", "parse", "construct", "semantic", "close"]


def test_registered_bundle_reader_can_own_a_directory(tmp_path: Path) -> None:
    class FakeBundleReader(RepresentationReader):
        representation = ArtifactRepresentation.BUNDLE

        @contextmanager
        def open(self, path: Path):
            identity = (path / "identity.txt").read_text(encoding="utf-8")
            artifact_type, raw_version = identity.split("|")
            yield OpenedRepresentation(
                self.representation,
                ProbeResult.match(),
                lambda: ParsedRepresentation(
                    ArtifactIdentity(
                        artifact_type,
                        self.representation,
                        ArtifactVersionSet(bundle=int(raw_version)),
                    ),
                    path,
                ),
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
    (path / "identity.txt").write_text("fake_bundle|2", encoding="utf-8")

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
    assert result.reason_codes in {
        ("schema.construction.profile_rejected",),
        ("schema.field.type_invalid",),
    }


def test_unexpected_adapter_exception_is_validator_failed(tmp_path: Path) -> None:
    def fail(_artifact) -> None:
        raise RuntimeError("programming bug")

    registry = _register_exact(_json_adapter("broken_adapter", validate_semantics=fail))
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


def test_json_reader_preserves_large_finite_decimal_for_schema(
    tmp_path: Path,
) -> None:
    observed: list[Decimal] = []
    registry = _register_exact(
        SchemaAdapter(
            artifact_type="decimal_example",
            representation=ArtifactRepresentation.JSON,
            versions=ArtifactVersionSet(manifest=1),
            allowed_fields=frozenset({"artifact_type", "schema_version", "value"}),
            required_fields=frozenset({"artifact_type", "schema_version", "value"}),
            construct=lambda mapping: mapping,
            validate_semantics=lambda mapping: observed.append(mapping["value"]),
        )
    )
    path = tmp_path / "decimal.json"
    path.write_text(
        '{"artifact_type":"decimal_example","schema_version":1,' '"value":1e999}',
        encoding="utf-8",
    )

    result = check_validity(
        "decimal_example",
        path,
        adapter_registry=registry,
    )

    assert result.outcome is ValidityOutcome.VALID
    assert observed == [Decimal("1e999")]


def test_json_reader_rejects_duplicate_keys(tmp_path: Path) -> None:
    path = tmp_path / "duplicate.json"
    path.write_text(
        '{"artifact_type":"pupil_profile","schema_version":1,' '"schema_version":1}',
        encoding="utf-8",
    )

    result = check_validity("pupil_profile", path)

    assert result.outcome is ValidityOutcome.UNREADABLE
    assert result.reason_codes == ("representation.json.duplicate_key",)


def test_reader_limit_is_not_reported_as_schema_invalid(tmp_path: Path) -> None:
    path = tmp_path / "huge.json"
    path.write_text(
        '{"artifact_type":"pupil_profile","schema_version":' + ("9" * 5000) + "}",
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
    assert result.reason_codes == ("representation.reader_not_registered",)


def test_yaml_import_is_not_an_artifact_representation(tmp_path: Path) -> None:
    assert "YAML_IMPORT" not in ArtifactRepresentation.__members__
    path = tmp_path / "profile.yaml"
    path.write_text(
        "artifact_type: pupil_profile\nschema_version: 1\n",
        encoding="utf-8",
    )

    result = check_validity("pupil_profile", path)

    assert result.outcome is ValidityOutcome.UNSUPPORTED
    assert result.representation is None
    assert result.reason_codes == ("representation.reader_not_registered",)


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


def test_required_adapter_authority_detects_omitted_provider() -> None:
    with pytest.raises(RuntimeError, match="required readable identities"):
        validate_registry_completeness(SchemaAdapterRegistry(), ())


def test_required_reader_authority_detects_omitted_json_reader() -> None:
    hdf_only = tuple(
        provider
        for provider in BUILTIN_READER_PROVIDERS
        if provider.representation is ArtifactRepresentation.HDF5
    )

    with pytest.raises(RuntimeError, match="required identifying"):
        build_representation_reader_registry(hdf_only)


def test_registry_composition_requires_reader_for_adapter_representation() -> None:
    adapters = _register_exact(
        SchemaAdapter(
            artifact_type="reader_gap",
            representation=ArtifactRepresentation.HDF5,
            versions=ArtifactVersionSet(payload=1),
            construct=lambda value: value,
            validate_semantics=lambda value: None,
        )
    )
    readers = RepresentationReaderRegistry()
    readers.register(validation_module._JSONRepresentationReader())
    readers.freeze()

    with pytest.raises(RuntimeError, match="lack identifying"):
        validation_module._validate_registry_composition(adapters, readers)


@pytest.mark.parametrize("axis", ["manifest", "payload", "bundle"])
@pytest.mark.parametrize("version", [True, False, 0, -1, 1.5, "1"])
def test_version_set_rejects_invalid_versions(axis: str, version) -> None:
    with pytest.raises(ValueError, match="positive integer"):
        ArtifactVersionSet(**{axis: version})


def test_validity_result_enforces_closed_state_invariants() -> None:
    identity = ArtifactIdentity(
        "example",
        ArtifactRepresentation.JSON,
        ArtifactVersionSet(manifest=1),
    )
    valid = ValidityResult(
        "example",
        ValidityOutcome.VALID,
        identified_identity=identity,
    )
    invalid = ValidityResult(
        "example",
        ValidityOutcome.INVALID,
        reason_codes=("schema.invalid",),
        errors=("invalid artifact",),
    )

    assert valid.ok
    assert not invalid.ok
    with pytest.raises(ValueError, match="VALID result"):
        ValidityResult(
            "example",
            ValidityOutcome.VALID,
            identified_identity=identity,
            errors=("failure",),
        )
    with pytest.raises(ValueError, match="reason code"):
        ValidityResult("example", ValidityOutcome.INVALID)
    with pytest.raises(ValueError, match="diagnostic message"):
        ValidityResult(
            "example",
            ValidityOutcome.INVALID,
            reason_codes=("schema.invalid",),
        )
    with pytest.raises(ValueError, match="cannot have complete identity"):
        ValidityResult(
            "example",
            ValidityOutcome.LEGACY_UNVERSIONED,
            identified_identity=identity,
            reason_codes=("schema.version.missing",),
            errors=("missing version",),
        )


def test_result_sanitizes_paths_from_adapter_errors(tmp_path: Path) -> None:
    def reject(_artifact) -> None:
        raise SemanticValidationError(
            "semantic.invalid",
            f"invalid artifact at {tmp_path / 'private.json'}",
        )

    registry = _register_exact(_json_adapter("path_message", validate_semantics=reject))
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
    [
        (2, 1, "schema.manifest_version.older"),
        (1, 2, "schema.manifest_version.newer"),
    ],
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


def test_reader_identity_is_independent_of_expected_artifact_type(
    tmp_path: Path,
) -> None:
    class IdentityReader(RepresentationReader):
        representation = ArtifactRepresentation.HDF5

        @contextmanager
        def open(self, path: Path):
            artifact_type = path.read_text(encoding="utf-8")
            yield OpenedRepresentation(
                self.representation,
                ProbeResult.match(),
                lambda: ParsedRepresentation(
                    ArtifactIdentity(
                        artifact_type,
                        self.representation,
                        ArtifactVersionSet(payload=1),
                    ),
                    path,
                ),
            )

    readers = RepresentationReaderRegistry()
    readers.register(IdentityReader())
    readers.freeze()
    adapters = _register_exact(
        SchemaAdapter(
            artifact_type="requested_type",
            representation=ArtifactRepresentation.HDF5,
            versions=ArtifactVersionSet(payload=1),
            construct=lambda path: path,
            validate_semantics=lambda path: None,
        )
    )
    path = tmp_path / "identity.payload"
    path.write_text("actual_type", encoding="utf-8")

    result = check_validity(
        "requested_type",
        path,
        adapter_registry=adapters,
        reader_registry=readers,
    )

    assert result.outcome is ValidityOutcome.INVALID
    assert result.reason_codes == ("schema.artifact_type.mismatch",)
    assert (
        "expected_artifact_type"
        not in inspect.signature(RepresentationReader.open).parameters
    )


def test_probe_unreadable_is_not_treated_as_no_match(tmp_path: Path) -> None:
    class UnreadableReader(RepresentationReader):
        representation = ArtifactRepresentation.HDF5

        @contextmanager
        def open(self, path: Path):
            yield OpenedRepresentation(
                self.representation,
                ProbeResult(
                    ProbeOutcome.UNREADABLE,
                    "representation.hdf5.probe_unreadable",
                    "probe failed",
                    ProbeFailureScope.LOCATION_GLOBAL,
                ),
                lambda: pytest.fail("unreadable probe must not parse"),
            )

    readers = RepresentationReaderRegistry()
    readers.register(UnreadableReader())
    readers.freeze()
    path = tmp_path / "payload"
    path.write_bytes(b"payload")

    result = check_validity(
        "raw_capture",
        path,
        adapter_registry=SchemaAdapterRegistry(),
        reader_registry=readers,
    )

    assert result.outcome is ValidityOutcome.UNREADABLE
    assert result.reason_codes == ("representation.hdf5.probe_unreadable",)


def test_probe_limit_is_not_treated_as_no_match(tmp_path: Path) -> None:
    class LimitedReader(RepresentationReader):
        representation = ArtifactRepresentation.HDF5

        @contextmanager
        def open(self, path: Path):
            yield OpenedRepresentation(
                self.representation,
                ProbeResult(
                    ProbeOutcome.UNSUPPORTED_LIMIT,
                    "reader_limit.hdf5_probe",
                    "probe limit reached",
                    ProbeFailureScope.READER_LOCAL,
                ),
                lambda: pytest.fail("limited probe must not parse"),
            )

    readers = RepresentationReaderRegistry()
    readers.register(LimitedReader())
    readers.freeze()
    path = tmp_path / "payload"
    path.write_bytes(b"payload")

    result = check_validity(
        "raw_capture",
        path,
        adapter_registry=SchemaAdapterRegistry(),
        reader_registry=readers,
    )

    assert result.outcome is ValidityOutcome.UNSUPPORTED
    assert result.reason_codes == ("reader_limit.hdf5_probe",)


def test_probe_ambiguity_is_explicit(tmp_path: Path) -> None:
    class HDFMatch(RepresentationReader):
        representation = ArtifactRepresentation.HDF5

        @contextmanager
        def open(self, path: Path):
            yield OpenedRepresentation(
                self.representation,
                ProbeResult.match(),
                lambda: pytest.fail("ambiguous probe must not parse"),
            )

    class JSONMatch(RepresentationReader):
        representation = ArtifactRepresentation.JSON

        @contextmanager
        def open(self, path: Path):
            yield OpenedRepresentation(
                self.representation,
                ProbeResult.match(),
                lambda: pytest.fail("ambiguous probe must not parse"),
            )

    readers = RepresentationReaderRegistry()
    readers.register(HDFMatch())
    readers.register(JSONMatch())
    readers.freeze()
    path = tmp_path / "ambiguous"
    path.write_bytes(b"payload")

    result = check_validity(
        "raw_capture",
        path,
        adapter_registry=SchemaAdapterRegistry(),
        reader_registry=readers,
    )

    assert result.outcome is ValidityOutcome.UNREADABLE
    assert result.reason_codes == ("representation.probe_ambiguous",)


def test_arbitrary_binary_is_not_claimed_by_json_reader(tmp_path: Path) -> None:
    path = tmp_path / "unknown.bin"
    path.write_bytes(b"\x00\x01not-json")

    result = check_validity("pupil_profile", path)

    assert result.outcome is ValidityOutcome.UNSUPPORTED
    assert result.representation is None
    assert result.reason_codes == ("representation.reader_not_registered",)


def test_hdf5_probe_has_no_artificial_user_block_cap(tmp_path: Path) -> None:
    path = tmp_path / "large-user-block.h5"
    with path.open("wb") as stream:
        stream.seek(128 * 1024 * 1024)
        stream.write(b"\x89HDF\r\n\x1a\nplaceholder")

    result = check_validity("pupil_profile", path)

    assert result.outcome is ValidityOutcome.UNSUPPORTED
    assert result.representation is ArtifactRepresentation.HDF5


def test_known_artifact_without_capability_is_not_reported_unknown(
    tmp_path: Path,
) -> None:
    registry = SchemaAdapterRegistry()
    registry.freeze()
    path = tmp_path / "known.json"
    path.write_text(
        json.dumps({"artifact_type": "full_frame_psf_survey", "schema_version": 1}),
        encoding="utf-8",
    )

    result = check_validity(
        "full_frame_psf_survey",
        path,
        adapter_registry=registry,
    )

    assert result.outcome is ValidityOutcome.UNSUPPORTED
    assert result.reason_codes == ("schema.adapter_not_registered",)


def test_exact_adapter_cannot_ignore_additional_fields() -> None:
    registry = SchemaAdapterRegistry()
    adapter = SchemaAdapter(
        artifact_type="open_exact",
        representation=ArtifactRepresentation.JSON,
        versions=ArtifactVersionSet(manifest=1),
        allowed_fields=frozenset({"artifact_type", "schema_version"}),
        required_fields=frozenset({"artifact_type", "schema_version"}),
        construct=dict,
        validate_semantics=lambda artifact: None,
        additional_fields_policy=AdditionalFieldsPolicy.IGNORE,
    )

    with pytest.raises(ValueError, match="must forbid"):
        register_schema_adapter(adapter, registry=registry)


@pytest.mark.parametrize(
    ("stage", "failure", "expected_outcome", "expected_reason"),
    [
        (
            "serialized",
            ValueError("bug"),
            ValidityOutcome.VALIDATOR_FAILED,
            "validator.internal_failure",
        ),
        (
            "serialized",
            SemanticValidationError("semantic.invalid", "wrong stage"),
            ValidityOutcome.VALIDATOR_FAILED,
            "validator.stage_contract_violation",
        ),
        (
            "construction",
            ConstructionValidationError(
                "schema.construction.invalid",
                "bad data",
            ),
            ValidityOutcome.INVALID,
            "schema.construction.invalid",
        ),
        (
            "construction",
            TypeError("bug"),
            ValidityOutcome.VALIDATOR_FAILED,
            "validator.internal_failure",
        ),
        (
            "semantic",
            SemanticValidationError("semantic.invalid", "bad data"),
            ValidityOutcome.INVALID,
            "semantic.invalid",
        ),
        (
            "semantic",
            ValueError("bug"),
            ValidityOutcome.VALIDATOR_FAILED,
            "validator.internal_failure",
        ),
    ],
)
def test_adapter_stage_errors_have_closed_classification(
    tmp_path: Path,
    stage: str,
    failure: Exception,
    expected_outcome: ValidityOutcome,
    expected_reason: str,
) -> None:
    def raise_failure(_value):
        raise failure

    adapter = SchemaAdapter(
        artifact_type="stage_failure",
        representation=ArtifactRepresentation.JSON,
        versions=ArtifactVersionSet(manifest=1),
        allowed_fields=frozenset({"artifact_type", "schema_version"}),
        required_fields=frozenset({"artifact_type", "schema_version"}),
        validate_serialized=raise_failure if stage == "serialized" else None,
        construct=raise_failure if stage == "construction" else dict,
        validate_semantics=raise_failure if stage == "semantic" else lambda value: None,
    )
    registry = _register_exact(adapter)
    path = tmp_path / "stage.json"
    path.write_text(
        json.dumps({"artifact_type": "stage_failure", "schema_version": 1}),
        encoding="utf-8",
    )

    result = check_validity("stage_failure", path, adapter_registry=registry)

    assert result.outcome is expected_outcome
    assert result.reason_codes == (expected_reason,)


def test_legacy_translator_does_not_blanket_classify_value_error(
    tmp_path: Path,
) -> None:
    bridge = LegacyCompatibilityBridge(
        artifact_type="legacy_bug",
        representation=ArtifactRepresentation.JSON,
        versions=ArtifactVersionSet(manifest=1),
        allowed_fields=frozenset({"artifact_type", "schema_version"}),
        required_fields=frozenset({"artifact_type", "schema_version"}),
        load_and_validate=lambda mapping: (_ for _ in ()).throw(ValueError("bug")),
        translate_error=lambda exc: None,
    )
    registry = SchemaAdapterRegistry()
    registry.register(bridge)
    registry.freeze()
    path = tmp_path / "legacy.json"
    path.write_text(
        json.dumps({"artifact_type": "legacy_bug", "schema_version": 1}),
        encoding="utf-8",
    )

    result = check_validity("legacy_bug", path, adapter_registry=registry)

    assert result.outcome is ValidityOutcome.VALIDATOR_FAILED
    assert result.reason_codes == ("validator.internal_failure",)


def test_multi_axis_identity_has_no_lossy_scalar_schema_version() -> None:
    versions = ArtifactVersionSet(manifest=2, payload=3, bundle=1)
    adapter = SchemaAdapter(
        artifact_type="multi_axis",
        representation=ArtifactRepresentation.BUNDLE,
        versions=versions,
        construct=lambda value: value,
        validate_semantics=lambda value: None,
    )
    result = ValidityResult(
        "multi_axis",
        ValidityOutcome.VALID,
        identified_identity=ArtifactIdentity(
            "multi_axis",
            ArtifactRepresentation.BUNDLE,
            versions,
        ),
    )

    with pytest.raises(ValueError, match="multi-axis"):
        _ = adapter.schema_version
    with pytest.raises(ValueError, match="multi-axis"):
        _ = result.schema_version
    with pytest.raises(TypeError, match="ArtifactVersionSet"):
        get_schema_adapter("camera_profile", ArtifactRepresentation.JSON, 1)


@pytest.mark.parametrize(
    ("representation", "versions"),
    [
        (ArtifactRepresentation.JSON, ArtifactVersionSet(payload=1)),
        (ArtifactRepresentation.HDF5, ArtifactVersionSet(manifest=1)),
        (ArtifactRepresentation.BUNDLE, ArtifactVersionSet(payload=1)),
    ],
)
def test_identity_rejects_representation_version_shape_mismatch(
    representation: ArtifactRepresentation,
    versions: ArtifactVersionSet,
) -> None:
    with pytest.raises(ValueError, match="version axes"):
        ArtifactIdentity("shape_mismatch", representation, versions)


def test_json_decimal_underflow_and_precision_are_preserved(tmp_path: Path) -> None:
    observed: list[tuple[Decimal, Decimal]] = []

    def validate(mapping) -> None:
        observed.append((mapping["tiny"], mapping["precise"]))

    adapter = SchemaAdapter(
        artifact_type="decimal_precision",
        representation=ArtifactRepresentation.JSON,
        versions=ArtifactVersionSet(manifest=1),
        allowed_fields=frozenset(
            {"artifact_type", "schema_version", "tiny", "precise"}
        ),
        required_fields=frozenset(
            {"artifact_type", "schema_version", "tiny", "precise"}
        ),
        construct=lambda mapping: mapping,
        validate_semantics=validate,
    )
    registry = _register_exact(adapter)
    path = tmp_path / "precision.json"
    path.write_text(
        '{"artifact_type":"decimal_precision","schema_version":1,'
        '"tiny":1e-999,"precise":0.12345678901234567890123456789}',
        encoding="utf-8",
    )

    result = check_validity(
        "decimal_precision",
        path,
        adapter_registry=registry,
    )

    assert result.outcome is ValidityOutcome.VALID
    assert observed == [
        (
            Decimal("1e-999"),
            Decimal("0.12345678901234567890123456789"),
        )
    ]
    assert observed[0][0] != 0


@pytest.mark.parametrize(
    "reason_code",
    ["invalid", "Invalid.code", "invalid-code.value", "C:/private/path"],
)
def test_reason_codes_enforce_stable_machine_grammar(reason_code: str) -> None:
    with pytest.raises(ValueError, match="reason code"):
        ValidityResult(
            "example",
            ValidityOutcome.INVALID,
            reason_codes=(reason_code,),
        )


def test_builtin_catalog_is_eagerly_bootstrapped_and_frozen() -> None:
    assert validation_module._BUILTIN_SCHEMA_ADAPTERS.frozen


def test_public_provider_api_exports_all_stage_error_types() -> None:
    import tasks.artifacts as artifacts

    for name in (
        "ArtifactValidationError",
        "RepresentationParseError",
        "IdentityValidationError",
        "SerializedSchemaError",
        "ConstructionValidationError",
        "SemanticValidationError",
        "RepresentationUnreadableError",
        "ReaderLimitError",
        "UnsupportedRepresentationError",
    ):
        assert name in artifacts.__all__
        assert getattr(artifacts, name) is getattr(validation_module, name)


def test_match_wins_over_unrelated_reader_limit(tmp_path: Path) -> None:
    class MatchReader(RepresentationReader):
        representation = ArtifactRepresentation.HDF5

        @contextmanager
        def open(self, path: Path):
            yield OpenedRepresentation(
                self.representation,
                ProbeResult.match(),
                lambda: ParsedRepresentation(
                    ArtifactIdentity(
                        "monotonic",
                        self.representation,
                        ArtifactVersionSet(payload=1),
                    ),
                    path,
                ),
            )

    class LimitedReader(RepresentationReader):
        representation = ArtifactRepresentation.BUNDLE

        @contextmanager
        def open(self, path: Path):
            yield OpenedRepresentation(
                self.representation,
                ProbeResult(
                    ProbeOutcome.UNSUPPORTED_LIMIT,
                    "reader_limit.bundle_probe",
                    "unrelated bundle limit",
                    ProbeFailureScope.READER_LOCAL,
                ),
                lambda: pytest.fail("unmatched reader must not parse"),
            )

    readers = RepresentationReaderRegistry()
    readers.register(LimitedReader())
    readers.register(MatchReader())
    readers.freeze()
    adapters = _register_exact(
        SchemaAdapter(
            artifact_type="monotonic",
            representation=ArtifactRepresentation.HDF5,
            versions=ArtifactVersionSet(payload=1),
            construct=lambda path: path,
            validate_semantics=lambda path: None,
        )
    )
    path = tmp_path / "payload"
    path.write_bytes(b"payload")

    result = check_validity(
        "monotonic",
        path,
        adapter_registry=adapters,
        reader_registry=readers,
    )

    assert result.outcome is ValidityOutcome.VALID


def test_reader_provider_can_extend_builtin_hdf5_slot(tmp_path: Path) -> None:
    class FullHDFReader(RepresentationReader):
        representation = ArtifactRepresentation.HDF5

        @contextmanager
        def open(self, path: Path):
            raw = path.read_bytes()
            probe = (
                ProbeResult.match()
                if raw.startswith(b"\x89HDF")
                else ProbeResult.no_match()
            )
            yield OpenedRepresentation(
                self.representation,
                probe,
                lambda: ParsedRepresentation(
                    ArtifactIdentity(
                        "provider_hdf",
                        self.representation,
                        ArtifactVersionSet(payload=1),
                    ),
                    raw,
                ),
            )

    provider = RepresentationReaderProvider(
        "full_hdf_test",
        ArtifactRepresentation.HDF5,
        FullHDFReader,
    )
    readers = build_representation_reader_registry(
        (*BUILTIN_READER_PROVIDERS, provider)
    )
    adapters = _register_exact(
        SchemaAdapter(
            artifact_type="provider_hdf",
            representation=ArtifactRepresentation.HDF5,
            versions=ArtifactVersionSet(payload=1),
            construct=lambda raw: raw,
            validate_semantics=lambda raw: None,
        )
    )
    path = tmp_path / "provider.h5"
    path.write_bytes(b"\x89HDF\r\n\x1a\n")

    result = check_validity(
        "provider_hdf",
        path,
        adapter_registry=adapters,
        reader_registry=readers,
    )

    assert result.outcome is ValidityOutcome.VALID


def test_json_array_is_identified_then_rejected_as_invalid(tmp_path: Path) -> None:
    path = tmp_path / "array.json"
    path.write_text("[]", encoding="utf-8")

    result = check_validity("pupil_profile", path)

    assert result.outcome is ValidityOutcome.INVALID
    assert result.representation is ArtifactRepresentation.JSON
    assert result.reason_codes == ("representation.json.root_invalid",)


def test_json_allows_more_than_4096_leading_whitespace(tmp_path: Path) -> None:
    registry = _register_exact(_json_adapter("whitespace"))
    path = tmp_path / "whitespace.json"
    path.write_text(
        " " * 5000 + '{"artifact_type":"whitespace","schema_version":1}',
        encoding="utf-8",
    )

    result = check_validity("whitespace", path, adapter_registry=registry)

    assert result.outcome is ValidityOutcome.VALID


def test_json_manifest_byte_limit_is_explicit(tmp_path: Path) -> None:
    path = tmp_path / "large.json"
    path.write_text(
        " " * (validation_module._MAX_JSON_BYTES + 1) + "[]",
        encoding="utf-8",
    )

    result = check_validity("pupil_profile", path)

    assert result.outcome is ValidityOutcome.UNSUPPORTED
    assert result.representation is ArtifactRepresentation.JSON
    assert result.reason_codes == ("reader_limit.json_bytes",)


def test_reader_wrong_stage_error_is_validator_failure(tmp_path: Path) -> None:
    class BrokenReader(RepresentationReader):
        representation = ArtifactRepresentation.HDF5

        @contextmanager
        def open(self, path: Path):
            yield OpenedRepresentation(
                self.representation,
                ProbeResult.match(),
                lambda: (_ for _ in ()).throw(
                    SemanticValidationError("semantic.invalid", "wrong reader stage")
                ),
            )

    readers = RepresentationReaderRegistry()
    readers.register(BrokenReader())
    readers.freeze()
    path = tmp_path / "broken"
    path.write_bytes(b"broken")

    result = check_validity(
        "raw_capture",
        path,
        adapter_registry=SchemaAdapterRegistry(),
        reader_registry=readers,
    )

    assert result.outcome is ValidityOutcome.VALIDATOR_FAILED
    assert result.reason_codes == ("validator.reader_stage_contract_violation",)


def test_reader_open_wrong_stage_error_is_validator_failure(tmp_path: Path) -> None:
    class BrokenOpenReader(RepresentationReader):
        representation = ArtifactRepresentation.HDF5

        @contextmanager
        def open(self, path: Path):
            raise SemanticValidationError("semantic.invalid", "wrong reader open stage")
            yield  # pragma: no cover - makes this a context-manager generator.

    readers = RepresentationReaderRegistry()
    readers.register(BrokenOpenReader())
    readers.freeze()
    path = tmp_path / "broken-open"
    path.write_bytes(b"broken")

    result = check_validity(
        "raw_capture",
        path,
        adapter_registry=SchemaAdapterRegistry(),
        reader_registry=readers,
    )

    assert result.outcome is ValidityOutcome.VALIDATOR_FAILED
    assert result.reason_codes == ("validator.reader_stage_contract_violation",)


def test_validity_result_rejects_missing_or_malformed_valid_identity() -> None:
    with pytest.raises(ValueError, match="complete artifact identity"):
        ValidityResult("example", ValidityOutcome.VALID)
    with pytest.raises(ValueError, match="mutually exclusive"):
        ValidityResult(
            "example",
            ValidityOutcome.VALID,
            identified_identity=ArtifactIdentity(
                "example",
                ArtifactRepresentation.JSON,
                ArtifactVersionSet(manifest=1),
            ),
            identified_representation=ArtifactRepresentation.HDF5,
        )


def test_version_authority_mappings_are_immutable() -> None:
    from tasks.artifact_versioning import CURRENT_SCHEMA_VERSIONS

    with pytest.raises(TypeError):
        CURRENT_SCHEMA_VERSIONS["camera_profile"] = 2


def test_legacy_bridge_does_not_repeat_global_version_dispatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import tasks.artifact_versioning as versioning

    data = pupil_profile_dict()
    data["artifact_type"] = "pupil_profile"
    data["schema_version"] = 1
    path = tmp_path / "pupil.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(
        versioning,
        "read_schema_version",
        lambda *_args, **_kwargs: pytest.fail("second version dispatch"),
    )

    result = check_validity("pupil_profile", path)

    assert result.outcome is ValidityOutcome.VALID


def test_exact_json_serialized_input_is_transitively_read_only(
    tmp_path: Path,
) -> None:
    def mutate(mapping) -> None:
        mapping["nested"]["value"] = 2

    adapter = SchemaAdapter(
        artifact_type="immutable_input",
        representation=ArtifactRepresentation.JSON,
        versions=ArtifactVersionSet(manifest=1),
        allowed_fields=frozenset({"artifact_type", "schema_version", "nested"}),
        required_fields=frozenset({"artifact_type", "schema_version", "nested"}),
        validate_serialized=mutate,
        construct=dict,
        validate_semantics=lambda artifact: None,
    )
    registry = _register_exact(adapter)
    path = tmp_path / "immutable.json"
    path.write_text(
        json.dumps(
            {
                "artifact_type": "immutable_input",
                "schema_version": 1,
                "nested": {"value": 1},
            }
        ),
        encoding="utf-8",
    )

    result = check_validity("immutable_input", path, adapter_registry=registry)

    assert result.outcome is ValidityOutcome.VALIDATOR_FAILED
    assert result.reason_codes == ("validator.internal_failure",)


def test_location_global_probe_failure_precedes_unique_match(tmp_path: Path) -> None:
    class GlobalFailure(RepresentationReader):
        representation = ArtifactRepresentation.HDF5

        @contextmanager
        def open(self, path: Path):
            yield OpenedRepresentation(
                self.representation,
                ProbeResult(
                    ProbeOutcome.UNREADABLE,
                    "location.changed_during_probe",
                    "artifact location became unreadable",
                    ProbeFailureScope.LOCATION_GLOBAL,
                ),
                lambda: pytest.fail("global failure must not parse"),
            )

    class Match(RepresentationReader):
        representation = ArtifactRepresentation.JSON

        @contextmanager
        def open(self, path: Path):
            yield OpenedRepresentation(
                self.representation,
                ProbeResult.match(),
                lambda: ParsedRepresentation(
                    ArtifactIdentity(
                        "global_priority",
                        self.representation,
                        ArtifactVersionSet(manifest=1),
                    ),
                    {},
                ),
            )

    readers = RepresentationReaderRegistry()
    readers.register(Match())
    readers.register(GlobalFailure())
    readers.freeze()
    path = tmp_path / "changing"
    path.write_bytes(b"payload")

    result = check_validity(
        "global_priority",
        path,
        adapter_registry=_register_exact(_json_adapter("global_priority")),
        reader_registry=readers,
    )

    assert result.outcome is ValidityOutcome.UNREADABLE
    assert result.reason_codes == ("location.changed_during_probe",)


def test_reader_local_unreadable_precedes_capability_sentinel(tmp_path: Path) -> None:
    class LocalFailure(RepresentationReader):
        representation = ArtifactRepresentation.HDF5

        @contextmanager
        def open(self, path: Path):
            yield OpenedRepresentation(
                self.representation,
                ProbeResult(
                    ProbeOutcome.UNREADABLE,
                    "representation.hdf5.structure_invalid",
                    "HDF5 structure is damaged",
                    ProbeFailureScope.READER_LOCAL,
                ),
                lambda: pytest.fail("local failure must not parse"),
            )

    class CapabilitySentinel(RepresentationReader):
        representation = ArtifactRepresentation.HDF5

        @contextmanager
        def open(self, path: Path):
            yield OpenedRepresentation(
                self.representation,
                ProbeResult(
                    ProbeOutcome.UNSUPPORTED_CAPABILITY,
                    "representation.reader_not_registered",
                    "sentinel",
                    ProbeFailureScope.READER_LOCAL,
                ),
                lambda: pytest.fail("sentinel must not parse"),
            )

    readers = RepresentationReaderRegistry()
    readers.register(CapabilitySentinel())
    readers.register(LocalFailure())
    readers.freeze()
    path = tmp_path / "damaged.h5"
    path.write_bytes(b"payload")

    result = check_validity(
        "raw_capture",
        path,
        adapter_registry=SchemaAdapterRegistry(),
        reader_registry=readers,
    )

    assert result.outcome is ValidityOutcome.UNREADABLE
    assert result.representation is ArtifactRepresentation.HDF5
    assert result.reason_codes == ("representation.hdf5.structure_invalid",)


@pytest.mark.parametrize(
    "failure",
    [
        RepresentationUnreadableError("location.unreadable", "direct failure"),
        ReaderLimitError("reader_limit.direct", "direct failure"),
    ],
)
def test_reader_open_expected_error_cannot_bypass_probe_arbitration(
    tmp_path: Path,
    failure: Exception,
) -> None:
    class DirectFailure(RepresentationReader):
        representation = ArtifactRepresentation.HDF5

        @contextmanager
        def open(self, path: Path):
            raise failure
            yield  # pragma: no cover

    readers = RepresentationReaderRegistry()
    readers.register(DirectFailure())
    readers.freeze()
    path = tmp_path / "direct"
    path.write_bytes(b"payload")

    result = check_validity(
        "raw_capture",
        path,
        adapter_registry=SchemaAdapterRegistry(),
        reader_registry=readers,
    )

    assert result.outcome is ValidityOutcome.VALIDATOR_FAILED
    assert result.reason_codes == ("validator.reader_stage_contract_violation",)


def test_reader_cannot_raise_adapter_serialized_error(tmp_path: Path) -> None:
    class WrongOwner(RepresentationReader):
        representation = ArtifactRepresentation.HDF5

        @contextmanager
        def open(self, path: Path):
            yield OpenedRepresentation(
                self.representation,
                ProbeResult.match(),
                lambda: (_ for _ in ()).throw(
                    SerializedSchemaError("schema.field.invalid", "wrong owner")
                ),
            )

    readers = RepresentationReaderRegistry()
    readers.register(WrongOwner())
    readers.freeze()
    path = tmp_path / "wrong-owner"
    path.write_bytes(b"payload")

    result = check_validity(
        "raw_capture",
        path,
        adapter_registry=SchemaAdapterRegistry(),
        reader_registry=readers,
    )

    assert result.outcome is ValidityOutcome.VALIDATOR_FAILED
    assert result.reason_codes == ("validator.reader_stage_contract_violation",)


def test_non_json_adapter_requires_explicit_identity() -> None:
    adapter = SchemaAdapter(
        artifact_type="payload",
        representation=ArtifactRepresentation.HDF5,
        versions=ArtifactVersionSet(payload=1),
        construct=lambda value: value,
        validate_semantics=lambda value: None,
    )

    with pytest.raises(ValidatorFailureError) as exc_info:
        adapter.parse_and_validate(object())

    assert exc_info.value.reason_code == "validator.adapter_identity_required"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("lcd_display_index", "abc"),
        ("lcd_display_index", None),
        ("subpixel_axis", "abc"),
    ],
)
def test_pupil_v1_conversion_rejections_are_invalid(
    tmp_path: Path,
    field: str,
    value,
) -> None:
    data = pupil_profile_dict()
    data.update(
        {
            "artifact_type": "pupil_profile",
            "schema_version": 1,
            field: value,
        }
    )
    path = tmp_path / "bad-pupil.json"
    path.write_text(json.dumps(data), encoding="utf-8")

    result = check_validity("pupil_profile", path)

    assert result.outcome is ValidityOutcome.INVALID
    assert result.reason_codes == ("schema.construction.profile_rejected",)


@pytest.mark.parametrize(
    "bad_location",
    ["wavelength", "exposure", "per_wavelength_mapping"],
)
def test_camera_v1_conversion_rejections_are_invalid(
    tmp_path: Path,
    bad_location: str,
) -> None:
    data = per_band_camera_profile_dict()
    data.update({"artifact_type": "camera_profile", "schema_version": 1})
    if bad_location == "wavelength":
        data["illumination"]["wavelengths_nm"][0] = "abc"
    elif bad_location == "exposure":
        data["camera"]["per_wavelength"]["450"]["exposure_us"] = "abc"
    else:
        data["camera"]["per_wavelength"]["450"] = "not-a-mapping"
    path = tmp_path / "bad-camera.json"
    path.write_text(json.dumps(data), encoding="utf-8")

    result = check_validity("camera_profile", path)

    assert result.outcome is ValidityOutcome.INVALID
    assert result.reason_codes == ("schema.construction.profile_rejected",)


@pytest.mark.parametrize("extension_field", ["historical_extension", "extra"])
def test_pupil_v1_ignored_or_opaque_extensions_do_not_enter_numeric_domain(
    tmp_path: Path,
    extension_field: str,
) -> None:
    data = pupil_profile_dict()
    data.update({"artifact_type": "pupil_profile", "schema_version": 1})
    text = json.dumps(data)[:-1]
    text += f',"{extension_field}":{{"tiny":1e-999}}}}'
    path = tmp_path / "opaque-extension.json"
    path.write_text(text, encoding="utf-8")

    result = check_validity("pupil_profile", path)

    assert result.outcome is ValidityOutcome.VALID


@pytest.mark.parametrize("extension_field", ["historical_extension", "extra"])
def test_camera_v1_ignored_or_opaque_extensions_do_not_enter_numeric_domain(
    tmp_path: Path,
    extension_field: str,
) -> None:
    data = per_band_camera_profile_dict()
    data.update({"artifact_type": "camera_profile", "schema_version": 1})
    text = json.dumps(data)[:-1]
    text += f',"{extension_field}":{{"tiny":1e-999}}}}'
    path = tmp_path / "opaque-camera-extension.json"
    path.write_text(text, encoding="utf-8")

    result = check_validity("camera_profile", path)

    assert result.outcome is ValidityOutcome.VALID


def test_pupil_v1_consumed_number_still_enforces_binary64_domain(
    tmp_path: Path,
) -> None:
    data = pupil_profile_dict()
    data.update({"artifact_type": "pupil_profile", "schema_version": 1})
    text = json.dumps(data).replace(
        '"lcd_physical_radius": 52.8', '"lcd_physical_radius": 1e-999'
    )
    path = tmp_path / "underflow-radius.json"
    path.write_text(text, encoding="utf-8")

    result = check_validity("pupil_profile", path)

    assert result.outcome is ValidityOutcome.INVALID
    assert result.reason_codes == ("schema.construction.profile_rejected",)


def test_result_separates_requested_type_from_identified_identity(
    tmp_path: Path,
) -> None:
    path = tmp_path / "mismatch.json"
    path.write_text(
        json.dumps(
            {
                "artifact_type": "pupil_profile",
                "schema_version": 7,
            }
        ),
        encoding="utf-8",
    )

    result = check_validity("camera_profile", path)

    assert result.requested_artifact_type == "camera_profile"
    assert result.identified_identity == ArtifactIdentity(
        "pupil_profile",
        ArtifactRepresentation.JSON,
        ArtifactVersionSet(manifest=7),
    )
    assert result.reason_codes == ("schema.artifact_type.mismatch",)


def test_invalid_requested_type_is_preserved() -> None:
    result = check_validity("Not-Canonical", "unused")

    assert result.requested_artifact_type == "Not-Canonical"
    assert result.identified_identity is None
    assert result.reason_codes == ("artifact_type.invalid",)


def test_validity_result_normalizes_reason_codes_to_tuple() -> None:
    reason_codes = ["schema.invalid"]
    result = ValidityResult(
        "example",
        ValidityOutcome.INVALID,
        reason_codes=reason_codes,
        errors=["invalid"],
    )
    reason_codes.append("schema.changed")

    assert result.reason_codes == ("schema.invalid",)
    assert result.errors == ("invalid",)


def test_adapter_field_declarations_require_string_names() -> None:
    registry = SchemaAdapterRegistry()
    adapter = SchemaAdapter(
        artifact_type="bad_fields",
        representation=ArtifactRepresentation.JSON,
        versions=ArtifactVersionSet(manifest=1),
        allowed_fields=frozenset({"artifact_type", "schema_version", 1}),
        required_fields=frozenset({"artifact_type", "schema_version"}),
        construct=dict,
        validate_semantics=lambda artifact: None,
    )

    with pytest.raises(ValueError, match="only strings"):
        registry.register(adapter)


def test_adapter_provider_names_must_be_unique() -> None:
    first = _json_adapter("provider_one")
    second = _json_adapter("provider_two")
    registry = SchemaAdapterRegistry()
    registry.register(first)
    registry.register(second)
    providers = (
        SchemaAdapterProvider(
            "duplicate_name",
            frozenset({first.identity}),
            lambda target: None,
        ),
        SchemaAdapterProvider(
            "duplicate_name",
            frozenset({second.identity}),
            lambda target: None,
        ),
    )

    with pytest.raises(RuntimeError, match="duplicate adapter provider name"):
        validate_registry_completeness(registry, providers)


def test_json_probe_stops_after_first_non_json_chunk(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "large-binary"
    path.write_bytes(b"placeholder")

    class CountingStream(io.BytesIO):
        bytes_read = 0

        def read(self, size: int = -1) -> bytes:
            value = super().read(size)
            self.bytes_read += len(value)
            return value

    stream = CountingStream(b"\x89" + (b"x" * (20 * 1024 * 1024)))
    original_open = Path.open

    def open_counting(candidate: Path, *args, **kwargs):
        if candidate == path:
            return stream
        return original_open(candidate, *args, **kwargs)

    monkeypatch.setattr(Path, "open", open_counting)

    with validation_module._JSONRepresentationReader().open(path) as opened:
        assert opened.probe.outcome is ProbeOutcome.NO_MATCH

    assert stream.bytes_read <= validation_module._JSON_PROBE_CHUNK_BYTES


def _json_prefixed_hdf5_bytes() -> bytes:
    metadata = b'{"user_block":"json metadata"}'
    return metadata + (b" " * (512 - len(metadata))) + b"\x89HDF\r\n\x1a\n" + b"payload"


def test_json_prefixed_hdf5_user_block_prefers_signature_capability(
    tmp_path: Path,
) -> None:
    path = tmp_path / "json-user-block.h5"
    path.write_bytes(_json_prefixed_hdf5_bytes())

    result = check_validity("raw_capture", path)

    assert result.outcome is ValidityOutcome.UNSUPPORTED
    assert result.representation is ArtifactRepresentation.HDF5
    assert result.reason_codes == ("representation.reader_not_registered",)


def test_full_hdf_reader_uniquely_matches_json_prefixed_user_block(
    tmp_path: Path,
) -> None:
    identity = ArtifactIdentity(
        "raw_capture",
        ArtifactRepresentation.HDF5,
        ArtifactVersionSet(payload=1),
    )

    class FullHDFReader(RepresentationReader):
        representation = ArtifactRepresentation.HDF5

        @contextmanager
        def open(self, path: Path):
            raw = path.read_bytes()
            probe = (
                ProbeResult.match()
                if raw[512:520] == b"\x89HDF\r\n\x1a\n"
                else ProbeResult.no_match()
            )
            yield OpenedRepresentation(
                self.representation,
                probe,
                lambda: ParsedRepresentation(identity, raw),
            )

    readers = RepresentationReaderRegistry()
    readers.register(validation_module._HDF5ProbeReader())
    readers.register(validation_module._JSONRepresentationReader())
    readers.register(FullHDFReader())
    readers.freeze()
    adapters = _register_exact(
        SchemaAdapter(
            artifact_type="raw_capture",
            representation=ArtifactRepresentation.HDF5,
            versions=ArtifactVersionSet(payload=1),
            construct=lambda raw: raw,
            validate_semantics=lambda raw: None,
        )
    )
    path = tmp_path / "full-reader-user-block.h5"
    path.write_bytes(_json_prefixed_hdf5_bytes())

    result = check_validity(
        "raw_capture",
        path,
        adapter_registry=adapters,
        reader_registry=readers,
    )

    assert result.outcome is ValidityOutcome.VALID
    assert result.identified_identity == identity


def test_explicit_json_identity_must_match_embedded_document() -> None:
    adapter = _json_adapter("identity_bound")

    with pytest.raises(ValidatorFailureError) as exc_info:
        adapter.parse_and_validate(
            {
                "artifact_type": "different_type",
                "schema_version": 999,
            },
            identity=adapter.identity,
        )

    assert exc_info.value.reason_code == "validator.reader_identity_contract_violation"


def test_reader_identity_mismatch_with_json_document_is_validator_failure(
    tmp_path: Path,
) -> None:
    adapter = _json_adapter("identity_bound")

    class MismatchedJSONReader(RepresentationReader):
        representation = ArtifactRepresentation.JSON

        @contextmanager
        def open(self, path: Path):
            yield OpenedRepresentation(
                self.representation,
                ProbeResult.match(),
                lambda: ParsedRepresentation(
                    adapter.identity,
                    {
                        "artifact_type": "different_type",
                        "schema_version": 1,
                    },
                ),
            )

    readers = RepresentationReaderRegistry()
    readers.register(MismatchedJSONReader())
    readers.freeze()
    path = tmp_path / "mismatched.json"
    path.write_text("{}", encoding="utf-8")

    result = check_validity(
        "identity_bound",
        path,
        adapter_registry=_register_exact(adapter),
        reader_registry=readers,
    )

    assert result.outcome is ValidityOutcome.VALIDATOR_FAILED
    assert result.reason_codes == ("validator.reader_identity_contract_violation",)


def test_parsed_representation_rejects_non_identity_from_reader(
    tmp_path: Path,
) -> None:
    class BrokenIdentityReader(RepresentationReader):
        representation = ArtifactRepresentation.HDF5

        @contextmanager
        def open(self, path: Path):
            yield OpenedRepresentation(
                self.representation,
                ProbeResult.match(),
                lambda: ParsedRepresentation("not-an-identity", object()),
            )

    readers = RepresentationReaderRegistry()
    readers.register(BrokenIdentityReader())
    readers.freeze()
    path = tmp_path / "broken-identity"
    path.write_bytes(b"payload")

    result = check_validity(
        "raw_capture",
        path,
        adapter_registry=SchemaAdapterRegistry(),
        reader_registry=readers,
    )

    assert result.outcome is ValidityOutcome.VALIDATOR_FAILED
    assert result.reason_codes == ("validator.reader_identity_contract_violation",)


def test_unselected_reader_resource_closes_before_adapter_stages(
    tmp_path: Path,
) -> None:
    events: list[str] = []
    identity = ArtifactIdentity(
        "resource_owner",
        ArtifactRepresentation.HDF5,
        ArtifactVersionSet(payload=1),
    )

    class MatchReader(RepresentationReader):
        representation = ArtifactRepresentation.HDF5

        @contextmanager
        def open(self, path: Path):
            events.append("winner.open")
            try:
                yield OpenedRepresentation(
                    self.representation,
                    ProbeResult.match(),
                    lambda: ParsedRepresentation(identity, b"payload"),
                )
            finally:
                events.append("winner.close")

    class NoMatchReader(RepresentationReader):
        representation = ArtifactRepresentation.BUNDLE

        @contextmanager
        def open(self, path: Path):
            events.append("loser.open")
            try:
                yield OpenedRepresentation(
                    self.representation,
                    ProbeResult.no_match(),
                    lambda: pytest.fail("unselected reader must not parse"),
                )
            finally:
                events.append("loser.close")

    def construct(document):
        assert "loser.close" in events
        assert "winner.close" not in events
        events.append("construct")
        return document

    readers = RepresentationReaderRegistry()
    readers.register(NoMatchReader())
    readers.register(MatchReader())
    readers.freeze()
    adapters = _register_exact(
        SchemaAdapter(
            artifact_type="resource_owner",
            representation=ArtifactRepresentation.HDF5,
            versions=ArtifactVersionSet(payload=1),
            construct=construct,
            validate_semantics=lambda document: events.append("semantic"),
        )
    )
    path = tmp_path / "resource"
    path.write_bytes(b"payload")

    result = check_validity(
        "resource_owner",
        path,
        adapter_registry=adapters,
        reader_registry=readers,
    )

    assert result.outcome is ValidityOutcome.VALID
    assert events.index("loser.close") < events.index("construct")
    assert events[-1] == "winner.close"


@pytest.mark.parametrize(
    ("profile_factory", "artifact_type"),
    [
        (
            lambda: CameraProfile.from_dict(per_band_camera_profile_dict()),
            "camera_profile",
        ),
        (lambda: PupilProfile.from_dict(pupil_profile_dict()), "pupil_profile"),
    ],
)
def test_current_profile_writer_round_trips_through_validator(
    tmp_path: Path,
    profile_factory,
    artifact_type: str,
) -> None:
    profile = profile_factory()
    path = tmp_path / f"{artifact_type}.json"

    profile.to_json(path)
    result = check_validity(artifact_type, path)

    assert result.outcome is ValidityOutcome.VALID
    assert result.identified_identity == ArtifactIdentity(
        artifact_type,
        ArtifactRepresentation.JSON,
        ArtifactVersionSet(manifest=1),
    )


def test_current_profile_writers_reject_nonfinite_json(tmp_path: Path) -> None:
    pupil = PupilProfile.from_dict(pupil_profile_dict())
    pupil.lcd_physical_center = (float("nan"), 1.0)
    camera = CameraProfile.from_dict(per_band_camera_profile_dict())
    camera.extra = {"nonfinite": float("inf")}

    with pytest.raises(ValueError, match="JSON compliant"):
        pupil.to_json(tmp_path / "pupil.json")
    with pytest.raises(ValueError, match="JSON compliant"):
        camera.to_json(tmp_path / "camera.json")
