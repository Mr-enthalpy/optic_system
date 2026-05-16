from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np


class PupilGeometryFitError(RuntimeError):
    pass


@dataclass(frozen=True)
class CircleProfileFit:
    center: float
    radius: float
    scale: float
    coefficients: tuple[float, float, float]
    residual_rms: float


@dataclass(frozen=True)
class EllipseOverlapFit:
    a: float
    b: float
    k: float
    r_squared: float
    pearson: float
    rmse: float
    predicted: np.ndarray
    adjusted_energy: np.ndarray


def fit_circle_from_profile(
    positions: np.ndarray,
    energies: np.ndarray,
    *,
    smooth_k: int = 5,
    use_top: float = 0.6,
) -> CircleProfileFit:
    x = np.asarray(positions, dtype=np.float64)
    f = np.asarray(energies, dtype=np.float64)
    if x.ndim != 1 or f.ndim != 1 or x.size != f.size:
        raise PupilGeometryFitError("positions and energies must be same-length 1D arrays")
    if x.size < 5:
        raise PupilGeometryFitError("at least 5 profile samples are required")

    n = f.size
    edge_n = max(1, int(0.1 * n))
    baseline = 0.5 * (float(np.min(f[:edge_n])) + float(np.min(f[-edge_n:])))
    f = f - baseline
    f[f < 0.0] = 0.0

    if smooth_k > 1:
        smooth_k = max(1, int(smooth_k))
        pad = np.pad(f, (smooth_k // 2, smooth_k - 1 - smooth_k // 2), mode="edge")
        cumsum = np.cumsum(pad, dtype=np.float64)
        f = (cumsum[smooth_k:] - cumsum[:-smooth_k]) / float(smooth_k)

    peak = float(np.max(f))
    if peak <= 0.0:
        raise PupilGeometryFitError("profile has no positive response")
    threshold = (1.0 - float(use_top)) * peak
    idx = np.where(f >= threshold)[0]
    if idx.size < 3:
        raise PupilGeometryFitError("not enough high-response samples for circle fit")

    x_fit = x[idx]
    y_fit = f[idx] ** 2
    design = np.vstack([x_fit ** 2, x_fit, np.ones_like(x_fit)]).T
    coef, *_ = np.linalg.lstsq(design, y_fit, rcond=None)
    qa, qb, qc = (float(v) for v in coef)
    if qa >= 0.0:
        raise PupilGeometryFitError("circle profile fit failed: quadratic opens upward")

    center = -qb / (2.0 * qa)
    scale = float(np.sqrt(-qa))
    radius_sq = qc / (-qa) + center * center
    if radius_sq <= 0.0 or not np.isfinite(radius_sq):
        raise PupilGeometryFitError("circle profile fit produced invalid radius")
    radius = float(np.sqrt(radius_sq))
    predicted = qa * x_fit ** 2 + qb * x_fit + qc
    residual_rms = float(np.sqrt(np.mean((y_fit - predicted) ** 2)))
    return CircleProfileFit(
        center=float(center),
        radius=radius,
        scale=scale,
        coefficients=(qa, qb, qc),
        residual_rms=residual_rms,
    )


def solve_aperture_from_profiles(
    pos_x: np.ndarray,
    energy_x: np.ndarray,
    pos_y: np.ndarray,
    energy_y: np.ndarray,
    *,
    smooth_k: int = 5,
    use_top: float = 0.6,
) -> dict[str, float]:
    fit_x = fit_circle_from_profile(pos_x, energy_x, smooth_k=smooth_k, use_top=use_top)
    fit_y = fit_circle_from_profile(pos_y, energy_y, smooth_k=smooth_k, use_top=use_top)
    r_avg = 0.5 * (fit_x.radius + fit_y.radius)
    return {
        "xc": float(fit_x.center),
        "yc": float(fit_y.center),
        "r_x": float(fit_x.radius),
        "r_y": float(fit_y.radius),
        "r_avg": float(r_avg),
        "s_x": float(fit_x.scale),
        "s_y": float(fit_y.scale),
        "rms_x": float(fit_x.residual_rms),
        "rms_y": float(fit_y.residual_rms),
        "x_quad_a": float(fit_x.coefficients[0]),
        "x_quad_b": float(fit_x.coefficients[1]),
        "x_quad_c": float(fit_x.coefficients[2]),
        "y_quad_a": float(fit_y.coefficients[0]),
        "y_quad_b": float(fit_y.coefficients[1]),
        "y_quad_c": float(fit_y.coefficients[2]),
    }


def ellipse_circle_overlap_area(r_values: np.ndarray, a: float, b: float) -> np.ndarray:
    r = np.asarray(r_values, dtype=np.float64)
    if a <= 0.0 or b <= 0.0:
        raise ValueError("ellipse axes must be positive")
    a_f = float(max(a, b))
    b_f = float(min(a, b))
    result = np.zeros_like(r, dtype=np.float64)

    small = r <= b_f
    large = r >= a_f
    mid = ~(small | large)
    result[small] = np.pi * r[small] ** 2
    result[large] = np.pi * a_f * b_f
    if np.any(mid):
        rm = r[mid]
        denom = max(a_f ** 2 - b_f ** 2, 1e-12)
        x0 = a_f * np.sqrt(np.clip((rm ** 2 - b_f ** 2) / denom, 0.0, 1.0))
        y0 = b_f * np.sqrt(np.clip((a_f ** 2 - rm ** 2) / denom, 0.0, 1.0))
        x0_over_r = np.clip(x0 / np.maximum(rm, 1e-12), -1.0, 1.0)
        x0_over_a = np.clip(x0 / max(a_f, 1e-12), -1.0, 1.0)
        term_circle = (
            np.pi * rm ** 2
            - 2.0 * x0 * y0
            - 2.0 * rm ** 2 * np.arcsin(x0_over_r)
        )
        term_ellipse = 2.0 * a_f * b_f * (
            np.arcsin(x0_over_a)
            + x0_over_a * np.sqrt(np.clip(1.0 - x0_over_a ** 2, 0.0, 1.0))
        )
        result[mid] = term_circle + term_ellipse
    return result


def fit_function(r_values: np.ndarray, k: float, a: float, b: float) -> np.ndarray:
    return float(k) * ellipse_circle_overlap_area(r_values, a, b)


def estimate_ellipse_parameters(
    energies: np.ndarray,
    r_values: np.ndarray,
    *,
    initial_guess: tuple[float, float, float] | None = None,
) -> EllipseOverlapFit:
    r = np.asarray(r_values, dtype=np.float64)
    y_raw = np.asarray(energies, dtype=np.float64)
    if r.ndim != 1 or y_raw.ndim != 1 or r.size != y_raw.size:
        raise PupilGeometryFitError("r_values and energies must be same-length 1D arrays")
    if r.size < 8:
        raise PupilGeometryFitError("at least 8 radius samples are required")
    y = y_raw - float(np.min(y_raw))
    y[y < 0.0] = 0.0
    if float(np.max(y)) <= 0.0:
        raise PupilGeometryFitError("radius scan has no positive response")

    if initial_guess is None:
        a0, b0 = _estimate_axis_initial_values(r, y)
        area0 = max(np.pi * a0 * b0, 1e-9)
        k0 = float(np.max(y) / area0)
    else:
        k0, a0, b0 = (float(v) for v in initial_guess)
    a0, b0 = max(a0, b0), min(a0, b0)

    best = _grid_fit(r, y, a0=a0, b0=b0)
    best = _coordinate_refine(r, y, best)
    k, a, b, _sse = best
    predicted = fit_function(r, k, a, b)
    residual = y - predicted
    rmse = float(np.sqrt(np.mean(residual ** 2)))
    total = float(np.sum((y - np.mean(y)) ** 2))
    r_squared = 1.0 - float(np.sum(residual ** 2)) / total if total > 0.0 else 0.0
    pearson = _pearson(y, predicted)
    return EllipseOverlapFit(
        a=float(max(a, b)),
        b=float(min(a, b)),
        k=float(k),
        r_squared=float(r_squared),
        pearson=float(pearson),
        rmse=rmse,
        predicted=predicted.astype(np.float64),
        adjusted_energy=y.astype(np.float64),
    )


def create_ellipse_mask(
    *,
    physical_shape: tuple[int, int],
    center: tuple[float, float],
    a: float,
    b: float,
    rotate_angle_deg: float = 0.0,
) -> np.ndarray:
    h, w = int(physical_shape[0]), int(physical_shape[1])
    xc, yc = float(center[0]), float(center[1])
    angle_rad = np.deg2rad(float(rotate_angle_deg))
    cos_a = float(np.cos(angle_rad))
    sin_a = float(np.sin(angle_rad))
    yy, xx = np.mgrid[:h, :w]
    x_centered = xx.astype(np.float64) - xc
    y_centered = yy.astype(np.float64) - yc
    x_rot = x_centered * cos_a + y_centered * sin_a
    y_rot = -x_centered * sin_a + y_centered * cos_a
    eq = (x_rot ** 2) / max(float(a) ** 2, 1e-12) + (y_rot ** 2) / max(float(b) ** 2, 1e-12)
    return (eq <= 1.0).astype(np.uint8)


def create_circular_window_mask(
    *,
    physical_shape: tuple[int, int],
    center: tuple[float, float],
    radius: float,
) -> np.ndarray:
    h, w = int(physical_shape[0]), int(physical_shape[1])
    xc, yc = float(center[0]), float(center[1])
    yy, xx = np.mgrid[:h, :w]
    return (((xx - xc) ** 2 + (yy - yc) ** 2) <= float(radius) ** 2).astype(np.uint8)


def _estimate_axis_initial_values(r: np.ndarray, y: np.ndarray) -> tuple[float, float]:
    y_norm = y / max(float(np.max(y)), 1e-12)
    above95 = np.where(y_norm >= 0.95)[0]
    a0 = float(r[above95[0]]) if above95.size else float(np.max(r) * 0.8)
    above50 = np.where(y_norm >= 0.50)[0]
    b0 = float(r[above50[0]]) if above50.size else float(a0 * 0.6)
    r_positive = r[r > 0]
    min_r = float(np.min(r_positive)) if r_positive.size else 1.0
    a0 = float(np.clip(a0, min_r * 2.0, max(float(np.max(r)), min_r * 2.0)))
    b0 = float(np.clip(b0, min_r, a0 * 0.95))
    return a0, b0


def _grid_fit(
    r: np.ndarray,
    y: np.ndarray,
    *,
    a0: float,
    b0: float,
) -> tuple[float, float, float, float]:
    r_max = max(float(np.max(r)), 1.0)
    a_min = max(float(np.min(r[r > 0])) if np.any(r > 0) else 1.0, a0 * 0.55)
    a_max = min(r_max * 1.05, max(a0 * 1.45, a_min * 1.2))
    if a_max <= a_min:
        a_max = r_max
    a_candidates = np.linspace(a_min, a_max, 44)
    best: tuple[float, float, float, float] | None = None
    for a in a_candidates:
        b_min = max(1.0, min(b0 * 0.45, a * 0.2))
        b_max = min(a * 0.98, max(b0 * 1.55, a * 0.95))
        if b_max <= b_min:
            b_max = a * 0.95
        for b in np.linspace(b_min, b_max, 44):
            candidate = _fit_k_for_axes(r, y, float(a), float(b))
            if best is None or candidate[3] < best[3]:
                best = candidate
    assert best is not None
    return best


def _coordinate_refine(
    r: np.ndarray,
    y: np.ndarray,
    best: tuple[float, float, float, float],
) -> tuple[float, float, float, float]:
    _k, a, b, _sse = best
    step_a = max(a * 0.12, 1.0)
    step_b = max(b * 0.12, 1.0)
    current = best
    for _ in range(40):
        improved = False
        candidates = []
        for da in (-step_a, 0.0, step_a):
            for db in (-step_b, 0.0, step_b):
                ca = max(1.0, current[1] + da)
                cb = max(1.0, current[2] + db)
                ca, cb = max(ca, cb), min(ca, cb)
                if cb >= ca:
                    cb = ca * 0.95
                candidates.append(_fit_k_for_axes(r, y, ca, cb))
        candidate = min(candidates, key=lambda item: item[3])
        if candidate[3] < current[3]:
            current = candidate
            improved = True
        if not improved:
            step_a *= 0.5
            step_b *= 0.5
            if step_a < 1e-3 and step_b < 1e-3:
                break
    return current


def _fit_k_for_axes(
    r: np.ndarray,
    y: np.ndarray,
    a: float,
    b: float,
) -> tuple[float, float, float, float]:
    area = ellipse_circle_overlap_area(r, a, b)
    denom = float(np.dot(area, area))
    k = float(np.dot(y, area) / denom) if denom > 0.0 else 0.0
    if k < 0.0:
        k = 0.0
    pred = k * area
    sse = float(np.sum((y - pred) ** 2))
    return k, float(max(a, b)), float(min(a, b)), sse


def _pearson(a: np.ndarray, b: np.ndarray) -> float:
    if a.size < 2:
        return 0.0
    aa = a - float(np.mean(a))
    bb = b - float(np.mean(b))
    denom = float(np.sqrt(np.sum(aa ** 2) * np.sum(bb ** 2)))
    if denom <= 0.0:
        return 0.0
    return float(np.sum(aa * bb) / denom)


def json_fit_summary(fit: EllipseOverlapFit) -> dict[str, Any]:
    return {
        "a": float(fit.a),
        "b": float(fit.b),
        "k": float(fit.k),
        "r_squared": float(fit.r_squared),
        "pearson": float(fit.pearson),
        "rmse": float(fit.rmse),
    }
