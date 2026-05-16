#!/usr/bin/env python3
"""Analyze Phase 3.1 effective pupil geometry calibration raw HDF5."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

import h5py
import numpy as np

from tasks.pupil_geometry_masks import circular_window_mask
from tasks.pupil_geometry_model import (
    estimate_ellipse_parameters,
    fit_function,
    json_fit_summary,
    solve_aperture_from_profiles,
)


def analyze_pupil_geometry(
    raw_h5: str | Path,
    output_dir: str | Path,
) -> dict[str, Any]:
    raw_path = Path(raw_h5)
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    data = _read_h5(raw_path)
    circle = solve_aperture_from_profiles(
        data["bar_x_positions"],
        data["bar_x_energies"],
        data["bar_y_positions"],
        data["bar_y_energies"],
    )
    ellipse_fit = estimate_ellipse_parameters(
        data["radius_energies"],
        data["radii"],
    )

    radius_factor = float(data["plan"].get("calibration", {}).get("effective_window", {}).get("radius_factor_of_b", 0.9))
    effective_radius = radius_factor * float(ellipse_fit.b)
    center = (float(circle["xc"]), float(circle["yc"]))
    window = circular_window_mask(
        data["physical_shape"],
        center=center,
        radius=effective_radius,
        bg_code=0,
        aperture_code=255,
    )

    _write_profile_csv(out_dir / "x_profile.csv", data["bar_x_positions"], data["bar_x_energies"])
    _write_profile_csv(out_dir / "y_profile.csv", data["bar_y_positions"], data["bar_y_energies"])
    _write_radius_csv(
        out_dir / "radius_scan.csv",
        data["radii"],
        data["radius_energies"],
        ellipse_fit.predicted,
    )
    _write_line_plot_png(
        out_dir / "bar_profile_fit.png",
        [
            (data["bar_x_positions"], data["bar_x_energies"]),
            (data["bar_y_positions"], data["bar_y_energies"]),
        ],
    )
    _write_line_plot_png(
        out_dir / "radius_overlap_fit.png",
        [
            (data["radii"], ellipse_fit.adjusted_energy),
            (data["radii"], fit_function(data["radii"], ellipse_fit.k, ellipse_fit.a, ellipse_fit.b)),
        ],
    )
    np.save(str(out_dir / "effective_pupil_window.npy"), window)
    _write_mask_png(out_dir / "effective_pupil_window.png", window)

    result = {
        "schema_version": 1,
        "phase": "3.1",
        "task": "pupil_geometry_calibration",
        "strategy": "bar_profiles_plus_radius_scan",
        "source_raw_h5": str(raw_h5),
        "capture_plan_id": data["plan_id"],
        "camera_params_source": data["camera_params_source"],
        "camera_profile_requested": data["camera_profile_requested"],
        "camera_profile_used": data["camera_profile_used"],
        "fallback_used": bool(data["fallback_used"]),
        "wavelength_nm": data["wavelength_nm"],
        "physical_shape": [int(data["physical_shape"][0]), int(data["physical_shape"][1])],
        "subpixel_axis": int(data["subpixel_axis"]),
        "circle_estimate": {k: float(v) for k, v in circle.items()},
        "window_type": "circle",
        "center": {"x": center[0], "y": center[1]},
        "radius": float(effective_radius),
        "radius_source": "factor_of_ellipse_semi_minor",
        "radius_factor_of_b": float(radius_factor),
        "ellipse": json_fit_summary(ellipse_fit),
        "validity": {
            "effective_window_estimated": True,
            "scientific_calibration_valid": False,
            "training_ready": False,
        },
    }
    with open(out_dir / "effective_pupil_window.json", "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, default=str)
    _write_report(out_dir / "pupil_geometry_report.md", result)
    return result


def _read_h5(path: Path) -> dict[str, Any]:
    with h5py.File(path, "r") as f:
        plan = _json_dataset(f["capture/plan_json"])
        plan_id = _read_scalar_str(f["capture/plan_id"])
        lcd_meta = _json_dataset(f["lcd/metadata_json"])
        tls_meta = _json_dataset(f["tls/metadata_json"])
        cam_source = _json_dataset(f["camera/camera_params_source_json"])
        physical_shape = lcd_meta.get("physical_shape")
        if physical_shape is None:
            raise ValueError("raw HDF5 lcd.metadata_json.physical_shape is required")
        return {
            "plan": plan,
            "plan_id": plan_id,
            "physical_shape": (int(physical_shape[0]), int(physical_shape[1])),
            "subpixel_axis": int(lcd_meta.get("subpixel_axis", 1)),
            "wavelength_nm": tls_meta.get("target_wavelength_nm") or tls_meta.get("current_wavelength_nm"),
            "camera_params_source": cam_source.get("source"),
            "camera_profile_requested": cam_source.get("camera_profile_requested"),
            "camera_profile_used": cam_source.get("camera_profile_used"),
            "fallback_used": cam_source.get("fallback_used", False),
            "bar_x_positions": np.asarray(f["bar_scan/x/positions"], dtype=np.float64),
            "bar_x_energies": np.asarray(f["bar_scan/x/energies"], dtype=np.float64),
            "bar_y_positions": np.asarray(f["bar_scan/y/positions"], dtype=np.float64),
            "bar_y_energies": np.asarray(f["bar_scan/y/energies"], dtype=np.float64),
            "radii": np.asarray(f["radius_scan/radii"], dtype=np.float64),
            "radius_energies": np.asarray(f["radius_scan/energies"], dtype=np.float64),
        }


def _write_profile_csv(path: Path, positions: np.ndarray, energies: np.ndarray) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["position", "energy"])
        writer.writeheader()
        for p, e in zip(positions, energies):
            writer.writerow({"position": float(p), "energy": float(e)})


def _write_radius_csv(
    path: Path,
    radii: np.ndarray,
    energies: np.ndarray,
    predicted: np.ndarray,
) -> None:
    adjusted = np.asarray(energies, dtype=np.float64) - float(np.min(energies))
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["radius", "energy", "adjusted_energy", "fit_energy"])
        writer.writeheader()
        for r, e, a, p in zip(radii, energies, adjusted, predicted):
            writer.writerow({
                "radius": float(r),
                "energy": float(e),
                "adjusted_energy": float(a),
                "fit_energy": float(p),
            })


def _write_line_plot_png(path: Path, series: list[tuple[np.ndarray, np.ndarray]]) -> None:
    import cv2

    canvas = np.full((360, 640, 3), 255, dtype=np.uint8)
    colors = [(20, 20, 20), (30, 110, 220), (220, 80, 30)]
    xs = np.concatenate([np.asarray(s[0], dtype=np.float64) for s in series if len(s[0])])
    ys = np.concatenate([np.asarray(s[1], dtype=np.float64) for s in series if len(s[1])])
    if xs.size == 0 or ys.size == 0:
        cv2.imwrite(str(path), canvas)
        return
    x_min, x_max = float(np.min(xs)), float(np.max(xs))
    y_min, y_max = float(np.min(ys)), float(np.max(ys))
    if y_max <= y_min:
        y_max = y_min + 1.0
    if x_max <= x_min:
        x_max = x_min + 1.0
    left, right, top, bottom = 50, 610, 30, 330
    cv2.rectangle(canvas, (left, top), (right, bottom), (210, 210, 210), 1)
    for idx, (x, y) in enumerate(series):
        pts = []
        for xv, yv in zip(np.asarray(x, dtype=np.float64), np.asarray(y, dtype=np.float64)):
            px = int(left + (xv - x_min) / (x_max - x_min) * (right - left))
            py = int(bottom - (yv - y_min) / (y_max - y_min) * (bottom - top))
            pts.append([px, py])
        if len(pts) >= 2:
            cv2.polylines(canvas, [np.asarray(pts, dtype=np.int32)], False, colors[idx % len(colors)], 2)
        elif len(pts) == 1:
            cv2.circle(canvas, tuple(pts[0]), 2, colors[idx % len(colors)], -1)
    cv2.imwrite(str(path), canvas)


def _write_mask_png(path: Path, mask: np.ndarray) -> None:
    import cv2

    arr = np.asarray(mask, dtype=np.uint8)
    cv2.imwrite(str(path), arr)


def _write_report(path: Path, result: dict[str, Any]) -> None:
    lines = [
        "# Pupil Geometry Report",
        "",
        "Phase 3.1 calibrates an effective pupil window in LCD physical coordinates using energy-based bar profiles and radius scans.",
        "It follows the old calibrating.py / ellipse.py physical model while using the current raw HDF5 path.",
        "This is not final scientific calibration and is not training-ready data.",
        "",
        f"- source_raw_h5: `{result['source_raw_h5']}`",
        f"- camera_profile_requested: `{result['camera_profile_requested']}`",
        f"- camera_profile_used: `{result['camera_profile_used']}`",
        f"- center: `{result['center']}`",
        f"- effective_radius: `{result['radius']:.6g}`",
        f"- ellipse: `{result['ellipse']}`",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _json_dataset(dataset: h5py.Dataset) -> dict[str, Any]:
    text = _read_scalar_str(dataset)
    return json.loads(text) if text else {}


def _read_scalar_str(dataset: h5py.Dataset) -> str:
    value = dataset[()]
    if isinstance(value, bytes):
        return value.decode("utf-8")
    if isinstance(value, np.bytes_):
        return bytes(value).decode("utf-8")
    return str(value)


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze Phase 3.1 pupil geometry calibration")
    parser.add_argument("--raw-h5", "--input", dest="raw_h5", required=True)
    parser.add_argument("--output-dir", default="outputs/pupil_geometry")
    args = parser.parse_args()
    result = analyze_pupil_geometry(args.raw_h5, args.output_dir)
    out = Path(args.output_dir) / "effective_pupil_window.json"
    print(f"effective pupil window written to {out}")
    print(f"radius: {result['radius']:.6g}")


if __name__ == "__main__":
    main()
