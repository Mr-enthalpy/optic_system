from __future__ import annotations

"""Representation-aware local artifact validation.

The registry in this module is keyed by the complete serialized identity:
``(artifact_type, representation, schema_version)``.  An adapter owns one
stable version contract.  Parsing, serialized-schema validation, typed
construction, and semantic validation form one non-recursive pipeline.

Artifact-specific schema revisions and migrations are intentionally defined
outside this foundation module.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum
import json
from pathlib import Path
import re
from typing import Any

from tasks.artifact_versioning import (
    CURRENT_SCHEMA_VERSIONS,
    LegacyUnversionedArtifactError,
    NewerSchemaVersionError,
    OlderSchemaVersionError,
    SchemaCompatibilityError,
    read_schema_version,
)


class ArtifactRepresentation(str, Enum):
    JSON = "json"
    YAML_IMPORT = "yaml_import"
    HDF5 = "hdf5"
    BUNDLE = "bundle"


class ValidityOutcome(str, Enum):
    VALID = "valid"
    INVALID = "invalid"
    UNSUPPORTED = "unsupported"
    LEGACY_UNVERSIONED = "legacy_unversioned"
    UNREADABLE = "unreadable"


@dataclass(frozen=True)
class ValidityResult:
    """Path-free, machine-readable result for one serialized representation."""

    artifact_type: str
    outcome: ValidityOutcome
    representation: ArtifactRepresentation | None = None
    schema_version: int | None = None
    reason_codes: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.outcome is ValidityOutcome.VALID


class ArtifactValidationError(ValueError):
    """Base error carrying stable outcome and reason-code classification."""

    outcome = ValidityOutcome.INVALID

    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code
        self.message = message


class SerializedSchemaError(ArtifactValidationError):
    pass


class RepresentationUnreadableError(ArtifactValidationError):
    outcome = ValidityOutcome.UNREADABLE


class UnsupportedRepresentationError(ArtifactValidationError):
    outcome = ValidityOutcome.UNSUPPORTED


class ReaderLimitError(ArtifactValidationError):
    """The local reader deliberately does not support this resource scale."""

    outcome = ValidityOutcome.UNSUPPORTED


SerializedValidator = Callable[[Mapping[str, Any]], None]
ArtifactConstructor = Callable[[Mapping[str, Any]], Any]
SemanticValidator = Callable[[Any], None]


@dataclass(frozen=True)
class SchemaAdapter:
    """One exact serialized schema contract.

    ``construct`` must construct from the already validated mapping; it must
    not parse the representation again.  New adapters should keep semantic
    validation in ``validate_semantics``.  The two v1 profile registrations
    below are compatibility bridges for pre-existing classes whose historical
    ``from_dict`` methods already run semantic validation.
    """

    artifact_type: str
    representation: ArtifactRepresentation
    schema_version: int
    allowed_fields: frozenset[str]
    required_fields: frozenset[str]
    construct: ArtifactConstructor
    validate_serialized: SerializedValidator | None = None
    validate_semantics: SemanticValidator | None = None
    migration_target: int | None = None
    constructor_validates_semantics: bool = False

    @property
    def key(self) -> tuple[str, ArtifactRepresentation, int]:
        return (self.artifact_type, self.representation, self.schema_version)

    def parse_and_validate(self, mapping: Mapping[str, Any]) -> Any:
        """Run the version contract exactly once over an already parsed mapping."""
        non_string = [key for key in mapping if not isinstance(key, str)]
        if non_string:
            raise SerializedSchemaError(
                "schema.field.name_invalid",
                "serialized field names must be strings",
            )
        unknown = sorted(set(mapping) - self.allowed_fields)
        if unknown:
            raise SerializedSchemaError(
                "schema.field.unknown",
                f"unknown serialized field(s): {', '.join(unknown)}",
            )
        missing = sorted(self.required_fields - set(mapping))
        if missing:
            raise SerializedSchemaError(
                "schema.field.missing",
                f"missing required serialized field(s): {', '.join(missing)}",
            )
        if self.validate_serialized is not None:
            self.validate_serialized(mapping)
        artifact = self.construct(mapping)
        if self.validate_semantics is not None:
            if self.constructor_validates_semantics:
                raise RuntimeError(
                    "adapter cannot validate semantics in both constructor and "
                    "validate_semantics"
                )
            self.validate_semantics(artifact)
        return artifact


AdapterKey = tuple[str, ArtifactRepresentation, int]
SCHEMA_ADAPTER_REGISTRY: dict[AdapterKey, SchemaAdapter] = {}


def register_schema_adapter(adapter: SchemaAdapter, *, replace: bool = False) -> None:
    """Register one adapter without silently replacing an existing contract."""
    key = adapter.key
    if key in SCHEMA_ADAPTER_REGISTRY and not replace:
        raise ValueError(f"schema adapter is already registered for {key!r}")
    if adapter.schema_version < 1:
        raise ValueError("schema adapter version must be positive")
    if not adapter.required_fields <= adapter.allowed_fields:
        raise ValueError("adapter required_fields must be a subset of allowed_fields")
    SCHEMA_ADAPTER_REGISTRY[key] = adapter


def get_schema_adapter(
    artifact_type: str,
    representation: ArtifactRepresentation,
    schema_version: int,
) -> SchemaAdapter | None:
    return SCHEMA_ADAPTER_REGISTRY.get(
        (artifact_type, representation, schema_version)
    )


_HDF5_SIGNATURE = b"\x89HDF\r\n\x1a\n"
_MAX_JSON_INTEGER_DIGITS = 4300


def _detect_representation(path: Path) -> ArtifactRepresentation:
    try:
        with path.open("rb") as stream:
            prefix = stream.read(len(_HDF5_SIGNATURE))
    except OSError as exc:
        raise RepresentationUnreadableError(
            "location.unreadable",
            "artifact location could not be read",
        ) from exc
    if prefix == _HDF5_SIGNATURE:
        return ArtifactRepresentation.HDF5
    return ArtifactRepresentation.JSON


def _parse_json_integer(token: str) -> int:
    digits = token[1:] if token.startswith("-") else token
    if len(digits) > _MAX_JSON_INTEGER_DIGITS:
        raise ReaderLimitError(
            "reader_limit.json_integer_digits",
            "JSON integer literal exceeds this reader's supported digit limit",
        )
    return int(token)


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


def _parse_json_mapping(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
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
            object_pairs_hook=_reject_duplicate_pairs,
        )
    except ArtifactValidationError:
        raise
    except json.JSONDecodeError as exc:
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


def _result(
    artifact_type: str,
    outcome: ValidityOutcome,
    *,
    representation: ArtifactRepresentation | None = None,
    schema_version: int | None = None,
    reason_codes: tuple[str, ...] = (),
    errors: tuple[str, ...] = (),
    warnings: tuple[str, ...] = (),
) -> ValidityResult:
    return ValidityResult(
        artifact_type=artifact_type,
        outcome=outcome,
        representation=representation,
        schema_version=schema_version,
        reason_codes=reason_codes,
        errors=errors,
        warnings=warnings,
    )


def check_validity(artifact_type: str, path: str | Path) -> ValidityResult:
    """Validate through the adapter selected by type, representation, and version."""
    if (
        not isinstance(artifact_type, str)
        or not artifact_type
        or artifact_type != artifact_type.strip()
        or artifact_type not in CURRENT_SCHEMA_VERSIONS
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

    representation: ArtifactRepresentation | None = None
    schema_version: int | None = None
    try:
        representation = _detect_representation(artifact_path)
        has_representation_adapter = any(
            key[0] == artifact_type and key[1] is representation
            for key in SCHEMA_ADAPTER_REGISTRY
        )
        if not has_representation_adapter:
            raise UnsupportedRepresentationError(
                "representation.adapter_not_registered",
                "validator_not_implemented: representation adapter is not registered",
            )
        if representation is not ArtifactRepresentation.JSON:
            raise UnsupportedRepresentationError(
                "representation.adapter_not_registered",
                "validator_not_implemented: representation adapter is not registered",
            )
        mapping = _parse_json_mapping(artifact_path)
        found_type = mapping.get("artifact_type")
        if not isinstance(found_type, str) or not found_type:
            raise SerializedSchemaError(
                "schema.artifact_type.missing",
                "artifact_type is required and must be a non-empty string",
            )
        if found_type != artifact_type:
            raise SerializedSchemaError(
                "schema.artifact_type.mismatch",
                f"artifact_type mismatch: expected {artifact_type!r}, "
                f"found {found_type!r}",
            )
        schema_version = read_schema_version(mapping, artifact_type)
        adapter = get_schema_adapter(
            artifact_type,
            representation,
            schema_version,
        )
        if adapter is None:
            raise UnsupportedRepresentationError(
                "schema.adapter_not_registered",
                "validator_not_implemented: no adapter is registered for the "
                "serialized schema identity",
            )
        adapter.parse_and_validate(mapping)
        return _result(
            artifact_type,
            ValidityOutcome.VALID,
            representation=representation,
            schema_version=schema_version,
        )
    except LegacyUnversionedArtifactError:
        return _result(
            artifact_type,
            ValidityOutcome.LEGACY_UNVERSIONED,
            representation=representation,
            reason_codes=("schema.version.missing",),
            errors=("legacy_unversioned: serialized artifact lacks schema_version",),
        )
    except NewerSchemaVersionError as exc:
        return _result(
            artifact_type,
            ValidityOutcome.UNSUPPORTED,
            representation=representation,
            schema_version=exc.version,
            reason_codes=("schema.version.newer",),
            errors=(_safe_message(exc, "schema version requires a newer reader"),),
        )
    except OlderSchemaVersionError as exc:
        return _result(
            artifact_type,
            ValidityOutcome.INVALID,
            representation=representation,
            schema_version=exc.version,
            reason_codes=("schema.version.older",),
            errors=(_safe_message(exc, "schema version is outside the readable window"),),
        )
    except SchemaCompatibilityError as exc:
        return _result(
            artifact_type,
            ValidityOutcome.INVALID,
            representation=representation,
            schema_version=schema_version,
            reason_codes=("schema.version.invalid",),
            errors=(_safe_message(exc, "schema_version is invalid"),),
        )
    except ArtifactValidationError as exc:
        return _result(
            artifact_type,
            exc.outcome,
            representation=representation,
            schema_version=schema_version,
            reason_codes=(exc.reason_code,),
            errors=(exc.message,),
        )
    except Exception as exc:  # noqa: BLE001 - programming failures fail closed.
        return _result(
            artifact_type,
            ValidityOutcome.UNSUPPORTED,
            representation=representation,
            schema_version=schema_version,
            reason_codes=("validator.internal_failure",),
            errors=(f"validator raised {type(exc).__name__}",),
        )


def _safe_message(exc: Exception, fallback: str) -> str:
    message = str(exc).strip()
    if not message or len(message) > 512:
        return fallback
    if re.search(r"(?<![A-Za-z0-9])(?:[A-Za-z]:[\\/]|\\\\|/)[^\s\"']+", message):
        return fallback
    return message


_CAMERA_PROFILE_V1_FIELDS = frozenset(
    {
        "artifact_type",
        "schema_version",
        "camera_profile_id",
        "profile_family",
        "illumination",
        "lcd_state",
        "valid_for",
        "depends_on",
        "depends_on_pupil_profile_id",
        "camera",
        "per_wavelength",
        "exposure_us",
        "gain_db",
        "peak_pixel",
        "saturation_margin",
        "frames_per_capture",
        "peak_pixel_domain",
        "full_frame_peak_pixel",
        "full_frame_saturated_pixel_count",
        "source_raw_capture_file",
        "created_at",
        "software_version",
        "extra",
    }
)
_PUPIL_PROFILE_V1_FIELDS = frozenset(
    {
        "artifact_type",
        "schema_version",
        "pupil_profile_id",
        "lcd_coordinate_convention",
        "lcd_display_index",
        "subpixel_axis",
        "lcd_physical_center",
        "lcd_physical_radius",
        "aperture_window",
        "camera_psf_center",
        "recommended_roi",
        "fit_quality",
        "source_raw_capture_file",
        "created_at",
        "software_version",
        "extra",
    }
)


def _construct_camera_profile_v1(mapping: Mapping[str, Any]) -> Any:
    from tasks.profiles.camera_profile import CameraProfile

    return CameraProfile.from_dict(dict(mapping))


def _construct_pupil_profile_v1(mapping: Mapping[str, Any]) -> Any:
    from tasks.profiles.pupil_profile import PupilProfile

    return PupilProfile.from_dict(dict(mapping))


register_schema_adapter(
    SchemaAdapter(
        artifact_type="camera_profile",
        representation=ArtifactRepresentation.JSON,
        schema_version=1,
        allowed_fields=_CAMERA_PROFILE_V1_FIELDS,
        required_fields=frozenset(
            {
                "artifact_type",
                "schema_version",
                "camera_profile_id",
                "profile_family",
                "illumination",
                "lcd_state",
                "valid_for",
            }
        ),
        construct=_construct_camera_profile_v1,
        constructor_validates_semantics=True,
    )
)
register_schema_adapter(
    SchemaAdapter(
        artifact_type="pupil_profile",
        representation=ArtifactRepresentation.JSON,
        schema_version=1,
        allowed_fields=_PUPIL_PROFILE_V1_FIELDS,
        required_fields=frozenset(
            {
                "artifact_type",
                "schema_version",
                "pupil_profile_id",
                "lcd_coordinate_convention",
                "lcd_display_index",
                "subpixel_axis",
                "lcd_physical_center",
            }
        ),
        construct=_construct_pupil_profile_v1,
        constructor_validates_semantics=True,
    )
)


__all__ = [
    "ArtifactRepresentation",
    "ArtifactValidationError",
    "ReaderLimitError",
    "SCHEMA_ADAPTER_REGISTRY",
    "SchemaAdapter",
    "SerializedSchemaError",
    "ValidityOutcome",
    "ValidityResult",
    "check_validity",
    "get_schema_adapter",
    "register_schema_adapter",
]
