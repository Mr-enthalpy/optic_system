from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from .camera_profile import ProfileError


@dataclass
class PupilProfile:
    pupil_profile_id: str
    lcd_coordinate_convention: str
    lcd_display_index: int
    subpixel_axis: int
    lcd_physical_center: tuple[float, float]
    lcd_physical_radius: float | None = None
    aperture_window: tuple[int, int, int, int] | None = None
    camera_psf_center: tuple[float, float] | None = None
    recommended_roi: tuple[int, int, int, int] | None = None
    fit_quality: dict[str, Any] = field(default_factory=dict)
    source_raw_capture_file: str | None = None
    created_at: str | None = None
    software_version: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)
    source_schema_version: int = field(default=2, repr=False, compare=False)

    @classmethod
    def from_dict(
        cls,
        d: dict[str, Any],
        *,
        legacy_mode: bool = False,
    ) -> PupilProfile:
        from tasks.artifacts.validation import ArtifactValidationError
        from tasks.profiles.schema_adapters import parse_profile_mapping

        try:
            return parse_profile_mapping(
                "pupil_profile",
                d,
                legacy_mode=legacy_mode,
            )
        except ArtifactValidationError as exc:
            raise ProfileError(str(exc)) from exc

    @classmethod
    def _from_validated_mapping(
        cls,
        d: Mapping[str, Any],
        *,
        source_schema_version: int,
    ) -> PupilProfile:
        data = dict(d)
        return cls(
            pupil_profile_id=_require_str(data, "pupil_profile_id"),
            lcd_coordinate_convention=_require_str(
                data,
                "lcd_coordinate_convention",
            ),
            lcd_display_index=int(_require_key(data, "lcd_display_index")),
            subpixel_axis=int(_require_key(data, "subpixel_axis")),
            lcd_physical_center=_float_pair(
                _require_key(data, "lcd_physical_center")
            ),
            lcd_physical_radius=_optional_float(data.get("lcd_physical_radius")),
            aperture_window=_optional_int_quad(data.get("aperture_window")),
            camera_psf_center=_optional_float_pair(data.get("camera_psf_center")),
            recommended_roi=_optional_int_quad(data.get("recommended_roi")),
            fit_quality=_optional_dict(data.get("fit_quality")) or {},
            source_raw_capture_file=_optional_str(
                data.get("source_raw_capture_file")
            ),
            created_at=_optional_str(data.get("created_at")),
            software_version=_optional_str(data.get("software_version")),
            extra=_optional_dict(data.get("extra")) or {},
            source_schema_version=source_schema_version,
        )

    def validate(self) -> None:
        self._validate_for_schema(self.source_schema_version)

    def _validate_for_schema(self, schema_version: int) -> None:
        if schema_version not in {1, 2}:
            raise ProfileError(
                f"unsupported pupil profile schema_version {schema_version}"
            )
        if self.subpixel_axis not in {0, 1}:
            raise ProfileError("subpixel_axis must be 0 or 1")
        if self.lcd_display_index < 0:
            raise ProfileError("lcd_display_index must be >= 0")
        if self.lcd_physical_radius is not None and self.lcd_physical_radius <= 0:
            raise ProfileError("lcd_physical_radius must be positive")
        if self.lcd_physical_radius is None and self.aperture_window is None:
            raise ProfileError(
                "PupilProfile requires lcd_physical_radius or aperture_window"
            )
        if schema_version >= 2:
            for field, window in (
                ("aperture_window", self.aperture_window),
                ("recommended_roi", self.recommended_roi),
            ):
                if window is not None:
                    _validate_xyxy_window(window, field)

    def to_dict(self) -> dict[str, Any]:
        from tasks.artifact_versioning import emit_schema_version

        self._assert_current_source()
        self._validate_for_schema(2)
        result: dict[str, Any] = {
            "artifact_type": "pupil_profile",
            "pupil_profile_id": self.pupil_profile_id,
            "lcd_coordinate_convention": self.lcd_coordinate_convention,
            "lcd_display_index": self.lcd_display_index,
            "subpixel_axis": self.subpixel_axis,
            "lcd_physical_center": list(self.lcd_physical_center),
        }
        if self.lcd_physical_radius is not None:
            result["lcd_physical_radius"] = self.lcd_physical_radius
        if self.aperture_window is not None:
            result["aperture_window"] = list(self.aperture_window)
        if self.camera_psf_center is not None:
            result["camera_psf_center"] = list(self.camera_psf_center)
        if self.recommended_roi is not None:
            result["recommended_roi"] = list(self.recommended_roi)
        if self.fit_quality:
            result["fit_quality"] = self.fit_quality
        for key in ("source_raw_capture_file", "created_at", "software_version"):
            value = getattr(self, key)
            if value is not None:
                result[key] = value
        if self.extra:
            result["extra"] = self.extra
        emit_schema_version(result, "pupil_profile")
        from tasks.profiles.schema_adapters import (
            validate_current_profile_serialized,
        )

        try:
            validate_current_profile_serialized("pupil_profile", result)
        except ValueError as exc:
            raise ProfileError(str(exc)) from exc
        return result

    def to_json(self, path: str | Path | None = None) -> str:
        text = json.dumps(
            self.to_dict(),
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        if path is not None:
            Path(path).write_text(text + "\n", encoding="utf-8")
        return text

    @classmethod
    def load_json(cls, path: str | Path) -> PupilProfile:
        from tasks.artifacts.validation import (
            ArtifactValidationError,
            parse_json_mapping,
        )

        try:
            return cls.from_dict(parse_json_mapping(path))
        except ArtifactValidationError as exc:
            raise ProfileError(str(exc)) from exc

    def _assert_current_source(self) -> None:
        if self.source_schema_version != 2:
            raise ProfileError(
                "compatibility-read pupil profile cannot be written as schema "
                "v2; call migrate_pupil_profile_v1_to_v2() explicitly"
            )


def _xywh_to_xyxy(
    window: tuple[int, int, int, int] | None,
) -> tuple[int, int, int, int] | None:
    if window is None:
        return None
    x, y, width, height = window
    if x < 0 or y < 0 or width <= 0 or height <= 0:
        raise ProfileError(
            "schema v1 XYWH window must be non-negative with positive size"
        )
    return (x, y, x + width, y + height)


def migrate_pupil_profile_v1_to_v2(profile: PupilProfile) -> PupilProfile:
    """Explicitly migrate historical XYWH windows into schema-v2 XYXY."""
    if profile.source_schema_version != 1:
        raise ProfileError("pupil profile migration requires a schema v1 source")
    migrated_extra = dict(profile.extra)
    migrated_extra["migration"] = {
        "name": "pupil_profile_v1_to_v2",
        "source_schema_version": 1,
        "aperture_window_conversion": "xywh_to_xyxy",
        "recommended_roi_conversion": "xywh_to_xyxy",
    }
    migrated = replace(
        profile,
        aperture_window=_xywh_to_xyxy(profile.aperture_window),
        recommended_roi=_xywh_to_xyxy(profile.recommended_roi),
        extra=migrated_extra,
        source_schema_version=2,
    )
    migrated._validate_for_schema(2)
    return migrated


def import_pupil_profile_yaml(
    source_yaml: str | Path,
    output_json: str | Path,
) -> PupilProfile:
    """Import YAML authoring input into a new canonical schema-v2 JSON artifact."""
    from tasks.profiles.schema_adapters import parse_profile_yaml_mapping

    data = parse_profile_yaml_mapping(source_yaml)
    if data.get("artifact_type", "pupil_profile") != "pupil_profile":
        raise ProfileError("YAML import artifact_type must be pupil_profile")
    if data.get("schema_version", 2) != 2:
        raise ProfileError("YAML import only produces pupil_profile schema v2")
    data["artifact_type"] = "pupil_profile"
    data["schema_version"] = 2
    extra = dict(data.get("extra") or {})
    extra["import"] = {
        "format": "yaml",
        "canonical_representation": "json",
    }
    data["extra"] = extra
    profile = PupilProfile.from_dict(data)
    profile.to_json(output_json)
    return profile


def _require_key(d: dict[str, Any], key: str) -> Any:
    if key not in d:
        raise ProfileError(f"missing required key {key!r}")
    return d[key]


def _require_str(d: dict[str, Any], key: str) -> str:
    value = _require_key(d, key)
    if not isinstance(value, str) or not value.strip():
        raise ProfileError(f"{key!r} must be a non-empty string")
    return value.strip()


def _float_pair(value: Any) -> tuple[float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ProfileError(f"expected a 2-element coordinate pair, got {value!r}")
    return float(value[0]), float(value[1])


def _optional_float_pair(value: Any) -> tuple[float, float] | None:
    if value is None:
        return None
    return _float_pair(value)


def _optional_int_quad(value: Any) -> tuple[int, int, int, int] | None:
    if value is None:
        return None
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise ProfileError(f"expected a 4-element integer tuple, got {value!r}")
    return int(value[0]), int(value[1]), int(value[2]), int(value[3])


def _validate_xyxy_window(
    window: tuple[int, int, int, int],
    field: str,
) -> None:
    x0, y0, x1, y1 = window
    if min(window) < 0 or x1 <= x0 or y1 <= y0:
        raise ProfileError(
            f"{field} must be a non-negative, positive-area XYXY window"
        )


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        raise ProfileError(f"expected float or null, got {value!r}") from None


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ProfileError(f"expected string or null, got {type(value).__name__}")
    stripped = value.strip()
    return stripped if stripped else None


def _optional_dict(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ProfileError(f"expected mapping or null, got {type(value).__name__}")
    return value
