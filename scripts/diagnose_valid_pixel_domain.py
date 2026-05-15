#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import ctypes
import json
import os
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _ensure_sys_path() -> None:
    root = str(_repo_root())
    if root not in sys.path:
        sys.path.insert(0, root)


def _set_keep_awake(enabled: bool) -> None:
    if not enabled:
        return
    ES_CONTINUOUS = 0x80000000
    ES_SYSTEM_REQUIRED = 0x00000001
    ES_DISPLAY_REQUIRED = 0x00000002
    ctypes.windll.kernel32.SetThreadExecutionState(
        ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED
    )


def _clear_keep_awake(enabled: bool) -> None:
    if not enabled:
        return
    ES_CONTINUOUS = 0x80000000
    ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS)


def _drop_top_rows_view(burst: np.ndarray, drop_top_rows: int) -> np.ndarray:
    if drop_top_rows <= 0:
        return burst
    if drop_top_rows >= burst.shape[1]:
        raise ValueError(
            f"drop_top_rows must satisfy 0 <= drop_top_rows < {burst.shape[1]}, "
            f"got {drop_top_rows}"
        )
    return burst[:, drop_top_rows:, :]


def summarize_probe_rows(
    rows: list[dict[str, Any]],
    *,
    full_scale: int,
    drop_top_rows: int,
) -> dict[str, Any]:
    top_row_full_scale_observed = False
    post_drop_full_scale_at_short_exposure = False
    psf_region_full_scale_at_long_exposure = False
    grouped: dict[str, dict[str, Any]] = {}

    for key in sorted({(r["gain_db"], r["exposure_us"]) for r in rows}):
        gain_db, exposure_us = key
        setting_rows = [
            row for row in rows
            if row["gain_db"] == gain_db and row["exposure_us"] == exposure_us
        ]
        coords = Counter((row["all_peak_y"], row["all_peak_x"]) for row in setting_rows)
        grouped[f"gain={gain_db},exposure={exposure_us}"] = {
            "n_repeats": len(setting_rows),
            "all_peaks": [int(row["all_peak"]) for row in setting_rows],
            "all_full_scale_repeats": sum(1 for row in setting_rows if row["all_peak"] >= full_scale),
            "drop_top_rows_peak_values": [int(row["drop_peak"]) for row in setting_rows],
            "drop_top_rows_full_counts": [
                int(row["drop_full_count"]) for row in setting_rows
            ],
            "top_all_peak_coords": [
                {"y": int(y), "x": int(x), "count": int(count)}
                for (y, x), count in coords.most_common(5)
            ],
        }
        if exposure_us <= 2000.0:
            top_row_full_scale_observed = top_row_full_scale_observed or any(
                row["all_peak"] >= full_scale and row["all_peak_y"] < max(1, drop_top_rows)
                for row in setting_rows
            )
            post_drop_full_scale_at_short_exposure = (
                post_drop_full_scale_at_short_exposure
                or any(row["drop_full_count"] > 0 for row in setting_rows)
            )
        if exposure_us >= 4000.0:
            psf_region_full_scale_at_long_exposure = (
                psf_region_full_scale_at_long_exposure
                or any(
                    row["drop_full_count"] > 0
                    and row["all_peak_y"] >= max(1, drop_top_rows)
                    for row in setting_rows
                )
            )

    recommended = {"type": "full_frame"}
    if top_row_full_scale_observed and not post_drop_full_scale_at_short_exposure and drop_top_rows > 0:
        recommended = {"type": "exclude_top_rows", "top_rows": int(drop_top_rows)}

    return {
        "settings": grouped,
        "evidence": {
            "top_row_full_scale_observed": top_row_full_scale_observed,
            "post_drop_full_scale_at_short_exposure": post_drop_full_scale_at_short_exposure,
            "psf_region_full_scale_at_long_exposure": psf_region_full_scale_at_long_exposure,
        },
        "recommended_valid_pixel_domain": recommended,
    }


def _fake_burst(
    exposure_us: float,
    repeat: int,
    *,
    frames_per_burst: int,
    frame_shape: tuple[int, int],
    full_scale: int,
) -> np.ndarray:
    height, width = frame_shape
    burst = np.full((frames_per_burst, height, width), 24.0, dtype=np.float64)
    burst[:, 0, (repeat % 3) + 1] = full_scale
    if exposure_us >= 4000.0:
        burst[:, 10:12, 15:17] = full_scale
    elif exposure_us >= 2000.0:
        burst[:, 10:12, 15:17] = 160.0
    elif exposure_us >= 1000.0:
        burst[:, 10:12, 15:17] = 95.0
    elif exposure_us >= 500.0:
        burst[:, 10:12, 15:17] = 60.0
    else:
        burst[:, 10:12, 15:17] = 40.0
    return burst


def _analyze_burst(
    burst: np.ndarray,
    *,
    exposure_us: float,
    gain_db: float,
    repeat: int,
    drop_top_rows: int,
    full_scale: int,
) -> dict[str, Any]:
    flat_idx = int(np.argmax(burst))
    frame_idx, peak_y, peak_x = np.unravel_index(flat_idx, burst.shape)
    drop_burst = _drop_top_rows_view(burst, drop_top_rows)
    avg = burst.mean(axis=0, dtype=np.float64)
    return {
        "exposure_us": float(exposure_us),
        "gain_db": float(gain_db),
        "repeat": int(repeat),
        "frames_per_burst": int(burst.shape[0]),
        "all_peak": int(np.max(burst)),
        "all_full_count": int(np.count_nonzero(burst >= full_scale)),
        "all_peak_frame": int(frame_idx),
        "all_peak_y": int(peak_y),
        "all_peak_x": int(peak_x),
        "drop_peak": int(np.max(drop_burst)),
        "drop_full_count": int(np.count_nonzero(drop_burst >= full_scale)),
        "p99_avg": float(np.percentile(avg, 99.0)),
        "p999_avg": float(np.percentile(avg, 99.9)),
    }


def run_valid_pixel_domain_diagnostic(
    *,
    output_dir: Path,
    wavelength_nm: float,
    grating: int | None,
    gain_db: float,
    exposures_us: list[float],
    repeats: int,
    frames_per_burst: int,
    drop_top_rows: int,
    hardware: bool,
    camera_index: int,
    lcd_display_index: int | None,
    lcd_subpixel_axis: int | None,
    tls_serial: str | None,
    keep_awake: bool,
) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    csv_path = output_dir / f"valid_pixel_probe_{stamp}.csv"
    json_path = output_dir / f"valid_pixel_probe_{stamp}.json"

    rows: list[dict[str, Any]] = []
    full_scale = 255
    frame_shape = (32, 32)
    camera_serial: str | None = None
    pixel_format = "RAW8"

    _set_keep_awake(keep_awake)
    try:
        if hardware:
            _ensure_sys_path()
            from capture.frame_capture import FrameCaptureHelper
            from devices.camera_service import CameraServiceClient
            from devices.frame_stream import FrameStreamClient
            from devices.lcd_service import LCDService
            from tasks.capture_forward_dataset import CameraCaptureAdapter

            camera = CameraServiceClient(timeout_ms=5000, auto_ensure=True)
            lcd = LCDService(
                display_index=lcd_display_index,
                subpixel_axis=lcd_subpixel_axis,
            )
            tls = None
            stream = None
            try:
                if tls_serial:
                    from devices.tls_service import TLSService

                    tls = TLSService(default_serial_number=tls_serial)
                    tls.connect(serial_number=tls_serial)
                    if grating is not None:
                        tls.set_grating(int(grating))
                    tls.set_wavelength_nm(float(wavelength_nm))
                    tls.move(timeout_s=60.0)
                    tls.wait_until_idle(timeout_s=60.0)

                lcd.show_all_transmissive()
                time.sleep(1.0)
                reply = camera.open_camera(index=camera_index, disable_trigger=True)
                camera_serial = str(reply.get("serial")) if reply.get("serial") is not None else None
                camera.start_stream()
                stream = FrameStreamClient(recv_timeout_ms=5000)
                helper = FrameCaptureHelper(stream)
                adapter = CameraCaptureAdapter(helper, camera)
                first_packet = helper.capture_one_packet(timeout_s=5.0)
                frame_shape = tuple(np.asarray(first_packet.raw).shape[-2:])
                pixel_format = str(first_packet.meta.get("pixel_format") or "RAW8")
                if str(first_packet.meta.get("format") or "").lower() in ("raw16", "mono16"):
                    full_scale = 65535

                for exposure_us in exposures_us:
                    adapter.apply_camera_params(exposure_us=exposure_us, gain_db=gain_db)
                    time.sleep(0.3)
                    for _ in range(5):
                        helper.capture_one_packet(timeout_s=5.0)
                    for repeat in range(repeats):
                        burst = np.stack(
                            [helper.capture_one_packet(timeout_s=5.0).raw for _ in range(frames_per_burst)],
                            axis=0,
                        )
                        rows.append(
                            _analyze_burst(
                                burst,
                                exposure_us=exposure_us,
                                gain_db=gain_db,
                                repeat=repeat,
                                drop_top_rows=drop_top_rows,
                                full_scale=full_scale,
                            )
                        )
            finally:
                try:
                    if stream is not None:
                        stream.close()
                except Exception:
                    pass
                try:
                    camera.stop_stream()
                except Exception:
                    pass
                try:
                    camera.close_camera()
                except Exception:
                    pass
                try:
                    camera.close()
                except Exception:
                    pass
                try:
                    lcd.close()
                except Exception:
                    pass
                try:
                    if tls is not None:
                        tls.close()
                except Exception:
                    pass
        else:
            for exposure_us in exposures_us:
                for repeat in range(repeats):
                    burst = _fake_burst(
                        exposure_us,
                        repeat,
                        frames_per_burst=frames_per_burst,
                        frame_shape=frame_shape,
                        full_scale=full_scale,
                    )
                    rows.append(
                        _analyze_burst(
                            burst,
                            exposure_us=exposure_us,
                            gain_db=gain_db,
                            repeat=repeat,
                            drop_top_rows=drop_top_rows,
                            full_scale=full_scale,
                        )
                    )
    finally:
        _clear_keep_awake(keep_awake)

    if not rows:
        raise RuntimeError("valid pixel domain diagnostic produced no rows")

    summary = summarize_probe_rows(
        rows,
        full_scale=full_scale,
        drop_top_rows=drop_top_rows,
    )
    payload = {
        "camera_serial": camera_serial,
        "frame_shape": [int(frame_shape[0]), int(frame_shape[1])],
        "pixel_format": pixel_format,
        "full_scale": int(full_scale),
        "tested_exposures_us": [float(v) for v in exposures_us],
        "gain_db": float(gain_db),
        "wavelength_nm": float(wavelength_nm),
        "grating": int(grating) if grating is not None else None,
        "drop_top_rows": int(drop_top_rows),
        "evidence": summary["evidence"],
        "recommended_valid_pixel_domain": summary["recommended_valid_pixel_domain"],
        "settings": summary["settings"],
    }

    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return csv_path, json_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Diagnose valid camera pixel domain evidence for Phase 3.0.5b",
    )
    parser.add_argument(
        "--output-dir",
        default="outputs/diagnostics/shutter_gain_peak_probe",
        help="Directory for CSV/JSON diagnostic outputs",
    )
    parser.add_argument("--wavelength-nm", type=float, default=550.0)
    parser.add_argument("--grating", type=int, default=1)
    parser.add_argument("--gain-db", type=float, default=0.0)
    parser.add_argument(
        "--exposures-us",
        nargs="+",
        type=float,
        default=[50.0, 100.0, 500.0, 1000.0, 2000.0, 4000.0, 6288.8, 50000.0],
    )
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--frames-per-burst", type=int, default=5)
    parser.add_argument("--drop-top-rows", type=int, default=1)
    parser.add_argument("--hardware", action="store_true")
    parser.add_argument("--camera-index", type=int, default=0)
    parser.add_argument("--lcd-display-index", type=int, default=None)
    parser.add_argument("--lcd-subpixel-axis", type=int, default=None)
    parser.add_argument("--tls-serial", default=None)
    parser.add_argument("--keep-awake", action="store_true")
    args = parser.parse_args()

    csv_path, json_path = run_valid_pixel_domain_diagnostic(
        output_dir=Path(args.output_dir),
        wavelength_nm=args.wavelength_nm,
        grating=args.grating,
        gain_db=args.gain_db,
        exposures_us=list(args.exposures_us),
        repeats=args.repeats,
        frames_per_burst=args.frames_per_burst,
        drop_top_rows=args.drop_top_rows,
        hardware=args.hardware,
        camera_index=args.camera_index,
        lcd_display_index=args.lcd_display_index,
        lcd_subpixel_axis=args.lcd_subpixel_axis,
        tls_serial=args.tls_serial or os.environ.get("TLS_C1_SERIAL"),
        keep_awake=args.keep_awake,
    )
    print(f"csv: {csv_path}")
    print(f"json: {json_path}")


if __name__ == "__main__":
    main()
