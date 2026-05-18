#!/usr/bin/env python3
"""Clean Phase 3.1 radius-scan anomalies while preserving raw provenance."""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from typing import Any

import h5py
import numpy as np


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _resolve(path: str | Path) -> Path:
    p = Path(path)
    return p if p.is_absolute() else _repo_root() / p


def _timestamp() -> str:
    from datetime import datetime

    return datetime.now().strftime("%Y%m%d_%H%M%S")


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


def detect_single_point_spike(energies: np.ndarray) -> int:
    diff = np.diff(energies)
    scores = np.minimum(diff[:-1], -diff[1:])
    if scores.size == 0:
        raise ValueError("radius scan too short to detect single-point spike")
    spike_idx = int(np.argmax(scores) + 1)
    if scores[spike_idx - 1] <= 0:
        raise ValueError("failed to detect positive-then-negative single-point spike")
    return spike_idx


def detect_baseline_step(energies: np.ndarray, *, ignore_before: int) -> int:
    diff = np.diff(energies)
    start = max(int(ignore_before), 0)
    if start >= diff.size:
        raise ValueError("ignore_before is beyond radius-scan range")
    step_rel = int(np.argmax(diff[start:]))
    step_idx = start + step_rel + 1
    if diff[step_idx - 1] <= 0:
        raise ValueError("failed to detect positive baseline step")
    return step_idx


def clean_radius_scan(
    radii: np.ndarray,
    energies: np.ndarray,
    *,
    pre_window: int = 5,
    post_window: int = 10,
) -> dict[str, Any]:
    r = np.asarray(radii, dtype=np.float64)
    e_raw = np.asarray(energies, dtype=np.float64)
    e_clean = np.array(e_raw, copy=True)

    spike_idx = detect_single_point_spike(e_raw)
    spike_replacement = float(0.5 * (e_raw[spike_idx - 1] + e_raw[spike_idx + 1]))
    e_clean[spike_idx] = spike_replacement

    step_idx = detect_baseline_step(e_clean, ignore_before=spike_idx + 8)
    pre_start = max(0, step_idx - int(pre_window))
    pre_end = step_idx
    post_start = step_idx + 1
    post_end = min(e_clean.size, post_start + int(post_window))
    if pre_end - pre_start < 2:
        raise ValueError("baseline-step pre-window too short")
    if post_end - post_start < 2:
        raise ValueError("baseline-step post-window too short")

    pre_segment = np.array(e_clean[pre_start:pre_end], copy=True)
    post_segment = np.array(e_clean[post_start:post_end], copy=True)
    pre_median = float(np.median(pre_segment))
    post_median = float(np.median(post_segment))
    offset = float(post_median - pre_median)
    e_clean[step_idx:] = e_clean[step_idx:] - offset

    return {
        "radii": r,
        "energies_raw": e_raw,
        "energies_clean": e_clean,
        "spike": {
            "index": int(spike_idx),
            "radius": float(r[spike_idx]),
            "value_raw": float(e_raw[spike_idx]),
            "value_clean": float(spike_replacement),
            "left_neighbor": float(e_raw[spike_idx - 1]),
            "right_neighbor": float(e_raw[spike_idx + 1]),
        },
        "baseline_step": {
            "index": int(step_idx),
            "radius": float(r[step_idx]),
            "value_before": float(e_clean[step_idx - 1]),
            "value_after_raw": float(e_raw[step_idx]),
            "pre_window": [int(pre_start), int(pre_end)],
            "post_window": [int(post_start), int(post_end)],
            "pre_median": pre_median,
            "post_median": post_median,
            "offset_subtracted": float(offset),
        },
    }


def _update_processing_flags(raw_text: str, cleaning_meta: dict[str, Any]) -> str:
    flags = json.loads(raw_text)
    flags["cleaned"] = True
    flags["cleaning_kind"] = "radius_scan_single_point_interp_plus_baseline_step_offset"
    flags["analysis_valid"] = False
    flags["cleaning_summary"] = {
        "spike_index": cleaning_meta["spike"]["index"],
        "baseline_step_index": cleaning_meta["baseline_step"]["index"],
        "offset_subtracted": cleaning_meta["baseline_step"]["offset_subtracted"],
    }
    return json.dumps(flags, indent=2)


def clean_h5(
    *,
    input_h5: Path,
    contaminated_h5: Path,
    cleaned_h5: Path,
    output_dir: Path,
) -> dict[str, Any]:
    shutil.move(str(input_h5), str(contaminated_h5))
    shutil.copy2(str(contaminated_h5), str(cleaned_h5))

    with h5py.File(cleaned_h5, "r+") as f:
        radii = np.asarray(f["radius_scan/radii"], dtype=np.float64)
        energies = np.asarray(f["radius_scan/energies"], dtype=np.float64)
        cleaned = clean_radius_scan(radii, energies)
        f["radius_scan/energies"][...] = cleaned["energies_clean"]

        if "cleaning" in f:
            del f["cleaning"]
        grp = f.create_group("cleaning")
        grp.create_dataset(
            "radius_scan_cleaning_json",
            data=np.bytes_(json.dumps({
                "phase": "3.1",
                "task": "radius_scan_cleaning",
                "source_h5_before_cleaning": str(contaminated_h5),
                "cleaned_h5": str(cleaned_h5),
                "method": "single_point_linear_interpolation_plus_baseline_step_offset",
                "spike": cleaned["spike"],
                "baseline_step": cleaned["baseline_step"],
            }, indent=2).encode("utf-8")),
        )

        pf_raw = f["capture/processing_flags_json"][()]
        pf_text = pf_raw.decode("utf-8") if isinstance(pf_raw, (bytes, np.bytes_)) else str(pf_raw)
        del f["capture/processing_flags_json"]
        f["capture"].create_dataset(
            "processing_flags_json",
            data=np.bytes_(_update_processing_flags(pf_text, cleaned).encode("utf-8")),
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    _write_line_plot_png(
        output_dir / "radius_scan_raw_vs_cleaned.png",
        [(cleaned["radii"], cleaned["energies_raw"]), (cleaned["radii"], cleaned["energies_clean"])],
    )
    report = {
        "phase": "3.1",
        "task": "radius_scan_cleaning",
        "contaminated_h5": str(contaminated_h5),
        "cleaned_h5": str(cleaned_h5),
        "spike": cleaned["spike"],
        "baseline_step": cleaned["baseline_step"],
    }
    (output_dir / "radius_scan_cleaning_report.json").write_text(
        json.dumps(report, indent=2),
        encoding="utf-8",
    )
    (output_dir / "radius_scan_cleaning_report.md").write_text(
        "\n".join(
            [
                "# Radius Scan Cleaning Report",
                "",
                f"- Contaminated HDF5: `{contaminated_h5}`",
                f"- Cleaned HDF5: `{cleaned_h5}`",
                f"- Single-point spike: idx {report['spike']['index']} at r={report['spike']['radius']:.6f}, "
                f"replaced {report['spike']['value_raw']:.3f} -> {report['spike']['value_clean']:.3f}",
                f"- Baseline step: idx {report['baseline_step']['index']} at r={report['baseline_step']['radius']:.6f}",
                f"- Pre-window: {report['baseline_step']['pre_window']}",
                f"- Post-window: {report['baseline_step']['post_window']}",
                f"- Offset subtracted from step tail: {report['baseline_step']['offset_subtracted']:.3f}",
                "",
                "Cleaning policy:",
                "- single-point spike removed by linear interpolation from neighboring points",
                "- post-step tail shifted downward by the median gap between short pre-step and post-step plateau windows",
            ]
        ) + "\n",
        encoding="utf-8",
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Clean Phase 3.1 radius-scan anomalies")
    parser.add_argument("--input-h5", default="data/raw/bishe_pupil_geometry.h5")
    parser.add_argument("--cleaned-h5", default="data/raw/bishe_pupil_geometry.h5")
    parser.add_argument("--contaminated-h5", default=None)
    parser.add_argument("--output-dir", default="outputs/pupil_geometry")
    args = parser.parse_args()

    input_h5 = _resolve(args.input_h5)
    cleaned_h5 = _resolve(args.cleaned_h5)
    contaminated_h5 = (
        _resolve(args.contaminated_h5)
        if args.contaminated_h5
        else input_h5.with_name(f"{input_h5.stem}_rscan_contaminated_{_timestamp()}{input_h5.suffix}")
    )
    report = clean_h5(
        input_h5=input_h5,
        contaminated_h5=contaminated_h5,
        cleaned_h5=cleaned_h5,
        output_dir=_resolve(args.output_dir),
    )
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
