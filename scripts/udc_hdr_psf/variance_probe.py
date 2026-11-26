"""Within-batch or between-batch?  The wing flux fluctuates 10 % between two
identical exposures.  Each batch holds three frames, so the variance of a ring
sum splits into a repeatable per-batch part and a frame-to-frame part.

  mostly within  -> photon/read noise, and more frames per batch would fix it
  mostly between -> the illumination moved between batches, and no averaging
                    inside a batch can fix it
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


def exposure_of(batch):
    for run_json in sorted(batch.glob("export/device_files/captures/run_*/run.json")):
        body = json.loads(run_json.read_text(encoding="utf-8-sig"))
        for frame in body.get("frames") or []:
            got = frame.get("actually_exposure_ns")
            if isinstance(got, (int, float)):
                return float(got) / 1000.0
        req = body.get("requested_exposure_ns")
        if isinstance(req, (int, float)):
            return float(req) / 1000.0
    return None


for label in LABELS:
    directory = newest(label)
    print("\n=====", directory.name)
    batches = []
    for batch in sorted(p for p in directory.glob("batch_*") if p.is_dir()):
        files = sorted((batch / "decoded").glob("frame_*.npy"))
        if not files:
            continue
        batches.append((exposure_of(batch), [np.load(f).astype(np.float32) for f in files]))
    if not batches:
        continue
    shortest = min(b[0] for b in batches)
    refs = [b for b in batches if abs(b[0] - shortest) < 1e-6]
    py, px = np.unravel_index(int(np.argmax(refs[0][1][0])), refs[0][1][0].shape)
    yy, xx = np.mgrid[0:refs[0][1][0].shape[0], 0:refs[0][1][0].shape[1]]
    radius = np.hypot(yy - py, xx - px)
    print("  {} identical batches at {:.0f} us, {} frames each".format(
        len(refs), shortest, len(refs[0][1])))
    for r0, r1 in RINGS:
        ring = (radius >= r0) & (radius < r1)
        rows = []
        for _, frames in refs:
            vals = [float((f[ring] - BLACK).sum()) for f in frames]
            rows.append(vals)
        arr = np.asarray(rows)
        batch_means = arr.mean(axis=1)
        within = float(arr.std(axis=1, ddof=1).mean())
        between = float(batch_means.std(ddof=1))
        level = float(batch_means.mean())
        if level <= 0:
            continue
        print("     r{}-{}  level {:8.2f} DN  frame sd {:6.2f} ({:5.2f}%)  "
              "batch sd {:6.2f} ({:5.2f}%)  N {}x{}".format(
                  r0, r1, level, within, 100 * within / level,
                  between, 100 * between / level,
                  len(refs), arr.shape[1]))
