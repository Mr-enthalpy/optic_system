from __future__ import annotations

from typing import Any

from tasks.profiles.camera_profile import (
    BROADBAND_PASSTHROUGH,
    PER_BAND_PUPIL_OPEN,
    CameraProfile,
)
from tasks.profiles.pupil_profile import (
    PupilProfile,
)


class ProfileDependencyError(ValueError):
    pass


def validate_broadband_pupil_scan_dependencies(
    plan: dict[str, Any],
    *,
    camera_profile: CameraProfile,
) -> None:
    requires = _requires(plan)
    required_camera = _require_str(requires, "camera_profile_id")
    if required_camera != camera_profile.camera_profile_id:
        raise ProfileDependencyError(
            f"plan requires camera_profile_id {required_camera!r}, "
            f"got {camera_profile.camera_profile_id!r}"
        )
    if "pupil_profile_id" in requires:
        raise ProfileDependencyError(
            "broadband pupil scan must not require an existing PupilProfile"
        )
    if camera_profile.profile_family != BROADBAND_PASSTHROUGH:
        raise ProfileDependencyError(
            "broadband pupil scan requires a broadband_passthrough CameraProfile"
        )
    if "pupil_scan_broadband" not in camera_profile.valid_for:
        raise ProfileDependencyError(
            "CameraProfile is not valid_for pupil_scan_broadband"
        )
    illumination = _optional_dict(plan.get("illumination")) or {}
    if illumination and illumination.get("mode") != BROADBAND_PASSTHROUGH:
        raise ProfileDependencyError(
            "broadband pupil scan plan illumination.mode must be broadband_passthrough"
        )


def validate_psf_profile_dependencies(
    plan: dict[str, Any],
    *,
    pupil_profile: PupilProfile,
    camera_profile: CameraProfile,
) -> None:
    requires = _requires(plan)
    required_pupil = _require_str(requires, "pupil_profile_id")
    required_camera = _require_str(requires, "camera_profile_id")

    if required_pupil != pupil_profile.pupil_profile_id:
        raise ProfileDependencyError(
            f"plan requires pupil_profile_id {required_pupil!r}, "
            f"got {pupil_profile.pupil_profile_id!r}"
        )
    if required_camera != camera_profile.camera_profile_id:
        raise ProfileDependencyError(
            f"plan requires camera_profile_id {required_camera!r}, "
            f"got {camera_profile.camera_profile_id!r}"
        )
    if camera_profile.profile_family != PER_BAND_PUPIL_OPEN:
        raise ProfileDependencyError(
            "PSF-producing tasks require a per_band_pupil_open CameraProfile"
        )
    if camera_profile.depends_on_pupil_profile_id != pupil_profile.pupil_profile_id:
        raise ProfileDependencyError(
            "CameraProfile must depend on the same PupilProfile required by the plan"
        )
    if camera_profile.illumination.mode == BROADBAND_PASSTHROUGH:
        raise ProfileDependencyError(
            "PSF-producing tasks must not use broadband_passthrough exposure profiles"
        )
    illumination = _optional_dict(plan.get("illumination")) or {}
    if illumination.get("mode") == BROADBAND_PASSTHROUGH:
        raise ProfileDependencyError(
            "PSF-producing task illumination must not be broadband_passthrough"
        )
    plan_wavelengths = illumination.get("wavelengths_nm") or []
    if plan_wavelengths:
        if not isinstance(plan_wavelengths, list):
            raise ProfileDependencyError("illumination.wavelengths_nm must be a list")
        missing = [
            _wavelength_key(w)
            for w in plan_wavelengths
            if _wavelength_key(w) not in camera_profile.per_wavelength
        ]
        if missing:
            raise ProfileDependencyError(
                "CameraProfile per_wavelength settings missing for plan wavelengths: "
                f"{missing}"
            )


def _requires(plan: dict[str, Any]) -> dict[str, Any]:
    requires = plan.get("requires")
    if not isinstance(requires, dict):
        raise ProfileDependencyError("plan requires a mapping field named 'requires'")
    return requires


def _require_str(d: dict[str, Any], key: str) -> str:
    value = d.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ProfileDependencyError(f"requires.{key} must be a non-empty string")
    return value.strip()


def _optional_dict(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ProfileDependencyError(
            f"expected mapping or null, got {type(value).__name__}"
        )
    return value


def _wavelength_key(wavelength_nm: Any) -> str:
    try:
        value = float(wavelength_nm)
    except (TypeError, ValueError):
        raise ProfileDependencyError(
            f"wavelength value must be numeric, got {wavelength_nm!r}"
        ) from None
    return str(int(value)) if value.is_integer() else str(value)
