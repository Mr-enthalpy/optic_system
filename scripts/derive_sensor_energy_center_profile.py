#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _ensure_repo_on_path() -> None:
    root = str(_repo_root())
    if root not in sys.path:
        sys.path.insert(0, root)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Derive a SensorEnergyCenterProfile from a FullFramePSFSurvey HDF5."
    )
    parser.add_argument("survey_h5")
    parser.add_argument("output_json")
    parser.add_argument("--center-profile-id")
    parser.add_argument("--bg-percentile", type=float, default=5.0)
    parser.add_argument(
        "--allow-raw-fallback",
        action="store_true",
        help="Allow legacy/dev raw/frames_avg inputs instead of a FullFramePSFSurvey.",
    )
    parser.add_argument("--notes")
    args = parser.parse_args()

    _ensure_repo_on_path()
    from tasks.psf import derive_sensor_energy_center_profile

    profile = derive_sensor_energy_center_profile(
        args.survey_h5,
        args.output_json,
        center_profile_id=args.center_profile_id,
        bg_percentile=args.bg_percentile,
        allow_raw_fallback=bool(args.allow_raw_fallback),
        notes=args.notes,
    )
    print(profile.to_json())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
