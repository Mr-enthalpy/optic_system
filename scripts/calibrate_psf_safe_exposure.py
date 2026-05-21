#!/usr/bin/env python3
"""
Phase 3.0.5b - PSF-safe camera exposure / gain refinement.

Finds camera parameters that keep every raw burst frame pixel strictly below
dtype full scale across all tested wavelengths while preserving usable signal
strength.

Outputs:
  data/raw/bishe_psf_safe_exposure.h5
  outputs/exposure_calibration/camera_params_psf_safe.json

Constraints:
  - Requires exclusive hardware access.  Close any hardware-owning
    GUI/session before running.  The read-only run-status monitor may remain open.
  - Always prefers gain_min.  Only elevates gain when gain_min yields
    safe-but-unusably-dim signal.
  - Current thesis-branch selection first searches each wavelength
    independently for a recommended PSF-safe camera profile, then derives
    `global_safe_camera` as a shared baseline from those per-wavelength safe
    bounds.
  - `global_safe_camera` is a derived shared-exposure diagnostic baseline.  It
    does not replace the per-wavelength `camera_param_catalog`.
  - Current thesis-branch selection is still discrete and lexicographic within
    each wavelength search: strict PSF safety, then gain_min preference, then
    largest usable exposure at that gain, with higher gain only as a
    low-signal fallback.
  - If even gain_min + exposure_min has any pixel at full scale in raw
    burst frames, fails immediately.  Raising gain is never used to solve
    pixel saturation.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import yaml


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _ensure_sys_path() -> None:
    root = str(_repo_root())
    if root not in sys.path:
        sys.path.insert(0, root)


def _now_ns() -> int:
    return time.monotonic_ns()


def _status_int(value: Any) -> int | None:
    if value is None:
        return None
    return int(value)


def _status_shape(value: Any) -> list[int] | None:
    if value is None:
        return None
    return [int(v) for v in value]


class OptionalRunStatus:
    """Publish run-status files when status_dir is given, otherwise no-op."""

    def __init__(self, status_dir: Path | None, run_id: str):
        self._publisher = None
        self._initialized_ok = True
        self._run_id = run_id
        if status_dir is not None:
            from diagnostics.run_status import RunStatusPublisher
            self._publisher = RunStatusPublisher(status_dir, run_id)

    def update(self, **kwargs: Any) -> None:
        if self._publisher is not None:
            try:
                self._publisher.update(**kwargs)
            except Exception:
                if self._initialized_ok:
                    print(
                        f"[{self._run_id}] status-dir: failed to write state.json, "
                        f"run-status publishing disabled",
                        file=sys.stderr,
                    )
                    self._initialized_ok = False

    def append_log(self, level: str, message: str, **fields: Any) -> None:
        if self._publisher is not None:
            try:
                self._publisher.append_log(level, message, **fields)
            except Exception:
                pass

    def write_frame_preview(self, frame: np.ndarray) -> None:
        if self._publisher is not None:
            try:
                self._publisher.write_frame_preview(frame)
            except Exception as exc:
                self.append_log("WARNING", "failed to write frame preview", error=str(exc))

    def write_fast_frame_preview(self, frame: np.ndarray) -> None:
        if self._publisher is not None:
            try:
                self._publisher.write_fast_frame_preview(frame)
            except Exception:
                pass

    def write_frame_stats(self, stats: dict[str, Any]) -> None:
        if self._publisher is not None:
            try:
                self._publisher.write_frame_stats(stats)
            except Exception as exc:
                self.append_log("WARNING", "failed to write frame stats", error=str(exc))

    def write_mask_preview(self, mask: np.ndarray) -> None:
        if self._publisher is not None:
            try:
                self._publisher.write_mask_preview(mask)
            except Exception as exc:
                self.append_log("WARNING", "failed to write mask preview", error=str(exc))


class _FastStatusPreviewPump:
    def __init__(self, run_status: OptionalRunStatus, *, min_interval_s: float = 0.25):
        self._run_status = run_status
        self._min_interval_s = max(0.05, float(min_interval_s))
        self._last_write_s = 0.0
        self._worker = None

    def start(self, stream_factory: Any, worker_factory: Any) -> None:
        stream = stream_factory()

        def _on_frame(packet: Any) -> None:
            now = time.monotonic()
            if now - self._last_write_s < self._min_interval_s:
                return
            self._last_write_s = now
            self._run_status.write_fast_frame_preview(packet.raw)

        self._worker = worker_factory(stream, on_frame=_on_frame)
        self._worker.start()

    def stop(self) -> None:
        if self._worker is not None:
            self._worker.stop(join_timeout=1.0)
            self._worker = None


# ---------------------------------------------------------------------------
# YAML loading
# ---------------------------------------------------------------------------


def load_psf_safe_exposure_plan(path: str | Path) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        plan = yaml.safe_load(f)
    _validate_plan(plan)
    return plan


def _validate_plan(plan: dict[str, Any]) -> None:
    assert plan.get("plan_id"), "plan_id is required"
    wls = plan.get("wavelengths", [])
    assert isinstance(wls, list) and len(wls) > 0, "wavelengths must be non-empty"
    for wl in wls:
        assert isinstance(wl.get("wavelength_nm"), (int, float)), "each wavelength needs wavelength_nm"
    assert plan.get("lcd"), "lcd section required"
    assert plan.get("camera_search"), "camera_search section required"
    assert plan.get("psf_safety"), "psf_safety section required"
    psf_safety = plan.get("psf_safety") or {}
    if psf_safety.get("rule") != "all_frames_all_pixels_strictly_below_full_scale":
        raise ValueError(
            "psf_safety.rule must be 'all_frames_all_pixels_strictly_below_full_scale'"
        )
    assert plan.get("signal"), "signal section required"


# ---------------------------------------------------------------------------
# Peak-pixel PSF safety metrics
# ---------------------------------------------------------------------------


def compute_peak_safety_metrics(
    burst: np.ndarray,
    full_scale: float,
    *,
    avg_frame: np.ndarray | None = None,
    valid_pixel_mask: np.ndarray | None = None,
    diagnostic_percentiles: tuple[float, ...] = (99.0, 99.9),
) -> dict[str, Any]:
    burst_arr = np.asarray(burst, dtype=np.float64)
    if burst_arr.ndim != 3:
        raise ValueError(f"burst must be 3D [K, H, W], got shape {burst_arr.shape}")
    if valid_pixel_mask is None:
        valid_mask = np.ones(burst_arr.shape[-2:], dtype=bool)
    else:
        valid_mask = np.asarray(valid_pixel_mask, dtype=bool)
    if valid_mask.shape != burst_arr.shape[-2:]:
        raise ValueError(
            f"valid_pixel_mask shape {valid_mask.shape} does not match frame shape "
            f"{burst_arr.shape[-2:]}"
        )
    if not np.any(valid_mask):
        raise ValueError("valid_pixel_mask leaves zero valid pixels")

    invalid_mask = ~valid_mask
    valid_pixels = burst_arr[:, valid_mask]
    valid_finite = bool(np.isfinite(valid_pixels).all())
    peak_pixel_burst = float(np.max(valid_pixels)) if valid_finite else float("inf")
    valid_eval = np.where(valid_mask[None, :, :], burst_arr, -np.inf)
    finite_valid_eval = np.where(np.isfinite(valid_eval), valid_eval, -np.inf)
    peak_flat_idx = int(np.argmax(finite_valid_eval))
    peak_frame_idx, peak_y, peak_x = np.unravel_index(peak_flat_idx, burst_arr.shape)

    psf_safe = bool(valid_finite and peak_pixel_burst < float(full_scale))
    unsafe_reason: str | None = None
    if not valid_finite:
        unsafe_reason = "non_finite_pixel_in_valid_domain"
    elif peak_pixel_burst >= float(full_scale):
        unsafe_reason = "peak_pixel_at_or_above_full_scale_in_valid_domain"

    peak_pixel_avg: float | None = None
    p99_0_avg: float | None = None
    p99_9_avg: float | None = None
    if avg_frame is not None:
        avg = np.asarray(avg_frame, dtype=np.float64)
        if avg.shape != valid_mask.shape:
            raise ValueError(
                f"avg_frame shape {avg.shape} does not match valid mask shape {valid_mask.shape}"
            )
        avg_valid = avg[valid_mask]
        peak_pixel_avg = float(np.max(avg_valid))
        for pct in diagnostic_percentiles:
            val = float(np.percentile(avg_valid, pct))
            if pct == 99.0:
                p99_0_avg = val
            elif pct == 99.9:
                p99_9_avg = val

    invalid_domain_peak_pixel_burst: float | None = None
    invalid_domain_full_scale_pixel_count = 0
    invalid_domain_nonfinite_pixel_count = 0
    valid_full_scale_mask = (
        np.isfinite(burst_arr)
        & (burst_arr >= float(full_scale))
        & valid_mask[None, :, :]
    )
    valid_domain_full_scale_pixel_count = int(np.count_nonzero(valid_full_scale_mask))
    valid_domain_full_scale_sample_coords = [
        {
            "frame": int(frame_idx),
            "y": int(y),
            "x": int(x),
            "value": float(burst_arr[frame_idx, y, x]),
        }
        for frame_idx, y, x in np.argwhere(valid_full_scale_mask)[:20]
    ]
    if np.any(invalid_mask):
        invalid_pixels = burst_arr[:, invalid_mask]
        invalid_domain_nonfinite_pixel_count = int(np.count_nonzero(~np.isfinite(invalid_pixels)))
        if invalid_pixels.size > 0:
            finite_invalid_pixels = invalid_pixels[np.isfinite(invalid_pixels)]
            if finite_invalid_pixels.size > 0:
                invalid_domain_peak_pixel_burst = float(np.max(finite_invalid_pixels))
            invalid_domain_full_scale_pixel_count = int(
                np.count_nonzero(np.isfinite(invalid_pixels) & (invalid_pixels >= float(full_scale)))
            )

    peak_pixel_fraction_burst = peak_pixel_burst / float(full_scale) if float(full_scale) > 0 else float("inf")
    peak_margin_to_full_scale = float(full_scale) - peak_pixel_burst

    return {
        "psf_safe": psf_safe,
        "unsafe_reason": unsafe_reason,
        "peak_pixel_burst": peak_pixel_burst,
        "peak_pixel_avg": peak_pixel_avg,
        "peak_pixel_fraction_burst": peak_pixel_fraction_burst,
        "peak_margin_to_full_scale": peak_margin_to_full_scale,
        "frame_dtype_full_scale": float(full_scale),
        "p99_0_avg": p99_0_avg,
        "p99_9_avg": p99_9_avg,
        "valid_pixel_count": int(np.count_nonzero(valid_mask)),
        "invalid_pixel_count": int(np.count_nonzero(invalid_mask)),
        "valid_domain_peak_frame_index": int(peak_frame_idx),
        "valid_domain_peak_y": int(peak_y),
        "valid_domain_peak_x": int(peak_x),
        "valid_domain_full_scale_pixel_count": valid_domain_full_scale_pixel_count,
        "valid_domain_full_scale_sample_coords": valid_domain_full_scale_sample_coords,
        "invalid_domain_peak_pixel_burst": invalid_domain_peak_pixel_burst,
        "invalid_domain_full_scale_pixel_count": invalid_domain_full_scale_pixel_count,
        "invalid_domain_nonfinite_pixel_count": invalid_domain_nonfinite_pixel_count,
    }


def compute_signal_metrics(
    frame: np.ndarray,
    full_scale: float,
    *,
    valid_pixel_mask: np.ndarray | None = None,
    signal_percentile: float = 99.0,
    min_signal_fraction_threshold: float = 0.10,
    min_dynamic_range_fraction: float = 0.08,
) -> dict[str, Any]:
    arr = np.asarray(frame, dtype=np.float64)
    if valid_pixel_mask is not None:
        mask = np.asarray(valid_pixel_mask, dtype=bool)
        if mask.shape != arr.shape:
            raise ValueError(
                f"valid_pixel_mask shape {mask.shape} does not match frame shape {arr.shape}"
            )
        if not np.any(mask):
            raise ValueError("valid_pixel_mask leaves zero valid pixels")
        arr = arr[mask]

    p_signal = float(np.percentile(arr, signal_percentile))
    p1 = float(np.percentile(arr, 1.0))
    dynamic_range = p_signal - p1

    usable = bool(
        p_signal > full_scale * min_signal_fraction_threshold
        and dynamic_range > full_scale * min_dynamic_range_fraction
    )

    return {
        "p_signal": p_signal,
        "dynamic_range": dynamic_range,
        "usable": usable,
    }


def infer_full_scale(frame: np.ndarray) -> int:
    if frame.dtype == np.uint8:
        return 255
    elif frame.dtype == np.uint16:
        return 65535
    elif frame.dtype == np.uint32:
        return 4294967295
    elif np.issubdtype(frame.dtype, np.floating):
        raise ValueError(
            "Cannot infer full_scale from float image. "
            "Use metadata.frame_dtype_full_scale or plan.camera.full_scale."
        )
    else:
        raise ValueError(f"Unknown dtype for full_scale inference: {frame.dtype}")


def _resolve_frame_full_scale(capture: Any, plan: dict[str, Any], *, dry_run: bool) -> tuple[float, str]:
    metadata = getattr(capture, "metadata", {}) or {}
    metadata_full_scale = metadata.get("frame_dtype_full_scale")
    if metadata_full_scale is not None:
        return float(metadata_full_scale), str(metadata.get("frame_dtype_full_scale_source") or "frame_metadata")
    if dry_run:
        plan_full_scale = plan.get("camera", {}).get("full_scale")
        if plan_full_scale is not None:
            return float(plan_full_scale), "dry_run_plan_camera_full_scale"
        return float(infer_full_scale(np.asarray(capture.frames_avg))), "dry_run_dtype_inference"
    raise RuntimeError(
        "Hardware PSF-safe exposure calibration requires camera frame metadata "
        "to provide frame_dtype_full_scale. Refusing to infer full scale from "
        "observed image data or ndarray dtype."
    )


def _resolve_dry_run_full_scale(plan: dict[str, Any]) -> tuple[float, str]:
    plan_full_scale = plan.get("camera", {}).get("full_scale")
    if plan_full_scale is not None:
        return float(plan_full_scale), "dry_run_plan_camera_full_scale"
    return 255.0, "dry_run_default_uint8_full_scale"


# ---------------------------------------------------------------------------
# Hardware lock
# ---------------------------------------------------------------------------


class HardwareLock:
    def __init__(self, lock_path: str | Path):
        self._lock_path = Path(lock_path)
        self._acquired = False

    def acquire(self) -> None:
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        if self._lock_path.exists():
            raise RuntimeError(
                f"Hardware lock file exists: {self._lock_path}\n"
                "Another capture task may be running.  Close that task "
                "and delete the lock file if it is stale."
            )
        self._lock_path.write_text(f"pid={os.getpid()}\nacquired_ns={_now_ns()}\n")
        self._acquired = True

    def release(self) -> None:
        if self._acquired and self._lock_path.exists():
            self._lock_path.unlink(missing_ok=True)
        self._acquired = False


# ---------------------------------------------------------------------------
# Sweep orchestration
# ---------------------------------------------------------------------------


def _acquire_and_evaluate(
    camera_adapter,
    k: int,
    full_scale: float,
    diagnostics_cfg: dict,
    sig_cfg: dict,
    *,
    valid_pixel_mask: np.ndarray | None = None,
) -> dict[str, Any]:
    capture = camera_adapter.acquire_burst(k)
    avg = capture.frames_avg
    burst = np.asarray(getattr(capture, "burst", avg[None, :, :]), dtype=np.float64)

    pcts = tuple(float(p) for p in diagnostics_cfg.get("percentiles", [99.0, 99.9]))
    safety = compute_peak_safety_metrics(
        burst,
        full_scale,
        avg_frame=avg,
        valid_pixel_mask=valid_pixel_mask,
        diagnostic_percentiles=pcts,
    )
    sig = compute_signal_metrics(
        avg, full_scale,
        valid_pixel_mask=valid_pixel_mask,
        signal_percentile=sig_cfg["percentile"],
        min_signal_fraction_threshold=sig_cfg["min_signal_fraction_threshold"],
        min_dynamic_range_fraction=sig_cfg["min_dynamic_range_fraction"],
    )
    row = {
        "frame": avg,
        "psf_safe": safety["psf_safe"],
        "unsafe_reason": safety["unsafe_reason"],
        "peak_pixel_burst": safety["peak_pixel_burst"],
        "peak_pixel_avg": safety["peak_pixel_avg"],
        "peak_pixel_fraction_burst": safety["peak_pixel_fraction_burst"],
        "peak_margin_to_full_scale": safety["peak_margin_to_full_scale"],
        "p99_0_avg": safety.get("p99_0_avg"),
        "p99_9_avg": safety.get("p99_9_avg"),
        "p_signal": sig["p_signal"],
        "dynamic_range": sig["dynamic_range"],
        "low_signal": not sig["usable"],
        "valid_pixel_count": safety["valid_pixel_count"],
        "invalid_pixel_count": safety["invalid_pixel_count"],
        "valid_domain_peak_frame_index": safety["valid_domain_peak_frame_index"],
        "valid_domain_peak_y": safety["valid_domain_peak_y"],
        "valid_domain_peak_x": safety["valid_domain_peak_x"],
        "valid_domain_full_scale_pixel_count": safety["valid_domain_full_scale_pixel_count"],
        "valid_domain_full_scale_sample_coords": safety["valid_domain_full_scale_sample_coords"],
        "invalid_domain_peak_pixel_burst": safety["invalid_domain_peak_pixel_burst"],
        "invalid_domain_full_scale_pixel_count": safety["invalid_domain_full_scale_pixel_count"],
        "invalid_domain_nonfinite_pixel_count": safety["invalid_domain_nonfinite_pixel_count"],
    }
    if not row["psf_safe"]:
        row["failure_frame"] = burst[int(safety["valid_domain_peak_frame_index"])]
        row["failure_frame_kind"] = "raw_burst_peak_frame"
    else:
        row["failure_frame"] = None
        row["failure_frame_kind"] = None
    return row


def _apply_camera_params_and_settle(
    camera_adapter,
    *,
    exposure_us: float,
    gain_db: float,
    settle_ms: float = 0.0,
    discard_frames: int = 0,
) -> None:
    camera_adapter.apply_camera_params(exposure_us=exposure_us, gain_db=gain_db)
    if settle_ms > 0:
        time.sleep(float(settle_ms) / 1000.0)
    if discard_frames > 0:
        camera_adapter.acquire_burst(int(discard_frames))


def _all_wavelengths_safe(results_per_wl: list[dict]) -> bool:
    return all(bool(r["psf_safe"]) for r in results_per_wl)


def _worst_signal_wavelength(results_per_wl: list[dict]) -> dict:
    return min(results_per_wl, key=lambda r: r["p_signal"])


def _estimate_safe_bound_for_wavelength(
    camera_adapter,
    *,
    k: int,
    full_scale: float,
    diagnostics_cfg: dict[str, Any],
    sig_cfg: dict[str, Any],
    L: float,
    R: float,
    gain_db: float,
    valid_pixel_mask: np.ndarray | None = None,
    camera_param_settle_ms: float = 0.0,
    discard_frames_after_param_change: int = 0,
    eps_absolute: float = 50.0,
    max_iter: int = 30,
    on_probe: Any | None = None,
) -> tuple[float, dict[str, Any]]:
    R = float(R)
    L = float(L)

    _apply_camera_params_and_settle(
        camera_adapter,
        exposure_us=R,
        gain_db=gain_db,
        settle_ms=camera_param_settle_ms,
        discard_frames=discard_frames_after_param_change,
    )
    row = _acquire_and_evaluate(
        camera_adapter,
        k,
        full_scale,
        diagnostics_cfg,
        sig_cfg,
        valid_pixel_mask=valid_pixel_mask,
    )
    if on_probe is not None:
        on_probe(R, row, "bracket_upper")
    if row["psf_safe"]:
        return R, row

    _apply_camera_params_and_settle(
        camera_adapter,
        exposure_us=L,
        gain_db=gain_db,
        settle_ms=camera_param_settle_ms,
        discard_frames=discard_frames_after_param_change,
    )
    row = _acquire_and_evaluate(
        camera_adapter,
        k,
        full_scale,
        diagnostics_cfg,
        sig_cfg,
        valid_pixel_mask=valid_pixel_mask,
    )
    if on_probe is not None:
        on_probe(L, row, "bracket_lower")
    if not row["psf_safe"]:
        return L, row
    safe_bound = L
    safe_row = row

    for _ in range(max_iter):
        if R - L < eps_absolute:
            break
        mid = (L + R) / 2.0
        _apply_camera_params_and_settle(
            camera_adapter,
            exposure_us=mid,
            gain_db=gain_db,
            settle_ms=camera_param_settle_ms,
            discard_frames=discard_frames_after_param_change,
        )
        row = _acquire_and_evaluate(
            camera_adapter,
            k,
            full_scale,
            diagnostics_cfg,
            sig_cfg,
            valid_pixel_mask=valid_pixel_mask,
        )
        if on_probe is not None:
            on_probe(mid, row, "bisect")
        if row["psf_safe"]:
            L = mid
            safe_bound = mid
            safe_row = row
        else:
            R = mid

    return safe_bound, safe_row


@dataclass
class _GainResult:
    gain_db: float
    exposure_us: float | None = None
    psf_safe: bool = False
    low_signal: bool = True
    per_wavelength_bounds: dict[str, float] = field(default_factory=dict)
    final_rows: list[dict[str, Any]] = field(default_factory=list)


def _camera_gain_candidates(plan: dict[str, Any]) -> list[float]:
    search = plan["camera_search"]
    explicit = search.get("gains_db")
    if explicit is not None:
        if not isinstance(explicit, list) or not explicit:
            raise ValueError("camera_search.gains_db must be a non-empty list when provided")
        return [float(x) for x in explicit]
    gain_min = float(search["gain_db_min"])
    gain_max = float(search["gain_db_max"])
    gain_step = float(search["gain_db_step_db"])
    gains = [gain_min]
    g = gain_min + gain_step
    while g <= gain_max + 1e-9:
        gains.append(g)
        g += gain_step
    return gains


def _apply_verification_exposure_backoff(
    bounds: dict[str, float],
    *,
    exposure_min: float,
    backoff_us: float,
) -> dict[str, float]:
    if backoff_us <= 0:
        return {str(key): float(value) for key, value in bounds.items()}
    adjusted: dict[str, float] = {}
    for key, value in bounds.items():
        adjusted[str(key)] = max(float(exposure_min), float(value) - float(backoff_us))
    return adjusted


def run_psf_safe_exposure(
    plan: dict[str, Any],
    camera_service,
    lcd_service,
    tls_service,
    *,
    dry_run: bool = False,
    allow_wavelength_labels_without_tls: bool = False,
    status_dir: Path | None = None,
    status_preview_every: int = 1,
) -> tuple[Path, dict[str, Any]]:
    _ensure_sys_path()
    from diagnostics.valid_pixel_domain import build_valid_pixel_mask
    from tasks.psf_safe_exposure_h5 import PsfSafeExposureWriter

    plan_id = plan["plan_id"]
    wls = plan["wavelengths"]
    lcd_cfg = plan["lcd"]
    search_cfg = plan["camera_search"]
    diagnostics_cfg = plan.get("diagnostics", {})
    sig_cfg = plan["signal"]

    exposure_start = float(search_cfg["exposure_us_start"])
    exposure_min = float(search_cfg["exposure_us_min"])
    step_factor = float(search_cfg["exposure_us_step_factor"])
    gain_min = float(search_cfg["gain_db_min"])
    gain_max = float(search_cfg["gain_db_max"])
    gain_step = float(search_cfg["gain_db_step_db"])
    k = int(search_cfg["frames_per_setting"])
    camera_param_settle_ms = float(search_cfg.get("camera_param_settle_ms", 0.0))
    discard_frames_after_param_change = int(
        search_cfg.get("discard_frames_after_camera_param_change", 0)
    )
    verification_exposure_backoff_us = float(
        search_cfg.get("verification_exposure_backoff_us", 0.0)
    )

    output_raw = plan["output"]["raw_h5"]
    output_json = plan["output"]["camera_params_json"]

    run_status = OptionalRunStatus(status_dir, run_id=plan_id)

    if not dry_run and tls_service is None and not allow_wavelength_labels_without_tls:
        msg = (
            "TLS service is required for hardware PSF-safe exposure calibration. "
            "Without TLS participation, plan wavelengths are only labels and "
            "camera_params_psf_safe.json cannot prove cross-wavelength safety. "
            "Pass --tls-serial or set TLS_C1_SERIAL. For explicit manual external "
            "wavelength control, rerun with --allow-wavelength-labels-without-tls."
        )
        run_status.append_log("CRITICAL", msg)
        run_status.update(
            plan_id=plan_id,
            phase="3.0.5b",
            completed=False,
            error=msg,
        )
        raise RuntimeError(msg)

    lock = HardwareLock(plan.get("lock_file", "outputs/run_status/capture_hardware.lock"))
    if not dry_run:
        lock.acquire()

    writer: PsfSafeExposureWriter | None = None
    fast_preview_pump: _FastStatusPreviewPump | None = None
    full_scale_source: str = "unknown"
    valid_domain_policy: dict[str, Any] | None = None
    valid_pixel_mask: np.ndarray | None = None

    try:
        lcd_metadata: dict[str, Any]
        current_mask_id = "all_transmissive"
        current_mask_preview: np.ndarray | None = None
        initial_frame_preview: np.ndarray | None = None

        if not dry_run:
            from capture.frame_capture import FrameCaptureHelper
            from capture.preview_worker import PreviewWorker
            from devices.frame_stream import FrameStreamClient
            from tasks.capture_forward_dataset import CameraCaptureAdapter

            frame_stream = FrameStreamClient(recv_timeout_ms=5000)
            capture_helper = FrameCaptureHelper(frame_stream)
            camera_adapter = CameraCaptureAdapter(capture_helper, camera_service)

            current_mask_preview = lcd_service.make_all_transmissive_mask()
            lcd_service.show_mono_mask(
                current_mask_preview,
                mask_id=current_mask_id,
                mode="all_transmissive",
            )
            time.sleep(lcd_cfg.get("settle_ms", 200) / 1000.0)

            camera_service.start_stream()
            fast_preview_pump = _FastStatusPreviewPump(run_status)
            fast_preview_pump.start(
                lambda: FrameStreamClient(recv_timeout_ms=5000),
                PreviewWorker,
            )
            first_frame = camera_adapter.acquire_burst(1)
            initial_frame_preview = np.asarray(first_frame.frames_avg)
            full_scale, full_scale_source = _resolve_frame_full_scale(
                first_frame, plan, dry_run=False,
            )
            valid_domain = build_valid_pixel_mask(
                tuple(np.asarray(first_frame.frames_avg).shape[-2:]),
                plan.get("valid_pixel_domain"),
            )
            valid_pixel_mask = valid_domain.mask
            valid_domain_policy = valid_domain.policy_json()
            lcd_metadata = lcd_service.get_metadata()
        else:
            camera_adapter = _make_fake_adapter()
            full_scale, full_scale_source = _resolve_dry_run_full_scale(plan)
            camera_adapter.apply_camera_params(
                exposure_us=exposure_start,
                gain_db=gain_min,
            )
            first_frame = camera_adapter.acquire_burst(1)
            initial_frame_preview = np.asarray(first_frame.frames_avg)
            valid_domain = build_valid_pixel_mask(
                tuple(np.asarray(first_frame.frames_avg).shape[-2:]),
                plan.get("valid_pixel_domain"),
            )
            valid_pixel_mask = valid_domain.mask
            valid_domain_policy = valid_domain.policy_json()
            lcd_metadata = {
                "display_index": -1,
                "subpixel_axis": 1,
                "logical_shape": (8, 8),
                "physical_shape": (8, 24),
                "mode": "fake",
            }
            current_mask_preview = np.full((8, 24), 255, dtype=np.uint8)

        if (
            not dry_run
            and valid_domain_policy["type"] != "full_frame"
            and not valid_domain_policy.get("artifact_hash")
        ):
            raise RuntimeError(
                "Hardware PSF-safe exposure runs with a non-full-frame "
                "valid_pixel_domain require an existing source_artifact with "
                "a recorded artifact_hash. Re-run "
                "scripts/diagnose_valid_pixel_domain.py or use an explicit "
                "dry-run/test fixture instead."
            )

        writer = PsfSafeExposureWriter(
            output_raw,
            plan_id=plan_id,
            frames_per_capture=k,
        )
        writer.open()
        writer.set_full_scale(int(full_scale), source=full_scale_source)
        writer.write_plan_json(plan)
        writer.write_lcd_metadata(lcd_metadata)
        writer.write_valid_pixel_domain(valid_domain_policy)

        run_status.update(
            plan_id=plan_id,
            phase="3.0.5b",
            capture_index=0,
            n_captures=0,
            current_mask_id=current_mask_id,
            lcd_display_index=_status_int(lcd_metadata.get("display_index")),
            lcd_physical_shape=_status_shape(lcd_metadata.get("physical_shape")),
            lcd_logical_shape=_status_shape(lcd_metadata.get("logical_shape")),
            lcd_subpixel_axis=_status_int(lcd_metadata.get("subpixel_axis")),
            camera_exposure_us=float(exposure_start),
            camera_gain_db=float(gain_min),
            camera_frame_dtype_full_scale=int(full_scale),
        )
        run_status.append_log(
            "INFO",
            "camera frame full scale resolved",
            frame_dtype_full_scale=int(full_scale),
            full_scale_source=full_scale_source,
        )
        run_status.append_log(
            "INFO",
            "valid pixel domain resolved",
            valid_pixel_domain_type=valid_domain_policy["type"],
            valid_pixel_count=valid_domain_policy["valid_pixel_count"],
            invalid_pixel_count=valid_domain_policy["invalid_pixel_count"],
            source_artifact=valid_domain_policy.get("source_artifact"),
            source_artifact_exists=valid_domain_policy.get("source_artifact_exists"),
            artifact_hash=valid_domain_policy.get("artifact_hash"),
        )
        if current_mask_preview is not None:
            run_status.write_mask_preview(current_mask_preview)
        if initial_frame_preview is not None:
            run_status.write_frame_preview(initial_frame_preview)
            run_status.write_frame_stats({
                "preview_kind": "initial_camera_frame",
                "peak_pixel_avg": float(np.max(initial_frame_preview)),
                "frame_dtype_full_scale": int(full_scale),
                "shape": list(np.asarray(initial_frame_preview).shape),
                "valid_pixel_domain_type": valid_domain_policy["type"],
                "valid_pixel_count": valid_domain_policy["valid_pixel_count"],
                "invalid_pixel_count": valid_domain_policy["invalid_pixel_count"],
            })
        run_status.append_log("INFO", "PSF-safe exposure calibration started",
                              exposure_start_us=exposure_start,
                              gain_min_db=gain_min,
                              camera_param_settle_ms=camera_param_settle_ms,
                              discard_frames_after_camera_param_change=discard_frames_after_param_change)
        if not dry_run and tls_service is None and allow_wavelength_labels_without_tls:
            run_status.append_log(
                "WARNING",
                "Dangerous override enabled: TLS service not configured; plan wavelengths are labels only and hardware wavelength will not change",
            )

        all_results: list[dict] = []
        selection_reason: str | None = None
        safe_exposure: float | None = None
        safe_gain: float | None = None
        binary_search_eps = float(search_cfg.get("binary_search_eps_us", 50.0))

        def _set_wavelength_once(wl: dict[str, Any]) -> float:
            wl_nm = float(wl["wavelength_nm"])
            grating = wl.get("grating")
            if not dry_run and tls_service is not None:
                run_status.update(
                    target_wavelength_nm=wl_nm,
                    tls_grating=int(grating) if grating is not None else None,
                    tls_moving=True,
                )
                if grating is not None:
                    tls_service.set_grating(int(grating))
                tls_service.set_wavelength_nm(wl_nm)
                tls_service.move(timeout_s=60.0)
                tls_status = tls_service.wait_until_idle(timeout_s=60.0)
                run_status.update(
                    current_wavelength_nm=tls_status.current_wavelength_nm or wl_nm,
                    target_wavelength_nm=tls_status.target_wavelength_nm or wl_nm,
                    tls_grating=tls_status.grating if tls_status.grating is not None else (
                        int(grating) if grating is not None else None
                    ),
                    tls_moving=False,
                )
                settle = wl.get("settle_ms", 1000)
                if settle > 0:
                    time.sleep(settle / 1000.0)
            else:
                run_status.update(current_wavelength_nm=wl_nm)
            return wl_nm

        def _record_candidate_row(
            *,
            wl_nm: float,
            at_exposure: float,
            at_gain: float,
            row: dict[str, Any],
            total_trials: int | None,
            phase_label: str = "search",
        ) -> None:
            row["wavelength_nm"] = wl_nm
            row["exposure_us"] = at_exposure
            row["gain_db"] = at_gain

            writer.append_sweep_row(
                wavelength_nm=wl_nm,
                exposure_us=at_exposure,
                gain_db=at_gain,
                frames_avg=row["frame"],
                peak_pixel_burst=row["peak_pixel_burst"],
                peak_pixel_avg=row["peak_pixel_avg"],
                peak_pixel_fraction_burst=row["peak_pixel_fraction_burst"],
                peak_margin_to_full_scale=row["peak_margin_to_full_scale"],
                p99_0_avg=row["p99_0_avg"],
                p99_9_avg=row["p99_9_avg"],
                unsafe_reason=row["unsafe_reason"],
                psf_safe=row["psf_safe"],
                p_signal=row["p_signal"],
                dynamic_range=row["dynamic_range"],
                low_signal=row["low_signal"],
                valid_pixel_count=row["valid_pixel_count"],
                invalid_pixel_count=row["invalid_pixel_count"],
                invalid_domain_peak_pixel_burst=row["invalid_domain_peak_pixel_burst"],
                invalid_domain_full_scale_pixel_count=row["invalid_domain_full_scale_pixel_count"],
                invalid_domain_nonfinite_pixel_count=row["invalid_domain_nonfinite_pixel_count"],
            )

            trial_idx = len(all_results) + 1
            run_status.update(
                capture_index=trial_idx,
                n_captures=total_trials,
                current_wavelength_nm=wl_nm,
                camera_exposure_us=float(at_exposure),
                camera_gain_db=float(at_gain),
            )
            run_status.write_frame_stats({
                "preview_kind": phase_label,
                "preview_frame_kind": (
                    row.get("failure_frame_kind")
                    if not bool(row["psf_safe"]) and row.get("failure_frame") is not None
                    else "averaged_frame"
                ),
                "wavelength_nm": wl_nm,
                "exposure_us": at_exposure,
                "gain_db": at_gain,
                "peak_pixel_burst": row["peak_pixel_burst"],
                "peak_pixel_avg": row["peak_pixel_avg"],
                "peak_pixel_fraction_burst": row["peak_pixel_fraction_burst"],
                "peak_margin_to_full_scale": row["peak_margin_to_full_scale"],
                "p99_9_avg": row["p99_9_avg"],
                "p_signal": row["p_signal"],
                "dynamic_range": row["dynamic_range"],
                "psf_safe": row["psf_safe"],
                "unsafe_reason": row["unsafe_reason"],
                "frame_dtype_full_scale": int(full_scale),
                "valid_pixel_domain_type": valid_domain_policy["type"],
                "valid_pixel_count": row["valid_pixel_count"],
                "invalid_pixel_count": row["invalid_pixel_count"],
                "valid_domain_peak_frame_index": row.get("valid_domain_peak_frame_index"),
                "valid_domain_peak_y": row.get("valid_domain_peak_y"),
                "valid_domain_peak_x": row.get("valid_domain_peak_x"),
                "valid_domain_full_scale_pixel_count": row.get("valid_domain_full_scale_pixel_count"),
                "valid_domain_full_scale_sample_coords": row.get("valid_domain_full_scale_sample_coords"),
                "invalid_domain_peak_pixel_burst": row["invalid_domain_peak_pixel_burst"],
                "invalid_domain_full_scale_pixel_count": row["invalid_domain_full_scale_pixel_count"],
                "invalid_domain_nonfinite_pixel_count": row["invalid_domain_nonfinite_pixel_count"],
                "shape": list(np.asarray(row["frame"]).shape),
            })
            should_write_preview = (
                trial_idx % max(1, int(status_preview_every)) == 0
                or not bool(row["psf_safe"])
            )
            if should_write_preview:
                preview_frame = (
                    row.get("failure_frame")
                    if not bool(row["psf_safe"]) and row.get("failure_frame") is not None
                    else row["frame"]
                )
                run_status.write_frame_preview(preview_frame)
            run_status.append_log(
                "INFO", "exposure trial evaluated",
                phase=phase_label,
                wavelength_nm=wl_nm,
                exposure_us=at_exposure,
                gain_db=at_gain,
                psf_safe=row["psf_safe"],
                peak_pixel_burst=row["peak_pixel_burst"],
                valid_domain_full_scale_pixel_count=row.get("valid_domain_full_scale_pixel_count"),
                valid_domain_full_scale_sample_coords=row.get("valid_domain_full_scale_sample_coords"),
            )
            if row["invalid_domain_full_scale_pixel_count"] > 0:
                run_status.append_log(
                    "WARNING",
                    "full-scale pixels observed outside valid pixel domain",
                    phase=phase_label,
                    wavelength_nm=wl_nm,
                    exposure_us=at_exposure,
                    gain_db=at_gain,
                    invalid_domain_full_scale_pixel_count=row["invalid_domain_full_scale_pixel_count"],
                    invalid_domain_peak_pixel_burst=row["invalid_domain_peak_pixel_burst"],
                )

            all_results.append({
                key: value for key, value in row.items()
                if key not in {"failure_frame"}
            })

            wl_label = f"{wl_nm}nm"
            safe_label = "SAFE" if row["psf_safe"] else f"UNSAFE: {row.get('unsafe_reason', 'unknown')}"
            sig_label = "LO" if row["low_signal"] else "ok"
            print(f"  exp={at_exposure:8.1f}  gain={at_gain:5.1f}  "
                  f"wl={wl_label:>7s}  peak_burst={row['peak_pixel_burst']:6.1f}  "
                  f"peak_avg={row['peak_pixel_avg'] or 0:6.1f}  "
                  f"peak_frac={row['peak_pixel_fraction_burst']:.4f}  "
                  f"margin={row['peak_margin_to_full_scale']:6.1f}  "
                  f"p_sig={row['p_signal']:6.1f}  [{safe_label}] [{sig_label}]")

        def _estimate_bound_for_wl(wl: dict[str, Any], R_upper: float, at_gain: float) -> tuple[float, dict]:
            wl_nm = _set_wavelength_once(wl)

            def _record_probe(at_exposure: float, row: dict[str, Any], stage: str) -> None:
                _record_candidate_row(
                    wl_nm=wl_nm,
                    at_exposure=at_exposure,
                    at_gain=at_gain,
                    row=row,
                    total_trials=None,
                    phase_label=f"bound_search:{stage}",
                )

            bound, row = _estimate_safe_bound_for_wavelength(
                camera_adapter,
                k=k, full_scale=full_scale,
                diagnostics_cfg=diagnostics_cfg, sig_cfg=sig_cfg,
                L=exposure_min, R=R_upper, gain_db=at_gain,
                valid_pixel_mask=valid_pixel_mask,
                camera_param_settle_ms=camera_param_settle_ms,
                discard_frames_after_param_change=discard_frames_after_param_change,
                eps_absolute=binary_search_eps,
                on_probe=_record_probe,
            )
            return bound, row

        def _final_verification_sweep(
            at_exposure: float, at_gain: float, *, phase_label: str = "final"
        ) -> list[dict]:
            rows: list[dict] = []
            total_trials = len(all_results) + len(wls)
            print(f"\n  [{phase_label} verification at exposure={at_exposure:.1f} gain={at_gain:.1f}]")
            for wl in wls:
                wl_nm = _set_wavelength_once(wl)
                _apply_camera_params_and_settle(
                    camera_adapter,
                    exposure_us=at_exposure,
                    gain_db=at_gain,
                    settle_ms=camera_param_settle_ms,
                    discard_frames=discard_frames_after_param_change,
                )
                row = _acquire_and_evaluate(
                    camera_adapter,
                    k,
                    full_scale,
                    diagnostics_cfg,
                    sig_cfg,
                    valid_pixel_mask=valid_pixel_mask,
                )
                _record_candidate_row(
                    wl_nm=wl_nm,
                    at_exposure=at_exposure,
                    at_gain=at_gain,
                    row=row,
                    total_trials=total_trials,
                    phase_label=phase_label,
                )
                rows.append(row)
            return rows

        def _final_verification_per_wavelength(
            bounds: dict[str, float], at_gain: float, *, phase_label: str = "final"
        ) -> list[dict]:
            rows: list[dict] = []
            total_trials = len(all_results) + len(wls)
            print(f"\n  [{phase_label} verification at independent per-wavelength bounds gain={at_gain:.1f}]")
            for wl in wls:
                wl_nm = _set_wavelength_once(wl)
                at_exposure = float(bounds[str(wl["wavelength_nm"])])
                _apply_camera_params_and_settle(
                    camera_adapter,
                    exposure_us=at_exposure,
                    gain_db=at_gain,
                    settle_ms=camera_param_settle_ms,
                    discard_frames=discard_frames_after_param_change,
                )
                row = _acquire_and_evaluate(
                    camera_adapter,
                    k,
                    full_scale,
                    diagnostics_cfg,
                    sig_cfg,
                    valid_pixel_mask=valid_pixel_mask,
                )
                _record_candidate_row(
                    wl_nm=wl_nm,
                    at_exposure=at_exposure,
                    at_gain=at_gain,
                    row=row,
                    total_trials=total_trials,
                    phase_label=phase_label,
                )
                rows.append(row)
            return rows

        def _estimate_global_for_gain(at_gain: float) -> _GainResult:
            bounds: dict[str, float] = {}
            all_safe = True
            print(f"\n---- gain={at_gain:.1f} dB  search upper bounds ----")

            for wl in wls:
                wl_nm_str = str(wl["wavelength_nm"])
                bound, _row = _estimate_bound_for_wl(wl, exposure_start, at_gain)
                bounds[wl_nm_str] = bound
                if not _row["psf_safe"]:
                    print(f"  wl={wl_nm_str}nm  FAIL: even min exposure unsafe at gain={at_gain}")
                    all_safe = False
                    break
                print(f"  wl={wl_nm_str}nm  safe_upper_bound={bound:.0f} us")

            if not all_safe:
                return _GainResult(gain_db=at_gain, psf_safe=False)

            verification_bounds = _apply_verification_exposure_backoff(
                bounds,
                exposure_min=exposure_min,
                backoff_us=verification_exposure_backoff_us,
            )
            global_exposure = min(verification_bounds.values()) if verification_bounds else 0.0

            rows = _final_verification_per_wavelength(
                verification_bounds,
                at_gain=at_gain,
                phase_label="final",
            )

            psf_safe = all(bool(r["psf_safe"]) for r in rows)
            if not psf_safe:
                return _GainResult(
                    gain_db=at_gain, exposure_us=global_exposure,
                    psf_safe=False, per_wavelength_bounds=bounds,
                )

            low_sig = any(r["low_signal"] for r in rows)
            return _GainResult(
                gain_db=at_gain, exposure_us=global_exposure,
                psf_safe=True, low_signal=low_sig,
                per_wavelength_bounds=bounds, final_rows=rows,
            )

        # ---- Lexicographic gain selection ----------------------------------
        gain_candidates = _camera_gain_candidates(plan)

        gain_min_result: _GainResult | None = None
        accepted_result: _GainResult | None = None
        safe_gain_results: list[_GainResult] = []

        for at_gain in gain_candidates:
            result = _estimate_global_for_gain(at_gain)
            if at_gain == gain_min and result.psf_safe:
                gain_min_result = result
            if not result.psf_safe:
                if at_gain == gain_min:
                    msg = (
                        f"FAIL: even min exposure ({exposure_min} us) at gain_min "
                        f"({gain_min} dB) has at least one pixel reaching full scale. "
                        f"Reduce source intensity, add ND filter, close aperture, "
                        f"or change optical alignment."
                    )
                    print(f"\n{msg}")
                    run_status.append_log("CRITICAL", msg)
                    run_status.update(error=msg, completed=False)
                    writer.finalize(completed=False, error=msg)
                    return Path(output_raw), _build_result(
                        plan, None, safe_gain_results, all_results, full_scale,
                        selection_reason="no_safe_usable_setting_found",
                        error=msg,
                        full_scale_source=full_scale_source,
                        valid_pixel_domain_policy=valid_domain_policy,
                    )
                print(f"  gain={at_gain:.1f}  SKIP: not PSF-safe at global_exposure={result.exposure_us}")
                continue

            safe_gain_results.append(result)
            if not result.low_signal:
                accepted_result = result
                safe_exposure = result.exposure_us
                safe_gain = at_gain
                selection_reason = (
                    "all_burst_pixels_below_full_scale"
                    if at_gain == gain_min
                    else "elevated_gain_due_to_low_signal"
                )
                print(f"\n  ACCEPT: exposure={safe_exposure} gain={safe_gain} "
                      f"({selection_reason})")
                break

            if at_gain == gain_min:
                print(f"  signal too weak at gain_min; trying higher gains ...")
            else:
                print(f"  safe but low signal at gain={at_gain}; trying next ...")

        else:
            if gain_min_result is not None and gain_min_result.psf_safe:
                accepted_result = gain_min_result
                safe_exposure = gain_min_result.exposure_us
                safe_gain = gain_min_result.gain_db
                selection_reason = "gain_min_psf_safe_low_signal_fallback"
                print(f"\n  WARNING: all gains safe-but-dim. "
                      f"Accepting gain_min safe exposure={safe_exposure} "
                      f"(global signal low -expected when LCD active region "
                      f"is small relative to sensor. Pupil scan (Phase 3.1) "
                      f"will determine the ROI.)")
            else:
                msg = "FAIL: no PSF-safe setting found at any gain"
                print(f"\n{msg}")
                run_status.append_log("CRITICAL", msg)
                run_status.update(error=msg, completed=False)
                writer.finalize(completed=False, error=msg)
                return Path(output_raw), _build_result(
                    plan, None, safe_gain_results, all_results, full_scale,
                    selection_reason="no_safe_usable_setting_found",
                    error=msg,
                    full_scale_source=full_scale_source,
                    valid_pixel_domain_policy=valid_domain_policy,
                )

        run_status.append_log("INFO", "PSF-safe exposure calibration complete",
                              exposure_us=safe_exposure, gain_db=safe_gain,
                              selection_reason=selection_reason)
        run_status.update(
            completed=True,
            camera_exposure_us=float(safe_exposure) if safe_exposure is not None else None,
            camera_gain_db=float(safe_gain) if safe_gain is not None else None,
        )
        writer.finalize(completed=True)

        result = _build_result(
            plan, accepted_result, safe_gain_results, all_results, full_scale,
            selection_reason=selection_reason,
            full_scale_source=full_scale_source,
            valid_pixel_domain_policy=valid_domain_policy,
        )

        json_path = _repo_root() / output_json
        json_path.parent.mkdir(parents=True, exist_ok=True)
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, default=str)
        print(f"\ncamera_params_psf_safe.json written to {json_path}")

        return Path(output_raw), result

    except Exception as exc:
        run_status.append_log("CRITICAL", str(exc))
        run_status.update(error=str(exc), completed=False)
        if writer is not None:
            try:
                writer.finalize(completed=False, error=str(exc))
            except Exception:
                pass
        raise

    finally:
        if fast_preview_pump is not None:
            fast_preview_pump.stop()
        lock.release()
        if not dry_run:
            try:
                camera_service.stop_stream()
            except Exception:
                pass


def _build_result(
    plan: dict,
    accepted: _GainResult | None,
    safe_gain_results: list[_GainResult],
    all_results: list[dict],
    full_scale: float,
    selection_reason: str | None = None,
    error: str | None = None,
    full_scale_source: str = "unknown",
    valid_pixel_domain_policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    wls = plan["wavelengths"]

    exposure_us = accepted.exposure_us if accepted else None
    gain_db = accepted.gain_db if accepted else None
    final_rows = accepted.final_rows if accepted else []
    bounds = accepted.per_wavelength_bounds if accepted else {}
    valid_domain = dict(valid_pixel_domain_policy or {"type": "full_frame"})

    wl_metrics: dict[str, dict] = {}
    for row in final_rows:
        wl_nm = str(row.get("wavelength_nm", ""))
        if wl_nm:
            valid_pixel_count = int(row.get(
                "valid_pixel_count",
                valid_domain.get("valid_pixel_count", 0),
            ))
            invalid_pixel_count = int(row.get(
                "invalid_pixel_count",
                valid_domain.get("invalid_pixel_count", 0),
            ))
            wl_metrics[wl_nm] = {
                "measured_at_exposure_us": row.get("exposure_us"),
                "measured_at_gain_db": row.get("gain_db"),
                "peak_pixel_burst": row["peak_pixel_burst"],
                "peak_pixel_avg": row.get("peak_pixel_avg"),
                "peak_pixel_fraction_burst": row["peak_pixel_fraction_burst"],
                "peak_margin_to_full_scale": row["peak_margin_to_full_scale"],
                "p99_0_avg": row.get("p99_0_avg"),
                "p99_9_avg": row.get("p99_9_avg"),
                "p_signal": row["p_signal"],
                "dynamic_range": row["dynamic_range"],
                "psf_safe": bool(row["psf_safe"]),
                "unsafe_reason": row.get("unsafe_reason"),
                "low_signal": row["low_signal"],
                "valid_pixel_count": valid_pixel_count,
                "invalid_pixel_count": invalid_pixel_count,
                "invalid_domain_peak_pixel_burst": row.get("invalid_domain_peak_pixel_burst"),
                "invalid_domain_full_scale_pixel_count": int(row.get(
                    "invalid_domain_full_scale_pixel_count", 0,
                )),
                "invalid_domain_nonfinite_pixel_count": int(row.get(
                    "invalid_domain_nonfinite_pixel_count", 0,
                )),
            }

    catalog: dict[str, dict[str, Any]] = {}
    for wl in wls:
        wl_key = format(float(wl["wavelength_nm"]), ".1f")
        safe_profiles: list[dict[str, Any]] = []
        for result in safe_gain_results:
            for row in result.final_rows:
                if format(float(row.get("wavelength_nm")), ".1f") != wl_key:
                    continue
                safe_profiles.append(
                    {
                        "profile_id": f"wl{wl_key.replace('.', 'p')}_gain{float(row['gain_db']):.1f}_near_full_scale",
                        "gain_db": float(row["gain_db"]),
                        "exposure_us": float(row["exposure_us"]),
                        "peak_pixel_burst": float(row["peak_pixel_burst"]),
                        "full_scale_margin": float(row["peak_margin_to_full_scale"]),
                        "verified": bool(row["psf_safe"]),
                        "frames_per_capture": int(plan["camera_search"]["frames_per_setting"]),
                    }
                )
        safe_profiles.sort(key=lambda item: (float(item["gain_db"]), -float(item["exposure_us"])))
        recommended = None
        if accepted is not None:
            for row in accepted.final_rows:
                if format(float(row.get("wavelength_nm")), ".1f") == wl_key:
                    recommended = {
                        "profile_id": f"wl{wl_key.replace('.', 'p')}_gain{float(row['gain_db']):.1f}_near_full_scale",
                        "gain_db": float(row["gain_db"]),
                        "exposure_us": float(row["exposure_us"]),
                        "peak_pixel_burst": float(row["peak_pixel_burst"]),
                        "full_scale_margin": float(row["peak_margin_to_full_scale"]),
                        "verified": bool(row["psf_safe"]),
                        "frames_per_capture": int(plan["camera_search"]["frames_per_setting"]),
                    }
                    break
        catalog[wl_key] = {
            "recommended": recommended,
            "safe_profiles": safe_profiles,
        }

    common_gain_keys: set[str] | None = None
    for entry in catalog.values():
        gain_keys = {format(float(item["gain_db"]), ".1f") for item in entry["safe_profiles"]}
        common_gain_keys = gain_keys if common_gain_keys is None else common_gain_keys & gain_keys
    derived_gain_key = None
    if common_gain_keys:
        derived_gain_key = sorted(common_gain_keys, key=lambda item: float(item))[0]
    if derived_gain_key is not None:
        derived_gain_db = float(derived_gain_key)
        derived_exposure = min(
            float(next(item["exposure_us"] for item in entry["safe_profiles"] if format(float(item["gain_db"]), ".1f") == derived_gain_key))
            for entry in catalog.values()
        )
        derived_from = "minimum_safe_exposure_across_wavelengths"
    else:
        derived_gain_db = float(gain_db) if gain_db is not None else None
        derived_exposure = float(exposure_us) if exposure_us is not None else None
        derived_from = "minimum_safe_exposure_across_wavelengths_without_common_gain_fallback"

    global_safe: dict[str, Any] = {
        "exposure_us": derived_exposure,
        "gain_db": derived_gain_db,
        "frames_per_capture": plan["camera_search"]["frames_per_setting"],
        "roi": None,
        "gain_elevated": (
            (derived_gain_db is not None and derived_gain_db > min(_camera_gain_candidates(plan)))
            if derived_gain_db is not None else None
        ),
        "derived_from": derived_from,
    }
    selected_psf_safe = (
        derived_exposure is not None
        and (not wl_metrics or all(bool(m.get("psf_safe")) for m in wl_metrics.values()))
    )

    result = {
        "schema_version": 2,
        "phase": "3.0.5b",
        "task": "psf_safe_camera_catalog",
        "plan_id": plan["plan_id"],
        "source_raw_capture_h5": plan["output"]["raw_h5"],
        "frame_dtype_full_scale": int(full_scale),
        "frame_dtype_full_scale_source": full_scale_source,
        "policy": {
            "safety_rule": "all_frames_all_pixels_strictly_below_full_scale_in_valid_domain",
            "wavelength_search_independent": bool(plan["camera_search"].get("wavelength_independent", True)),
            "inter_wavelength_upper_bound_inheritance": bool(plan["camera_search"].get("inherit_upper_bound_across_wavelengths", False)),
            "allow_full_scale_pixel": False,
        },
        "valid_pixel_domain": valid_domain,
        "camera_param_catalog": catalog,
        "derived_profiles": {
            "global_safe_camera": {
                "gain_db": derived_gain_db,
                "exposure_us": derived_exposure,
                "derived_from": derived_from,
                "use_case": "shared-exposure diagnostic baseline",
                "frames_per_capture": plan["camera_search"]["frames_per_setting"],
            }
        },
        "recommended_usage": {
            "phase3_1_single_wavelength_geometry": "global_safe_camera_or_wavelength_recommended",
            "phase3_2_single_wavelength_roi_repeatability": "global_safe_camera_or_wavelength_recommended",
            "phase3_3_single_wavelength_dotf": "global_safe_camera_or_wavelength_recommended",
            "phase3_4_normalized_psf_dictionary": "camera_param_catalog[wavelength].recommended",
            "phase3_6_sequential_target_capture": "camera_param_catalog[wavelength].recommended",
        },
        "global_safe_camera": global_safe,
        "wavelengths_nm": [float(w["wavelength_nm"]) for w in wls],
        "psf_safety_policy": {
            "rule": "all_frames_all_pixels_strictly_below_full_scale",
            "evaluated_on": "raw_burst_frames",
            "evaluated_domain": "valid_camera_pixel_domain",
            "allow_full_scale_pixel": False,
            "allow_non_finite_pixel": False,
            "frame_dtype_full_scale": int(full_scale),
            "frame_dtype_full_scale_source": full_scale_source,
            "valid_pixel_domain": valid_domain,
        },
        "signal_policy": {
            "percentile": plan["signal"]["percentile"],
            "min_signal_fraction_threshold": plan["signal"]["min_signal_fraction_threshold"],
            "min_dynamic_range_fraction": plan["signal"]["min_dynamic_range_fraction"],
        },
        "per_wavelength_metrics": wl_metrics,
        "search_diagnostics": {
            "binary_search_eps_us": float(plan["camera_search"].get("binary_search_eps_us", 50.0)),
            "verification_exposure_backoff_us": float(
                plan["camera_search"].get("verification_exposure_backoff_us", 0.0)
            ),
            "per_wavelength_safe_upper_bounds": bounds,
            "valid_pixel_domain_type": valid_domain["type"],
        },
        "selection_reason": selection_reason,
        "validity": {
            "exposure_safety_valid": selected_psf_safe,
            "psf_exposure_safe": selected_psf_safe,
            "scientific_calibration_valid": False,
            "training_ready": False,
        },
    }
    if error:
        result["error"] = error
    return result


def _make_fake_adapter():
    class _CalibFakeCamera:
        def __init__(self):
            self.exposure_us: float | None = None
            self.gain_db: float | None = None
            self._rng = np.random.default_rng(4242)

        def apply_camera_params(self, exposure_us=None, gain_db=None):
            if exposure_us is not None:
                self.exposure_us = float(exposure_us)
            if gain_db is not None:
                self.gain_db = float(gain_db)

        def acquire_burst(self, k: int):
            from tasks.capture_forward_dataset import CaptureFrames
            burst = self._rng.uniform(10, 200, (k, 48, 64)).astype(np.float64)
            burst[0, 24, 32] = 220.0
            avg = burst.mean(axis=0, dtype=np.float64)
            return CaptureFrames(
                burst=burst,
                frames_avg=avg,
                metadata={
                    "frame_dtype_full_scale": 255,
                    "acquisition": "burst",
                    "n": k,
                    "timestamp_ns": time.monotonic_ns(),
                },
            )

    return _CalibFakeCamera()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Phase 3.0.5b PSF-safe camera exposure/gain refinement",
    )
    parser.add_argument(
        "--plan", default="plans/bishe_psf_safe_exposure.yaml",
        help="Path to exposure sweep plan YAML",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Run without hardware (uses FakeCamera)",
    )
    parser.add_argument(
        "--camera-index", type=int, default=0,
        help="Camera device index (default 0)",
    )
    parser.add_argument(
        "--lcd-display-index", type=int, default=None,
        help="LCD display index (auto-detected if not set)",
    )
    parser.add_argument(
        "--lcd-subpixel-axis", type=int, default=None,
        help="LCD subpixel axis (0=height-tripled, 1=width-tripled)",
    )
    parser.add_argument(
        "--tls-serial", default=None,
        help="TLS serial number (if not set, TLS from env TLS_C1_SERIAL)",
    )
    parser.add_argument(
        "--allow-wavelength-labels-without-tls",
        action="store_true",
        help=(
            "Dangerous hardware override: allow plan wavelengths to be treated "
            "as labels when TLS is not configured. Use only for manual external "
            "wavelength control or fixed single-wavelength tests."
        ),
    )
    parser.add_argument(
        "--status-dir", default=None,
        help="Publish run-status files to this directory",
    )
    parser.add_argument(
        "--status-preview-every", type=int, default=1,
        help="Write frame preview every N trials (default 1)",
    )
    args = parser.parse_args()

    status_dir = Path(args.status_dir) if args.status_dir else None

    plan_path = _repo_root() / args.plan
    if not plan_path.exists():
        print(f"plan not found: {plan_path}", file=sys.stderr)
        sys.exit(1)

    plan = load_psf_safe_exposure_plan(plan_path)

    if args.dry_run:
        print("=== DRY RUN (no hardware) ===")
        run_psf_safe_exposure(plan, None, None, None, dry_run=True,
                              status_dir=status_dir,
                              status_preview_every=args.status_preview_every)
        print("Dry run complete.")
        return

    _ensure_sys_path()

    print("Connecting hardware ...")
    tls_service = None
    tls_serial = args.tls_serial or os.environ.get("TLS_C1_SERIAL")
    if tls_serial:
        try:
            from devices.tls_service import TLSService
            tls_service = TLSService(default_serial_number=tls_serial)
            status = tls_service.connect(serial_number=tls_serial)
            print(f"  TLS connected: device={status.device_id}")
        except ImportError:
            print("  TLS not available (tls_c1 not installed)")
        except Exception as e:
            print(f"  TLS connection failed: {e}")
    else:
        print("  TLS disabled: pass --tls-serial or set TLS_C1_SERIAL to move wavelength hardware")

    if tls_service is None and not args.allow_wavelength_labels_without_tls:
        print(
            "ERROR: TLS service is required for hardware PSF-safe exposure calibration. "
            "Use --tls-serial or set TLS_C1_SERIAL. For explicit manual external "
            "wavelength control, rerun with --allow-wavelength-labels-without-tls.",
            file=sys.stderr,
        )
        sys.exit(1)

    _ensure_sys_path()
    from devices.camera_service import CameraServiceClient
    from devices.lcd_service import LCDService

    camera_service = CameraServiceClient(timeout_ms=5000, auto_ensure=True)
    lcd_service = LCDService(
        display_index=args.lcd_display_index,
        subpixel_axis=args.lcd_subpixel_axis,
    )

    try:
        reply = camera_service.open_camera(index=args.camera_index, disable_trigger=True)
        print(f"  camera: {reply.get('serial')} {reply['width']}x{reply['height']}")
        print(f"  LCD: display={lcd_service._display_index} "
              f"subpixel_axis={lcd_service.subpixel_axis}")
        print()

        _, result = run_psf_safe_exposure(
            plan, camera_service, lcd_service, tls_service,
            allow_wavelength_labels_without_tls=args.allow_wavelength_labels_without_tls,
            status_dir=status_dir,
            status_preview_every=args.status_preview_every,
        )

        gsc = result.get("global_safe_camera", {})
        if gsc.get("exposure_us"):
            print(f"\nGLOBAL SAFE: exposure={gsc['exposure_us']} us  "
                  f"gain={gsc['gain_db']} dB  "
                  f"gain_elevated={gsc.get('gain_elevated')}")
            print(f"selection_reason: {result.get('selection_reason')}")
        else:
            print(f"\nFAILED: {result.get('selection_reason')}")

    finally:
        try:
            camera_service.stop_stream()
        except Exception:
            pass
        try:
            camera_service.close_camera()
        except Exception:
            pass
        camera_service.close()
        lcd_service.close()
        if tls_service:
            tls_service.close()


if __name__ == "__main__":
    main()
