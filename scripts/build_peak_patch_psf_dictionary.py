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
        description="Build a peak-patch PSF dictionary from raw capture and PeakLayoutProfile"
    )
    parser.add_argument("source_raw_capture_h5")
    parser.add_argument("peak_layout_profile")
    parser.add_argument("output_h5")
    parser.add_argument("--dictionary-id")
    parser.add_argument("--manifest-path")
    parser.add_argument("--pupil-profile-manifest")
    parser.add_argument("--camera-profile-manifest")
    parser.add_argument("--output-dtype", choices=["float32", "float64"], default="float32")
    parser.add_argument("--allow-camera-frame-extent-mismatch", action="store_true")
    parser.add_argument("--camera-frame-extent-mismatch-reason")
    parser.add_argument("--notes")
    args = parser.parse_args()

    _ensure_repo_on_path()
    from tasks.psf import build_peak_patch_psf_dictionary

    build_peak_patch_psf_dictionary(
        source_raw_capture_h5=args.source_raw_capture_h5,
        peak_layout_profile=args.peak_layout_profile,
        output_h5=args.output_h5,
        dictionary_id=args.dictionary_id,
        manifest_path=args.manifest_path,
        pupil_profile_manifest=args.pupil_profile_manifest,
        camera_profile_manifest=args.camera_profile_manifest,
        output_dtype=args.output_dtype,
        allow_camera_frame_extent_mismatch=args.allow_camera_frame_extent_mismatch,
        camera_frame_extent_mismatch_reason=args.camera_frame_extent_mismatch_reason,
        notes=args.notes,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
