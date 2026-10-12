"""Estimate a sensor energy center from an offline measured survey."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--survey", required=True)
    parser.add_argument("--output", required=True, help="new center profile JSON path")
    parser.add_argument("--background-percentile", type=float, default=5.0)
    parser.add_argument("--valid-pixel-mask", help="boolean sensor mask .npy")
    args = parser.parse_args(argv)
    import numpy as np
    from tasks.psf.sensor_energy_center import derive_sensor_energy_center_profile
    try:
        valid = np.load(args.valid_pixel_mask, allow_pickle=False) if args.valid_pixel_mask else None
        profile = derive_sensor_energy_center_profile(
            Path(args.survey).resolve(), args.output,
            bg_percentile=args.background_percentile, valid_pixel_mask=valid,
        )
    except (OSError, ValueError, KeyError) as exc:
        parser.exit(2, f"Sensor center estimation failed: {exc}\n")
    print(f"Sensor center (x, y): {profile.center_xy}; output: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
