"""Is the 4-6 % seam error caused by the level-dependent pixel mask?

The current seam test selects pixels with ``short >= 4 DN and long >= 4 DN``.
On a steeply falling PSF the *long* frame lets more faint outer pixels of an
annulus through that gate, so the median is taken over a dimmer subset and the
ratio falls below 1 -- a bias that grows with exposure and has nothing to do
with the sensor or the lamp.

Three masks are compared on the same data:

  sym     both frames >= 4 DN (what psf_analysis does today)
  short   only the short frame >= 4 DN, long frame below ceiling
  lowest  the band's dimmest tier >= 4 DN, i.e. one pixel set per band

Under ``lowest`` every pair sees the same pixels, so any remaining deviation is
a real response change.  Each frame's robust centroid is printed too, because a
sub-pixel wander of the arc would produce scatter of either sign.
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


def centroid(signal, py, px, radius=8):
    y0, y1 = max(0, py - radius), py + radius + 1
    x0, x1 = max(0, px - radius), px + radius + 1
    sub = np.clip(signal[y0:y1, x0:x1], 0, None)
    if sub.sum() <= 0:
        return float("nan"), float("nan")
    yy, xx = np.mgrid[y0:y1, x0:x1]
    return float((sub * yy).sum() / sub.sum()), float((sub * xx).sum() / sub.sum())


for label in LABELS:
    directory = newest(label)
    if directory is None:
        print("=====", label, "MISSING")
        continue
    frames, exposures = load_band(directory)
    print("\n=====", directory.name, "  batches", len(frames))
    order = int(np.argmin(exposures))
    ref = frames[order] - BLACK
    py, px = np.unravel_index(int(np.argmax(ref)), ref.shape)
    yy, xx = np.mgrid[0:ref.shape[0], 0:ref.shape[1]]
    radius = np.hypot(yy - py, xx - px)
    lowest = frames[order] - BLACK
    print("  peak reference ({}, {}) at {:.0f} us".format(px, py, exposures[order]))
    for i, (frame, exposure) in enumerate(zip(frames, exposures)):
        cy, cx = centroid(frame - BLACK, py, px)
        peak = float(np.percentile(frame[radius < 3], 99.9)) - BLACK
        print("    batch {:02d} {:9.0f} us  centroid ({:8.3f},{:8.3f})  peak {:7.1f} DN  {}".format(
            i, exposure, cy, cx, peak, "OVER" if peak > CEILING - BLACK else ""))

    ranked = list(np.argsort(np.asarray(exposures)))
    for short_i, long_i in zip(ranked[:-1], ranked[1:]):
        factor = exposures[long_i] / exposures[short_i]
        short = frames[short_i] - BLACK
        long_ = frames[long_i] - BLACK
        print("  {:.0f} -> {:.0f} us  x{:.2f}".format(
            exposures[short_i], exposures[long_i], factor))
        for r0, r1 in RINGS:
            band = (radius >= r0) & (radius < r1)
            masks = {
                "sym": band & (short >= 4) & (long_ >= 4)
                        & (frames[short_i] < CEILING) & (frames[long_i] < CEILING),
                "short": band & (short >= 4) & (frames[long_i] < CEILING),
                "lowest": band & (lowest >= 4) & (frames[short_i] < CEILING)
                          & (frames[long_i] < CEILING),
            }
            row = []
            for name, mask in masks.items():
                count = int(mask.sum())
                if count < 20:
                    row.append("{:>5} ---   ".format(name))
                    continue
                s = float(np.median(short[mask]))
                l = float(np.median(long_[mask]))
                ratio = (l / factor) / s if s else float("nan")
                row.append("{:>5} {:5.3f}".format(name, ratio))
            print("     r{}-{}  {}   ({} px)".format(r0, r1, "  ".join(row), int(band.sum())))
