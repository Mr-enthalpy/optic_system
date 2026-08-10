from __future__ import annotations

"""Small, deterministic validation for project-owned artifact formats.

Validation is an import/diagnostic slow path.  Normal scientific code should
load already trusted typed artifacts directly.
"""

from abc import ABC, abstractmethod
from collections.abc import Callable, Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import Enum
import json
from pathlib import Path
import re
from types import MappingProxyType
from typing import Any

from tasks.artifact_versioning import ARTIFACT_TYPE_VOCABULARY

_ARTIFACT_TYPE_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_REASON_CODE_RE = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$")
_KNOWN_ARTIFACT_TYPES = ARTIFACT_TYPE_VOCABULARY


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
    EXPLICIT = "explicit"
    LEGACY_LOADER = "legacy_loader"


class AdditionalFieldsPolicy(str, Enum):
    FORBID = "forbid"
    IGNORE = "ignore"


class ProbeOutcome(str, Enum):
    NO_MATCH = "no_match"
    MATCH = "match"
    UNREADABLE = "unreadable"
    UNSUPPORTED_LIMIT = "unsupported_limit"
    UNSUPPORTED_CAPABILITY = "unsupported_capability"


class ProbeFailureScope(str, Enum):
    READER_LOCAL = "reader_local"
    LOCATION_GLOBAL = "location_global"


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

    def __post_init__(self) -> None:
        _require_artifact_type(self.artifact_type)
        if not isinstance(self.representation, ArtifactRepresentation):
            raise ValueError("identity representation must be ArtifactRepresentation")
        if not isinstance(self.versions, ArtifactVersionSet):
            raise ValueError("identity versions must be ArtifactVersionSet")
        manifest, payload, bundle = self.versions.values()
        if self.representation is ArtifactRepresentation.JSON:
            valid_shape = manifest is not None and payload is None and bundle is None
        elif self.representation is ArtifactRepresentation.HDF5:
            valid_shape = payload is not None and bundle is None
        else:
            valid_shape = bundle is not None
        if not valid_shape:
            raise ValueError(
                "version axes are inconsistent with artifact representation"
            )


@dataclass(frozen=True)
class ParsedRepresentation:
    identity: ArtifactIdentity
    document: Any

    def __post_init__(self) -> None:
        if not isinstance(self.identity, ArtifactIdentity):
            raise ParsedRepresentationContractError(
                "parsed representation identity must be ArtifactIdentity"
            )


class ParsedRepresentationContractError(TypeError):
    pass


@dataclass(frozen=True)
class ProbeResult:
    outcome: ProbeOutcome
    reason_code: str | None = None
    message: str | None = None
    failure_scope: ProbeFailureScope | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.outcome, ProbeOutcome):
            raise ValueError("probe outcome must be ProbeOutcome")
        needs_diagnostic = self.outcome in {
            ProbeOutcome.UNREADABLE,
            ProbeOutcome.UNSUPPORTED_LIMIT,
            ProbeOutcome.UNSUPPORTED_CAPABILITY,
        }
        if needs_diagnostic:
            if self.reason_code is None or self.message is None:
                raise ValueError("terminal probe outcome requires diagnostics")
            _require_reason_code(self.reason_code)
            if not isinstance(self.failure_scope, ProbeFailureScope):
                raise ValueError("terminal probe outcome requires failure scope")
        elif (
            self.reason_code is not None
            or self.message is not None
            or self.failure_scope is not None
        ):
            raise ValueError("match/no-match probe cannot carry diagnostics")

    @classmethod
    def no_match(cls) -> ProbeResult:
        return cls(ProbeOutcome.NO_MATCH)

    @classmethod
    def match(cls) -> ProbeResult:
        return cls(ProbeOutcome.MATCH)


@dataclass(frozen=True)
class OpenedRepresentation:
    """A probed resource kept alive until schema validation completes."""

    representation: ArtifactRepresentation
    probe: ProbeResult
    parse_representation: Callable[[], ParsedRepresentation]

    def parse(self) -> ParsedRepresentation:
        return self.parse_representation()


@dataclass(frozen=True)
class ValidityResult:
    """Path-free, machine-readable result for one serialized representation."""

    requested_artifact_type: str
    outcome: ValidityOutcome
    identified_identity: ArtifactIdentity | None = None
    identified_representation: ArtifactRepresentation | None = None
    reason_codes: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.requested_artifact_type, str):
            raise ValueError("requested_artifact_type must be a string")
        if not isinstance(self.outcome, ValidityOutcome):
            raise ValueError("outcome must be ValidityOutcome")
        if self.identified_identity is not None and not isinstance(
            self.identified_identity,
            ArtifactIdentity,
        ):
            raise ValueError("identified_identity must be ArtifactIdentity or None")
        if self.identified_representation is not None and not isinstance(
            self.identified_representation,
            ArtifactRepresentation,
        ):
            raise ValueError(
                "identified_representation must be ArtifactRepresentation or None"
            )
        if (
            self.identified_identity is not None
            and self.identified_representation is not None
        ):
            raise ValueError(
                "complete identity and partial representation are mutually exclusive"
            )
        object.__setattr__(self, "reason_codes", tuple(self.reason_codes))
        for reason_code in self.reason_codes:
            _require_reason_code(reason_code)
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
        if self.outcome is ValidityOutcome.VALID:
            if self.reason_codes or self.errors:
                raise ValueError("VALID result cannot contain reason codes or errors")
            if self.identified_identity is None:
                raise ValueError("VALID result requires a complete artifact identity")
        elif not self.reason_codes:
            raise ValueError("non-VALID result requires at least one reason code")
        elif not self.errors:
            raise ValueError("non-VALID result requires a diagnostic message")
        if (
            self.outcome is ValidityOutcome.LEGACY_UNVERSIONED
            and self.identified_identity is not None
        ):
            raise ValueError("LEGACY_UNVERSIONED result cannot have complete identity")
        if (
            self.outcome is ValidityOutcome.LEGACY_UNVERSIONED
            and self.identified_representation is None
        ):
            raise ValueError("LEGACY_UNVERSIONED result requires representation")

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
        """Compatibility scalar, valid only when zero or one axis is populated."""
        return _unambiguous_schema_version(self.versions)

    @property
    def representation(self) -> ArtifactRepresentation | None:
        if self.identified_identity is not None:
            return self.identified_identity.representation
        return self.identified_representation

    @property
    def versions(self) -> ArtifactVersionSet:
        if self.identified_identity is not None:
            return self.identified_identity.versions
        return ArtifactVersionSet()


class ArtifactValidationError(ValueError):
    outcome = ValidityOutcome.INVALID

    def __init__(
        self,
        reason_code: str,
        message: str,
        *,
        representation: ArtifactRepresentation | None = None,
    ) -> None:
        _require_reason_code(reason_code)
        if representation is not None and not isinstance(
            representation, ArtifactRepresentation
        ):
            raise ValueError("error representation is invalid")
        super().__init__(message)
        self.reason_code = reason_code
        self.message = message
        self.representation = representation


class SerializedSchemaError(ArtifactValidationError):
    pass


class RepresentationParseError(ArtifactValidationError):
    outcome = ValidityOutcome.UNREADABLE


class RepresentationStructureError(RepresentationParseError):
    outcome = ValidityOutcome.INVALID


class IdentityValidationError(ArtifactValidationError):
    pass


class ConstructionValidationError(ArtifactValidationError):
    pass


class SemanticValidationError(ArtifactValidationError):
    pass


class RepresentationUnreadableError(ArtifactValidationError):
    outcome = ValidityOutcome.UNREADABLE


class UnsupportedRepresentationError(ArtifactValidationError):
    outcome = ValidityOutcome.UNSUPPORTED


class ReaderLimitError(ArtifactValidationError):
    outcome = ValidityOutcome.UNSUPPORTED


class LegacyUnversionedValidationError(IdentityValidationError):
    outcome = ValidityOutcome.LEGACY_UNVERSIONED


class ValidatorFailureError(ArtifactValidationError):
    outcome = ValidityOutcome.VALIDATOR_FAILED


SerializedValidator = Callable[[Any], None]
ArtifactConstructor = Callable[[Any], Any]
SemanticValidator = Callable[[Any], None]
LegacyErrorTranslator = Callable[[Exception], ConstructionValidationError | None]


@dataclass(frozen=True)
class SchemaAdapter:
    """One closed serialized contract with three stage-specific callbacks."""

    artifact_type: str
    representation: ArtifactRepresentation
    versions: ArtifactVersionSet
    construct: ArtifactConstructor
    allowed_fields: frozenset[str] | None = None
    required_fields: frozenset[str] = frozenset()
    validate_serialized: SerializedValidator | None = None
    validate_semantics: SemanticValidator | None = None
    additional_fields_policy: AdditionalFieldsPolicy = AdditionalFieldsPolicy.FORBID

    @property
    def key(self) -> tuple[str, ArtifactRepresentation, ArtifactVersionSet]:
        return (self.artifact_type, self.representation, self.versions)

    @property
    def identity(self) -> ArtifactIdentity:
        return ArtifactIdentity(
            self.artifact_type,
            self.representation,
            self.versions,
        )

    @property
    def semantic_mode(self) -> SemanticValidationMode:
        return SemanticValidationMode.EXPLICIT

    @property
    def schema_version(self) -> int | None:
        return _unambiguous_schema_version(self.versions)

    def parse_and_validate(
        self,
        document: Any,
        *,
        identity: ArtifactIdentity | None = None,
    ) -> Any:
        resolved_identity = _resolve_adapter_identity(self, document, identity)
        if resolved_identity != self.identity:
            raise SerializedSchemaError(
                "schema.identity.mismatch",
                "serialized identity does not match schema adapter identity",
            )
        _validate_document_fields(self, document)
        if self.validate_serialized is not None:
            _invoke_typed_stage(
                "serialized",
                self.validate_serialized,
                document,
                SerializedSchemaError,
            )
        artifact = _invoke_typed_stage(
            "construction",
            self.construct,
            document,
            ConstructionValidationError,
        )
        if self.validate_semantics is None:
            raise RuntimeError("exact adapter lacks its semantic validator")
        _invoke_typed_stage(
            "semantic",
            self.validate_semantics,
            artifact,
            SemanticValidationError,
        )
        return artifact


@dataclass(frozen=True)
class LegacyCompatibilityBridge:
    """Read-only bridge for one historical composite loader."""

    artifact_type: str
    representation: ArtifactRepresentation
    versions: ArtifactVersionSet
    load_and_validate: ArtifactConstructor
    allowed_fields: frozenset[str] | None = None
    required_fields: frozenset[str] = frozenset()
    validate_serialized: SerializedValidator | None = None
    translate_error: LegacyErrorTranslator | None = None
    additional_fields_policy: AdditionalFieldsPolicy = AdditionalFieldsPolicy.IGNORE

    @property
    def key(self) -> tuple[str, ArtifactRepresentation, ArtifactVersionSet]:
        return (self.artifact_type, self.representation, self.versions)

    @property
    def identity(self) -> ArtifactIdentity:
        return ArtifactIdentity(
            self.artifact_type,
            self.representation,
            self.versions,
        )

    @property
    def semantic_mode(self) -> SemanticValidationMode:
        return SemanticValidationMode.LEGACY_LOADER

    @property
    def schema_version(self) -> int | None:
        return _unambiguous_schema_version(self.versions)

    def parse_and_validate(
        self,
        document: Any,
        *,
        identity: ArtifactIdentity | None = None,
    ) -> Any:
        resolved_identity = _resolve_adapter_identity(self, document, identity)
        if resolved_identity != self.identity:
            raise SerializedSchemaError(
                "schema.identity.mismatch",
                "serialized identity does not match compatibility bridge identity",
            )
        _validate_document_fields(self, document)
        if self.validate_serialized is not None:
            _invoke_typed_stage(
                "serialized",
                self.validate_serialized,
                document,
                SerializedSchemaError,
            )
        try:
            return self.load_and_validate(document)
        except ConstructionValidationError:
            raise
        except ArtifactValidationError as exc:
            raise ValidatorFailureError(
                "validator.stage_contract_violation",
                "legacy loader raised a validation error for the wrong stage",
            ) from exc
        except Exception as exc:
            if self.translate_error is not None:
                translated = self.translate_error(exc)
                if translated is not None:
                    if not isinstance(translated, ConstructionValidationError):
                        raise ValidatorFailureError(
                            "validator.translator_contract_violation",
                            "legacy error translator returned the wrong error type",
                        ) from exc
                    raise translated from exc
            raise


SchemaContract = SchemaAdapter | LegacyCompatibilityBridge
AdapterKey = tuple[str, ArtifactRepresentation, ArtifactVersionSet]


class SchemaAdapterRegistry:
    def __init__(self) -> None:
        self._adapters: dict[AdapterKey, SchemaContract] = {}
        self._frozen = False

    @property
    def frozen(self) -> bool:
        return self._frozen

    @property
    def adapters(self) -> Mapping[AdapterKey, SchemaContract]:
        return MappingProxyType(self._adapters)

    @property
    def artifact_types(self) -> frozenset[str]:
        return frozenset(adapter.artifact_type for adapter in self._adapters.values())

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
    versions: ArtifactVersionSet,
    *,
    registry: SchemaAdapterRegistry | None = None,
) -> SchemaContract | None:
    if not isinstance(versions, ArtifactVersionSet):
        raise TypeError("versions must be ArtifactVersionSet")
    registry = registry or _BUILTIN_SCHEMA_ADAPTERS
    return registry.get((artifact_type, representation, versions))


class RepresentationReader(ABC):
    representation: ArtifactRepresentation
    identifies_representation: bool = True

    @abstractmethod
    def open(
        self,
        path: Path,
    ) -> AbstractContextManager[OpenedRepresentation]:
        """Open once, probe, and retain the resource through validation."""


class RepresentationReaderRegistry:
    def __init__(self) -> None:
        self._readers: dict[ArtifactRepresentation, RepresentationReader] = {}
        self._frozen = False

    @property
    def frozen(self) -> bool:
        return self._frozen

    @property
    def readers(self) -> tuple[RepresentationReader, ...]:
        return tuple(self._readers.values())

    def register(self, reader: RepresentationReader) -> None:
        if self._frozen:
            raise RuntimeError("representation reader registry is frozen")
        if not isinstance(reader, RepresentationReader):
            raise TypeError("reader must implement RepresentationReader")
        if not isinstance(reader.representation, ArtifactRepresentation):
            raise ValueError("reader representation must be ArtifactRepresentation")
        if reader.representation in self._readers:
            raise ValueError(f"reader already registered for {reader.representation.value}")
        self._readers[reader.representation] = reader

    @contextmanager
    def open(self, path: Path) -> Iterator[OpenedRepresentation]:
        representation = _dispatch_representation(path)
        reader = self._readers.get(representation)
        if reader is None:
            raise UnsupportedRepresentationError(
                "representation.reader_not_registered",
                f"{representation.value} representation is recognized but unsupported",
                representation=representation,
            )
        with reader.open(path) as opened:
            _validate_opened_reader(reader, opened)
            _raise_probe_failure(opened)
            yield opened

    def freeze(self) -> None:
        self._frozen = True


def _validate_opened_reader(
    reader: RepresentationReader, opened: OpenedRepresentation
) -> None:
    if not isinstance(opened, OpenedRepresentation):
        raise ValidatorFailureError(
            "validator.reader_contract_violation",
            "reader did not return OpenedRepresentation",
        )
    if not isinstance(opened.representation, ArtifactRepresentation):
        raise ValidatorFailureError(
            "validator.reader_contract_violation",
            "reader returned an invalid representation",
        )
    if opened.representation is not reader.representation:
        raise ValidatorFailureError(
            "validator.reader_contract_violation",
            "reader returned a different representation",
        )
    if not isinstance(opened.probe, ProbeResult):
        raise ValidatorFailureError(
            "validator.reader_contract_violation",
            "reader returned an invalid probe result",
        )
    if not callable(opened.parse_representation):
        raise ValidatorFailureError(
            "validator.reader_contract_violation",
            "reader returned an invalid parse callback",
        )
    return opened


def _raise_probe_failure(opened: OpenedRepresentation) -> None:
    if opened.probe.outcome is ProbeOutcome.MATCH:
        return
    if opened.probe.outcome is ProbeOutcome.NO_MATCH:
        raise UnsupportedRepresentationError(
            "representation.not_detected",
            "validator_not_implemented: artifact is not a supported representation",
            representation=opened.representation,
        )
    error_type = (
        ReaderLimitError
        if opened.probe.outcome is ProbeOutcome.UNSUPPORTED_LIMIT
        else UnsupportedRepresentationError
        if opened.probe.outcome is ProbeOutcome.UNSUPPORTED_CAPABILITY
        else RepresentationUnreadableError
    )
    raise error_type(
        opened.probe.reason_code or "representation.not_detected",
        opened.probe.message or "artifact representation could not be read",
        representation=opened.representation,
    )


def _dispatch_representation(path: Path) -> ArtifactRepresentation:
    """Use stable project format rules; readers never compete for a path."""
    if path.is_dir():
        return ArtifactRepresentation.BUNDLE
    try:
        with path.open("rb") as stream:
            stream.seek(0, 2)
            size = stream.tell()
            offset = 0
            while offset <= size:
                stream.seek(offset)
                if stream.read(len(_HDF5_SIGNATURE)) == _HDF5_SIGNATURE:
                    return ArtifactRepresentation.HDF5
                offset = 512 if offset == 0 else offset * 2
    except OSError as exc:
        raise RepresentationUnreadableError(
            "location.unreadable", "artifact location could not be read"
        ) from exc
    return ArtifactRepresentation.JSON


def register_representation_reader(
    reader: RepresentationReader,
    *,
    registry: RepresentationReaderRegistry,
) -> None:
    registry.register(reader)


_HDF5_SIGNATURE = b"\x89HDF\r\n\x1a\n"
_MAX_JSON_INTEGER_DIGITS = 4300
_MAX_JSON_DECIMAL_DIGITS = 4300
_MAX_JSON_DECIMAL_EXPONENT = 1_000_000
_MAX_JSON_BYTES = 16 * 1024 * 1024
_JSON_PROBE_CHUNK_BYTES = 4096
_JSON_START_BYTES = frozenset(b'{["-0123456789tfnNI')
_JSON_WHITESPACE = b" \t\r\n"


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


def _parse_json_decimal(token: str) -> Decimal:
    try:
        value = Decimal(token)
    except InvalidOperation as exc:
        raise RepresentationParseError(
            "representation.json.number_invalid",
            "JSON decimal token is invalid",
        ) from exc
    digits = len(value.as_tuple().digits)
    exponent = value.as_tuple().exponent
    if digits > _MAX_JSON_DECIMAL_DIGITS or abs(exponent) > _MAX_JSON_DECIMAL_EXPONENT:
        raise ReaderLimitError(
            "reader_limit.json_decimal_range",
            "JSON decimal literal exceeds this reader's token limits",
        )
    return value


def _reject_json_constant(token: str) -> Any:
    raise RepresentationParseError(
        "representation.json.nonstandard_constant",
        f"non-standard JSON numeric constant {token!r} is not allowed",
    )


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise RepresentationStructureError(
                "representation.json.duplicate_key",
                f"duplicate JSON object key {key!r}",
            )
        result[key] = value
    return result


def _parse_json_text_value(text: str) -> Any:
    try:
        value = json.loads(
            text,
            parse_int=_parse_json_integer,
            parse_float=_parse_json_decimal,
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
        raise RepresentationParseError(
            "representation.json.parse_error",
            "artifact JSON could not be parsed",
        ) from exc
    return value


def parse_json_text_mapping(text: str) -> dict[str, Any]:
    value = _parse_json_text_value(text)
    if not isinstance(value, dict):
        raise IdentityValidationError(
            "representation.json.root_invalid",
            "artifact JSON root must be a mapping",
        )
    return value


def _parsed_json_representation(value: Any) -> ParsedRepresentation:
    if not isinstance(value, dict):
        raise IdentityValidationError(
            "representation.json.root_invalid",
            "artifact JSON root must be a mapping",
        )
    return ParsedRepresentation(_identity_from_json_mapping(value), value)


def parse_json_mapping(path: str | Path) -> dict[str, Any]:
    reader = _JSONRepresentationReader()
    with reader.open(Path(path)) as opened:
        if opened.probe.outcome is ProbeOutcome.UNREADABLE:
            raise RepresentationUnreadableError(
                opened.probe.reason_code or "location.unreadable",
                opened.probe.message or "artifact location could not be read",
            )
        if opened.probe.outcome is ProbeOutcome.UNSUPPORTED_LIMIT:
            raise ReaderLimitError(
                opened.probe.reason_code or "reader_limit.json_bytes",
                opened.probe.message or "JSON representation exceeds reader limits",
            )
        if opened.probe.outcome is not ProbeOutcome.MATCH:
            raise RepresentationUnreadableError(
                "representation.json.not_detected",
                "artifact location is not a JSON object representation",
            )
        parsed = opened.parse()
        return parsed.document


def _unsupported_hdf_parse() -> ParsedRepresentation:
    raise UnsupportedRepresentationError(
        "representation.adapter_not_registered",
        "HDF5 representation reader is not implemented",
    )


class _HDF5ProbeReader(RepresentationReader):
    representation = ArtifactRepresentation.HDF5
    identifies_representation = False

    @contextmanager
    def open(self, path: Path) -> Iterator[OpenedRepresentation]:
        # Dispatch already verified the signature, including an HDF5 user block.
        yield OpenedRepresentation(
            self.representation,
            ProbeResult(
                ProbeOutcome.UNSUPPORTED_CAPABILITY,
                "representation.reader_not_registered",
                "HDF5 representation is recognized but its reader is not installed",
                ProbeFailureScope.READER_LOCAL,
            ),
            _unsupported_hdf_parse,
        )


class _JSONRepresentationReader(RepresentationReader):
    representation = ArtifactRepresentation.JSON

    @contextmanager
    def open(self, path: Path) -> Iterator[OpenedRepresentation]:
        if not path.is_file():
            yield OpenedRepresentation(
                self.representation,
                ProbeResult.no_match(),
                lambda: _raise_not_detected_json(),
            )
            return
        try:
            stream = path.open("rb")
        except OSError:
            yield OpenedRepresentation(
                self.representation,
                ProbeResult(
                    ProbeOutcome.UNREADABLE,
                    "representation.json.probe_unreadable",
                    "JSON probe could not read artifact location",
                    ProbeFailureScope.LOCATION_GLOBAL,
                ),
                lambda: _raise_not_detected_json(),
            )
            return
        with stream:
            try:
                raw = bytearray()
                looks_json: bool | None = None
                probe_scanned = 0
                bom_decided = False
                while len(raw) <= _MAX_JSON_BYTES:
                    chunk = stream.read(
                        min(
                            _JSON_PROBE_CHUNK_BYTES,
                            _MAX_JSON_BYTES + 1 - len(raw),
                        )
                    )
                    if not chunk:
                        break
                    raw.extend(chunk)
                    if not bom_decided:
                        if len(raw) < 3 and b"\xef\xbb\xbf".startswith(raw):
                            continue
                        probe_scanned = 3 if raw.startswith(b"\xef\xbb\xbf") else 0
                        bom_decided = True
                    first = next(
                        (
                            value
                            for value in raw[probe_scanned:]
                            if value not in _JSON_WHITESPACE
                        ),
                        None,
                    )
                    probe_scanned = len(raw)
                    if first is None:
                        continue
                    looks_json = first in _JSON_START_BYTES
                    break
                if looks_json is False:
                    yield OpenedRepresentation(
                        self.representation,
                        ProbeResult.no_match(),
                        lambda: _raise_not_detected_json(),
                    )
                    return
                if looks_json:
                    while len(raw) <= _MAX_JSON_BYTES:
                        chunk = stream.read(
                            min(
                                _JSON_PROBE_CHUNK_BYTES,
                                _MAX_JSON_BYTES + 1 - len(raw),
                            )
                        )
                        if not chunk:
                            break
                        raw.extend(chunk)
            except OSError:
                yield OpenedRepresentation(
                    self.representation,
                    ProbeResult(
                        ProbeOutcome.UNREADABLE,
                        "representation.json.probe_unreadable",
                        "JSON probe could not read artifact location",
                        ProbeFailureScope.LOCATION_GLOBAL,
                    ),
                    lambda: _raise_not_detected_json(),
                )
                return
            if len(raw) > _MAX_JSON_BYTES:
                if looks_json or looks_json is None:
                    yield OpenedRepresentation(
                        self.representation,
                        ProbeResult(
                            ProbeOutcome.UNSUPPORTED_LIMIT,
                            "reader_limit.json_bytes",
                            "JSON representation exceeds this reader's byte limit",
                            ProbeFailureScope.READER_LOCAL,
                        ),
                        lambda: _raise_not_detected_json(),
                    )
                else:
                    yield OpenedRepresentation(
                        self.representation,
                        ProbeResult.no_match(),
                        lambda: _raise_not_detected_json(),
                    )
                return
            if looks_json is None:
                yield OpenedRepresentation(
                    self.representation,
                    ProbeResult.no_match(),
                    lambda: _raise_not_detected_json(),
                )
                return
            try:
                text = bytes(raw).decode("utf-8-sig")
            except UnicodeError:
                yield OpenedRepresentation(
                    self.representation,
                    ProbeResult(
                        ProbeOutcome.UNREADABLE,
                        "representation.json.not_utf8",
                        "artifact JSON is not valid UTF-8",
                        ProbeFailureScope.READER_LOCAL,
                    ),
                    lambda: _raise_not_detected_json(),
                )
                return
            try:
                parsed_value = _parse_json_text_value(text)
            except ReaderLimitError as exc:
                yield OpenedRepresentation(
                    self.representation,
                    ProbeResult(
                        ProbeOutcome.UNSUPPORTED_LIMIT,
                        exc.reason_code,
                        exc.message,
                        ProbeFailureScope.READER_LOCAL,
                    ),
                    lambda: _raise_not_detected_json(),
                )
                return
            except RepresentationParseError as exc:
                yield OpenedRepresentation(
                    self.representation,
                    ProbeResult(
                        ProbeOutcome.UNREADABLE,
                        exc.reason_code,
                        exc.message,
                        ProbeFailureScope.READER_LOCAL,
                    ),
                    lambda: _raise_not_detected_json(),
                )
                return

            yield OpenedRepresentation(
                self.representation,
                ProbeResult.match(),
                lambda: _parsed_json_representation(parsed_value),
            )


def _raise_not_detected_json() -> ParsedRepresentation:
    raise UnsupportedRepresentationError(
        "representation.json.not_detected",
        "artifact location is not a JSON object representation",
    )


def build_default_representation_reader_registry() -> RepresentationReaderRegistry:
    registry = RepresentationReaderRegistry()
    registry.register(_HDF5ProbeReader())
    registry.register(_JSONRepresentationReader())
    registry.freeze()
    return registry


_BUILTIN_REPRESENTATION_READERS: RepresentationReaderRegistry
_BUILTIN_SCHEMA_ADAPTERS: SchemaAdapterRegistry


def _bootstrap_builtin_validation() -> None:
    """Build and freeze the built-in catalog at import time, failing fast."""
    global _BUILTIN_SCHEMA_ADAPTERS
    from .adapter_catalog import build_builtin_schema_registry

    _BUILTIN_SCHEMA_ADAPTERS = build_builtin_schema_registry()



def check_validity(
    artifact_type: str,
    path: str | Path,
    *,
    adapter_registry: SchemaAdapterRegistry | None = None,
    reader_registry: RepresentationReaderRegistry | None = None,
) -> ValidityResult:
    """Compose one independently identifying reader and one exact contract."""
    requested_artifact_type = (
        artifact_type if isinstance(artifact_type, str) else repr(artifact_type)
    )
    if not _is_artifact_type(artifact_type):
        return _result(
            requested_artifact_type,
            ValidityOutcome.UNSUPPORTED,
            reason_codes=("artifact_type.invalid",),
            errors=("artifact_type must be a canonical identifier",),
        )
    artifact_path = Path(path)
    if not artifact_path.exists():
        return _result(
            requested_artifact_type,
            ValidityOutcome.UNREADABLE,
            reason_codes=("location.missing",),
            errors=("artifact location does not exist",),
        )

    adapters = adapter_registry or _BUILTIN_SCHEMA_ADAPTERS
    readers = reader_registry or _BUILTIN_REPRESENTATION_READERS
    identified_identity: ArtifactIdentity | None = None
    identified_representation: ArtifactRepresentation | None = None
    try:
        with readers.open(artifact_path) as opened:
            identified_representation = opened.representation
            parsed = _parse_opened_representation(opened)
            identified_identity = parsed.identity
            if parsed.identity.representation is not identified_representation:
                raise ValidatorFailureError(
                    "validator.reader_contract_violation",
                    "reader returned an identity for a different representation",
                )
            if parsed.identity.artifact_type != artifact_type:
                raise IdentityValidationError(
                    "schema.artifact_type.mismatch",
                    f"artifact_type mismatch: expected {artifact_type!r}, "
                    f"found {parsed.identity.artifact_type!r}",
                )
            adapter = adapters.get(
                (
                    artifact_type,
                    parsed.identity.representation,
                    parsed.identity.versions,
                )
            )
            if adapter is None:
                raise _missing_adapter_error(
                    artifact_type,
                    parsed.identity.representation,
                    parsed.identity.versions,
                    adapters,
                )
            adapter.parse_and_validate(parsed.document, identity=parsed.identity)
            return _result(
                requested_artifact_type,
                ValidityOutcome.VALID,
                identified_identity=identified_identity,
            )
    except ArtifactValidationError as exc:
        if identified_representation is None and exc.representation is not None:
            identified_representation = exc.representation
        return _result(
            requested_artifact_type,
            exc.outcome,
            identified_identity=identified_identity,
            identified_representation=(
                None if identified_identity is not None else identified_representation
            ),
            reason_codes=(exc.reason_code,),
            errors=(exc.message,),
        )
    except Exception as exc:  # noqa: BLE001 - undeclared programming failure.
        return _result(
            requested_artifact_type,
            ValidityOutcome.VALIDATOR_FAILED,
            identified_identity=identified_identity,
            identified_representation=(
                None if identified_identity is not None else identified_representation
            ),
            reason_codes=("validator.internal_failure",),
            errors=(f"validator raised {type(exc).__name__}",),
        )


def _result(
    requested_artifact_type: str,
    outcome: ValidityOutcome,
    *,
    identified_identity: ArtifactIdentity | None = None,
    identified_representation: ArtifactRepresentation | None = None,
    reason_codes: tuple[str, ...] = (),
    errors: tuple[str, ...] = (),
    warnings: tuple[str, ...] = (),
) -> ValidityResult:
    return ValidityResult(
        requested_artifact_type=requested_artifact_type,
        outcome=outcome,
        identified_identity=identified_identity,
        identified_representation=identified_representation,
        reason_codes=reason_codes,
        errors=errors,
        warnings=warnings,
    )


def _invoke_typed_stage(
    stage: str,
    callback: Callable[[Any], Any],
    value: Any,
    expected_error: type[ArtifactValidationError],
) -> Any:
    try:
        return callback(value)
    except expected_error:
        raise
    except ArtifactValidationError as exc:
        raise ValidatorFailureError(
            "validator.stage_contract_violation",
            f"{stage} callback raised a validation error for the wrong stage",
        ) from exc


def _parse_opened_representation(
    opened: OpenedRepresentation,
) -> ParsedRepresentation:
    try:
        parsed = opened.parse()
    except ParsedRepresentationContractError as exc:
        raise ValidatorFailureError(
            "validator.reader_identity_contract_violation",
            "reader returned an invalid parsed identity",
        ) from exc
    except (
        RepresentationUnreadableError,
        RepresentationParseError,
        ReaderLimitError,
        IdentityValidationError,
    ):
        raise
    except ArtifactValidationError as exc:
        raise ValidatorFailureError(
            "validator.reader_stage_contract_violation",
            "reader parse/identity raised a validation error for the wrong stage",
        ) from exc
    if not isinstance(parsed, ParsedRepresentation):
        raise ValidatorFailureError(
            "validator.reader_contract_violation",
            "reader parse did not return ParsedRepresentation",
        )
    return parsed


def _validate_schema_contract(adapter: SchemaContract) -> None:
    _require_artifact_type(adapter.artifact_type)
    adapter.identity
    if type(adapter.allowed_fields) not in (frozenset, type(None)):
        raise ValueError("adapter allowed_fields must be frozenset or None")
    if type(adapter.required_fields) is not frozenset:
        raise ValueError("adapter required_fields must be frozenset")
    for name, fields in (
        ("allowed_fields", adapter.allowed_fields or frozenset()),
        ("required_fields", adapter.required_fields),
    ):
        if any(not isinstance(field_name, str) for field_name in fields):
            raise ValueError(f"adapter {name} must contain only strings")
    if (
        adapter.allowed_fields is not None
        and not adapter.required_fields <= adapter.allowed_fields
    ):
        raise ValueError("adapter required_fields must be a subset of allowed_fields")
    if not isinstance(adapter.additional_fields_policy, AdditionalFieldsPolicy):
        raise ValueError("adapter additional_fields_policy is invalid")
    if adapter.representation is ArtifactRepresentation.JSON:
        if adapter.allowed_fields is None:
            raise ValueError("JSON contracts must declare allowed_fields")
        identity_fields = frozenset({"artifact_type", "schema_version"})
        if not identity_fields <= adapter.required_fields:
            raise ValueError("JSON contracts must require serialized identity fields")
    if isinstance(adapter, SchemaAdapter):
        if adapter.additional_fields_policy is not AdditionalFieldsPolicy.FORBID:
            raise ValueError("exact schema adapter must forbid additional fields")
        if not callable(adapter.construct):
            raise ValueError("exact schema adapter construct must be callable")
        if not callable(adapter.validate_semantics):
            raise ValueError("exact schema adapter requires validate_semantics")
    else:
        if not callable(adapter.load_and_validate):
            raise ValueError("legacy bridge load_and_validate must be callable")
        if adapter.translate_error is not None and not callable(
            adapter.translate_error
        ):
            raise ValueError("legacy bridge translate_error must be callable")
    if adapter.validate_serialized is not None and not callable(
        adapter.validate_serialized
    ):
        raise ValueError("validate_serialized must be callable")


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


def _resolve_adapter_identity(
    adapter: SchemaContract,
    document: Any,
    identity: ArtifactIdentity | None,
) -> ArtifactIdentity:
    if adapter.representation is ArtifactRepresentation.JSON:
        embedded_identity = _identity_from_json_mapping(document)
        if identity is not None and identity != embedded_identity:
            raise ValidatorFailureError(
                "validator.reader_identity_contract_violation",
                "reader identity disagrees with serialized JSON identity",
            )
        return embedded_identity
    if identity is not None:
        if not isinstance(identity, ArtifactIdentity):
            raise ValidatorFailureError(
                "validator.adapter_identity_invalid",
                "adapter identity argument must be ArtifactIdentity",
            )
        return identity
    raise ValidatorFailureError(
        "validator.adapter_identity_required",
        "non-JSON schema adapter invocation requires explicit identity",
    )


def _identity_from_json_mapping(document: Any) -> ArtifactIdentity:
    if not isinstance(document, Mapping):
        raise IdentityValidationError(
            "schema.identity.missing",
            "JSON identity requires a mapping document",
        )
    artifact_type = document.get("artifact_type")
    if not _is_artifact_type(artifact_type):
        raise IdentityValidationError(
            "schema.artifact_type.missing",
            "artifact_type is required and must be a canonical identifier",
        )
    if "schema_version" not in document:
        raise LegacyUnversionedValidationError(
            "schema.version.missing",
            "legacy_unversioned: serialized artifact lacks schema_version",
        )
    version = document["schema_version"]
    if type(version) is not int or version < 1:
        raise IdentityValidationError(
            "schema.version.invalid",
            "schema_version must be a positive integer",
        )
    return ArtifactIdentity(
        artifact_type,
        ArtifactRepresentation.JSON,
        ArtifactVersionSet(manifest=version),
    )


def _unambiguous_schema_version(versions: ArtifactVersionSet) -> int | None:
    populated = [version for version in versions.values() if version is not None]
    if not populated:
        return None
    if len(populated) != 1:
        raise ValueError("multi-axis identity has no unambiguous scalar schema_version")
    return populated[0]


def _missing_adapter_error(
    artifact_type: str,
    representation: ArtifactRepresentation,
    versions: ArtifactVersionSet,
    registry: SchemaAdapterRegistry,
) -> UnsupportedRepresentationError:
    known_types = _KNOWN_ARTIFACT_TYPES | registry.artifact_types
    same_type = [
        adapter
        for adapter in registry.values()
        if adapter.artifact_type == artifact_type
    ]
    if artifact_type not in known_types:
        return UnsupportedRepresentationError(
            "artifact_type.unknown",
            "artifact type is not part of the known identity vocabulary",
        )
    if not same_type:
        return UnsupportedRepresentationError(
            "schema.adapter_not_registered",
            "validator_not_implemented: known artifact type has no schema adapter",
        )
    same_representation = [
        adapter for adapter in same_type if adapter.representation is representation
    ]
    if not same_representation:
        return UnsupportedRepresentationError(
            "representation.adapter_not_registered",
            "validator_not_implemented: no schema adapter is registered for this "
            "representation",
        )
    for axis in ("manifest", "payload", "bundle"):
        requested = getattr(versions, axis)
        registered = sorted(
            {
                value
                for adapter in same_representation
                if (value := getattr(adapter.versions, axis)) is not None
            }
        )
        if requested is None and not registered:
            continue
        if requested is None or not registered:
            suffix = "unsupported"
        elif requested < registered[0]:
            suffix = "older"
        elif requested > registered[-1]:
            suffix = "newer"
        elif requested not in registered:
            suffix = "unsupported"
        else:
            continue
        return UnsupportedRepresentationError(
            f"schema.{axis}_version.{suffix}",
            f"requested {axis} version is {suffix}; no schema adapter matches "
            "that version axis",
        )
    return UnsupportedRepresentationError(
        "schema.version_set.unsupported",
        "no schema adapter is registered for this complete version identity",
    )


def _is_artifact_type(value: Any) -> bool:
    return isinstance(value, str) and _ARTIFACT_TYPE_RE.fullmatch(value) is not None


def _require_artifact_type(value: Any) -> None:
    if not _is_artifact_type(value):
        raise ValueError("artifact_type must be a canonical identifier")


def _require_reason_code(value: Any) -> None:
    if not isinstance(value, str) or _REASON_CODE_RE.fullmatch(value) is None:
        raise ValueError("reason code must use the stable dotted identifier grammar")


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


_BUILTIN_REPRESENTATION_READERS = build_default_representation_reader_registry()
_bootstrap_builtin_validation()


__all__ = [
    "AdditionalFieldsPolicy",
    "ArtifactIdentity",
    "ArtifactRepresentation",
    "ArtifactValidationError",
    "ArtifactVersionSet",
    "ConstructionValidationError",
    "LegacyCompatibilityBridge",
    "LegacyUnversionedValidationError",
    "IdentityValidationError",
    "OpenedRepresentation",
    "ParsedRepresentation",
    "ProbeOutcome",
    "ProbeFailureScope",
    "ProbeResult",
    "ReaderLimitError",
    "RepresentationReader",
    "RepresentationReaderRegistry",
    "RepresentationParseError",
    "RepresentationStructureError",
    "RepresentationUnreadableError",
    "SchemaAdapter",
    "SchemaAdapterRegistry",
    "SemanticValidationError",
    "SemanticValidationMode",
    "SerializedSchemaError",
    "UnsupportedRepresentationError",
    "ValidatorFailureError",
    "ValidityOutcome",
    "ValidityResult",
    "build_default_representation_reader_registry",
    "check_validity",
    "get_schema_adapter",
    "parse_json_mapping",
    "parse_json_text_mapping",
    "register_legacy_compatibility_bridge",
    "register_representation_reader",
    "register_schema_adapter",
]
