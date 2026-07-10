from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Protocol

import numpy as np

from tasks.valid_pixel_domain import (
    ValidPixelDomainError,
    resolve_valid_pixel_mask,
)


class ExposureSearchError(ValueError):
    pass


class ExposureLowerBoundUnsafeError(ExposureSearchError):
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


@dataclass(frozen=True)
class ExposureBinarySearchConfig:
    min_exposure_us: float
    max_exposure_us: float
    gain_db: float = 0.0
    iterations: int = 8
    safety_fraction: float = 0.95

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ExposureBinarySearchConfig":
        return cls(
            min_exposure_us=float(data["min_exposure_us"]),
            max_exposure_us=float(data["max_exposure_us"]),
            gain_db=float(data.get("gain_db", 0.0)),
            iterations=int(data.get("iterations", 8)),
            safety_fraction=float(data.get("safety_fraction", 0.95)),
        )

    @classmethod
    def from_candidates(
        cls,
        candidates: list[ExposureCandidate],
        *,
        iterations: int = 8,
        safety_fraction: float = 0.95,
    ) -> "ExposureBinarySearchConfig":
        if not candidates:
            raise ExposureSearchError("at least one exposure candidate is required")
        gains = {float(c.gain_db) for c in candidates}
        if len(gains) != 1:
            raise ExposureSearchError(
                "binary exposure search requires one fixed gain_db per search"
            )
        exposures = [float(c.exposure_us) for c in candidates]
        return cls(
            min_exposure_us=min(exposures),
            max_exposure_us=max(exposures),
            gain_db=float(candidates[0].gain_db),
            iterations=iterations,
            safety_fraction=safety_fraction,
        )

    def to_dict(self) -> dict[str, float | int]:
        return {
            "min_exposure_us": float(self.min_exposure_us),
            "max_exposure_us": float(self.max_exposure_us),
            "gain_db": float(self.gain_db),
            "iterations": int(self.iterations),
            "safety_fraction": float(self.safety_fraction),
        }


@dataclass(frozen=True)
class ExposureGainSearchConfig:
    min_exposure_us: float
    max_exposure_us: float
    gains_db: list[float]
    iterations: int = 8
    safety_fraction: float = 0.95
    camera_param_settle_ms: float = 300.0
    discard_frames_after_param_change: int = 80

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ExposureGainSearchConfig":
        gains = data.get("gains_db")
        if not isinstance(gains, list) or not gains:
            raise ExposureSearchError("gains_db must be a non-empty list")
        return cls(
            min_exposure_us=float(data["min_exposure_us"]),
            max_exposure_us=float(data["max_exposure_us"]),
            gains_db=[float(g) for g in gains],
            iterations=int(data.get("iterations", 8)),
            safety_fraction=float(data.get("safety_fraction", 0.95)),
            camera_param_settle_ms=float(data.get("camera_param_settle_ms", 300.0)),
            discard_frames_after_param_change=int(
                data.get("discard_frames_after_param_change", 80)
            ),
        )

    @classmethod
    def from_candidates(
        cls,
        candidates: list[ExposureCandidate],
        *,
        iterations: int = 8,
        safety_fraction: float = 0.95,
        camera_param_settle_ms: float = 300.0,
        discard_frames_after_param_change: int = 80,
    ) -> "ExposureGainSearchConfig":
        if not candidates:
            raise ExposureSearchError("at least one exposure candidate is required")
        exposures = [float(c.exposure_us) for c in candidates]
        gains = sorted({float(c.gain_db) for c in candidates})
        return cls(
            min_exposure_us=min(exposures),
            max_exposure_us=max(exposures),
            gains_db=gains,
            iterations=iterations,
            safety_fraction=safety_fraction,
            camera_param_settle_ms=camera_param_settle_ms,
            discard_frames_after_param_change=discard_frames_after_param_change,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "min_exposure_us": float(self.min_exposure_us),
            "max_exposure_us": float(self.max_exposure_us),
            "gains_db": [float(g) for g in self.gains_db],
            "iterations": int(self.iterations),
            "safety_fraction": float(self.safety_fraction),
            "camera_param_settle_ms": float(self.camera_param_settle_ms),
            "discard_frames_after_param_change": int(self.discard_frames_after_param_change),
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
    valid_pixel_domain: dict[str, Any] | None = None,
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
        rows.append(
            _probe_candidate(
                camera,
                candidate,
                frames_per_capture=frames_per_capture,
                full_scale=full_scale,
                valid_pixel_domain=valid_pixel_domain,
                valid_pixel_mask=valid_pixel_mask,
                signal_percentile=signal_percentile,
                min_signal_fraction=min_signal_fraction,
                min_dynamic_range_fraction=min_dynamic_range_fraction,
            )
        )
    return rows


def evaluate_gain_binary_search(
    camera: ExposureSearchCamera,
    config: ExposureGainSearchConfig,
    *,
    frames_per_capture: int,
    full_scale: float,
    valid_pixel_domain: dict[str, Any] | None = None,
    valid_pixel_mask: np.ndarray | None = None,
    signal_percentile: float = 99.0,
    min_signal_fraction: float = 0.10,
    min_dynamic_range_fraction: float = 0.08,
) -> list[ExposureProbeResult]:
    if not config.gains_db:
        raise ExposureSearchError("at least one gain is required")
    rows: list[ExposureProbeResult] = []
    configured_gains = [float(g) for g in config.gains_db]
    gains = sorted(configured_gains)
    for gain_index, gain_db in enumerate(gains):
        (
            min_exposure_us,
            max_exposure_us,
            exposure_bounds_source,
            exposure_bounds_metadata,
        ) = _resolve_exposure_bounds_us(
            camera,
            config,
        )
        try:
            gain_rows = evaluate_exposure_binary_search(
                camera,
                ExposureBinarySearchConfig(
                    min_exposure_us=min_exposure_us,
                    max_exposure_us=max_exposure_us,
                    gain_db=float(gain_db),
                    iterations=int(config.iterations),
                    safety_fraction=float(config.safety_fraction),
                ),
                frames_per_capture=frames_per_capture,
                full_scale=full_scale,
                valid_pixel_domain=valid_pixel_domain,
                valid_pixel_mask=valid_pixel_mask,
                signal_percentile=signal_percentile,
                min_signal_fraction=min_signal_fraction,
                min_dynamic_range_fraction=min_dynamic_range_fraction,
                camera_param_settle_ms=float(config.camera_param_settle_ms),
                discard_frames_after_param_change=int(config.discard_frames_after_param_change),
            )
        except ExposureLowerBoundUnsafeError:
            if gain_index == 0:
                raise
            for row in rows:
                row.metadata["gain_search_stopped_after_gain_db"] = float(gain_db)
                row.metadata["gain_search_stop_reason"] = "min_exposure_unsafe_at_higher_gain"
            break
        for row in gain_rows:
            row.metadata["gain_search_method"] = "gain_outer_binary_exposure_inner"
            row.metadata["configured_gains_db"] = list(configured_gains)
            row.metadata["sorted_gains_db"] = list(gains)
            row.metadata["gain_iteration_order"] = "ascending"
            row.metadata["gain_index"] = int(gain_index)
            row.metadata["exposure_bounds_source"] = exposure_bounds_source
            row.metadata["min_exposure_source"] = exposure_bounds_source
            row.metadata["max_exposure_source"] = exposure_bounds_source
            row.metadata.update(exposure_bounds_metadata)
        rows.extend(gain_rows)
    if not rows:
        raise ExposureSearchError("no gain/exposure probes were completed")
    return rows


def evaluate_exposure_binary_search(
    camera: ExposureSearchCamera,
    config: ExposureBinarySearchConfig,
    *,
    frames_per_capture: int,
    full_scale: float,
    valid_pixel_domain: dict[str, Any] | None = None,
    valid_pixel_mask: np.ndarray | None = None,
    signal_percentile: float = 99.0,
    min_signal_fraction: float = 0.10,
    min_dynamic_range_fraction: float = 0.08,
    camera_param_settle_ms: float = 300.0,
    discard_frames_after_param_change: int = 80,
) -> list[ExposureProbeResult]:
    if int(frames_per_capture) < 1:
        raise ExposureSearchError("frames_per_capture must be >= 1")
    if float(full_scale) <= 0:
        raise ExposureSearchError("full_scale must be positive")
    if config.min_exposure_us <= 0 or config.max_exposure_us <= 0:
        raise ExposureSearchError("binary exposure bounds must be positive")
    if config.max_exposure_us < config.min_exposure_us:
        raise ExposureSearchError("max_exposure_us must be >= min_exposure_us")
    if int(config.iterations) < 1:
        raise ExposureSearchError("binary exposure iterations must be >= 1")
    if not (0.0 < float(config.safety_fraction) <= 1.0):
        raise ExposureSearchError("safety_fraction must be in (0,1]")

    rows: list[ExposureProbeResult] = []
    safety_limit = float(full_scale) * float(config.safety_fraction)

    def probe(exposure_us: float, stage: str) -> ExposureProbeResult:
        candidate = ExposureCandidate(exposure_us=float(exposure_us), gain_db=float(config.gain_db))
        row = _probe_candidate(
            camera,
            candidate,
            frames_per_capture=frames_per_capture,
            full_scale=full_scale,
            valid_pixel_domain=valid_pixel_domain,
            valid_pixel_mask=valid_pixel_mask,
            signal_percentile=signal_percentile,
            min_signal_fraction=min_signal_fraction,
            min_dynamic_range_fraction=min_dynamic_range_fraction,
            camera_param_settle_ms=camera_param_settle_ms,
            discard_frames_after_param_change=discard_frames_after_param_change,
        )
        row.metadata.update({
            "search_method": "binary",
            "search_stage": stage,
            "min_exposure_us": float(config.min_exposure_us),
            "max_exposure_us": float(config.max_exposure_us),
            "max_exposure_source": "config_expected_camera_api_upper_bound",
            "upper_bound_policy": "no_extrapolation_past_config_max",
            "safety_fraction": float(config.safety_fraction),
            "safety_limit": safety_limit,
            "binary_search_safe": bool(row.peak_pixel_burst < safety_limit),
        })
        rows.append(row)
        return row

    low = float(config.min_exposure_us)
    high = float(config.max_exposure_us)
    low_row = probe(low, "lower_bound")
    if not bool(low_row.metadata["binary_search_safe"]):
        raise ExposureLowerBoundUnsafeError(
            "minimum exposure is not safe under binary search policy"
        )
    high_row = probe(high, "upper_bound")
    if bool(high_row.metadata["binary_search_safe"]):
        for row in rows:
            row.metadata["binary_search_termination"] = "max_exposure_safe_no_extrapolation"
            row.metadata["max_exposure_safe_without_saturation"] = True
        return rows

    safe_low = low
    unsafe_high = high
    for _ in range(int(config.iterations)):
        mid = 0.5 * (safe_low + unsafe_high)
        mid_row = probe(mid, "midpoint")
        if bool(mid_row.metadata["binary_search_safe"]):
            safe_low = mid
        else:
            unsafe_high = mid
    for row in rows:
        row.metadata["binary_search_termination"] = "bracketed_unsafe_upper_bound"
        row.metadata["max_exposure_safe_without_saturation"] = False
    return rows


def evaluate_capture_safety(
    *,
    burst: np.ndarray,
    avg_frame: np.ndarray,
    exposure_us: float,
    gain_db: float,
    full_scale: float,
    valid_pixel_domain: dict[str, Any] | None = None,
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

    mask = _valid_mask(avg.shape, valid_pixel_domain, valid_pixel_mask)
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
        metadata={
            **dict(metadata or {}),
            "saturation_report": _saturation_report(
                burst_arr=burst_arr,
                valid_mask=mask,
                full_scale=float(full_scale),
                valid_peak_burst=peak_burst,
                valid_peak_avg=peak_avg,
            ),
        },
    )


def select_recommended_probe(rows: list[ExposureProbeResult]) -> ExposureProbeResult:
    safe = [
        row for row in rows
        if row.psf_safe and bool(row.metadata.get("binary_search_safe", True))
    ]
    if not safe:
        raise ExposureSearchError("no PSF-safe exposure candidate was found")
    usable = [row for row in safe if row.usable_signal]
    pool = usable or safe
    # Prefer lower gain, then strongest usable signal without saturation.
    return sorted(pool, key=lambda r: (float(r.gain_db), -float(r.p_signal), -float(r.exposure_us)))[0]


def safe_exposure_profiles_by_gain(rows: list[ExposureProbeResult]) -> list[dict[str, Any]]:
    """Publish the maximum verified safe exposure for each enumerated gain."""
    safe = [
        row for row in rows
        if row.psf_safe and bool(row.metadata.get("binary_search_safe", True))
    ]
    by_gain: dict[float, list[ExposureProbeResult]] = {}
    for row in safe:
        by_gain.setdefault(float(row.gain_db), []).append(row)

    profiles: list[dict[str, Any]] = []
    for gain_db in sorted(by_gain):
        gain_rows = by_gain[gain_db]
        selected = sorted(
            gain_rows,
            key=lambda r: (-float(r.exposure_us), -float(r.p_signal)),
        )[0]
        profiles.append({
            "gain_db": float(selected.gain_db),
            "max_safe_exposure_us": float(selected.exposure_us),
            "exposure_us": float(selected.exposure_us),
            "peak_pixel_burst": float(selected.peak_pixel_burst),
            "peak_pixel_avg": float(selected.peak_pixel_avg),
            "peak_pixel_fraction_burst": float(selected.peak_pixel_fraction_burst),
            "saturation_margin": float(selected.peak_margin_to_full_scale),
            "p_signal": float(selected.p_signal),
            "dynamic_range": float(selected.dynamic_range),
            "usable_signal": bool(selected.usable_signal),
            "selection": "max_verified_safe_exposure_for_gain",
            "binary_search_termination": selected.metadata.get("binary_search_termination"),
            "max_exposure_safe_without_saturation": bool(
                selected.metadata.get("max_exposure_safe_without_saturation", False)
            ),
        })
    return profiles


def _probe_candidate(
    camera: ExposureSearchCamera,
    candidate: ExposureCandidate,
    *,
    frames_per_capture: int,
    full_scale: float,
    valid_pixel_domain: dict[str, Any] | None,
    valid_pixel_mask: np.ndarray | None,
    signal_percentile: float,
    min_signal_fraction: float,
    min_dynamic_range_fraction: float,
    camera_param_settle_ms: float = 0.0,
    discard_frames_after_param_change: int = 0,
) -> ExposureProbeResult:
    if candidate.exposure_us <= 0:
        raise ExposureSearchError("candidate exposure_us must be positive")
    camera.apply_camera_params(
        exposure_us=float(candidate.exposure_us),
        gain_db=float(candidate.gain_db),
    )
    if float(camera_param_settle_ms) > 0:
        time.sleep(float(camera_param_settle_ms) / 1000.0)
    if int(discard_frames_after_param_change) > 0:
        camera.acquire_burst(int(discard_frames_after_param_change))
    capture = camera.acquire_burst(int(frames_per_capture))
    burst = np.asarray(capture.burst, dtype=np.float64)
    avg = np.asarray(capture.frames_avg, dtype=np.float64)
    return evaluate_capture_safety(
        burst=burst,
        avg_frame=avg,
        exposure_us=float(candidate.exposure_us),
        gain_db=float(candidate.gain_db),
        full_scale=float(full_scale),
        valid_pixel_domain=valid_pixel_domain,
        valid_pixel_mask=valid_pixel_mask,
        signal_percentile=signal_percentile,
        min_signal_fraction=min_signal_fraction,
        min_dynamic_range_fraction=min_dynamic_range_fraction,
        metadata=getattr(capture, "metadata", {}) or {},
    )


def _valid_mask(
    shape: tuple[int, int],
    valid_pixel_domain: dict[str, Any] | None,
    valid_pixel_mask: np.ndarray | None,
) -> np.ndarray:
    try:
        return resolve_valid_pixel_mask(shape, valid_pixel_domain, valid_pixel_mask)
    except ValidPixelDomainError as exc:
        raise ExposureSearchError(str(exc)) from exc


def _resolve_exposure_bounds_us(
    camera: ExposureSearchCamera,
    config: ExposureGainSearchConfig,
) -> tuple[float, float, str, dict[str, Any]]:
    config_min_exposure_us = float(config.min_exposure_us)
    config_max_exposure_us = float(config.max_exposure_us)
    _validate_exposure_bounds_us(
        config_min_exposure_us,
        config_max_exposure_us,
        label="config exposure bounds",
    )
    reader = getattr(camera, "read_exposure_bounds_us", None)
    if callable(reader):
        try:
            api_min_exposure_us, api_max_exposure_us = reader()
        except Exception as exc:
            raise ExposureSearchError(
                "failed to read camera SHUTTER exposure bounds from API"
            ) from exc
        api_min_exposure_us = float(api_min_exposure_us)
        api_max_exposure_us = float(api_max_exposure_us)
        _validate_exposure_bounds_us(
            api_min_exposure_us,
            api_max_exposure_us,
            label="camera API exposure bounds",
        )
        min_exposure_us = max(api_min_exposure_us, config_min_exposure_us)
        max_exposure_us = min(api_max_exposure_us, config_max_exposure_us)
        if max_exposure_us < min_exposure_us:
            raise ExposureSearchError(
                "camera API exposure bounds do not overlap config exposure bounds"
            )
        source = "camera_api_clamped_by_plan"
        metadata = {
            "camera_api_min_exposure_us": api_min_exposure_us,
            "camera_api_max_exposure_us": api_max_exposure_us,
            "config_min_exposure_us": config_min_exposure_us,
            "config_max_exposure_us": config_max_exposure_us,
            "effective_min_exposure_us": float(min_exposure_us),
            "effective_max_exposure_us": float(max_exposure_us),
        }
    else:
        min_exposure_us = config_min_exposure_us
        max_exposure_us = config_max_exposure_us
        source = "config_expected_camera_api_upper_bound"
        metadata = {
            "config_min_exposure_us": config_min_exposure_us,
            "config_max_exposure_us": config_max_exposure_us,
            "effective_min_exposure_us": float(min_exposure_us),
            "effective_max_exposure_us": float(max_exposure_us),
        }
    return float(min_exposure_us), float(max_exposure_us), source, metadata


def _validate_exposure_bounds_us(
    min_exposure_us: float,
    max_exposure_us: float,
    *,
    label: str,
) -> None:
    if min_exposure_us <= 0.0 or max_exposure_us <= 0.0:
        raise ExposureSearchError(f"{label} must be positive")
    if max_exposure_us < min_exposure_us:
        raise ExposureSearchError(f"{label} upper bound is below lower bound")


def _saturation_report(
    *,
    burst_arr: np.ndarray,
    valid_mask: np.ndarray,
    full_scale: float,
    valid_peak_burst: float,
    valid_peak_avg: float,
) -> dict[str, Any]:
    finite_all = np.isfinite(burst_arr)
    full_finite = bool(finite_all.all())
    full_peak = float(np.max(burst_arr)) if full_finite else None
    full_peak_fraction = (
        full_peak / float(full_scale)
        if full_peak is not None
        else None
    )
    saturated_all = np.asarray(
        np.logical_and(finite_all, burst_arr >= float(full_scale)),
        dtype=bool,
    )
    valid_saturated = saturated_all[:, valid_mask]
    excluded_mask = np.logical_not(valid_mask)
    excluded_saturated = saturated_all[:, excluded_mask]
    nonfinite_all = np.asarray(np.logical_not(finite_all), dtype=bool)
    valid_nonfinite = nonfinite_all[:, valid_mask]
    excluded_nonfinite = nonfinite_all[:, excluded_mask]
    frame_saturated = np.any(saturated_all.reshape(saturated_all.shape[0], -1), axis=1)
    return {
        "policy": "safety_decision_uses_valid_pixel_mask_to_exclude_bad_pixels",
        "full_scale": float(full_scale),
        "frame_count": int(burst_arr.shape[0]),
        "frame_shape_hw": [int(burst_arr.shape[1]), int(burst_arr.shape[2])],
        "valid_pixel_count_per_frame": int(np.count_nonzero(valid_mask)),
        "excluded_pixel_count_per_frame": int(valid_mask.size - np.count_nonzero(valid_mask)),
        "all_pixels_finite": full_finite,
        "all_frames_all_pixels_below_full_scale": bool(full_finite and full_peak < float(full_scale)),
        "full_frame_nonfinite_status": "all_finite" if full_finite else "nonfinite_pixels_present",
        "full_frame_nonfinite_pixel_count": int(np.count_nonzero(nonfinite_all)),
        "full_frame_peak_pixel_burst": full_peak,
        "full_frame_peak_pixel_fraction_burst": full_peak_fraction,
        "full_frame_saturated_pixel_count": int(np.count_nonzero(saturated_all)),
        "full_frame_saturated_frame_count": int(np.count_nonzero(frame_saturated)),
        "valid_domain_peak_pixel_burst": float(valid_peak_burst),
        "valid_domain_peak_pixel_avg": float(valid_peak_avg),
        "valid_domain_saturated_pixel_count": int(np.count_nonzero(valid_saturated)),
        "valid_domain_nonfinite_pixel_count": int(np.count_nonzero(valid_nonfinite)),
        "excluded_domain_saturated_pixel_count": int(np.count_nonzero(excluded_saturated)),
        "excluded_domain_nonfinite_pixel_count": int(np.count_nonzero(excluded_nonfinite)),
        "report_only_not_safety_decision": True,
    }
