from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

import numpy as np

from .camera_profile import MONOCHROMATIC, PER_BAND_PUPIL_OPEN, CameraProfile, IlluminationSpec, PerWavelengthCameraSettings
from .exposure_search import (
    ExposureCandidate,
    ExposureProbeResult,
    ExposureSearchCamera,
    evaluate_exposure_candidates,
    select_recommended_probe,
)
from .pupil_profile import PupilProfile
from .scan_pupil_broadband import circular_window_mask


class PerBandCalibrationError(ValueError):
    pass


class PerBandLCD(Protocol):
    def show_physical_mask(self, mask: np.ndarray, *, mask_id: str | None = None) -> None:
        ...


class PerBandTLS(Protocol):
    def set_grating(self, grating: int) -> None:
        ...

    def set_wavelength_nm(self, wavelength_nm: float):
        ...

    def move(self, timeout_s: float = 60.0):
        ...

    def wait_until_idle(self, *, timeout_s: float = 60.0, **kwargs):
        ...

    def get_status(self):
        ...


@dataclass
class WavelengthCalibrationSpec:
    wavelength_nm: float
    candidates: list[ExposureCandidate]
    grating: int | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "WavelengthCalibrationSpec":
        return cls(
            wavelength_nm=float(data["wavelength_nm"]),
            candidates=[ExposureCandidate.from_dict(item) for item in data["candidates"]],
            grating=int(data["grating"]) if data.get("grating") is not None else None,
        )


@dataclass
class PerBandPupilOpenCalibrationPlan:
    camera_profile_id: str
    pupil_profile_id: str
    wavelengths: list[WavelengthCalibrationSpec]
    frames_per_capture: int = 5
    full_scale: float = 255.0
    valid_for: list[str] = field(default_factory=lambda: [
        "psf_dictionary_capture",
        "dotf_capture",
        "mask_family_psf_capture",
        "target_multiframe_capture",
    ])

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PerBandPupilOpenCalibrationPlan":
        return cls(
            camera_profile_id=str(data["camera_profile_id"]),
            pupil_profile_id=str(data["pupil_profile_id"]),
            wavelengths=[WavelengthCalibrationSpec.from_dict(item) for item in data["wavelengths"]],
            frames_per_capture=int(data.get("frames_per_capture", 5)),
            full_scale=float(data.get("full_scale", 255.0)),
            valid_for=[str(x) for x in data.get("valid_for", [
                "psf_dictionary_capture",
                "dotf_capture",
                "mask_family_psf_capture",
                "target_multiframe_capture",
            ])],
        )


@dataclass
class PerBandPupilOpenCalibrationResult:
    camera_profile: CameraProfile
    probe_results_by_wavelength: dict[str, list[ExposureProbeResult]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_type": "per_band_pupil_open_camera_calibration_result",
            "camera_profile": self.camera_profile.to_dict(),
            "probe_results_by_wavelength": {
                key: [row.to_dict() for row in rows]
                for key, rows in sorted(self.probe_results_by_wavelength.items())
            },
        }


def calibrate_per_band_pupil_open_camera_profile(
    plan: PerBandPupilOpenCalibrationPlan,
    *,
    pupil_profile: PupilProfile,
    camera: ExposureSearchCamera,
    lcd: PerBandLCD,
    tls: PerBandTLS | None = None,
    valid_pixel_mask: np.ndarray | None = None,
) -> PerBandPupilOpenCalibrationResult:
    if plan.pupil_profile_id != pupil_profile.pupil_profile_id:
        raise PerBandCalibrationError("plan pupil_profile_id does not match PupilProfile")
    if not plan.wavelengths:
        raise PerBandCalibrationError("at least one wavelength is required")
    if pupil_profile.lcd_physical_radius is None:
        raise PerBandCalibrationError("PupilProfile.lcd_physical_radius is required")

    pupil_mask = circular_window_mask(
        tuple(int(v) for v in _physical_shape_from_pupil(pupil_profile)),
        center=pupil_profile.lcd_physical_center,
        radius=float(pupil_profile.lcd_physical_radius),
        bg_code=0,
        aperture_code=255,
    )
    lcd.show_physical_mask(pupil_mask, mask_id=f"selected_pupil_open:{pupil_profile.pupil_profile_id}")

    per_wavelength: dict[str, PerWavelengthCameraSettings] = {}
    probe_results: dict[str, list[ExposureProbeResult]] = {}
    wavelengths_nm: list[float] = []
    for spec in plan.wavelengths:
        if spec.wavelength_nm <= 0.0:
            raise PerBandCalibrationError("per-band monochromatic calibration wavelengths must be positive")
        wavelengths_nm.append(float(spec.wavelength_nm))
        if tls is not None:
            if spec.grating is not None:
                tls.set_grating(int(spec.grating))
            tls.set_wavelength_nm(float(spec.wavelength_nm))
            tls.move(timeout_s=60.0)
            tls.wait_until_idle(timeout_s=60.0)
        rows = evaluate_exposure_candidates(
            camera,
            spec.candidates,
            frames_per_capture=plan.frames_per_capture,
            full_scale=plan.full_scale,
            valid_pixel_mask=valid_pixel_mask,
        )
        selected = select_recommended_probe(rows)
        key = _wavelength_key(spec.wavelength_nm)
        probe_results[key] = rows
        per_wavelength[key] = PerWavelengthCameraSettings(
            exposure_us=float(selected.exposure_us),
            gain_db=float(selected.gain_db),
            peak_pixel=float(selected.peak_pixel_burst),
            saturation_margin=float(selected.peak_margin_to_full_scale),
            frames_per_capture=int(plan.frames_per_capture),
        )

    profile = CameraProfile(
        camera_profile_id=plan.camera_profile_id,
        profile_family=PER_BAND_PUPIL_OPEN,
        illumination=IlluminationSpec(
            mode=MONOCHROMATIC,
            wavelengths_nm=wavelengths_nm,
        ),
        lcd_state={
            "mode": "selected_pupil_open",
            "pupil_profile_id": pupil_profile.pupil_profile_id,
        },
        valid_for=list(plan.valid_for),
        per_wavelength=per_wavelength,
        depends_on_pupil_profile_id=pupil_profile.pupil_profile_id,
        extra={
            "selection_policy": "selected_pupil_open_per_wavelength_safe_low_gain_high_signal",
            "full_scale": float(plan.full_scale),
        },
    )
    profile.validate()
    return PerBandPupilOpenCalibrationResult(
        camera_profile=profile,
        probe_results_by_wavelength=probe_results,
    )


def _physical_shape_from_pupil(pupil_profile: PupilProfile) -> tuple[int, int]:
    shape = pupil_profile.extra.get("physical_shape") if isinstance(pupil_profile.extra, dict) else None
    if shape is not None:
        return (int(shape[0]), int(shape[1]))
    if pupil_profile.aperture_window is None:
        r = float(pupil_profile.lcd_physical_radius or 1.0)
        cx, cy = pupil_profile.lcd_physical_center
        return (int(np.ceil(cy + r + 2)), int(np.ceil(cx + r + 2)))
    x, y, w, h = pupil_profile.aperture_window
    return (int(y + h), int(x + w))


def _wavelength_key(wavelength_nm: float) -> str:
    value = float(wavelength_nm)
    return str(int(value)) if value.is_integer() else str(value)
