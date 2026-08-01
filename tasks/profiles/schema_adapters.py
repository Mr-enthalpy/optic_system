from __future__ import annotations

"""Version-specific JSON adapters and YAML import parsing for profile artifacts."""

from collections.abc import Mapping
import math
from pathlib import Path
from typing import Any

from tasks.artifact_versioning import read_schema_version
from tasks.artifacts.validation import (
    ArtifactRepresentation,
    SchemaAdapter,
    SerializedSchemaError,
    get_schema_adapter,
    register_schema_adapter,
)


CAMERA_PROFILE_FIELDS = frozenset(
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
PUPIL_PROFILE_FIELDS = frozenset(
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

_CAMERA_REQUIRED = frozenset(
    {
        "artifact_type",
        "schema_version",
        "camera_profile_id",
        "profile_family",
        "illumination",
        "lcd_state",
        "valid_for",
    }
)
_PUPIL_REQUIRED = frozenset(
    {
        "artifact_type",
        "schema_version",
        "pupil_profile_id",
        "lcd_coordinate_convention",
        "lcd_display_index",
        "subpixel_axis",
        "lcd_physical_center",
    }
)

_ILLUMINATION_FIELDS = frozenset(
    {
        "mode",
        "tls_setpoint_nm",
        "effective_wavelength_nm",
        "wavelengths_nm",
        "source",
    }
)
_CAMERA_SCALAR_FIELDS = frozenset(
    {
        "exposure_us",
        "gain_db",
        "peak_pixel",
        "saturation_margin",
        "frames_per_capture",
        "peak_pixel_domain",
        "full_frame_peak_pixel",
        "full_frame_saturated_pixel_count",
    }
)
_CAMERA_BLOCK_FIELDS = _CAMERA_SCALAR_FIELDS | {"per_wavelength"}
_PER_WAVELENGTH_FIELDS = _CAMERA_SCALAR_FIELDS


def _schema_error(code: str, message: str) -> None:
    raise SerializedSchemaError(code, message)


def _strict_json_tree(value: Any, field: str) -> None:
    if value is None or type(value) in {str, bool, int}:
        return
    if type(value) is float:
        if not math.isfinite(value):
            _schema_error("schema.number.nonfinite", f"{field} must be finite")
        return
    if type(value) is list:
        for index, item in enumerate(value):
            _strict_json_tree(item, f"{field}[{index}]")
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                _schema_error(
                    "schema.field.name_invalid",
                    f"{field} field names must be strings",
                )
            _strict_json_tree(item, f"{field}.{key}")
        return
    _schema_error(
        "schema.type.invalid",
        f"{field} contains unsupported value type {type(value).__name__}",
    )


def _closed_mapping(
    value: Any,
    field: str,
    allowed: frozenset[str],
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        _schema_error("schema.type.invalid", f"{field} must be a mapping")
    _strict_json_tree(value, field)
    unknown = sorted(set(value) - allowed)
    if unknown:
        _schema_error(
            "schema.field.unknown",
            f"unknown {field} field(s): {', '.join(unknown)}",
        )
    return value


def _required_string(mapping: Mapping[str, Any], field: str) -> str:
    value = mapping.get(field)
    if not isinstance(value, str) or not value or value != value.strip():
        _schema_error(
            "schema.string.not_canonical",
            f"{field} must be a non-empty canonical string",
        )
    return value


def _strict_number(
    mapping: Mapping[str, Any],
    field: str,
    *,
    required: bool = False,
    positive: bool = False,
) -> None:
    if field not in mapping:
        if required:
            _schema_error("schema.field.missing", f"{field} is required")
        return
    value = mapping[field]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _schema_error("schema.type.invalid", f"{field} must be a number")
    number = float(value)
    if not math.isfinite(number):
        _schema_error("schema.number.nonfinite", f"{field} must be finite")
    if positive and number <= 0:
        _schema_error("schema.number.out_of_range", f"{field} must be positive")


def _strict_integer(
    mapping: Mapping[str, Any],
    field: str,
    *,
    required: bool = False,
    minimum: int | None = None,
) -> None:
    if field not in mapping:
        if required:
            _schema_error("schema.field.missing", f"{field} is required")
        return
    value = mapping[field]
    if isinstance(value, bool) or not isinstance(value, int):
        _schema_error("schema.type.invalid", f"{field} must be an integer")
    if minimum is not None and value < minimum:
        requirement = "non-negative" if minimum == 0 else f">= {minimum}"
        _schema_error(
            "schema.number.out_of_range",
            f"{field} must be {requirement}",
        )


def _validate_v1_profile_mapping(mapping: Mapping[str, Any]) -> None:
    """Freeze historical v1 top-level fields without retroactive v2 semantics."""
    _strict_json_tree(mapping, "$")


def _validate_camera_settings_v2(
    settings: Mapping[str, Any],
    field: str,
) -> None:
    _strict_number(settings, "exposure_us", required=True, positive=True)
    _strict_number(settings, "gain_db", required=True)
    _strict_number(settings, "peak_pixel")
    _strict_number(settings, "saturation_margin")
    _strict_integer(settings, "frames_per_capture", minimum=1)
    _strict_number(settings, "full_frame_peak_pixel")
    _strict_integer(settings, "full_frame_saturated_pixel_count", minimum=0)
    if "peak_pixel_domain" in settings:
        value = settings["peak_pixel_domain"]
        if value != "valid_pixel_domain":
            _schema_error(
                "schema.enum.invalid",
                f"{field}.peak_pixel_domain must equal 'valid_pixel_domain'",
            )


def _validate_camera_profile_v2(mapping: Mapping[str, Any]) -> None:
    for field in ("camera_profile_id", "profile_family"):
        _required_string(mapping, field)
    illumination = _closed_mapping(
        mapping.get("illumination"),
        "illumination",
        _ILLUMINATION_FIELDS,
    )
    _required_string(illumination, "mode")
    if "source" in illumination:
        _required_string(illumination, "source")
    for field in ("tls_setpoint_nm", "effective_wavelength_nm"):
        if illumination.get(field) is not None:
            _strict_number(illumination, field)
    wavelengths = illumination.get("wavelengths_nm", [])
    if not isinstance(wavelengths, list):
        _schema_error(
            "schema.type.invalid",
            "illumination.wavelengths_nm must be a list",
        )
    for index, wavelength in enumerate(wavelengths):
        if isinstance(wavelength, bool) or not isinstance(wavelength, (int, float)):
            _schema_error(
                "schema.type.invalid",
                f"illumination.wavelengths_nm[{index}] must be a number",
            )
        if not math.isfinite(float(wavelength)):
            _schema_error(
                "schema.number.nonfinite",
                f"illumination.wavelengths_nm[{index}] must be finite",
            )

    lcd_state = mapping.get("lcd_state")
    if not isinstance(lcd_state, Mapping):
        _schema_error("schema.type.invalid", "lcd_state must be a mapping")
    _strict_json_tree(lcd_state, "lcd_state")
    valid_for = mapping.get("valid_for")
    if not isinstance(valid_for, list) or not valid_for:
        _schema_error("schema.type.invalid", "valid_for must be a non-empty list")
    for index, value in enumerate(valid_for):
        if not isinstance(value, str) or not value or value != value.strip():
            _schema_error(
                "schema.string.not_canonical",
                f"valid_for[{index}] must be a canonical string",
            )

    if "depends_on" in mapping:
        depends_on = _closed_mapping(
            mapping["depends_on"],
            "depends_on",
            frozenset({"pupil_profile_id"}),
        )
        _required_string(depends_on, "pupil_profile_id")
    if "depends_on_pupil_profile_id" in mapping:
        _required_string(mapping, "depends_on_pupil_profile_id")

    camera = _closed_mapping(
        mapping.get("camera", {}),
        "camera",
        _CAMERA_BLOCK_FIELDS,
    )
    profile_family = mapping["profile_family"]
    top_scalar = _CAMERA_SCALAR_FIELDS & set(mapping)
    top_per_wavelength = "per_wavelength" in mapping
    nested_scalar = _CAMERA_SCALAR_FIELDS & set(camera)
    nested_per_wavelength = "per_wavelength" in camera

    if profile_family == "broadband_passthrough":
        if top_per_wavelength or nested_per_wavelength:
            _schema_error(
                "camera.settings_mode.mixed",
                "broadband profile must not contain per_wavelength settings",
            )
        if top_scalar and nested_scalar:
            _schema_error(
                "camera.settings_mode.aliased",
                "camera scalar settings must use one representation",
            )
        scalar_settings = mapping if top_scalar else camera
        _validate_camera_settings_v2(scalar_settings, "camera")
    elif profile_family == "per_band_pupil_open":
        if top_scalar or nested_scalar:
            _schema_error(
                "camera.settings_mode.mixed",
                "per-band profile must not contain scalar camera settings",
            )
        if top_per_wavelength and nested_per_wavelength:
            _schema_error(
                "camera.settings_mode.aliased",
                "per_wavelength settings must use one representation",
            )
        records = (
            mapping.get("per_wavelength")
            if top_per_wavelength
            else camera.get("per_wavelength")
        )
        if not isinstance(records, Mapping) or not records:
            _schema_error(
                "schema.field.missing",
                "per-band profile requires per_wavelength settings",
            )
        for key, value in records.items():
            if not isinstance(key, str) or not key or key != key.strip():
                _schema_error(
                    "schema.string.not_canonical",
                    "per_wavelength keys must be canonical strings",
                )
            settings = _closed_mapping(
                value,
                f"per_wavelength[{key!r}]",
                _PER_WAVELENGTH_FIELDS,
            )
            _validate_camera_settings_v2(
                settings,
                f"per_wavelength[{key!r}]",
            )
    else:
        _schema_error(
            "schema.enum.invalid",
            f"unsupported camera profile_family {profile_family!r}",
        )

    for field in ("source_raw_capture_file", "created_at", "software_version"):
        if field in mapping:
            _required_string(mapping, field)
    if "extra" in mapping:
        if not isinstance(mapping["extra"], Mapping):
            _schema_error("schema.type.invalid", "extra must be a mapping")
        _strict_json_tree(mapping["extra"], "extra")


def _validate_xyxy(value: Any, field: str) -> None:
    if not isinstance(value, list) or len(value) != 4:
        _schema_error(
            "geometry.window.invalid",
            f"{field} must contain [x0, y0, x1, y1]",
        )
    if any(isinstance(v, bool) or not isinstance(v, int) for v in value):
        _schema_error(
            "schema.type.invalid",
            f"{field} coordinates must be integers",
        )
    x0, y0, x1, y1 = value
    if min(value) < 0 or x1 <= x0 or y1 <= y0:
        _schema_error(
            "geometry.window.invalid",
            f"{field} must be a non-negative, positive-area XYXY window",
        )


def _validate_pair(value: Any, field: str) -> None:
    if not isinstance(value, list) or len(value) != 2:
        _schema_error("schema.type.invalid", f"{field} must contain two numbers")
    for item in value:
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            _schema_error("schema.type.invalid", f"{field} must contain numbers")
        if not math.isfinite(float(item)):
            _schema_error("schema.number.nonfinite", f"{field} must be finite")


def _validate_pupil_profile_v2(mapping: Mapping[str, Any]) -> None:
    for field in ("pupil_profile_id", "lcd_coordinate_convention"):
        _required_string(mapping, field)
    _strict_integer(mapping, "lcd_display_index", required=True, minimum=0)
    _strict_integer(mapping, "subpixel_axis", required=True, minimum=0)
    if mapping["subpixel_axis"] not in {0, 1}:
        _schema_error("schema.enum.invalid", "subpixel_axis must be 0 or 1")
    _validate_pair(mapping.get("lcd_physical_center"), "lcd_physical_center")
    _strict_number(mapping, "lcd_physical_radius", positive=True)
    if "aperture_window" in mapping:
        _validate_xyxy(mapping["aperture_window"], "aperture_window")
    if "camera_psf_center" in mapping:
        _validate_pair(mapping["camera_psf_center"], "camera_psf_center")
    if "recommended_roi" in mapping:
        _validate_xyxy(mapping["recommended_roi"], "recommended_roi")
    if "fit_quality" in mapping:
        if not isinstance(mapping["fit_quality"], Mapping):
            _schema_error("schema.type.invalid", "fit_quality must be a mapping")
        _strict_json_tree(mapping["fit_quality"], "fit_quality")
    if "extra" in mapping:
        if not isinstance(mapping["extra"], Mapping):
            _schema_error("schema.type.invalid", "extra must be a mapping")
        _strict_json_tree(mapping["extra"], "extra")


def _construct_camera(version: int):
    def construct(mapping: Mapping[str, Any]) -> Any:
        from tasks.profiles.camera_profile import CameraProfile, ProfileError

        try:
            return CameraProfile._from_validated_mapping(  # noqa: SLF001
                mapping,
                source_schema_version=version,
            )
        except ProfileError as exc:
            raise SerializedSchemaError(
                "semantic.camera_profile.invalid",
                str(exc),
            ) from exc

    return construct


def _construct_pupil(version: int):
    def construct(mapping: Mapping[str, Any]) -> Any:
        from tasks.profiles.camera_profile import ProfileError
        from tasks.profiles.pupil_profile import PupilProfile

        try:
            return PupilProfile._from_validated_mapping(  # noqa: SLF001
                mapping,
                source_schema_version=version,
            )
        except ProfileError as exc:
            raise SerializedSchemaError(
                "semantic.pupil_profile.invalid",
                str(exc),
            ) from exc

    return construct


def _validate_camera_semantics(version: int):
    def validate(profile: Any) -> None:
        from tasks.profiles.camera_profile import ProfileError

        try:
            profile._validate_for_schema(version)  # noqa: SLF001
        except ProfileError as exc:
            raise SerializedSchemaError(
                "semantic.camera_profile.invalid",
                str(exc),
            ) from exc

    return validate


def _validate_pupil_semantics(version: int):
    def validate(profile: Any) -> None:
        from tasks.profiles.camera_profile import ProfileError

        try:
            profile._validate_for_schema(version)  # noqa: SLF001
        except ProfileError as exc:
            raise SerializedSchemaError(
                "semantic.pupil_profile.invalid",
                str(exc),
            ) from exc

    return validate


def register_profile_schema_adapters() -> None:
    adapters = (
        SchemaAdapter(
            artifact_type="camera_profile",
            representation=ArtifactRepresentation.JSON,
            schema_version=1,
            allowed_fields=CAMERA_PROFILE_FIELDS,
            required_fields=_CAMERA_REQUIRED,
            validate_serialized=_validate_v1_profile_mapping,
            construct=_construct_camera(1),
            validate_semantics=_validate_camera_semantics(1),
            migration_target=2,
        ),
        SchemaAdapter(
            artifact_type="camera_profile",
            representation=ArtifactRepresentation.JSON,
            schema_version=2,
            allowed_fields=CAMERA_PROFILE_FIELDS,
            required_fields=_CAMERA_REQUIRED,
            validate_serialized=_validate_camera_profile_v2,
            construct=_construct_camera(2),
            validate_semantics=_validate_camera_semantics(2),
        ),
        SchemaAdapter(
            artifact_type="pupil_profile",
            representation=ArtifactRepresentation.JSON,
            schema_version=1,
            allowed_fields=PUPIL_PROFILE_FIELDS,
            required_fields=_PUPIL_REQUIRED,
            validate_serialized=_validate_v1_profile_mapping,
            construct=_construct_pupil(1),
            validate_semantics=_validate_pupil_semantics(1),
            migration_target=2,
        ),
        SchemaAdapter(
            artifact_type="pupil_profile",
            representation=ArtifactRepresentation.JSON,
            schema_version=2,
            allowed_fields=PUPIL_PROFILE_FIELDS,
            required_fields=_PUPIL_REQUIRED,
            validate_serialized=_validate_pupil_profile_v2,
            construct=_construct_pupil(2),
            validate_semantics=_validate_pupil_semantics(2),
        ),
    )
    for adapter in adapters:
        existing = get_schema_adapter(*adapter.key)
        if existing == adapter:
            continue
        register_schema_adapter(adapter, replace=existing is not None)


def parse_profile_mapping(
    artifact_type: str,
    mapping: Mapping[str, Any],
    *,
    legacy_mode: bool = False,
) -> Any:
    register_profile_schema_adapters()
    data = dict(mapping)
    if "schema_version" not in data and legacy_mode:
        data.setdefault("artifact_type", artifact_type)
        data["schema_version"] = 1
    version = read_schema_version(data, artifact_type)
    adapter = get_schema_adapter(
        artifact_type,
        ArtifactRepresentation.JSON,
        version,
    )
    if adapter is None:
        raise SerializedSchemaError(
            "schema.adapter_not_registered",
            "no profile adapter is registered for this schema identity",
        )
    return adapter.parse_and_validate(data)


def validate_current_profile_serialized(
    artifact_type: str,
    mapping: Mapping[str, Any],
) -> None:
    register_profile_schema_adapters()
    adapter = get_schema_adapter(
        artifact_type,
        ArtifactRepresentation.JSON,
        2,
    )
    if adapter is None:
        raise SerializedSchemaError(
            "schema.adapter_not_registered",
            "current profile adapter is not registered",
        )
    adapter.validate_serialized_mapping(mapping)


def parse_profile_yaml_mapping(path: str | Path) -> dict[str, Any]:
    """Parse YAML authoring input; the result is not itself an artifact."""
    try:
        import yaml
    except ImportError as exc:
        raise SerializedSchemaError(
            "representation.yaml.unavailable",
            "PyYAML is required for profile YAML import",
        ) from exc

    class UniqueStringKeyLoader(yaml.SafeLoader):
        pass

    def construct_mapping(loader, node, deep=False):
        pairs = loader.construct_pairs(node, deep=deep)
        result: dict[str, Any] = {}
        for key, value in pairs:
            if not isinstance(key, str):
                _schema_error(
                    "schema.field.name_invalid",
                    "YAML mapping keys must be strings",
                )
            if key in result:
                _schema_error(
                    "representation.yaml.duplicate_key",
                    f"duplicate YAML mapping key {key!r}",
                )
            result[key] = value
        return result

    UniqueStringKeyLoader.add_constructor(
        yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
        construct_mapping,
    )
    try:
        data = yaml.load(
            Path(path).read_text(encoding="utf-8"),
            Loader=UniqueStringKeyLoader,
        )
    except SerializedSchemaError:
        raise
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise SerializedSchemaError(
            "representation.yaml.parse_error",
            "profile YAML authoring input could not be parsed",
        ) from exc
    if not isinstance(data, dict):
        _schema_error(
            "representation.yaml.root_invalid",
            "profile YAML root must be a mapping",
        )
    _strict_json_tree(data, "$")
    return data


__all__ = [
    "CAMERA_PROFILE_FIELDS",
    "PUPIL_PROFILE_FIELDS",
    "parse_profile_mapping",
    "parse_profile_yaml_mapping",
    "register_profile_schema_adapters",
    "validate_current_profile_serialized",
]
