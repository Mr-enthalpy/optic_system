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
    parser.add_argument("--roi", type=int, nargs=4, metavar=("X", "Y", "WIDTH", "HEIGHT"), required=True)
    parser.add_argument("--output", required=True, help="new .npz analysis file")
    args = parser.parse_args(argv)
    import numpy as np
    from tasks.psf.roi import ROI, extract_roi_psf
    output = Path(args.output)
    if output.suffix != ".npz" or output.exists():
        parser.error("output must be a new .npz path")
    result = extract_roi_psf(args.survey, wavelength_index=args.wavelength_index, display_index=args.display_index, roi=ROI(*args.roi))
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("xb") as stream:
        np.savez_compressed(stream, psf=result.psf, metadata_json=json.dumps(result.metadata, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
