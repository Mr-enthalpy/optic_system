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
        description="Derive a peak layout profile from a full-frame PSF scout survey"
    )
    parser.add_argument("survey_h5")
    parser.add_argument("output_json")
    parser.add_argument("--peak-layout-id")
    parser.add_argument("--patch-shape", nargs=2, type=int, metavar=("H", "W"), default=(9, 9))
    parser.add_argument("--threshold-sigma", type=float, default=3.0)
    parser.add_argument("--min-area", type=int, default=1)
    parser.add_argument("--max-peaks", type=int)
    parser.add_argument("--notes")
    args = parser.parse_args()

    _ensure_repo_on_path()
    from tasks.psf import derive_peak_layout_profile

    derive_peak_layout_profile(
        survey_h5=args.survey_h5,
        output_json=args.output_json,
        peak_layout_id=args.peak_layout_id,
        patch_shape_hw=tuple(args.patch_shape),
        threshold_sigma=args.threshold_sigma,
        min_area=args.min_area,
        max_peaks=args.max_peaks,
        notes=args.notes,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
