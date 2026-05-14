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

    def write_frame_stats(self, stats: dict[str, Any]) -> None:
        if self._publisher is not None:
            try:
                self._publisher.write_frame_stats(stats)
            except Exception as exc:
                self.append_log("WARNING", "failed to write frame stats", error=str(exc))


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


def run_psf_safe_exposure(
    plan: dict[str, Any],
    camera_service,
    lcd_service,
    tls_service,
    *,
    dry_run: bool = False,
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

    lock = HardwareLock(plan.get("lock_file", "outputs/run_status/capture_hardware.lock"))
    if not dry_run:
        lock.acquire()

    run_status = OptionalRunStatus(status_dir, run_id=plan_id)
    writer: PsfSafeExposureWriter | None = None

    try:
        if not dry_run:
            from capture.frame_capture import FrameCaptureHelper
            from devices.frame_stream import FrameStreamClient
            from tasks.capture_forward_dataset import CameraCaptureAdapter

            frame_stream = FrameStreamClient(recv_timeout_ms=5000)
            capture_helper = FrameCaptureHelper(frame_stream)
            camera_adapter = CameraCaptureAdapter(capture_helper, camera_service)

            lcd_service.show_all_transmissive()
            time.sleep(lcd_cfg.get("settle_ms", 200) / 1000.0)

            camera_service.start_stream()
            first_frame = camera_adapter.acquire_burst(1)
            full_scale = float(
                first_frame.metadata.get("frame_dtype_full_scale")
                or plan.get("camera", {}).get("full_scale")
                or infer_full_scale(first_frame.frames_avg)
            )
        else:
            camera_adapter = _make_fake_adapter()
            full_scale = 255.0

        writer = PsfSafeExposureWriter(
            output_raw,
            plan_id=plan_id,
            frames_per_capture=k,
        )
        writer.open()
        writer.set_full_scale(int(full_scale))
        writer.write_plan_json(plan)
        if not dry_run:
            writer.write_lcd_metadata(lcd_service.get_metadata())
        else:
            writer.write_lcd_metadata({"display_index": -1, "subpixel_axis": 1, "mode": "fake"})

        run_status.update(
            plan_id=plan_id,
            phase="3.0.5b",
            capture_index=0,
            n_captures=0,
            camera_exposure_us=float(exposure_start),
            camera_gain_db=float(gain_min),
            camera_frame_dtype_full_scale=int(full_scale),
        )
        run_status.append_log("INFO", "PSF-safe exposure calibration started",
                              exposure_start_us=exposure_start, gain_min_db=gain_min)

        all_results: list[dict] = []
        selection_reason: str | None = None
        safe_exposure: float | None = None
        safe_gain: float | None = None

        def _sweep_wavelengths(at_exposure: float, at_gain: float) -> list[dict]:
            results = []
            camera_adapter.apply_camera_params(exposure_us=at_exposure, gain_db=at_gain)
            for wl in wls:
                wl_nm = float(wl["wavelength_nm"])
                if not dry_run and tls_service is not None:
                    grating = wl.get("grating")
                    if grating is not None:
                        tls_service.set_grating(int(grating))
                    tls_service.set_wavelength_nm(wl_nm)
                    tls_service.move(timeout_s=60.0)
                    tls_service.wait_until_idle(timeout_s=60.0)
                    settle = wl.get("settle_ms", 1000)
                    if settle > 0:
                        time.sleep(settle / 1000.0)

                row = _acquire_and_evaluate(
                    camera_adapter, k, full_scale, diagnostics_cfg, sig_cfg,
                )
                row["wavelength_nm"] = wl_nm
                row["exposure_us"] = at_exposure
                row["gain_db"] = at_gain
                results.append(row)

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

                # --- status publishing ---
                trial_idx = len(all_results) + 1
                run_status.update(
                    capture_index=trial_idx,
                    n_captures=trial_idx,
                    current_wavelength_nm=wl_nm,
                    camera_exposure_us=float(at_exposure),
                    camera_gain_db=float(at_gain),
                )
                run_status.write_frame_stats({
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
                })
                if trial_idx % max(1, int(status_preview_every)) == 0:
                    run_status.write_frame_preview(row["frame"])
                run_status.append_log(
                    "INFO", "exposure trial evaluated",
                    wavelength_nm=wl_nm,
                    exposure_us=at_exposure,
                    gain_db=at_gain,
                    psf_safe=row["psf_safe"],
                    peak_pixel_burst=row["peak_pixel_burst"],
                )
                # -------------------------

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
            return results

        # ---- Phase 1: gain = gain_min -------------------------------------
        print(f"\nPhase 1 -searching at gain_min={gain_min}")
        gain = gain_min
        exposure = exposure_start
        max_safe_exposure: float | None = None

        while exposure >= exposure_min:
            print(f"\n  trying exposure={exposure}")
            results = _sweep_wavelengths(exposure, gain)
            if _all_wavelengths_safe(results):
                max_safe_exposure = exposure
                break
            exposure *= step_factor

        if max_safe_exposure is None:
            msg = (
                f"FAIL: even min exposure ({exposure_min} us) at gain_min "
                f"({gain_min} dB) has at least one pixel reaching full scale "
                f"in the raw burst frames. Reduce source intensity, add ND "
                f"filter, close aperture, or change optical alignment."
            )
            print(f"\n{msg}")
            run_status.append_log("CRITICAL", msg)
            run_status.update(error=msg, completed=False)
            writer.finalize(completed=False, error=msg)
            return Path(output_raw), _build_result(
                plan, None, None, all_results, full_scale,
                selection_reason="no_safe_usable_setting_found",
                error=msg,
            )

        worst = _worst_signal_wavelength([r for r in all_results
                                           if r["exposure_us"] == max_safe_exposure
                                           and r["gain_db"] == gain])
        if not worst["low_signal"]:
            safe_exposure = max_safe_exposure
            safe_gain = gain
            selection_reason = "all_burst_pixels_below_full_scale"
            print(f"\n  ACCEPT: exposure={safe_exposure} gain={safe_gain} "
                  f"({selection_reason})")
        else:
            print(f"\n  signal too weak at gain_min: p_signal={worst['p_signal']:.1f}")
            print(f"  entering Phase 2 -elevated gain search")

            # ---- Phase 2: elevated gain --------------------------------
            found = False
            gain = gain_min + gain_step
            while gain <= gain_max:
                print(f"\nPhase 2 -trying gain={gain}")
                exposure = exposure_start
                max_safe_at_gain: float | None = None
                while exposure >= exposure_min:
                    results = _sweep_wavelengths(exposure, gain)
                    if _all_wavelengths_safe(results):
                        max_safe_at_gain = exposure
                        break
                    exposure *= step_factor

                if max_safe_at_gain is not None:
                    worst2 = _worst_signal_wavelength(
                        [r for r in all_results
                         if r["exposure_us"] == max_safe_at_gain
                         and r["gain_db"] == gain]
                    )
                    if not worst2["low_signal"]:
                        safe_exposure = max_safe_at_gain
                        safe_gain = gain
                        selection_reason = "elevated_gain_due_to_low_signal"
                        print(f"  ACCEPT: exposure={safe_exposure} gain={safe_gain} "
                              f"({selection_reason})")
                        found = True
                        break
                    else:
                        print(f"  safe but still low signal at gain={gain}")
                else:
                    print(f"  full-scale pixel encountered even at min exposure at gain={gain}")

                gain += gain_step

            if not found:
                safe_exposure = max_safe_exposure
                safe_gain = gain_min
                selection_reason = "psf_safe_gain_min_low_signal_fallback"
                print(f"\n  WARNING: all gains safe-but-dim. "
                      f"Accepting gain_min safe exposure={safe_exposure} "
                      f"(global signal low -expected when LCD active region "
                      f"is small relative to sensor. Pupil scan (Phase 3.1) "
                      f"will determine the ROI.)")

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
            plan, safe_exposure, safe_gain, all_results, full_scale,
            selection_reason=selection_reason,
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
        lock.release()
        if not dry_run:
            try:
                camera_service.stop_stream()
            except Exception:
                pass


def _build_result(
    plan: dict,
    exposure_us: float | None,
    gain_db: float | None,
    all_results: list[dict],
    full_scale: float,
    selection_reason: str | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    wls = plan["wavelengths"]

    wl_metrics: dict[str, dict] = {}
    for wl in wls:
        wl_nm = str(wl["wavelength_nm"])
        matching = [r for r in all_results if r["wavelength_nm"] == float(wl_nm)]
        if exposure_us is not None and gain_db is not None:
            selected_matching = [
                r for r in matching
                if "exposure_us" in r
                and "gain_db" in r
                and float(r["exposure_us"]) == float(exposure_us)
                and float(r["gain_db"]) == float(gain_db)
            ]
            if selected_matching:
                matching = selected_matching
        if matching:
            last = matching[-1]
            wl_metrics[wl_nm] = {
                "peak_pixel_burst": last["peak_pixel_burst"],
                "peak_pixel_avg": last.get("peak_pixel_avg"),
                "peak_pixel_fraction_burst": last["peak_pixel_fraction_burst"],
                "peak_margin_to_full_scale": last["peak_margin_to_full_scale"],
                "p99_0_avg": last.get("p99_0_avg"),
                "p99_9_avg": last.get("p99_9_avg"),
                "p_signal": last["p_signal"],
                "dynamic_range": last["dynamic_range"],
                "psf_safe": bool(last["psf_safe"]),
                "unsafe_reason": last.get("unsafe_reason"),
                "low_signal": last["low_signal"],
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
        "global_safe_camera": global_safe,
        "wavelengths_nm": [float(w["wavelength_nm"]) for w in wls],
        "psf_safety_policy": {
            "rule": "all_frames_all_pixels_strictly_below_full_scale",
            "evaluated_on": "raw_burst_frames",
            "allow_full_scale_pixel": False,
            "allow_non_finite_pixel": False,
            "frame_dtype_full_scale": int(full_scale),
        },
        "signal_policy": {
            "percentile": plan["signal"]["percentile"],
            "min_signal_fraction_threshold": plan["signal"]["min_signal_fraction_threshold"],
            "min_dynamic_range_fraction": plan["signal"]["min_dynamic_range_fraction"],
        },
        "per_wavelength_metrics": wl_metrics,
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
    from tasks.capture_forward_dataset import FakeCamera
    return FakeCamera(height=480, width=640, exposure_us=5000.0, gain_db=0.0)


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
    from devices.camera_service import CameraServiceClient
    from devices.lcd_service import LCDService

    print("Connecting hardware ...")
    camera_service = CameraServiceClient(timeout_ms=5000, auto_ensure=True)
    lcd_service = LCDService(
        display_index=args.lcd_display_index,
        subpixel_axis=args.lcd_subpixel_axis,
    )

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

    try:
        reply = camera_service.open_camera(index=args.camera_index, disable_trigger=True)
        print(f"  camera: {reply.get('serial')} {reply['width']}x{reply['height']}")
        print(f"  LCD: display={lcd_service._display_index} "
              f"subpixel_axis={lcd_service.subpixel_axis}")
        print()

        _, result = run_psf_safe_exposure(
            plan, camera_service, lcd_service, tls_service,
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
