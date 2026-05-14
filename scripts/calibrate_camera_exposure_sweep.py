#!/usr/bin/env python3
"""
Phase 3.0.5b - PSF-safe camera exposure / gain refinement.

Finds camera parameters that keep point-source PSF captures below strict
max-pixel headroom across a predefined wavelength set while preserving usable
signal strength. Global saturated fraction is recorded only as a diagnostic.

Outputs:
  data/raw/bishe_exposure_psf_safe_sweep.h5
  outputs/exposure_calibration/camera_params_psf_safe.json

Constraints:
  - Requires exclusive hardware access.  Close the monitor GUI first.
  - Always prefers gain_min.  Only elevates gain when gain_min yields
    safe-but-unusably-dim signal.
  - If even gain_min + exposure_min violates max-pixel headroom, fails
    immediately. Raising gain is never used to solve overexposure.
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


# ---------------------------------------------------------------------------
# YAML loading
# ---------------------------------------------------------------------------


def load_exposure_sweep_plan(path: str | Path) -> dict[str, Any]:
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
    assert plan.get("saturation"), "saturation section required"
    assert plan.get("signal"), "signal section required"


# ---------------------------------------------------------------------------
# Saturation / signal metrics
# ---------------------------------------------------------------------------


def compute_saturation_metrics(
    frame: np.ndarray,
    full_scale: float,
    percentile: float = 99.9,
    max_pixel_fraction_threshold: float = 0.90,
    hard_max_pixel_fraction_threshold: float = 0.98,
    saturated_pixel_count_threshold: int = 0,
) -> dict[str, Any]:
    p99_9 = float(np.percentile(frame, percentile))
    max_pixel = float(frame.max())
    saturated = frame >= full_scale
    saturated_pixel_count = int(np.count_nonzero(saturated))
    saturated_fraction = float(saturated.mean())

    max_limit = full_scale * max_pixel_fraction_threshold
    hard_max_limit = full_scale * hard_max_pixel_fraction_threshold
    hard_full_scale_hit = max_pixel >= full_scale
    hard_threshold_hit = max_pixel >= hard_max_limit
    max_threshold_hit = max_pixel >= max_limit
    saturated_count_hit = saturated_pixel_count > int(saturated_pixel_count_threshold)

    reasons: list[str] = []
    if hard_full_scale_hit:
        reasons.append("max_pixel_at_or_above_full_scale")
    if hard_threshold_hit:
        reasons.append("max_pixel_at_or_above_hard_threshold")
    if max_threshold_hit:
        reasons.append("max_pixel_at_or_above_psf_safe_threshold")
    if saturated_count_hit:
        reasons.append("saturated_pixel_count_above_threshold")

    psf_safe = not reasons

    return {
        "max_pixel": max_pixel,
        "p99_9": p99_9,
        "saturated_pixel_count": saturated_pixel_count,
        "saturated_fraction": saturated_fraction,
        "psf_safe": psf_safe,
        "safe": psf_safe,
        "saturated": not psf_safe,
        "saturation_reasons": reasons,
        "full_scale": full_scale,
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
                "Another capture task may be running, or the monitor GUI "
                "is still open.  Close the monitor GUI and delete the "
                "lock file if it is stale."
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
    sat_cfg: dict,
    sig_cfg: dict,
) -> dict[str, Any]:
    capture = camera_adapter.acquire_burst(k)
    frame = capture.frames_avg
    burst = np.asarray(getattr(capture, "burst", frame[None, :, :]), dtype=np.float64)
    sat_avg = compute_saturation_metrics(
        frame, full_scale,
        percentile=sat_cfg["percentile"],
        max_pixel_fraction_threshold=sat_cfg["max_pixel_fraction_threshold"],
        hard_max_pixel_fraction_threshold=sat_cfg["hard_max_pixel_fraction_threshold"],
        saturated_pixel_count_threshold=sat_cfg["saturated_pixel_count_threshold"],
    )
    sat_burst = compute_saturation_metrics(
        burst, full_scale,
        percentile=sat_cfg["percentile"],
        max_pixel_fraction_threshold=sat_cfg["max_pixel_fraction_threshold"],
        hard_max_pixel_fraction_threshold=sat_cfg["hard_max_pixel_fraction_threshold"],
        saturated_pixel_count_threshold=sat_cfg["saturated_pixel_count_threshold"],
    )
    sig = compute_signal_metrics(
        frame, full_scale,
        signal_percentile=sig_cfg["percentile"],
        min_signal_fraction_threshold=sig_cfg["min_signal_fraction_threshold"],
        min_dynamic_range_fraction=sig_cfg["min_dynamic_range_fraction"],
    )
    return {
        "frame": frame,
        "max_pixel": sat_burst["max_pixel"],
        "max_pixel_avg": sat_avg["max_pixel"],
        "max_pixel_burst": sat_burst["max_pixel"],
        "p99_9": sat_avg["p99_9"],
        "saturated_pixel_count": sat_burst["saturated_pixel_count"],
        "saturated_pixel_count_avg": sat_avg["saturated_pixel_count"],
        "saturated_pixel_count_burst": sat_burst["saturated_pixel_count"],
        "saturated_fraction": sat_burst["saturated_fraction"],
        "saturated_fraction_avg": sat_avg["saturated_fraction"],
        "saturated_fraction_burst": sat_burst["saturated_fraction"],
        "psf_safe": sat_burst["psf_safe"],
        "safe": sat_burst["safe"],
        "saturated": sat_burst["saturated"],
        "saturation_reasons": sat_burst["saturation_reasons"],
        "p_signal": sig["p_signal"],
        "low_signal": not sig["usable"],
    }


def _all_wavelengths_safe(results_per_wl: list[dict]) -> bool:
    return all(bool(r.get("psf_safe", not r.get("saturated", True))) for r in results_per_wl)


def _worst_signal_wavelength(results_per_wl: list[dict]) -> dict:
    return min(results_per_wl, key=lambda r: r["p_signal"])


def run_exposure_sweep(
    plan: dict[str, Any],
    camera_service,
    lcd_service,
    tls_service,
    *,
    dry_run: bool = False,
) -> tuple[Path, dict[str, Any]]:
    _ensure_sys_path()
    from tasks.exposure_sweep_h5 import ExposureSweepWriter
    from tasks.capture_forward_dataset import CameraCaptureAdapter
    from capture.frame_capture import FrameCaptureHelper
    from devices.frame_stream import FrameStreamClient

    plan_id = plan["plan_id"]
    wls = plan["wavelengths"]
    lcd_cfg = plan["lcd"]
    search_cfg = plan["camera_search"]
    sat_cfg = plan["saturation"]
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

    try:
        if not dry_run:
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

        writer = ExposureSweepWriter(
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
                    camera_adapter, k, full_scale, sat_cfg, sig_cfg,
                )
                row["wavelength_nm"] = wl_nm
                row["exposure_us"] = at_exposure
                row["gain_db"] = at_gain
                results.append(row)

                safe_flag = not row["saturated"]
                writer.append_sweep_row(
                    wavelength_nm=wl_nm,
                    exposure_us=at_exposure,
                    gain_db=at_gain,
                    frames_avg=row["frame"],
                    max_pixel=row["max_pixel"],
                    max_pixel_avg=row["max_pixel_avg"],
                    max_pixel_burst=row["max_pixel_burst"],
                    p99_9=row["p99_9"],
                    saturated_pixel_count=row["saturated_pixel_count"],
                    saturated_pixel_count_avg=row["saturated_pixel_count_avg"],
                    saturated_pixel_count_burst=row["saturated_pixel_count_burst"],
                    saturated_fraction=row["saturated_fraction"],
                    saturated_fraction_avg=row["saturated_fraction_avg"],
                    saturated_fraction_burst=row["saturated_fraction_burst"],
                    safe=safe_flag,
                    psf_safe=row["psf_safe"],
                    p_signal=row["p_signal"],
                    low_signal=row["low_signal"],
                )
                all_results.append(row)

                wl_label = f"{wl_nm}nm"
                sat_label = "SAT" if row["saturated"] else "ok"
                sig_label = "LO" if row["low_signal"] else "ok"
                print(f"  exp={at_exposure:8.1f}  gain={at_gain:5.1f}  "
                      f"wl={wl_label:>7s}  max_burst={row['max_pixel_burst']:6.1f}  "
                      f"max_avg={row['max_pixel_avg']:6.1f}  "
                      f"p99.9={row['p99_9']:6.1f}  sat_count={row['saturated_pixel_count']:4d}  "
                      f"sat_frac={row['saturated_fraction']:.4f}  "
                      f"p_sig={row['p_signal']:6.1f}  [{sat_label}] [{sig_label}]")
            return results

        # ---- Phase 1: gain = gain_min -------------------------------------
        print(f"\nPhase 1 — searching at gain_min={gain_min}")
        gain = gain_min
        exposure = exposure_start
        max_safe_exposure: float | None = None
        exposure_min_saturated = True

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
                f"({gain_min} dB) violates PSF-safe max-pixel headroom. Reduce source intensity, "
                f"add ND filter, or close aperture."
            )
            print(f"\n{msg}")
            writer.finalize(completed=False, error=msg)
            return Path(output_raw), _build_result(
                plan, None, None, all_results, full_scale,
                selection_reason="no_safe_usable_setting_found",
                error=msg,
            )

        exposure_min_saturated = False

        worst = _worst_signal_wavelength([r for r in all_results
                                           if r["exposure_us"] == max_safe_exposure
                                           and r["gain_db"] == gain])
        if not worst["low_signal"]:
            safe_exposure = max_safe_exposure
            safe_gain = gain
            selection_reason = "psf_safe_max_pixel_headroom"
            print(f"\n  ACCEPT: exposure={safe_exposure} gain={safe_gain} "
                  f"({selection_reason})")
        else:
            print(f"\n  signal too weak at gain_min: p_signal={worst['p_signal']:.1f}")
            print(f"  entering Phase 2 — elevated gain search")

            # ---- Phase 2: elevated gain --------------------------------
            found = False
            gain = gain_min + gain_step
            while gain <= gain_max:
                print(f"\nPhase 2 — trying gain={gain}")
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
                    print(f"  saturated even at min exposure at gain={gain}")

                gain += gain_step

            if not found:
                safe_exposure = max_safe_exposure
                safe_gain = gain_min
                selection_reason = "psf_safe_gain_min_low_signal_fallback"
                print(f"\n  WARNING: all gains safe-but-dim. "
                      f"Accepting gain_min safe exposure={safe_exposure} "
                      f"(global signal low — expected when LCD active region "
                      f"is small relative to sensor. Pupil scan (Phase 3.1) "
                      f"will determine the ROI.)")

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
    sat_cfg = plan["saturation"]

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
                "max_pixel": last["max_pixel"],
                "max_pixel_avg": last.get("max_pixel_avg", last["max_pixel"]),
                "max_pixel_burst": last.get("max_pixel_burst", last["max_pixel"]),
                "p99_9": last["p99_9"],
                "saturated_pixel_count": last.get("saturated_pixel_count", 0),
                "saturated_pixel_count_avg": last.get(
                    "saturated_pixel_count_avg",
                    last.get("saturated_pixel_count", 0),
                ),
                "saturated_pixel_count_burst": last.get(
                    "saturated_pixel_count_burst",
                    last.get("saturated_pixel_count", 0),
                ),
                "saturated_fraction": last["saturated_fraction"],
                "saturated_fraction_avg": last.get(
                    "saturated_fraction_avg",
                    last["saturated_fraction"],
                ),
                "saturated_fraction_burst": last.get(
                    "saturated_fraction_burst",
                    last["saturated_fraction"],
                ),
                "p_signal": last["p_signal"],
                "safe": bool(last.get("safe", not last["saturated"])),
                "psf_safe": bool(last.get("psf_safe", not last["saturated"])),
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
        "saturation_policy": {
            "percentile": sat_cfg["percentile"],
            "max_pixel_fraction_threshold": sat_cfg["max_pixel_fraction_threshold"],
            "hard_max_pixel_fraction_threshold": sat_cfg["hard_max_pixel_fraction_threshold"],
            "saturated_pixel_count_threshold": sat_cfg["saturated_pixel_count_threshold"],
            "saturated_fraction_diagnostic_only": True,
            "psf_safe_uses_burst_max_pixel": True,
            "bad_pixel_mask": None,
            "bad_pixel_mask_policy": "none; any full-scale burst pixel is unsafe",
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
        "--plan", default="plans/bishe_exposure_psf_safe_sweep.yaml",
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
    args = parser.parse_args()

    plan_path = _repo_root() / args.plan
    if not plan_path.exists():
        print(f"plan not found: {plan_path}", file=sys.stderr)
        sys.exit(1)

    plan = load_exposure_sweep_plan(plan_path)

    if args.dry_run:
        print("=== DRY RUN (no hardware) ===")
        run_exposure_sweep(plan, None, None, None, dry_run=True)
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

        _, result = run_exposure_sweep(
            plan, camera_service, lcd_service, tls_service,
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
