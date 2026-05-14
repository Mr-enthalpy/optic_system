#!/usr/bin/env python3
"""
Phase 3.1 - procedural effective LCD pupil scan capture.

Hardware mode requires exclusive camera/LCD access:
    Close any hardware-owning GUI/session before running.
    The read-only run-status monitor may remain open.

Dry-run mode is the default unless --hardware is passed. It uses no camera,
LCD, TLS, vendor SDK, or sidecar imports and writes a structurally valid raw
HDF5 file with procedural mask provenance.
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

    def write_mask_preview(self, mask: np.ndarray) -> None:
        if self._publisher is not None:
            try:
                self._publisher.write_mask_preview(mask)
            except Exception as exc:
                self.append_log("WARNING", "failed to write mask preview", error=str(exc))


class HardwareLock:
    def __init__(self, lock_path: str | Path):
        self._lock_path = Path(lock_path)
        self._acquired = False

    def acquire(self) -> None:
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        if self._lock_path.exists():
            raise RuntimeError(
                f"Hardware lock file exists: {self._lock_path}\n"
                "Phase 3.1 pupil scan requires exclusive camera/LCD access. "
                "A hardware-owning task may still be running. "
                "The read-only run-status monitor may remain open. "
                "If the lock is stale, "
                "delete it manually after confirming no capture is running."
            )
        self._lock_path.write_text(
            f"pid={os.getpid()}\nacquired_ns={_now_ns()}\n",
            encoding="utf-8",
        )
        self._acquired = True

    def release(self) -> None:
        if self._acquired and self._lock_path.exists():
            self._lock_path.unlink(missing_ok=True)
        self._acquired = False


def load_pupil_scan_plan(path: str | Path) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        plan = yaml.safe_load(f)
    _validate_plan(plan)
    return plan


def _validate_plan(plan: dict[str, Any]) -> None:
    if not isinstance(plan, dict):
        raise ValueError("plan must be a YAML mapping")
    if not plan.get("plan_id"):
        raise ValueError("plan_id is required")
    if not plan.get("camera_params_source"):
        raise ValueError("camera_params_source is required")
    if not plan.get("lcd"):
        raise ValueError("lcd section is required")
    if not plan.get("scan"):
        raise ValueError("scan section is required")
    if not plan.get("output", {}).get("raw_h5"):
        raise ValueError("output.raw_h5 is required")
    if "exposure_us" in plan.get("camera", {}) or "gain_db" in plan.get("camera", {}):
        raise ValueError(
            "Phase 3.1 plans must use camera_params_source, not handwritten "
            "camera.exposure_us or camera.gain_db"
        )


def load_camera_params(source: str | Path) -> tuple[dict[str, Any], Path]:
    source_path = Path(source)
    if not source_path.is_absolute():
        source_path = _repo_root() / source_path
    if not source_path.exists():
        raise FileNotFoundError(f"camera_params_source not found: {source_path}")
    with open(source_path, "r", encoding="utf-8") as f:
        params = json.load(f)
    _validate_camera_params(params, source_path)
    return params, source_path


def _validate_camera_params(
    params: dict[str, Any],
    source_path: Path,
) -> None:
    validity = params.get("validity", {})
    if validity.get("exposure_safety_valid") is not True:
        raise ValueError(
            f"{source_path} is not exposure-safety valid: "
            "validity.exposure_safety_valid must be true"
        )
    if validity.get("psf_exposure_safe") is not True:
        raise ValueError(
            f"{source_path} is not PSF-exposure safe: "
            "validity.psf_exposure_safe must be true"
        )
    gsc = params.get("global_safe_camera", {})
    if gsc.get("exposure_us") is None:
        raise ValueError(f"{source_path}: global_safe_camera.exposure_us is required")
    if gsc.get("gain_db") is None:
        raise ValueError(f"{source_path}: global_safe_camera.gain_db is required")
    if params.get("frame_dtype_full_scale") is None:
        raise ValueError(f"{source_path}: frame_dtype_full_scale is required")
    psf_policy = params.get("psf_safety_policy", {})
    if psf_policy.get("rule") != "all_frames_all_pixels_strictly_below_full_scale":
        raise ValueError(
            f"{source_path}: psf_safety_policy.rule must be "
            "'all_frames_all_pixels_strictly_below_full_scale'"
        )
    if psf_policy.get("allow_full_scale_pixel") is not False:
        raise ValueError(
            f"{source_path}: psf_safety_policy.allow_full_scale_pixel must be False"
        )


def resolve_camera_settings(
    plan: dict[str, Any],
    camera_params: dict[str, Any],
) -> tuple[float, float, int, float, dict[str, Any]]:
    source = str(plan["camera_params_source"])
    gsc = camera_params["global_safe_camera"]
    exposure_us = float(gsc["exposure_us"])
    gain_db = float(gsc["gain_db"])
    frames_per_capture = plan.get("camera", {}).get("frames_per_capture")
    if frames_per_capture is None:
        frames_per_capture = int(gsc["frames_per_capture"])
    frame_dtype_full_scale = float(camera_params["frame_dtype_full_scale"])

    provenance = {
        "source": source,
        "overridden": False,
        "camera_params": camera_params,
    }

    return exposure_us, gain_db, int(frames_per_capture), frame_dtype_full_scale, provenance


def run_pupil_scan(
    plan: dict[str, Any],
    *,
    dry_run: bool = True,
    camera_service: Any = None,
    lcd_service: Any = None,
    tls_service: Any = None,
    store_physical_masks: bool | None = None,
    emit_mask_files: bool = False,
    lcd_display_index: int | None = None,
    lcd_subpixel_axis: int | None = None,
    status_dir: Path | None = None,
    status_preview_every: int = 1,
) -> Path:
    _ensure_sys_path()
    from tasks.pupil_scan_h5 import PupilScanWriter
    from tasks.pupil_scan_masks import ScanMaskSpec, iter_pupil_scan_masks

    camera_params, _camera_params_path = load_camera_params(plan["camera_params_source"])
    (
        exposure_us,
        gain_db,
        frames_per_capture,
        frame_dtype_full_scale,
        camera_params_provenance,
    ) = resolve_camera_settings(plan, camera_params)

    if store_physical_masks is None:
        store_physical_masks = bool(plan.get("scan", {}).get("store_physical_masks", False))

    output_raw = Path(plan["output"]["raw_h5"])
    if not output_raw.is_absolute():
        output_raw = _repo_root() / output_raw

    lock = HardwareLock(plan.get("lock_file", "outputs/run_status/capture_hardware.lock"))
    writer: PupilScanWriter | None = None
    run_status = OptionalRunStatus(status_dir, run_id=plan["plan_id"])

    if not dry_run:
        print("Phase 3.1 pupil scan requires exclusive camera/LCD access.")
        print("Close any hardware-owning GUI/session before starting.")
        lock.acquire()

    try:
        if dry_run:
            lcd_meta = _dry_run_lcd_metadata(plan, lcd_subpixel_axis=lcd_subpixel_axis)
            capture_adapter = None
        else:
            if camera_service is None or lcd_service is None:
                raise RuntimeError("camera_service and lcd_service are required in hardware mode")
            lcd_meta = lcd_service.get_metadata()
            from capture.frame_capture import FrameCaptureHelper
            from devices.frame_stream import FrameStreamClient
            from tasks.capture_forward_dataset import CameraCaptureAdapter

            frame_stream = FrameStreamClient(recv_timeout_ms=5000)
            capture_helper = FrameCaptureHelper(frame_stream)
            capture_adapter = CameraCaptureAdapter(capture_helper, camera_service)
            capture_adapter.apply_camera_params(exposure_us=exposure_us, gain_db=gain_db)
            camera_service.start_stream()

        physical_shape, subpixel_axis = _resolve_lcd_geometry(
            plan,
            lcd_meta,
            lcd_subpixel_axis=lcd_subpixel_axis,
        )
        if dry_run:
            lcd_meta["physical_shape"] = list(physical_shape)
            lcd_meta["subpixel_axis"] = subpixel_axis

        spec = ScanMaskSpec(
            physical_shape=physical_shape,
            subpixel_axis=subpixel_axis,
            scan_modes=list(plan["scan"]["scan_modes"]),
            active_code=int(plan["scan"].get("active_code", 255)),
            background_code=int(plan["scan"].get("background_code", 0)),
            bar_count=int(plan["scan"].get("bar_count", 40)),
            bar_width=plan["scan"].get("bar_width"),
            block_rows=int(plan["scan"].get("block_rows", 20)),
            block_cols=int(plan["scan"].get("block_cols", 20)),
            aperture_grid_rows=int(plan["scan"].get("aperture_grid_rows", 10)),
            aperture_grid_cols=int(plan["scan"].get("aperture_grid_cols", 10)),
            aperture_radius=plan["scan"].get("aperture_radius"),
            include_baselines=bool(plan["scan"].get("include_baselines", True)),
        )

        wl_cfg = plan.get("wavelength", {})
        wavelength_nm = wl_cfg.get("wavelength_nm")
        grating = wl_cfg.get("grating")
        tls_status = {
            "connected": False,
            "current_wavelength_nm": wavelength_nm,
            "target_wavelength_nm": wavelength_nm,
            "grating": grating,
            "moving": False,
            "timestamp_ns": _now_ns(),
        }
        if not dry_run and tls_service is not None and wavelength_nm is not None:
            if grating is not None:
                tls_service.set_grating(int(grating))
            tls_service.set_wavelength_nm(float(wavelength_nm))
            tls_service.move(timeout_s=60.0)
            tls_service.wait_until_idle(timeout_s=60.0)
            if wl_cfg.get("settle_ms", 0) > 0:
                time.sleep(float(wl_cfg.get("settle_ms", 0)) / 1000.0)
            status_obj = tls_service.get_status()
            tls_status = {
                "connected": status_obj.connected,
                "current_wavelength_nm": status_obj.current_wavelength_nm,
                "target_wavelength_nm": status_obj.target_wavelength_nm,
                "grating": status_obj.grating,
                "moving": status_obj.moving,
                "timestamp_ns": _now_ns(),
            }

        writer = PupilScanWriter(
            output_raw,
            plan_id=plan["plan_id"],
            store_physical_masks=store_physical_masks,
        )
        writer.open()
        writer.write_plan_json(plan)
        writer.write_lcd_metadata(lcd_meta)
        writer.write_camera_metadata(
            exposure_us=exposure_us,
            gain_db=gain_db,
            frame_dtype_full_scale=frame_dtype_full_scale,
            camera_params_source=camera_params_provenance,
        )
        writer.write_tls_metadata(
            wavelength_nm=float(wavelength_nm) if wavelength_nm is not None else None,
            grating=int(grating) if grating is not None else None,
            status=tls_status,
        )

        run_status.update(
            plan_id=plan["plan_id"],
            phase="3.1",
            n_captures=0,
            current_wavelength_nm=float(wavelength_nm) if wavelength_nm else None,
            camera_exposure_us=float(exposure_us),
            camera_gain_db=float(gain_db),
            camera_frame_dtype_full_scale=int(frame_dtype_full_scale),
            lcd_display_index=int(lcd_meta.get("display_index", -1)),
            lcd_physical_shape=list(lcd_phy) if (lcd_phy := lcd_meta.get("physical_shape")) else None,
            lcd_logical_shape=list(lcd_meta.get("logical_shape", [])) if lcd_meta.get("logical_shape") else None,
            lcd_subpixel_axis=int(subpixel_axis),
        )
        run_status.append_log("INFO", "pupil scan started",
                              scan_modes=plan.get("scan", {}).get("scan_modes"))

        emit_dir = None
        if emit_mask_files:
            emit_dir = Path(plan["output"].get("output_dir", "outputs/pupil_scan")) / "emitted_masks"
            if not emit_dir.is_absolute():
                emit_dir = _repo_root() / emit_dir
            emit_dir.mkdir(parents=True, exist_ok=True)

        mask_list = list(iter_pupil_scan_masks(spec))
        total = len(mask_list)
        run_status.update(capture_index=0, n_captures=total)

        n = 0
        for mask_id, mask, meta in mask_list:
            if emit_dir is not None:
                np.save(str(emit_dir / f"{mask_id}.npy"), mask)

            # --- status: mask identity before LCD show ---
            run_status.update(
                capture_index=n + 1,
                current_mask_id=mask_id,
            )
            if (n + 1) % max(1, int(status_preview_every)) == 0:
                run_status.write_mask_preview(mask)
            # ---------------------------------------------

            if dry_run:
                frame_avg = _synthetic_pupil_frame(meta, physical_shape)
            else:
                lcd_service.show_mono_mask(mask, mask_id=mask_id, mode="pupil_scan")
                if plan.get("lcd", {}).get("settle_ms", 0) > 0:
                    time.sleep(float(plan["lcd"]["settle_ms"]) / 1000.0)
                assert capture_adapter is not None
                capture = capture_adapter.acquire_burst(frames_per_capture)
                frame_avg = capture.frames_avg

            # --- status: frame preview after capture ---
            if (n + 1) % max(1, int(status_preview_every)) == 0:
                run_status.write_frame_preview(frame_avg)
            run_status.write_frame_stats({
                "max_pixel": float(frame_avg.max()),
                "min_pixel": float(frame_avg.min()),
                "mean_pixel": float(frame_avg.mean()),
                "p99_9": float(np.percentile(frame_avg, 99.9)),
                "peak_pixel": float(frame_avg.max()),
                "shape": list(frame_avg.shape),
                "dtype": str(frame_avg.dtype),
                "frame_dtype_full_scale": int(frame_dtype_full_scale),
            })
            # ---------------------------------------------

            writer.append_capture(
                mask_id=mask_id,
                mask_metadata=meta,
                frames_avg=frame_avg,
                physical_mask=mask if store_physical_masks else None,
            )
            n += 1

            if n % 25 == 0:
                print(f"  captured {n} pupil-scan masks")

        run_status.append_log("INFO", "pupil scan complete", n_captures=n)
        run_status.update(completed=True)
        writer.finalize(completed=True)
        print(f"pupil scan raw HDF5 written to {output_raw}")
        return output_raw

    except Exception as exc:
        run_status.append_log("CRITICAL", str(exc))
        run_status.update(error=str(exc), completed=False)
        if writer is not None:
            writer.finalize(completed=False, error=str(exc))
        raise
    finally:
        if not dry_run:
            lock.release()
            if camera_service is not None:
                try:
                    camera_service.stop_stream()
                except Exception:
                    pass


def _resolve_lcd_geometry(
    plan: dict[str, Any],
    lcd_meta: dict[str, Any],
    *,
    lcd_subpixel_axis: int | None,
) -> tuple[tuple[int, int], int]:
    lcd_cfg = plan.get("lcd", {})
    physical_shape = lcd_cfg.get("physical_shape")
    subpixel_axis = lcd_cfg.get("subpixel_axis")

    if physical_shape is None:
        physical_shape = lcd_meta.get("physical_shape")
    if subpixel_axis is None:
        subpixel_axis = lcd_subpixel_axis
    if subpixel_axis is None:
        subpixel_axis = lcd_meta.get("subpixel_axis")

    if physical_shape is None:
        raise ValueError("LCD physical_shape could not be inferred")
    if subpixel_axis is None:
        raise ValueError("LCD subpixel_axis could not be inferred")

    h, w = int(physical_shape[0]), int(physical_shape[1])
    return (h, w), int(subpixel_axis)


def _dry_run_lcd_metadata(
    plan: dict[str, Any],
    *,
    lcd_subpixel_axis: int | None,
) -> dict[str, Any]:
    lcd_cfg = plan.get("lcd", {})
    subpixel_axis = lcd_cfg.get("subpixel_axis")
    if subpixel_axis is None:
        subpixel_axis = lcd_subpixel_axis if lcd_subpixel_axis is not None else 1

    physical_shape = lcd_cfg.get("physical_shape")
    if physical_shape is None:
        physical_shape = [60, 180] if int(subpixel_axis) == 1 else [180, 60]

    h, w = int(physical_shape[0]), int(physical_shape[1])
    logical_shape = [h, w // 3] if int(subpixel_axis) == 1 else [h // 3, w]
    return {
        "display_index": -1,
        "reported_shape": [logical_shape[0], logical_shape[1], 3],
        "logical_shape": logical_shape,
        "physical_shape": [h, w],
        "subpixel_axis": int(subpixel_axis),
        "mode": "dry_run",
        "mapping_policy": "axis-aware physical mono",
    }


def _synthetic_pupil_frame(
    mask_meta: dict[str, Any],
    physical_shape: tuple[int, int],
) -> np.ndarray:
    """
    Synthetic camera response for dry-run.

    The active LCD region is a fixed central rectangle in physical
    coordinates. Response strength depends on how much the generated mask
    overlaps that region. The output frame is camera-like but small enough for
    fast tests.
    """

    h, w = physical_shape
    roi = {
        "x_min": int(w * 0.25),
        "x_max": int(w * 0.75),
        "y_min": int(h * 0.22),
        "y_max": int(h * 0.78),
    }
    mode = mask_meta.get("mode")
    if mask_meta.get("baseline") == "baseline_all_open":
        overlap = 1.0
    elif mask_meta.get("baseline") == "baseline_all_closed":
        overlap = 0.0
    elif mode in ("bars_x", "bars_y", "blocks", "apertures"):
        overlap = _rect_iou_like(mask_meta, roi)
    else:
        overlap = 0.0

    yy, xx = np.mgrid[:96, :128]
    blob = np.exp(-(((xx - 64.0) / 28.0) ** 2 + ((yy - 48.0) / 20.0) ** 2))
    base = 8.0 + 2.0 * np.sin(xx / 17.0) + 1.5 * np.cos(yy / 13.0)
    return (base + overlap * 120.0 * blob).astype(np.float64)


def _rect_iou_like(mask_meta: dict[str, Any], roi: dict[str, int]) -> float:
    x0 = int(mask_meta.get("x_min", 0))
    x1 = int(mask_meta.get("x_max", 0))
    y0 = int(mask_meta.get("y_min", 0))
    y1 = int(mask_meta.get("y_max", 0))
    ix0 = max(x0, roi["x_min"])
    ix1 = min(x1, roi["x_max"])
    iy0 = max(y0, roi["y_min"])
    iy1 = min(y1, roi["y_max"])
    inter = max(0, ix1 - ix0) * max(0, iy1 - iy0)
    area = max(1, (x1 - x0) * (y1 - y0))
    roi_area = max(1, (roi["x_max"] - roi["x_min"]) * (roi["y_max"] - roi["y_min"]))
    if mask_meta.get("mode") in ("bars_x", "bars_y"):
        return float(inter / area)
    return float(inter / min(area, roi_area))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Phase 3.1 procedural LCD pupil scan capture",
    )
    parser.add_argument("--plan", default="plans/bishe_pupil_scan.yaml")
    parser.add_argument("--dry-run", action="store_true", help="Run without hardware")
    parser.add_argument("--hardware", action="store_true", help="Use real camera/LCD hardware")
    parser.add_argument("--camera-index", type=int, default=0)
    parser.add_argument("--lcd-display-index", type=int, default=None)
    parser.add_argument("--lcd-subpixel-axis", type=int, choices=(0, 1), default=None)
    parser.add_argument("--tls-serial", default=None)
    parser.add_argument("--store-physical-masks", action="store_true")
    parser.add_argument("--emit-mask-files", action="store_true")
    parser.add_argument("--status-dir", default=None, help="Publish run-status files to this directory")
    parser.add_argument("--status-preview-every", type=int, default=1, help="Write preview every N masks")
    args = parser.parse_args()

    if args.hardware and args.dry_run:
        parser.error("--hardware and --dry-run are mutually exclusive")

    plan_path = Path(args.plan)
    if not plan_path.is_absolute():
        plan_path = _repo_root() / plan_path
    plan = load_pupil_scan_plan(plan_path)

    dry_run = not args.hardware
    status_dir = Path(args.status_dir) if args.status_dir else None
    if dry_run:
        print("=== DRY RUN (no hardware) ===")
        run_pupil_scan(
            plan,
            dry_run=True,
            store_physical_masks=args.store_physical_masks or None,
            emit_mask_files=args.emit_mask_files,
            lcd_subpixel_axis=args.lcd_subpixel_axis,
            status_dir=status_dir,
            status_preview_every=args.status_preview_every,
        )
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
        except Exception as exc:
            print(f"  TLS connection failed: {exc}")

    try:
        reply = camera_service.open_camera(index=args.camera_index, disable_trigger=True)
        print(f"  camera: {reply.get('serial')} {reply['width']}x{reply['height']}")
        lcd_meta = lcd_service.get_metadata()
        print(
            f"  LCD: display={lcd_meta.get('display_index')} "
            f"subpixel_axis={lcd_meta.get('subpixel_axis')} "
            f"physical_shape={lcd_meta.get('physical_shape')}"
        )
        run_pupil_scan(
            plan,
            dry_run=False,
            camera_service=camera_service,
            lcd_service=lcd_service,
            tls_service=tls_service,
            store_physical_masks=args.store_physical_masks or None,
            emit_mask_files=args.emit_mask_files,
            lcd_display_index=args.lcd_display_index,
            lcd_subpixel_axis=args.lcd_subpixel_axis,
            status_dir=status_dir,
            status_preview_every=args.status_preview_every,
        )
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
        if tls_service is not None:
            tls_service.close()


if __name__ == "__main__":
    main()
