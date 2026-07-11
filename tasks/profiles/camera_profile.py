from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


BROADBAND_PASSTHROUGH = "broadband_passthrough"
PER_BAND_PUPIL_OPEN = "per_band_pupil_open"
MONOCHROMATIC = "monochromatic"
PSF_PRODUCING_TASK_FAMILIES = frozenset({
    "psf_dictionary_capture",
    "dotf_capture",
    "mask_family_psf_capture",
    "psf_capture",
})


class ProfileError(ValueError):
    pass


@dataclass
class CameraProfileIllumination:
    """Illumination schema for CameraProfile artifacts, not runtime TLS control."""

    mode: str
    tls_setpoint_nm: float | None = None
    effective_wavelength_nm: float | None = None
    wavelengths_nm: list[float] = field(default_factory=list)
    source: str | None = None

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> CameraProfileIllumination:
        wavelengths = d.get("wavelengths_nm") or []
        if not isinstance(wavelengths, list):
            raise ProfileError("illumination.wavelengths_nm must be a list")
        spec = cls(
            mode=_require_str(d, "mode"),
            tls_setpoint_nm=_optional_float(d.get("tls_setpoint_nm")),
            effective_wavelength_nm=_optional_float(d.get("effective_wavelength_nm")),
            wavelengths_nm=[float(w) for w in wavelengths],
            source=_optional_str(d.get("source")),
        )
        spec.validate()
        return spec

    def validate(self) -> None:
        if self.mode == BROADBAND_PASSTHROUGH:
            if self.tls_setpoint_nm != 0:
                raise ProfileError(
                    "broadband_passthrough illumination must record tls_setpoint_nm: 0"
                )
            if self.effective_wavelength_nm is not None:
                raise ProfileError(
                    "broadband_passthrough effective_wavelength_nm must be null"
                )
            if self.wavelengths_nm:
                raise ProfileError(
                    "broadband_passthrough must not populate scientific wavelengths_nm"
                )
            return

        if self.mode == MONOCHROMATIC:
            if not self.wavelengths_nm:
                raise ProfileError("monochromatic illumination requires wavelengths_nm")
            nonpositive = [w for w in self.wavelengths_nm if w <= 0]
            if nonpositive:
                raise ProfileError(
                    f"monochromatic wavelengths_nm must be positive, got {nonpositive}"
                )
            if self.tls_setpoint_nm == 0:
                raise ProfileError(
                    "tls_setpoint_nm: 0 is only valid for broadband_passthrough"
                )
            return

        raise ProfileError(f"unsupported illumination mode: {self.mode!r}")

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"mode": self.mode}
        if self.tls_setpoint_nm is not None:
            result["tls_setpoint_nm"] = self.tls_setpoint_nm
        if self.effective_wavelength_nm is not None:
            result["effective_wavelength_nm"] = self.effective_wavelength_nm
        elif self.mode == BROADBAND_PASSTHROUGH:
            result["effective_wavelength_nm"] = None
        if self.wavelengths_nm:
            result["wavelengths_nm"] = list(self.wavelengths_nm)
        if self.source is not None:
            result["source"] = self.source
        return result


@dataclass
class PerWavelengthCameraSettings:
    exposure_us: float
    gain_db: float
    peak_pixel: float | None = None
    saturation_margin: float | None = None
    frames_per_capture: int | None = None
    peak_pixel_domain: str | None = None
    full_frame_peak_pixel: float | None = None
    full_frame_saturated_pixel_count: int | None = None

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> PerWavelengthCameraSettings:
        settings = cls(
            exposure_us=float(_require_key(d, "exposure_us")),
            gain_db=float(d.get("gain_db", 0.0)),
            peak_pixel=_optional_float(d.get("peak_pixel")),
            saturation_margin=_optional_float(d.get("saturation_margin")),
            frames_per_capture=_optional_int(d.get("frames_per_capture")),
            peak_pixel_domain=_optional_str(d.get("peak_pixel_domain")),
            full_frame_peak_pixel=_optional_float(d.get("full_frame_peak_pixel")),
            full_frame_saturated_pixel_count=_optional_int(
                d.get("full_frame_saturated_pixel_count")
            ),
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        if self.exposure_us <= 0:
            raise ProfileError("exposure_us must be positive")
        if self.frames_per_capture is not None and self.frames_per_capture < 1:
            raise ProfileError("frames_per_capture must be >= 1")
        _validate_peak_domain_fields(
            peak_pixel_domain=self.peak_pixel_domain,
            full_frame_peak_pixel=self.full_frame_peak_pixel,
            full_frame_saturated_pixel_count=self.full_frame_saturated_pixel_count,
        )

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "exposure_us": self.exposure_us,
            "gain_db": self.gain_db,
        }
        if self.peak_pixel is not None:
            result["peak_pixel"] = self.peak_pixel
        if self.saturation_margin is not None:
            result["saturation_margin"] = self.saturation_margin
        if self.frames_per_capture is not None:
            result["frames_per_capture"] = self.frames_per_capture
        if self.peak_pixel_domain is not None:
            result["peak_pixel_domain"] = self.peak_pixel_domain
        if self.full_frame_peak_pixel is not None:
            result["full_frame_peak_pixel"] = self.full_frame_peak_pixel
        if self.full_frame_saturated_pixel_count is not None:
            result["full_frame_saturated_pixel_count"] = self.full_frame_saturated_pixel_count
        return result


@dataclass
class CameraProfile:
    camera_profile_id: str
    profile_family: str
    illumination: CameraProfileIllumination
    lcd_state: dict[str, Any]
    valid_for: list[str]
    per_wavelength: dict[str, PerWavelengthCameraSettings] = field(default_factory=dict)
    exposure_us: float | None = None
    gain_db: float | None = None
    peak_pixel: float | None = None
    saturation_margin: float | None = None
    frames_per_capture: int | None = None
    peak_pixel_domain: str | None = None
    full_frame_peak_pixel: float | None = None
    full_frame_saturated_pixel_count: int | None = None
    depends_on_pupil_profile_id: str | None = None
    source_raw_capture_file: str | None = None
    created_at: str | None = None
    software_version: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> CameraProfile:
        profile_family = _require_str(d, "profile_family")
        illumination = CameraProfileIllumination.from_dict(
            _require_dict(d, "illumination")
        )
        camera_block = _optional_dict(d.get("camera")) or {}
        per_wavelength_raw = (
            d.get("per_wavelength")
            or camera_block.get("per_wavelength")
            or {}
        )
        if not isinstance(per_wavelength_raw, dict):
            raise ProfileError("per_wavelength must be a mapping")
        profile = cls(
            camera_profile_id=_require_str(d, "camera_profile_id"),
            profile_family=profile_family,
            illumination=illumination,
            lcd_state=_require_dict(d, "lcd_state"),
            valid_for=_require_str_list(d, "valid_for"),
            per_wavelength={
                str(k): PerWavelengthCameraSettings.from_dict(v)
                for k, v in per_wavelength_raw.items()
            },
            exposure_us=_optional_float(d.get("exposure_us", camera_block.get("exposure_us"))),
            gain_db=_optional_float(d.get("gain_db", camera_block.get("gain_db"))),
            peak_pixel=_optional_float(d.get("peak_pixel", camera_block.get("peak_pixel"))),
            saturation_margin=_optional_float(
                d.get("saturation_margin", camera_block.get("saturation_margin"))
            ),
            frames_per_capture=_optional_int(
                d.get("frames_per_capture", camera_block.get("frames_per_capture"))
            ),
            peak_pixel_domain=_optional_str(
                d.get("peak_pixel_domain", camera_block.get("peak_pixel_domain"))
            ),
            full_frame_peak_pixel=_optional_float(
                d.get("full_frame_peak_pixel", camera_block.get("full_frame_peak_pixel"))
            ),
            full_frame_saturated_pixel_count=_optional_int(
                d.get(
                    "full_frame_saturated_pixel_count",
                    camera_block.get("full_frame_saturated_pixel_count"),
                )
            ),
            depends_on_pupil_profile_id=_optional_str(
                d.get("depends_on_pupil_profile_id")
                or (_optional_dict(d.get("depends_on")) or {}).get("pupil_profile_id")
            ),
            source_raw_capture_file=_optional_str(d.get("source_raw_capture_file")),
            created_at=_optional_str(d.get("created_at")),
            software_version=_optional_str(d.get("software_version")),
            extra=_optional_dict(d.get("extra")) or {},
        )
        profile.validate()
        return profile

    def validate(self) -> None:
        self.illumination.validate()
        if not self.camera_profile_id:
            raise ProfileError("camera_profile_id must not be empty")
        if not self.valid_for:
            raise ProfileError("valid_for must not be empty")
        _validate_peak_domain_fields(
            peak_pixel_domain=self.peak_pixel_domain,
            full_frame_peak_pixel=self.full_frame_peak_pixel,
            full_frame_saturated_pixel_count=self.full_frame_saturated_pixel_count,
        )

        if self.profile_family == BROADBAND_PASSTHROUGH:
            if self.illumination.mode != BROADBAND_PASSTHROUGH:
                raise ProfileError(
                    "broadband_passthrough camera profile requires broadband illumination"
                )
            if "pupil_scan_broadband" not in self.valid_for:
                raise ProfileError(
                    "broadband_passthrough camera profile must be valid_for pupil_scan_broadband"
                )
            if self.depends_on_pupil_profile_id is not None:
                raise ProfileError(
                    "broadband_passthrough camera profile must not depend on a PupilProfile"
                )
            _validate_single_camera_settings(self)
            return

        if self.profile_family == PER_BAND_PUPIL_OPEN:
            if self.illumination.mode != MONOCHROMATIC:
                raise ProfileError(
                    "per_band_pupil_open camera profile requires monochromatic illumination"
                )
            if not (set(self.valid_for) & PSF_PRODUCING_TASK_FAMILIES):
                raise ProfileError(
                    "per_band_pupil_open camera profile valid_for must include "
                    "at least one PSF-producing task family"
                )
            if not self.depends_on_pupil_profile_id:
                raise ProfileError(
                    "per_band_pupil_open camera profile requires depends_on_pupil_profile_id"
                )
            if self.lcd_state.get("mode") != "selected_pupil_open":
                raise ProfileError(
                    "per_band_pupil_open camera profile requires lcd_state.mode selected_pupil_open"
                )
            missing = [
                str(int(w)) if float(w).is_integer() else str(w)
                for w in self.illumination.wavelengths_nm
                if _wavelength_key(w) not in self.per_wavelength
            ]
            if missing:
                raise ProfileError(
                    f"per_wavelength settings missing for wavelengths: {missing}"
                )
            return

        raise ProfileError(f"unsupported camera profile family: {self.profile_family!r}")

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "artifact_type": "camera_profile",
            "camera_profile_id": self.camera_profile_id,
            "profile_family": self.profile_family,
            "illumination": self.illumination.to_dict(),
            "lcd_state": dict(self.lcd_state),
            "valid_for": list(self.valid_for),
        }
        if self.depends_on_pupil_profile_id is not None:
            result["depends_on"] = {
                "pupil_profile_id": self.depends_on_pupil_profile_id,
            }
        if self.per_wavelength:
            result["camera"] = {
                "per_wavelength": {
                    k: v.to_dict() for k, v in sorted(self.per_wavelength.items())
                }
            }
        else:
            camera: dict[str, Any] = {}
            if self.exposure_us is not None:
                camera["exposure_us"] = self.exposure_us
            if self.gain_db is not None:
                camera["gain_db"] = self.gain_db
            if self.peak_pixel is not None:
                camera["peak_pixel"] = self.peak_pixel
            if self.saturation_margin is not None:
                camera["saturation_margin"] = self.saturation_margin
            if self.frames_per_capture is not None:
                camera["frames_per_capture"] = self.frames_per_capture
            if self.peak_pixel_domain is not None:
                camera["peak_pixel_domain"] = self.peak_pixel_domain
            if self.full_frame_peak_pixel is not None:
                camera["full_frame_peak_pixel"] = self.full_frame_peak_pixel
            if self.full_frame_saturated_pixel_count is not None:
                camera["full_frame_saturated_pixel_count"] = self.full_frame_saturated_pixel_count
            if camera:
                result["camera"] = camera
        for key in ("source_raw_capture_file", "created_at", "software_version"):
            value = getattr(self, key)
            if value is not None:
                result[key] = value
        if self.extra:
            result["extra"] = self.extra
        return result

    def to_json(self, path: str | Path | None = None) -> str:
        text = json.dumps(self.to_dict(), indent=2, sort_keys=True)
        if path is not None:
            Path(path).write_text(text + "\n", encoding="utf-8")
        return text

    @classmethod
    def load_json(cls, path: str | Path) -> CameraProfile:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ProfileError("camera profile JSON root must be a mapping")
        return cls.from_dict(data)

    @classmethod
    def load_yaml(cls, path: str | Path) -> CameraProfile:
        try:
            import yaml
        except ImportError as exc:
            raise ProfileError("PyYAML is required for YAML profile loading") from exc
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ProfileError("camera profile YAML root must be a mapping")
        return cls.from_dict(data)


def _validate_peak_domain_fields(
    *,
    peak_pixel_domain: str | None,
    full_frame_peak_pixel: float | None,
    full_frame_saturated_pixel_count: int | None,
) -> None:
    import math

    if peak_pixel_domain not in (None, "valid_pixel_domain"):
        raise ProfileError(
            "peak_pixel_domain must be None or 'valid_pixel_domain', "
            f"got {peak_pixel_domain!r}"
        )
    if full_frame_peak_pixel is not None and not math.isfinite(float(full_frame_peak_pixel)):
        raise ProfileError("full_frame_peak_pixel must be finite when present")
    if (
        full_frame_saturated_pixel_count is not None
        and int(full_frame_saturated_pixel_count) < 0
    ):
        raise ProfileError("full_frame_saturated_pixel_count must be non-negative")
    if (
        full_frame_peak_pixel is not None
        or full_frame_saturated_pixel_count is not None
    ) and peak_pixel_domain is None:
        raise ProfileError(
            "peak_pixel_domain is required when full_frame peak/saturation "
            "fields are present"
        )


def _validate_single_camera_settings(profile: CameraProfile) -> None:
    if profile.exposure_us is None or profile.exposure_us <= 0:
        raise ProfileError("camera.exposure_us must be positive")
    if profile.gain_db is None:
        raise ProfileError("camera.gain_db is required")
    if profile.frames_per_capture is not None and profile.frames_per_capture < 1:
        raise ProfileError("camera.frames_per_capture must be >= 1")


def _wavelength_key(wavelength_nm: float) -> str:
    value = float(wavelength_nm)
    return str(int(value)) if value.is_integer() else str(value)


def _require_key(d: dict[str, Any], key: str) -> Any:
    if key not in d:
        raise ProfileError(f"missing required key {key!r}")
    return d[key]


def _require_str(d: dict[str, Any], key: str) -> str:
    value = _require_key(d, key)
    if not isinstance(value, str) or not value.strip():
        raise ProfileError(f"{key!r} must be a non-empty string")
    return value.strip()


def _require_dict(d: dict[str, Any], key: str) -> dict[str, Any]:
    value = _require_key(d, key)
    if not isinstance(value, dict):
        raise ProfileError(f"{key!r} must be a mapping")
    return value


def _require_str_list(d: dict[str, Any], key: str) -> list[str]:
    value = _require_key(d, key)
    if not isinstance(value, list) or not value:
        raise ProfileError(f"{key!r} must be a non-empty list")
    result = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ProfileError(f"{key!r} must contain only non-empty strings")
        result.append(item.strip())
    return result


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ProfileError(f"expected string or null, got {type(value).__name__}")
    stripped = value.strip()
    return stripped if stripped else None


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        raise ProfileError(f"expected float or null, got {value!r}") from None


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ProfileError(f"expected int or null, got {value!r}") from None


def _optional_dict(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ProfileError(f"expected mapping or null, got {type(value).__name__}")
    return value
