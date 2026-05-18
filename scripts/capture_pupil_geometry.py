#!/usr/bin/env python3
"""
Phase 3.1 - effective pupil geometry calibration capture.

This task follows the old calibrating.py physical model using the current
camera/LCD/TLS and raw-HDF5 infrastructure:

1. bright reference
2. dark reference
3. dark-bar X/Y energy profiles
4. circle center/radius fit
5. bright circular aperture radius scan on a dark background
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

from tasks.pupil_geometry_h5 import PupilGeometryWriter
from tasks.pupil_geometry_masks import (
    bar_metadata,
    circular_window_mask,
    circular_window_metadata,
    horizontal_bar_mask,
    solid_mask,
    solid_metadata,
    vertical_bar_mask,
)
from tasks.pupil_geometry_model import create_ellipse_mask, solve_aperture_from_profiles


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _ensure_sys_path() -> None:
    root = str(_repo_root())
    if root not in sys.path:
        sys.path.insert(0, root)


def _now_ns() -> int:
    return time.monotonic_ns()


class OptionalRunStatus:
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
                        "run-status publishing disabled",
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
                "Phase 3.1 pupil geometry calibration requires exclusive camera/LCD access. "
                "If the lock is stale, delete it manually after confirming no capture is running."
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


def _validate_camera_params(params: dict[str, Any], source_path: Path) -> None:
    validity = params.get("validity", {})
    if validity.get("exposure_safety_valid") is not True:
        raise ValueError(f"{source_path}: validity.exposure_safety_valid must be true")
    if validity.get("psf_exposure_safe") is not True:
        raise ValueError(f"{source_path}: validity.psf_exposure_safe must be true")
    global_safe = params.get("global_safe_camera", {})
    if global_safe.get("exposure_us") is None:
        raise ValueError(f"{source_path}: global_safe_camera.exposure_us is required")
    if global_safe.get("gain_db") is None:
        raise ValueError(f"{source_path}: global_safe_camera.gain_db is required")
    if params.get("frame_dtype_full_scale") is None:
        raise ValueError(f"{source_path}: frame_dtype_full_scale is required")
    policy = params.get("psf_safety_policy", {})
    if policy.get("rule") != "all_frames_all_pixels_strictly_below_full_scale":
        raise ValueError(
            f"{source_path}: psf_safety_policy.rule must be "
            "'all_frames_all_pixels_strictly_below_full_scale'"
        )
    if policy.get("evaluated_on") != "raw_burst_frames":
        raise ValueError(f"{source_path}: psf_safety_policy.evaluated_on must be raw_burst_frames")
    if policy.get("evaluated_domain") != "valid_camera_pixel_domain":
        raise ValueError(
            f"{source_path}: psf_safety_policy.evaluated_domain must be valid_camera_pixel_domain"
        )
    if policy.get("allow_full_scale_pixel") is not False:
        raise ValueError(f"{source_path}: psf_safety_policy.allow_full_scale_pixel must be false")
    if policy.get("allow_non_finite_pixel") is not False:
        raise ValueError(f"{source_path}: psf_safety_policy.allow_non_finite_pixel must be false")
    domain = policy.get("valid_pixel_domain")
    if not isinstance(domain, dict):
        raise ValueError(f"{source_path}: psf_safety_policy.valid_pixel_domain is required")
    if int(domain.get("valid_pixel_count", 0)) <= 0:
        raise ValueError(f"{source_path}: valid_pixel_domain.valid_pixel_count must be > 0")
    if int(domain.get("invalid_pixel_count", 0)) < 0:
        raise ValueError(f"{source_path}: valid_pixel_domain.invalid_pixel_count must be >= 0")


def load_pupil_geometry_plan(path: str | Path) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        plan = yaml.safe_load(f)
    _validate_plan(plan)
    return plan


def _validate_plan(plan: dict[str, Any]) -> None:
    if not isinstance(plan, dict):
        raise ValueError("plan must be a YAML mapping")
    for key in ("plan_id", "camera_params_source", "lcd", "calibration", "output"):
        if not plan.get(key):
            raise ValueError(f"{key} is required")
    if plan.get("calibration", {}).get("strategy") != "bar_profiles_plus_radius_scan":
        raise ValueError("calibration.strategy must be bar_profiles_plus_radius_scan")
    if not plan.get("output", {}).get("raw_h5"):
        raise ValueError("output.raw_h5 is required")
    if "exposure_us" in plan.get("camera", {}) or "gain_db" in plan.get("camera", {}):
        raise ValueError("Phase 3.1 geometry plans must use camera_params_source + camera_profile")


def resolve_geometry_camera_settings(
    plan: dict[str, Any],
    camera_params: dict[str, Any],
) -> tuple[float, float, int, float, dict[str, Any]]:
    source = str(plan["camera_params_source"])
    global_safe = camera_params["global_safe_camera"]
    profile_name = None
    used_profile = "global_safe_camera"
    fallback_used = False
    fallback_reason = None

    gain_key = plan.get("camera_gain_selection")
    if gain_key:
        per_gain = camera_params.get("per_gain_safe_params", {})
        gain_str = str(gain_key)
        match = None
        for gk in per_gain:
            if format(float(gk), ".1f") == format(float(gain_key), ".1f"):
                match = gk
                break
        if match is None and gain_str in per_gain:
            match = gain_str
        if match is not None:
            gv = per_gain[match]
            selected = {
                "exposure_us": float(gv["exposure_us"]),
                "gain_db": float(gv["gain_db"]),
                "frames_per_capture": global_safe.get("frames_per_capture", 5),
            }
            used_profile = "per_gain_safe_params:" + match
        else:
            raise ValueError(
                f"camera_gain_selection={gain_str!r} not found in per_gain_safe_params. "
                f"Available: {list(per_gain.keys())}"
            )
    else:
        profile_name = plan.get("camera_profile")
        profiles = camera_params.get("verified_camera_profiles", {})
        selected = global_safe

        if profile_name:
            if isinstance(profiles, dict) and profile_name in profiles:
                selected = profiles[profile_name]
                used_profile = str(profile_name)
            elif bool(plan.get("allow_global_safe_camera_fallback", False)):
                fallback_used = True
                fallback_reason = (
                    f"verified_camera_profiles.{profile_name} is unavailable; "
                    "using global_safe_camera because allow_global_safe_camera_fallback=true"
                )
                print(f"WARNING: {fallback_reason}", file=sys.stderr)
            else:
                raise ValueError(
                    f"camera_profile={profile_name!r} not found in verified_camera_profiles "
                    "and allow_global_safe_camera_fallback is false"
                )

    frames_per_capture = selected.get("frames_per_capture", global_safe.get("frames_per_capture"))
    if frames_per_capture is None:
        raise ValueError("frames_per_capture is required in selected camera profile or global_safe_camera")
    provenance = {
        "source": source,
        "camera_profile_requested": str(profile_name) if profile_name else plan.get("camera_gain_selection"),
        "camera_profile_used": used_profile,
        "fallback_used": bool(fallback_used),
        "fallback_reason": fallback_reason,
        "camera_params": camera_params,
    }
    return (
        float(selected["exposure_us"]),
        float(selected["gain_db"]),
        int(frames_per_capture),
        float(camera_params["frame_dtype_full_scale"]),
        provenance,
    )


def run_pupil_geometry_calibration(
    plan: dict[str, Any],
    *,
    dry_run: bool = True,
    camera_service: Any = None,
    lcd_service: Any = None,
    tls_service: Any = None,
    lcd_subpixel_axis: int | None = None,
    status_dir: Path | None = None,
    status_preview_every: int = 10,
    resume_from_h5: str | None = None,
) -> Path:
    _ensure_sys_path()
    camera_params, _camera_params_path = load_camera_params(plan["camera_params_source"])
    (
        exposure_us,
        gain_db,
        _frames_per_capture,
        frame_dtype_full_scale,
        camera_params_provenance,
    ) = resolve_geometry_camera_settings(plan, camera_params)

    output_raw = Path(plan["output"]["raw_h5"])
    if not output_raw.is_absolute():
        output_raw = _repo_root() / output_raw

    lock = HardwareLock(plan.get("lock_file", "outputs/run_status/capture_hardware.lock"))
    run_status = OptionalRunStatus(status_dir, run_id=plan["plan_id"])
    writer: PupilGeometryWriter | None = None
    capture_adapter = None

    if not dry_run:
        print("Phase 3.1 pupil geometry calibration requires exclusive camera/LCD access.")
        lock.acquire()

    try:
        if dry_run:
            lcd_meta = _dry_run_lcd_metadata(plan, lcd_subpixel_axis=lcd_subpixel_axis)
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
        lcd_meta["physical_shape"] = list(physical_shape)
        lcd_meta["subpixel_axis"] = int(subpixel_axis)

        tls_meta = _configure_tls(plan, dry_run=dry_run, tls_service=tls_service)
        run_status.update(
            current_wavelength_nm=tls_meta.get("current_wavelength_nm"),
            target_wavelength_nm=tls_meta.get("target_wavelength_nm"),
            tls_grating=tls_meta.get("grating"),
            tls_moving=bool(tls_meta.get("moving")),
        )

        if resume_from_h5 and not dry_run:
            output_raw = output_raw.parent / (output_raw.stem + "_resumed" + output_raw.suffix)
        camera_meta = {
            "exposure_us": float(exposure_us),
            "gain_db": float(gain_db),
            "frame_dtype_full_scale": float(frame_dtype_full_scale),
        }

        writer = PupilGeometryWriter(output_raw, plan_id=plan["plan_id"]).open()
        writer.write_plan_json(plan)
        writer.write_lcd_metadata(lcd_meta)
        writer.write_camera_metadata(
            metadata=camera_meta,
            camera_params_source=camera_params_provenance,
        )
        writer.write_tls_metadata(tls_meta)

        run_status.update(
            plan_id=plan["plan_id"],
            phase="3.1",
            task="pupil_geometry_calibration",
            current_stage="starting",
            capture_index=0,
            n_captures=None,
            current_mask_id=None,
            camera_profile_requested=camera_params_provenance["camera_profile_requested"],
            camera_profile_used=camera_params_provenance["camera_profile_used"],
            camera_profile_fallback_used=bool(camera_params_provenance["fallback_used"]),
            camera_exposure_us=float(exposure_us),
            camera_gain_db=float(gain_db),
            camera_frame_dtype_full_scale=int(frame_dtype_full_scale),
            lcd_display_index=int(lcd_meta.get("display_index", -1)),
            lcd_physical_shape=list(physical_shape),
            lcd_subpixel_axis=int(subpixel_axis),
        )
        run_status.append_log("INFO", "pupil geometry calibration started")

        truth = _synthetic_truth(physical_shape) if dry_run else None
        cal = plan["calibration"]
        bar_cfg = cal["bar_scan"]
        radius_cfg = cal["radius_scan"]
        settle_s = float(plan.get("lcd", {}).get("settle_ms", 0)) / 1000.0

        bright_meta = solid_metadata(
            mask_id="reference_bright_all_open",
            physical_shape=physical_shape,
            subpixel_axis=subpixel_axis,
            code=int(bar_cfg.get("bg_code", 255)),
        )
        bright_frame = _capture_mask(
            bright_meta["mask_id"],
            solid_mask(physical_shape, int(bar_cfg.get("bg_code", 255))),
            bright_meta,
            dry_run=dry_run,
            truth=truth,
            lcd_service=lcd_service,
            capture_adapter=capture_adapter,
            avg_n=int(bar_cfg.get("avg_n", 10)),
            settle_s=settle_s,
            run_status=run_status,
            stage="bright_reference",
            preview=True,
        )
        bright_index = writer.append_frame(
            mask_id=bright_meta["mask_id"],
            mask_metadata=bright_meta,
            frame_avg=bright_frame,
        )
        writer.write_bright_reference(bright_frame, frame_index=bright_index)
        bright_sum = float(np.sum(bright_frame))

        dark_meta = solid_metadata(
            mask_id="reference_dark_all_closed",
            physical_shape=physical_shape,
            subpixel_axis=subpixel_axis,
            code=int(radius_cfg.get("background_code", 0)),
        )
        dark_frame = _capture_mask(
            dark_meta["mask_id"],
            solid_mask(physical_shape, int(radius_cfg.get("background_code", 0))),
            dark_meta,
            dry_run=dry_run,
            truth=truth,
            lcd_service=lcd_service,
            capture_adapter=capture_adapter,
            avg_n=int(radius_cfg.get("avg_n", 20)),
            settle_s=settle_s,
            run_status=run_status,
            stage="dark_reference",
            preview=True,
        )
        dark_index = writer.append_frame(
            mask_id=dark_meta["mask_id"],
            mask_metadata=dark_meta,
            frame_avg=dark_frame,
        )
        writer.write_dark_reference(dark_frame, frame_index=dark_index)
        dark_sum = float(np.sum(dark_frame))

        if resume_from_h5 and not dry_run:
            print(f"Loading bar scan data from {resume_from_h5}", file=sys.stderr)
            import h5py as _h5py
            _resume_f = _h5py.File(resume_from_h5, "r")
            _resume_bs = _resume_f["bar_scan"]
            pos_x = _resume_bs["x"]["positions"][()]
            energy_x = _resume_bs["x"]["energies"][()]
            pos_y = _resume_bs["y"]["positions"][()]
            energy_y = _resume_bs["y"]["energies"][()]
            _resume_f.close()
            run_status.append_log("INFO", "bar scan data loaded from HDF5",
                                   source_h5=resume_from_h5,
                                   pos_x_n=len(pos_x), pos_y_n=len(pos_y))
            for _ax, _pos, _enr in [("x", pos_x, energy_x), ("y", pos_y, energy_y)]:
                for _i, (_p, _e) in enumerate(zip(_pos, _enr)):
                    writer.append_bar_scan(
                        axis=_ax, position=float(_p), energy=float(_e),
                        frame_index=-1, mask_metadata={"mask_id": f"bar_{_ax}_resumed_{_i:04d}"},
                    )
        else:
            pos_x, energy_x = _run_bar_axis(
                axis="x",
                plan=plan,
                physical_shape=physical_shape,
                subpixel_axis=subpixel_axis,
                bright_sum=bright_sum,
                writer=writer,
                dry_run=dry_run,
                truth=truth,
                lcd_service=lcd_service,
                capture_adapter=capture_adapter,
                run_status=run_status,
                status_preview_every=status_preview_every,
            )
            pos_y, energy_y = _run_bar_axis(
                axis="y",
                plan=plan,
                physical_shape=physical_shape,
                subpixel_axis=subpixel_axis,
                bright_sum=bright_sum,
                writer=writer,
                dry_run=dry_run,
                truth=truth,
                lcd_service=lcd_service,
                capture_adapter=capture_adapter,
                run_status=run_status,
                status_preview_every=status_preview_every,
            )

        if not resume_from_h5:
            writer.flush()
            backup_dir = _repo_root() / "outputs" / "pupil_geometry"
            backup_dir.mkdir(parents=True, exist_ok=True)
            np.save(str(backup_dir / "bar_scan_x_pos.npy"), pos_x)
            np.save(str(backup_dir / "bar_scan_x_enr.npy"), energy_x)
            np.save(str(backup_dir / "bar_scan_y_pos.npy"), pos_y)
            np.save(str(backup_dir / "bar_scan_y_enr.npy"), energy_y)
            run_status.append_log("INFO", "bar scan data flushed to disk")

        circle = solve_aperture_from_profiles(pos_x, energy_x, pos_y, energy_y)
        cx = float(circle["xc"])
        cy = float(circle["yc"])
        r_avg = float(circle["r_avg"])
        center = (cx, cy)
        run_status.append_log("INFO", "bar profile circle estimate complete", xc=cx, yc=cy, r_avg=r_avg)

        _run_radius_scan(
            plan=plan,
            physical_shape=physical_shape,
            subpixel_axis=subpixel_axis,
            center=center,
            r_avg=r_avg,
            dark_sum=dark_sum,
            writer=writer,
            dry_run=dry_run,
            truth=truth,
            lcd_service=lcd_service,
            capture_adapter=capture_adapter,
            run_status=run_status,
            status_preview_every=status_preview_every,
        )

        writer.finalize(completed=True)
        run_status.update(completed=True, error=None, current_stage="complete")
        run_status.append_log("INFO", "pupil geometry calibration complete")
        print(f"pupil geometry raw HDF5 written to {output_raw}")
        return output_raw
    except Exception as exc:
        run_status.update(completed=False, error=str(exc))
        run_status.append_log("CRITICAL", str(exc))
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


def _run_bar_axis(
    *,
    axis: str,
    plan: dict[str, Any],
    physical_shape: tuple[int, int],
    subpixel_axis: int,
    bright_sum: float,
    writer: PupilGeometryWriter,
    dry_run: bool,
    truth: dict[str, Any] | None,
    lcd_service: Any,
    capture_adapter: Any,
    run_status: OptionalRunStatus,
    status_preview_every: int,
) -> tuple[np.ndarray, np.ndarray]:
    cfg = plan["calibration"]["bar_scan"]
    h, w = physical_shape
    width = int(cfg.get("bar_width", 16))
    step = int(cfg.get("step", 4))
    avg_n = int(cfg.get("avg_n", 10))
    starts = _bar_starts(axis, physical_shape, width, step, cfg.get("scan_range"))
    positions: list[float] = []
    energies: list[float] = []
    settle_s = float(plan.get("lcd", {}).get("settle_ms", 0)) / 1000.0

    for i, start in enumerate(starts):
        if axis == "x":
            position = float(start + width / 2.0)
            mask = vertical_bar_mask(
                physical_shape,
                x0=start,
                width=width,
                bg_code=int(cfg.get("bg_code", 255)),
                bar_code=int(cfg.get("bar_code", 0)),
            )
            mask_id = f"bar_x_{i:04d}"
        else:
            position = float(start + width / 2.0)
            mask = horizontal_bar_mask(
                physical_shape,
                y0=start,
                width=width,
                bg_code=int(cfg.get("bg_code", 255)),
                bar_code=int(cfg.get("bar_code", 0)),
            )
            mask_id = f"bar_y_{i:04d}"
        meta = bar_metadata(
            mask_id=mask_id,
            axis=axis,
            position=position,
            start=start,
            width=width,
            bg_code=int(cfg.get("bg_code", 255)),
            bar_code=int(cfg.get("bar_code", 0)),
            physical_shape=physical_shape,
            subpixel_axis=subpixel_axis,
        )
        frame = _capture_mask(
            mask_id,
            mask,
            meta,
            dry_run=dry_run,
            truth=truth,
            lcd_service=lcd_service,
            capture_adapter=capture_adapter,
            avg_n=avg_n,
            settle_s=settle_s,
            run_status=run_status,
            stage=f"bar_scan_{axis}",
            preview=(i % max(1, int(status_preview_every)) == 0),
            current_position=position,
        )
        frame_index = writer.append_frame(mask_id=mask_id, mask_metadata=meta, frame_avg=frame)
        energy = float(abs(bright_sum - np.sum(frame)))
        writer.append_bar_scan(
            axis=axis,
            position=position,
            energy=energy,
            frame_index=frame_index,
            mask_metadata=meta,
        )
        positions.append(position)
        energies.append(energy)
    return np.asarray(positions, dtype=np.float64), np.asarray(energies, dtype=np.float64)


def _run_radius_scan(
    *,
    plan: dict[str, Any],
    physical_shape: tuple[int, int],
    subpixel_axis: int,
    center: tuple[float, float],
    r_avg: float,
    dark_sum: float,
    writer: PupilGeometryWriter,
    dry_run: bool,
    truth: dict[str, Any] | None,
    lcd_service: Any,
    capture_adapter: Any,
    run_status: OptionalRunStatus,
    status_preview_every: int,
) -> None:
    cfg = plan["calibration"]["radius_scan"]
    r_min = float(cfg.get("r_min_factor", 0.0)) * float(r_avg)
    r_max = float(cfg.get("r_max_factor", 2.0)) * float(r_avg)
    radii = np.linspace(r_min, r_max, int(cfg.get("steps", 200)))
    settle_s = float(plan.get("lcd", {}).get("settle_ms", 0)) / 1000.0
    for i, radius in enumerate(radii):
        mask_id = f"radius_{i:04d}"
        mask = circular_window_mask(
            physical_shape,
            center=center,
            radius=float(radius),
            bg_code=int(cfg.get("background_code", 0)),
            aperture_code=int(cfg.get("aperture_code", 255)),
        )
        meta = circular_window_metadata(
            mask_id=mask_id,
            center=center,
            radius=float(radius),
            bg_code=int(cfg.get("background_code", 0)),
            aperture_code=int(cfg.get("aperture_code", 255)),
            physical_shape=physical_shape,
            subpixel_axis=subpixel_axis,
        )
        frame = _capture_mask(
            mask_id,
            mask,
            meta,
            dry_run=dry_run,
            truth=truth,
            lcd_service=lcd_service,
            capture_adapter=capture_adapter,
            avg_n=int(cfg.get("avg_n", 20)),
            settle_s=settle_s,
            run_status=run_status,
            stage="radius_scan",
            preview=(i % max(1, int(status_preview_every)) == 0),
            current_radius=float(radius),
        )
        frame_index = writer.append_frame(mask_id=mask_id, mask_metadata=meta, frame_avg=frame)
        energy = float(abs(np.sum(frame) - dark_sum))
        writer.append_radius_scan(
            radius=float(radius),
            energy=energy,
            frame_index=frame_index,
            mask_metadata=meta,
        )


def _capture_mask(
    mask_id: str,
    mask: np.ndarray,
    meta: dict[str, Any],
    *,
    dry_run: bool,
    truth: dict[str, Any] | None,
    lcd_service: Any,
    capture_adapter: Any,
    avg_n: int,
    settle_s: float,
    run_status: OptionalRunStatus,
    stage: str,
    preview: bool,
    current_position: float | None = None,
    current_radius: float | None = None,
) -> np.ndarray:
    run_status.update(
        current_stage=stage,
        current_mask_id=mask_id,
        current_position=current_position,
        current_radius=current_radius,
    )
    if preview:
        run_status.write_mask_preview(mask)
    if dry_run:
        frame = _synthetic_frame(mask, truth)
    else:
        lcd_service.show_mono_mask(mask, mask_id=mask_id, mode="pupil_geometry")
        if settle_s > 0:
            time.sleep(settle_s)
        capture = capture_adapter.acquire_burst(int(avg_n))
        frame = np.asarray(capture.frames_avg, dtype=np.float64)
    if preview:
        run_status.write_frame_preview(frame)
    run_status.write_frame_stats({
        "min_pixel": float(np.min(frame)),
        "mean_pixel": float(np.mean(frame)),
        "p99_9": float(np.percentile(frame, 99.9)),
        "peak_pixel": float(np.max(frame)),
        "shape": list(frame.shape),
        "dtype": str(frame.dtype),
    })
    return np.asarray(frame, dtype=np.float64)


def _bar_starts(
    axis: str,
    physical_shape: tuple[int, int],
    width: int,
    step: int,
    scan_range: list[int] | tuple[int, int, int, int] | None,
) -> list[int]:
    h, w = physical_shape
    if scan_range is None:
        start, end = (0, w) if axis == "x" else (0, h)
    else:
        x_start, x_end, y_start, y_end = [int(v) for v in scan_range]
        start, end = (x_start, x_end) if axis == "x" else (y_start, y_end)
    step = max(1, int(step))
    starts = list(range(int(start), int(end), step))
    limit = w if axis == "x" else h
    starts = [min(max(0, s), max(0, limit - 1)) for s in starts]
    return starts or [0]


def _configure_tls(plan: dict[str, Any], *, dry_run: bool, tls_service: Any) -> dict[str, Any]:
    wl_cfg = plan.get("wavelength", {})
    wavelength_nm = wl_cfg.get("wavelength_nm")
    grating = wl_cfg.get("grating")
    meta = {
        "connected": False,
        "current_wavelength_nm": wavelength_nm,
        "target_wavelength_nm": wavelength_nm,
        "grating": grating,
        "moving": False,
        "timestamp_ns": _now_ns(),
    }
    if dry_run or tls_service is None or wavelength_nm is None:
        return meta
    if grating is not None:
        tls_service.set_grating(int(grating))
    tls_service.set_wavelength_nm(float(wavelength_nm))
    tls_service.move(timeout_s=60.0)
    tls_service.wait_until_idle(timeout_s=60.0)
    if wl_cfg.get("settle_ms", 0) > 0:
        time.sleep(float(wl_cfg.get("settle_ms", 0)) / 1000.0)
    status = tls_service.get_status()
    return {
        "connected": status.connected,
        "current_wavelength_nm": status.current_wavelength_nm,
        "target_wavelength_nm": status.target_wavelength_nm,
        "grating": status.grating,
        "moving": status.moving,
        "timestamp_ns": _now_ns(),
    }


def _resolve_lcd_geometry(
    plan: dict[str, Any],
    lcd_meta: dict[str, Any],
    *,
    lcd_subpixel_axis: int | None,
) -> tuple[tuple[int, int], int]:
    lcd_cfg = plan.get("lcd", {})
    physical_shape = lcd_cfg.get("physical_shape") or lcd_meta.get("physical_shape")
    subpixel_axis = lcd_cfg.get("subpixel_axis")
    if subpixel_axis is None:
        subpixel_axis = lcd_subpixel_axis if lcd_subpixel_axis is not None else lcd_meta.get("subpixel_axis")
    if physical_shape is None:
        raise ValueError("LCD physical_shape could not be inferred")
    if subpixel_axis is None:
        raise ValueError("LCD subpixel_axis could not be inferred")
    return (int(physical_shape[0]), int(physical_shape[1])), int(subpixel_axis)


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
        physical_shape = [90, 270] if int(subpixel_axis) == 1 else [270, 90]
    h, w = int(physical_shape[0]), int(physical_shape[1])
    logical_shape = [h, w // 3] if int(subpixel_axis) == 1 else [h // 3, w]
    return {
        "display_index": int(lcd_cfg.get("display_index", -1)),
        "reported_shape": [logical_shape[0], logical_shape[1], 3],
        "logical_shape": logical_shape,
        "physical_shape": [h, w],
        "subpixel_axis": int(subpixel_axis),
        "mode": "dry_run",
        "mapping_policy": "axis-aware physical mono",
    }


def _synthetic_truth(physical_shape: tuple[int, int]) -> dict[str, Any]:
    h, w = physical_shape
    center = (0.53 * w, 0.47 * h)
    a = 0.28 * min(h, w)
    b = 0.18 * min(h, w)
    pupil = create_ellipse_mask(physical_shape=physical_shape, center=center, a=a, b=b)
    return {"center": center, "a": a, "b": b, "pupil": pupil}


def _synthetic_frame(mask: np.ndarray, truth: dict[str, Any] | None) -> np.ndarray:
    if truth is None:
        raise RuntimeError("dry-run synthetic truth is required")
    pupil = np.asarray(truth["pupil"], dtype=np.float64)
    transmission = np.asarray(mask, dtype=np.float64) / 255.0
    visible = float(np.sum(transmission * pupil))
    total = max(float(np.sum(pupil)), 1.0)
    yy, xx = np.mgrid[:64, :80]
    blob = np.exp(-(((xx - 40.0) / 17.0) ** 2 + ((yy - 32.0) / 13.0) ** 2))
    base = 5.0 + 0.2 * np.sin(xx / 7.0) + 0.15 * np.cos(yy / 11.0)
    return (base + (visible / total) * 140.0 * blob).astype(np.float64)


def main() -> None:
    parser = argparse.ArgumentParser(description="Phase 3.1 effective pupil geometry capture")
    parser.add_argument("--plan", default="plans/bishe_pupil_geometry.yaml")
    parser.add_argument("--dry-run", action="store_true", help="Run without hardware")
    parser.add_argument("--hardware", action="store_true", help="Use real camera/LCD hardware")
    parser.add_argument("--camera-index", type=int, default=0)
    parser.add_argument("--lcd-display-index", type=int, default=None)
    parser.add_argument("--lcd-subpixel-axis", type=int, choices=(0, 1), default=None)
    parser.add_argument("--tls-serial", default=None)
    parser.add_argument(
        "--allow-wavelength-labels-without-tls",
        action="store_true",
        help=(
            "Dangerous hardware override: allow the plan wavelength to be treated "
            "as a label when TLS is not configured. Use only for manual external "
            "wavelength control or fixed single-wavelength tests."
        ),
    )
    parser.add_argument("--status-dir", default=None)
    parser.add_argument("--status-preview-every", type=int, default=10)
    parser.add_argument("--resume-from-h5", default=None,
                        help="load bar scan data from existing HDF5, skip bar profile acquisition")
    args = parser.parse_args()

    if args.hardware and args.dry_run:
        parser.error("--hardware and --dry-run are mutually exclusive")
    plan_path = Path(args.plan)
    if not plan_path.is_absolute():
        plan_path = _repo_root() / plan_path
    plan = load_pupil_geometry_plan(plan_path)
    status_dir = Path(args.status_dir) if args.status_dir else None

    if not args.hardware:
        print("=== DRY RUN (no hardware) ===")
        run_pupil_geometry_calibration(
            plan,
            dry_run=True,
            lcd_subpixel_axis=args.lcd_subpixel_axis,
            status_dir=status_dir,
            status_preview_every=args.status_preview_every,
        )
        print("Dry run complete.")
        return

    _ensure_sys_path()
    from devices.camera_service import CameraServiceClient
    from devices.lcd_service import LCDService

    lcd_cfg = plan.get("lcd", {})
    display_index = args.lcd_display_index if args.lcd_display_index is not None else lcd_cfg.get("display_index")
    subpixel_axis = args.lcd_subpixel_axis if args.lcd_subpixel_axis is not None else lcd_cfg.get("subpixel_axis")
    camera_service = CameraServiceClient(timeout_ms=5000, auto_ensure=True)
    lcd_service = LCDService(display_index=display_index, subpixel_axis=subpixel_axis)
    tls_service = None
    tls_serial = args.tls_serial or os.environ.get("TLS_C1_SERIAL")
    if tls_serial:
        try:
            from devices.tls_service import TLSService

            tls_service = TLSService(default_serial_number=tls_serial)
            tls_service.connect(serial_number=tls_serial)
        except ImportError:
            print("TLS not available (tls_c1 not installed)")
        except Exception as exc:
            print(f"TLS connection failed: {exc}")

    if tls_service is None and not args.allow_wavelength_labels_without_tls:
        print(
            "ERROR: TLS service is required for hardware pupil geometry calibration. "
            "Without TLS wavelength filtering the light source outputs broadband white "
            "light, which will overexpose the camera with PSF-safe parameters that "
            "assume filtered monochromatic light. "
            "Pass --tls-serial or set TLS_C1_SERIAL. For explicit manual external "
            "wavelength control, rerun with --allow-wavelength-labels-without-tls.",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        camera_service.open_camera(index=args.camera_index, disable_trigger=True)
        run_pupil_geometry_calibration(
            plan,
            dry_run=False,
            camera_service=camera_service,
            lcd_service=lcd_service,
            tls_service=tls_service,
            lcd_subpixel_axis=args.lcd_subpixel_axis,
            status_dir=status_dir,
            status_preview_every=args.status_preview_every,
            resume_from_h5=args.resume_from_h5,
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
