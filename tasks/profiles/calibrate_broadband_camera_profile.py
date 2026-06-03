from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

import numpy as np

from .camera_profile import BROADBAND_PASSTHROUGH, CameraProfile, IlluminationSpec
from .exposure_search import (
    ExposureCandidate,
    ExposureProbeResult,
    ExposureSearchCamera,
    evaluate_exposure_candidates,
    select_recommended_probe,
)


class BroadbandCalibrationError(ValueError):
    pass


class PassThroughTLS(Protocol):
    def set_pass_through(self, timeout_s: float = 60.0):
        ...

    def get_status(self):
        ...


@dataclass
class BroadbandCameraCalibrationResult:
    camera_profile: CameraProfile
    probe_results: list[ExposureProbeResult]
    tls_status: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_type": "broadband_camera_calibration_result",
            "camera_profile": self.camera_profile.to_dict(),
            "probe_results": [row.to_dict() for row in self.probe_results],
            "tls_status": dict(self.tls_status),
        }


@dataclass
class BroadbandCameraCalibrationPlan:
    camera_profile_id: str
    candidates: list[ExposureCandidate]
    frames_per_capture: int = 5
    full_scale: float = 255.0
    source: str = "xenon"
    valid_for: list[str] = field(default_factory=lambda: ["pupil_scan_broadband"])

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "BroadbandCameraCalibrationPlan":
        return cls(
            camera_profile_id=str(data["camera_profile_id"]),
            candidates=[ExposureCandidate.from_dict(item) for item in data["candidates"]],
            frames_per_capture=int(data.get("frames_per_capture", 5)),
            full_scale=float(data.get("full_scale", 255.0)),
            source=str(data.get("source", "xenon")),
            valid_for=[str(x) for x in data.get("valid_for", ["pupil_scan_broadband"])],
        )


def calibrate_broadband_camera_profile(
    plan: BroadbandCameraCalibrationPlan,
    *,
    camera: ExposureSearchCamera,
    tls: PassThroughTLS | None = None,
    valid_pixel_mask: np.ndarray | None = None,
) -> BroadbandCameraCalibrationResult:
    if tls is not None:
        tls.set_pass_through(timeout_s=60.0)
    tls_status = _tls_status_dict(tls)

    rows = evaluate_exposure_candidates(
        camera,
        plan.candidates,
        frames_per_capture=plan.frames_per_capture,
        full_scale=plan.full_scale,
        valid_pixel_mask=valid_pixel_mask,
    )
    recommended = select_recommended_probe(rows)
    profile = CameraProfile(
        camera_profile_id=plan.camera_profile_id,
        profile_family=BROADBAND_PASSTHROUGH,
        illumination=IlluminationSpec(
            mode=BROADBAND_PASSTHROUGH,
            tls_setpoint_nm=0.0,
            effective_wavelength_nm=None,
            wavelengths_nm=[],
            source=plan.source,
        ),
        lcd_state={"mode": "all_transmissive"},
        valid_for=list(plan.valid_for),
        exposure_us=float(recommended.exposure_us),
        gain_db=float(recommended.gain_db),
        peak_pixel=float(recommended.peak_pixel_burst),
        saturation_margin=float(recommended.peak_margin_to_full_scale),
        frames_per_capture=int(plan.frames_per_capture),
        extra={
            "selection_policy": "pass_through_safe_low_gain_high_signal",
            "full_scale": float(plan.full_scale),
        },
    )
    profile.validate()
    return BroadbandCameraCalibrationResult(
        camera_profile=profile,
        probe_results=rows,
        tls_status=tls_status,
    )


def _tls_status_dict(tls: PassThroughTLS | None) -> dict[str, Any]:
    if tls is None:
        return {
            "connected": False,
            "target_wavelength_nm": 0.0,
            "current_wavelength_nm": None,
            "pass_through_requested": False,
        }
    try:
        status = tls.get_status()
    except Exception:
        status = None
    return {
        "connected": bool(_read_attr(status, "connected", True)),
        "target_wavelength_nm": _read_attr(status, "target_wavelength_nm", 0.0),
        "current_wavelength_nm": _read_attr(status, "current_wavelength_nm", None),
        "grating": _read_attr(status, "grating", None),
        "moving": bool(_read_attr(status, "moving", False)),
        "pass_through_requested": True,
    }


def _read_attr(obj: Any, name: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)
