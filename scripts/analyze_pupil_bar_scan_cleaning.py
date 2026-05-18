#!/usr/bin/env python3
"""Offline bar-scan cleaning for Phase 3.1 rerun diagnostics."""

from __future__ import annotations

import argparse
import json
import sys
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

from scripts.analyze_pupil_geometry import _write_line_plot_png
from tasks.pupil_geometry_model import solve_aperture_from_profiles


def analyze_bar_scan_cleaning(
    *,
    x_pos_path: str | Path,
    x_enr_path: str | Path,
    y_pos_path: str | Path,
    y_enr_path: str | Path,
    output_dir: str | Path,
) -> dict[str, Any]:
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    pos_x = np.load(str(x_pos_path))
    enr_x = np.load(str(x_enr_path))
    pos_y = np.load(str(y_pos_path))
    enr_y = np.load(str(y_enr_path))

    raw_fit = solve_aperture_from_profiles(pos_x, enr_x, pos_y, enr_y)
    jump = detect_primary_jump(pos_x, enr_x)
    cleaned = clean_shifted_profiles(pos_x, enr_x, pos_y, enr_y, jump_index=jump["jump_index"])
    cleaned_fit = solve_aperture_from_profiles(
        cleaned["pos_x"],
        cleaned["enr_x_clean"],
        cleaned["pos_y"],
        cleaned["enr_y_clean"],
    )

    _write_line_plot_png(
        out_dir / "bar_profile_x_raw_vs_cleaned.png",
        [
            (cleaned["pos_x"], cleaned["enr_x_raw"]),
            (cleaned["pos_x"], cleaned["enr_x_clean"]),
        ],
    )
    _write_line_plot_png(
        out_dir / "bar_profile_y_raw_vs_cleaned.png",
        [
            (cleaned["pos_y"], cleaned["enr_y_raw"]),
            (cleaned["pos_y"], cleaned["enr_y_clean"]),
        ],
    )
    _write_line_plot_png(
        out_dir / "bar_profile_xy_cleaned.png",
        [
            (cleaned["pos_x"], cleaned["enr_x_clean"]),
            (cleaned["pos_y"], cleaned["enr_y_clean"]),
        ],
    )

    result = {
        "phase": "3.1",
        "task": "bar_scan_cleaning_diagnostic",
        "input": {
            "x_pos_path": str(x_pos_path),
            "x_enr_path": str(x_enr_path),
            "y_pos_path": str(y_pos_path),
            "y_enr_path": str(y_enr_path),
        },
        "jump_detection": jump,
        "cleaning": {
            "x_kept_from_index": int(cleaned["x_keep_from_index"]),
            "x_kept_from_position": float(cleaned["x_kept_from_position"]),
            "x_base_offset": float(cleaned["x_base_offset"]),
            "y_base_offset": float(cleaned["y_base_offset"]),
            "base_window_radius_px": float(cleaned["base_window_radius_px"]),
        },
        "raw_fit": {k: float(v) for k, v in raw_fit.items()},
        "cleaned_fit": {k: float(v) for k, v in cleaned_fit.items()},
    }
    (out_dir / "bar_scan_cleaning_report.json").write_text(
        json.dumps(result, indent=2),
        encoding="utf-8",
    )
    (out_dir / "bar_scan_cleaning_report.md").write_text(
        build_report(result),
        encoding="utf-8",
    )
    return result


def detect_primary_jump(pos_x: np.ndarray, enr_x: np.ndarray) -> dict[str, Any]:
    diff = np.diff(np.asarray(enr_x, dtype=np.float64))
    jump_index = int(np.argmax(diff))
    return {
        "jump_index": jump_index,
        "jump_position_before": float(pos_x[jump_index]),
        "jump_position_after": float(pos_x[jump_index + 1]),
        "jump_delta": float(diff[jump_index]),
    }


def clean_shifted_profiles(
    pos_x: np.ndarray,
    enr_x: np.ndarray,
    pos_y: np.ndarray,
    enr_y: np.ndarray,
    *,
    jump_index: int,
) -> dict[str, Any]:
    pos_x = np.asarray(pos_x, dtype=np.float64)
    enr_x = np.asarray(enr_x, dtype=np.float64)
    pos_y = np.asarray(pos_y, dtype=np.float64)
    enr_y = np.asarray(enr_y, dtype=np.float64)

    x_keep_from = int(jump_index + 1)
    pos_x_kept = pos_x[x_keep_from:]
    enr_x_kept = enr_x[x_keep_from:]

    # Estimate the shifted baseline from post-jump X regions away from the pupil peak.
    x_peak_idx = int(np.argmax(enr_x_kept))
    x_peak_pos = float(pos_x_kept[x_peak_idx])
    base_window_radius_px = max(80.0, 0.18 * float(pos_x_kept[-1] - pos_x_kept[0]))
    x_base_mask = np.abs(pos_x_kept - x_peak_pos) >= base_window_radius_px
    if int(np.count_nonzero(x_base_mask)) < 8:
        x_base_mask = np.ones_like(pos_x_kept, dtype=bool)
    x_base_offset = float(np.median(enr_x_kept[x_base_mask]))

    # Y was scanned entirely after the jump, so reuse the shifted state and subtract
    # its own outer-domain median away from the main peak.
    y_peak_idx = int(np.argmax(enr_y))
    y_peak_pos = float(pos_y[y_peak_idx])
    y_base_mask = np.abs(pos_y - y_peak_pos) >= base_window_radius_px
    if int(np.count_nonzero(y_base_mask)) < 8:
        y_base_mask = np.ones_like(pos_y, dtype=bool)
    y_base_offset = float(np.median(enr_y[y_base_mask]))

    enr_x_clean = np.maximum(enr_x_kept - x_base_offset, 0.0)
    enr_y_clean = np.maximum(enr_y - y_base_offset, 0.0)
    return {
        "pos_x": pos_x_kept,
        "enr_x_raw": enr_x_kept,
        "enr_x_clean": enr_x_clean,
        "pos_y": pos_y,
        "enr_y_raw": enr_y,
        "enr_y_clean": enr_y_clean,
        "x_keep_from_index": x_keep_from,
        "x_kept_from_position": float(pos_x[x_keep_from]),
        "x_base_offset": x_base_offset,
        "y_base_offset": y_base_offset,
        "base_window_radius_px": base_window_radius_px,
    }


def build_report(result: dict[str, Any]) -> str:
    lines = [
        "# Bar Scan Cleaning Report",
        "",
        f"- X jump: index {result['jump_detection']['jump_index']}, "
        f"position {result['jump_detection']['jump_position_before']:.3f} -> "
        f"{result['jump_detection']['jump_position_after']:.3f}, "
        f"delta {result['jump_detection']['jump_delta']:.3f}",
        f"- X kept from: index {result['cleaning']['x_kept_from_index']}, "
        f"position {result['cleaning']['x_kept_from_position']:.3f}",
        f"- X base offset: {result['cleaning']['x_base_offset']:.3f}",
        f"- Y base offset: {result['cleaning']['y_base_offset']:.3f}",
        "",
        f"- Raw fit r_x={result['raw_fit']['r_x']:.3f}, "
        f"r_y={result['raw_fit']['r_y']:.3f}, "
        f"r_avg={result['raw_fit']['r_avg']:.3f}",
        f"- Cleaned fit r_x={result['cleaned_fit']['r_x']:.3f}, "
        f"r_y={result['cleaned_fit']['r_y']:.3f}, "
        f"r_avg={result['cleaned_fit']['r_avg']:.3f}",
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Offline bar-scan cleaning diagnostic")
    parser.add_argument("--x-pos", default="outputs/pupil_geometry/bar_scan_x_pos.npy")
    parser.add_argument("--x-enr", default="outputs/pupil_geometry/bar_scan_x_enr.npy")
    parser.add_argument("--y-pos", default="outputs/pupil_geometry/bar_scan_y_pos.npy")
    parser.add_argument("--y-enr", default="outputs/pupil_geometry/bar_scan_y_enr.npy")
    parser.add_argument("--output-dir", default="outputs/pupil_geometry")
    args = parser.parse_args()
    result = analyze_bar_scan_cleaning(
        x_pos_path=args.x_pos,
        x_enr_path=args.x_enr,
        y_pos_path=args.y_pos,
        y_enr_path=args.y_enr,
        output_dir=args.output_dir,
    )
    print(json.dumps(result["cleaned_fit"], indent=2))


if __name__ == "__main__":
    main()
