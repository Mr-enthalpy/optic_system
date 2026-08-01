from __future__ import annotations

"""Exact JSON contracts for versioned measured-artifact manifests.

Version 1 preserves the historical path-bearing representation.  Version 2
uses artifact identities and is writable only after explicit migration.
"""

from collections.abc import Mapping
import math
from typing import Any

from tasks.artifact_versioning import (
    CURRENT_MANIFEST_SCHEMA_VERSIONS,
    LegacyUnversionedArtifactError,
    read_schema_version,
)

from .validation import (
    ArtifactRepresentation,
    SCHEMA_ADAPTER_REGISTRY,
    SchemaAdapter,
    SerializedSchemaError,
    get_schema_adapter,
    register_schema_adapter,
)
from .identity import ArtifactIdentityError, validate_artifact_id


_DERIVED_TYPES = {
    "full_frame_psf_survey",
    "sensor_energy_center_profile",
    "peak_layout_profile",
    "peak_support_analysis_report",
    "peak_patch_psf_dictionary",
}


_FIELDS: dict[str, dict[int, set[str]]] = {
    "full_frame_psf_survey": {
        1: {
            "artifact_type", "schema_version", "survey_id", "source_raw_capture_h5",
            "pupil_profile_id", "camera_profile_id", "illumination_mode",
            "entry_wavelengths_nm", "entry_illumination_json", "entry_mask_ids",
            "unique_wavelengths_nm", "unique_mask_ids", "frame_shape",
            "camera_frame_extent", "survey_policy", "full_frame_role", "notes",
        },
        2: {
            "artifact_type", "schema_version", "survey_id",
            "source_raw_capture_artifact_id", "pupil_profile_id", "camera_profile_id",
            "illumination_mode", "entry_wavelengths_nm", "entry_illumination_json",
            "entry_mask_ids", "unique_wavelengths_nm", "unique_mask_ids",
            "frame_shape", "camera_frame_extent", "survey_policy", "full_frame_role",
            "notes", "migration",
        },
    },
    "sensor_energy_center_profile": {
        1: {
            "artifact_type", "schema_version", "center_profile_id", "source_survey_h5",
            "coordinate_frame", "camera_frame_extent", "center_xy", "estimator_name",
            "bg_policy", "corr_policy", "aggregation_policy", "per_entry_center_xy",
            "per_entry_mask_ids", "per_entry_wavelengths_nm",
            "per_entry_background_value", "per_entry_total_corr_energy",
            "per_entry_fallback_used", "per_wavelength_mean_center_xy",
            "per_wavelength_center_std_xy", "global_center_std_xy",
            "max_center_deviation_px", "camera_frame_shape", "notes",
        },
        2: {
            "artifact_type", "schema_version", "center_profile_id",
            "source_survey_artifact_id", "coordinate_frame", "camera_frame_extent",
            "center_xy", "estimator_name", "bg_policy", "corr_policy",
            "aggregation_policy", "per_entry_center_xy", "per_entry_mask_ids",
            "per_entry_wavelengths_nm", "per_entry_background_value",
            "per_entry_total_corr_energy", "per_entry_fallback_used",
            "per_wavelength_mean_center_xy", "per_wavelength_center_std_xy",
            "global_center_std_xy", "max_center_deviation_px", "camera_frame_shape",
            "notes", "migration",
        },
    },
    "peak_layout_profile": {
        1: {
            "artifact_type", "schema_version", "peak_layout_id", "source_survey_h5",
            "frame_shape", "coordinate_frame", "camera_frame_extent", "peak_ids",
            "center_xy", "patch_shape_hw", "patch_origin_xy", "stability_score",
            "amplitude_range", "local_background_stats", "survey_wavelengths_nm",
            "survey_mask_ids", "valid_wavelengths_nm", "valid_mask_ids",
            "validity_scope", "detection_policy", "notes", "center_profile_id",
            "energy_center_xy", "center_xy_rel",
        },
        2: {
            "artifact_type", "schema_version", "peak_layout_id",
            "source_survey_artifact_id", "frame_shape", "coordinate_frame",
            "camera_frame_extent", "peak_ids", "center_xy", "patch_shape_hw",
            "patch_origin_xy", "stability_score", "amplitude_range",
            "local_background_stats", "survey_wavelengths_nm", "survey_mask_ids",
            "valid_wavelengths_nm", "valid_mask_ids", "validity_scope",
            "detection_policy", "notes", "center_profile_id", "energy_center_xy",
            "center_xy_rel", "migration",
        },
    },
    "peak_support_analysis_report": {
        1: {
            "artifact_type", "schema_version", "report_id", "source_survey_h5",
            "frame_shape", "coordinate_frame", "camera_frame_extent", "tau_values",
            "support_radii", "bg_policy", "corr_policy", "radial_policy",
            "component_policy", "entry_mask_ids", "entry_wavelengths_nm", "notes",
            "valid_pixel_domain",
        },
        2: {
            "artifact_type", "schema_version", "report_id",
            "source_survey_artifact_id", "frame_shape", "coordinate_frame",
            "camera_frame_extent", "tau_values", "support_radii", "bg_policy",
            "corr_policy", "radial_policy", "component_policy", "entry_mask_ids",
            "entry_wavelengths_nm", "notes", "valid_pixel_domain", "migration",
        },
    },
    "peak_patch_psf_dictionary": {
        1: {
            "artifact_type", "schema_version", "dictionary_id", "source_raw_capture_h5",
            "peak_layout_profile", "pupil_profile_id", "camera_profile_id",
            "illumination_mode", "entry_wavelengths_nm", "entry_mask_ids",
            "unique_wavelengths_nm", "unique_mask_ids", "frame_shape",
            "camera_frame_extent", "peak_layout_coordinate_frame",
            "peak_layout_camera_frame_extent", "peak_ids", "patch_shape_hw",
            "patch_origin_xy", "applied_background_policy",
            "applied_normalization_policy", "notes",
        },
        2: {
            "artifact_type", "schema_version", "dictionary_id",
            "source_raw_capture_artifact_id", "peak_layout_artifact_id",
            "pupil_profile_id", "camera_profile_id", "illumination_mode",
            "entry_wavelengths_nm", "entry_mask_ids", "unique_wavelengths_nm",
            "unique_mask_ids", "frame_shape", "camera_frame_extent",
            "peak_layout_coordinate_frame", "peak_layout_camera_frame_extent",
            "peak_ids", "patch_shape_hw", "patch_origin_xy",
            "applied_background_policy", "applied_normalization_policy",
            "extent_compatibility", "notes", "migration",
        },
    },
}

_OPTIONAL = {
    "pupil_profile_id", "camera_profile_id", "notes", "migration",
    "camera_frame_shape", "center_profile_id", "energy_center_xy", "center_xy_rel",
    "valid_pixel_domain", "entry_illumination_json",
    "per_entry_background_value", "per_entry_total_corr_energy",
    "per_entry_fallback_used", "survey_wavelengths_nm", "survey_mask_ids",
}

_V2_ID_FIELDS = {
    "full_frame_psf_survey": ("source_raw_capture_artifact_id",),
    "sensor_energy_center_profile": ("source_survey_artifact_id",),
    "peak_layout_profile": ("source_survey_artifact_id",),
    "peak_support_analysis_report": ("source_survey_artifact_id",),
    "peak_patch_psf_dictionary": (
        "source_raw_capture_artifact_id", "peak_layout_artifact_id"
    ),
}


def _required(artifact_type: str, version: int) -> frozenset[str]:
    fields = _FIELDS[artifact_type][version]
    required = set(fields) - _OPTIONAL
    if artifact_type == "sensor_energy_center_profile" and version == 2:
        required.update({
            "per_entry_background_value", "per_entry_total_corr_energy",
            "per_entry_fallback_used",
        })
    if artifact_type == "full_frame_psf_survey" and version == 2:
        required.add("entry_illumination_json")
    if artifact_type == "peak_layout_profile" and version == 2:
        required.update({"survey_wavelengths_nm", "survey_mask_ids"})
    if artifact_type == "peak_patch_psf_dictionary" and version == 2:
        required.add("extent_compatibility")
    return frozenset(required)


def _validate_serialized(
    artifact_type: str, version: int, mapping: Mapping[str, Any]
) -> None:
    if mapping.get("artifact_type") != artifact_type:
        raise SerializedSchemaError(
            "schema.artifact_type.mismatch", "artifact_type does not match adapter"
        )
    if mapping.get("schema_version") != version:
        raise SerializedSchemaError(
            "schema.version.mismatch", "schema_version does not match adapter"
        )
    if version == 2:
        for field in _V2_ID_FIELDS[artifact_type]:
            value = mapping.get(field)
            try:
                validate_artifact_id(value, field)
            except ArtifactIdentityError as exc:
                raise SerializedSchemaError(
                    f"provenance.{field}.invalid",
                    str(exc),
                ) from exc
        _require_finite_json(mapping)


def _require_finite_json(value: Any, field: str = "manifest") -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise SerializedSchemaError(
            "schema.number.non_finite", f"{field} contains a non-finite number"
        )
    if isinstance(value, Mapping):
        for key, item in value.items():
            _require_finite_json(item, f"{field}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _require_finite_json(item, f"{field}[{index}]")


def _construct(artifact_type: str, version: int, mapping: Mapping[str, Any]) -> Any:
    data = dict(mapping)
    if artifact_type == "full_frame_psf_survey":
        from tasks.psf.build_full_frame_psf_survey import FullFramePSFSurveyManifest as cls
    elif artifact_type == "sensor_energy_center_profile":
        from tasks.psf.sensor_energy_center import SensorEnergyCenterProfile as cls
    elif artifact_type == "peak_layout_profile":
        from tasks.psf.derive_peak_layout_profile import PeakLayoutProfileManifest as cls
    elif artifact_type == "peak_support_analysis_report":
        from tasks.psf.analyze_diffraction_support import PeakSupportAnalysisManifest as cls
    else:
        from tasks.psf.build_peak_patch_psf_dictionary import PeakPatchPSFDictionaryManifest as cls
    try:
        return cls._from_validated_mapping(data, source_schema_version=version)
    except (ValueError, TypeError, KeyError) as exc:
        raise SerializedSchemaError(
            f"semantic.{artifact_type}.construction_invalid", str(exc)
        ) from exc


def _validate_semantics(artifact_type: str, artifact: Any) -> None:
    try:
        artifact.validate()
    except (ValueError, TypeError, KeyError) as exc:
        raise SerializedSchemaError(
            _semantic_reason_code(artifact_type, str(exc)), str(exc)
        ) from exc


def _semantic_reason_code(artifact_type: str, message: str) -> str:
    """Classify known invariant families without exception-type whitelists."""
    normalized = message.lower()
    if "extent" in normalized:
        category = "coordinate_extent_mismatch"
    elif "wavelength" in normalized:
        category = "wavelength_identity_mismatch"
    elif "policy" in normalized:
        category = "policy_inconsistent"
    elif "artifact_id" in normalized or "source" in normalized:
        category = "dependency_identity_missing"
    elif any(token in normalized for token in ("frame_shape", "patch", "geometry")):
        category = "geometry_invalid"
    elif any(token in normalized for token in ("length", "count", "entries")):
        category = "cardinality_mismatch"
    else:
        category = "invariant_violation"
    return f"semantic.{artifact_type}.{category}"


def register_derived_manifest_adapters() -> None:
    for artifact_type in sorted(_DERIVED_TYPES):
        for version in (1, 2):
            key = (artifact_type, ArtifactRepresentation.JSON, version)
            if key in SCHEMA_ADAPTER_REGISTRY:
                continue
            register_schema_adapter(
                SchemaAdapter(
                    artifact_type=artifact_type,
                    representation=ArtifactRepresentation.JSON,
                    schema_version=version,
                    allowed_fields=frozenset(_FIELDS[artifact_type][version]),
                    required_fields=_required(artifact_type, version),
                    validate_serialized=lambda mapping, a=artifact_type, v=version: _validate_serialized(a, v, mapping),
                    construct=lambda mapping, a=artifact_type, v=version: _construct(a, v, mapping),
                    validate_semantics=lambda artifact, a=artifact_type: _validate_semantics(a, artifact),
                    migration_target=2 if version == 1 else None,
                )
            )


def parse_derived_manifest_mapping(
    artifact_type: str,
    mapping: Mapping[str, Any],
    *,
    legacy_mode: bool = False,
) -> Any:
    if artifact_type not in _DERIVED_TYPES:
        raise SerializedSchemaError("artifact_type.unknown", "unknown derived artifact type")
    data = dict(mapping)
    try:
        version = read_schema_version(data, artifact_type)
    except LegacyUnversionedArtifactError:
        if not legacy_mode:
            raise
        version = 1
        data.setdefault("artifact_type", artifact_type)
        data["schema_version"] = 1
    register_derived_manifest_adapters()
    adapter = get_schema_adapter(artifact_type, ArtifactRepresentation.JSON, version)
    if adapter is None:
        raise SerializedSchemaError("schema.adapter_not_registered", "schema adapter is unavailable")
    return adapter.parse_and_validate(data)


def validate_current_derived_manifest_serialized(
    artifact_type: str, mapping: Mapping[str, Any]
) -> None:
    register_derived_manifest_adapters()
    version = CURRENT_MANIFEST_SCHEMA_VERSIONS[artifact_type]
    adapter = get_schema_adapter(artifact_type, ArtifactRepresentation.JSON, version)
    if adapter is None:
        raise RuntimeError("current derived manifest adapter is unavailable")
    adapter.validate_serialized_mapping(mapping)


__all__ = [
    "parse_derived_manifest_mapping",
    "register_derived_manifest_adapters",
    "validate_current_derived_manifest_serialized",
]
