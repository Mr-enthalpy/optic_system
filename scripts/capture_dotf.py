#!/usr/bin/env python3
"""Phase 3.3 dOTF diagnostic raw capture."""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _ensure_sys_path() -> None:
    root = str(_repo_root())
    if root not in sys.path:
        sys.path.insert(0, root)


_ensure_sys_path()

from scripts.capture_psf_repeatability import _apply_pupil_shape_to_dry_lcd_meta  # noqa: E402
from scripts.capture_psf_roi import _close_hardware, _connect_tls, _frame_stats  # noqa: E402
from scripts.capture_pupil_geometry import (  # noqa: E402
    HardwareLock,
    OptionalRunStatus,
    _configure_tls,
    _dry_run_lcd_metadata,
    _resolve_lcd_geometry,
    load_camera_params,
    resolve_geometry_camera_settings,
)
from tasks.dotf_phase3 import DotfRawWriter, dotf_edge_perturbation_mask, dotf_reference_mask  # noqa: E402
from tasks.psf_phase3 import crop_frame, load_psf_roi, load_pupil_window, load_yaml_plan, validate_phase32_plan  # noqa: E402


def _resolve_repo_path(path: str | Path) -> Path:
    p = Path(path)
    return p if p.is_absolute() else _repo_root() / p


def run_capture_dotf(
    plan: dict[str, Any],
    *,
    dry_run: bool,
    camera_service: Any = None,
    lcd_service: Any = None,
    tls_service: Any = None,
    lcd_subpixel_axis: int | None = None,
    status_dir: Path | None = None,
    status_preview_every: int = 1,
) -> Path:
    validate_phase32_plan(plan, task="dotf", hardware=not dry_run)
    output_raw = _resolve_repo_path(plan["output"]["raw_h5"])
    if not dry_run and output_raw.exists():
        raise FileExistsError(
            f"Refusing to overwrite existing hardware raw HDF5: {output_raw}. "
            "Rename the existing file or update output.raw_h5 in the plan."
        )
    camera_params, _ = load_camera_params(plan["camera_params_source"])
    wavelength_entries = _dotf_wavelength_entries(plan)
    wavelength_runs = []
    for wavelength_index, wl_cfg in enumerate(wavelength_entries):
        wavelength_nm = float(wl_cfg["wavelength_nm"])
        exposure_us, gain_db, _profile_frames, full_scale, camera_profile = resolve_geometry_camera_settings(
            plan,
            camera_params,
            wavelength_nm=wavelength_nm,
            task_name="dotf",
        )
        wavelength_runs.append(
            {
                "wavelength_index": int(wavelength_index),
                "wavelength_nm": float(wavelength_nm),
                "grating": wl_cfg.get("grating"),
                "settle_ms": wl_cfg.get("settle_ms", 0),
                "exposure_us": float(exposure_us),
                "gain_db": float(gain_db),
                "full_scale": float(full_scale),
                "camera_profile": camera_profile,
            }
        )
    full_scale = float(wavelength_runs[0]["full_scale"])
    if any(abs(float(item["full_scale"]) - full_scale) > 1e-9 for item in wavelength_runs[1:]):
        raise ValueError("all Phase 3.3 wavelength profiles must share the same frame_dtype_full_scale")
    pupil_window = load_pupil_window(_resolve_repo_path(plan["pupil_window_source"]))
    psf_roi = load_psf_roi(_resolve_repo_path(plan["psf_roi_source"]))
    lock = HardwareLock(_resolve_repo_path(plan.get("lock_file", "outputs/run_status/capture_hardware.lock")))
    run_status = OptionalRunStatus(status_dir, run_id=plan["plan_id"])
    capture_adapter = None
    writer: DotfRawWriter | None = None

    if not dry_run:
        print("Phase 3.3 dOTF capture requires exclusive camera/LCD access.")
        lock.acquire()

    try:
        if dry_run:
            lcd_meta = _dry_run_lcd_metadata(plan, lcd_subpixel_axis=lcd_subpixel_axis)
            if not plan.get("lcd", {}).get("physical_shape"):
                _apply_pupil_shape_to_dry_lcd_meta(lcd_meta, pupil_window)
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
            camera_service.start_stream()

        physical_shape, subpixel_axis = _resolve_lcd_geometry(plan, lcd_meta, lcd_subpixel_axis=lcd_subpixel_axis)
        _assert_pupil_shape_matches_lcd(pupil_window, physical_shape)
        lcd_meta["physical_shape"] = list(physical_shape)
        lcd_meta["subpixel_axis"] = int(subpixel_axis)

        perturbation_cfg = plan["dotf"]["perturbation"]
        reference_mask, reference_meta = dotf_reference_mask(
            physical_shape,
            pupil_window,
            subpixel_axis=subpixel_axis,
            bg_code=int(perturbation_cfg.get("background_code", lcd_meta.get("opaque_code", 0))),
            open_code=int(perturbation_cfg.get("code_inside_reference", lcd_meta.get("transmissive_code", 255))),
        )
        perturbations = []
        for perturbation_id in plan["dotf"]["perturbation_set"]:
            perturbation_mask, perturbation_meta = dotf_edge_perturbation_mask(
                physical_shape,
                pupil_window,
                subpixel_axis=subpixel_axis,
                side=str(perturbation_id),
                block_size_px=int(perturbation_cfg["size_px"]),
                offset_from_effective_radius_px=int(perturbation_cfg.get("offset_from_effective_radius_px", 0)),
                bg_code=int(perturbation_cfg.get("background_code", lcd_meta.get("opaque_code", 0))),
                open_code=int(perturbation_cfg.get("code_inside_reference", lcd_meta.get("transmissive_code", 255))),
                perturb_code=int(perturbation_cfg.get("code_perturbation", lcd_meta.get("opaque_code", 0))),
            )
            perturbations.append((perturbation_meta["perturbation_id"], perturbation_mask, perturbation_meta))

        camera_meta = {
            "frame_dtype_full_scale": float(full_scale),
            "camera_profile_requested": str(plan.get("camera_profile_policy") or plan.get("camera_gain_selection") or "global_safe_camera"),
            "camera_profile_used": "per_capture",
            "multi_wavelength": bool(len(wavelength_runs) > 1),
            "wavelength_count": int(len(wavelength_runs)),
            "per_wavelength_profiles": [
                {
                    "wavelength_nm": item["wavelength_nm"],
                    "wavelength_index": item["wavelength_index"],
                    "grating": item["grating"],
                    "exposure_us": item["exposure_us"],
                    "gain_db": item["gain_db"],
                    "camera_profile_used": item["camera_profile"]["camera_profile_used"],
                    "camera_profile_requested": item["camera_profile"]["camera_profile_requested"],
                    "catalog_wavelength_nm": item["camera_profile"].get("catalog_wavelength_nm"),
                }
                for item in wavelength_runs
            ],
        }
        tls_meta = {
            "connected": bool(tls_service is not None),
            "multi_wavelength": bool(len(wavelength_runs) > 1),
            "wavelength_sequence": [float(item["wavelength_nm"]) for item in wavelength_runs],
            "grating_sequence": [item["grating"] for item in wavelength_runs],
        }
        writer = DotfRawWriter(output_raw, plan_id=plan["plan_id"]).open()
        writer.write_json_sections(
            plan=plan,
            camera_metadata=camera_meta,
            lcd_metadata=lcd_meta,
            tls_metadata=tls_meta,
            pupil_window_source=pupil_window,
            psf_roi_source=psf_roi,
            camera_params_source=camera_params,
        )

        repeats = int(plan["capture"]["repeats"])
        frames_per_capture = int(plan["capture"]["frames_per_capture"])
        settle_s = float(plan["lcd"].get("settle_ms", 200)) / 1000.0
        n_captures = repeats * (1 + len(perturbations)) * len(wavelength_runs)
        capture_index = 0
        run_status.update(
            plan_id=plan["plan_id"],
            phase="3.3",
            task="dotf_diagnostic_capture",
            current_stage="starting",
            n_captures=n_captures,
            repeat_index=0,
            current_wavelength_nm=float(wavelength_runs[0]["wavelength_nm"]),
            target_wavelength_nm=float(wavelength_runs[0]["wavelength_nm"]),
            camera_exposure_us=float(wavelength_runs[0]["exposure_us"]),
            camera_gain_db=float(wavelength_runs[0]["gain_db"]),
            camera_frame_dtype_full_scale=int(full_scale),
            camera_profile_used=wavelength_runs[0]["camera_profile"]["camera_profile_used"],
            lcd_display_index=int(lcd_meta.get("display_index", -1)),
            lcd_physical_shape=list(physical_shape),
            lcd_subpixel_axis=int(subpixel_axis),
            lcd_settle_ms=float(plan["lcd"].get("settle_ms", 200)),
        )

        for run in wavelength_runs:
            wavelength_nm = float(run["wavelength_nm"])
            exposure_us = float(run["exposure_us"])
            gain_db = float(run["gain_db"])
            camera_profile = dict(run["camera_profile"])
            if not dry_run:
                capture_adapter.apply_camera_params(exposure_us=exposure_us, gain_db=gain_db)
            tls_status = _configure_tls_for_wavelength(
                {
                    "wavelength_nm": wavelength_nm,
                    "grating": run.get("grating"),
                    "settle_ms": run.get("settle_ms", 0),
                },
                dry_run=dry_run,
                tls_service=tls_service,
            )
            run_status.update(
                current_stage="wavelength_setup",
                current_wavelength_nm=float(tls_status.get("current_wavelength_nm", wavelength_nm) or wavelength_nm),
                target_wavelength_nm=float(wavelength_nm),
                tls_grating=run.get("grating"),
                tls_moving=bool(tls_status.get("moving", False)),
                camera_exposure_us=exposure_us,
                camera_gain_db=gain_db,
                camera_profile_used=camera_profile["camera_profile_used"],
            )
            for repeat_index in range(repeats):
                capture_index = _capture_one(
                    plan=plan,
                    dry_run=dry_run,
                    capture_adapter=capture_adapter,
                    lcd_service=lcd_service,
                    frames_per_capture=frames_per_capture,
                    settle_s=settle_s,
                    reference_mask=reference_mask,
                    reference_meta=reference_meta,
                    repeat_index=repeat_index,
                    capture_index=capture_index,
                    writer=writer,
                    psf_roi=psf_roi,
                    run_status=run_status,
                    status_preview_every=status_preview_every,
                    wavelength_nm=wavelength_nm,
                    wavelength_index=int(run["wavelength_index"]),
                    exposure_us=exposure_us,
                    gain_db=gain_db,
                    camera_profile_used=str(camera_profile["camera_profile_used"]),
                )
                for perturbation_id, perturbation_mask, perturbation_meta in perturbations:
                    capture_index = _capture_one(
                        plan=plan,
                        dry_run=dry_run,
                        capture_adapter=capture_adapter,
                        lcd_service=lcd_service,
                        frames_per_capture=frames_per_capture,
                        settle_s=settle_s,
                        reference_mask=perturbation_mask,
                        reference_meta=perturbation_meta,
                        repeat_index=repeat_index,
                        capture_index=capture_index,
                        writer=writer,
                        psf_roi=psf_roi,
                        run_status=run_status,
                        status_preview_every=status_preview_every,
                        perturbation_id=perturbation_id,
                        wavelength_nm=wavelength_nm,
                        wavelength_index=int(run["wavelength_index"]),
                        exposure_us=exposure_us,
                        gain_db=gain_db,
                        camera_profile_used=str(camera_profile["camera_profile_used"]),
                    )
        writer.finalize(completed=True)
        run_status.update(current_stage="completed", completed=True, capture_index=capture_index)
        return output_raw
    except Exception as exc:
        if writer is not None:
            writer.finalize(completed=False, error=str(exc))
        run_status.update(current_stage="failed", completed=False, error=str(exc))
        raise
    finally:
        if not dry_run:
            lock.release()


def _capture_one(
    *,
    plan: dict[str, Any],
    dry_run: bool,
    capture_adapter: Any,
    lcd_service: Any,
    frames_per_capture: int,
    settle_s: float,
    reference_mask: np.ndarray,
    reference_meta: dict[str, Any],
    repeat_index: int,
    capture_index: int,
    writer: DotfRawWriter,
    psf_roi: dict[str, Any],
    run_status: OptionalRunStatus,
    status_preview_every: int,
    perturbation_id: str | None = None,
    wavelength_nm: float,
    wavelength_index: int,
    exposure_us: float,
    gain_db: float,
    camera_profile_used: str,
) -> int:
    capture_role = str(reference_meta["capture_role"])
    current_perturbation_id = perturbation_id or str(reference_meta["perturbation_id"])
    run_status.update(
        current_stage="capture",
        capture_index=capture_index,
        repeat_index=repeat_index,
        current_mask_id=reference_meta["mask_id"],
        capture_role=capture_role,
        perturbation_id=current_perturbation_id,
        current_wavelength_nm=float(wavelength_nm),
        target_wavelength_nm=float(wavelength_nm),
        camera_exposure_us=float(exposure_us),
        camera_gain_db=float(gain_db),
        camera_profile_used=str(camera_profile_used),
    )
    run_status.write_mask_preview(reference_mask)
    if dry_run:
        frame = _synthetic_dotf_frame(
            capture_role=capture_role,
            perturbation_id=current_perturbation_id,
            repeat_index=repeat_index,
            wavelength_nm=wavelength_nm,
        )
    else:
        lcd_service.show_mono_mask(reference_mask, mask_id=reference_meta["mask_id"], mode="phase3_3_dotf")
        time.sleep(settle_s)
        capture = capture_adapter.acquire_burst(frames_per_capture)
        frame = np.asarray(capture.frames_avg, dtype=np.float64)
    crop = crop_frame(frame, psf_roi["roi"])
    writer.append_capture(
        frame_avg=frame,
        crop=crop,
        mask_id=reference_meta["mask_id"],
        repeat_index=repeat_index,
        capture_role=capture_role,
        perturbation_id=current_perturbation_id,
        wavelength_nm=float(wavelength_nm),
        wavelength_index=int(wavelength_index),
        exposure_us=float(exposure_us),
        gain_db=float(gain_db),
        camera_profile_used=str(camera_profile_used),
        mask_metadata=reference_meta,
    )
    if capture_index % max(1, int(status_preview_every)) == 0:
        run_status.write_frame_preview(frame)
        run_status.write_frame_stats(_frame_stats(frame))
    writer._ensure_open().flush()
    return capture_index + 1


def _assert_pupil_shape_matches_lcd(pupil_window: dict[str, Any], physical_shape: tuple[int, int]) -> None:
    src_shape = tuple(int(v) for v in pupil_window.get("physical_shape", []))
    if src_shape and src_shape != tuple(physical_shape):
        raise ValueError(
            "effective_pupil_window.json physical_shape "
            f"{src_shape} does not match LCD physical_shape {physical_shape}"
        )


def _synthetic_dotf_frame(*, capture_role: str, perturbation_id: str, repeat_index: int, wavelength_nm: float) -> np.ndarray:
    h, w = 256, 320
    yy, xx = np.mgrid[:h, :w]
    seed = 5000 + 31 * int(repeat_index) + sum(ord(c) for c in perturbation_id) + int(round(float(wavelength_nm)))
    rng = np.random.default_rng(seed)
    wl_offset = (float(wavelength_nm) - 550.0) / 100.0
    cx = 160.0 + 0.6 * wl_offset + rng.normal(0.0, 0.12)
    cy = 128.0 - 0.4 * wl_offset + rng.normal(0.0, 0.12)
    base = 10.0 + 0.02 * xx + 0.015 * yy
    wl_gain = 1.0 + 0.08 * wl_offset
    core = (220.0 * wl_gain) * np.exp(-(((xx - cx) / 10.5) ** 2 + ((yy - cy) / 9.0) ** 2))
    halo = (30.0 * (1.0 - 0.03 * wl_offset)) * np.exp(-(((xx - cx) / 26.0) ** 2 + ((yy - cy) / 24.0) ** 2))
    if capture_role == "reference":
        perturb = 0.0
    else:
        dx, dy = {
            "edge_block_left": (-9.0, 0.0),
            "edge_block_right": (9.0, 0.0),
            "edge_block_top": (0.0, -9.0),
            "edge_block_bottom": (0.0, 9.0),
        }.get(perturbation_id, (6.0, 0.0))
        perturb = -12.0 * np.exp(-(((xx - (cx + dx)) / 6.0) ** 2 + ((yy - (cy + dy)) / 6.5) ** 2))
    noise = rng.normal(0.0, 0.7, size=(h, w))
    return (base + core + halo + perturb + noise).astype(np.float64)


def _dotf_wavelength_entries(plan: dict[str, Any]) -> list[dict[str, Any]]:
    wavelengths = plan.get("wavelengths")
    if isinstance(wavelengths, list) and wavelengths:
        return [dict(item) for item in wavelengths]
    wavelength = plan.get("wavelength")
    if isinstance(wavelength, dict) and wavelength:
        return [dict(wavelength)]
    raise ValueError("Phase 3.3 plan requires wavelength or wavelengths")


def _configure_tls_for_wavelength(
    wl_cfg: dict[str, Any],
    *,
    dry_run: bool,
    tls_service: Any,
) -> dict[str, Any]:
    wavelength_nm = wl_cfg.get("wavelength_nm")
    grating = wl_cfg.get("grating")
    meta = {
        "connected": False,
        "current_wavelength_nm": wavelength_nm,
        "target_wavelength_nm": wavelength_nm,
        "grating": grating,
        "moving": False,
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
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Phase 3.3 dOTF diagnostic capture")
    parser.add_argument("--plan", default="plans/bishe_dotf_diagnostic.yaml")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--hardware", action="store_true")
    parser.add_argument("--camera-index", type=int, default=0)
    parser.add_argument("--lcd-display-index", type=int, default=None)
    parser.add_argument("--lcd-subpixel-axis", type=int, choices=(0, 1), default=None)
    parser.add_argument("--tls-serial", default=None)
    parser.add_argument("--allow-wavelength-labels-without-tls", action="store_true")
    parser.add_argument("--status-dir", default=None)
    parser.add_argument("--status-preview-every", type=int, default=1)
    args = parser.parse_args()
    if args.hardware and args.dry_run:
        parser.error("--hardware and --dry-run are mutually exclusive")
    plan = load_yaml_plan(_resolve_repo_path(args.plan))
    status_dir = Path(args.status_dir) if args.status_dir else None

    if not args.hardware:
        print("=== DRY RUN (no hardware) ===")
        run_capture_dotf(
            plan,
            dry_run=True,
            lcd_subpixel_axis=args.lcd_subpixel_axis,
            status_dir=status_dir,
            status_preview_every=args.status_preview_every,
        )
        return

    validate_phase32_plan(plan, task="dotf", hardware=True)
    from devices.camera_service import CameraServiceClient
    from devices.lcd_service import LCDService

    lcd_cfg = plan.get("lcd", {})
    display_index = args.lcd_display_index if args.lcd_display_index is not None else lcd_cfg.get("display_index")
    subpixel_axis = args.lcd_subpixel_axis if args.lcd_subpixel_axis is not None else lcd_cfg.get("subpixel_axis")
    camera_service = CameraServiceClient(timeout_ms=5000, auto_ensure=True)
    lcd_service = LCDService(display_index=display_index, subpixel_axis=subpixel_axis)
    tls_service = _connect_tls(args.tls_serial)
    if tls_service is None and not args.allow_wavelength_labels_without_tls:
        print("ERROR: TLS service is required for hardware Phase 3.3 capture.", file=sys.stderr)
        sys.exit(1)
    try:
        camera_service.open_camera(index=args.camera_index, disable_trigger=True)
        run_capture_dotf(
            plan,
            dry_run=False,
            camera_service=camera_service,
            lcd_service=lcd_service,
            tls_service=tls_service,
            lcd_subpixel_axis=args.lcd_subpixel_axis,
            status_dir=status_dir,
            status_preview_every=args.status_preview_every,
        )
    finally:
        _close_hardware(camera_service, lcd_service, tls_service)


if __name__ == "__main__":
    main()
