from __future__ import annotations

"""Central schema-version registry and validity checking for measured artifacts.

This module is the single source of truth for artifact ``schema_version`` values
and their read compatibility windows. Artifact modules import the light-weight
``emit_schema_version`` / ``read_schema_version`` helpers so that every serialized
artifact carries a round-trippable ``schema_version``. ``check_validity`` composes
schema-compatibility, ``.validate()``, and coordinate-frame checks into a single
data-based validity judgement (never filename-based).

It must not import artifact modules at module import time; loaders are imported
lazily inside ``check_validity`` to avoid circular imports.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping


class SchemaCompatibilityError(ValueError):
    """Raised when an artifact schema_version is outside the readable window."""


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


def read_schema_version(data: Mapping[str, Any], artifact_type: str) -> int:
    """Read and validate ``schema_version`` from a serialized artifact mapping.

    A missing ``schema_version`` is treated as the current version for backward
    compatibility with artifacts written before versioning was wired in. A version
    outside ``[min_readable, current]`` raises ``SchemaCompatibilityError``.
    """
    compat = schema_compat(artifact_type)
    raw = data.get("schema_version")
    if raw is None:
        return compat.current
    try:
        version = int(raw)
    except (TypeError, ValueError):
        raise SchemaCompatibilityError(
            f"{artifact_type} schema_version must be an integer, got {raw!r}"
        ) from None
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
    path: str
    ok: bool
    schema_version: int | None = None
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


# Maps artifact_type -> (module, loader classmethod/function name) for lazy import.
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

    Runs schema-version compatibility, the artifact's own ``.validate()`` when
    present, and coordinate-frame descriptor sanity when applicable. Returns a
    ``ValidityResult`` rather than raising, so callers (catalog, orchestrator)
    can record the outcome.
    """
    path = Path(path)
    errors: list[str] = []
    warnings: list[str] = []
    schema_version: int | None = None

    if artifact_type not in CURRENT_SCHEMA_VERSIONS:
        return ValidityResult(
            artifact_type=artifact_type,
            path=str(path),
            ok=False,
            errors=(f"unknown artifact_type {artifact_type!r}",),
        )

    if not path.exists():
        return ValidityResult(
            artifact_type=artifact_type,
            path=str(path),
            ok=False,
            errors=(f"artifact path does not exist: {path}",),
        )

    if artifact_type not in _JSON_LOADERS:
        warnings.append(
            f"no data-level validity loader registered for {artifact_type!r}; "
            "path existence only"
        )
        return ValidityResult(
            artifact_type=artifact_type,
            path=str(path),
            ok=True,
            warnings=tuple(warnings),
        )

    import json

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return ValidityResult(
            artifact_type=artifact_type,
            path=str(path),
            ok=False,
            errors=(f"failed to read artifact JSON: {exc}",),
        )

    if isinstance(raw, Mapping):
        found_type = raw.get("artifact_type")
        if found_type is not None and found_type != artifact_type:
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
            path=str(path),
            ok=False,
            schema_version=schema_version,
            errors=tuple(errors),
            warnings=tuple(warnings),
        )

    try:
        artifact = _load_json_artifact(artifact_type, path)
    except Exception as exc:  # noqa: BLE001 - surfaced as a validity error
        return ValidityResult(
            artifact_type=artifact_type,
            path=str(path),
            ok=False,
            schema_version=schema_version,
            errors=(f"artifact failed to load/validate: {exc}",),
            warnings=tuple(warnings),
        )

    validate = getattr(artifact, "validate", None)
    if callable(validate):
        try:
            validate()
        except Exception as exc:  # noqa: BLE001
            errors.append(f"validate() failed: {exc}")

    return ValidityResult(
        artifact_type=artifact_type,
        path=str(path),
        ok=not errors,
        schema_version=schema_version,
        errors=tuple(errors),
        warnings=tuple(warnings),
    )
