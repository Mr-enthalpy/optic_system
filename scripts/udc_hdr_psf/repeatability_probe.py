"""Repeatability of the annulus medians from the five identical reference batches.

If the same exposure, repeated five times inside one bracket, scatters by as much
as a seam is allowed to, the seam criterion is below the measurement's own
repeatability -- and no exposure model can fix that.
"""
import json
import os
import sys
from pathlib import Path

import numpy as np

# The capture tree is data: it is named by the environment, never baked in.
ROOT = Path(os.environ.get("UDC_CAPTURES", "captures"))
BLACK = 64.0
RINGS = ((2, 4), (4, 6), (6, 8), (8, 11), (11, 14), (14, 18), (18, 24))
LABELS = sys.argv[1:] or ["gate_450nm_r1", "gate_650nm_r1"]


def newest(label):
    found = sorted(p for p in ROOT.glob(label + "*") if p.is_dir())
    return found[-1] if found else None


for label in LABELS:
    directory = newest(label)
    if directory is None:
        print("=====", label, "MISSING")
        continue
    print("=====", directory.name)
    batches = sorted(p for p in directory.glob("batch_*") if p.is_dir())
    exposures = []
    frames = []
    for batch in batches:
        files = sorted((batch / "decoded").glob("frame_*.npy"))
        if files:
            img = np.median(np.stack([np.load(f) for f in files]).astype(np.float64), axis=0)
        else:
            img = np.load(batch / "decoded/temporal_mean_dn.npy").astype(np.float64)
        frames.append(img - BLACK)
        for run_json in sorted(batch.glob("export/device_files/captures/run_*/run.json")):
            body = json.loads(run_json.read_text(encoding="utf-8-sig"))
            exposures.append(body.get("requested_exposure_ns", 0) / 1000.0)
            break
    # the peak reference is the first batch (the shortest tier)
    py, px = np.unravel_index(int(np.argmax(frames[0])), frames[0].shape)
    yy, xx = np.mgrid[0:frames[0].shape[0], 0:frames[0].shape[1]]
    radius = np.hypot(yy - py, xx - px)
    print(f"  peak reference ({px},{py}), batches {len(frames)}")
    repeated = [i for i, e in enumerate(exposures) if e == exposures[0]]
    print(f"  identical-exposure batches at {exposures[0]:.0f} us: {repeated}")
    for lo, hi in RINGS:
        sel = (radius >= lo) & (radius < hi)
        if sel.sum() < 8:
            continue
        values = [float(np.median(f[sel])) for i, f in enumerate(frames) if i in repeated]
        if len(values) < 3:
            continue
        arr = np.asarray(values)
        spread = float(arr.max() - arr.min())
        sd = float(arr.std(ddof=1))
        print(f"  r{lo}-{hi}px ({int(sel.sum())} px): "
              + " ".join(f"{v:7.2f}" for v in values)
              + f"   range {spread:5.2f} DN = {100*spread/max(arr.mean(),1e-9):5.2f} %"
              + f"   sd {100*sd/max(arr.mean(),1e-9):5.2f} %")
