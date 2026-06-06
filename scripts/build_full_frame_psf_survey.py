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
        description="Build a small full-frame PSF scout survey from raw capture HDF5"
    )
    parser.add_argument("source_raw_capture_h5")
    parser.add_argument("output_h5")
    parser.add_argument("--survey-id")
    parser.add_argument("--manifest-path")
    parser.add_argument("--pupil-profile-manifest")
    parser.add_argument("--camera-profile-manifest")
    parser.add_argument("--notes")
    args = parser.parse_args()

    _ensure_repo_on_path()
    from tasks.psf import build_full_frame_psf_survey

    build_full_frame_psf_survey(
        source_raw_capture_h5=args.source_raw_capture_h5,
        output_h5=args.output_h5,
        survey_id=args.survey_id,
        manifest_path=args.manifest_path,
        pupil_profile_manifest=args.pupil_profile_manifest,
        camera_profile_manifest=args.camera_profile_manifest,
        notes=args.notes,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
