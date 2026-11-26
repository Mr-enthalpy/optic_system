"""Compare the opening and closing dark sets.

Both are three batches at 1171 / 18736 / 400000 us, source occluded, at the two
ends of a 2.8 hour session.  They are the only measurement of whether the
instrument's baseline moved while the bands were being captured, so the numbers
are worth printing rather than assuming: a black level that drifts, or a plane
that gains offset, changes what "above dark" means for every band in between.

    python dark_end_probe.py <session root>
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import psf_analysis as psf  # noqa: E402

WINDOW = (1454, 1136, 176, 176)
PLANES = ("B", "Gr", "Gb", "R")


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def plane_means(mean: np.ndarray):
    yy, xx = np.indices(mean.shape)
    return {name: float(mean[mask].mean())
            for name, mask in psf.plane_masks(yy, xx).items()}


def main() -> int:
    session = Path(sys.argv[1]).resolve()
    start = session / "captures" / "dark_start"
    end = session / "captures" / "dark_end"
    if not end.exists():
        raise SystemExit("no closing dark set")
    plan_start = load(start / "plan.json")
    plan_end = load(end / "plan.json")
    if [b["exposure_us"] for b in plan_start["batches"]] != \
            [b["exposure_us"] for b in plan_end["batches"]]:
        raise SystemExit("the two dark sets do not share an exposure ladder")

    print("| exposure | plane | start DN | end DN | change DN | change % |")
    print("|" + "---|" * 6)
    for index, batch in enumerate(plan_start["batches"]):
        a = np.load(start / f"batch_{index:03d}" / "decoded" / "temporal_mean_dn.npy",
                    mmap_mode="r")
        b = np.load(end / f"batch_{index:03d}" / "decoded" / "temporal_mean_dn.npy",
                    mmap_mode="r")
        a = np.asarray(a, dtype=np.float64)
        b = np.asarray(b, dtype=np.float64)
        if a.shape != b.shape:
            raise SystemExit(f"shape mismatch at batch {index}: {a.shape} vs {b.shape}")
        ma, mb = plane_means(a), plane_means(b)
        for plane in PLANES:
            change = mb[plane] - ma[plane]
            share = 100 * change / ma[plane] if ma[plane] else float("nan")
            print(f"| {batch['exposure_us']} | {plane} | {ma[plane]:.3f} | "
                  f"{mb[plane]:.3f} | {change:+.3f} | {share:+.2f} % |")

    print()
    print("window used by the bands, at the shortest exposure:")
    a = np.asarray(np.load(start / "batch_000" / "decoded" / "temporal_mean_dn.npy",
                           mmap_mode="r"), dtype=np.float64)
    b = np.asarray(np.load(end / "batch_000" / "decoded" / "temporal_mean_dn.npy",
                           mmap_mode="r"), dtype=np.float64)
    wa = psf.crop(a, list(WINDOW))
    wb = psf.crop(b, list(WINDOW))
    radius, yy, xx = psf.radius_grid(wa.shape, (88, 88))
    inside = radius <= 20
    for plane, mask in psf.plane_masks(yy, xx).items():
        m = mask & inside
        va, vb = float(wa[m].mean()), float(wb[m].mean())
        print(f"  {plane}: start {va:.3f} DN, end {vb:.3f} DN, change {vb - va:+.3f} DN")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
