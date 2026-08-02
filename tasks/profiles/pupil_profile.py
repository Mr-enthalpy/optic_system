from __future__ import annotations

import json
from dataclasses import dataclass, field
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

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> PupilProfile:
        from tasks.artifact_versioning import read_schema_version

        read_schema_version(d, "pupil_profile", legacy_mode=True)
        return cls.from_v1_serialized_mapping(d)

    @classmethod
    def from_v1_serialized_mapping(cls, d: dict[str, Any]) -> PupilProfile:
        """Construct the already-dispatched v1 contract without version lookup."""
        profile = cls(
            pupil_profile_id=_require_str(d, "pupil_profile_id"),
            lcd_coordinate_convention=_require_str(d, "lcd_coordinate_convention"),
            lcd_display_index=int(_require_key(d, "lcd_display_index")),
            subpixel_axis=int(_require_key(d, "subpixel_axis")),
            lcd_physical_center=_float_pair(_require_key(d, "lcd_physical_center")),
            lcd_physical_radius=_optional_float(d.get("lcd_physical_radius")),
            aperture_window=_optional_int_quad(d.get("aperture_window")),
            camera_psf_center=_optional_float_pair(d.get("camera_psf_center")),
            recommended_roi=_optional_int_quad(d.get("recommended_roi")),
            fit_quality=_optional_dict(d.get("fit_quality")) or {},
            source_raw_capture_file=_optional_str(d.get("source_raw_capture_file")),
            created_at=_optional_str(d.get("created_at")),
            software_version=_optional_str(d.get("software_version")),
            extra=_optional_dict(d.get("extra")) or {},
        )
        profile.validate()
        return profile

    def validate(self) -> None:
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

    def to_dict(self) -> dict[str, Any]:
        from tasks.artifact_versioning import emit_schema_version

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
        return result

    def to_json(self, path: str | Path | None = None) -> str:
        text = json.dumps(self.to_dict(), indent=2, sort_keys=True)
        if path is not None:
            Path(path).write_text(text + "\n", encoding="utf-8")
        return text

    @classmethod
    def load_json(cls, path: str | Path) -> PupilProfile:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ProfileError("pupil profile JSON root must be a mapping")
        return cls.from_dict(data)

    @classmethod
    def load_yaml(cls, path: str | Path) -> PupilProfile:
        try:
            import yaml
        except ImportError as exc:
            raise ProfileError("PyYAML is required for YAML profile loading") from exc
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ProfileError("pupil profile YAML root must be a mapping")
        return cls.from_dict(data)


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
