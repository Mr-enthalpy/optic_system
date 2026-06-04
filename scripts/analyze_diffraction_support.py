#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _ensure_sys_path() -> None:
    root = str(_repo_root())
    if root not in sys.path:
        sys.path.insert(0, root)


_ensure_sys_path()

from tasks.psf.analyze_diffraction_support import (  # noqa: E402
    DEFAULT_SUPPORT_RADII,
    DEFAULT_TAU_VALUES,
    SUPPORT_ANALYSIS_PRESETS,
    analyze_diffraction_support,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build a PeakSupportAnalysisReport from a FullFramePSFSurvey HDF5."
    )
    parser.add_argument("survey_h5", help="Input FullFramePSFSurvey HDF5")
    parser.add_argument("output_h5", help="Output PeakSupportAnalysisReport HDF5")
    parser.add_argument("--report-id", default=None)
    parser.add_argument("--tau", dest="tau_values", type=float, nargs="+", default=DEFAULT_TAU_VALUES)
    parser.add_argument("--support-radii", type=float, nargs="+", default=DEFAULT_SUPPORT_RADII)
    parser.add_argument("--far-field-radius", type=float, default=None)
    parser.add_argument("--bg-percentile", type=float, default=None)
    parser.add_argument("--min-component-area", type=int, default=None)
    parser.add_argument("--connectivity", type=int, choices=(4, 8), default=None)
    parser.add_argument(
        "--preset",
        choices=sorted(SUPPORT_ANALYSIS_PRESETS),
        default=None,
        help=(
            "Named analysis preset. measured_full_frame_2048 keeps synthetic defaults untouched "
            "but uses min_component_area=8 unless explicitly overridden."
        ),
    )
    parser.add_argument(
        "--energy-only",
        action="store_true",
        help="Compute energy metrics only and intentionally skip the connected-component table.",
    )
    parser.add_argument(
        "--allow-raw-fallback",
        action="store_true",
        help="Allow legacy/dev raw/frames_avg inputs instead of a FullFramePSFSurvey.",
    )
    parser.add_argument(
        "--center-policy",
        choices=("frame_center", "manual_xy", "brightest_component", "sensor_energy_center_profile"),
        default="frame_center",
    )
    parser.add_argument("--manual-center-xy", type=float, nargs=2, default=None)
    parser.add_argument(
        "--center-profile",
        default=None,
        help="SensorEnergyCenterProfile JSON. Implies center-policy=sensor_energy_center_profile.",
    )
    parser.add_argument("--notes", default=None)
    args = parser.parse_args()

    manifest = analyze_diffraction_support(
        args.survey_h5,
        args.output_h5,
        report_id=args.report_id,
        tau_values=args.tau_values,
        support_radii=args.support_radii,
        far_field_radius=args.far_field_radius,
        bg_percentile=args.bg_percentile,
        min_component_area=args.min_component_area,
        connectivity=args.connectivity,
        center_policy=args.center_policy,
        manual_center_xy=tuple(args.manual_center_xy) if args.manual_center_xy is not None else None,
        center_profile=args.center_profile,
        allow_raw_fallback=bool(args.allow_raw_fallback),
        energy_only=bool(args.energy_only),
        preset_name=args.preset,
        notes=args.notes,
    )
    print(manifest.to_json_text())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
