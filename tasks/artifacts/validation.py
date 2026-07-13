from __future__ import annotations

"""Local structural validation for measured artifacts.

Validation answers whether a supplied local artifact is readable and internally
consistent with its declared contract. It does not register artifacts, select a
generation, promote scientific trust, or infer identity from filenames.
"""

import importlib
import json
import math
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum
from numbers import Integral
from pathlib import Path
from typing import Any

import h5py
import numpy as np

from tasks.artifact_versioning import (
    LegacyUnversionedArtifactError,
    NewerSchemaVersionError,
    SchemaCompatibilityError,
    read_schema_version,
    schema_compat,
)

from .coordinate_frame import (
    camera_frame_extent_from_dict,
    camera_frame_extent_to_dict,
    strict_camera_frame_extent_from_mapping,
    validate_coordinate_frame_extent,
)
from .json_io import decode_h5_string


class ValidityOutcome(str, Enum):
    """Closed vocabulary for local artifact validation outcomes."""

    VALID = "valid"
    INVALID = "invalid"
    UNSUPPORTED = "unsupported"
    LEGACY_UNVERSIONED = "legacy_unversioned"
    UNREADABLE = "unreadable"


@dataclass(frozen=True)
class ValidityResult:
    """Machine-readable local validation outcome without a stored location."""

    artifact_type: str
    outcome: ValidityOutcome
    schema_version: int | None = None
    reason_codes: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        """Return true only for structurally validated artifacts."""
        return self.outcome is ValidityOutcome.VALID


ArtifactValidator = Callable[[Path], ValidityResult]


class _InvalidArtifact(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class _UnreadableArtifact(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class _UnsupportedArtifact(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def check_validity(artifact_type: str, path: str | Path) -> ValidityResult:
    """Validate one local artifact against the caller-provided canonical type.

    The caller must provide ``artifact_type``. This function never infers a
    type from the filename, suffix, or directory. Missing locations and parse
    failures are ``unreadable``; missing explicit schema versions are
    ``legacy_unversioned``; known types lacking a validator are ``unsupported``.
    """
    if not isinstance(artifact_type, str) or not artifact_type.strip():
        return _result(
            str(artifact_type),
            ValidityOutcome.UNSUPPORTED,
            reason_codes=("unknown_artifact_type",),
            errors=("artifact_type must be a non-empty canonical string",),
        )
    canonical_type = artifact_type.strip()
    try:
        schema_compat(canonical_type)
    except SchemaCompatibilityError:
        return _result(
            canonical_type,
            ValidityOutcome.UNSUPPORTED,
            reason_codes=("unknown_artifact_type",),
            errors=("artifact_type is not registered",),
        )

    artifact_path = Path(path)
    if not artifact_path.exists():
        return _result(
            canonical_type,
            ValidityOutcome.UNREADABLE,
            reason_codes=("location_missing",),
            errors=("artifact location does not exist",),
        )

    validator = VALIDATOR_REGISTRY.get(canonical_type)
    if validator is None:
        return _result(
            canonical_type,
            ValidityOutcome.UNSUPPORTED,
            reason_codes=("validator_not_implemented",),
            errors=("no complete structural validator is registered",),
        )
    try:
        return validator(artifact_path)
    except _InvalidArtifact as exc:
        return _result(
            canonical_type,
            ValidityOutcome.INVALID,
            reason_codes=(exc.code,),
            errors=(exc.message,),
        )
    except _UnreadableArtifact as exc:
        return _result(
            canonical_type,
            ValidityOutcome.UNREADABLE,
            reason_codes=(exc.code,),
            errors=(exc.message,),
        )
    except _UnsupportedArtifact as exc:
        return _result(
            canonical_type,
            ValidityOutcome.UNSUPPORTED,
            reason_codes=(exc.code,),
            errors=(exc.message,),
        )
    except LegacyUnversionedArtifactError:
        return _result(
            canonical_type,
            ValidityOutcome.LEGACY_UNVERSIONED,
            reason_codes=("legacy_unversioned",),
            errors=("serialized artifact lacks explicit schema_version",),
        )
    except OSError:
        return _result(
            canonical_type,
            ValidityOutcome.UNREADABLE,
            reason_codes=("location_unreadable",),
            errors=("artifact location could not be read",),
        )
    except Exception as exc:  # noqa: BLE001 - do not misclassify validator defects as data defects.
        return _result(
            canonical_type,
            ValidityOutcome.UNSUPPORTED,
            reason_codes=("validator_failed",),
            errors=(f"validator raised {type(exc).__name__}",),
        )


def _result(
    artifact_type: str,
    outcome: ValidityOutcome,
    *,
    schema_version: int | None = None,
    reason_codes: tuple[str, ...] = (),
    errors: tuple[str, ...] = (),
    warnings: tuple[str, ...] = (),
) -> ValidityResult:
    return ValidityResult(
        artifact_type=artifact_type,
        outcome=outcome,
        schema_version=schema_version,
        reason_codes=reason_codes,
        errors=errors,
        warnings=warnings,
    )


def _validate_json_artifact(
    path: Path,
    *,
    artifact_type: str,
    loader: Callable[[dict[str, Any]], Any],
) -> ValidityResult:
    try:
        data = _read_json_file(path)
    except _UnreadableArtifact as exc:
        return _result(
            artifact_type,
            ValidityOutcome.UNREADABLE,
            reason_codes=(exc.code,),
            errors=(exc.message,),
        )
    schema_result = _validate_serialized_mapping(data, artifact_type)
    if isinstance(schema_result, ValidityResult):
        return schema_result
    schema_version = schema_result
    try:
        data = _normalize_artifact_manifest_json(
            data,
            artifact_type=artifact_type,
            schema_version=schema_version,
        )
        _validate_strict_serialized_mapping(
            data,
            artifact_type,
            schema_version=schema_version,
        )
        artifact = _load_and_validate_serialized_artifact(data, loader)
    except _InvalidArtifact as exc:
        return _result(
            artifact_type,
            ValidityOutcome.INVALID,
            schema_version=schema_version,
            reason_codes=(exc.code,),
            errors=(exc.message,),
        )
    return _result(
        artifact_type,
        ValidityOutcome.VALID,
        schema_version=schema_version,
        warnings=_artifact_validation_warnings(artifact_type, artifact),
    )


def _artifact_validation_warnings(
    artifact_type: str,
    artifact: Any,
) -> tuple[str, ...]:
    if artifact_type != "peak_patch_psf_dictionary":
        return ()
    compatibility = getattr(artifact, "extent_compatibility", None)
    if not isinstance(compatibility, Mapping):
        return ()
    if (
        compatibility.get("matches") is False
        and compatibility.get("mismatch_override") is True
    ):
        return (
            "dictionary frame extents differ under an explicit audited override",
        )
    return ()


def _read_json_file(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeError as exc:
        raise _UnreadableArtifact(
            "json_unreadable",
            "artifact JSON is not valid UTF-8",
        ) from exc
    except OSError as exc:
        raise _UnreadableArtifact("location_unreadable", "artifact location could not be read") from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise _UnreadableArtifact("json_unreadable", "artifact JSON could not be parsed") from exc
    if not isinstance(data, dict):
        raise _InvalidArtifact(
            "json_root_invalid",
            "artifact JSON root must be a mapping",
        )
    return data


_V1_BROADBAND_WAVELENGTH_FIELDS: dict[str, tuple[str, ...]] = {
    "full_frame_psf_survey": (
        "entry_wavelengths_nm",
        "unique_wavelengths_nm",
    ),
    "sensor_energy_center_profile": ("per_entry_wavelengths_nm",),
    "peak_support_analysis_report": ("entry_wavelengths_nm",),
    "peak_layout_profile": (
        "survey_wavelengths_nm",
        "valid_wavelengths_nm",
    ),
    "peak_patch_psf_dictionary": (
        "entry_wavelengths_nm",
        "unique_wavelengths_nm",
    ),
}

_V1_COMPAT_CAMERA_EXTENT_FIELDS: dict[str, tuple[str, ...]] = {
    "full_frame_psf_survey": ("camera_frame_extent",),
    "sensor_energy_center_profile": ("camera_frame_extent",),
    "peak_support_analysis_report": ("camera_frame_extent",),
    "peak_layout_profile": ("camera_frame_extent",),
    "peak_patch_psf_dictionary": (
        "camera_frame_extent",
        "peak_layout_camera_frame_extent",
    ),
}


def _normalize_artifact_manifest_json(
    data: dict[str, Any],
    *,
    artifact_type: str,
    schema_version: int,
) -> dict[str, Any]:
    """Normalize approved schema-v1 broadband NaN values, then reject others."""
    normalized = dict(data)
    if schema_version == 1:
        for field in _V1_BROADBAND_WAVELENGTH_FIELDS.get(artifact_type, ()):
            values = normalized.get(field)
            if isinstance(values, list):
                normalized[field] = [
                    None if _is_nan_number(value) else value for value in values
                ]
    _reject_remaining_nonfinite_json(normalized)
    return normalized


def _reject_remaining_nonfinite_json(value: Any, *, field: str = "$") -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            _reject_remaining_nonfinite_json(item, field=f"{field}.{key}")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _reject_remaining_nonfinite_json(item, field=f"{field}[{index}]")
        return
    if isinstance(value, float) and not math.isfinite(value):
        raise _InvalidArtifact(
            "json_number_nonfinite",
            f"non-standard non-finite JSON number is not allowed at {field}",
        )


def _is_nan_number(value: Any) -> bool:
    return isinstance(value, float) and math.isnan(value)


def _json_loads_strict(text: str) -> Any:
    return json.loads(text, parse_constant=_reject_nonfinite_json_constant)


def _reject_nonfinite_json_constant(token: str) -> Any:
    raise _InvalidArtifact(
        "json_number_nonfinite",
        f"non-standard JSON numeric constant {token!r} is not allowed",
    )


def _validate_serialized_mapping(
    data: Mapping[str, Any],
    artifact_type: str,
) -> int | ValidityResult:
    found_type = data.get("artifact_type")
    if not isinstance(found_type, str) or not found_type.strip():
        return _result(
            artifact_type,
            ValidityOutcome.INVALID,
            reason_codes=("artifact_type_missing",),
            errors=("serialized artifact_type is required",),
        )
    if found_type != artifact_type:
        return _result(
            artifact_type,
            ValidityOutcome.INVALID,
            reason_codes=("artifact_type_mismatch",),
            errors=(
                f"serialized artifact_type {found_type!r} does not match expected "
                f"{artifact_type!r}",
            ),
        )
    try:
        return read_schema_version(data, artifact_type)
    except LegacyUnversionedArtifactError:
        return _result(
            artifact_type,
            ValidityOutcome.LEGACY_UNVERSIONED,
            reason_codes=("legacy_unversioned",),
            errors=("serialized artifact lacks explicit schema_version",),
        )
    except NewerSchemaVersionError:
        return _result(
            artifact_type,
            ValidityOutcome.UNSUPPORTED,
            reason_codes=("schema_newer_than_supported",),
            errors=("serialized schema_version requires a newer reader",),
        )
    except SchemaCompatibilityError as exc:
        return _result(
            artifact_type,
            ValidityOutcome.INVALID,
            reason_codes=("schema_incompatible",),
            errors=(
                _safe_validation_message(
                    exc,
                    "serialized schema_version is not supported",
                ),
            ),
        )


def _load_and_validate_serialized_artifact(
    data: dict[str, Any],
    loader: Callable[[dict[str, Any]], Any],
) -> Any:
    """Load one already type-checked mapping and run its explicit contract.

    Compatibility loaders remain available to normal task code.  Strict
    validation reaches them only after raw JSON fields have been checked, and
    it promotes only known artifact-contract exceptions to ``_InvalidArtifact``.
    An unexpected exception is deliberately allowed to reach ``check_validity``
    where it becomes ``unsupported/validator_failed`` rather than a persistent
    claim that the data itself is invalid.
    """
    try:
        artifact = loader(data)
        validate = getattr(artifact, "validate", None)
        if not callable(validate):
            raise _UnsupportedArtifact(
                "validator_not_implemented",
                "serialized artifact has no validate() contract",
            )
        validate()
        return artifact
    except _InvalidArtifact:
        raise
    except Exception as exc:  # noqa: BLE001 - classification is intentionally narrow.
        if _is_explicit_contract_error(exc):
            raise _InvalidArtifact(
                "serialized_contract_rejected",
                _safe_validation_message(
                    exc,
                    f"serialized artifact was rejected by {type(exc).__name__}",
                ),
            ) from exc
        raise


def _is_explicit_contract_error(exc: Exception) -> bool:
    """Return whether *exc* came from a current artifact contract.

    Importing lazily keeps the validation module independent of task import
    order.  These error types are intentionally specific: a generic
    ``ValueError``, ``TypeError``, or programming error must not mark data as
    structurally invalid.
    """
    from tasks.profiles.camera_profile import ProfileError
    from tasks.psf.analyze_diffraction_support import DiffractionSupportAnalysisError
    from tasks.psf.profile_requirements import PSFArtifactError
    from tasks.psf.sensor_energy_center import SensorEnergyCenterError

    return isinstance(
        exc,
        (
            ProfileError,
            SensorEnergyCenterError,
            DiffractionSupportAnalysisError,
            PSFArtifactError,
        ),
    )


def _validate_strict_serialized_mapping(
    data: Mapping[str, Any],
    artifact_type: str,
    *,
    schema_version: int,
) -> None:
    """Reject coercive serialized values before compatibility deserialization.

    Existing ``from_dict`` methods intentionally retain limited compatibility
    behavior for task loading.  Structural validation instead verifies the raw
    JSON representation first, so values such as ``2.9`` for an integer field
    cannot silently become ``2`` and numeric IDs cannot silently become text.
    """
    validator = _STRICT_SERIALIZED_VALIDATORS.get(artifact_type)
    if validator is None:
        raise _InvalidArtifact(
            "validator_not_implemented",
            "strict serialized validator is not registered",
        )
    validator_data = data
    if schema_version == 1:
        validator_data = _v1_strict_validation_mapping(data, artifact_type)
    validator(validator_data)


def _v1_strict_validation_mapping(
    data: Mapping[str, Any],
    artifact_type: str,
) -> Mapping[str, Any]:
    """Adapt legacy-coercive extent fields only for the strict type pass."""
    adapted = dict(data)
    for field in _V1_COMPAT_CAMERA_EXTENT_FIELDS.get(artifact_type, ()):
        value = adapted.get(field)
        if isinstance(value, Mapping):
            try:
                adapted[field] = camera_frame_extent_to_dict(
                    camera_frame_extent_from_dict(value)
                )
            except (TypeError, ValueError):
                pass
    if (
        artifact_type == "peak_patch_psf_dictionary"
        and "extent_compatibility" not in adapted
    ):
        dictionary_extent = adapted.get("camera_frame_extent")
        layout_extent = adapted.get("peak_layout_camera_frame_extent")
        if isinstance(dictionary_extent, Mapping) and isinstance(
            layout_extent,
            Mapping,
        ):
            matches = _canonical_mapping(dictionary_extent) == _canonical_mapping(
                layout_extent
            )
            adapted["extent_compatibility"] = {
                "matches": matches,
                "mismatch_override": not matches,
                "reason": (
                    None
                    if matches
                    else "legacy schema v1 did not record the extent mismatch approval"
                ),
            }
    return adapted


def _strict_error(field: str, expected: str) -> None:
    raise _InvalidArtifact(
        "serialized_type_invalid",
        f"{field} must be {expected} in the serialized artifact",
    )


def _strict_required(data: Mapping[str, Any], field: str) -> Any:
    if field not in data:
        raise _InvalidArtifact(
            "serialized_field_missing",
            f"{field} is required in the serialized artifact",
        )
    return data[field]


def _strict_mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        _strict_error(field, "a mapping")
    return value


def _strict_required_mapping(data: Mapping[str, Any], field: str) -> Mapping[str, Any]:
    return _strict_mapping(_strict_required(data, field), field)


def _strict_camera_frame_extent_field(
    data: Mapping[str, Any],
    field: str,
) -> None:
    mapping = _strict_required_mapping(data, field)
    try:
        strict_camera_frame_extent_from_mapping(mapping, field_name=field)
    except ValueError as exc:
        raise _InvalidArtifact("serialized_type_invalid", str(exc)) from exc


def _strict_list(value: Any, field: str) -> list[Any]:
    if not isinstance(value, list):
        _strict_error(field, "a JSON array")
    return value


def _strict_required_list(data: Mapping[str, Any], field: str) -> list[Any]:
    return _strict_list(_strict_required(data, field), field)


def _strict_string(value: Any, field: str, *, nullable: bool = False) -> None:
    if value is None and nullable:
        return
    if not isinstance(value, str) or not value.strip():
        _strict_error(field, "a non-empty string" + (" or null" if nullable else ""))


def _strict_required_string(data: Mapping[str, Any], field: str) -> None:
    _strict_string(_strict_required(data, field), field)


def _strict_optional_string(data: Mapping[str, Any], field: str) -> None:
    if field in data:
        _strict_string(data[field], field, nullable=True)


def _strict_integer(value: Any, field: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        _strict_error(field, "an integer")


def _strict_number(value: Any, field: str, *, nullable: bool = False) -> None:
    if value is None and nullable:
        return
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _strict_error(field, "a JSON number" + (" or null" if nullable else ""))
    if not math.isfinite(float(value)):
        _strict_error(
            field,
            "a finite JSON number" + (" or null" if nullable else ""),
        )


def _strict_optional_number(data: Mapping[str, Any], field: str) -> None:
    if field in data:
        _strict_number(data[field], field, nullable=True)


def _strict_pair(value: Any, field: str, *, integers: bool = False, nullable: bool = False) -> None:
    if value is None and nullable:
        return
    values = _strict_list(value, field)
    if len(values) != 2:
        _strict_error(field, "a two-element JSON array")
    for index, item in enumerate(values):
        if integers:
            _strict_integer(item, f"{field}[{index}]")
        else:
            _strict_number(item, f"{field}[{index}]")


def _strict_quad(value: Any, field: str, *, nullable: bool = False) -> None:
    if value is None and nullable:
        return
    values = _strict_list(value, field)
    if len(values) != 4:
        _strict_error(field, "a four-element JSON array")
    for index, item in enumerate(values):
        _strict_integer(item, f"{field}[{index}]")


def _strict_number_list(data: Mapping[str, Any], field: str, *, required: bool = True) -> None:
    if not required and field not in data:
        return
    for index, item in enumerate(_strict_required_list(data, field)):
        _strict_number(item, f"{field}[{index}]")


def _strict_wavelength_list(
    data: Mapping[str, Any],
    field: str,
    *,
    required: bool = True,
) -> None:
    """Require finite wavelengths while allowing JSON null for broadband."""
    if not required and field not in data:
        return
    for index, item in enumerate(_strict_required_list(data, field)):
        _strict_number(item, f"{field}[{index}]", nullable=True)


def _strict_string_list(data: Mapping[str, Any], field: str, *, required: bool = True) -> None:
    if not required and field not in data:
        return
    for index, item in enumerate(_strict_required_list(data, field)):
        _strict_string(item, f"{field}[{index}]")


def _strict_pair_list(
    data: Mapping[str, Any],
    field: str,
    *,
    integers: bool = False,
    required: bool = True,
) -> None:
    if not required and field not in data:
        return
    for index, item in enumerate(_strict_required_list(data, field)):
        _strict_pair(item, f"{field}[{index}]", integers=integers)


def _strict_mapping_field(data: Mapping[str, Any], field: str, *, required: bool = True) -> Mapping[str, Any] | None:
    if not required and field not in data:
        return None
    return _strict_required_mapping(data, field)


def _validate_serialized_camera_profile(data: Mapping[str, Any]) -> None:
    for field in ("camera_profile_id", "profile_family"):
        _strict_required_string(data, field)
    illumination = _strict_required_mapping(data, "illumination")
    _strict_required_string(illumination, "mode")
    for field in ("tls_setpoint_nm", "effective_wavelength_nm"):
        _strict_optional_number(illumination, field)
    if "wavelengths_nm" in illumination:
        for index, value in enumerate(_strict_list(illumination["wavelengths_nm"], "illumination.wavelengths_nm")):
            _strict_number(value, f"illumination.wavelengths_nm[{index}]")
    _strict_optional_string(illumination, "source")
    _strict_required_mapping(data, "lcd_state")
    _strict_string_list(data, "valid_for")
    for field in ("source_raw_capture_file", "created_at", "software_version", "depends_on_pupil_profile_id"):
        _strict_optional_string(data, field)
    if "depends_on" in data:
        depends_on = _strict_mapping(data["depends_on"], "depends_on")
        if "pupil_profile_id" in depends_on:
            _strict_string(depends_on["pupil_profile_id"], "depends_on.pupil_profile_id")
    _strict_camera_settings_container(data.get("camera"), "camera")
    _strict_camera_settings_container(data.get("per_wavelength"), "per_wavelength", direct=True)
    _strict_optional_camera_scalar_fields(data, "")
    if "extra" in data:
        _strict_mapping(data["extra"], "extra")


def _strict_camera_settings_container(
    value: Any,
    field: str,
    *,
    direct: bool = False,
) -> None:
    if value is None:
        return
    container = _strict_mapping(value, field)
    if direct:
        per_wavelength = container
    else:
        _strict_optional_camera_scalar_fields(container, f"{field}.")
        per_wavelength = container.get("per_wavelength")
    if per_wavelength is None:
        return
    settings_map = _strict_mapping(per_wavelength, f"{field}.per_wavelength" if not direct else field)
    for key, settings in settings_map.items():
        _strict_string(key, f"{field} key")
        setting_map = _strict_mapping(settings, f"{field}[{key!r}]")
        _strict_number(_strict_required(setting_map, "exposure_us"), f"{field}[{key!r}].exposure_us")
        _strict_optional_camera_scalar_fields(setting_map, f"{field}[{key!r}].")


def _strict_optional_camera_scalar_fields(data: Mapping[str, Any], prefix: str) -> None:
    for field in (
        "exposure_us",
        "gain_db",
        "peak_pixel",
        "saturation_margin",
        "full_frame_peak_pixel",
    ):
        if field in data:
            _strict_number(data[field], f"{prefix}{field}", nullable=True)
    for field in ("frames_per_capture", "full_frame_saturated_pixel_count"):
        if field in data and data[field] is not None:
            _strict_integer(data[field], f"{prefix}{field}")
    if "peak_pixel_domain" in data:
        _strict_string(data["peak_pixel_domain"], f"{prefix}peak_pixel_domain", nullable=True)


def _validate_serialized_pupil_profile(data: Mapping[str, Any]) -> None:
    for field in ("pupil_profile_id", "lcd_coordinate_convention"):
        _strict_required_string(data, field)
    for field in ("lcd_display_index", "subpixel_axis"):
        _strict_integer(_strict_required(data, field), field)
    _strict_pair(_strict_required(data, "lcd_physical_center"), "lcd_physical_center")
    if "lcd_physical_radius" in data:
        _strict_number(data["lcd_physical_radius"], "lcd_physical_radius", nullable=True)
    for field in ("aperture_window", "recommended_roi"):
        if field in data:
            _strict_quad(data[field], field, nullable=True)
    if "camera_psf_center" in data:
        _strict_pair(data["camera_psf_center"], "camera_psf_center", nullable=True)
    for field in ("fit_quality", "extra"):
        if field in data:
            _strict_mapping(data[field], field)
    for field in ("source_raw_capture_file", "created_at", "software_version"):
        _strict_optional_string(data, field)


def _validate_serialized_sensor_energy_center_profile(data: Mapping[str, Any]) -> None:
    for field in ("center_profile_id", "source_survey_h5", "coordinate_frame", "estimator_name"):
        _strict_required_string(data, field)
    _strict_camera_frame_extent_field(data, "camera_frame_extent")
    for field in ("bg_policy", "corr_policy", "aggregation_policy"):
        _strict_required_mapping(data, field)
    _strict_pair(_strict_required(data, "center_xy"), "center_xy")
    _strict_pair(_strict_required(data, "global_center_std_xy"), "global_center_std_xy")
    _strict_number(_strict_required(data, "max_center_deviation_px"), "max_center_deviation_px")
    _strict_pair_list(data, "per_entry_center_xy")
    _strict_string_list(data, "per_entry_mask_ids")
    _strict_wavelength_list(data, "per_entry_wavelengths_nm")
    _strict_number_list(data, "per_entry_background_value")
    _strict_number_list(data, "per_entry_total_corr_energy")
    for index, value in enumerate(
        _strict_required_list(data, "per_entry_fallback_used")
    ):
        if type(value) is not bool:
            _strict_error(f"per_entry_fallback_used[{index}]", "a boolean")
    for field in ("per_wavelength_mean_center_xy", "per_wavelength_center_std_xy"):
        mapping = _strict_required_mapping(data, field)
        for key, value in mapping.items():
            _strict_string(key, f"{field} key")
            _strict_pair(value, f"{field}[{key!r}]")
    if "camera_frame_shape" in data:
        _strict_pair(data["camera_frame_shape"], "camera_frame_shape", integers=True, nullable=True)
    _strict_optional_string(data, "notes")


def _validate_serialized_peak_layout_profile(data: Mapping[str, Any]) -> None:
    for field in ("peak_layout_id", "source_survey_h5", "coordinate_frame"):
        _strict_required_string(data, field)
    _strict_pair(_strict_required(data, "frame_shape"), "frame_shape", integers=True)
    _strict_camera_frame_extent_field(data, "camera_frame_extent")
    _strict_string_list(data, "peak_ids")
    _strict_pair_list(data, "center_xy")
    _strict_pair_list(data, "patch_shape_hw", integers=True)
    _strict_pair_list(data, "patch_origin_xy", integers=True)
    _strict_number_list(data, "stability_score")
    _strict_pair_list(data, "amplitude_range")
    for index, item in enumerate(_strict_required_list(data, "local_background_stats")):
        stats = _strict_mapping(item, f"local_background_stats[{index}]")
        for key, value in stats.items():
            _strict_string(key, f"local_background_stats[{index}] key")
            _strict_number(value, f"local_background_stats[{index}][{key!r}]")
    _strict_wavelength_list(data, "survey_wavelengths_nm")
    _strict_string_list(data, "survey_mask_ids")
    _strict_wavelength_list(data, "valid_wavelengths_nm")
    _strict_string_list(data, "valid_mask_ids")
    validity_scope = _strict_required_mapping(data, "validity_scope")
    for key, value in validity_scope.items():
        _strict_string(key, "validity_scope key")
        _strict_string(value, f"validity_scope[{key!r}]")
    _strict_required_mapping(data, "detection_policy")
    for field in ("notes", "center_profile_id"):
        _strict_optional_string(data, field)
    if "energy_center_xy" in data:
        _strict_pair(data["energy_center_xy"], "energy_center_xy", nullable=True)
    if "center_xy_rel" in data:
        value = data["center_xy_rel"]
        if value is not None:
            _strict_pair_list({"center_xy_rel": value}, "center_xy_rel")


def _validate_serialized_full_frame_psf_survey(data: Mapping[str, Any]) -> None:
    for field in ("survey_id", "source_raw_capture_h5", "illumination_mode", "full_frame_role"):
        _strict_required_string(data, field)
    for field in ("pupil_profile_id", "camera_profile_id", "notes"):
        _strict_optional_string(data, field)
    _strict_wavelength_list(data, "entry_wavelengths_nm")
    _strict_string_list(data, "entry_illumination_json")
    _strict_string_list(data, "entry_mask_ids")
    _strict_wavelength_list(data, "unique_wavelengths_nm")
    _strict_string_list(data, "unique_mask_ids")
    _strict_pair(_strict_required(data, "frame_shape"), "frame_shape", integers=True)
    _strict_camera_frame_extent_field(data, "camera_frame_extent")
    _strict_required_mapping(data, "survey_policy")


def _validate_serialized_peak_support_analysis_report(data: Mapping[str, Any]) -> None:
    for field in ("report_id", "source_survey_h5", "coordinate_frame"):
        _strict_required_string(data, field)
    _strict_pair(_strict_required(data, "frame_shape"), "frame_shape", integers=True)
    _strict_camera_frame_extent_field(data, "camera_frame_extent")
    for field in (
        "bg_policy",
        "corr_policy",
        "radial_policy",
        "component_policy",
    ):
        _strict_required_mapping(data, field)
    _strict_number_list(data, "tau_values")
    _strict_number_list(data, "support_radii")
    _strict_string_list(data, "entry_mask_ids")
    _strict_wavelength_list(data, "entry_wavelengths_nm")
    _strict_optional_string(data, "notes")
    if "valid_pixel_domain" in data and data["valid_pixel_domain"] is not None:
        _strict_mapping(data["valid_pixel_domain"], "valid_pixel_domain")


def _validate_serialized_peak_patch_psf_dictionary(data: Mapping[str, Any]) -> None:
    for field in (
        "dictionary_id",
        "source_raw_capture_h5",
        "peak_layout_profile",
        "illumination_mode",
        "peak_layout_coordinate_frame",
        "applied_background_policy",
        "applied_normalization_policy",
    ):
        _strict_required_string(data, field)
    for field in ("pupil_profile_id", "camera_profile_id", "notes"):
        _strict_optional_string(data, field)
    _strict_wavelength_list(data, "entry_wavelengths_nm")
    _strict_string_list(data, "entry_mask_ids")
    _strict_wavelength_list(data, "unique_wavelengths_nm")
    _strict_string_list(data, "unique_mask_ids")
    _strict_pair(_strict_required(data, "frame_shape"), "frame_shape", integers=True)
    _strict_camera_frame_extent_field(data, "camera_frame_extent")
    _strict_camera_frame_extent_field(data, "peak_layout_camera_frame_extent")
    extent_compatibility = _strict_required_mapping(data, "extent_compatibility")
    if set(extent_compatibility) != {"matches", "mismatch_override", "reason"}:
        _strict_error(
            "extent_compatibility",
            "a mapping containing exactly matches, mismatch_override, and reason",
        )
    for field in ("matches", "mismatch_override"):
        if type(extent_compatibility[field]) is not bool:
            _strict_error(f"extent_compatibility.{field}", "a boolean")
    _strict_optional_string(extent_compatibility, "reason")
    _strict_string_list(data, "peak_ids")
    _strict_pair_list(data, "patch_shape_hw", integers=True)
    _strict_pair_list(data, "patch_origin_xy", integers=True)


_STRICT_SERIALIZED_VALIDATORS: dict[str, Callable[[Mapping[str, Any]], None]] = {
    "camera_profile": _validate_serialized_camera_profile,
    "pupil_profile": _validate_serialized_pupil_profile,
    "sensor_energy_center_profile": _validate_serialized_sensor_energy_center_profile,
    "peak_layout_profile": _validate_serialized_peak_layout_profile,
    "full_frame_psf_survey": _validate_serialized_full_frame_psf_survey,
    "peak_support_analysis_report": _validate_serialized_peak_support_analysis_report,
    "peak_patch_psf_dictionary": _validate_serialized_peak_patch_psf_dictionary,
}


def _class_loader(module_name: str, class_name: str) -> Callable[[dict[str, Any]], Any]:
    def _load(data: dict[str, Any]) -> Any:
        module = importlib.import_module(module_name)
        cls = getattr(module, class_name)
        return cls.from_dict(data, legacy_mode=False)

    return _load


_MANIFEST_LOADERS: dict[str, Callable[[dict[str, Any]], Any]] = {
    "camera_profile": _class_loader("tasks.profiles.camera_profile", "CameraProfile"),
    "pupil_profile": _class_loader("tasks.profiles.pupil_profile", "PupilProfile"),
    "sensor_energy_center_profile": _class_loader(
        "tasks.psf.sensor_energy_center",
        "SensorEnergyCenterProfile",
    ),
    "peak_layout_profile": _class_loader(
        "tasks.psf.derive_peak_layout_profile",
        "PeakLayoutProfileManifest",
    ),
    "full_frame_psf_survey": _class_loader(
        "tasks.psf.build_full_frame_psf_survey",
        "FullFramePSFSurveyManifest",
    ),
    "peak_support_analysis_report": _class_loader(
        "tasks.psf.analyze_diffraction_support",
        "PeakSupportAnalysisManifest",
    ),
    "peak_patch_psf_dictionary": _class_loader(
        "tasks.psf.build_peak_patch_psf_dictionary",
        "PeakPatchPSFDictionaryManifest",
    ),
}


_EMBEDDED_HDF_MANIFESTS: dict[str, tuple[str, bool]] = {
    "full_frame_psf_survey": ("full_frame_survey/manifest_json", True),
    "peak_support_analysis_report": ("metadata/manifest_json", False),
    "peak_patch_psf_dictionary": ("peak_patch_dictionary/manifest_json", True),
}


def read_validated_manifest_mapping(
    artifact_type: str,
    path: str | Path,
    *,
    require_json: bool = False,
) -> tuple[dict[str, Any], int] | ValidityResult:
    """Read and strictly validate an artifact manifest mapping for local comparison.

    JSON artifacts are read directly.  For the measured HDF5 products, the
    function reads the artifact's canonical embedded manifest.  It has no
    catalog behavior and never infers the artifact type from the location.
    ``require_json`` is used for a ``manifest_sidecar`` so that a second HDF5
    file cannot masquerade as a sidecar manifest.
    """
    if not isinstance(artifact_type, str) or not artifact_type.strip():
        return _result(
            str(artifact_type),
            ValidityOutcome.UNSUPPORTED,
            reason_codes=("unknown_artifact_type",),
            errors=("artifact_type must be a non-empty canonical string",),
        )
    canonical_type = artifact_type.strip()
    loader = _MANIFEST_LOADERS.get(canonical_type)
    if loader is None:
        return _result(
            canonical_type,
            ValidityOutcome.UNSUPPORTED,
            reason_codes=("embedded_manifest_not_supported",),
            errors=("artifact type has no manifest mapping contract",),
        )
    manifest_path = Path(path)
    if not manifest_path.exists():
        return _result(
            canonical_type,
            ValidityOutcome.UNREADABLE,
            reason_codes=("location_missing",),
            errors=("artifact location does not exist",),
        )

    try:
        if _is_hdf5_payload(manifest_path):
            if require_json:
                raise _InvalidArtifact(
                    "manifest_sidecar_not_json",
                    "manifest_sidecar must be a JSON manifest payload",
                )
            embedded = _EMBEDDED_HDF_MANIFESTS.get(canonical_type)
            if embedded is None:
                return _result(
                    canonical_type,
                    ValidityOutcome.UNSUPPORTED,
                    reason_codes=("embedded_manifest_not_supported",),
                    errors=("artifact type has no embedded manifest contract",),
                )
            embedded_path, root_type_required = embedded
            with h5py.File(manifest_path, "r") as h5:
                _validate_root_artifact_type(
                    h5,
                    canonical_type,
                    required=root_type_required,
                )
                data = _read_hdf_artifact_manifest_mapping(h5, embedded_path)
                schema_result = _validate_serialized_mapping(data, canonical_type)
                if isinstance(schema_result, ValidityResult):
                    return schema_result
                schema_version = schema_result
                _validate_optional_root_schema_version(
                    h5,
                    canonical_type,
                    expected=schema_version,
                )
        else:
            data = _read_json_file(manifest_path)
            schema_result = _validate_serialized_mapping(data, canonical_type)
            if isinstance(schema_result, ValidityResult):
                return schema_result
            schema_version = schema_result
        data = _normalize_artifact_manifest_json(
            data,
            artifact_type=canonical_type,
            schema_version=schema_version,
        )
        _validate_strict_serialized_mapping(
            data,
            canonical_type,
            schema_version=schema_version,
        )
        _load_and_validate_serialized_artifact(data, loader)
        return data, schema_version
    except _InvalidArtifact as exc:
        return _result(
            canonical_type,
            ValidityOutcome.INVALID,
            reason_codes=(exc.code,),
            errors=(exc.message,),
        )
    except _UnreadableArtifact as exc:
        return _result(
            canonical_type,
            ValidityOutcome.UNREADABLE,
            reason_codes=(exc.code,),
            errors=(exc.message,),
        )
    except _UnsupportedArtifact as exc:
        return _result(
            canonical_type,
            ValidityOutcome.UNSUPPORTED,
            reason_codes=(exc.code,),
            errors=(exc.message,),
        )
    except OSError:
        return _result(
            canonical_type,
            ValidityOutcome.UNREADABLE,
            reason_codes=("location_unreadable",),
            errors=("artifact location could not be read",),
        )
    except Exception as exc:  # noqa: BLE001 - retain validator/data distinction.
        return _result(
            canonical_type,
            ValidityOutcome.UNSUPPORTED,
            reason_codes=("validator_failed",),
            errors=(f"validator raised {type(exc).__name__}",),
        )


def _validate_camera_profile(path: Path) -> ValidityResult:
    return _validate_json_artifact(
        path,
        artifact_type="camera_profile",
        loader=_class_loader("tasks.profiles.camera_profile", "CameraProfile"),
    )


def _validate_pupil_profile(path: Path) -> ValidityResult:
    return _validate_json_artifact(
        path,
        artifact_type="pupil_profile",
        loader=_class_loader("tasks.profiles.pupil_profile", "PupilProfile"),
    )


def _validate_sensor_energy_center_profile(path: Path) -> ValidityResult:
    return _validate_json_artifact(
        path,
        artifact_type="sensor_energy_center_profile",
        loader=_class_loader("tasks.psf.sensor_energy_center", "SensorEnergyCenterProfile"),
    )


def _validate_peak_layout_profile(path: Path) -> ValidityResult:
    return _validate_json_artifact(
        path,
        artifact_type="peak_layout_profile",
        loader=_class_loader("tasks.psf.derive_peak_layout_profile", "PeakLayoutProfileManifest"),
    )


def _validate_full_frame_psf_survey_json(path: Path) -> ValidityResult:
    return _validate_json_artifact(
        path,
        artifact_type="full_frame_psf_survey",
        loader=_class_loader(
            "tasks.psf.build_full_frame_psf_survey",
            "FullFramePSFSurveyManifest",
        ),
    )


def _validate_peak_patch_psf_dictionary_json(path: Path) -> ValidityResult:
    return _validate_json_artifact(
        path,
        artifact_type="peak_patch_psf_dictionary",
        loader=_class_loader(
            "tasks.psf.build_peak_patch_psf_dictionary",
            "PeakPatchPSFDictionaryManifest",
        ),
    )


def _validate_raw_capture(path: Path) -> ValidityResult:
    artifact_type = "raw_capture"
    try:
        with h5py.File(path, "r") as h5:
            schema_version, outcome = _read_hdf_schema_version(
                h5,
                attribute="raw_capture_schema_version",
                artifact_type=artifact_type,
            )
            if outcome is not None:
                return outcome
            if schema_version == 2:
                # Historical v2 captures predate the root identity attribute
                # and the finalized capture-count processing flags.
                _validate_root_artifact_type(h5, artifact_type, required=False)
                _validate_raw_capture_v2(h5)
            elif schema_version == 3:
                _validate_root_artifact_type(h5, artifact_type, required=True)
                _validate_raw_capture_v3(h5)
            else:  # pragma: no cover - read_schema_version bounds this branch.
                raise _InvalidArtifact(
                    "schema_incompatible",
                    "raw capture schema version has no registered validator",
                )
            return _result(
                artifact_type,
                ValidityOutcome.VALID,
                schema_version=schema_version,
            )
    except _InvalidArtifact as exc:
        return _result(
            artifact_type,
            ValidityOutcome.INVALID,
            reason_codes=(exc.code,),
            errors=(exc.message,),
        )
    except _UnreadableArtifact as exc:
        return _result(
            artifact_type,
            ValidityOutcome.UNREADABLE,
            reason_codes=(exc.code,),
            errors=(exc.message,),
        )
    except _UnsupportedArtifact as exc:
        return _result(
            artifact_type,
            ValidityOutcome.UNSUPPORTED,
            reason_codes=(exc.code,),
            errors=(exc.message,),
        )
    except OSError:
        return _result(
            artifact_type,
            ValidityOutcome.UNREADABLE,
            reason_codes=("hdf5_unreadable",),
            errors=("HDF5 payload could not be opened",),
        )


def _validate_full_frame_psf_survey_h5(path: Path) -> ValidityResult:
    artifact_type = "full_frame_psf_survey"
    return _validate_hdf_manifest_artifact(
        path,
        artifact_type=artifact_type,
        manifest_path="full_frame_survey/manifest_json",
        loader=_class_loader(
            "tasks.psf.build_full_frame_psf_survey",
            "FullFramePSFSurveyManifest",
        ),
        hdf_validator=_validate_full_frame_psf_survey_payload,
    )


def _validate_full_frame_psf_survey(path: Path) -> ValidityResult:
    if _is_hdf5_payload(path):
        return _validate_full_frame_psf_survey_h5(path)
    return _validate_full_frame_psf_survey_json(path)


def _validate_peak_support_analysis_report_h5(path: Path) -> ValidityResult:
    artifact_type = "peak_support_analysis_report"
    return _validate_hdf_manifest_artifact(
        path,
        artifact_type=artifact_type,
        manifest_path="metadata/manifest_json",
        loader=_class_loader(
            "tasks.psf.analyze_diffraction_support",
            "PeakSupportAnalysisManifest",
        ),
        hdf_validator=_validate_peak_support_analysis_payload,
        root_artifact_type_required=False,
    )


def _validate_peak_support_analysis_report_json(path: Path) -> ValidityResult:
    return _validate_json_artifact(
        path,
        artifact_type="peak_support_analysis_report",
        loader=_class_loader(
            "tasks.psf.analyze_diffraction_support",
            "PeakSupportAnalysisManifest",
        ),
    )


def _validate_peak_support_analysis_report(path: Path) -> ValidityResult:
    if _is_hdf5_payload(path):
        return _validate_peak_support_analysis_report_h5(path)
    return _validate_peak_support_analysis_report_json(path)


def _validate_peak_patch_psf_dictionary_h5(path: Path) -> ValidityResult:
    artifact_type = "peak_patch_psf_dictionary"
    return _validate_hdf_manifest_artifact(
        path,
        artifact_type=artifact_type,
        manifest_path="peak_patch_dictionary/manifest_json",
        loader=_class_loader(
            "tasks.psf.build_peak_patch_psf_dictionary",
            "PeakPatchPSFDictionaryManifest",
        ),
        hdf_validator=_validate_peak_patch_psf_dictionary_payload,
    )


def _validate_peak_patch_psf_dictionary(path: Path) -> ValidityResult:
    if _is_hdf5_payload(path):
        return _validate_peak_patch_psf_dictionary_h5(path)
    return _validate_peak_patch_psf_dictionary_json(path)


def _is_hdf5_payload(path: Path) -> bool:
    """Identify HDF5 by payload signature, never by filename suffix."""
    try:
        return bool(h5py.is_hdf5(path))
    except OSError:
        return False


def _validate_hdf_manifest_artifact(
    path: Path,
    *,
    artifact_type: str,
    manifest_path: str,
    loader: Callable[[dict[str, Any]], Any],
    hdf_validator: Callable[[h5py.File, Any, int], None],
    root_artifact_type_required: bool = True,
) -> ValidityResult:
    try:
        with h5py.File(path, "r") as h5:
            _validate_root_artifact_type(
                h5,
                artifact_type,
                required=root_artifact_type_required,
            )
            data = _read_hdf_artifact_manifest_mapping(h5, manifest_path)
            schema_result = _validate_serialized_mapping(data, artifact_type)
            if isinstance(schema_result, ValidityResult):
                return schema_result
            schema_version = schema_result
            data = _normalize_artifact_manifest_json(
                data,
                artifact_type=artifact_type,
                schema_version=schema_version,
            )
            _validate_strict_serialized_mapping(
                data,
                artifact_type,
                schema_version=schema_version,
            )
            _validate_optional_root_schema_version(
                h5,
                artifact_type,
                expected=schema_version,
            )
            manifest = _load_and_validate_serialized_artifact(data, loader)
            hdf_validator(h5, manifest, schema_version)
            return _result(
                artifact_type,
                ValidityOutcome.VALID,
                schema_version=schema_version,
                warnings=_artifact_validation_warnings(
                    artifact_type,
                    manifest,
                ),
            )
    except _InvalidArtifact as exc:
        return _result(
            artifact_type,
            ValidityOutcome.INVALID,
            reason_codes=(exc.code,),
            errors=(exc.message,),
        )
    except _UnreadableArtifact as exc:
        return _result(
            artifact_type,
            ValidityOutcome.UNREADABLE,
            reason_codes=(exc.code,),
            errors=(exc.message,),
        )
    except _UnsupportedArtifact as exc:
        return _result(
            artifact_type,
            ValidityOutcome.UNSUPPORTED,
            reason_codes=(exc.code,),
            errors=(exc.message,),
        )
    except OSError:
        return _result(
            artifact_type,
            ValidityOutcome.UNREADABLE,
            reason_codes=("hdf5_unreadable",),
            errors=("HDF5 payload could not be opened",),
        )


def _read_hdf_schema_version(
    h5: h5py.File,
    *,
    attribute: str,
    artifact_type: str,
) -> tuple[int | None, ValidityResult | None]:
    if attribute not in h5.attrs:
        return None, _result(
            artifact_type,
            ValidityOutcome.LEGACY_UNVERSIONED,
            reason_codes=("legacy_unversioned",),
            errors=("HDF5 payload lacks explicit schema version",),
        )
    raw = h5.attrs[attribute]
    if isinstance(raw, (bool, np.bool_)) or not isinstance(raw, (Integral, np.integer)):
        return None, _result(
            artifact_type,
            ValidityOutcome.INVALID,
            reason_codes=("schema_incompatible",),
            errors=("HDF5 schema version must be an integer",),
        )
    try:
        version = read_schema_version(
            {"schema_version": int(raw)},
            artifact_type,
        )
    except NewerSchemaVersionError:
        return None, _result(
            artifact_type,
            ValidityOutcome.UNSUPPORTED,
            reason_codes=("schema_newer_than_supported",),
            errors=("HDF5 schema version requires a newer reader",),
        )
    except SchemaCompatibilityError:
        return None, _result(
            artifact_type,
            ValidityOutcome.INVALID,
            reason_codes=("schema_incompatible",),
            errors=("HDF5 schema version is not supported",),
        )
    return version, None


def _validate_optional_root_schema_version(
    h5: h5py.File,
    artifact_type: str,
    *,
    expected: int,
) -> None:
    if "schema_version" not in h5.attrs:
        return
    version, outcome = _read_hdf_schema_version(
        h5,
        attribute="schema_version",
        artifact_type=artifact_type,
    )
    if outcome is not None:
        if outcome.outcome is ValidityOutcome.UNSUPPORTED:
            raise _UnsupportedArtifact(
                outcome.reason_codes[0],
                outcome.errors[0],
            )
        raise _InvalidArtifact(
            "schema_incompatible",
            "root schema_version is not supported",
        )
    if version != expected:
        raise _InvalidArtifact(
            "schema_version_mismatch",
            "root schema_version does not match embedded manifest",
        )


def _validate_root_artifact_type(
    h5: h5py.File,
    expected: str,
    *,
    required: bool,
) -> None:
    if "artifact_type" not in h5.attrs:
        if required:
            raise _InvalidArtifact(
                "artifact_type_missing",
                "HDF5 artifact_type attribute is required",
            )
        return
    value = decode_h5_string(h5.attrs["artifact_type"])
    if value != expected:
        raise _InvalidArtifact(
            "artifact_type_mismatch",
            f"HDF5 artifact_type {value!r} does not match expected {expected!r}",
        )


def _read_hdf_json_mapping(h5: h5py.File, dataset_path: str) -> dict[str, Any]:
    if dataset_path not in h5:
        raise _InvalidArtifact(
            "missing_required_path",
            "required embedded manifest dataset is missing",
        )
    text = _read_hdf_text(h5[dataset_path])
    try:
        data = _json_loads_strict(text)
    except json.JSONDecodeError as exc:
        raise _UnreadableArtifact(
            "manifest_unreadable",
            "embedded manifest JSON could not be parsed",
        ) from exc
    if not isinstance(data, dict):
        raise _InvalidArtifact(
            "manifest_root_invalid",
            "embedded manifest JSON root must be a mapping",
        )
    return data


def _read_hdf_artifact_manifest_mapping(
    h5: h5py.File,
    dataset_path: str,
) -> dict[str, Any]:
    """Read a versioned manifest before applying schema-specific NaN policy."""
    if dataset_path not in h5:
        raise _InvalidArtifact(
            "missing_required_path",
            "required embedded manifest dataset is missing",
        )
    text = _read_hdf_text(h5[dataset_path])
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise _UnreadableArtifact(
            "manifest_unreadable",
            "embedded manifest JSON could not be parsed",
        ) from exc
    if not isinstance(data, dict):
        raise _InvalidArtifact(
            "manifest_root_invalid",
            "embedded manifest JSON root must be a mapping",
        )
    return data


def _read_hdf_text(dataset: h5py.Dataset) -> str:
    try:
        return decode_h5_string(dataset[()])
    except Exception as exc:  # noqa: BLE001
        raise _UnreadableArtifact(
            "payload_unreadable",
            "required HDF5 text payload could not be read",
        ) from exc


def _require_dataset(h5: h5py.File, path: str) -> h5py.Dataset:
    if path not in h5 or not isinstance(h5[path], h5py.Dataset):
        raise _InvalidArtifact(
            "missing_required_path",
            "required HDF5 dataset is missing",
        )
    return h5[path]


def _require_rank(dataset: h5py.Dataset, rank: int, *, name: str) -> None:
    if dataset.ndim != rank:
        raise _InvalidArtifact(
            "dataset_rank_mismatch",
            f"{name} has an unexpected rank",
        )


def _require_length(dataset: h5py.Dataset, length: int, *, name: str) -> None:
    if dataset.ndim < 1 or int(dataset.shape[0]) != length:
        raise _InvalidArtifact(
            "entry_count_mismatch",
            f"{name} length does not match entry count",
        )


def _require_vector_length(dataset: h5py.Dataset, length: int, *, name: str) -> None:
    _require_rank(dataset, 1, name=name)
    _require_length(dataset, length, name=name)


def _require_numeric_dataset(
    dataset: h5py.Dataset,
    *,
    name: str,
    finite: bool = False,
    nonnegative: bool = False,
) -> np.ndarray | None:
    """Require a real integer/float HDF5 payload, optionally checking values."""
    if not (
        np.issubdtype(dataset.dtype, np.integer)
        or np.issubdtype(dataset.dtype, np.floating)
    ):
        raise _InvalidArtifact(
            "dataset_dtype_invalid",
            f"{name} must use a real numeric dtype",
        )
    if not finite and not nonnegative:
        return None
    values = np.asarray(dataset[()])
    if finite and np.any(~np.isfinite(values)):
        raise _InvalidArtifact(
            "dataset_value_invalid",
            f"{name} must contain only finite values",
        )
    if nonnegative and np.any(values < 0):
        raise _InvalidArtifact(
            "dataset_value_invalid",
            f"{name} must contain only nonnegative values",
        )
    return values


def _require_integer_dataset(
    dataset: h5py.Dataset,
    *,
    name: str,
    nonnegative: bool = False,
    positive: bool = False,
) -> np.ndarray | None:
    """Require an integer HDF5 payload without accepting float coercion."""
    if not np.issubdtype(dataset.dtype, np.integer):
        raise _InvalidArtifact(
            "dataset_dtype_invalid",
            f"{name} must use an integer dtype",
        )
    if not nonnegative and not positive:
        return None
    values = np.asarray(dataset[()])
    if nonnegative and np.any(values < 0):
        raise _InvalidArtifact(
            "dataset_value_invalid",
            f"{name} must contain only nonnegative values",
        )
    if positive and np.any(values <= 0):
        raise _InvalidArtifact(
            "dataset_value_invalid",
            f"{name} must contain only positive values",
        )
    return values


def _require_boolean_dataset(dataset: h5py.Dataset, *, name: str) -> None:
    if not np.issubdtype(dataset.dtype, np.bool_):
        raise _InvalidArtifact(
            "dataset_dtype_invalid",
            f"{name} must use a boolean dtype",
        )


def _read_text_array(dataset: h5py.Dataset, *, name: str) -> list[str]:
    _require_rank(dataset, 1, name=name)
    try:
        return [decode_h5_string(value) for value in dataset[()]]
    except Exception as exc:  # noqa: BLE001
        raise _UnreadableArtifact(
            "payload_unreadable",
            f"{name} could not be read",
        ) from exc


def _read_int_pair_dataset(dataset: h5py.Dataset, *, name: str) -> tuple[int, int]:
    _require_rank(dataset, 1, name=name)
    if dataset.shape[0] != 2:
        raise _InvalidArtifact("dataset_shape_mismatch", f"{name} must contain two values")
    _require_integer_dataset(dataset, name=name)
    values = np.asarray(dataset[()], dtype=np.int64)
    return (int(values[0]), int(values[1]))


def _validate_raw_capture_v2(h5: h5py.File) -> None:
    """Validate the historical raw-capture v2 contract.

    V2 is intentionally narrower than v3. It represents files produced by the
    pre-v3 writer, where root ``artifact_type`` was not emitted, capture-count
    processing flags did not exist, and mask/LCD metadata could be initialized
    after file creation. Do not add v3 requirements here: versioned validation
    must preserve the contract emitted by the historical writer.
    """
    required_paths = (
        "raw/frames_avg",
        "masks/masks_physical",
        "masks/mask_id",
        "masks/family_id",
        "masks/family_params_json",
        "masks/has_mask_array",
        "illumination/illumination_json",
        "illumination/tls_setpoint_nm",
        "illumination/effective_wavelength_nm",
        "camera/frame_extent_json",
        "capture/capture_index",
        "capture/wavelength_index",
        "capture/mask_index",
        "capture/burst_count",
        "capture/completed",
        "capture/plan_json",
        "capture/processing_flags_json",
    )
    for required in required_paths:
        _require_dataset(h5, required)

    frames = _require_dataset(h5, "raw/frames_avg")
    _require_rank(frames, 3, name="raw/frames_avg")
    if frames.shape[1] <= 0 or frames.shape[2] <= 0:
        raise _InvalidArtifact(
            "frame_shape_invalid",
            "raw/frames_avg spatial dimensions must be positive",
        )
    planned_count = int(frames.shape[0])

    plan = _read_hdf_json_mapping(h5, "capture/plan_json")
    wavelengths = plan.get("wavelengths")
    masks = plan.get("masks")
    if not isinstance(wavelengths, list) or not isinstance(masks, list):
        raise _InvalidArtifact("plan_invalid", "capture plan lacks masks or wavelengths")
    if planned_count != len(wavelengths) * len(masks):
        raise _InvalidArtifact(
            "planned_capture_count_mismatch",
            "capture datasets do not match planned capture count",
        )
    if not wavelengths or not masks:
        raise _InvalidArtifact(
            "plan_invalid",
            "capture plan masks and wavelengths must be non-empty",
        )

    masks_physical = _require_dataset(h5, "masks/masks_physical")
    _require_rank(masks_physical, 3, name="masks/masks_physical")
    mask_count = int(_require_dataset(h5, "masks/mask_id").shape[0])
    if masks_physical.shape[0] != mask_count or mask_count != len(masks):
        raise _InvalidArtifact(
            "mask_count_mismatch",
            "mask datasets do not match capture plan mask count",
        )
    for path in (
        "masks/mask_id",
        "masks/family_id",
        "masks/family_params_json",
        "masks/has_mask_array",
    ):
        _require_vector_length(_require_dataset(h5, path), mask_count, name=path)

    wavelength_count = int(
        _require_dataset(h5, "illumination/illumination_json").shape[0]
    )
    if wavelength_count != len(wavelengths):
        raise _InvalidArtifact(
            "wavelength_count_mismatch",
            "illumination datasets do not match capture plan wavelength count",
        )
    for path in (
        "illumination/illumination_json",
        "illumination/tls_setpoint_nm",
        "illumination/effective_wavelength_nm",
    ):
        _require_vector_length(
            _require_dataset(h5, path), wavelength_count, name=path
        )

    capture_paths = (
        "capture/capture_index",
        "capture/wavelength_index",
        "capture/mask_index",
        "capture/burst_count",
        "capture/completed",
        "camera/frame_extent_json",
    )
    for capture_path in capture_paths:
        _require_vector_length(
            _require_dataset(h5, capture_path),
            planned_count,
            name=capture_path,
        )

    completed_dataset = _require_dataset(h5, "capture/completed")
    if not np.issubdtype(completed_dataset.dtype, np.bool_):
        raise _InvalidArtifact(
            "completed_dtype_invalid",
            "capture/completed must use boolean dtype",
        )
    completed = np.asarray(completed_dataset[()], dtype=bool)
    capture_indices = np.asarray(h5["capture/capture_index"][()], dtype=np.int64)
    wavelength_indices = np.asarray(h5["capture/wavelength_index"][()], dtype=np.int64)
    mask_indices = np.asarray(h5["capture/mask_index"][()], dtype=np.int64)
    for row in np.flatnonzero(completed):
        if capture_indices[row] < 0 or capture_indices[row] >= planned_count:
            raise _InvalidArtifact(
                "capture_index_out_of_bounds",
                "completed capture has an invalid capture index",
            )
        if wavelength_indices[row] < 0 or wavelength_indices[row] >= wavelength_count:
            raise _InvalidArtifact(
                "wavelength_index_out_of_bounds",
                "completed capture has an invalid wavelength index",
            )
        if mask_indices[row] < 0 or mask_indices[row] >= mask_count:
            raise _InvalidArtifact(
                "mask_index_out_of_bounds",
                "completed capture has an invalid mask index",
            )

    extents = _read_text_array(
        h5["camera/frame_extent_json"],
        name="camera/frame_extent_json",
    )
    frame_shape = (int(frames.shape[1]), int(frames.shape[2]))
    for row in np.flatnonzero(completed):
        try:
            extent_data = _json_loads_strict(extents[int(row)])
        except json.JSONDecodeError as exc:
            raise _UnreadableArtifact(
                "frame_extent_unreadable",
                "completed capture frame extent JSON could not be parsed",
            ) from exc
        if not isinstance(extent_data, dict):
            raise _InvalidArtifact(
                "frame_extent_invalid",
                "completed capture frame extent must be a mapping",
            )
        coordinate_frame = (
            "sensor_full_frame"
            if extent_data.get("mode") == "full_sensor"
            else "acquired_frame"
        )
        try:
            validate_coordinate_frame_extent(
                coordinate_frame,
                extent_data,
                frame_shape,
            )
        except ValueError as exc:
            raise _InvalidArtifact("frame_extent_invalid", str(exc)) from exc

    flags = _read_hdf_json_mapping(h5, "capture/processing_flags_json")
    if flags.get("raw_capture_schema_version") != int(
        h5.attrs["raw_capture_schema_version"]
    ):
        raise _InvalidArtifact(
            "processing_flags_mismatch",
            "processing flags do not record the raw capture schema version",
        )


def _validate_raw_capture_v3(h5: h5py.File) -> None:
    """Validate the complete current raw-capture schema v3 contract.

    A capture can be incomplete, but it must still retain every v3 metadata
    surface.  The completed bitmap determines which per-capture values must be
    populated; missing schema fields are never used to represent an incomplete
    run.
    """
    root_plan_id = _require_hdf_attribute_text(h5, "plan_id")
    _require_hdf_attribute_integer(h5, "created_at_ns", minimum=0)
    _require_hdf_attribute_text(h5, "software_version")
    root_schema_version = _require_hdf_attribute_integer(
        h5,
        "raw_capture_schema_version",
        minimum=1,
    )
    root_capture_role = _require_hdf_attribute_text(h5, "capture_role")
    _require_hdf_attribute_text(h5, "hdf5_writer_version")

    required_paths = (
        "raw/frames_avg",
        "masks/masks_physical",
        "masks/mask_id",
        "masks/family_id",
        "masks/family_params_json",
        "masks/has_mask_array",
        "illumination/illumination_json",
        "illumination/tls_setpoint_nm",
        "illumination/effective_wavelength_nm",
        "tls/grating",
        "tls/settle_ms",
        "tls/timestamp_ns",
        "tls/status_json",
        "camera/requested_exposure_us",
        "camera/requested_gain_db",
        "camera/readback_exposure_us",
        "camera/readback_gain_db",
        "camera/frame_extent_json",
        "camera/timestamp_ns",
        "camera/status_json",
        "lcd/settle_ms",
        "lcd/display_timestamp_ns",
        "lcd/mapping_policy_json",
        "lcd/metadata_json",
        "profiles/requirements_json",
        "profiles/pupil_profile_id",
        "profiles/camera_profile_id",
        "capture/capture_index",
        "capture/wavelength_index",
        "capture/mask_index",
        "capture/burst_count",
        "capture/completed",
        "capture/plan_json",
        "capture/plan_id",
        "capture/runtime_mode",
        "capture/runtime_policy_json",
        "capture/processing_flags_json",
    )
    for required in required_paths:
        _require_dataset(h5, required)

    plan = _read_hdf_json_mapping(h5, "capture/plan_json")
    (
        plan_id,
        plan_mask_ids,
        plan_illuminations,
        plan_frames_per_capture,
        plan_store_burst,
    ) = _raw_capture_plan_contract(plan)
    capture_plan_id = _read_hdf_scalar_text(h5, "capture/plan_id")
    if root_plan_id != plan_id or capture_plan_id != plan_id:
        raise _InvalidArtifact(
            "plan_id_mismatch",
            "root plan_id, capture/plan_id, and capture/plan_json must agree",
        )

    frames = _require_dataset(h5, "raw/frames_avg")
    _require_rank(frames, 3, name="raw/frames_avg")
    if not np.issubdtype(frames.dtype, np.number):
        raise _InvalidArtifact(
            "frame_dtype_invalid",
            "raw/frames_avg must use a numeric dtype",
        )
    if frames.shape[1] <= 0 or frames.shape[2] <= 0:
        raise _InvalidArtifact(
            "frame_shape_invalid",
            "raw/frames_avg spatial dimensions must be positive",
        )
    planned_count = int(frames.shape[0])
    if planned_count != len(plan_illuminations) * len(plan_mask_ids):
        raise _InvalidArtifact(
            "planned_capture_count_mismatch",
            "capture datasets do not match planned capture count",
        )

    raw_group = h5.get("raw")
    if not isinstance(raw_group, h5py.Group):
        raise _InvalidArtifact("missing_required_path", "raw group is missing")
    raw_store_burst = _require_hdf_attribute_bool(raw_group, "store_burst")
    raw_frames_per_capture = _require_hdf_attribute_integer(
        raw_group,
        "frames_per_capture",
        minimum=1,
    )
    if raw_store_burst != plan_store_burst or raw_frames_per_capture != plan_frames_per_capture:
        raise _InvalidArtifact(
            "raw_plan_metadata_mismatch",
            "raw storage metadata does not match capture/plan_json",
        )
    _read_hdf_attribute_json_mapping(raw_group, "storage_policy_json")
    for attribute in (
        "average_compute_dtype",
        "frames_avg_stored_dtype",
        "burst_stored_dtype",
    ):
        _require_hdf_attribute_text(raw_group, attribute)
    if raw_store_burst:
        burst_frames = _require_dataset(h5, "raw/frames")
        _validate_raw_burst_frames(
            burst_frames,
            planned_count=planned_count,
            frames_per_capture=raw_frames_per_capture,
            frame_shape=(int(frames.shape[1]), int(frames.shape[2])),
        )
    elif "raw/frames" in h5:
        burst_frames = _require_dataset(h5, "raw/frames")
        _validate_raw_burst_frames(
            burst_frames,
            planned_count=planned_count,
            frames_per_capture=raw_frames_per_capture,
            frame_shape=(int(frames.shape[1]), int(frames.shape[2])),
        )

    masks_physical = _require_dataset(h5, "masks/masks_physical")
    _require_rank(masks_physical, 3, name="masks/masks_physical")
    mask_count = int(_require_dataset(h5, "masks/mask_id").shape[0])
    if masks_physical.shape[0] != mask_count or mask_count != len(plan_mask_ids):
        raise _InvalidArtifact(
            "mask_count_mismatch",
            "mask datasets do not match capture plan mask count",
        )
    for path in (
        "masks/mask_id",
        "masks/family_id",
        "masks/family_params_json",
        "masks/has_mask_array",
    ):
        _require_vector_length(_require_dataset(h5, path), mask_count, name=path)
    mask_ids = _read_text_array(_require_dataset(h5, "masks/mask_id"), name="masks/mask_id")
    if mask_ids != plan_mask_ids:
        raise _InvalidArtifact(
            "mask_metadata_mismatch",
            "masks/mask_id does not match capture/plan_json",
        )
    for value in _read_text_array(
        _require_dataset(h5, "masks/family_params_json"),
        name="masks/family_params_json",
    ):
        _read_json_mapping_text(
            value,
            unreadable_code="mask_metadata_unreadable",
            invalid_code="mask_metadata_invalid",
            context="masks/family_params_json",
        )
    has_mask_array_dataset = _require_dataset(h5, "masks/has_mask_array")
    if not np.issubdtype(has_mask_array_dataset.dtype, np.bool_):
        raise _InvalidArtifact(
            "mask_metadata_dtype_invalid",
            "masks/has_mask_array must use boolean dtype",
        )
    has_mask_array = np.asarray(has_mask_array_dataset[()], dtype=bool)

    wavelength_count = int(
        _require_dataset(h5, "illumination/illumination_json").shape[0]
    )
    if wavelength_count != len(plan_illuminations):
        raise _InvalidArtifact(
            "wavelength_count_mismatch",
            "illumination datasets do not match capture plan wavelength count",
        )
    for path in (
        "illumination/illumination_json",
        "illumination/tls_setpoint_nm",
        "illumination/effective_wavelength_nm",
    ):
        _require_vector_length(
            _require_dataset(h5, path), wavelength_count, name=path
        )
    illumination_json = _read_text_array(
        _require_dataset(h5, "illumination/illumination_json"),
        name="illumination/illumination_json",
    )
    tls_setpoints = _read_numeric_vector(
        h5,
        "illumination/tls_setpoint_nm",
        length=wavelength_count,
    )
    effective_wavelengths = _read_numeric_vector(
        h5,
        "illumination/effective_wavelength_nm",
        length=wavelength_count,
    )
    for path in ("tls/grating", "tls/settle_ms", "tls/timestamp_ns"):
        _read_integer_vector(h5, path, length=wavelength_count)
    tls_status_dataset = _require_dataset(h5, "tls/status_json")
    _require_vector_length(
        tls_status_dataset,
        wavelength_count,
        name="tls/status_json",
    )
    tls_statuses = _read_text_array(
        tls_status_dataset,
        name="tls/status_json",
    )

    capture_paths = (
        "capture/capture_index",
        "capture/wavelength_index",
        "capture/mask_index",
        "capture/burst_count",
        "capture/completed",
        "camera/frame_extent_json",
        "camera/requested_exposure_us",
        "camera/requested_gain_db",
        "camera/readback_exposure_us",
        "camera/readback_gain_db",
        "camera/timestamp_ns",
        "camera/status_json",
        "lcd/settle_ms",
        "lcd/display_timestamp_ns",
    )
    for capture_path in capture_paths:
        _require_vector_length(
            _require_dataset(h5, capture_path),
            planned_count,
            name=capture_path,
        )

    completed_dataset = _require_dataset(h5, "capture/completed")
    if not np.issubdtype(completed_dataset.dtype, np.bool_):
        raise _InvalidArtifact(
            "completed_dtype_invalid",
            "capture/completed must use boolean dtype",
        )
    completed = np.asarray(
        completed_dataset[()],
        dtype=bool,
    )
    capture_indices = _read_integer_vector(
        h5,
        "capture/capture_index",
        length=planned_count,
    )
    wavelength_indices = _read_integer_vector(
        h5,
        "capture/wavelength_index",
        length=planned_count,
    )
    mask_indices = _read_integer_vector(
        h5,
        "capture/mask_index",
        length=planned_count,
    )
    burst_counts = _read_integer_vector(
        h5,
        "capture/burst_count",
        length=planned_count,
    )
    capture_schedule_complete = _validate_raw_capture_schedule(
        completed=completed,
        capture_indices=capture_indices,
        wavelength_indices=wavelength_indices,
        mask_indices=mask_indices,
        wavelength_count=wavelength_count,
        mask_count=mask_count,
    )
    camera_statuses = _read_text_array(
        _require_dataset(h5, "camera/status_json"),
        name="camera/status_json",
    )
    _read_numeric_vector(h5, "camera/requested_exposure_us", length=planned_count)
    _read_numeric_vector(h5, "camera/requested_gain_db", length=planned_count)
    _read_numeric_vector(h5, "camera/readback_exposure_us", length=planned_count)
    _read_numeric_vector(h5, "camera/readback_gain_db", length=planned_count)
    _read_integer_vector(h5, "camera/timestamp_ns", length=planned_count)
    _read_integer_vector(h5, "lcd/settle_ms", length=planned_count)
    _read_integer_vector(h5, "lcd/display_timestamp_ns", length=planned_count)
    _read_single_hdf_json_mapping(h5, "lcd/mapping_policy_json")
    _read_single_hdf_json_mapping(h5, "lcd/metadata_json")
    requirements = _read_hdf_json_mapping(h5, "profiles/requirements_json")
    _validate_profile_requirement_ids(h5, requirements)
    _read_hdf_scalar_text(h5, "capture/runtime_mode", allow_empty=True)
    _read_hdf_json_mapping(h5, "capture/runtime_policy_json")

    for row in np.flatnonzero(completed):
        if not has_mask_array[int(mask_indices[row])]:
            raise _InvalidArtifact(
                "mask_payload_missing",
                "completed capture references a mask without a stored physical mask array",
            )
        if burst_counts[row] != raw_frames_per_capture:
            raise _InvalidArtifact(
                "burst_count_mismatch",
                "completed capture burst_count does not match raw frames_per_capture",
            )

    extents = _read_text_array(h5["camera/frame_extent_json"], name="camera/frame_extent_json")
    frame_shape = (int(frames.shape[1]), int(frames.shape[2]))
    for row in np.flatnonzero(completed):
        row_index = int(row)
        extent_data = _read_json_mapping_text(
            extents[row_index],
            unreadable_code="frame_extent_unreadable",
            invalid_code="frame_extent_invalid",
            context="completed capture frame extent",
        )
        coordinate_frame = (
            "sensor_full_frame"
            if extent_data.get("mode") == "full_sensor"
            else "acquired_frame"
        )
        try:
            strict_camera_frame_extent_from_mapping(
                extent_data,
                field_name="completed capture frame extent",
            )
            validate_coordinate_frame_extent(
                coordinate_frame,
                extent_data,
                frame_shape,
            )
        except ValueError as exc:
            raise _InvalidArtifact("frame_extent_invalid", str(exc)) from exc
        _read_json_mapping_text(
            camera_statuses[row_index],
            unreadable_code="camera_status_unreadable",
            invalid_code="camera_status_invalid",
            context="completed capture camera status",
        )
        wavelength_index = int(wavelength_indices[row_index])
        illumination = _illumination_identity_from_json_text(
            illumination_json[wavelength_index],
            context=f"illumination/illumination_json[{wavelength_index}]",
            code="illumination_metadata_invalid",
        )
        if not _illumination_identity_equal(
            illumination,
            plan_illuminations[wavelength_index],
        ):
            raise _InvalidArtifact(
                "illumination_metadata_mismatch",
                "illumination metadata does not match capture/plan_json",
            )
        _validate_hdf_illumination_numeric_metadata(
            tls_setpoint=float(tls_setpoints[wavelength_index]),
            effective_wavelength=float(effective_wavelengths[wavelength_index]),
            identity=illumination,
        )
        _read_json_mapping_text(
            tls_statuses[wavelength_index],
            unreadable_code="tls_status_unreadable",
            invalid_code="tls_status_invalid",
            context="active illumination TLS status",
        )

    flags = _read_hdf_json_mapping(h5, "capture/processing_flags_json")
    _validate_raw_processing_flags(
        flags,
        schema_version=root_schema_version,
        capture_role=root_capture_role,
        completed=completed,
        capture_indices=capture_indices,
        planned_count=planned_count,
        capture_schedule_complete=capture_schedule_complete,
    )


def _validate_raw_capture_schedule(
    *,
    completed: np.ndarray,
    capture_indices: np.ndarray,
    wavelength_indices: np.ndarray,
    mask_indices: np.ndarray,
    wavelength_count: int,
    mask_count: int,
) -> bool:
    """Validate committed rows against the wavelength-major Cartesian plan."""
    planned_count = wavelength_count * mask_count
    completed_rows = [int(row) for row in np.flatnonzero(completed)]
    for row in completed_rows:
        if capture_indices[row] < 0 or capture_indices[row] >= planned_count:
            raise _InvalidArtifact(
                "capture_index_out_of_bounds",
                "completed capture has an invalid capture index",
            )
        if wavelength_indices[row] < 0 or wavelength_indices[row] >= wavelength_count:
            raise _InvalidArtifact(
                "wavelength_index_out_of_bounds",
                "completed capture has an invalid wavelength index",
            )
        if mask_indices[row] < 0 or mask_indices[row] >= mask_count:
            raise _InvalidArtifact(
                "mask_index_out_of_bounds",
                "completed capture has an invalid mask index",
            )

    committed_capture_indices = [
        int(capture_indices[row]) for row in completed_rows
    ]
    if len(set(committed_capture_indices)) != len(committed_capture_indices):
        raise _InvalidArtifact(
            "capture_index_duplicate",
            "completed capture_index values must be unique",
        )

    committed_combinations = [
        (int(wavelength_indices[row]), int(mask_indices[row]))
        for row in completed_rows
    ]
    expected_capture_indices = set(range(planned_count))
    expected_combinations = {
        (wavelength_index, mask_index)
        for wavelength_index in range(wavelength_count)
        for mask_index in range(mask_count)
    }
    all_rows_committed = bool(np.all(completed))
    if all_rows_committed and (
        set(committed_capture_indices) != expected_capture_indices
        or set(committed_combinations) != expected_combinations
    ):
        raise _InvalidArtifact(
            "capture_schedule_incomplete",
            "completed capture rows do not cover the full capture-plan Cartesian schedule",
        )
    if len(set(committed_combinations)) != len(committed_combinations):
        raise _InvalidArtifact(
            "capture_combination_duplicate",
            "completed wavelength_index and mask_index combinations must be unique",
        )
    for capture_index, (wavelength_index, mask_index) in zip(
        committed_capture_indices,
        committed_combinations,
        strict=True,
    ):
        expected_capture_index = wavelength_index * mask_count + mask_index
        if capture_index != expected_capture_index:
            raise _InvalidArtifact(
                "capture_schedule_mismatch",
                "capture_index does not match wavelength-major capture-plan ordering",
            )
    return all_rows_committed


def _validate_raw_burst_frames(
    dataset: h5py.Dataset,
    *,
    planned_count: int,
    frames_per_capture: int,
    frame_shape: tuple[int, int],
) -> None:
    _require_rank(dataset, 4, name="raw/frames")
    if not np.issubdtype(dataset.dtype, np.number):
        raise _InvalidArtifact(
            "frame_dtype_invalid",
            "raw/frames must use a numeric dtype",
        )
    if (
        dataset.shape[0] != planned_count
        or dataset.shape[1] != frames_per_capture
        or tuple(int(value) for value in dataset.shape[2:]) != frame_shape
    ):
        raise _InvalidArtifact(
            "burst_frame_shape_mismatch",
            "raw/frames must match planned capture count, burst count, and averaged frame shape",
        )


def _require_hdf_attribute_text(
    container: h5py.File | h5py.Group,
    name: str,
) -> str:
    if name not in container.attrs:
        raise _InvalidArtifact(
            "missing_required_attribute",
            f"required HDF5 attribute {name!r} is missing",
        )
    value = container.attrs[name]
    if not isinstance(value, (str, bytes, np.bytes_)):
        raise _InvalidArtifact(
            "attribute_type_invalid",
            f"HDF5 attribute {name!r} must be a non-empty string",
        )
    try:
        text = decode_h5_string(value).strip()
    except Exception as exc:  # noqa: BLE001 - decoding failures are unreadable payloads.
        raise _UnreadableArtifact(
            "attribute_unreadable",
            f"HDF5 attribute {name!r} could not be decoded",
        ) from exc
    if not text:
        raise _InvalidArtifact(
            "attribute_value_invalid",
            f"HDF5 attribute {name!r} must be non-empty",
        )
    return text


def _require_hdf_attribute_integer(
    container: h5py.File | h5py.Group,
    name: str,
    *,
    minimum: int | None = None,
) -> int:
    if name not in container.attrs:
        raise _InvalidArtifact(
            "missing_required_attribute",
            f"required HDF5 attribute {name!r} is missing",
        )
    value = container.attrs[name]
    if isinstance(value, (bool, np.bool_)) or not isinstance(
        value,
        (Integral, np.integer),
    ):
        raise _InvalidArtifact(
            "attribute_type_invalid",
            f"HDF5 attribute {name!r} must be an integer",
        )
    integer = int(value)
    if minimum is not None and integer < minimum:
        raise _InvalidArtifact(
            "attribute_value_invalid",
            f"HDF5 attribute {name!r} is below its required minimum",
        )
    return integer


def _require_hdf_attribute_bool(
    container: h5py.File | h5py.Group,
    name: str,
) -> bool:
    if name not in container.attrs:
        raise _InvalidArtifact(
            "missing_required_attribute",
            f"required HDF5 attribute {name!r} is missing",
        )
    value = container.attrs[name]
    if not isinstance(value, (bool, np.bool_)):
        raise _InvalidArtifact(
            "attribute_type_invalid",
            f"HDF5 attribute {name!r} must be boolean",
        )
    return bool(value)


def _read_hdf_attribute_json_mapping(
    container: h5py.File | h5py.Group,
    name: str,
) -> dict[str, Any]:
    text = _require_hdf_attribute_text(container, name)
    return _read_json_mapping_text(
        text,
        unreadable_code="metadata_unreadable",
        invalid_code="metadata_invalid",
        context=f"HDF5 attribute {name!r}",
    )


def _read_hdf_scalar_text(
    h5: h5py.File,
    path: str,
    *,
    allow_empty: bool = False,
) -> str:
    dataset = _require_dataset(h5, path)
    if dataset.ndim != 0:
        raise _InvalidArtifact(
            "dataset_rank_mismatch",
            f"{path} must be a scalar text dataset",
        )
    text = _read_hdf_text(dataset).strip()
    if not allow_empty and not text:
        raise _InvalidArtifact(
            "metadata_value_invalid",
            f"{path} must be non-empty",
        )
    return text


def _read_single_hdf_json_mapping(h5: h5py.File, path: str) -> dict[str, Any]:
    dataset = _require_dataset(h5, path)
    _require_vector_length(dataset, 1, name=path)
    value = _read_text_array(dataset, name=path)[0]
    return _read_json_mapping_text(
        value,
        unreadable_code="metadata_unreadable",
        invalid_code="metadata_invalid",
        context=path,
    )


def _read_json_mapping_text(
    text: str,
    *,
    unreadable_code: str,
    invalid_code: str,
    context: str,
) -> dict[str, Any]:
    try:
        data = _json_loads_strict(text)
    except json.JSONDecodeError as exc:
        raise _UnreadableArtifact(
            unreadable_code,
            f"{context} could not be parsed as JSON",
        ) from exc
    if not isinstance(data, dict):
        raise _InvalidArtifact(invalid_code, f"{context} JSON root must be a mapping")
    return data


def _read_numeric_vector(
    h5: h5py.File,
    path: str,
    *,
    length: int,
) -> np.ndarray:
    dataset = _require_dataset(h5, path)
    _require_vector_length(dataset, length, name=path)
    if not np.issubdtype(dataset.dtype, np.number):
        raise _InvalidArtifact(
            "metadata_dtype_invalid",
            f"{path} must use a numeric dtype",
        )
    return np.asarray(dataset[()], dtype=np.float64)


def _read_integer_vector(
    h5: h5py.File,
    path: str,
    *,
    length: int,
) -> np.ndarray:
    dataset = _require_dataset(h5, path)
    _require_vector_length(dataset, length, name=path)
    if not np.issubdtype(dataset.dtype, np.integer):
        raise _InvalidArtifact(
            "metadata_dtype_invalid",
            f"{path} must use an integer dtype",
        )
    return np.asarray(dataset[()], dtype=np.int64)


def _raw_capture_plan_contract(
    plan: Mapping[str, Any],
) -> tuple[str, list[str], list[_IlluminationIdentity], int, bool]:
    """Validate the current writer's serialized capture-plan fields."""
    plan_id = plan.get("plan_id")
    if not isinstance(plan_id, str) or not plan_id.strip():
        raise _InvalidArtifact("plan_invalid", "capture plan plan_id must be non-empty")
    masks = plan.get("masks")
    wavelengths = plan.get("wavelengths")
    if not isinstance(masks, list) or not masks:
        raise _InvalidArtifact("plan_invalid", "capture plan masks must be a non-empty array")
    if not isinstance(wavelengths, list) or not wavelengths:
        raise _InvalidArtifact(
            "plan_invalid",
            "capture plan wavelengths must be a non-empty array",
        )
    mask_ids: list[str] = []
    for index, entry in enumerate(masks):
        if not isinstance(entry, Mapping):
            raise _InvalidArtifact("plan_invalid", "capture plan mask entries must be mappings")
        mask_id = entry.get("mask_id")
        if not isinstance(mask_id, str) or not mask_id.strip():
            raise _InvalidArtifact(
                "plan_invalid",
                f"capture plan masks[{index}].mask_id must be non-empty",
            )
        mask_ids.append(mask_id)
    illuminations: list[_IlluminationIdentity] = []
    for index, entry in enumerate(wavelengths):
        if not isinstance(entry, Mapping):
            raise _InvalidArtifact(
                "plan_invalid",
                "capture plan wavelength entries must be mappings",
            )
        illumination = entry.get("illumination")
        illuminations.append(
            _illumination_identity_from_mapping(
                illumination,
                context=f"capture plan wavelengths[{index}].illumination",
                code="plan_invalid",
            )
        )
    camera = plan.get("camera")
    if not isinstance(camera, Mapping):
        raise _InvalidArtifact("plan_invalid", "capture plan camera must be a mapping")
    frames_per_capture = camera.get("frames_per_capture")
    if isinstance(frames_per_capture, bool) or not isinstance(frames_per_capture, int):
        raise _InvalidArtifact(
            "plan_invalid",
            "capture plan camera.frames_per_capture must be an integer",
        )
    if frames_per_capture < 1:
        raise _InvalidArtifact(
            "plan_invalid",
            "capture plan camera.frames_per_capture must be positive",
        )
    store_burst = plan.get("store_burst")
    if not isinstance(store_burst, bool):
        raise _InvalidArtifact("plan_invalid", "capture plan store_burst must be boolean")
    return (
        plan_id.strip(),
        mask_ids,
        illuminations,
        int(frames_per_capture),
        store_burst,
    )


def _validate_profile_requirement_ids(
    h5: h5py.File,
    requirements: Mapping[str, Any],
) -> None:
    for field in ("pupil_profile_id", "camera_profile_id"):
        expected = requirements.get(field, "")
        if expected is None:
            expected = ""
        if not isinstance(expected, str):
            raise _InvalidArtifact(
                "profile_requirements_invalid",
                f"profiles/requirements_json.{field} must be a string when present",
            )
        actual = _read_hdf_scalar_text(h5, f"profiles/{field}", allow_empty=True)
        if actual != expected:
            raise _InvalidArtifact(
                "profile_requirements_mismatch",
                f"profiles/{field} does not match profiles/requirements_json",
            )


def _validate_hdf_illumination_numeric_metadata(
    *,
    tls_setpoint: float,
    effective_wavelength: float,
    identity: _IlluminationIdentity,
) -> None:
    if identity.mode == "broadband_passthrough":
        if not math.isnan(effective_wavelength) or not math.isclose(
            tls_setpoint,
            0.0,
            rel_tol=0.0,
            abs_tol=1e-9,
        ):
            raise _InvalidArtifact(
                "illumination_metadata_mismatch",
                "broadband illumination metadata must use NaN effective wavelength and zero TLS setpoint",
            )
        return
    assert identity.effective_wavelength_nm is not None
    if not math.isclose(
        effective_wavelength,
        identity.effective_wavelength_nm,
        rel_tol=0.0,
        abs_tol=1e-9,
    ):
        raise _InvalidArtifact(
            "illumination_metadata_mismatch",
            "effective wavelength metadata does not match capture plan",
        )
    if identity.tls_setpoint_nm is None:
        if not math.isnan(tls_setpoint):
            raise _InvalidArtifact(
                "illumination_metadata_mismatch",
                "null monochromatic TLS setpoint must be stored as NaN",
            )
    elif not math.isclose(
        tls_setpoint,
        identity.tls_setpoint_nm,
        rel_tol=0.0,
        abs_tol=1e-9,
    ):
        raise _InvalidArtifact(
            "illumination_metadata_mismatch",
            "TLS setpoint metadata does not match capture plan",
        )


def _validate_raw_processing_flags(
    flags: Mapping[str, Any],
    *,
    schema_version: int,
    capture_role: str,
    completed: np.ndarray,
    capture_indices: np.ndarray,
    planned_count: int,
    capture_schedule_complete: bool,
) -> None:
    for field in (
        "scientific_calibration_valid",
        "optical_alignment_validated",
        "training_ready",
        "capture_complete",
        "run_succeeded",
    ):
        if not isinstance(flags.get(field), bool):
            raise _InvalidArtifact(
                "processing_flags_invalid",
                f"processing_flags_json.{field} must be boolean",
            )
    if flags.get("raw_capture_schema_version") != schema_version:
        raise _InvalidArtifact(
            "processing_flags_mismatch",
            "processing flags do not record the raw capture schema version",
        )
    if flags.get("capture_role") != capture_role:
        raise _InvalidArtifact(
            "processing_flags_mismatch",
            "processing flags do not record the root capture role",
        )
    if flags.get("error") is not None and not isinstance(flags.get("error"), str):
        raise _InvalidArtifact(
            "processing_flags_invalid",
            "processing_flags_json.error must be a string or null",
        )
    for field in (
        "last_completed_capture_index",
        "n_captures_written",
        "n_captures_total",
    ):
        value = flags.get(field)
        if isinstance(value, bool) or not isinstance(value, int):
            raise _InvalidArtifact(
                "processing_flags_invalid",
                f"processing_flags_json.{field} must be an integer",
            )
    completed_count = int(np.count_nonzero(completed))
    if flags["n_captures_written"] != completed_count:
        raise _InvalidArtifact(
            "processing_flags_mismatch",
            "processing flags written capture count does not match capture/completed",
        )
    if flags["n_captures_total"] != planned_count:
        raise _InvalidArtifact(
            "processing_flags_mismatch",
            "processing flags planned capture count does not match raw frame rows",
        )
    if flags["capture_complete"] != capture_schedule_complete:
        raise _InvalidArtifact(
            "processing_flags_mismatch",
            "processing flags capture_complete state does not match the validated capture schedule",
        )
    error = flags["error"]
    if flags["run_succeeded"] != (error is None):
        raise _InvalidArtifact(
            "processing_flags_mismatch",
            "processing flags run_succeeded must equal whether error is null",
        )
    last_completed = flags["last_completed_capture_index"]
    if last_completed < -1 or last_completed >= planned_count:
        raise _InvalidArtifact(
            "processing_flags_invalid",
            "processing flags last completed capture index is out of bounds",
        )
    completed_rows = np.flatnonzero(completed)
    expected_last = (
        int(capture_indices[int(completed_rows[-1])])
        if completed_rows.size
        else -1
    )
    if last_completed != expected_last:
        raise _InvalidArtifact(
            "processing_flags_mismatch",
            "last_completed_capture_index does not match the last committed row",
        )


@dataclass(frozen=True)
class _IlluminationIdentity:
    """Strict, mode-aware identity for one capture-plan illumination row."""

    mode: str
    effective_wavelength_nm: float | None
    tls_setpoint_nm: float | None
    wavelength_label_nm: float | None


def _source_plan_index_contract(
    h5: h5py.File,
) -> tuple[list[str], list[_IlluminationIdentity]]:
    """Read the source capture-plan identity table used by derived HDF5 data."""
    plan = _read_hdf_json_mapping(h5, "source/plan_json")
    masks = plan.get("masks")
    wavelengths = plan.get("wavelengths")
    if not isinstance(masks, list) or not masks:
        raise _InvalidArtifact(
            "source_plan_invalid",
            "source plan must declare a non-empty masks array",
        )
    if not isinstance(wavelengths, list) or not wavelengths:
        raise _InvalidArtifact(
            "source_plan_invalid",
            "source plan must declare a non-empty wavelengths array",
        )

    mask_ids: list[str] = []
    for index, entry in enumerate(masks):
        if not isinstance(entry, Mapping):
            raise _InvalidArtifact(
                "source_plan_invalid",
                "source plan mask entries must be mappings",
            )
        mask_id = entry.get("mask_id")
        if not isinstance(mask_id, str) or not mask_id.strip():
            raise _InvalidArtifact(
                "source_plan_invalid",
                f"source plan masks[{index}].mask_id must be a non-empty string",
            )
        mask_ids.append(mask_id)

    illumination_identities: list[_IlluminationIdentity] = []
    for index, entry in enumerate(wavelengths):
        if not isinstance(entry, Mapping):
            raise _InvalidArtifact(
                "source_plan_invalid",
                "source plan wavelength entries must be mappings",
            )
        illumination = entry.get("illumination")
        if not isinstance(illumination, Mapping):
            raise _InvalidArtifact(
                "source_plan_invalid",
                f"source plan wavelengths[{index}].illumination must be a mapping",
            )
        illumination_identities.append(
            _illumination_identity_from_mapping(
                illumination,
                context=f"source plan wavelengths[{index}].illumination",
                code="source_plan_invalid",
            )
        )

    _validate_optional_mask_table_against_source_plan(h5, mask_ids)
    return mask_ids, illumination_identities


def _illumination_identity_from_mapping(
    data: Mapping[str, Any],
    *,
    context: str,
    code: str,
) -> _IlluminationIdentity:
    """Validate a serialized illumination mapping without compatibility coercion."""
    if not isinstance(data, Mapping):
        raise _InvalidArtifact(code, f"{context} must be a mapping")
    for field in (
        "mode",
        "effective_wavelength_nm",
        "tls_setpoint_nm",
        "wavelength_label_nm",
    ):
        if field not in data:
            raise _InvalidArtifact(code, f"{context}.{field} is required")
    mode = data["mode"]
    if not isinstance(mode, str) or not mode.strip():
        raise _InvalidArtifact(code, f"{context}.mode must be a non-empty string")
    effective = _illumination_identity_number(
        data["effective_wavelength_nm"],
        context=f"{context}.effective_wavelength_nm",
        code=code,
    )
    tls_setpoint = _illumination_identity_number(
        data["tls_setpoint_nm"],
        context=f"{context}.tls_setpoint_nm",
        code=code,
    )
    wavelength_label = _illumination_identity_number(
        data["wavelength_label_nm"],
        context=f"{context}.wavelength_label_nm",
        code=code,
    )
    normalized_mode = mode.strip()
    if normalized_mode == "monochromatic":
        if effective is None or effective <= 0.0:
            raise _InvalidArtifact(
                code,
                f"{context}.effective_wavelength_nm must be finite and positive for monochromatic illumination",
            )
        if tls_setpoint is not None and tls_setpoint <= 0.0:
            raise _InvalidArtifact(
                code,
                f"{context}.tls_setpoint_nm must be finite and positive or null for monochromatic illumination",
            )
    elif normalized_mode == "broadband_passthrough":
        if effective is not None:
            raise _InvalidArtifact(
                code,
                f"{context}.effective_wavelength_nm must be null for broadband_passthrough",
            )
        if tls_setpoint != 0.0:
            raise _InvalidArtifact(
                code,
                f"{context}.tls_setpoint_nm must be 0.0 for broadband_passthrough",
            )
        if wavelength_label is not None:
            raise _InvalidArtifact(
                code,
                f"{context}.wavelength_label_nm must be null for broadband_passthrough",
            )
    else:
        raise _InvalidArtifact(code, f"{context}.mode is not supported")
    return _IlluminationIdentity(
        mode=normalized_mode,
        effective_wavelength_nm=effective,
        tls_setpoint_nm=tls_setpoint,
        wavelength_label_nm=wavelength_label,
    )


def _illumination_identity_number(
    value: Any,
    *,
    context: str,
    code: str,
) -> float | None:
    if value is None:
        return None
    if isinstance(value, (bool, np.bool_)) or not isinstance(
        value,
        (int, float, np.integer, np.floating),
    ):
        raise _InvalidArtifact(code, f"{context} must be a finite JSON number or null")
    number = float(value)
    if not math.isfinite(number):
        raise _InvalidArtifact(code, f"{context} must be finite when present")
    return number


def _illumination_identity_from_json_text(
    text: str,
    *,
    context: str,
    code: str,
) -> _IlluminationIdentity:
    try:
        data = _json_loads_strict(text)
    except json.JSONDecodeError as exc:
        raise _UnreadableArtifact(
            "entry_illumination_unreadable",
            f"{context} could not be parsed as JSON",
        ) from exc
    if not isinstance(data, Mapping):
        raise _InvalidArtifact(code, f"{context} JSON root must be a mapping")
    return _illumination_identity_from_mapping(data, context=context, code=code)


def _illumination_identity_equal(
    left: _IlluminationIdentity,
    right: _IlluminationIdentity,
) -> bool:
    return (
        left.mode == right.mode
        and _same_optional_float(
            left.effective_wavelength_nm,
            right.effective_wavelength_nm,
        )
        and _same_optional_float(left.tls_setpoint_nm, right.tls_setpoint_nm)
        and _same_optional_float(
            left.wavelength_label_nm,
            right.wavelength_label_nm,
        )
    )


def _same_optional_float(left: float | None, right: float | None) -> bool:
    if left is None or right is None:
        return left is right
    return math.isclose(left, right, rel_tol=0.0, abs_tol=1e-9)


def _same_wavelength(left: float, right: float) -> bool:
    """Compare numeric wavelengths while preserving the broadband NaN sentinel."""
    left_value = float(left)
    right_value = float(right)
    if math.isnan(left_value) or math.isnan(right_value):
        return math.isnan(left_value) and math.isnan(right_value)
    return math.isclose(left_value, right_value, rel_tol=0.0, abs_tol=1e-9)


def _entry_wavelength_matches_illumination_identity(
    wavelength: float,
    identity: _IlluminationIdentity,
) -> bool:
    """Compare survey/dictionary wavelength metadata to its mode-aware identity."""
    if identity.mode == "broadband_passthrough":
        return math.isnan(float(wavelength))
    assert identity.effective_wavelength_nm is not None
    return math.isfinite(float(wavelength)) and math.isclose(
        float(wavelength),
        identity.effective_wavelength_nm,
        rel_tol=0.0,
        abs_tol=1e-9,
    )


def _validate_optional_mask_table_against_source_plan(
    h5: h5py.File,
    source_mask_ids: list[str],
) -> None:
    """Require an emitted mask table to agree with source-plan mask identity."""
    if "mask_table" not in h5:
        return
    group = h5.get("mask_table")
    if not isinstance(group, h5py.Group):
        raise _InvalidArtifact("mask_table_invalid", "mask_table must be a group")
    table_ids = _read_text_array(
        _require_dataset(h5, "mask_table/mask_ids"),
        name="mask_table/mask_ids",
    )
    if table_ids != source_mask_ids:
        raise _InvalidArtifact(
            "source_plan_metadata_mismatch",
            "mask_table mask_ids do not match source plan mask IDs",
        )


def _read_integer_index_vector(
    h5: h5py.File,
    path: str,
    *,
    entry_count: int,
) -> np.ndarray:
    """Read an index vector without coercing non-integer HDF5 data."""
    dataset = _require_dataset(h5, path)
    _require_vector_length(dataset, entry_count, name=path)
    if not np.issubdtype(dataset.dtype, np.integer):
        raise _InvalidArtifact(
            "index_dtype_invalid",
            f"{path} must use an integer dtype",
        )
    return np.asarray(dataset[()], dtype=np.int64)


def _read_vlen_integer_index_rows(
    h5: h5py.File,
    path: str,
    *,
    entry_count: int,
) -> list[np.ndarray]:
    """Read one integer capture-index vector per entry without coercion."""
    dataset = _require_dataset(h5, path)
    _require_vector_length(dataset, entry_count, name=path)
    base_dtype = h5py.check_dtype(vlen=dataset.dtype)
    if base_dtype is None or not np.issubdtype(base_dtype, np.integer):
        raise _InvalidArtifact(
            "index_dtype_invalid",
            f"{path} must use a variable-length integer dtype",
        )
    rows: list[np.ndarray] = []
    for index, raw in enumerate(dataset[()]):
        values = np.asarray(raw, dtype=np.int64)
        if values.ndim != 1 or values.size == 0:
            raise _InvalidArtifact(
                "capture_indices_invalid",
                f"{path}[{index}] must contain at least one capture index",
            )
        rows.append(values)
    return rows


def _validate_capture_index_in_bounds(
    capture_index: int,
    *,
    planned_capture_count: int,
) -> None:
    """Check a derived entry's source capture index without assuming capture order."""
    if capture_index < 0 or capture_index >= planned_capture_count:
        raise _InvalidArtifact(
            "capture_index_out_of_bounds",
            "entry capture index is outside the source capture plan",
        )


def _validate_full_frame_psf_survey_payload(
    h5: h5py.File,
    manifest: Any,
    schema_version: int,
) -> None:
    group = h5.get("full_frame_survey")
    if not isinstance(group, h5py.Group):
        raise _InvalidArtifact("missing_required_path", "full_frame_survey group is missing")
    frames = _require_dataset(h5, "full_frame_survey/frames_avg")
    _require_rank(frames, 3, name="full_frame_survey/frames_avg")
    _require_numeric_dataset(frames, name="full_frame_survey/frames_avg")
    entry_count = int(frames.shape[0])
    if tuple(int(v) for v in frames.shape[1:]) != tuple(manifest.frame_shape):
        raise _InvalidArtifact(
            "frame_shape_mismatch",
            "survey frame dataset does not match embedded manifest frame_shape",
        )
    required_entry_paths = (
        "full_frame_survey/entry_mask_ids",
        "full_frame_survey/entry_wavelength_nm",
        "full_frame_survey/entry_illumination_json",
        "full_frame_survey/mask_index",
        "full_frame_survey/wavelength_index",
        "full_frame_survey/capture_indices",
    )
    for required in required_entry_paths:
        _require_vector_length(
            _require_dataset(h5, required), entry_count, name=required
        )
    frame_shape = _read_int_pair_dataset(
        _require_dataset(h5, "full_frame_survey/frame_shape"),
        name="full_frame_survey/frame_shape",
    )
    if frame_shape != tuple(manifest.frame_shape):
        raise _InvalidArtifact(
            "frame_shape_mismatch",
            "survey frame_shape metadata does not match embedded manifest",
        )
    extent = _read_hdf_json_mapping(h5, "full_frame_survey/camera_frame_extent_json")
    try:
        if schema_version >= 2:
            strict_camera_frame_extent_from_mapping(
                extent,
                field_name="full_frame_survey.camera_frame_extent",
            )
        validate_coordinate_frame_extent(
            "sensor_full_frame",
            extent,
            tuple(manifest.frame_shape),
            require_full_sensor=True,
        )
    except ValueError as exc:
        raise _InvalidArtifact("frame_extent_invalid", str(exc)) from exc
    if _canonical_mapping(extent) != _canonical_mapping(manifest.camera_frame_extent):
        raise _InvalidArtifact(
            "manifest_metadata_mismatch",
            "survey frame extent does not match embedded manifest",
        )
    entry_mask_ids = _read_text_array(
        h5["full_frame_survey/entry_mask_ids"],
        name="entry_mask_ids",
    )
    _require_manifest_sequence_match(
        entry_mask_ids,
        manifest.entry_mask_ids,
        name="entry_mask_ids",
    )
    entry_wavelengths = np.asarray(
        h5["full_frame_survey/entry_wavelength_nm"][()],
        dtype=np.float64,
    )
    _require_numeric_sequence_match(
        entry_wavelengths,
        manifest.entry_wavelengths_nm,
        name="entry_wavelength_nm",
    )
    entry_illumination_json = _read_text_array(
        h5["full_frame_survey/entry_illumination_json"],
        name="entry_illumination_json",
    )
    _require_manifest_sequence_match(
        entry_illumination_json,
        manifest.entry_illumination_json,
        name="entry_illumination_json",
    )
    _require_manifest_sequence_match(
        _read_text_array(
            _require_dataset(h5, "full_frame_survey/unique_mask_ids"),
            name="unique_mask_ids",
        ),
        manifest.unique_mask_ids,
        name="unique_mask_ids",
    )
    _require_numeric_sequence_match(
        _require_dataset(h5, "full_frame_survey/unique_wavelength_nm")[()],
        manifest.unique_wavelengths_nm,
        name="unique_wavelength_nm",
    )
    policy = _read_hdf_json_mapping(h5, "full_frame_survey/survey_policy_json")
    if policy != manifest.survey_policy:
        raise _InvalidArtifact(
            "manifest_metadata_mismatch",
            "survey_policy_json does not match embedded manifest",
        )
    source_mask_ids, source_illuminations = _source_plan_index_contract(h5)
    entry_illuminations = [
        _illumination_identity_from_json_text(
            value,
            context=f"full_frame_survey.entry_illumination_json[{index}]",
            code="entry_illumination_invalid",
        )
        for index, value in enumerate(entry_illumination_json)
    ]
    mask_indices = _read_integer_index_vector(
        h5,
        "full_frame_survey/mask_index",
        entry_count=entry_count,
    )
    wavelength_indices = _read_integer_index_vector(
        h5,
        "full_frame_survey/wavelength_index",
        entry_count=entry_count,
    )
    capture_indices = _read_integer_index_vector(
        h5,
        "full_frame_survey/capture_indices",
        entry_count=entry_count,
    )
    for index in range(entry_count):
        mask_index = int(mask_indices[index])
        wavelength_index = int(wavelength_indices[index])
        capture_index = int(capture_indices[index])
        if mask_index < 0 or mask_index >= len(source_mask_ids):
            raise _InvalidArtifact(
                "mask_index_out_of_bounds",
                "survey mask_index is outside the source capture plan",
            )
        if wavelength_index < 0 or wavelength_index >= len(source_illuminations):
            raise _InvalidArtifact(
                "wavelength_index_out_of_bounds",
                "survey wavelength_index is outside the source capture plan",
            )
        if entry_mask_ids[index] != source_mask_ids[mask_index]:
            raise _InvalidArtifact(
                "entry_mask_id_mismatch",
                "survey entry_mask_ids do not match source plan mask IDs",
            )
        source_illumination = source_illuminations[wavelength_index]
        if not _entry_wavelength_matches_illumination_identity(
            float(entry_wavelengths[index]),
            source_illumination,
        ):
            raise _InvalidArtifact(
                "entry_wavelength_mismatch",
                "survey entry wavelengths do not match source plan wavelengths",
            )
        if not _illumination_identity_equal(
            entry_illuminations[index],
            source_illumination,
        ):
            raise _InvalidArtifact(
                "entry_illumination_mismatch",
                "survey entry illumination metadata does not match the source capture plan",
            )
        _validate_capture_index_in_bounds(
            capture_index,
            planned_capture_count=len(source_mask_ids) * len(source_illuminations),
        )
        expected_capture_index = wavelength_index * len(source_mask_ids) + mask_index
        if capture_index != expected_capture_index:
            raise _InvalidArtifact(
                "source_capture_binding_mismatch",
                "survey capture_index does not match its wavelength_index and mask_index",
            )
    if "survey_id" in h5.attrs and decode_h5_string(h5.attrs["survey_id"]) != manifest.survey_id:
        raise _InvalidArtifact(
            "manifest_metadata_mismatch",
            "HDF5 survey_id does not match embedded manifest",
        )


def _validate_peak_support_analysis_payload(
    h5: h5py.File,
    manifest: Any,
    _schema_version: int,
) -> None:
    support = h5.get("support_analysis")
    if not isinstance(support, h5py.Group):
        raise _InvalidArtifact("missing_required_path", "support_analysis group is missing")
    tau = _require_dataset(h5, "support_analysis/tau_values")
    radii = _require_dataset(h5, "support_analysis/support_radii")
    _require_numeric_dataset(
        tau,
        name="support_analysis/tau_values",
        finite=True,
    )
    _require_numeric_dataset(
        radii,
        name="support_analysis/support_radii",
        finite=True,
        nonnegative=True,
    )
    _require_numeric_sequence_match(tau[()], manifest.tau_values, name="tau_values")
    _require_numeric_sequence_match(radii[()], manifest.support_radii, name="support_radii")
    frame_shape = _read_int_pair_dataset(
        _require_dataset(h5, "support_analysis/frame_shape"),
        name="support_analysis/frame_shape",
    )
    if frame_shape != tuple(manifest.frame_shape):
        raise _InvalidArtifact(
            "frame_shape_mismatch",
            "support report frame_shape does not match embedded manifest",
        )
    entry_count = len(manifest.entry_mask_ids)
    expected_shapes = {
        "support_analysis/background_value": (entry_count,),
        "support_analysis/center_xy": (entry_count, 2),
        "support_analysis/total_corr_energy": (entry_count,),
        "support_analysis/compact_support_energy": (entry_count, len(manifest.support_radii)),
        "support_analysis/compact_support_fraction": (entry_count, len(manifest.support_radii)),
        "support_analysis/far_field_noise_energy": (entry_count, len(manifest.tau_values)),
        "support_analysis/far_field_significant_energy": (entry_count, len(manifest.tau_values)),
        "support_analysis/far_field_noise_pixel_count": (entry_count, len(manifest.tau_values)),
        "support_analysis/far_field_significant_pixel_count": (entry_count, len(manifest.tau_values)),
    }
    for path, expected_shape in expected_shapes.items():
        dataset = _require_dataset(h5, path)
        if tuple(dataset.shape) != expected_shape:
            raise _InvalidArtifact(
                "dataset_shape_mismatch",
                f"{path} has an unexpected shape",
            )
    for path in (
        "support_analysis/background_value",
        "support_analysis/center_xy",
    ):
        _require_numeric_dataset(
            h5[path],
            name=path,
            finite=True,
        )
    for path in (
        "support_analysis/total_corr_energy",
        "support_analysis/compact_support_energy",
        "support_analysis/compact_support_fraction",
        "support_analysis/far_field_noise_energy",
        "support_analysis/far_field_significant_energy",
    ):
        _require_numeric_dataset(
            h5[path],
            name=path,
            finite=True,
            nonnegative=True,
        )
    for path in (
        "support_analysis/far_field_noise_pixel_count",
        "support_analysis/far_field_significant_pixel_count",
    ):
        _require_integer_dataset(
            h5[path],
            name=path,
            nonnegative=True,
        )
    component_policy = manifest.component_policy
    analysis_mode = component_policy.get("analysis_mode")
    expected_components = component_policy.get("component_table_written")
    if analysis_mode not in {"energy_only", "component_table"}:
        raise _InvalidArtifact(
            "component_policy_invalid",
            "component_policy.analysis_mode must be energy_only or component_table",
        )
    if not isinstance(expected_components, bool):
        raise _InvalidArtifact(
            "component_policy_invalid",
            "component_policy.component_table_written must be boolean",
        )
    if expected_components != (analysis_mode == "component_table"):
        raise _InvalidArtifact(
            "component_policy_invalid",
            "component_policy analysis_mode and component_table_written disagree",
        )
    actual_components = "components" in h5
    if actual_components != expected_components:
        raise _InvalidArtifact(
            "component_table_presence_mismatch",
            "components group presence does not match manifest component_policy",
        )
    if actual_components:
        components = h5.get("components")
        if not isinstance(components, h5py.Group):
            raise _InvalidArtifact(
                "component_table_invalid",
                "components must be an HDF5 group",
            )
        _validate_component_table(
            components,
            entry_count,
            manifest.tau_values,
            manifest.entry_mask_ids,
            manifest.entry_wavelengths_nm,
            manifest.frame_shape,
        )


def _validate_component_table(
    group: h5py.Group,
    entry_count: int,
    tau_values: list[float],
    entry_mask_ids: list[str],
    entry_wavelengths_nm: list[float],
    frame_shape: tuple[int, int],
) -> None:
    required = (
        "entry_index",
        "tau",
        "component_id",
        "bbox_xyxy",
        "centroid_xy",
        "centroid_xy_abs",
        "centroid_xy_rel",
        "area",
        "energy",
        "peak_value",
        "mean_value",
        "max_radius",
        "max_radius_from_energy_center",
        "is_far_field",
        "mask_id",
        "wavelength_nm",
    )
    for name in required:
        if name not in group or not isinstance(group[name], h5py.Dataset):
            raise _InvalidArtifact(
                "component_table_incomplete",
                "component table is missing a required field",
            )
    count = int(group["entry_index"].shape[0])
    for name in required:
        _require_length(group[name], count, name=f"components/{name}")
    for name, width in (
        ("bbox_xyxy", 4),
        ("centroid_xy", 2),
        ("centroid_xy_abs", 2),
        ("centroid_xy_rel", 2),
    ):
        if tuple(group[name].shape) != (count, width):
            raise _InvalidArtifact(
                "component_table_shape_mismatch",
                f"components/{name} has an unexpected shape",
            )
    for name in ("entry_index", "component_id", "bbox_xyxy"):
        _require_integer_dataset(
            group[name],
            name=f"components/{name}",
            nonnegative=True,
        )
    _require_integer_dataset(
        group["area"],
        name="components/area",
        positive=True,
    )
    for name in (
        "tau",
        "centroid_xy",
        "centroid_xy_abs",
        "centroid_xy_rel",
    ):
        _require_numeric_dataset(
            group[name],
            name=f"components/{name}",
            finite=True,
        )
    for name in (
        "energy",
        "peak_value",
        "mean_value",
        "max_radius",
        "max_radius_from_energy_center",
    ):
        _require_numeric_dataset(
            group[name],
            name=f"components/{name}",
            finite=True,
            nonnegative=True,
        )
    _require_boolean_dataset(
        group["is_far_field"],
        name="components/is_far_field",
    )
    _require_numeric_dataset(
        group["wavelength_nm"],
        name="components/wavelength_nm",
    )
    indices = np.asarray(group["entry_index"][()], dtype=np.int64)
    if np.any(indices < 0) or np.any(indices >= entry_count):
        raise _InvalidArtifact(
            "component_entry_out_of_bounds",
            "component table contains an invalid entry index",
        )
    taus = np.asarray(group["tau"][()], dtype=np.float64)
    if any(not any(math.isclose(float(value), float(item)) for item in tau_values) for value in taus):
        raise _InvalidArtifact(
            "component_tau_invalid",
            "component table contains a tau not declared by the manifest",
        )
    component_ids = np.asarray(group["component_id"][()], dtype=np.int64)
    areas = np.asarray(group["area"][()], dtype=np.int64)
    if np.any(component_ids < 0) or np.any(areas <= 0):
        raise _InvalidArtifact(
            "component_table_invalid",
            "component IDs must be nonnegative and component areas positive",
        )
    bboxes = np.asarray(group["bbox_xyxy"][()], dtype=np.int64)
    height, width = frame_shape
    if (
        np.any(bboxes[:, 0] < 0)
        or np.any(bboxes[:, 1] < 0)
        or np.any(bboxes[:, 2] <= bboxes[:, 0])
        or np.any(bboxes[:, 3] <= bboxes[:, 1])
        or np.any(bboxes[:, 2] > width)
        or np.any(bboxes[:, 3] > height)
    ):
        raise _InvalidArtifact(
            "component_bbox_invalid",
            "component table bounding boxes lie outside the frame",
        )
    mask_ids = _read_text_array(group["mask_id"], name="components/mask_id")
    wavelengths = np.asarray(group["wavelength_nm"][()], dtype=np.float64)
    for index, entry_index in enumerate(indices):
        if mask_ids[index] != entry_mask_ids[int(entry_index)]:
            raise _InvalidArtifact(
                "component_metadata_mismatch",
                "component mask_id does not match its source entry",
            )
        if not _same_wavelength(
            float(wavelengths[index]),
            float(entry_wavelengths_nm[int(entry_index)]),
        ):
            raise _InvalidArtifact(
                "component_metadata_mismatch",
                "component wavelength does not match its source entry",
            )


def _validate_peak_patch_psf_dictionary_payload(
    h5: h5py.File,
    manifest: Any,
    schema_version: int,
) -> None:
    group = h5.get("peak_patch_dictionary")
    if not isinstance(group, h5py.Group):
        raise _InvalidArtifact(
            "missing_required_path",
            "peak_patch_dictionary group is missing",
        )
    patches = _require_dataset(h5, "peak_patch_dictionary/patches")
    _require_rank(patches, 4, name="peak_patch_dictionary/patches")
    _require_numeric_dataset(patches, name="peak_patch_dictionary/patches")
    entry_count = len(manifest.entry_mask_ids)
    peak_count = len(manifest.peak_ids)
    if patches.shape[0] != entry_count or patches.shape[1] != peak_count:
        raise _InvalidArtifact(
            "entry_count_mismatch",
            "patch tensor entry or peak dimensions do not match manifest",
        )
    for shape in manifest.patch_shape_hw:
        if tuple(int(v) for v in shape) != tuple(int(v) for v in patches.shape[2:]):
            raise _InvalidArtifact(
                "patch_shape_mismatch",
                "patch tensor shape does not match embedded manifest",
            )
    required_entry_paths = [
        "peak_patch_dictionary/entry_mask_ids",
        "peak_patch_dictionary/entry_mask_index",
        "peak_patch_dictionary/entry_wavelength_nm",
        "peak_patch_dictionary/entry_capture_indices",
    ]
    if schema_version >= 2:
        required_entry_paths.append(
            "peak_patch_dictionary/entry_wavelength_index"
        )
    for required in required_entry_paths:
        _require_vector_length(
            _require_dataset(h5, required), entry_count, name=required
        )
    entry_mask_ids = _read_text_array(
        h5["peak_patch_dictionary/entry_mask_ids"],
        name="entry_mask_ids",
    )
    _require_manifest_sequence_match(
        entry_mask_ids,
        manifest.entry_mask_ids,
        name="entry_mask_ids",
    )
    entry_wavelengths = np.asarray(
        h5["peak_patch_dictionary/entry_wavelength_nm"][()],
        dtype=np.float64,
    )
    _require_numeric_sequence_match(
        entry_wavelengths,
        manifest.entry_wavelengths_nm,
        name="entry_wavelength_nm",
    )
    _require_manifest_sequence_match(
        _read_text_array(
            _require_dataset(h5, "peak_patch_dictionary/unique_mask_ids"),
            name="unique_mask_ids",
        ),
        manifest.unique_mask_ids,
        name="unique_mask_ids",
    )
    _require_numeric_sequence_match(
        _require_dataset(
            h5,
            "peak_patch_dictionary/unique_wavelength_nm",
        )[()],
        manifest.unique_wavelengths_nm,
        name="unique_wavelength_nm",
    )
    _require_manifest_sequence_match(
        _read_text_array(h5["peak_patch_dictionary/peak_id"], name="peak_id"),
        manifest.peak_ids,
        name="peak_id",
    )
    for path, values, name in (
        (
            "peak_patch_dictionary/patch_shape_hw",
            manifest.patch_shape_hw,
            "patch_shape_hw",
        ),
        (
            "peak_patch_dictionary/patch_origin_xy",
            manifest.patch_origin_xy,
            "patch_origin_xy",
        ),
    ):
        dataset = _require_dataset(h5, path)
        if tuple(dataset.shape) != (peak_count, 2):
            raise _InvalidArtifact(
                "dataset_shape_mismatch",
                f"{name} metadata has an unexpected shape",
            )
        _require_integer_dataset(dataset, name=path, nonnegative=True)
        if not np.array_equal(np.asarray(dataset[()], dtype=np.int64), np.asarray(values, dtype=np.int64)):
            raise _InvalidArtifact(
                "manifest_metadata_mismatch",
                f"{name} does not match embedded manifest",
            )
    frame_shape = _read_int_pair_dataset(
        _require_dataset(h5, "peak_patch_dictionary/frame_shape"),
        name="peak_patch_dictionary/frame_shape",
    )
    if frame_shape != tuple(manifest.frame_shape):
        raise _InvalidArtifact(
            "frame_shape_mismatch",
            "dictionary frame_shape does not match embedded manifest",
        )
    coordinate_frame = _read_hdf_text(
        _require_dataset(h5, "peak_patch_dictionary/coordinate_frame")
    )
    if coordinate_frame != manifest.peak_layout_coordinate_frame:
        raise _InvalidArtifact(
            "coordinate_frame_mismatch",
            "dictionary coordinate_frame does not match embedded manifest",
        )
    dictionary_extent = _read_hdf_json_mapping(
        h5,
        "peak_patch_dictionary/camera_frame_extent_json",
    )
    layout_extent = _read_hdf_json_mapping(
        h5,
        "peak_patch_dictionary/peak_layout_camera_frame_extent_json",
    )
    if schema_version >= 2:
        try:
            strict_camera_frame_extent_from_mapping(
                dictionary_extent,
                field_name="peak_patch_dictionary.camera_frame_extent",
            )
            strict_camera_frame_extent_from_mapping(
                layout_extent,
                field_name="peak_patch_dictionary.peak_layout_camera_frame_extent",
            )
        except ValueError as exc:
            raise _InvalidArtifact("frame_extent_invalid", str(exc)) from exc
    if _canonical_mapping(dictionary_extent) != _canonical_mapping(manifest.camera_frame_extent):
        raise _InvalidArtifact(
            "manifest_metadata_mismatch",
            "dictionary frame extent does not match embedded manifest",
        )
    if _canonical_mapping(layout_extent) != _canonical_mapping(
        manifest.peak_layout_camera_frame_extent
    ):
        raise _InvalidArtifact(
            "manifest_metadata_mismatch",
            "layout frame extent does not match embedded manifest",
        )
    if schema_version >= 2:
        extent_compatibility = _read_hdf_json_mapping(
            h5,
            "peak_patch_dictionary/extent_compatibility_json",
        )
        if extent_compatibility != manifest.extent_compatibility:
            raise _InvalidArtifact(
                "manifest_metadata_mismatch",
                "extent_compatibility_json does not match embedded manifest",
            )
    background_policy = _read_hdf_json_mapping(
        h5,
        "peak_patch_dictionary/background_policy_json",
    )
    normalization_policy = _read_hdf_json_mapping(
        h5,
        "peak_patch_dictionary/normalization_policy_json",
    )
    if background_policy.get("applied") != manifest.applied_background_policy:
        raise _InvalidArtifact(
            "manifest_metadata_mismatch",
            "background_policy_json does not match embedded manifest",
        )
    if normalization_policy.get("applied") != manifest.applied_normalization_policy:
        raise _InvalidArtifact(
            "manifest_metadata_mismatch",
            "normalization_policy_json does not match embedded manifest",
        )
    source_mask_ids, source_illuminations = _source_plan_index_contract(h5)
    mask_indices = _read_integer_index_vector(
        h5,
        "peak_patch_dictionary/entry_mask_index",
        entry_count=entry_count,
    )
    wavelength_indices = (
        _read_integer_index_vector(
            h5,
            "peak_patch_dictionary/entry_wavelength_index",
            entry_count=entry_count,
        )
        if schema_version >= 2
        else None
    )
    capture_index_rows = _read_vlen_integer_index_rows(
        h5,
        "peak_patch_dictionary/entry_capture_indices",
        entry_count=entry_count,
    )
    for index in range(entry_count):
        mask_index = int(mask_indices[index])
        if mask_index < 0 or mask_index >= len(source_mask_ids):
            raise _InvalidArtifact(
                "mask_index_out_of_bounds",
                "dictionary entry_mask_index is outside the source capture plan",
            )
        if entry_mask_ids[index] != source_mask_ids[mask_index]:
            raise _InvalidArtifact(
                "entry_mask_id_mismatch",
                "dictionary entry_mask_ids do not match source plan mask IDs",
            )
        if wavelength_indices is not None:
            wavelength_index = int(wavelength_indices[index])
        else:
            decoded_wavelength_indices = {
                int(capture_index) // len(source_mask_ids)
                for capture_index in capture_index_rows[index]
            }
            if len(decoded_wavelength_indices) != 1:
                raise _InvalidArtifact(
                    "source_capture_binding_mismatch",
                    "legacy dictionary entry captures do not share one wavelength index",
                )
            wavelength_index = decoded_wavelength_indices.pop()
        if wavelength_index < 0 or wavelength_index >= len(source_illuminations):
            raise _InvalidArtifact(
                "wavelength_index_out_of_bounds",
                "dictionary entry_wavelength_index is outside the source capture plan",
            )
        if not _entry_wavelength_matches_illumination_identity(
            float(entry_wavelengths[index]),
            source_illuminations[wavelength_index],
        ):
            raise _InvalidArtifact(
                "entry_wavelength_mismatch",
                "dictionary entry wavelengths do not match the source capture plan",
            )
        if len(set(int(value) for value in capture_index_rows[index])) != len(
            capture_index_rows[index]
        ):
            raise _InvalidArtifact(
                "source_capture_binding_mismatch",
                "dictionary entry_capture_indices must not contain duplicates",
            )
        expected_capture_index = wavelength_index * len(source_mask_ids) + mask_index
        for capture_index in capture_index_rows[index]:
            _validate_capture_index_in_bounds(
                int(capture_index),
                planned_capture_count=len(source_mask_ids) * len(source_illuminations),
            )
            if int(capture_index) != expected_capture_index:
                raise _InvalidArtifact(
                    "source_capture_binding_mismatch",
                    "dictionary capture index does not match its wavelength and mask indices",
                )
    if "dictionary_id" in h5.attrs and decode_h5_string(
        h5.attrs["dictionary_id"]
    ) != manifest.dictionary_id:
        raise _InvalidArtifact(
            "manifest_metadata_mismatch",
            "HDF5 dictionary_id does not match embedded manifest",
        )


def _require_manifest_sequence_match(
    actual: list[str],
    expected: list[str],
    *,
    name: str,
) -> None:
    if actual != list(expected):
        raise _InvalidArtifact(
            "manifest_metadata_mismatch",
            f"{name} does not match embedded manifest",
        )


def _require_numeric_sequence_match(
    actual: Any,
    expected: list[float],
    *,
    name: str,
) -> None:
    raw_actual = np.asarray(actual)
    if not (
        np.issubdtype(raw_actual.dtype, np.integer)
        or np.issubdtype(raw_actual.dtype, np.floating)
    ):
        raise _InvalidArtifact(
            "dataset_dtype_invalid",
            f"{name} must use a real numeric dtype",
        )
    actual_arr = np.asarray(raw_actual, dtype=np.float64)
    expected_arr = np.asarray(expected, dtype=np.float64)
    if actual_arr.shape != expected_arr.shape or not np.array_equal(
        actual_arr,
        expected_arr,
        equal_nan=True,
    ):
        raise _InvalidArtifact(
            "manifest_metadata_mismatch",
            f"{name} does not match embedded manifest",
        )


def _canonical_mapping(value: Mapping[str, Any]) -> dict[str, Any]:
    try:
        extent = camera_frame_extent_from_dict(value)
    except ValueError:
        return dict(value)
    return camera_frame_extent_to_dict(extent)


def _safe_validation_message(exc: Exception, fallback: str) -> str:
    """Keep useful contract detail without retaining an absolute local path."""
    message = str(exc).strip()
    if not message:
        return fallback
    absolute_path = re.search(
        r"(?<![A-Za-z0-9])(?:[A-Za-z]:[\\/]|\\\\|/)[^\s\"']+",
        message,
    )
    if len(message) > 512 or absolute_path is not None:
        return fallback
    return message


VALIDATOR_REGISTRY: dict[str, ArtifactValidator] = {
    "camera_profile": _validate_camera_profile,
    "pupil_profile": _validate_pupil_profile,
    "sensor_energy_center_profile": _validate_sensor_energy_center_profile,
    "peak_layout_profile": _validate_peak_layout_profile,
    "full_frame_psf_survey": _validate_full_frame_psf_survey,
    "peak_support_analysis_report": _validate_peak_support_analysis_report,
    "peak_patch_psf_dictionary": _validate_peak_patch_psf_dictionary,
    "raw_capture": _validate_raw_capture,
}


__all__ = [
    "ArtifactValidator",
    "VALIDATOR_REGISTRY",
    "ValidityOutcome",
    "ValidityResult",
    "check_validity",
    "read_validated_manifest_mapping",
]
