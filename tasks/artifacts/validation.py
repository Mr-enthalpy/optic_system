from __future__ import annotations

"""Representation-independent local artifact validation.

Representation readers own probing, parsing, and serialized identity
extraction. Schema contracts own one exact artifact identity. The orchestrator
only composes those two registries; it contains no JSON/HDF5/bundle dispatch
branches.
"""

from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import Enum
import json
import math
from pathlib import Path
import re
from types import MappingProxyType
from typing import Any

from tasks.artifact_versioning import (
    LegacyUnversionedArtifactError,
    NewerSchemaVersionError,
    OlderSchemaVersionError,
    SchemaCompatibilityError,
)


class ArtifactRepresentation(str, Enum):
    JSON = "json"
    HDF5 = "hdf5"
    BUNDLE = "bundle"


class ValidityOutcome(str, Enum):
    VALID = "valid"
    INVALID = "invalid"
    UNSUPPORTED = "unsupported"
    LEGACY_UNVERSIONED = "legacy_unversioned"
    UNREADABLE = "unreadable"
    VALIDATOR_FAILED = "validator_failed"


class SemanticValidationMode(str, Enum):
    """Closed semantic-ownership modes supported by schema contracts."""

    EXPLICIT = "explicit"
    LEGACY_LOADER = "legacy_loader"


class AdditionalFieldsPolicy(str, Enum):
    FORBID = "forbid"
    IGNORE = "ignore"


@dataclass(frozen=True)
class ArtifactVersionSet:
    """Independent versions for manifest, payload, and bundle envelope."""

    manifest: int | None = None
    payload: int | None = None
    bundle: int | None = None

    def __post_init__(self) -> None:
        for axis, version in zip(
            ("manifest", "payload", "bundle"),
            self.values(),
            strict=True,
        ):
            if version is None:
                continue
            if type(version) is not int or version < 1:
                raise ValueError(f"{axis} schema version must be a positive integer")

    def values(self) -> tuple[int | None, int | None, int | None]:
        return (self.manifest, self.payload, self.bundle)


@dataclass(frozen=True)
class ArtifactIdentity:
    artifact_type: str
    representation: ArtifactRepresentation
    versions: ArtifactVersionSet


@dataclass(frozen=True)
class ParsedRepresentation:
    identity: ArtifactIdentity
    document: Any


@dataclass(frozen=True)
class ValidityResult:
    """Path-free, machine-readable result for one serialized representation."""

    artifact_type: str
    outcome: ValidityOutcome
    representation: ArtifactRepresentation | None = None
    versions: ArtifactVersionSet = field(default_factory=ArtifactVersionSet)
    reason_codes: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "errors",
            tuple(_sanitize_result_message(value) for value in self.errors),
        )
        object.__setattr__(
            self,
            "warnings",
            tuple(_sanitize_result_message(value) for value in self.warnings),
        )
        if not isinstance(self.outcome, ValidityOutcome):
            raise ValueError("outcome must be ValidityOutcome")
        if self.outcome is ValidityOutcome.VALID:
            if self.reason_codes or self.errors:
                raise ValueError("VALID result cannot contain reason codes or errors")
        elif not self.reason_codes:
            raise ValueError("non-VALID result requires at least one reason code")
        if (
            self.outcome is ValidityOutcome.LEGACY_UNVERSIONED
            and any(version is not None for version in self.versions.values())
        ):
            raise ValueError("LEGACY_UNVERSIONED result cannot declare schema versions")

    @property
    def ok(self) -> bool:
        return self.outcome is ValidityOutcome.VALID

    @property
    def manifest_schema_version(self) -> int | None:
        return self.versions.manifest

    @property
    def payload_schema_version(self) -> int | None:
        return self.versions.payload

    @property
    def bundle_schema_version(self) -> int | None:
        return self.versions.bundle

    @property
    def schema_version(self) -> int | None:
        """Compatibility view for callers that predate the version set."""
        if self.versions.manifest is not None:
            return self.versions.manifest
        if self.versions.payload is not None:
            return self.versions.payload
        return self.versions.bundle


class ArtifactValidationError(ValueError):
    """Expected failure carrying stable outcome and reason-code classification."""

    outcome = ValidityOutcome.INVALID

    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code
        self.message = message


class SerializedSchemaError(ArtifactValidationError):
    pass


class SemanticValidationError(ArtifactValidationError):
    pass


class RepresentationUnreadableError(ArtifactValidationError):
    outcome = ValidityOutcome.UNREADABLE


class UnsupportedRepresentationError(ArtifactValidationError):
    outcome = ValidityOutcome.UNSUPPORTED


class ReaderLimitError(ArtifactValidationError):
    """The local reader deliberately does not support this resource scale."""

    outcome = ValidityOutcome.UNSUPPORTED


SerializedValidator = Callable[[Any], None]
ArtifactConstructor = Callable[[Any], Any]
SemanticValidator = Callable[[Any], None]


@dataclass(frozen=True)
class SchemaAdapter:
    """One exact serialized contract with an explicit semantic validator."""

    artifact_type: str
    representation: ArtifactRepresentation
    versions: ArtifactVersionSet
    construct: ArtifactConstructor
    allowed_fields: frozenset[str] | None = None
    required_fields: frozenset[str] = frozenset()
    validate_serialized: SerializedValidator | None = None
    validate_semantics: SemanticValidator | None = None
    contract_error_types: tuple[type[Exception], ...] = ()
    additional_fields_policy: AdditionalFieldsPolicy = AdditionalFieldsPolicy.FORBID

    @property
    def key(self) -> tuple[str, ArtifactRepresentation, ArtifactVersionSet]:
        return (self.artifact_type, self.representation, self.versions)

    @property
    def semantic_mode(self) -> SemanticValidationMode:
        return SemanticValidationMode.EXPLICIT

    @property
    def schema_version(self) -> int | None:
        return _single_schema_version(self.representation, self.versions)

    def parse_and_validate(
        self,
        document: Any,
        *,
        identity: ArtifactIdentity | None = None,
    ) -> Any:
        """Invoke each configured engine stage once over a parsed document."""
        resolved_identity = identity or _identity_from_mapping(
            document,
            self.representation,
        )
        expected = ArtifactIdentity(
            self.artifact_type,
            self.representation,
            self.versions,
        )
        if resolved_identity != expected:
            raise SerializedSchemaError(
                "schema.identity.mismatch",
                "serialized identity does not match schema adapter identity",
            )
        try:
            _validate_document_fields(self, document)
            if self.validate_serialized is not None:
                self.validate_serialized(document)
            artifact = self.construct(document)
            if self.validate_semantics is None:
                raise RuntimeError("exact adapter lacks its semantic validator")
            self.validate_semantics(artifact)
            return artifact
        except ArtifactValidationError:
            raise
        except self.contract_error_types as exc:
            raise SemanticValidationError(
                "schema.semantic.invalid",
                _safe_message(exc, "artifact violates its semantic contract"),
            ) from exc


@dataclass(frozen=True)
class LegacyCompatibilityBridge:
    """Historical load-and-validate callback with explicitly non-exact internals."""

    artifact_type: str
    representation: ArtifactRepresentation
    versions: ArtifactVersionSet
    load_and_validate: ArtifactConstructor
    allowed_fields: frozenset[str] | None = None
    required_fields: frozenset[str] = frozenset()
    validate_serialized: SerializedValidator | None = None
    contract_error_types: tuple[type[Exception], ...] = ()
    additional_fields_policy: AdditionalFieldsPolicy = AdditionalFieldsPolicy.IGNORE

    @property
    def key(self) -> tuple[str, ArtifactRepresentation, ArtifactVersionSet]:
        return (self.artifact_type, self.representation, self.versions)

    @property
    def semantic_mode(self) -> SemanticValidationMode:
        return SemanticValidationMode.LEGACY_LOADER

    @property
    def schema_version(self) -> int | None:
        return _single_schema_version(self.representation, self.versions)

    def parse_and_validate(
        self,
        document: Any,
        *,
        identity: ArtifactIdentity | None = None,
    ) -> Any:
        resolved_identity = identity or _identity_from_mapping(
            document,
            self.representation,
        )
        expected = ArtifactIdentity(
            self.artifact_type,
            self.representation,
            self.versions,
        )
        if resolved_identity != expected:
            raise SerializedSchemaError(
                "schema.identity.mismatch",
                "serialized identity does not match compatibility bridge identity",
            )
        try:
            _validate_document_fields(self, document)
            if self.validate_serialized is not None:
                self.validate_serialized(document)
            return self.load_and_validate(document)
        except ArtifactValidationError:
            raise
        except self.contract_error_types as exc:
            raise SemanticValidationError(
                "schema.semantic.invalid",
                _safe_message(exc, "artifact violates its historical contract"),
            ) from exc


SchemaContract = SchemaAdapter | LegacyCompatibilityBridge
AdapterKey = tuple[str, ArtifactRepresentation, ArtifactVersionSet]


class SchemaAdapterRegistry:
    """Mutable only during deterministic bootstrap, then read-only."""

    def __init__(self) -> None:
        self._adapters: dict[AdapterKey, SchemaContract] = {}
        self._frozen = False

    @property
    def frozen(self) -> bool:
        return self._frozen

    @property
    def adapters(self) -> Mapping[AdapterKey, SchemaContract]:
        return MappingProxyType(self._adapters)

    def register(self, adapter: SchemaContract) -> None:
        if self._frozen:
            raise RuntimeError("schema adapter registry is frozen")
        _validate_schema_contract(adapter)
        if adapter.key in self._adapters:
            raise ValueError(
                f"schema adapter is already registered for {adapter.key!r}"
            )
        self._adapters[adapter.key] = adapter

    def get(self, key: AdapterKey) -> SchemaContract | None:
        return self._adapters.get(key)

    def values(self) -> tuple[SchemaContract, ...]:
        return tuple(self._adapters.values())

    def freeze(self) -> None:
        self._frozen = True


def register_schema_adapter(
    adapter: SchemaAdapter,
    *,
    registry: SchemaAdapterRegistry,
) -> None:
    registry.register(adapter)


def register_legacy_compatibility_bridge(
    adapter: LegacyCompatibilityBridge,
    *,
    registry: SchemaAdapterRegistry,
) -> None:
    registry.register(adapter)


def get_schema_adapter(
    artifact_type: str,
    representation: ArtifactRepresentation,
    versions: ArtifactVersionSet | int,
    *,
    registry: SchemaAdapterRegistry | None = None,
) -> SchemaContract | None:
    registry = registry or _get_builtin_schema_registry()
    if type(versions) is int:
        versions = _version_set_for_representation(representation, versions)
    return registry.get((artifact_type, representation, versions))


class RepresentationReader(ABC):
    representation: ArtifactRepresentation

    @abstractmethod
    def detect(self, path: Path) -> bool:
        """Return whether this reader owns the serialized location."""

    @abstractmethod
    def parse(self, path: Path, expected_artifact_type: str) -> ParsedRepresentation:
        """Parse representation data and extract its complete identity."""


class RepresentationReaderRegistry:
    """Ordered reader registry, mutable only until bootstrap is frozen."""

    def __init__(self) -> None:
        self._readers: list[RepresentationReader] = []
        self._frozen = False

    @property
    def frozen(self) -> bool:
        return self._frozen

    @property
    def readers(self) -> tuple[RepresentationReader, ...]:
        return tuple(self._readers)

    def register(self, reader: RepresentationReader) -> None:
        if self._frozen:
            raise RuntimeError("representation reader registry is frozen")
        if not isinstance(reader, RepresentationReader):
            raise TypeError("reader must implement RepresentationReader")
        if not isinstance(reader.representation, ArtifactRepresentation):
            raise ValueError("reader representation must be ArtifactRepresentation")
        if any(
            existing.representation is reader.representation
            for existing in self._readers
        ):
            raise ValueError(
                f"representation reader already registered for "
                f"{reader.representation.value}"
            )
        self._readers.append(reader)

    def detect(self, path: Path) -> RepresentationReader | None:
        for reader in self._readers:
            if reader.detect(path):
                return reader
        return None

    def freeze(self) -> None:
        self._frozen = True


def register_representation_reader(
    reader: RepresentationReader,
    *,
    registry: RepresentationReaderRegistry,
) -> None:
    registry.register(reader)


_HDF5_SIGNATURE = b"\x89HDF\r\n\x1a\n"
_MAX_HDF5_PROBE_OFFSET = 64 * 1024 * 1024
_MAX_JSON_INTEGER_DIGITS = 4300


def _parse_json_integer(token: str) -> int:
    digits = token[1:] if token.startswith("-") else token
    if len(digits) > _MAX_JSON_INTEGER_DIGITS:
        raise ReaderLimitError(
            "reader_limit.json_integer_digits",
            "JSON integer literal exceeds this reader's supported digit limit",
        )
    try:
        return int(token)
    except ValueError as exc:
        raise ReaderLimitError(
            "reader_limit.json_integer_digits",
            "JSON integer literal exceeds the runtime integer policy",
        ) from exc


def _parse_json_float(token: str) -> float:
    value = float(token)
    if not math.isfinite(value):
        raise ReaderLimitError(
            "reader_limit.json_float_range",
            "JSON number is outside the finite binary64 range",
        )
    return value


def _reject_json_constant(token: str) -> Any:
    raise RepresentationUnreadableError(
        "representation.json.nonstandard_constant",
        f"non-standard JSON numeric constant {token!r} is not allowed",
    )


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise SerializedSchemaError(
                "representation.json.duplicate_key",
                f"duplicate JSON object key {key!r}",
            )
        result[key] = value
    return result


def parse_json_mapping(path: str | Path) -> dict[str, Any]:
    artifact_path = Path(path)
    try:
        text = artifact_path.read_text(encoding="utf-8")
    except UnicodeError as exc:
        raise RepresentationUnreadableError(
            "representation.json.not_utf8",
            "artifact JSON is not valid UTF-8",
        ) from exc
    except OSError as exc:
        raise RepresentationUnreadableError(
            "location.unreadable",
            "artifact location could not be read",
        ) from exc
    try:
        value = json.loads(
            text,
            parse_int=_parse_json_integer,
            parse_float=_parse_json_float,
            parse_constant=_reject_json_constant,
            object_pairs_hook=_reject_duplicate_pairs,
        )
    except ArtifactValidationError:
        raise
    except RecursionError as exc:
        raise ReaderLimitError(
            "reader_limit.json_nesting",
            "JSON representation exceeds this reader's nesting limit",
        ) from exc
    except (json.JSONDecodeError, ValueError) as exc:
        raise RepresentationUnreadableError(
            "representation.json.parse_error",
            "artifact JSON could not be parsed",
        ) from exc
    if not isinstance(value, dict):
        raise SerializedSchemaError(
            "representation.json.root_invalid",
            "artifact JSON root must be a mapping",
        )
    return value


class _HDF5ProbeReader(RepresentationReader):
    """Recognize HDF5 locations until a payload reader is registered."""

    representation = ArtifactRepresentation.HDF5

    def detect(self, path: Path) -> bool:
        if not path.is_file():
            return False
        try:
            size = path.stat().st_size
            with path.open("rb") as stream:
                offset = 0
                while offset <= size and offset <= _MAX_HDF5_PROBE_OFFSET:
                    stream.seek(offset)
                    if stream.read(len(_HDF5_SIGNATURE)) == _HDF5_SIGNATURE:
                        return True
                    offset = 512 if offset == 0 else offset * 2
        except OSError:
            return False
        return False

    def parse(self, path: Path, expected_artifact_type: str) -> ParsedRepresentation:
        raise UnsupportedRepresentationError(
            "representation.adapter_not_registered",
            "HDF5 representation reader is not implemented",
        )


class _JSONRepresentationReader(RepresentationReader):
    representation = ArtifactRepresentation.JSON

    def detect(self, path: Path) -> bool:
        return path.is_file()

    def parse(self, path: Path, expected_artifact_type: str) -> ParsedRepresentation:
        mapping = parse_json_mapping(path)
        identity = _identity_from_mapping(mapping, self.representation)
        return ParsedRepresentation(identity, mapping)


def build_default_representation_reader_registry() -> RepresentationReaderRegistry:
    registry = RepresentationReaderRegistry()
    registry.register(_HDF5ProbeReader())
    registry.register(_JSONRepresentationReader())
    registry.freeze()
    return registry


_BUILTIN_REPRESENTATION_READERS = build_default_representation_reader_registry()
_BUILTIN_SCHEMA_ADAPTERS: SchemaAdapterRegistry | None = None


def _get_builtin_schema_registry() -> SchemaAdapterRegistry:
    global _BUILTIN_SCHEMA_ADAPTERS
    if _BUILTIN_SCHEMA_ADAPTERS is None:
        from .adapter_catalog import build_builtin_schema_registry

        _BUILTIN_SCHEMA_ADAPTERS = build_builtin_schema_registry()
    return _BUILTIN_SCHEMA_ADAPTERS


def check_validity(
    artifact_type: str,
    path: str | Path,
    *,
    adapter_registry: SchemaAdapterRegistry | None = None,
    reader_registry: RepresentationReaderRegistry | None = None,
) -> ValidityResult:
    """Validate by composing one representation reader and one exact contract."""
    if (
        not isinstance(artifact_type, str)
        or not artifact_type
        or artifact_type != artifact_type.strip()
    ):
        return _result(
            str(artifact_type),
            ValidityOutcome.UNSUPPORTED,
            reason_codes=("artifact_type.unknown",),
            errors=("unknown artifact_type",),
        )

    artifact_path = Path(path)
    if not artifact_path.exists():
        return _result(
            artifact_type,
            ValidityOutcome.UNREADABLE,
            reason_codes=("location.missing",),
            errors=("artifact location does not exist",),
        )

    adapters = adapter_registry or _get_builtin_schema_registry()
    readers = reader_registry or _BUILTIN_REPRESENTATION_READERS
    representation: ArtifactRepresentation | None = None
    versions = ArtifactVersionSet()
    try:
        if not any(
            adapter.artifact_type == artifact_type for adapter in adapters.values()
        ):
            raise UnsupportedRepresentationError(
                "artifact_type.unknown",
                "validator_not_implemented: no schema contract is registered "
                "for this artifact type",
            )
        reader = readers.detect(artifact_path)
        if reader is None:
            raise UnsupportedRepresentationError(
                "representation.reader_not_registered",
                "no representation reader recognized the artifact location",
            )
        representation = reader.representation
        parsed = reader.parse(artifact_path, artifact_type)
        if parsed.identity.representation is not representation:
            raise SerializedSchemaError(
                "schema.identity.mismatch",
                "reader returned an identity for a different representation",
            )
        versions = parsed.identity.versions
        if parsed.identity.artifact_type != artifact_type:
            raise SerializedSchemaError(
                "schema.artifact_type.mismatch",
                f"artifact_type mismatch: expected {artifact_type!r}, "
                f"found {parsed.identity.artifact_type!r}",
            )
        adapter = adapters.get((artifact_type, representation, versions))
        if adapter is None:
            raise _missing_adapter_error(
                artifact_type,
                representation,
                versions,
                adapters,
            )
        adapter.parse_and_validate(parsed.document, identity=parsed.identity)
        return _result(
            artifact_type,
            ValidityOutcome.VALID,
            representation=representation,
            versions=versions,
        )
    except LegacyUnversionedArtifactError:
        return _result(
            artifact_type,
            ValidityOutcome.LEGACY_UNVERSIONED,
            representation=representation,
            reason_codes=("schema.version.missing",),
            errors=("legacy_unversioned: serialized artifact lacks schema version",),
        )
    except (NewerSchemaVersionError, OlderSchemaVersionError) as exc:
        return _result(
            artifact_type,
            ValidityOutcome.UNSUPPORTED,
            representation=representation,
            versions=_version_set_for_representation(representation, exc.version),
            reason_codes=(
                "schema.version.newer"
                if isinstance(exc, NewerSchemaVersionError)
                else "schema.version.older",
            ),
            errors=(_safe_message(exc, "schema version is outside the readable window"),),
        )
    except SchemaCompatibilityError as exc:
        return _result(
            artifact_type,
            ValidityOutcome.INVALID,
            representation=representation,
            versions=versions,
            reason_codes=("schema.version.invalid",),
            errors=(_safe_message(exc, "schema version is invalid"),),
        )
    except ArtifactValidationError as exc:
        return _result(
            artifact_type,
            exc.outcome,
            representation=representation,
            versions=versions,
            reason_codes=(exc.reason_code,),
            errors=(exc.message,),
        )
    except Exception as exc:  # noqa: BLE001 - undeclared programming failure.
        return _result(
            artifact_type,
            ValidityOutcome.VALIDATOR_FAILED,
            representation=representation,
            versions=versions,
            reason_codes=("validator.internal_failure",),
            errors=(f"validator raised {type(exc).__name__}",),
        )


def _result(
    artifact_type: str,
    outcome: ValidityOutcome,
    *,
    representation: ArtifactRepresentation | None = None,
    versions: ArtifactVersionSet | None = None,
    reason_codes: tuple[str, ...] = (),
    errors: tuple[str, ...] = (),
    warnings: tuple[str, ...] = (),
) -> ValidityResult:
    return ValidityResult(
        artifact_type=artifact_type,
        outcome=outcome,
        representation=representation,
        versions=versions or ArtifactVersionSet(),
        reason_codes=reason_codes,
        errors=errors,
        warnings=warnings,
    )


def _validate_schema_contract(adapter: SchemaContract) -> None:
    if (
        not isinstance(adapter.artifact_type, str)
        or not adapter.artifact_type
        or adapter.artifact_type != adapter.artifact_type.strip()
    ):
        raise ValueError("adapter artifact_type must be a canonical non-empty string")
    if not isinstance(adapter.representation, ArtifactRepresentation):
        raise ValueError("adapter representation must be ArtifactRepresentation")
    if not any(version is not None for version in adapter.versions.values()):
        raise ValueError("adapter must declare at least one schema version axis")
    if type(adapter.allowed_fields) not in (frozenset, type(None)):
        raise ValueError("adapter allowed_fields must be frozenset or None")
    if type(adapter.required_fields) is not frozenset:
        raise ValueError("adapter required_fields must be frozenset")
    if (
        adapter.allowed_fields is not None
        and not adapter.required_fields <= adapter.allowed_fields
    ):
        raise ValueError("adapter required_fields must be a subset of allowed_fields")
    if not isinstance(adapter.additional_fields_policy, AdditionalFieldsPolicy):
        raise ValueError("adapter additional_fields_policy is invalid")
    if adapter.representation is ArtifactRepresentation.JSON:
        if adapter.versions.manifest is None or any(
            version is not None
            for version in (adapter.versions.payload, adapter.versions.bundle)
        ):
            raise ValueError("JSON contracts must use only the manifest version axis")
        if adapter.allowed_fields is None:
            raise ValueError("JSON contracts must declare allowed_fields")
        identity_fields = frozenset({"artifact_type", "schema_version"})
        if not identity_fields <= adapter.required_fields:
            raise ValueError("JSON contracts must require serialized identity fields")
    if isinstance(adapter, SchemaAdapter):
        if not callable(adapter.construct):
            raise ValueError("exact schema adapter construct must be callable")
        if not callable(adapter.validate_semantics):
            raise ValueError("exact schema adapter requires validate_semantics")
    elif not callable(adapter.load_and_validate):
        raise ValueError("legacy bridge load_and_validate must be callable")
    if adapter.validate_serialized is not None and not callable(
        adapter.validate_serialized
    ):
        raise ValueError("validate_serialized must be callable")
    for error_type in adapter.contract_error_types:
        if not isinstance(error_type, type) or not issubclass(error_type, Exception):
            raise ValueError("contract_error_types must contain exception classes")


def _validate_document_fields(adapter: SchemaContract, document: Any) -> None:
    if isinstance(document, Mapping):
        if any(not isinstance(key, str) for key in document):
            raise SerializedSchemaError(
                "schema.field.name_invalid",
                "serialized field names must be strings",
            )
        if adapter.allowed_fields is not None:
            unknown = sorted(set(document) - adapter.allowed_fields)
            if (
                unknown
                and adapter.additional_fields_policy is AdditionalFieldsPolicy.FORBID
            ):
                raise SerializedSchemaError(
                    "schema.field.unknown",
                    f"unknown serialized field(s): {', '.join(unknown)}",
                )
        missing = sorted(adapter.required_fields - set(document))
        if missing:
            raise SerializedSchemaError(
                "schema.field.missing",
                f"missing required serialized field(s): {', '.join(missing)}",
            )
    elif adapter.allowed_fields is not None or adapter.required_fields:
        raise SerializedSchemaError(
            "schema.document.type_invalid",
            "this schema contract requires a mapping document",
        )


def _identity_from_mapping(
    document: Any,
    representation: ArtifactRepresentation,
) -> ArtifactIdentity:
    if representation is not ArtifactRepresentation.JSON or not isinstance(
        document,
        Mapping,
    ):
        raise SerializedSchemaError(
            "schema.identity.missing",
            "non-JSON adapter invocation requires reader-provided identity",
        )
    artifact_type = document.get("artifact_type")
    if not isinstance(artifact_type, str) or not artifact_type:
        raise SerializedSchemaError(
            "schema.artifact_type.missing",
            "artifact_type is required and must be a non-empty string",
        )
    if "schema_version" not in document:
        raise LegacyUnversionedArtifactError("schema_version is required")
    version = document["schema_version"]
    if type(version) is not int or version < 1:
        raise SerializedSchemaError(
            "schema.version.invalid",
            "schema_version must be a positive integer",
        )
    return ArtifactIdentity(
        artifact_type,
        representation,
        ArtifactVersionSet(manifest=version),
    )


def _single_schema_version(
    representation: ArtifactRepresentation,
    versions: ArtifactVersionSet,
) -> int | None:
    if representation is ArtifactRepresentation.JSON:
        return versions.manifest
    if representation is ArtifactRepresentation.HDF5:
        return versions.payload
    return versions.bundle


def _version_set_for_representation(
    representation: ArtifactRepresentation | None,
    version: int,
) -> ArtifactVersionSet:
    if representation is ArtifactRepresentation.HDF5:
        return ArtifactVersionSet(payload=version)
    if representation is ArtifactRepresentation.BUNDLE:
        return ArtifactVersionSet(bundle=version)
    return ArtifactVersionSet(manifest=version)


def _missing_adapter_error(
    artifact_type: str,
    representation: ArtifactRepresentation,
    versions: ArtifactVersionSet,
    registry: SchemaAdapterRegistry,
) -> UnsupportedRepresentationError:
    same_type = [
        adapter for adapter in registry.values() if adapter.artifact_type == artifact_type
    ]
    if not same_type:
        return UnsupportedRepresentationError(
            "artifact_type.unknown",
            "validator_not_implemented: no schema contract is registered for "
            "this artifact type",
        )
    if not any(adapter.representation is representation for adapter in same_type):
        return UnsupportedRepresentationError(
            "representation.adapter_not_registered",
            "validator_not_implemented: no schema contract is registered for "
            "this representation",
        )
    requested = _single_schema_version(representation, versions)
    registered = [
        _single_schema_version(adapter.representation, adapter.versions)
        for adapter in same_type
        if adapter.representation is representation
    ]
    registered = [version for version in registered if version is not None]
    if requested is not None and registered:
        if requested > max(registered):
            return UnsupportedRepresentationError(
                "schema.version.newer",
                "schema version is newer than this reader's registered contracts",
            )
        if requested < min(registered):
            return UnsupportedRepresentationError(
                "schema.version.older",
                "schema version is older than this reader's registered contracts",
            )
    return UnsupportedRepresentationError(
        "schema.adapter_not_registered",
        "validator_not_implemented: no schema contract is registered for this "
        "version identity",
    )


def _safe_message(exc: Exception, fallback: str) -> str:
    message = str(exc).strip()
    if not message or len(message) > 512:
        return fallback
    if re.search(r"(?<![A-Za-z0-9])(?:[A-Za-z]:[\\/]|\\\\|/)[^\s\"']+", message):
        return fallback
    return message


def _sanitize_result_message(value: Any) -> str:
    return _safe_message(
        ValueError(str(value)),
        "diagnostic detail omitted because it contained unsafe location data",
    )


__all__ = [
    "AdditionalFieldsPolicy",
    "ArtifactIdentity",
    "ArtifactRepresentation",
    "ArtifactValidationError",
    "ArtifactVersionSet",
    "LegacyCompatibilityBridge",
    "ParsedRepresentation",
    "ReaderLimitError",
    "RepresentationReader",
    "RepresentationReaderRegistry",
    "SchemaAdapter",
    "SchemaAdapterRegistry",
    "SemanticValidationError",
    "SemanticValidationMode",
    "SerializedSchemaError",
    "UnsupportedRepresentationError",
    "ValidityOutcome",
    "ValidityResult",
    "build_default_representation_reader_registry",
    "check_validity",
    "get_schema_adapter",
    "parse_json_mapping",
    "register_legacy_compatibility_bridge",
    "register_representation_reader",
    "register_schema_adapter",
]
