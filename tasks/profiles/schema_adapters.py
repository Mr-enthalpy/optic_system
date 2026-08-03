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


def _load_camera_profile_v1(mapping: Mapping[str, Any]) -> CameraProfile:
    return CameraProfile.from_v1_serialized_mapping(_prepare_camera_profile_v1(mapping))


def _load_pupil_profile_v1(mapping: Mapping[str, Any]) -> PupilProfile:
    return PupilProfile.from_v1_serialized_mapping(_prepare_pupil_profile_v1(mapping))


def _construction_rejected(message: str) -> ConstructionValidationError:
    return ConstructionValidationError(
        "schema.construction.profile_rejected",
        message,
    )


def _legacy_binary64(value: Any, field: str) -> float:
    """Apply the v1 bridge's explicit Decimal-to-binary64 policy."""
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        raise _construction_rejected(f"{field} must be numeric") from None
    if not math.isfinite(result):
        raise _construction_rejected(f"{field} must be finite")
    if isinstance(value, Decimal) and value != 0 and result == 0:
        raise _construction_rejected(f"{field} is outside the binary64 range")
    return result


def _legacy_integer(value: Any, field: str) -> int:
    try:
        if isinstance(value, Decimal):
            return int(_legacy_binary64(value, field))
        return int(value)
    except (TypeError, ValueError, OverflowError):
        raise _construction_rejected(f"{field} must be integer-compatible") from None


def _convert_present(
    target: dict[str, Any],
    field: str,
    converter,
    *,
    path: str | None = None,
    allow_none: bool = True,
) -> None:
    if field in target and (target[field] is not None or not allow_none):
        target[field] = converter(target[field], path or field)


def _copy_mapping(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise _construction_rejected(f"{field} must be a mapping")
    return dict(value)


def _convert_sequence(
    value: Any,
    field: str,
    length: int | None,
    converter,
    *,
    allow_tuple: bool = True,
) -> list[Any]:
    accepted_types = (list, tuple) if allow_tuple else (list,)
    if not isinstance(value, accepted_types) or (
        length is not None and len(value) != length
    ):
        qualifier = f" exactly {length}" if length is not None else ""
        raise _construction_rejected(f"{field} must contain{qualifier} values")
    return [converter(item, field) for item in value]


def _prepare_pupil_profile_v1(mapping: Mapping[str, Any]) -> dict[str, Any]:
    """Convert only fields consumed by the v1 loader; extensions stay opaque."""
    prepared = dict(mapping)
    for field in ("lcd_display_index", "subpixel_axis"):
        _convert_present(prepared, field, _legacy_integer, allow_none=False)
    for field in ("lcd_physical_center", "camera_psf_center"):
        if field in prepared and prepared[field] is not None:
            prepared[field] = _convert_sequence(
                prepared[field], field, 2, _legacy_binary64
            )
    _convert_present(prepared, "lcd_physical_radius", _legacy_binary64)
    for field in ("aperture_window", "recommended_roi"):
        if field in prepared and prepared[field] is not None:
            prepared[field] = _convert_sequence(
                prepared[field], field, 4, _legacy_integer
            )
    return prepared


_CAMERA_FLOAT_FIELDS = (
    "exposure_us",
    "gain_db",
    "peak_pixel",
    "saturation_margin",
    "full_frame_peak_pixel",
)
_CAMERA_INTEGER_FIELDS = (
    "frames_per_capture",
    "full_frame_saturated_pixel_count",
)


def _prepare_camera_settings(
    value: Any,
    field: str,
) -> dict[str, Any]:
    settings = _copy_mapping(value, field)
    for name in _CAMERA_FLOAT_FIELDS:
        _convert_present(
            settings,
            name,
            _legacy_binary64,
            path=f"{field}.{name}",
            allow_none=name not in {"exposure_us", "gain_db"},
        )
    for name in _CAMERA_INTEGER_FIELDS:
        _convert_present(settings, name, _legacy_integer, path=f"{field}.{name}")
    return settings


def _prepare_camera_profile_v1(mapping: Mapping[str, Any]) -> dict[str, Any]:
    """Apply v1 numeric policy without inspecting ignored or opaque extensions."""
    prepared = dict(mapping)
    illumination = _copy_mapping(prepared.get("illumination"), "illumination")
    for field in ("tls_setpoint_nm", "effective_wavelength_nm"):
        _convert_present(
            illumination,
            field,
            _legacy_binary64,
            path=f"illumination.{field}",
        )
    if "wavelengths_nm" in illumination and illumination["wavelengths_nm"] is not None:
        illumination["wavelengths_nm"] = _convert_sequence(
            illumination["wavelengths_nm"],
            "illumination.wavelengths_nm",
            None,
            _legacy_binary64,
            allow_tuple=False,
        )
    prepared["illumination"] = illumination

    camera = prepared.get("camera")
    if camera is not None:
        prepared["camera"] = _prepare_camera_settings(camera, "camera")
        camera = prepared["camera"]
    else:
        camera = {}
    for field in _CAMERA_FLOAT_FIELDS:
        _convert_present(prepared, field, _legacy_binary64)
    for field in _CAMERA_INTEGER_FIELDS:
        _convert_present(prepared, field, _legacy_integer)

    if prepared.get("per_wavelength"):
        per_wavelength = _copy_mapping(prepared["per_wavelength"], "per_wavelength")
        target = prepared
    elif camera.get("per_wavelength"):
        per_wavelength = _copy_mapping(
            camera["per_wavelength"], "camera.per_wavelength"
        )
        target = camera
    else:
        per_wavelength = {}
        target = prepared
    target["per_wavelength"] = {
        str(key): _prepare_camera_settings(value, f"per_wavelength[{key!r}]")
        for key, value in per_wavelength.items()
    }
    return prepared


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
