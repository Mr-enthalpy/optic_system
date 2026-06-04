from __future__ import annotations

import json
import time
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


@dataclass(frozen=True)
class EllipseOverlapFit:
    semi_major: float
    semi_minor: float
    scale: float
    r_squared: float
    pearson: float
    rmse: float
    predicted: np.ndarray
    adjusted_energy: np.ndarray


@dataclass
class PupilScanPlan:
    pupil_profile_id: str
    camera_profile_id: str
    physical_shape: tuple[int, int]
    lcd_display_index: int
    subpixel_axis: int
    frames_per_capture: int = 5
    lcd_settle_ms: float = 20.0
    bar_width: int = 16
    scan_step: int = 8
    # Conventional image/detection order: x0, y0, x1, y1 in LCD physical pixels.
    scan_range_xyxy: tuple[int, int, int, int] | None = None
    bg_code: int = 255
    bar_code: int = 0
    radius_scan_min_factor: float = 0.0
    radius_scan_max_factor: float = 2.0
    radius_scan_steps: int = 80
    radius_scan_bg_code: int = 0
    radius_scan_aperture_code: int = 255
    radius_scan_frames_per_capture: int | None = None
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
            lcd_settle_ms=float(data.get("lcd_settle_ms", 20.0)),
            bar_width=int(data.get("bar_width", 16)),
            scan_step=int(data.get("scan_step", 8)),
            scan_range_xyxy=_optional_int_quad(data.get("scan_range_xyxy")),
            bg_code=int(data.get("bg_code", 255)),
            bar_code=int(data.get("bar_code", 0)),
            radius_scan_min_factor=float(data.get("radius_scan_min_factor", 0.0)),
            radius_scan_max_factor=float(data.get("radius_scan_max_factor", 2.0)),
            radius_scan_steps=int(data.get("radius_scan_steps", 80)),
            radius_scan_bg_code=int(data.get("radius_scan_bg_code", 0)),
            radius_scan_aperture_code=int(data.get("radius_scan_aperture_code", 255)),
            radius_scan_frames_per_capture=(
                int(data["radius_scan_frames_per_capture"])
                if data.get("radius_scan_frames_per_capture") is not None else None
            ),
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
    radii: np.ndarray
    radius_energies: np.ndarray
    radius_fit_predicted: np.ndarray
    radius_fit_adjusted_energy: np.ndarray
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
            "radii": self.radii.astype(float).tolist(),
            "radius_energies": self.radius_energies.astype(float).tolist(),
            "radius_fit_predicted": self.radius_fit_predicted.astype(float).tolist(),
            "radius_fit_adjusted_energy": self.radius_fit_adjusted_energy.astype(float).tolist(),
            "fit_quality": dict(self.fit_quality),
            "tls_status": dict(self.tls_status),
            "plan": {
                "pupil_profile_id": self.plan.pupil_profile_id,
                "camera_profile_id": self.plan.camera_profile_id,
                "physical_shape": list(self.plan.physical_shape),
                "lcd_display_index": int(self.plan.lcd_display_index),
                "subpixel_axis": int(self.plan.subpixel_axis),
                "frames_per_capture": int(self.plan.frames_per_capture),
                "lcd_settle_ms": float(self.plan.lcd_settle_ms),
                "bar_width": int(self.plan.bar_width),
                "scan_step": int(self.plan.scan_step),
                "scan_range_xyxy": (
                    list(self.plan.scan_range_xyxy)
                    if self.plan.scan_range_xyxy is not None else None
                ),
                "scan_range_xyxy_convention": "x0,y0,x1,y1",
                "radius_scan_min_factor": float(self.plan.radius_scan_min_factor),
                "radius_scan_max_factor": float(self.plan.radius_scan_max_factor),
                "radius_scan_steps": int(self.plan.radius_scan_steps),
                "radius_scan_bg_code": int(self.plan.radius_scan_bg_code),
                "radius_scan_aperture_code": int(self.plan.radius_scan_aperture_code),
                "radius_scan_frames_per_capture": self.plan.radius_scan_frames_per_capture,
                "radius_factor": float(self.plan.radius_factor),
                "radius_factor_reference": "ellipse_semi_minor",
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
    bright = _capture_mask(camera, lcd, solid_mask(physical_shape, plan.bg_code), "pupil_scan_bright", plan.frames_per_capture, plan.lcd_settle_ms)
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
    center = (float(fit["xc"]), float(fit["yc"]))
    dark = _capture_mask(
        camera,
        lcd,
        solid_mask(physical_shape, plan.radius_scan_bg_code),
        "pupil_scan_dark",
        plan.frames_per_capture,
        plan.lcd_settle_ms,
    )
    dark_sum = float(np.sum(dark))
    radii, radius_energies = _run_radius_scan(
        plan=plan,
        camera=camera,
        lcd=lcd,
        center=center,
        initial_radius=float(fit["r_avg"]),
        dark_sum=dark_sum,
    )
    ellipse = estimate_ellipse_parameters(radius_energies, radii)
    radius = float(plan.radius_factor) * float(ellipse.semi_minor)
    aperture_window = _aperture_window(center, radius, physical_shape)
    fit_quality = {
        **fit,
        "radius_factor": float(plan.radius_factor),
        "radius_factor_reference": "ellipse_semi_minor",
        "selected_radius": float(radius),
        "ellipse_semi_major": float(ellipse.semi_major),
        "ellipse_semi_minor": float(ellipse.semi_minor),
        "ellipse_scale": float(ellipse.scale),
        "ellipse_r_squared": float(ellipse.r_squared),
        "ellipse_pearson": float(ellipse.pearson),
        "ellipse_rmse": float(ellipse.rmse),
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
        radii=radii,
        radius_energies=radius_energies,
        radius_fit_predicted=ellipse.predicted,
        radius_fit_adjusted_energy=ellipse.adjusted_energy,
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


def ellipse_circle_overlap_area(r_values: np.ndarray, semi_major: float, semi_minor: float) -> np.ndarray:
    r = np.asarray(r_values, dtype=np.float64)
    if semi_major <= 0.0 or semi_minor <= 0.0:
        raise PupilScanError("ellipse axes must be positive")
    a = float(max(semi_major, semi_minor))
    b = float(min(semi_major, semi_minor))
    result = np.zeros_like(r, dtype=np.float64)

    small = r <= b
    large = r >= a
    mid = ~(small | large)
    result[small] = np.pi * r[small] ** 2
    result[large] = np.pi * a * b
    if np.any(mid):
        rm = r[mid]
        denom = max(a ** 2 - b ** 2, 1e-12)
        x0 = a * np.sqrt(np.clip((rm ** 2 - b ** 2) / denom, 0.0, 1.0))
        y0 = b * np.sqrt(np.clip((a ** 2 - rm ** 2) / denom, 0.0, 1.0))
        x0_over_r = np.clip(x0 / np.maximum(rm, 1e-12), -1.0, 1.0)
        x0_over_a = np.clip(x0 / max(a, 1e-12), -1.0, 1.0)
        term_circle = (
            np.pi * rm ** 2
            - 2.0 * x0 * y0
            - 2.0 * rm ** 2 * np.arcsin(x0_over_r)
        )
        term_ellipse = 2.0 * a * b * (
            np.arcsin(x0_over_a)
            + x0_over_a * np.sqrt(np.clip(1.0 - x0_over_a ** 2, 0.0, 1.0))
        )
        result[mid] = term_circle + term_ellipse
    return result


def fit_radius_overlap_function(
    r_values: np.ndarray,
    scale: float,
    semi_major: float,
    semi_minor: float,
) -> np.ndarray:
    return float(scale) * ellipse_circle_overlap_area(r_values, semi_major, semi_minor)


def estimate_ellipse_parameters(
    energies: np.ndarray,
    r_values: np.ndarray,
    *,
    initial_guess: tuple[float, float, float] | None = None,
) -> EllipseOverlapFit:
    r = np.asarray(r_values, dtype=np.float64)
    y_raw = np.asarray(energies, dtype=np.float64)
    if r.ndim != 1 or y_raw.ndim != 1 or r.size != y_raw.size:
        raise PupilScanError("r_values and energies must be same-length 1D arrays")
    if r.size < 8:
        raise PupilScanError("at least 8 radius samples are required")
    y = y_raw - float(np.min(y_raw))
    y[y < 0.0] = 0.0
    if float(np.max(y)) <= 0.0:
        raise PupilScanError("radius scan has no positive response")

    if initial_guess is None:
        a0, b0 = _estimate_axis_initial_values(r, y)
    else:
        _scale0, a0, b0 = (float(v) for v in initial_guess)
    a0, b0 = max(a0, b0), min(a0, b0)

    best = _grid_fit_ellipse_axes(r, y, a0=a0, b0=b0)
    best = _coordinate_refine_ellipse_axes(r, y, best)
    scale, a, b, _sse = best
    predicted = fit_radius_overlap_function(r, scale, a, b)
    residual = y - predicted
    rmse = float(np.sqrt(np.mean(residual ** 2)))
    total = float(np.sum((y - np.mean(y)) ** 2))
    r_squared = 1.0 - float(np.sum(residual ** 2)) / total if total > 0.0 else 0.0
    return EllipseOverlapFit(
        semi_major=float(max(a, b)),
        semi_minor=float(min(a, b)),
        scale=float(scale),
        r_squared=float(r_squared),
        pearson=_pearson(y, predicted),
        rmse=rmse,
        predicted=predicted.astype(np.float64),
        adjusted_energy=y.astype(np.float64),
    )


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
        frame = _capture_mask(camera, lcd, mask, f"pupil_scan_bar_{axis}_{start:04d}", plan.frames_per_capture, plan.lcd_settle_ms)
        positions.append(position)
        energies.append(float(abs(bright_sum - np.sum(frame))))
    return np.asarray(positions, dtype=np.float64), np.asarray(energies, dtype=np.float64)


def _run_radius_scan(
    *,
    plan: PupilScanPlan,
    camera: PupilScanCamera,
    lcd: PupilScanLCD,
    center: tuple[float, float],
    initial_radius: float,
    dark_sum: float,
) -> tuple[np.ndarray, np.ndarray]:
    if int(plan.radius_scan_steps) < 8:
        raise PupilScanError("radius_scan_steps must be >= 8 for ellipse fitting")
    r_min = float(plan.radius_scan_min_factor) * float(initial_radius)
    r_max = float(plan.radius_scan_max_factor) * float(initial_radius)
    if r_max <= r_min:
        raise PupilScanError("radius scan max radius must be greater than min radius")
    radii = np.linspace(r_min, r_max, int(plan.radius_scan_steps), dtype=np.float64)
    frames_per_capture = (
        int(plan.radius_scan_frames_per_capture)
        if plan.radius_scan_frames_per_capture is not None
        else int(plan.frames_per_capture)
    )
    energies: list[float] = []
    for index, radius in enumerate(radii):
        mask = circular_window_mask(
            plan.physical_shape,
            center=center,
            radius=float(radius),
            bg_code=plan.radius_scan_bg_code,
            aperture_code=plan.radius_scan_aperture_code,
        )
        frame = _capture_mask(
            camera,
            lcd,
            mask,
            f"pupil_scan_radius_{index:04d}",
            frames_per_capture,
            plan.lcd_settle_ms,
        )
        energies.append(float(abs(np.sum(frame) - dark_sum)))
    return radii, np.asarray(energies, dtype=np.float64)


def _capture_mask(
    camera: PupilScanCamera,
    lcd: PupilScanLCD,
    mask: np.ndarray,
    mask_id: str,
    frames_per_capture: int,
    lcd_settle_ms: float,
) -> np.ndarray:
    lcd.show_physical_mask(mask, mask_id=mask_id)
    _settle_lcd(lcd_settle_ms)
    capture = camera.acquire_burst(int(frames_per_capture))
    return np.asarray(capture.frames_avg, dtype=np.float64)


def _bar_starts(axis: str, plan: PupilScanPlan) -> list[int]:
    h, w = plan.physical_shape
    if plan.scan_range_xyxy is None:
        start, end = (0, w) if axis == "x" else (0, h)
    else:
        x0, y0, x1, y1 = plan.scan_range_xyxy
        start, end = (x0, x1) if axis == "x" else (y0, y1)
    limit = w if axis == "x" else h
    step = max(1, int(plan.scan_step))
    return [max(0, min(limit - 1, value)) for value in range(int(start), int(end), step)] or [0]


def _estimate_axis_initial_values(r: np.ndarray, y: np.ndarray) -> tuple[float, float]:
    y_norm = y / max(float(np.max(y)), 1e-12)
    above95 = np.where(y_norm >= 0.95)[0]
    a0 = float(r[above95[0]]) if above95.size else float(np.max(r) * 0.8)
    above50 = np.where(y_norm >= 0.50)[0]
    b0 = float(r[above50[0]]) if above50.size else float(a0 * 0.6)
    r_positive = r[r > 0.0]
    min_r = float(np.min(r_positive)) if r_positive.size else 1.0
    a0 = float(np.clip(a0, min_r * 2.0, max(float(np.max(r)), min_r * 2.0)))
    b0 = float(np.clip(b0, min_r, a0 * 0.95))
    return a0, b0


def _grid_fit_ellipse_axes(
    r: np.ndarray,
    y: np.ndarray,
    *,
    a0: float,
    b0: float,
) -> tuple[float, float, float, float]:
    r_max = max(float(np.max(r)), 1.0)
    r_positive = r[r > 0.0]
    min_positive = float(np.min(r_positive)) if r_positive.size else 1.0
    a_min = max(min_positive, float(a0) * 0.55)
    a_max = min(r_max * 1.05, max(float(a0) * 1.45, a_min * 1.2))
    if a_max <= a_min:
        a_max = r_max
    best: tuple[float, float, float, float] | None = None
    for a in np.linspace(a_min, a_max, 44):
        b_min = max(1.0, min(float(b0) * 0.45, float(a) * 0.2))
        b_max = min(float(a) * 0.98, max(float(b0) * 1.55, float(a) * 0.95))
        if b_max <= b_min:
            b_max = float(a) * 0.95
        for b in np.linspace(b_min, b_max, 44):
            candidate = _fit_scale_for_axes(r, y, float(a), float(b))
            if best is None or candidate[3] < best[3]:
                best = candidate
    if best is None:
        raise PupilScanError("ellipse grid fit failed")
    return best


def _coordinate_refine_ellipse_axes(
    r: np.ndarray,
    y: np.ndarray,
    best: tuple[float, float, float, float],
) -> tuple[float, float, float, float]:
    _scale, a, b, _sse = best
    step_a = max(float(a) * 0.12, 1.0)
    step_b = max(float(b) * 0.12, 1.0)
    current = best
    for _ in range(40):
        candidates = []
        for da in (-step_a, 0.0, step_a):
            for db in (-step_b, 0.0, step_b):
                ca = max(1.0, current[1] + da)
                cb = max(1.0, current[2] + db)
                ca, cb = max(ca, cb), min(ca, cb)
                if cb >= ca:
                    cb = ca * 0.95
                candidates.append(_fit_scale_for_axes(r, y, ca, cb))
        candidate = min(candidates, key=lambda item: item[3])
        if candidate[3] < current[3]:
            current = candidate
            continue
        step_a *= 0.5
        step_b *= 0.5
        if step_a < 1e-3 and step_b < 1e-3:
            break
    return current


def _fit_scale_for_axes(
    r: np.ndarray,
    y: np.ndarray,
    semi_major: float,
    semi_minor: float,
) -> tuple[float, float, float, float]:
    area = ellipse_circle_overlap_area(r, semi_major, semi_minor)
    denom = float(np.dot(area, area))
    scale = float(np.dot(y, area) / denom) if denom > 0.0 else 0.0
    if scale < 0.0:
        scale = 0.0
    pred = scale * area
    sse = float(np.sum((y - pred) ** 2))
    return scale, float(max(semi_major, semi_minor)), float(min(semi_major, semi_minor)), sse


def _pearson(a: np.ndarray, b: np.ndarray) -> float:
    if a.size < 2:
        return 0.0
    aa = a - float(np.mean(a))
    bb = b - float(np.mean(b))
    denom = float(np.sqrt(np.sum(aa ** 2) * np.sum(bb ** 2)))
    if denom <= 0.0:
        return 0.0
    return float(np.sum(aa * bb) / denom)


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


def _settle_lcd(settle_ms: float) -> None:
    if float(settle_ms) < 20.0:
        raise PupilScanError("lcd_settle_ms must be at least 20 ms")
    time.sleep(float(settle_ms) / 1000.0)


def _int_pair(value: Any, name: str) -> tuple[int, int]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise PupilScanError(f"{name} must be a pair")
    return (int(value[0]), int(value[1]))


def _optional_int_quad(value: Any) -> tuple[int, int, int, int] | None:
    if value is None:
        return None
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise PupilScanError("scan_range_xyxy must contain four integers: x0, y0, x1, y1")
    return (int(value[0]), int(value[1]), int(value[2]), int(value[3]))
