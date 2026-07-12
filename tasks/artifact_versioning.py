from __future__ import annotations

"""Central schema-version registry and validity checking for measured artifacts.

This module is the single source of truth for artifact ``schema_version`` values
and their read compatibility windows. Artifact modules import the light-weight
``emit_schema_version`` / ``read_schema_version`` helpers so that every serialized
artifact carries a round-trippable ``schema_version``. ``check_validity`` composes
schema-compatibility, ``.validate()``, and coordinate-frame checks into a single
data-based validity judgement (never filename-based).

It must not import artifact modules at module import time; loaders are imported
lazily inside ``check_validity`` to avoid circular imports.  This module does
not yet validate artifact bundles or HDF5 payloads; types without an implemented
validator fail closed.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


class SchemaCompatibilityError(ValueError):
    """Raised when an artifact schema_version is outside the readable window."""


class LegacyUnversionedArtifactError(SchemaCompatibilityError):
    """Raised when strict validation encounters an artifact without a version."""


# Current schema version emitted when writing each artifact type.
CURRENT_SCHEMA_VERSIONS: dict[str, int] = {
    "camera_profile": 1,
    "pupil_profile": 1,
    "peak_layout_profile": 1,
    "full_frame_psf_survey": 1,
    "peak_patch_psf_dictionary": 1,
    "sensor_energy_center_profile": 1,
    "peak_support_analysis_report": 1,
    "raw_capture": 2,
}

# Oldest schema version this codebase can still read for each artifact type.
MIN_READABLE_SCHEMA_VERSIONS: dict[str, int] = {
    "camera_profile": 1,
    "pupil_profile": 1,
    "peak_layout_profile": 1,
    "full_frame_psf_survey": 1,
    "peak_patch_psf_dictionary": 1,
    "sensor_energy_center_profile": 1,
    "peak_support_analysis_report": 1,
    "raw_capture": 2,
}


@dataclass(frozen=True)
class SchemaCompat:
    artifact_type: str
    current: int
    min_readable: int


def schema_compat(artifact_type: str) -> SchemaCompat:
    if artifact_type not in CURRENT_SCHEMA_VERSIONS:
        raise SchemaCompatibilityError(f"unknown artifact_type {artifact_type!r}")
    return SchemaCompat(
        artifact_type=artifact_type,
        current=CURRENT_SCHEMA_VERSIONS[artifact_type],
        min_readable=MIN_READABLE_SCHEMA_VERSIONS[artifact_type],
    )


def emit_schema_version(data: dict[str, Any], artifact_type: str) -> dict[str, Any]:
    """Set ``data["schema_version"]`` to the current version for the type."""
    data["schema_version"] = schema_compat(artifact_type).current
    return data


def read_schema_version(
    data: Mapping[str, Any],
    artifact_type: str,
    *,
    legacy_mode: bool = False,
) -> int:
    """Read and validate ``schema_version`` from a serialized artifact mapping.

    Strict callers reject missing versions as ``legacy_unversioned``.  Explicit
    compatibility loaders may pass ``legacy_mode=True`` to read pre-versioning
    artifacts, but that mode must not be used for catalog eligibility or
    validity decisions.  A present version must be a JSON integer (not a bool,
    float, or string) and remain inside ``[min_readable, current]``.
    """
    compat = schema_compat(artifact_type)
    if not isinstance(legacy_mode, bool):
        raise SchemaCompatibilityError("legacy_mode must be a boolean")
    if "schema_version" not in data:
        if legacy_mode:
            return compat.current
        raise LegacyUnversionedArtifactError(
            f"legacy_unversioned: {artifact_type} is missing required schema_version"
        )
    raw = data["schema_version"]
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise SchemaCompatibilityError(
            f"{artifact_type} schema_version must be an integer, got {raw!r}"
        )
    version = raw
    if version < compat.min_readable:
        raise SchemaCompatibilityError(
            f"{artifact_type} schema_version {version} is older than the minimum "
            f"readable version {compat.min_readable}"
        )
    if version > compat.current:
        raise SchemaCompatibilityError(
            f"{artifact_type} schema_version {version} is newer than the supported "
            f"version {compat.current}; upgrade optic_system to read it"
        )
    return version


@dataclass(frozen=True)
class ValidityResult:
    artifact_type: str
    ok: bool
    schema_version: int | None = None
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


# Maps artifact_type -> (module, loader classmethod/function name) for lazy import.
# A loader alone is not a validator: ``check_validity`` requires the loaded
# object to expose ``validate()`` and fails closed when it does not.
_JSON_LOADERS: dict[str, tuple[str, str]] = {
    "camera_profile": ("tasks.profiles.camera_profile", "CameraProfile"),
    "pupil_profile": ("tasks.profiles.pupil_profile", "PupilProfile"),
    "peak_layout_profile": (
        "tasks.psf.derive_peak_layout_profile",
        "PeakLayoutProfileManifest",
    ),
    "full_frame_psf_survey": (
        "tasks.psf.build_full_frame_psf_survey",
        "FullFramePSFSurveyManifest",
    ),
    "peak_patch_psf_dictionary": (
        "tasks.psf.build_peak_patch_psf_dictionary",
        "PeakPatchPSFDictionaryManifest",
    ),
    "sensor_energy_center_profile": (
        "tasks.psf.sensor_energy_center",
        "SensorEnergyCenterProfile",
    ),
}


def _load_json_artifact(artifact_type: str, path: Path) -> Any:
    import importlib

    module_name, class_name = _JSON_LOADERS[artifact_type]
    module = importlib.import_module(module_name)
    cls = getattr(module, class_name)
    return cls.load_json(path)


def check_validity(artifact_type: str, path: str | Path) -> ValidityResult:
    """Judge an artifact's validity from its data, not its filename.

    This is a strict local JSON-artifact check.  It verifies a declared type,
    explicit schema version, loader readability, and an implemented
    ``validate()`` method.  Types without a validator, including HDF5-only
    artifact types, return ``ok=False`` with ``validator_not_implemented``.

    The result deliberately does not retain ``path`` so it can never be copied
    into a future catalog as a machine-specific absolute location.  Artifact
    bundle and payload validation are separate future work.
    """
    artifact_path = Path(path)
    errors: list[str] = []
    warnings: list[str] = []
    schema_version: int | None = None

    if artifact_type not in CURRENT_SCHEMA_VERSIONS:
        return ValidityResult(
            artifact_type=artifact_type,
            ok=False,
            errors=(f"unknown artifact_type {artifact_type!r}",),
        )

    if not artifact_path.exists():
        return ValidityResult(
            artifact_type=artifact_type,
            ok=False,
            errors=("artifact location does not exist",),
        )

    if artifact_type not in _JSON_LOADERS:
        return ValidityResult(
            artifact_type=artifact_type,
            ok=False,
            errors=(
                f"validator_not_implemented: no data-level validator for "
                f"artifact_type {artifact_type!r}",
            ),
        )

    import json

    try:
        raw = json.loads(artifact_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return ValidityResult(
            artifact_type=artifact_type,
            ok=False,
            errors=(f"failed to read artifact JSON ({type(exc).__name__})",),
        )

    if isinstance(raw, Mapping):
        found_type = raw.get("artifact_type")
        if not isinstance(found_type, str) or not found_type.strip():
            errors.append("artifact_type is required and must be a non-empty string")
        elif found_type != artifact_type:
            errors.append(
                f"artifact_type mismatch: expected {artifact_type!r}, "
                f"found {found_type!r}"
            )
        try:
            schema_version = read_schema_version(raw, artifact_type)
        except SchemaCompatibilityError as exc:
            errors.append(str(exc))
    else:
        errors.append("artifact JSON root must be a mapping")

    if errors:
        return ValidityResult(
            artifact_type=artifact_type,
            ok=False,
            schema_version=schema_version,
            errors=tuple(errors),
            warnings=tuple(warnings),
        )

    try:
        artifact = _load_json_artifact(artifact_type, artifact_path)
    except Exception as exc:  # noqa: BLE001 - surfaced as a validity error
        return ValidityResult(
            artifact_type=artifact_type,
            ok=False,
            schema_version=schema_version,
            errors=(
                f"artifact loader rejected serialized data "
                f"({type(exc).__name__})",
            ),
            warnings=tuple(warnings),
        )

    validate = getattr(artifact, "validate", None)
    if not callable(validate):
        errors.append(
            f"validator_not_implemented: artifact_type {artifact_type!r} "
            "does not expose validate()"
        )
    else:
        try:
            validate()
        except Exception as exc:  # noqa: BLE001
            errors.append(f"validate() failed ({type(exc).__name__})")

    return ValidityResult(
        artifact_type=artifact_type,
        ok=not errors,
        schema_version=schema_version,
        errors=tuple(errors),
        warnings=tuple(warnings),
    )
