from __future__ import annotations

"""Central schema-version registry for measured artifacts.

This module is the single source of truth for artifact ``schema_version`` values
and their read compatibility windows. Artifact modules import the light-weight
``emit_schema_version`` / ``read_schema_version`` helpers so that every serialized
artifact carries a round-trippable ``schema_version``. Local structural
validation lives in :mod:`tasks.artifacts.validation`; compatibility re-exports
remain available here for callers that used the original public entry point.
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


def check_validity(artifact_type: str, path: str | Path) -> "ValidityResult":
    """Compatibility wrapper for :func:`tasks.artifacts.validation.check_validity`."""
    from tasks.artifacts.validation import check_validity as _check_validity

    return _check_validity(artifact_type, path)


def __getattr__(name: str) -> Any:
    """Lazily re-export validation types without an import cycle."""
    if name in {"ValidityOutcome", "ValidityResult"}:
        from tasks.artifacts import validation

        return getattr(validation, name)
    raise AttributeError(name)


__all__ = [
    "CURRENT_SCHEMA_VERSIONS",
    "LegacyUnversionedArtifactError",
    "MIN_READABLE_SCHEMA_VERSIONS",
    "SchemaCompat",
    "SchemaCompatibilityError",
    "ValidityOutcome",
    "ValidityResult",
    "check_validity",
    "emit_schema_version",
    "read_schema_version",
    "schema_compat",
]
