from __future__ import annotations

from pathlib import Path

import pytest

from tasks.profiles import (
    BROADBAND_PASSTHROUGH,
    PER_BAND_PUPIL_OPEN,
    CameraProfile,
    ProfileError,
    PupilProfile,
)
from tasks.psf import (
    ProfileDependencyError,
    validate_broadband_pupil_scan_dependencies,
    validate_psf_profile_dependencies,
)


def broadband_camera_profile_dict() -> dict:
    return {
        "camera_profile_id": "broadband_passthrough_safe_v1",
        "profile_family": BROADBAND_PASSTHROUGH,
        "illumination": {
            "mode": BROADBAND_PASSTHROUGH,
            "tls_setpoint_nm": 0,
            "effective_wavelength_nm": None,
            "source": "xenon",
        },
        "lcd_state": {"mode": "safe_probe_mask"},
        "camera": {
            "exposure_us": 120.0,
            "gain_db": 0.0,
            "peak_pixel": 210,
            "saturation_margin": 45,
        },
        "valid_for": ["pupil_scan_broadband"],
    }


def pupil_profile_dict() -> dict:
    return {
        "pupil_profile_id": "pupil_profile_v1",
        "lcd_coordinate_convention": "physical_mono_xy",
        "lcd_display_index": 1,
        "subpixel_axis": 1,
        "lcd_physical_center": [1065.25, 1871.54],
        "lcd_physical_radius": 52.8,
        "camera_psf_center": [1149.13, 934.51],
        "recommended_roi": [893, 679, 512, 512],
        "fit_quality": {"r2": 0.9992},
    }


def per_band_camera_profile_dict() -> dict:
    return {
        "camera_profile_id": "per_band_pupil_open_v1",
        "profile_family": PER_BAND_PUPIL_OPEN,
        "depends_on": {"pupil_profile_id": "pupil_profile_v1"},
        "illumination": {
            "mode": "monochromatic",
            "wavelengths_nm": [450, 550, 650],
        },
        "lcd_state": {
            "mode": "selected_pupil_open",
            "pupil_profile_id": "pupil_profile_v1",
        },
        "camera": {
            "per_wavelength": {
                "450": {
                    "exposure_us": 780.0,
                    "gain_db": 0.0,
                    "peak_pixel": 230,
                    "saturation_margin": 25,
                },
                "550": {
                    "exposure_us": 490.0,
                    "gain_db": 0.0,
                    "peak_pixel": 214,
                    "saturation_margin": 41,
                },
                "650": {
                    "exposure_us": 2240.0,
                    "gain_db": 0.0,
                    "peak_pixel": 236,
                    "saturation_margin": 19,
                },
            }
        },
        "valid_for": [
            "psf_dictionary_capture",
            "dotf_capture",
            "mask_family_psf_capture",
        ],
    }


def test_broadband_profile_records_setpoint_zero_not_wavelength() -> None:
    profile = CameraProfile.from_dict(broadband_camera_profile_dict())

    assert profile.illumination.mode == BROADBAND_PASSTHROUGH
    assert profile.illumination.tls_setpoint_nm == 0
    assert profile.illumination.effective_wavelength_nm is None
    assert profile.illumination.wavelengths_nm == []
    assert profile.to_dict()["illumination"]["effective_wavelength_nm"] is None


def test_broadband_profile_rejects_scientific_zero_wavelength() -> None:
    data = broadband_camera_profile_dict()
    data["illumination"]["wavelengths_nm"] = [0]

    with pytest.raises(ProfileError, match="wavelengths_nm"):
        CameraProfile.from_dict(data)


def test_monochromatic_profile_rejects_tls_setpoint_zero() -> None:
    data = per_band_camera_profile_dict()
    data["illumination"]["tls_setpoint_nm"] = 0

    with pytest.raises(ProfileError, match="tls_setpoint_nm"):
        CameraProfile.from_dict(data)


def test_pupil_profile_roundtrip_json(tmp_path: Path) -> None:
    profile = PupilProfile.from_dict(pupil_profile_dict())
    path = tmp_path / "pupil_profile.json"

    profile.to_json(path)
    loaded = PupilProfile.load_json(path)

    assert loaded.pupil_profile_id == "pupil_profile_v1"
    assert loaded.subpixel_axis == 1
    assert loaded.recommended_roi == (893, 679, 512, 512)


def test_camera_profile_rejects_unknown_peak_pixel_domain() -> None:
    data = broadband_camera_profile_dict()
    data["camera"]["peak_pixel_domain"] = "full_frame"

    with pytest.raises(ProfileError, match="peak_pixel_domain"):
        CameraProfile.from_dict(data)


def test_camera_profile_requires_peak_domain_when_full_frame_peak_present() -> None:
    data = broadband_camera_profile_dict()
    data["camera"]["full_frame_peak_pixel"] = 255.0

    with pytest.raises(ProfileError, match="peak_pixel_domain is required"):
        CameraProfile.from_dict(data)


def test_camera_profile_accepts_dual_peak_fields() -> None:
    data = broadband_camera_profile_dict()
    data["camera"]["peak_pixel_domain"] = "valid_pixel_domain"
    data["camera"]["full_frame_peak_pixel"] = 255.0
    data["camera"]["full_frame_saturated_pixel_count"] = 3

    profile = CameraProfile.from_dict(data)

    assert profile.peak_pixel_domain == "valid_pixel_domain"
    assert profile.full_frame_peak_pixel == 255.0
    assert profile.full_frame_saturated_pixel_count == 3
    reloaded = CameraProfile.from_dict(profile.to_dict())
    assert reloaded.full_frame_saturated_pixel_count == 3


def test_per_wavelength_settings_reject_negative_saturated_count() -> None:
    data = per_band_camera_profile_dict()
    data["camera"]["per_wavelength"]["450"]["peak_pixel_domain"] = "valid_pixel_domain"
    data["camera"]["per_wavelength"]["450"]["full_frame_saturated_pixel_count"] = -1

    with pytest.raises(ProfileError, match="non-negative"):
        CameraProfile.from_dict(data)


def test_camera_profile_validate_recurses_into_per_wavelength() -> None:
    from tasks.profiles.camera_profile import PerWavelengthCameraSettings

    profile = CameraProfile.from_dict(per_band_camera_profile_dict())
    # Inject an invalid settings object directly (bypassing from_dict validation).
    profile.per_wavelength["450"] = PerWavelengthCameraSettings(
        exposure_us=-1.0, gain_db=0.0
    )

    with pytest.raises(ProfileError, match="per_wavelength\\['450'\\]"):
        profile.validate()


@pytest.mark.parametrize("bad", [1.5, True, "3"])
def test_broadband_full_frame_saturated_count_rejects_non_integer(bad) -> None:
    data = broadband_camera_profile_dict()
    data["camera"]["peak_pixel_domain"] = "valid_pixel_domain"
    data["camera"]["full_frame_saturated_pixel_count"] = bad

    with pytest.raises(ProfileError, match="full_frame_saturated_pixel_count"):
        CameraProfile.from_dict(data)


@pytest.mark.parametrize("bad", [1.5, True, "3"])
def test_per_wavelength_full_frame_saturated_count_rejects_non_integer(bad) -> None:
    data = per_band_camera_profile_dict()
    data["camera"]["per_wavelength"]["450"]["peak_pixel_domain"] = "valid_pixel_domain"
    data["camera"]["per_wavelength"]["450"]["full_frame_saturated_pixel_count"] = bad

    with pytest.raises(ProfileError, match="full_frame_saturated_pixel_count"):
        CameraProfile.from_dict(data)


def test_per_band_profile_requires_selected_pupil_open() -> None:
    data = per_band_camera_profile_dict()
    data["lcd_state"]["mode"] = "all_open"

    with pytest.raises(ProfileError, match="selected_pupil_open"):
        CameraProfile.from_dict(data)


def test_per_band_profile_valid_for_requires_psf_family() -> None:
    data = per_band_camera_profile_dict()
    data["valid_for"] = ["pupil_scan_broadband"]

    with pytest.raises(ProfileError, match="PSF-producing"):
        CameraProfile.from_dict(data)


def test_broadband_pupil_scan_requires_broadband_profile() -> None:
    profile = CameraProfile.from_dict(broadband_camera_profile_dict())
    plan = {
        "requires": {"camera_profile_id": "broadband_passthrough_safe_v1"},
        "illumination": {"mode": BROADBAND_PASSTHROUGH},
    }

    validate_broadband_pupil_scan_dependencies(plan, camera_profile=profile)


def test_broadband_pupil_scan_rejects_existing_pupil_dependency() -> None:
    profile = CameraProfile.from_dict(broadband_camera_profile_dict())
    plan = {
        "requires": {
            "camera_profile_id": "broadband_passthrough_safe_v1",
            "pupil_profile_id": "pupil_profile_v1",
        },
        "illumination": {"mode": BROADBAND_PASSTHROUGH},
    }

    with pytest.raises(ProfileDependencyError, match="must not require"):
        validate_broadband_pupil_scan_dependencies(plan, camera_profile=profile)


def test_psf_plan_requires_matching_pupil_and_per_band_profile() -> None:
    pupil = PupilProfile.from_dict(pupil_profile_dict())
    camera = CameraProfile.from_dict(per_band_camera_profile_dict())
    plan = {
        "task_type": "psf_dictionary_capture",
        "requires": {
            "pupil_profile_id": "pupil_profile_v1",
            "camera_profile_id": "per_band_pupil_open_v1",
        },
        "illumination": {
            "mode": "monochromatic",
            "wavelengths_nm": [450, 550, 650],
        },
    }

    validate_psf_profile_dependencies(
        plan,
        pupil_profile=pupil,
        camera_profile=camera,
    )


def test_psf_plan_wavelengths_must_be_covered_by_camera_profile() -> None:
    pupil = PupilProfile.from_dict(pupil_profile_dict())
    camera = CameraProfile.from_dict(per_band_camera_profile_dict())
    plan = {
        "task_type": "psf_dictionary_capture",
        "requires": {
            "pupil_profile_id": "pupil_profile_v1",
            "camera_profile_id": "per_band_pupil_open_v1",
        },
        "illumination": {
            "mode": "monochromatic",
            "wavelengths_nm": [450, 550, 700],
        },
    }

    with pytest.raises(ProfileDependencyError, match="700"):
        validate_psf_profile_dependencies(
            plan,
            pupil_profile=pupil,
            camera_profile=camera,
        )


def test_psf_plan_rejects_broadband_camera_profile() -> None:
    pupil = PupilProfile.from_dict(pupil_profile_dict())
    camera = CameraProfile.from_dict(broadband_camera_profile_dict())
    plan = {
        "task_type": "psf_dictionary_capture",
        "requires": {
            "pupil_profile_id": "pupil_profile_v1",
            "camera_profile_id": "broadband_passthrough_safe_v1",
        },
    }

    with pytest.raises(ProfileDependencyError, match="per_band_pupil_open"):
        validate_psf_profile_dependencies(
            plan,
            pupil_profile=pupil,
            camera_profile=camera,
        )


def test_psf_plan_refuses_missing_explicit_profile_ids() -> None:
    pupil = PupilProfile.from_dict(pupil_profile_dict())
    camera = CameraProfile.from_dict(per_band_camera_profile_dict())

    with pytest.raises(ProfileDependencyError, match="requires"):
        validate_psf_profile_dependencies(
            {},
            pupil_profile=pupil,
            camera_profile=camera,
        )
