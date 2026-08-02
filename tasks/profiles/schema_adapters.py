from __future__ import annotations

"""Historical profile readers registered outside the validation mechanism."""

from collections.abc import Mapping
from typing import Any

from tasks.artifacts.validation import (
    AdditionalFieldsPolicy,
    ArtifactRepresentation,
    ArtifactVersionSet,
    LegacyCompatibilityBridge,
    SchemaAdapterRegistry,
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
    return CameraProfile.from_dict(dict(mapping))


def _load_pupil_profile_v1(mapping: Mapping[str, Any]) -> PupilProfile:
    return PupilProfile.from_dict(dict(mapping))


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
            contract_error_types=(ProfileError, TypeError, ValueError),
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
            load_and_validate=_load_pupil_profile_v1,
            contract_error_types=(ProfileError, TypeError, ValueError),
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
