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
        description="Publish a peak-patch PSF dictionary as a measured-evidence handoff"
    )
    parser.add_argument("dictionary_h5")
    parser.add_argument("output_h5")
    parser.add_argument("--include-dense-diagnostic", action="store_true")
    args = parser.parse_args()

    _ensure_repo_on_path()
    from tasks.psf import publish_measured_evidence_handoff

    publish_measured_evidence_handoff(
        dictionary_h5=args.dictionary_h5,
        output_h5=args.output_h5,
        include_dense_diagnostic=args.include_dense_diagnostic,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
