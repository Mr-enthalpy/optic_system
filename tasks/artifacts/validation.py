from __future__ import annotations

"""Local structural validation for measured artifacts.

Validation answers whether a supplied local artifact is readable and internally
consistent with its declared contract. It does not register artifacts, select a
generation, promote scientific trust, or infer identity from filenames.
"""

import importlib
import json
import math
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
    SchemaCompatibilityError,
    read_schema_version,
    schema_compat,
)

from .coordinate_frame import (
    camera_frame_extent_from_dict,
    camera_frame_extent_to_dict,
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
        _validate_strict_serialized_mapping(data, artifact_type)
        _load_and_validate_serialized_artifact(data, loader)
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
    )


def _read_json_file(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise _UnreadableArtifact("location_unreadable", "artifact location could not be read") from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise _UnreadableArtifact("json_unreadable", "artifact JSON could not be parsed") from exc
    if not isinstance(data, dict):
        raise _UnreadableArtifact("json_root_unreadable", "artifact JSON root is not a mapping")
    return data


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
            raise _InvalidArtifact(
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
    validator(data)


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
    for field in ("camera_frame_extent", "bg_policy", "corr_policy", "aggregation_policy"):
        _strict_required_mapping(data, field)
    _strict_pair(_strict_required(data, "center_xy"), "center_xy")
    _strict_pair(_strict_required(data, "global_center_std_xy"), "global_center_std_xy")
    _strict_number(_strict_required(data, "max_center_deviation_px"), "max_center_deviation_px")
    _strict_pair_list(data, "per_entry_center_xy")
    _strict_string_list(data, "per_entry_mask_ids")
    _strict_number_list(data, "per_entry_wavelengths_nm")
    _strict_number_list(data, "per_entry_background_value", required=False)
    _strict_number_list(data, "per_entry_total_corr_energy", required=False)
    if "per_entry_fallback_used" in data:
        for index, value in enumerate(_strict_list(data["per_entry_fallback_used"], "per_entry_fallback_used")):
            if not isinstance(value, bool):
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
    _strict_required_mapping(data, "camera_frame_extent")
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
    _strict_number_list(data, "survey_wavelengths_nm")
    _strict_string_list(data, "survey_mask_ids")
    _strict_number_list(data, "valid_wavelengths_nm")
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
    _strict_number_list(data, "entry_wavelengths_nm")
    _strict_string_list(data, "entry_illumination_json")
    _strict_string_list(data, "entry_mask_ids")
    _strict_number_list(data, "unique_wavelengths_nm")
    _strict_string_list(data, "unique_mask_ids")
    _strict_pair(_strict_required(data, "frame_shape"), "frame_shape", integers=True)
    _strict_required_mapping(data, "camera_frame_extent")
    _strict_required_mapping(data, "survey_policy")


def _validate_serialized_peak_support_analysis_report(data: Mapping[str, Any]) -> None:
    for field in ("report_id", "source_survey_h5", "coordinate_frame"):
        _strict_required_string(data, field)
    _strict_pair(_strict_required(data, "frame_shape"), "frame_shape", integers=True)
    for field in (
        "camera_frame_extent",
        "bg_policy",
        "corr_policy",
        "radial_policy",
        "component_policy",
    ):
        _strict_required_mapping(data, field)
    _strict_number_list(data, "tau_values")
    _strict_number_list(data, "support_radii")
    _strict_string_list(data, "entry_mask_ids")
    _strict_number_list(data, "entry_wavelengths_nm")
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
    _strict_number_list(data, "entry_wavelengths_nm")
    _strict_string_list(data, "entry_mask_ids")
    _strict_number_list(data, "unique_wavelengths_nm")
    _strict_string_list(data, "unique_mask_ids")
    _strict_pair(_strict_required(data, "frame_shape"), "frame_shape", integers=True)
    _strict_required_mapping(data, "camera_frame_extent")
    _strict_required_mapping(data, "peak_layout_camera_frame_extent")
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
                data = _read_hdf_json_mapping(h5, embedded_path)
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
        _validate_strict_serialized_mapping(data, canonical_type)
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
            _validate_root_artifact_type(h5, artifact_type, required=False)
            schema_version, outcome = _read_hdf_schema_version(
                h5,
                attribute="raw_capture_schema_version",
                artifact_type=artifact_type,
            )
            if outcome is not None:
                return outcome
            _validate_raw_capture_h5(h5)
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
    hdf_validator: Callable[[h5py.File, Any], None],
    root_artifact_type_required: bool = True,
) -> ValidityResult:
    try:
        with h5py.File(path, "r") as h5:
            _validate_root_artifact_type(
                h5,
                artifact_type,
                required=root_artifact_type_required,
            )
            data = _read_hdf_json_mapping(h5, manifest_path)
            schema_result = _validate_serialized_mapping(data, artifact_type)
            if isinstance(schema_result, ValidityResult):
                return schema_result
            schema_version = schema_result
            _validate_strict_serialized_mapping(data, artifact_type)
            _validate_optional_root_schema_version(
                h5,
                artifact_type,
                expected=schema_version,
            )
            manifest = _load_and_validate_serialized_artifact(data, loader)
            hdf_validator(h5, manifest)
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
    values = np.asarray(dataset[()], dtype=np.int64)
    return (int(values[0]), int(values[1]))


def _validate_raw_capture_h5(h5: h5py.File) -> None:
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
    completed = np.asarray(
        completed_dataset[()],
        dtype=bool,
    )
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

    extents = _read_text_array(h5["camera/frame_extent_json"], name="camera/frame_extent_json")
    frame_shape = (int(frames.shape[1]), int(frames.shape[2]))
    for row in np.flatnonzero(completed):
        try:
            extent_data = json.loads(extents[int(row)])
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
    if flags.get("raw_capture_schema_version") != int(h5.attrs["raw_capture_schema_version"]):
        raise _InvalidArtifact(
            "processing_flags_mismatch",
            "processing flags do not record the raw capture schema version",
        )


def _source_plan_index_contract(h5: h5py.File) -> tuple[list[str], list[float]]:
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

    wavelength_values: list[float] = []
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
        value = illumination.get("effective_wavelength_nm")
        if value is None:
            value = illumination.get("wavelength_label_nm")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise _InvalidArtifact(
                "source_plan_invalid",
                f"source plan wavelengths[{index}] lacks a numeric effective wavelength",
            )
        wavelength = float(value)
        if not math.isfinite(wavelength):
            raise _InvalidArtifact(
                "source_plan_invalid",
                f"source plan wavelengths[{index}] has a non-finite effective wavelength",
            )
        wavelength_values.append(wavelength)

    _validate_optional_mask_table_against_source_plan(h5, mask_ids)
    return mask_ids, wavelength_values


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


def _validate_full_frame_psf_survey_payload(h5: h5py.File, manifest: Any) -> None:
    group = h5.get("full_frame_survey")
    if not isinstance(group, h5py.Group):
        raise _InvalidArtifact("missing_required_path", "full_frame_survey group is missing")
    frames = _require_dataset(h5, "full_frame_survey/frames_avg")
    _require_rank(frames, 3, name="full_frame_survey/frames_avg")
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
    _require_manifest_sequence_match(
        _read_text_array(
            h5["full_frame_survey/entry_illumination_json"],
            name="entry_illumination_json",
        ),
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
    source_mask_ids, source_wavelengths = _source_plan_index_contract(h5)
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
        if wavelength_index < 0 or wavelength_index >= len(source_wavelengths):
            raise _InvalidArtifact(
                "wavelength_index_out_of_bounds",
                "survey wavelength_index is outside the source capture plan",
            )
        if entry_mask_ids[index] != source_mask_ids[mask_index]:
            raise _InvalidArtifact(
                "entry_mask_id_mismatch",
                "survey entry_mask_ids do not match source plan mask IDs",
            )
        if not math.isclose(
            float(entry_wavelengths[index]),
            source_wavelengths[wavelength_index],
            rel_tol=0.0,
            abs_tol=1e-9,
        ):
            raise _InvalidArtifact(
                "entry_wavelength_mismatch",
                "survey entry wavelengths do not match source plan wavelengths",
            )
        _validate_capture_index_in_bounds(
            capture_index,
            planned_capture_count=len(source_mask_ids) * len(source_wavelengths),
        )
    if "survey_id" in h5.attrs and decode_h5_string(h5.attrs["survey_id"]) != manifest.survey_id:
        raise _InvalidArtifact(
            "manifest_metadata_mismatch",
            "HDF5 survey_id does not match embedded manifest",
        )


def _validate_peak_support_analysis_payload(h5: h5py.File, manifest: Any) -> None:
    support = h5.get("support_analysis")
    if not isinstance(support, h5py.Group):
        raise _InvalidArtifact("missing_required_path", "support_analysis group is missing")
    tau = _require_dataset(h5, "support_analysis/tau_values")
    radii = _require_dataset(h5, "support_analysis/support_radii")
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
    if "components" in h5:
        _validate_component_table(
            h5["components"],
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
        if not math.isclose(
            float(wavelengths[index]),
            float(entry_wavelengths_nm[int(entry_index)]),
        ):
            raise _InvalidArtifact(
                "component_metadata_mismatch",
                "component wavelength does not match its source entry",
            )


def _validate_peak_patch_psf_dictionary_payload(h5: h5py.File, manifest: Any) -> None:
    group = h5.get("peak_patch_dictionary")
    if not isinstance(group, h5py.Group):
        raise _InvalidArtifact(
            "missing_required_path",
            "peak_patch_dictionary group is missing",
        )
    patches = _require_dataset(h5, "peak_patch_dictionary/patches")
    _require_rank(patches, 4, name="peak_patch_dictionary/patches")
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
    for required in (
        "peak_patch_dictionary/entry_mask_ids",
        "peak_patch_dictionary/entry_mask_index",
        "peak_patch_dictionary/entry_wavelength_nm",
        "peak_patch_dictionary/entry_capture_indices",
    ):
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
    source_mask_ids, source_wavelengths = _source_plan_index_contract(h5)
    mask_indices = _read_integer_index_vector(
        h5,
        "peak_patch_dictionary/entry_mask_index",
        entry_count=entry_count,
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
        matching_wavelength_indices = [
            wavelength_index
            for wavelength_index, source_wavelength in enumerate(source_wavelengths)
            if math.isclose(
                float(entry_wavelengths[index]),
                source_wavelength,
                rel_tol=0.0,
                abs_tol=1e-9,
            )
        ]
        if not matching_wavelength_indices:
            raise _InvalidArtifact(
                "entry_wavelength_mismatch",
                "dictionary entry wavelengths do not match the source capture plan",
            )
        for capture_index in capture_index_rows[index]:
            _validate_capture_index_in_bounds(
                int(capture_index),
                planned_capture_count=len(source_mask_ids) * len(source_wavelengths),
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
    actual_arr = np.asarray(actual, dtype=np.float64)
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
    """Keep useful contract detail without retaining an absolute Windows path."""
    message = str(exc).strip()
    if not message:
        return fallback
    if len(message) > 512 or (":\\" in message and "\n" not in message):
        return fallback
    return message


VALIDATOR_REGISTRY: dict[str, ArtifactValidator] = {
    "camera_profile": _validate_camera_profile,
    "pupil_profile": _validate_pupil_profile,
    "sensor_energy_center_profile": _validate_sensor_energy_center_profile,
    "peak_layout_profile": _validate_peak_layout_profile,
    "full_frame_psf_survey": _validate_full_frame_psf_survey,
    "peak_support_analysis_report": _validate_peak_support_analysis_report_h5,
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
