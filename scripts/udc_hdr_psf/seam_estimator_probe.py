"""Median versus ring-integrated flux as the seam statistic.

The recorded gate judges a seam with the *median* of an annulus.  On a steeply
falling PSF that estimator discards all but the middle pixels and its own
repeatability is several percent -- the identical-exposure pairs (x1.00) in the
same bracket show it directly, without any assumption about the source.

This probe recomputes every pair, including the x1.00 controls, two ways:

  median  what psf_analysis.annulus_seams does today
  flux    sum over the whole ring, which uses all 36..400 pixels

A criterion is only meaningful if its control pairs sit far inside it.  The
x1.00 rows are that control.
"""
import json
import os
import sys
from pathlib import Path

import numpy as np

# The capture tree is data: it is named by the environment, never baked in.
ROOT = Path(os.environ.get("UDC_CAPTURES", "captures"))
BLACK = 64.0
CEILING = 1023.0
RINGS = ((2, 4), (4, 6), (6, 8), (8, 11), (11, 14), (14, 18))
LABELS = sys.argv[1:] or ["gate_450nm", "gate_650nm"]


def newest(label):
    found = sorted(p for p in ROOT.glob(label + "*") if p.is_dir())
    return found[-1] if found else None


def load_band(directory):
    frames, exposures = [], []
    for batch in sorted(p for p in directory.glob("batch_*") if p.is_dir()):
        mean = batch / "decoded/temporal_mean_dn.npy"
        if not mean.exists():
            continue
        frames.append(np.load(mean).astype(np.float32))
        exposure = None
        for run_json in sorted(batch.glob("export/device_files/captures/run_*/run.json")):
            body = json.loads(run_json.read_text(encoding="utf-8-sig"))
            for frame in body.get("frames") or []:
                got = frame.get("actually_exposure_ns")
                if isinstance(got, (int, float)):
                    exposure = float(got) / 1000.0
                    break
            if exposure is None:
                req = body.get("requested_exposure_ns")
                if isinstance(req, (int, float)):
                    exposure = float(req) / 1000.0
            if exposure is not None:
                break
        exposures.append(exposure)
    return frames, exposures


for label in LABELS:
    directory = newest(label)
    if directory is None:
        print("=====", label, "MISSING")
        continue
    frames, exposures = load_band(directory)
    order = int(np.argmin(exposures))
    ref = frames[order] - BLACK
    py, px = np.unravel_index(int(np.argmax(ref)), ref.shape)
    yy, xx = np.mgrid[0:ref.shape[0], 0:ref.shape[1]]
    radius = np.hypot(yy - py, xx - px)
    print("\n=====", directory.name, " peak", (px, py), "at", exposures[order], "us")

    ranked = list(np.argsort(np.asarray(exposures)))
    worst = {"median": 0.0, "flux": 0.0}
    worst_null = {"median": 0.0, "flux": 0.0}
    for short_i, long_i in zip(ranked[:-1], ranked[1:]):
        factor = exposures[long_i] / exposures[short_i]
        short = frames[short_i] - BLACK
        long_ = frames[long_i] - BLACK
        null = abs(factor - 1.0) < 1e-9
        print("  {:.0f} -> {:.0f} us  x{:.2f}{}".format(
            exposures[short_i], exposures[long_i], factor, "   [x1.00 control]" if null else ""))
        for r0, r1 in RINGS:
            ring = (radius >= r0) & (radius < r1)
            unclipped = ring & (frames[short_i] < CEILING) & (frames[long_i] < CEILING)
            clipped = 1.0 - unclipped.sum() / max(ring.sum(), 1)
            lit = unclipped & (short >= 4.0) & (long_ >= 4.0)
            text = "     r{}-{}\t".format(r0, r1)
            if lit.sum() >= 20:
                s_med = float(np.median(short[lit]))
                l_med = float(np.median(long_[lit]))
                med = (l_med / factor) / s_med if s_med else float("nan")
                text += "med {:5.3f} ({:6.1f} DN)  ".format(med, s_med)
            else:
                med = None
                text += "med   ---            "
            if unclipped.sum() >= 20:
                s_sum = float(short[unclipped].sum())
                l_sum = float(long_[unclipped].sum())
                flux = (l_sum / factor) / s_sum if s_sum else float("nan")
                text += "flux {:5.3f}  clipped {:4.1f}%".format(flux, 100 * clipped)
            else:
                flux = None
                text += "flux  ---"
            print(text)
            if med is not None:
                target = worst_null if null else worst
                target["median"] = max(target["median"], abs(med - 1.0))
            if flux is not None and clipped < 0.02:
                target = worst_null if null else worst
                target["flux"] = max(target["flux"], abs(flux - 1.0))
    print("  worst deviation, x1.00 controls : median {:5.2f}%  flux {:5.2f}%".format(
        100 * worst_null["median"], 100 * worst_null["flux"]))
    print("  worst deviation, real pairs     : median {:5.2f}%  flux {:5.2f}%".format(
        100 * worst["median"], 100 * worst["flux"]))
