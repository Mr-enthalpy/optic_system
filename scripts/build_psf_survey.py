"""Convert a complete successful RawCapture v3 into a measured survey v1."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw", required=True)
    parser.add_argument("--pupil-profile", required=True)
    parser.add_argument("--camera-profile", required=True)
    parser.add_argument("--output", required=True, help="new survey HDF5 path")
    parser.add_argument("--manifest", help="new JSON path; defaults beside the survey")
    parser.add_argument("--allow-large-survey", action="store_true")
    args = parser.parse_args(argv)
    from tasks.psf.build_full_frame_psf_survey import build_full_frame_psf_survey
    try:
        manifest = build_full_frame_psf_survey(
            source_raw_capture_h5=args.raw, output_h5=args.output,
            pupil_profile_manifest=args.pupil_profile,
            camera_profile_manifest=args.camera_profile,
            manifest_path=args.manifest, allow_large_survey=args.allow_large_survey,
        )
    except (OSError, ValueError, KeyError) as exc:
        parser.exit(2, f"Survey conversion failed: {exc}\n")
    print(f"Survey: {args.output}; {len(manifest.entry_wavelengths_nm)} entries, {manifest.frame_shape}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
