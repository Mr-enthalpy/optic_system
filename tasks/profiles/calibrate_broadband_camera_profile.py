from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Protocol

import numpy as np

from tasks.illumination import IlluminationSpec
from tasks.runtime_mode import (
    RuntimePolicy,
    RuntimeModeError,
    normalize_runtime_policy,
    validate_lcd_settle_policy,
    validate_no_fake_devices,
    validate_required_devices,
)
from tasks.valid_pixel_domain import (
    ValidPixelDomainError,
    coerce_valid_pixel_domain,
    describe_valid_pixel_domain,
)

from .camera_profile import (
    BROADBAND_PASSTHROUGH,
    CameraProfile,
    CameraProfileIllumination,
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


class BroadbandCalibrationError(ValueError):
    pass


class PassThroughTLS(Protocol):
    def set_pass_through(self, timeout_s: float = 60.0):
        ...

    def get_status(self):
        ...


class BroadbandCalibrationLCD(Protocol):
    def show_physical_mask(self, mask: np.ndarray, *, mask_id: str | None = None) -> None:
        ...

    def physical_shape(self) -> tuple[int, int]:
        ...


@dataclass
class BroadbandCameraCalibrationResult:
    camera_profile: CameraProfile
    probe_results: list[ExposureProbeResult]
    tls_status: dict[str, Any]
    runtime_policy: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        result = {
            "artifact_type": "broadband_camera_calibration_result",
            "camera_profile": self.camera_profile.to_dict(),
            "probe_results": [row.to_dict() for row in self.probe_results],
            "tls_status": dict(self.tls_status),
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


@dataclass
class BroadbandCameraCalibrationPlan:
    camera_profile_id: str
    candidates: list[ExposureCandidate] = field(default_factory=list)
    exposure_search: ExposureGainSearchConfig | None = None
    physical_shape: tuple[int, int] | None = None
    frames_per_capture: int = 5
    full_scale: float = 255.0
    source: str = "xenon"
    transmissive_code: int = 255
    all_transmissive_mask_id: str = "broadband_camera_calibration_all_transmissive"
    lcd_settle_ms: float = 20.0
    allow_test_lcd_settle_below_refresh: bool = False
    valid_pixel_domain: dict[str, Any] | None = None
    valid_for: list[str] = field(default_factory=lambda: ["pupil_scan_broadband"])

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "BroadbandCameraCalibrationPlan":
        return cls(
            camera_profile_id=str(data["camera_profile_id"]),
            candidates=[ExposureCandidate.from_dict(item) for item in data.get("candidates", [])],
            exposure_search=(
                ExposureGainSearchConfig.from_dict(data["exposure_search"])
                if data.get("exposure_search") is not None else None
            ),
            physical_shape=_optional_int_pair(data.get("physical_shape")),
            frames_per_capture=int(data.get("frames_per_capture", 5)),
            full_scale=float(data.get("full_scale", 255.0)),
            source=str(data.get("source", "xenon")),
            transmissive_code=int(data.get("transmissive_code", 255)),
            all_transmissive_mask_id=str(
                data.get(
                    "all_transmissive_mask_id",
                    "broadband_camera_calibration_all_transmissive",
                )
            ),
            lcd_settle_ms=float(data.get("lcd_settle_ms", 20.0)),
            allow_test_lcd_settle_below_refresh=bool(
                data.get("allow_test_lcd_settle_below_refresh", False)
            ),
            valid_pixel_domain=_coerce_domain(data.get("valid_pixel_domain")),
            valid_for=[str(x) for x in data.get("valid_for", ["pupil_scan_broadband"])],
        )


def calibrate_broadband_camera_profile(
    plan: BroadbandCameraCalibrationPlan,
    *,
    camera: ExposureSearchCamera,
    lcd: BroadbandCalibrationLCD,
    tls: PassThroughTLS | None = None,
    valid_pixel_mask: np.ndarray | None = None,
    explicit_mask_large_exclusion_override: bool = False,
    explicit_mask_large_exclusion_reason: str | None = None,
    runtime_policy: RuntimePolicy | str | None = None,
) -> BroadbandCameraCalibrationResult:
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
    # Freeze an explicit mask once so the safety search and the provenance record
    # use the identical array (a caller-mutable ndarray must not drift between the
    # per-probe resolution and the recorded resolved domain).
    if valid_pixel_mask is not None:
        valid_pixel_mask = np.array(valid_pixel_mask, dtype=bool, copy=True, order="C")
        valid_pixel_mask.setflags(write=False)
    _validate_test_settle_override(
        allow_test_override=plan.allow_test_lcd_settle_below_refresh,
        lcd_settle_ms=plan.lcd_settle_ms,
        policy=policy,
    )
    physical_shape = _physical_shape(plan, lcd)
    all_transmissive = _solid_mask(physical_shape, plan.transmissive_code)
    lcd.show_physical_mask(
        all_transmissive,
        mask_id=plan.all_transmissive_mask_id,
    )
    _settle_lcd(
        plan.lcd_settle_ms,
        allow_test_below_refresh=plan.allow_test_lcd_settle_below_refresh,
    )

    if tls is not None:
        tls.set_pass_through(timeout_s=60.0)
    tls_status = _tls_status_dict(tls)

    exposure_search = plan.exposure_search or ExposureGainSearchConfig.from_candidates(plan.candidates)
    rows = evaluate_gain_binary_search(
        camera,
        exposure_search,
        frames_per_capture=plan.frames_per_capture,
        full_scale=plan.full_scale,
        valid_pixel_domain=plan.valid_pixel_domain,
        valid_pixel_mask=valid_pixel_mask,
        explicit_mask_large_exclusion_override=explicit_mask_large_exclusion_override,
        explicit_mask_large_exclusion_reason=explicit_mask_large_exclusion_reason,
    )
    recommended = select_recommended_probe(rows)
    frame_shape = _recommended_frame_shape(recommended)
    valid_pixel_domain_record = describe_valid_pixel_domain(
        frame_shape=frame_shape,
        valid_pixel_domain=plan.valid_pixel_domain,
        valid_pixel_mask=valid_pixel_mask,
        explicit_mask_large_exclusion_override=explicit_mask_large_exclusion_override,
        explicit_mask_large_exclusion_reason=explicit_mask_large_exclusion_reason,
    )
    full_frame_peak, full_frame_saturated = _full_frame_peak_stats(recommended)
    profile = CameraProfile(
        camera_profile_id=plan.camera_profile_id,
        profile_family=BROADBAND_PASSTHROUGH,
        illumination=CameraProfileIllumination(
            mode=BROADBAND_PASSTHROUGH,
            tls_setpoint_nm=0.0,
            effective_wavelength_nm=None,
            wavelengths_nm=[],
            source=plan.source,
        ),
        lcd_state={
            "mode": "all_transmissive",
            "mask_id": plan.all_transmissive_mask_id,
            "physical_shape": [int(physical_shape[0]), int(physical_shape[1])],
            "transmissive_code": int(plan.transmissive_code),
            "asserted_by_task": True,
        },
        valid_for=list(plan.valid_for),
        exposure_us=float(recommended.exposure_us),
        gain_db=float(recommended.gain_db),
        peak_pixel=float(recommended.peak_pixel_burst),
        saturation_margin=float(recommended.peak_margin_to_full_scale),
        frames_per_capture=int(plan.frames_per_capture),
        peak_pixel_domain="valid_pixel_domain",
        full_frame_peak_pixel=full_frame_peak,
        full_frame_saturated_pixel_count=full_frame_saturated,
        extra={
            "selection_policy": "pass_through_gain_outer_binary_exposure_inner",
            "default_selection_policy": "low_gain_then_strong_signal_then_long_exposure",
            "full_scale": float(plan.full_scale),
            "exposure_search": exposure_search.to_dict(),
            "safe_profiles_by_gain": safe_exposure_profiles_by_gain(rows),
            "valid_pixel_domain": valid_pixel_domain_record,
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
    return BroadbandCameraCalibrationResult(
        camera_profile=profile,
        probe_results=rows,
        tls_status=tls_status,
        runtime_policy=policy.to_dict(),
    )


def _tls_status_dict(tls: PassThroughTLS | None) -> dict[str, Any]:
    illumination = IlluminationSpec(
        mode="broadband_passthrough",
        effective_wavelength_nm=None,
        tls_setpoint_nm=0.0,
    ).to_dict()
    if tls is None:
        return {
            "connected": False,
            "target_wavelength_nm": 0.0,
            "current_wavelength_nm": None,
            "pass_through_requested": False,
            "illumination": illumination,
            "tls_action": "none",
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
        "illumination": illumination,
        "tls_action": "set_pass_through",
    }


def _read_attr(obj: Any, name: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _physical_shape(
    plan: BroadbandCameraCalibrationPlan,
    lcd: BroadbandCalibrationLCD,
) -> tuple[int, int]:
    if plan.physical_shape is not None:
        return _validate_shape(plan.physical_shape)
    return _validate_shape(lcd.physical_shape())


def _solid_mask(shape: tuple[int, int], code: int) -> np.ndarray:
    if code < 0 or code > 255:
        raise BroadbandCalibrationError("transmissive_code must be in [0,255]")
    h, w = _validate_shape(shape)
    return np.full((h, w), int(code), dtype=np.uint8)


def _validate_shape(shape: tuple[int, int]) -> tuple[int, int]:
    if len(shape) != 2:
        raise BroadbandCalibrationError("physical_shape must be [H,W]")
    h, w = int(shape[0]), int(shape[1])
    if h <= 0 or w <= 0:
        raise BroadbandCalibrationError("physical_shape must be positive")
    return h, w


def _optional_int_pair(value: Any) -> tuple[int, int] | None:
    if value is None:
        return None
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise BroadbandCalibrationError("physical_shape must contain two integers")
    return (int(value[0]), int(value[1]))


def _coerce_domain(value: Any) -> dict[str, Any] | None:
    try:
        return coerce_valid_pixel_domain(value)
    except ValidPixelDomainError as exc:
        raise BroadbandCalibrationError(str(exc)) from exc


def _recommended_frame_shape(recommended: ExposureProbeResult) -> tuple[int, int]:
    report = recommended.metadata.get("saturation_report") or {}
    shape = report.get("frame_shape_hw")
    if not isinstance(shape, (list, tuple)) or len(shape) != 2:
        raise BroadbandCalibrationError(
            "recommended probe is missing saturation_report.frame_shape_hw"
        )
    return (int(shape[0]), int(shape[1]))


def _full_frame_peak_stats(
    recommended: ExposureProbeResult,
) -> tuple[float | None, int | None]:
    report = recommended.metadata.get("saturation_report") or {}
    peak = report.get("full_frame_peak_pixel_burst")
    saturated = report.get("full_frame_saturated_pixel_count")
    peak_val = float(peak) if peak is not None else None
    saturated_val = int(saturated) if saturated is not None else None
    return peak_val, saturated_val


def _settle_lcd(settle_ms: float, *, allow_test_below_refresh: bool = False) -> None:
    if float(settle_ms) < 0.0:
        raise BroadbandCalibrationError("lcd_settle_ms must be non-negative")
    if float(settle_ms) < 20.0 and not allow_test_below_refresh:
        raise BroadbandCalibrationError("lcd_settle_ms must be at least 20 ms")
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
