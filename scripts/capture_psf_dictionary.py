#!/usr/bin/env python3
"""Phase 3.4 measured PSF dictionary raw capture."""

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
from tasks.psf_dictionary_masks import generate_psf_dictionary_masks  # noqa: E402
from tasks.psf_dictionary_phase3 import PSFDictionaryRawWriter  # noqa: E402
from tasks.psf_phase3 import crop_frame, load_psf_roi, load_pupil_window, load_yaml_plan, validate_phase32_plan  # noqa: E402


def _resolve_repo_path(path: str | Path) -> Path:
    p = Path(path)
    return p if p.is_absolute() else _repo_root() / p


def run_capture_psf_dictionary(
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
    validate_phase32_plan(plan, task="dictionary", hardware=not dry_run)
    output_raw = _resolve_repo_path(plan["output"]["raw_h5"])
    if not dry_run and output_raw.exists():
        raise FileExistsError(
            f"Refusing to overwrite existing hardware raw HDF5: {output_raw}. "
            "Rename the existing file or update output.raw_h5 in the plan."
        )
    camera_params, _ = load_camera_params(plan["camera_params_source"])
    exposure_us, gain_db, _profile_frames, full_scale, camera_profile = resolve_geometry_camera_settings(plan, camera_params)
    pupil_window = load_pupil_window(_resolve_repo_path(plan["pupil_window_source"]))
    psf_roi = load_psf_roi(_resolve_repo_path(plan["psf_roi_source"]))
    lock = HardwareLock(_resolve_repo_path(plan.get("lock_file", "outputs/run_status/capture_hardware.lock")))
    run_status = OptionalRunStatus(status_dir, run_id=plan["plan_id"])
    capture_adapter = None
    writer: PSFDictionaryRawWriter | None = None

    if not dry_run:
        print("Phase 3.4 PSF dictionary capture requires exclusive camera/LCD access.")
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
            capture_adapter.apply_camera_params(exposure_us=exposure_us, gain_db=gain_db)
            camera_service.start_stream()

        physical_shape, subpixel_axis = _resolve_lcd_geometry(plan, lcd_meta, lcd_subpixel_axis=lcd_subpixel_axis)
        _assert_pupil_shape_matches_lcd(pupil_window, physical_shape)
        lcd_meta["physical_shape"] = list(physical_shape)
        lcd_meta["subpixel_axis"] = int(subpixel_axis)
        masks_cfg = plan["masks"]
        random_cfg = masks_cfg.get("random", {})
        mask_records = generate_psf_dictionary_masks(
            lowres_shape=(int(masks_cfg["lowres_shape"][0]), int(masks_cfg["lowres_shape"][1])),
            physical_shape=physical_shape,
            pupil_window=pupil_window,
            subpixel_axis=subpixel_axis,
            include=list(masks_cfg["include"]),
            random_lowfreq_count=int(random_cfg.get("lowfreq_count", 0)),
            random_midfreq_count=int(random_cfg.get("midfreq_count", 0)),
            task_related_count=int(random_cfg.get("task_related_count", 0)),
            random_seed=int(plan["capture"].get("random_seed", 0)),
            bg_code=int(lcd_meta.get("opaque_code", 0)),
            open_code=int(lcd_meta.get("transmissive_code", 255)),
        )
        tls_meta = _initial_tls_metadata(plan)
        camera_meta = {
            "exposure_us": float(exposure_us),
            "gain_db": float(gain_db),
            "frame_dtype_full_scale": float(full_scale),
            "camera_profile_requested": camera_profile["camera_profile_requested"],
            "camera_profile_used": camera_profile["camera_profile_used"],
        }
        writer = PSFDictionaryRawWriter(output_raw, plan_id=plan["plan_id"]).open()
        writer.write_json_sections(
            plan=plan,
            camera_metadata=camera_meta,
            lcd_metadata=lcd_meta,
            tls_metadata=tls_meta,
            pupil_window_source=pupil_window,
            psf_roi_source=psf_roi,
            camera_params_source=camera_params,
        )
        repeats = int(plan["capture"]["repeats_per_mask"])
        frames_per_capture = int(plan["capture"]["frames_per_capture"])
        settle_s = float(plan["lcd"].get("settle_ms", 200)) / 1000.0
        wavelengths = list(plan.get("wavelengths", []))
        n_captures = len(mask_records) * repeats * len(wavelengths)
        capture_index = 0
        run_status.update(
            plan_id=plan["plan_id"],
            phase="3.4",
            task="measured_psf_dictionary_capture",
            current_stage="starting",
            n_captures=n_captures,
            n_repeats=repeats,
            current_wavelength_nm=tls_meta.get("current_wavelength_nm"),
            camera_exposure_us=float(exposure_us),
            camera_gain_db=float(gain_db),
            camera_frame_dtype_full_scale=int(full_scale),
            camera_profile_used=camera_profile["camera_profile_used"],
            lcd_display_index=int(lcd_meta.get("display_index", -1)),
            lcd_physical_shape=list(physical_shape),
            lcd_subpixel_axis=int(subpixel_axis),
            lcd_settle_ms=float(plan["lcd"].get("settle_ms", 200)),
        )
        for wavelength_index, wavelength_entry in enumerate(wavelengths):
            current_wavelength_nm = float(wavelength_entry["wavelength_nm"])
            run_status.update(current_stage="wavelength", current_wavelength_nm=current_wavelength_nm)
            if not dry_run and tls_service is not None:
                _move_tls_to_wavelength(
                    tls_service,
                    wavelength_entry,
                    settle_ms=float(plan.get("tls", {}).get("settle_ms", wavelength_entry.get("settle_ms", 500))),
                )
            for record in mask_records:
                run_status.write_mask_preview(record["physical_mask"])
                for repeat_index in range(repeats):
                    run_status.update(
                        current_stage="capture",
                        capture_index=capture_index,
                        current_mask_id=record["mask_id"],
                        current_mask_family=record["mask_family"],
                        repeat_index=repeat_index,
                        current_wavelength_nm=current_wavelength_nm,
                    )
                    if dry_run:
                        frame = _synthetic_dictionary_frame(
                            mask_id=record["mask_id"],
                            mask_family=record["mask_family"],
                            wavelength_nm=current_wavelength_nm,
                            repeat_index=repeat_index,
                        )
                    else:
                        lcd_service.show_mono_mask(record["physical_mask"], mask_id=record["mask_id"], mode="phase3_4_psf_dictionary")
                        time.sleep(settle_s)
                        capture = capture_adapter.acquire_burst(frames_per_capture)
                        frame = np.asarray(capture.frames_avg, dtype=np.float64)
                    crop = crop_frame(frame, psf_roi["roi"])
                    mask_metadata = dict(record["mask_metadata"])
                    mask_metadata["repeat_index"] = int(repeat_index)
                    mask_metadata["wavelength_nm"] = current_wavelength_nm
                    mask_metadata["wavelength_index"] = int(wavelength_index)
                    writer.append_capture(
                        frame_avg=frame,
                        crop=crop,
                        lowres_mask=record["lowres_mask"],
                        mask_id=record["mask_id"],
                        mask_family=record["mask_family"],
                        wavelength_nm=current_wavelength_nm,
                        wavelength_index=wavelength_index,
                        repeat_index=repeat_index,
                        mask_metadata=mask_metadata,
                    )
                    if capture_index % max(1, int(status_preview_every)) == 0:
                        run_status.write_frame_preview(frame)
                        run_status.write_frame_stats(_frame_stats(frame))
                    writer._ensure_open().flush()
                    capture_index += 1
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


def _assert_pupil_shape_matches_lcd(pupil_window: dict[str, Any], physical_shape: tuple[int, int]) -> None:
    src_shape = tuple(int(v) for v in pupil_window.get("physical_shape", []))
    if src_shape and src_shape != tuple(physical_shape):
        raise ValueError(
            "effective_pupil_window.json physical_shape "
            f"{src_shape} does not match LCD physical_shape {physical_shape}"
        )


def _synthetic_dictionary_frame(*, mask_id: str, mask_family: str, wavelength_nm: float, repeat_index: int) -> np.ndarray:
    h, w = 256, 320
    yy, xx = np.mgrid[:h, :w]
    seed = sum(ord(c) for c in mask_id) + int(round(wavelength_nm)) + 97 * int(repeat_index)
    rng = np.random.default_rng(seed)
    family_offset = sum(ord(c) for c in mask_family) % 11 - 5
    wavelength_offset = (float(wavelength_nm) - 550.0) / 120.0
    cx = 160.0 + 0.45 * family_offset + 2.2 * wavelength_offset + rng.normal(0.0, 0.08)
    cy = 128.0 - 0.35 * family_offset - 1.4 * wavelength_offset + rng.normal(0.0, 0.08)
    sx = 11.0 + abs(family_offset % 4)
    sy = 10.0 + abs((family_offset + 1) % 4)
    core = 180.0 * np.exp(-(((xx - cx) / sx) ** 2 + ((yy - cy) / sy) ** 2))
    halo = 18.0 * np.exp(-(((xx - cx) / 24.0) ** 2 + ((yy - cy) / 22.0) ** 2))
    base = 9.0 + 0.02 * xx + 0.02 * yy
    noise = rng.normal(0.0, 0.8, size=(h, w))
    return (base + core + halo + noise).astype(np.float64)


def _move_tls_to_wavelength(tls_service: Any, wavelength_entry: dict[str, Any], *, settle_ms: float) -> None:
    wavelength_nm = float(wavelength_entry["wavelength_nm"])
    grating = int(wavelength_entry.get("grating", 1))
    tls_service.set_target_wavelength_nm(wavelength_nm)
    tls_service.move_to_target_wavelength(grating=grating)
    time.sleep(float(settle_ms) / 1000.0)


def _initial_tls_metadata(plan: dict[str, Any]) -> dict[str, Any]:
    first = dict(plan.get("wavelengths", [{}])[0]) if plan.get("wavelengths") else {}
    return {
        "connected": False,
        "current_wavelength_nm": first.get("wavelength_nm"),
        "target_wavelength_nm": first.get("wavelength_nm"),
        "grating": first.get("grating"),
        "moving": False,
        "timestamp_ns": time.monotonic_ns(),
        "wavelength_sequence": list(plan.get("wavelengths", [])),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Phase 3.4 measured PSF dictionary capture")
    parser.add_argument("--plan", default="plans/bishe_psf_dictionary.yaml")
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
        run_capture_psf_dictionary(
            plan,
            dry_run=True,
            lcd_subpixel_axis=args.lcd_subpixel_axis,
            status_dir=status_dir,
            status_preview_every=args.status_preview_every,
        )
        return

    validate_phase32_plan(plan, task="dictionary", hardware=True)
    from devices.camera_service import CameraServiceClient
    from devices.lcd_service import LCDService

    lcd_cfg = plan.get("lcd", {})
    display_index = args.lcd_display_index if args.lcd_display_index is not None else lcd_cfg.get("display_index")
    subpixel_axis = args.lcd_subpixel_axis if args.lcd_subpixel_axis is not None else lcd_cfg.get("subpixel_axis")
    camera_service = CameraServiceClient(timeout_ms=5000, auto_ensure=True)
    lcd_service = LCDService(display_index=display_index, subpixel_axis=subpixel_axis)
    tls_service = _connect_tls(args.tls_serial)
    if tls_service is None and not args.allow_wavelength_labels_without_tls:
        print("ERROR: TLS service is required for hardware Phase 3.4 capture.", file=sys.stderr)
        sys.exit(1)
    try:
        camera_service.open_camera(index=args.camera_index, disable_trigger=True)
        run_capture_psf_dictionary(
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
