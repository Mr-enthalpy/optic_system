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
  - Current thesis-branch selection is discrete and lexicographic, not
    continuous joint optimization: strict PSF safety across all wavelengths,
    then gain_min preference, then largest usable exposure at that gain, with
    higher gain only as a low-signal fallback.
  - The selected camera parameters are global across wavelengths.  Per-
    wavelength camera parameters are out of scope for Phase 3.0.5b because
    Phase 3.1+ captures need comparable camera response conditions.
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
    diagnostic_percentiles: tuple[float, ...] = (99.0, 99.9),
) -> dict[str, Any]:
    burst_arr = np.asarray(burst, dtype=np.float64)
    finite = np.isfinite(burst_arr).all()
    peak_pixel_burst = float(np.max(burst_arr)) if finite else float("inf")

    psf_safe = finite and peak_pixel_burst < float(full_scale)
    unsafe_reason: str | None = None
    if not finite:
        unsafe_reason = "non_finite_pixel"
    elif peak_pixel_burst >= float(full_scale):
        unsafe_reason = "peak_pixel_at_or_above_full_scale"

    peak_pixel_avg: float | None = None
    p99_0_avg: float | None = None
    p99_9_avg: float | None = None
    if avg_frame is not None:
        avg = np.asarray(avg_frame, dtype=np.float64)
        peak_pixel_avg = float(np.max(avg))
        for pct in diagnostic_percentiles:
            val = float(np.percentile(avg, pct))
            if pct == 99.0:
                p99_0_avg = val
            elif pct == 99.9:
                p99_9_avg = val

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
    }


def compute_signal_metrics(
    frame: np.ndarray,
    full_scale: float,
    signal_percentile: float = 99.0,
    min_signal_fraction_threshold: float = 0.10,
    min_dynamic_range_fraction: float = 0.08,
) -> dict[str, Any]:
    p_signal = float(np.percentile(frame, signal_percentile))
    p1 = float(np.percentile(frame, 1.0))
    dynamic_range = p_signal - p1

    usable = (
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
) -> dict[str, Any]:
    capture = camera_adapter.acquire_burst(k)
    avg = capture.frames_avg
    burst = np.asarray(getattr(capture, "burst", avg[None, :, :]), dtype=np.float64)

    pcts = tuple(float(p) for p in diagnostics_cfg.get("percentiles", [99.0, 99.9]))
    safety = compute_peak_safety_metrics(
        burst, full_scale, avg_frame=avg, diagnostic_percentiles=pcts,
    )
    sig = compute_signal_metrics(
        avg, full_scale,
        signal_percentile=sig_cfg["percentile"],
        min_signal_fraction_threshold=sig_cfg["min_signal_fraction_threshold"],
        min_dynamic_range_fraction=sig_cfg["min_dynamic_range_fraction"],
    )
    return {
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
    }


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
    eps_absolute: float = 50.0,
    max_iter: int = 30,
    on_probe: Any | None = None,
) -> tuple[float, dict[str, Any]]:
    R = float(R)
    L = float(L)

    camera_adapter.apply_camera_params(exposure_us=R, gain_db=gain_db)
    row = _acquire_and_evaluate(camera_adapter, k, full_scale, diagnostics_cfg, sig_cfg)
    if on_probe is not None:
        on_probe(R, row, "bracket_upper")
    if row["psf_safe"]:
        return R, row

    camera_adapter.apply_camera_params(exposure_us=L, gain_db=gain_db)
    row = _acquire_and_evaluate(camera_adapter, k, full_scale, diagnostics_cfg, sig_cfg)
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
        camera_adapter.apply_camera_params(exposure_us=mid, gain_db=gain_db)
        row = _acquire_and_evaluate(camera_adapter, k, full_scale, diagnostics_cfg, sig_cfg)
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
            lcd_metadata = lcd_service.get_metadata()
        else:
            camera_adapter = _make_fake_adapter()
            full_scale, full_scale_source = _resolve_dry_run_full_scale(plan)
            lcd_metadata = {
                "display_index": -1,
                "subpixel_axis": 1,
                "logical_shape": (8, 8),
                "physical_shape": (8, 24),
                "mode": "fake",
            }
            current_mask_preview = np.full((8, 24), 255, dtype=np.uint8)

        writer = PsfSafeExposureWriter(
            output_raw,
            plan_id=plan_id,
            frames_per_capture=k,
        )
        writer.open()
        writer.set_full_scale(int(full_scale), source=full_scale_source)
        writer.write_plan_json(plan)
        writer.write_lcd_metadata(lcd_metadata)

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
        if current_mask_preview is not None:
            run_status.write_mask_preview(current_mask_preview)
        if initial_frame_preview is not None:
            run_status.write_frame_preview(initial_frame_preview)
            run_status.write_frame_stats({
                "preview_kind": "initial_camera_frame",
                "peak_pixel_avg": float(np.max(initial_frame_preview)),
                "frame_dtype_full_scale": int(full_scale),
                "shape": list(np.asarray(initial_frame_preview).shape),
            })
        run_status.append_log("INFO", "PSF-safe exposure calibration started",
                              exposure_start_us=exposure_start, gain_min_db=gain_min)
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
                "shape": list(np.asarray(row["frame"]).shape),
            })
            should_write_preview = (
                trial_idx % max(1, int(status_preview_every)) == 0
                or not bool(row["psf_safe"])
            )
            if should_write_preview:
                run_status.write_frame_preview(row["frame"])
            run_status.append_log(
                "INFO", "exposure trial evaluated",
                phase=phase_label,
                wavelength_nm=wl_nm,
                exposure_us=at_exposure,
                gain_db=at_gain,
                psf_safe=row["psf_safe"],
                peak_pixel_burst=row["peak_pixel_burst"],
            )

            all_results.append(row)

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
                camera_adapter.apply_camera_params(exposure_us=at_exposure, gain_db=at_gain)
                row = _acquire_and_evaluate(
                    camera_adapter, k, full_scale, diagnostics_cfg, sig_cfg,
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
            R_upper = exposure_start
            all_safe = True
            print(f"\n---- gain={at_gain:.1f} dB  search upper bounds ----")

            for wl in wls:
                wl_nm_str = str(wl["wavelength_nm"])
                bound, _row = _estimate_bound_for_wl(wl, R_upper, at_gain)
                bounds[wl_nm_str] = bound
                if not _row["psf_safe"]:
                    print(f"  wl={wl_nm_str}nm  FAIL: even min exposure unsafe at gain={at_gain}")
                    all_safe = False
                    break
                print(f"  wl={wl_nm_str}nm  safe_upper_bound={bound:.0f} us")
                R_upper = bound

            if not all_safe:
                return _GainResult(gain_db=at_gain, psf_safe=False)

            global_exposure = min(bounds.values()) if bounds else 0.0

            rows = _final_verification_sweep(
                at_exposure=global_exposure, at_gain=at_gain,
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
        gain_candidates: list[float] = [gain_min]
        g = gain_min + gain_step
        while g <= gain_max:
            gain_candidates.append(g)
            g += gain_step

        gain_min_result: _GainResult | None = None
        accepted_result: _GainResult | None = None

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
                        plan, None, all_results, full_scale,
                        selection_reason="no_safe_usable_setting_found",
                        error=msg,
                        full_scale_source=full_scale_source,
                    )
                print(f"  gain={at_gain:.1f}  SKIP: not PSF-safe at global_exposure={result.exposure_us}")
                continue

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
                    plan, None, all_results, full_scale,
                    selection_reason="no_safe_usable_setting_found",
                    error=msg,
                    full_scale_source=full_scale_source,
                )

        run_status.append_log("INFO", "PSF-safe exposure calibration complete",
                              exposure_us=safe_exposure, gain_db=safe_gain,
                              selection_reason=selection_reason)
        run_status.update(
            completed=True,
            camera_exposure_us=float(safe_exposure) if safe_exposure else None,
            camera_gain_db=float(safe_gain) if safe_gain else None,
        )
        writer.finalize(completed=True)

        result = _build_result(
            plan, accepted_result, all_results, full_scale,
            selection_reason=selection_reason,
            full_scale_source=full_scale_source,
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
    all_results: list[dict],
    full_scale: float,
    selection_reason: str | None = None,
    error: str | None = None,
    full_scale_source: str = "unknown",
) -> dict[str, Any]:
    wls = plan["wavelengths"]

    exposure_us = accepted.exposure_us if accepted else None
    gain_db = accepted.gain_db if accepted else None
    final_rows = accepted.final_rows if accepted else []
    bounds = accepted.per_wavelength_bounds if accepted else {}

    wl_metrics: dict[str, dict] = {}
    for row in final_rows:
        wl_nm = str(row.get("wavelength_nm", ""))
        if wl_nm:
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
            }

    global_safe: dict[str, Any] = {
        "exposure_us": exposure_us,
        "gain_db": gain_db,
        "frames_per_capture": plan["camera_search"]["frames_per_setting"],
        "roi": None,
        "gain_elevated": (
            (gain_db is not None and gain_db > plan["camera_search"]["gain_db_min"])
            if gain_db is not None else None
        ),
    }
    selected_psf_safe = (
        exposure_us is not None
        and (not wl_metrics or all(bool(m.get("psf_safe")) for m in wl_metrics.values()))
    )

    result = {
        "schema_version": "1.0",
        "plan_id": plan["plan_id"],
        "source_raw_capture_h5": plan["output"]["raw_h5"],
        "frame_dtype_full_scale": int(full_scale),
        "frame_dtype_full_scale_source": full_scale_source,
        "global_safe_camera": global_safe,
        "wavelengths_nm": [float(w["wavelength_nm"]) for w in wls],
        "psf_safety_policy": {
            "rule": "all_frames_all_pixels_strictly_below_full_scale",
            "evaluated_on": "raw_burst_frames",
            "allow_full_scale_pixel": False,
            "allow_non_finite_pixel": False,
            "frame_dtype_full_scale": int(full_scale),
            "frame_dtype_full_scale_source": full_scale_source,
        },
        "signal_policy": {
            "percentile": plan["signal"]["percentile"],
            "min_signal_fraction_threshold": plan["signal"]["min_signal_fraction_threshold"],
            "min_dynamic_range_fraction": plan["signal"]["min_dynamic_range_fraction"],
        },
        "per_wavelength_metrics": wl_metrics,
        "search_diagnostics": {
            "binary_search_eps_us": float(plan["camera_search"].get("binary_search_eps_us", 50.0)),
            "per_wavelength_safe_upper_bounds": bounds,
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
