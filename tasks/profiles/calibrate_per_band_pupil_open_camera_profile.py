from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Protocol

import numpy as np

from tasks.runtime_mode import (
    RuntimePolicy,
    RuntimeModeError,
    normalize_runtime_policy,
    validate_lcd_settle_policy,
    validate_no_fake_devices,
    validate_required_devices,
)

from .camera_profile import (
    MONOCHROMATIC,
    PER_BAND_PUPIL_OPEN,
    CameraProfile,
    CameraProfileIllumination,
    PerWavelengthCameraSettings,
)
from .exposure_search import (
    ExposureCandidate,
    ExposureGainSearchConfig,
    ExposureProbeResult,
    ExposureSearchCamera,
    evaluate_gain_binary_search,
    safe_exposure_profiles_by_gain,
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
    candidates: list[ExposureCandidate] = field(default_factory=list)
    exposure_search: ExposureGainSearchConfig | None = None
    grating: int | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "WavelengthCalibrationSpec":
        return cls(
            wavelength_nm=float(data["wavelength_nm"]),
            candidates=[ExposureCandidate.from_dict(item) for item in data.get("candidates", [])],
            exposure_search=(
                ExposureGainSearchConfig.from_dict(data["exposure_search"])
                if data.get("exposure_search") is not None else None
            ),
            grating=int(data["grating"]) if data.get("grating") is not None else None,
        )


@dataclass
class PerBandPupilOpenCalibrationPlan:
    camera_profile_id: str
    pupil_profile_id: str
    wavelengths: list[WavelengthCalibrationSpec]
    frames_per_capture: int = 5
    full_scale: float = 255.0
    lcd_settle_ms: float = 20.0
    allow_test_lcd_settle_below_refresh: bool = False
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
            lcd_settle_ms=float(data.get("lcd_settle_ms", 20.0)),
            allow_test_lcd_settle_below_refresh=bool(
                data.get("allow_test_lcd_settle_below_refresh", False)
            ),
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
    runtime_policy: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        result = {
            "artifact_type": "per_band_pupil_open_camera_calibration_result",
            "camera_profile": self.camera_profile.to_dict(),
            "probe_results_by_wavelength": {
                key: [row.to_dict() for row in rows]
                for key, rows in sorted(self.probe_results_by_wavelength.items())
            },
        }
        if self.runtime_policy is not None:
            result["runtime_policy"] = dict(self.runtime_policy)
            result["runtime_mode"] = self.runtime_policy.get("mode")
        return result

    def write_json(self, path: str | Path) -> None:
        Path(path).write_text(
            json.dumps(self.to_dict(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )


def calibrate_per_band_pupil_open_camera_profile(
    plan: PerBandPupilOpenCalibrationPlan,
    *,
    pupil_profile: PupilProfile,
    camera: ExposureSearchCamera,
    lcd: PerBandLCD,
    tls: PerBandTLS | None = None,
    valid_pixel_mask: np.ndarray | None = None,
    runtime_policy: RuntimePolicy | str | None = None,
) -> PerBandPupilOpenCalibrationResult:
    policy = normalize_runtime_policy(runtime_policy)
    devices = SimpleNamespace(camera=camera, lcd=lcd, tls=tls)
    validate_required_devices(
        devices,
        policy=policy,
        require_camera=True,
        require_lcd=True,
        require_tls=True,
    )
    validate_no_fake_devices(devices, policy=policy)
    _validate_test_settle_override(
        allow_test_override=plan.allow_test_lcd_settle_below_refresh,
        lcd_settle_ms=plan.lcd_settle_ms,
        policy=policy,
    )
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
    _settle_lcd(
        plan.lcd_settle_ms,
        allow_test_below_refresh=plan.allow_test_lcd_settle_below_refresh,
    )

    per_wavelength: dict[str, PerWavelengthCameraSettings] = {}
    safe_profiles_by_wavelength: dict[str, list[dict[str, Any]]] = {}
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
        exposure_search = spec.exposure_search or ExposureGainSearchConfig.from_candidates(spec.candidates)
        rows = evaluate_gain_binary_search(
            camera,
            exposure_search,
            frames_per_capture=plan.frames_per_capture,
            full_scale=plan.full_scale,
            valid_pixel_mask=valid_pixel_mask,
        )
        for row in rows:
            row.metadata["tls_outer_loop_wavelength_nm"] = float(spec.wavelength_nm)
        selected = select_recommended_probe(rows)
        key = _wavelength_key(spec.wavelength_nm)
        probe_results[key] = rows
        safe_profiles_by_wavelength[key] = safe_exposure_profiles_by_gain(rows)
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
        illumination=CameraProfileIllumination(
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
            "selection_policy": "selected_pupil_open_tls_outer_gain_outer_binary_exposure_inner",
            "default_selection_policy": "low_gain_then_strong_signal_then_long_exposure",
            "full_scale": float(plan.full_scale),
            "tls_iteration_order": "outermost_by_wavelength",
            "safe_profiles_by_wavelength": safe_profiles_by_wavelength,
            "timing_policy": {
                "lcd_settle_ms": float(plan.lcd_settle_ms),
                "allow_test_lcd_settle_below_refresh": bool(
                    plan.allow_test_lcd_settle_below_refresh
                ),
                "hardware_default_camera_param_settle_ms": 300.0,
                "hardware_default_discard_frames_after_param_change": 80,
            },
        },
    )
    profile.validate()
    return PerBandPupilOpenCalibrationResult(
        camera_profile=profile,
        probe_results_by_wavelength=probe_results,
        runtime_policy=policy.to_dict(),
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


def _settle_lcd(settle_ms: float, *, allow_test_below_refresh: bool = False) -> None:
    if float(settle_ms) < 0.0:
        raise PerBandCalibrationError("lcd_settle_ms must be non-negative")
    if float(settle_ms) < 20.0 and not allow_test_below_refresh:
        raise PerBandCalibrationError("lcd_settle_ms must be at least 20 ms")
    if float(settle_ms) > 0.0:
        time.sleep(float(settle_ms) / 1000.0)


def _validate_test_settle_override(
    *,
    allow_test_override: bool,
    lcd_settle_ms: float,
    policy: RuntimePolicy,
) -> None:
    if allow_test_override and not policy.allow_test_settle_override:
        raise RuntimeModeError(
            "allow_test_lcd_settle_below_refresh requires explicit non-hardware runtime mode"
        )
    validate_lcd_settle_policy(
        lcd_settle_ms=lcd_settle_ms,
        expected_min_settle_ms=20,
        policy=policy,
    )
