#!/usr/bin/env python3
"""Analyze far-field diffraction wing contribution to PSF energy leakage."""
from __future__ import annotations

import h5py
import numpy as np

RELEASE = r"D:\datasets\optic_system\phase3_release_20260520\common\provenance\raw_h5"

with h5py.File(RELEASE + r"\bishe_psf_roi.h5", "r") as f:
    frames = np.asarray(f["raw/frames_avg"], dtype=np.float64)
    psf = np.mean(frames, axis=0)

valid = np.ones(psf.shape, dtype=bool)
valid[:1, :] = False
bg = float(np.percentile(psf[valid], 5.0))
corr = np.maximum(psf - bg, 0.0)
total = float(np.sum(corr[valid]))

cy, cx = 934.5, 1149.1
yg, xg = np.ogrid[:2048, :2448]
r = np.sqrt((xg - cx) ** 2 + (yg - cy) ** 2)

print(f"5% bg       = {bg:.4f}")
print(f"peak        = {psf[valid].max():.1f}")
print(f"p99  raw    = {float(np.percentile(psf[valid], 99.0)):.2f}")
print(f"p99.9 raw   = {float(np.percentile(psf[valid], 99.9)):.2f}")
print(f"total signal = {total:.1f}")
print()

entries = [
    ("core r<50",       (r < 50) & valid),
    ("ring 50-200",     (r >= 50) & (r < 200) & valid),
    ("far r>200 all",   (r >= 200) & valid),
    ("far corr>=0.1",   (r >= 200) & valid & (corr >= 0.1)),
    ("far corr>=0.5",   (r >= 200) & valid & (corr >= 0.5)),
    ("far corr>=1.0",   (r >= 200) & valid & (corr >= 1.0)),
    ("far corr>=2.0",   (r >= 200) & valid & (corr >= 2.0)),
    ("far corr>=5.0",   (r >= 200) & valid & (corr >= 5.0)),
    ("far corr>=10.0",  (r >= 200) & valid & (corr >= 10.0)),
]

row_fmt = "{:<20s} {:>10d} {:>12.1f} {:>9.2f}%"
print("{:<20s} {:>10s} {:>12s} {:>9s}".format("Domain", "Pixels", "Energy", "% of total"))
print("-" * 55)
for label, mask in entries:
    e = float(np.sum(corr[mask]))
    n = mask.sum()
    print(row_fmt.format(label, n, e, e / total * 100))

print()

# how many far-field pixels have genuine diffraction above noise floor?
dark_std = 0.2
for nsig in [1, 2, 3, 5, 10]:
    thresh = dark_std * nsig
    far_mask = (r >= 200) & valid & (corr >= thresh)
    n_far_peak = far_mask.sum()
    e_far_peak = float(np.sum(corr[far_mask]))
    n_all = ((r >= 200) & valid).sum()
    print(
        f"far r>200, >={nsig:2d}*sigma (>{thresh:.1f}): "
        f"pixels={n_far_peak:>6d} ({n_far_peak/n_all*100:5.2f}%), "
        f"energy={e_far_peak:>10.1f} ({e_far_peak/total*100:6.2f}%)"
    )

print()

# now the key question: if we restrict the denominator to a physically motivated
# support region (e.g. excluding noise-floor pixels), how do the ROI fractions look?
for support_r in [100, 150, 200, 300, 500]:
    supp_mask = (r < support_r) & valid
    supp_total = float(np.sum(corr[supp_mask]))
    rois = {
        "roi_256": (1021, 1277, 807, 1063),
        "roi_512": (893, 1405, 679, 1191),
        "roi_768": (765, 1533, 551, 1319),
        "roi_1024": (637, 1661, 423, 1447),
    }
    print(f"\n--- support domain r<{support_r} (total energy = {supp_total:.1f}) ---")
    for rk, (x0, x1, y0, y1) in rois.items():
        e = float(np.sum(corr[y0:y1, x0:x1]))
        print(f"  {rk:<10} enclosed={e/supp_total*100:6.2f}%  leak={100-e/supp_total*100:6.2f}%")
