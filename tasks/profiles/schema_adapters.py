from __future__ import annotations

"""Historical profile readers registered outside the validation mechanism."""

from collections.abc import Mapping
from decimal import Decimal
import math
from typing import Any

from tasks.artifacts.validation import (
    AdditionalFieldsPolicy,
    ArtifactRepresentation,
    ArtifactVersionSet,
    ConstructionValidationError,
    LegacyCompatibilityBridge,
    SchemaAdapterRegistry,
    SerializedSchemaError,
    register_legacy_compatibility_bridge,
)

from .camera_profile import CameraProfile, ProfileError
from .pupil_profile import PupilProfile


CAMERA_PROFILE_V1_FIELDS = frozenset(
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

PUPIL_PROFILE_V1_FIELDS = frozenset(
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


def _decimal_to_legacy_binary64(value: Any) -> Any:
    if isinstance(value, Decimal):
        converted = float(value)
        if not math.isfinite(converted) or (value != 0 and converted == 0):
            raise ConstructionValidationError(
                "schema.construction.numeric_range",
                "legacy profile number is outside its binary64 domain",
            )
        return converted
    if isinstance(value, dict):
        return {
            key: _decimal_to_legacy_binary64(child) for key, child in value.items()
        }
    if isinstance(value, list):
        return [_decimal_to_legacy_binary64(child) for child in value]
    return value


def _load_camera_profile_v1(mapping: Mapping[str, Any]) -> CameraProfile:
    return CameraProfile.from_dict(_decimal_to_legacy_binary64(dict(mapping)))


def _load_pupil_profile_v1(mapping: Mapping[str, Any]) -> PupilProfile:
    return PupilProfile.from_dict(_decimal_to_legacy_binary64(dict(mapping)))


def _validate_numeric_sequence(
    mapping: Mapping[str, Any],
    key: str,
    length: int,
) -> None:
    value = mapping.get(key)
    if value is None:
        return
    if not isinstance(value, (list, tuple)) or len(value) != length:
        raise SerializedSchemaError(
            "schema.field.type_invalid",
            f"{key} must contain exactly {length} numeric values",
        )
    try:
        for item in value:
            float(item)
    except (TypeError, ValueError, OverflowError) as exc:
        raise SerializedSchemaError(
            "schema.field.type_invalid",
            f"{key} must contain numeric values",
        ) from exc


def _validate_pupil_v1_serialized(mapping: Mapping[str, Any]) -> None:
    _validate_numeric_sequence(mapping, "lcd_physical_center", 2)
    _validate_numeric_sequence(mapping, "camera_psf_center", 2)
    _validate_numeric_sequence(mapping, "aperture_window", 4)
    _validate_numeric_sequence(mapping, "recommended_roi", 4)


def _translate_profile_error(
    exc: Exception,
) -> ConstructionValidationError | None:
    if isinstance(exc, ProfileError):
        return ConstructionValidationError(
            "schema.construction.profile_rejected",
            str(exc),
        )
    return None


def register_profile_v1_adapters(registry: SchemaAdapterRegistry) -> None:
    """Register explicit read-only bridges preserving historical v1 behavior."""
    bridges = (
        LegacyCompatibilityBridge(
            artifact_type="camera_profile",
            representation=ArtifactRepresentation.JSON,
            versions=ArtifactVersionSet(manifest=1),
            allowed_fields=CAMERA_PROFILE_V1_FIELDS,
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
            load_and_validate=_load_camera_profile_v1,
            translate_error=_translate_profile_error,
            additional_fields_policy=AdditionalFieldsPolicy.IGNORE,
        ),
        LegacyCompatibilityBridge(
            artifact_type="pupil_profile",
            representation=ArtifactRepresentation.JSON,
            versions=ArtifactVersionSet(manifest=1),
            allowed_fields=PUPIL_PROFILE_V1_FIELDS,
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
            validate_serialized=_validate_pupil_v1_serialized,
            load_and_validate=_load_pupil_profile_v1,
            translate_error=_translate_profile_error,
            additional_fields_policy=AdditionalFieldsPolicy.IGNORE,
        ),
    )
    for bridge in bridges:
        register_legacy_compatibility_bridge(bridge, registry=registry)


__all__ = [
    "CAMERA_PROFILE_V1_FIELDS",
    "PUPIL_PROFILE_V1_FIELDS",
    "register_profile_v1_adapters",
]
