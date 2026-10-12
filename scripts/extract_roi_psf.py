"""Extract one dense ROI as an analysis NPZ plus JSON measurement metadata."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--survey", required=True)
    parser.add_argument("--wavelength-index", type=int, required=True)
    parser.add_argument("--display-index", type=int, required=True)
    origin = parser.add_mutually_exclusive_group(required=True)
    origin.add_argument("--roi", type=int, nargs=4, metavar=("X", "Y", "WIDTH", "HEIGHT"))
    origin.add_argument("--center-profile", help="center derived from this survey at its current location")
    parser.add_argument("--roi-shape", type=int, nargs=2, metavar=("HEIGHT", "WIDTH"))
    parser.add_argument("--output", required=True, help="new .npz analysis file")
    args = parser.parse_args(argv)
    if bool(args.center_profile) != bool(args.roi_shape):
        parser.error("--roi-shape is required only with --center-profile")
    import numpy as np
    from tasks.psf.roi import ROI, extract_roi_psf
    from tasks.psf.sensor_energy_center import SensorEnergyCenterProfile
    output = Path(args.output)
    if output.suffix != ".npz" or output.exists():
        parser.error("output must be a new .npz path")
    try:
        result = extract_roi_psf(
            args.survey, wavelength_index=args.wavelength_index, display_index=args.display_index,
            roi=ROI(*args.roi) if args.roi else None,
            center_profile=SensorEnergyCenterProfile.load_json(args.center_profile) if args.center_profile else None,
            roi_shape_hw=tuple(args.roi_shape) if args.roi_shape else None,
        )
    except (OSError, ValueError, KeyError) as exc:
        parser.exit(2, f"ROI extraction failed: {exc}\n")
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("xb") as stream:
        np.savez_compressed(stream, psf=result.psf, metadata_json=json.dumps(result.metadata, sort_keys=True))
    print(json.dumps({"shape_hw": list(result.psf.shape), "total_intensity": float(result.psf.sum()),
        "max_intensity": float(result.psf.max()), "wavelength_nm": result.metadata["wavelength_nm"],
        "normalization": "none"}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
