from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

import numpy as np


class ExposureSearchError(ValueError):
    pass


@dataclass(frozen=True)
class ExposureCandidate:
    exposure_us: float
    gain_db: float = 0.0

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ExposureCandidate":
        return cls(
            exposure_us=float(data["exposure_us"]),
            gain_db=float(data.get("gain_db", 0.0)),
        )

    def to_dict(self) -> dict[str, float]:
        return {
            "exposure_us": float(self.exposure_us),
            "gain_db": float(self.gain_db),
        }


@dataclass
class ExposureProbeResult:
    exposure_us: float
    gain_db: float
    peak_pixel_burst: float
    peak_pixel_avg: float
    peak_pixel_fraction_burst: float
    peak_margin_to_full_scale: float
    p_signal: float
    dynamic_range: float
    psf_safe: bool
    usable_signal: bool
    unsafe_reason: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "exposure_us": float(self.exposure_us),
            "gain_db": float(self.gain_db),
            "peak_pixel_burst": float(self.peak_pixel_burst),
            "peak_pixel_avg": float(self.peak_pixel_avg),
            "peak_pixel_fraction_burst": float(self.peak_pixel_fraction_burst),
            "peak_margin_to_full_scale": float(self.peak_margin_to_full_scale),
            "p_signal": float(self.p_signal),
            "dynamic_range": float(self.dynamic_range),
            "psf_safe": bool(self.psf_safe),
            "usable_signal": bool(self.usable_signal),
            "unsafe_reason": self.unsafe_reason,
            "metadata": dict(self.metadata),
        }


class ExposureSearchCamera(Protocol):
    def apply_camera_params(self, exposure_us=None, gain_db=None):
        ...

    def acquire_burst(self, k: int):
        ...


def evaluate_exposure_candidates(
    camera: ExposureSearchCamera,
    candidates: list[ExposureCandidate],
    *,
    frames_per_capture: int,
    full_scale: float,
    valid_pixel_mask: np.ndarray | None = None,
    signal_percentile: float = 99.0,
    min_signal_fraction: float = 0.10,
    min_dynamic_range_fraction: float = 0.08,
) -> list[ExposureProbeResult]:
    if not candidates:
        raise ExposureSearchError("at least one exposure candidate is required")
    if int(frames_per_capture) < 1:
        raise ExposureSearchError("frames_per_capture must be >= 1")
    if float(full_scale) <= 0:
        raise ExposureSearchError("full_scale must be positive")

    rows: list[ExposureProbeResult] = []
    for candidate in candidates:
        if candidate.exposure_us <= 0:
            raise ExposureSearchError("candidate exposure_us must be positive")
        camera.apply_camera_params(
            exposure_us=float(candidate.exposure_us),
            gain_db=float(candidate.gain_db),
        )
        capture = camera.acquire_burst(int(frames_per_capture))
        burst = np.asarray(capture.burst, dtype=np.float64)
        avg = np.asarray(capture.frames_avg, dtype=np.float64)
        rows.append(
            evaluate_capture_safety(
                burst=burst,
                avg_frame=avg,
                exposure_us=float(candidate.exposure_us),
                gain_db=float(candidate.gain_db),
                full_scale=float(full_scale),
                valid_pixel_mask=valid_pixel_mask,
                signal_percentile=signal_percentile,
                min_signal_fraction=min_signal_fraction,
                min_dynamic_range_fraction=min_dynamic_range_fraction,
                metadata=getattr(capture, "metadata", {}) or {},
            )
        )
    return rows


def evaluate_capture_safety(
    *,
    burst: np.ndarray,
    avg_frame: np.ndarray,
    exposure_us: float,
    gain_db: float,
    full_scale: float,
    valid_pixel_mask: np.ndarray | None = None,
    signal_percentile: float = 99.0,
    min_signal_fraction: float = 0.10,
    min_dynamic_range_fraction: float = 0.08,
    metadata: dict[str, Any] | None = None,
) -> ExposureProbeResult:
    burst_arr = np.asarray(burst, dtype=np.float64)
    avg = np.asarray(avg_frame, dtype=np.float64)
    if burst_arr.ndim != 3:
        raise ExposureSearchError(f"burst must be [K,H,W], got {burst_arr.shape}")
    if avg.shape != burst_arr.shape[-2:]:
        raise ExposureSearchError("avg_frame shape must match burst frame shape")

    mask = _valid_mask(avg.shape, valid_pixel_mask)
    valid_burst = burst_arr[:, mask]
    valid_avg = avg[mask]
    finite = bool(np.isfinite(valid_burst).all())
    peak_burst = float(np.max(valid_burst)) if finite else float("inf")
    peak_avg = float(np.max(valid_avg)) if valid_avg.size else 0.0
    psf_safe = bool(finite and peak_burst < float(full_scale))
    unsafe_reason = None
    if not finite:
        unsafe_reason = "non_finite_pixel_in_valid_domain"
    elif peak_burst >= float(full_scale):
        unsafe_reason = "peak_pixel_at_or_above_full_scale_in_valid_domain"

    p_signal = float(np.percentile(valid_avg, float(signal_percentile)))
    p1 = float(np.percentile(valid_avg, 1.0))
    dynamic_range = float(p_signal - p1)
    usable = bool(
        p_signal > float(full_scale) * float(min_signal_fraction)
        and dynamic_range > float(full_scale) * float(min_dynamic_range_fraction)
    )
    return ExposureProbeResult(
        exposure_us=float(exposure_us),
        gain_db=float(gain_db),
        peak_pixel_burst=peak_burst,
        peak_pixel_avg=peak_avg,
        peak_pixel_fraction_burst=peak_burst / float(full_scale),
        peak_margin_to_full_scale=float(full_scale) - peak_burst,
        p_signal=p_signal,
        dynamic_range=dynamic_range,
        psf_safe=psf_safe,
        usable_signal=usable,
        unsafe_reason=unsafe_reason,
        metadata=dict(metadata or {}),
    )


def select_recommended_probe(rows: list[ExposureProbeResult]) -> ExposureProbeResult:
    safe = [row for row in rows if row.psf_safe]
    if not safe:
        raise ExposureSearchError("no PSF-safe exposure candidate was found")
    usable = [row for row in safe if row.usable_signal]
    pool = usable or safe
    # Prefer lower gain, then strongest usable signal without saturation.
    return sorted(pool, key=lambda r: (float(r.gain_db), -float(r.p_signal), -float(r.exposure_us)))[0]


def _valid_mask(shape: tuple[int, int], valid_pixel_mask: np.ndarray | None) -> np.ndarray:
    if valid_pixel_mask is None:
        return np.ones(shape, dtype=bool)
    mask = np.asarray(valid_pixel_mask, dtype=bool)
    if mask.shape != shape:
        raise ExposureSearchError(f"valid_pixel_mask shape {mask.shape} does not match {shape}")
    if not np.any(mask):
        raise ExposureSearchError("valid_pixel_mask leaves zero valid pixels")
    return mask
