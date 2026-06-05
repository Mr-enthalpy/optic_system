#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
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
        "--valid-pixel-domain-json",
        default=None,
        help="JSON valid-pixel-domain policy, e.g. '{\"type\":\"exclude_top_rows\",\"top_rows\":16}'.",
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
        valid_pixel_domain=(
            json.loads(args.valid_pixel_domain_json)
            if args.valid_pixel_domain_json is not None else None
        ),
        notes=args.notes,
    )
    print(profile.to_json())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
