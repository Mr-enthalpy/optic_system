from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import numpy as np

from .camera_profile import BROADBAND_PASSTHROUGH, CameraProfile
from .pupil_profile import PupilProfile


class PupilScanError(ValueError):
    pass


class PupilScanCamera(Protocol):
    def apply_camera_params(self, exposure_us=None, gain_db=None):
        ...

    def acquire_burst(self, k: int):
        ...


class PupilScanLCD(Protocol):
    def show_physical_mask(self, mask: np.ndarray, *, mask_id: str | None = None) -> None:
        ...

    def metadata(self) -> dict[str, Any]:
        ...

    def physical_shape(self) -> tuple[int, int]:
        ...

    def subpixel_axis(self) -> int:
        ...


class PupilScanTLS(Protocol):
    def set_pass_through(self, timeout_s: float = 60.0):
        ...

    def get_status(self):
        ...


@dataclass
class PupilScanPlan:
    pupil_profile_id: str
    camera_profile_id: str
    physical_shape: tuple[int, int]
    lcd_display_index: int
    subpixel_axis: int
    frames_per_capture: int = 5
    bar_width: int = 16
    scan_step: int = 8
    scan_range_xyxy: tuple[int, int, int, int] | None = None
    bg_code: int = 255
    bar_code: int = 0
    radius_factor: float = 0.9
    source_raw_capture_file: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PupilScanPlan":
        return cls(
            pupil_profile_id=str(data["pupil_profile_id"]),
            camera_profile_id=str(data["camera_profile_id"]),
            physical_shape=_int_pair(data["physical_shape"], "physical_shape"),
            lcd_display_index=int(data["lcd_display_index"]),
            subpixel_axis=int(data["subpixel_axis"]),
            frames_per_capture=int(data.get("frames_per_capture", 5)),
            bar_width=int(data.get("bar_width", 16)),
            scan_step=int(data.get("scan_step", 8)),
            scan_range_xyxy=_optional_int_quad(data.get("scan_range_xyxy")),
            bg_code=int(data.get("bg_code", 255)),
            bar_code=int(data.get("bar_code", 0)),
            radius_factor=float(data.get("radius_factor", 0.9)),
            source_raw_capture_file=data.get("source_raw_capture_file"),
            extra=dict(data.get("extra") or {}),
        )


@dataclass
class PupilScanReport:
    plan: PupilScanPlan
    pupil_profile: PupilProfile
    x_positions: np.ndarray
    x_energies: np.ndarray
    y_positions: np.ndarray
    y_energies: np.ndarray
    fit_quality: dict[str, Any]
    tls_status: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_type": "broadband_pupil_scan_report",
            "pupil_profile": self.pupil_profile.to_dict(),
            "x_positions": self.x_positions.astype(float).tolist(),
            "x_energies": self.x_energies.astype(float).tolist(),
            "y_positions": self.y_positions.astype(float).tolist(),
            "y_energies": self.y_energies.astype(float).tolist(),
            "fit_quality": dict(self.fit_quality),
            "tls_status": dict(self.tls_status),
            "plan": {
                "pupil_profile_id": self.plan.pupil_profile_id,
                "camera_profile_id": self.plan.camera_profile_id,
                "physical_shape": list(self.plan.physical_shape),
                "lcd_display_index": int(self.plan.lcd_display_index),
                "subpixel_axis": int(self.plan.subpixel_axis),
                "frames_per_capture": int(self.plan.frames_per_capture),
                "bar_width": int(self.plan.bar_width),
                "scan_step": int(self.plan.scan_step),
                "scan_range_xyxy": (
                    list(self.plan.scan_range_xyxy)
                    if self.plan.scan_range_xyxy is not None else None
                ),
                "radius_factor": float(self.plan.radius_factor),
            },
        }

    def write_json(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")


def run_broadband_pupil_scan(
    plan: PupilScanPlan,
    *,
    camera_profile: CameraProfile,
    camera: PupilScanCamera,
    lcd: PupilScanLCD,
    tls: PupilScanTLS | None = None,
) -> PupilScanReport:
    _validate_broadband_camera_profile(plan, camera_profile)
    if camera_profile.camera_profile_id != plan.camera_profile_id:
        raise PupilScanError("plan camera_profile_id does not match camera_profile")
    if tls is not None:
        tls.set_pass_through(timeout_s=60.0)

    camera.apply_camera_params(
        exposure_us=camera_profile.exposure_us,
        gain_db=camera_profile.gain_db,
    )
    physical_shape = tuple(int(v) for v in plan.physical_shape)
    bright = _capture_mask(camera, lcd, solid_mask(physical_shape, plan.bg_code), "pupil_scan_bright", plan.frames_per_capture)
    bright_sum = float(np.sum(bright))

    x_positions, x_energies = _scan_axis(
        "x",
        plan=plan,
        camera=camera,
        lcd=lcd,
        bright_sum=bright_sum,
    )
    y_positions, y_energies = _scan_axis(
        "y",
        plan=plan,
        camera=camera,
        lcd=lcd,
        bright_sum=bright_sum,
    )
    fit = solve_pupil_from_bar_profiles(x_positions, x_energies, y_positions, y_energies)
    radius = float(plan.radius_factor) * min(float(fit["r_x"]), float(fit["r_y"]))
    center = (float(fit["xc"]), float(fit["yc"]))
    aperture_window = _aperture_window(center, radius, physical_shape)
    fit_quality = {
        **fit,
        "radius_factor": float(plan.radius_factor),
        "selected_radius": float(radius),
        "tls_pass_through_required": True,
    }
    pupil = PupilProfile(
        pupil_profile_id=plan.pupil_profile_id,
        lcd_coordinate_convention="physical_mono_xy",
        lcd_display_index=int(plan.lcd_display_index),
        subpixel_axis=int(plan.subpixel_axis),
        lcd_physical_center=center,
        lcd_physical_radius=radius,
        aperture_window=aperture_window,
        fit_quality=fit_quality,
        source_raw_capture_file=plan.source_raw_capture_file,
        extra={
            "source_task": "scan_pupil_broadband",
            "camera_profile_id": plan.camera_profile_id,
            "illumination_mode": BROADBAND_PASSTHROUGH,
            "physical_shape": list(physical_shape),
            **dict(plan.extra),
        },
    )
    pupil.validate()
    return PupilScanReport(
        plan=plan,
        pupil_profile=pupil,
        x_positions=x_positions,
        x_energies=x_energies,
        y_positions=y_positions,
        y_energies=y_energies,
        fit_quality=fit_quality,
        tls_status=_tls_status_dict(tls),
    )


def _validate_broadband_camera_profile(
    plan: PupilScanPlan,
    camera_profile: CameraProfile,
) -> None:
    if camera_profile.camera_profile_id != plan.camera_profile_id:
        raise PupilScanError("plan camera_profile_id does not match camera_profile")
    if camera_profile.profile_family != BROADBAND_PASSTHROUGH:
        raise PupilScanError(
            "broadband pupil scan requires a broadband_passthrough CameraProfile"
        )
    if "pupil_scan_broadband" not in camera_profile.valid_for:
        raise PupilScanError("CameraProfile is not valid_for pupil_scan_broadband")
    if camera_profile.depends_on_pupil_profile_id is not None:
        raise PupilScanError(
            "broadband pupil scan CameraProfile must not depend on a PupilProfile"
        )
    if camera_profile.illumination.mode != BROADBAND_PASSTHROUGH:
        raise PupilScanError(
            "broadband pupil scan CameraProfile illumination must be pass-through"
        )


def solid_mask(physical_shape: tuple[int, int], code: int) -> np.ndarray:
    h, w = _shape(physical_shape)
    return np.full((h, w), _code(code), dtype=np.uint8)


def vertical_bar_mask(physical_shape: tuple[int, int], *, x0: int, width: int, bg_code: int, bar_code: int) -> np.ndarray:
    h, w = _shape(physical_shape)
    mask = solid_mask((h, w), bg_code)
    start = max(0, min(w, int(x0)))
    end = max(start, min(w, int(x0) + int(width)))
    mask[:, start:end] = _code(bar_code)
    return mask


def horizontal_bar_mask(physical_shape: tuple[int, int], *, y0: int, width: int, bg_code: int, bar_code: int) -> np.ndarray:
    h, w = _shape(physical_shape)
    mask = solid_mask((h, w), bg_code)
    start = max(0, min(h, int(y0)))
    end = max(start, min(h, int(y0) + int(width)))
    mask[start:end, :] = _code(bar_code)
    return mask


def circular_window_mask(physical_shape: tuple[int, int], *, center: tuple[float, float], radius: float, bg_code: int = 0, aperture_code: int = 255) -> np.ndarray:
    h, w = _shape(physical_shape)
    yy, xx = np.mgrid[:h, :w]
    inside = (xx - float(center[0])) ** 2 + (yy - float(center[1])) ** 2 <= float(radius) ** 2
    mask = solid_mask((h, w), bg_code)
    mask[inside] = _code(aperture_code)
    return mask


def solve_pupil_from_bar_profiles(
    x_positions: np.ndarray,
    x_energies: np.ndarray,
    y_positions: np.ndarray,
    y_energies: np.ndarray,
) -> dict[str, float]:
    fx = _fit_circle_profile(x_positions, x_energies)
    fy = _fit_circle_profile(y_positions, y_energies)
    return {
        "xc": float(fx["center"]),
        "yc": float(fy["center"]),
        "r_x": float(fx["radius"]),
        "r_y": float(fy["radius"]),
        "r_avg": float(0.5 * (fx["radius"] + fy["radius"])),
        "rms_x": float(fx["residual_rms"]),
        "rms_y": float(fy["residual_rms"]),
    }


def _scan_axis(
    axis: str,
    *,
    plan: PupilScanPlan,
    camera: PupilScanCamera,
    lcd: PupilScanLCD,
    bright_sum: float,
) -> tuple[np.ndarray, np.ndarray]:
    positions: list[float] = []
    energies: list[float] = []
    for start in _bar_starts(axis, plan):
        position = float(start) + 0.5 * float(plan.bar_width)
        if axis == "x":
            mask = vertical_bar_mask(plan.physical_shape, x0=start, width=plan.bar_width, bg_code=plan.bg_code, bar_code=plan.bar_code)
        else:
            mask = horizontal_bar_mask(plan.physical_shape, y0=start, width=plan.bar_width, bg_code=plan.bg_code, bar_code=plan.bar_code)
        frame = _capture_mask(camera, lcd, mask, f"pupil_scan_bar_{axis}_{start:04d}", plan.frames_per_capture)
        positions.append(position)
        energies.append(float(abs(bright_sum - np.sum(frame))))
    return np.asarray(positions, dtype=np.float64), np.asarray(energies, dtype=np.float64)


def _capture_mask(
    camera: PupilScanCamera,
    lcd: PupilScanLCD,
    mask: np.ndarray,
    mask_id: str,
    frames_per_capture: int,
) -> np.ndarray:
    lcd.show_physical_mask(mask, mask_id=mask_id)
    capture = camera.acquire_burst(int(frames_per_capture))
    return np.asarray(capture.frames_avg, dtype=np.float64)


def _bar_starts(axis: str, plan: PupilScanPlan) -> list[int]:
    h, w = plan.physical_shape
    if plan.scan_range_xyxy is None:
        start, end = (0, w) if axis == "x" else (0, h)
    else:
        x0, x1, y0, y1 = plan.scan_range_xyxy
        start, end = (x0, x1) if axis == "x" else (y0, y1)
    limit = w if axis == "x" else h
    step = max(1, int(plan.scan_step))
    return [max(0, min(limit - 1, value)) for value in range(int(start), int(end), step)] or [0]


def _fit_circle_profile(positions: np.ndarray, energies: np.ndarray) -> dict[str, float]:
    x = np.asarray(positions, dtype=np.float64)
    y = np.asarray(energies, dtype=np.float64)
    if x.ndim != 1 or y.ndim != 1 or x.size != y.size or x.size < 5:
        raise PupilScanError("bar profile fit requires at least 5 same-length samples")
    y = y - float(np.min(y))
    y[y < 0.0] = 0.0
    if float(np.max(y)) <= 0.0:
        raise PupilScanError("bar profile has no positive response")
    idx = np.where(y >= 0.4 * float(np.max(y)))[0]
    if idx.size < 3:
        raise PupilScanError("not enough high-response samples for pupil fit")
    xf = x[idx]
    yf = y[idx] ** 2
    design = np.vstack([xf ** 2, xf, np.ones_like(xf)]).T
    coef, *_ = np.linalg.lstsq(design, yf, rcond=None)
    qa, qb, qc = (float(v) for v in coef)
    if qa >= 0.0:
        raise PupilScanError("pupil profile fit failed: quadratic opens upward")
    center = -qb / (2.0 * qa)
    radius_sq = qc / (-qa) + center * center
    if radius_sq <= 0.0:
        raise PupilScanError("pupil profile fit produced invalid radius")
    pred = qa * xf ** 2 + qb * xf + qc
    return {
        "center": float(center),
        "radius": float(np.sqrt(radius_sq)),
        "residual_rms": float(np.sqrt(np.mean((yf - pred) ** 2))),
    }


def _aperture_window(center: tuple[float, float], radius: float, shape: tuple[int, int]) -> tuple[int, int, int, int]:
    h, w = _shape(shape)
    x0 = max(0, int(np.floor(float(center[0]) - float(radius))))
    y0 = max(0, int(np.floor(float(center[1]) - float(radius))))
    x1 = min(w, int(np.ceil(float(center[0]) + float(radius))))
    y1 = min(h, int(np.ceil(float(center[1]) + float(radius))))
    return (x0, y0, max(1, x1 - x0), max(1, y1 - y0))


def _tls_status_dict(tls: PupilScanTLS | None) -> dict[str, Any]:
    if tls is None:
        return {"connected": False, "target_wavelength_nm": 0.0, "pass_through_requested": False}
    status = tls.get_status()
    return {
        "connected": bool(_read_attr(status, "connected", True)),
        "current_wavelength_nm": _read_attr(status, "current_wavelength_nm", None),
        "target_wavelength_nm": _read_attr(status, "target_wavelength_nm", 0.0),
        "grating": _read_attr(status, "grating", None),
        "moving": bool(_read_attr(status, "moving", False)),
        "pass_through_requested": True,
    }


def _read_attr(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _shape(shape: tuple[int, int]) -> tuple[int, int]:
    if len(shape) != 2:
        raise PupilScanError("physical_shape must be [H,W]")
    h, w = int(shape[0]), int(shape[1])
    if h <= 0 or w <= 0:
        raise PupilScanError("physical_shape must be positive")
    return h, w


def _code(value: int) -> int:
    code = int(value)
    if code < 0 or code > 255:
        raise PupilScanError("mask code must be in [0,255]")
    return code


def _int_pair(value: Any, name: str) -> tuple[int, int]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise PupilScanError(f"{name} must be a pair")
    return (int(value[0]), int(value[1]))


def _optional_int_quad(value: Any) -> tuple[int, int, int, int] | None:
    if value is None:
        return None
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise PupilScanError("scan_range_xyxy must contain four integers")
    return (int(value[0]), int(value[1]), int(value[2]), int(value[3]))
