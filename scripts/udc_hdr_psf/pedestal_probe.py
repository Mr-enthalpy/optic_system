"""The pedestal is declared as 64 DN.  Is that what the sensor actually reads?

The bracket's faint wings sit at 5-50 DN, where a 0.3 DN error in the pedestal
is a 1-6 % error in the seam ratio.  Most of an 8 Mpx frame is dark, so the real
pedestal is measurable per batch, per colour plane, without any extra capture.

Prints, per batch: the global dark median, the same in wide dark annuli, and the
seam flux ratios recomputed with the measured pedestal instead of 64.0.
"""
import json
import os
import sys
from pathlib import Path

import numpy as np

# The capture tree is data: it is named by the environment, never baked in.
ROOT = Path(os.environ.get("UDC_CAPTURES", "captures"))
CEILING = 1023.0
RINGS = ((2, 4), (4, 6), (6, 8), (8, 11), (11, 14), (14, 18))
DARK_RINGS = ((100, 150), (150, 200), (200, 300), (300, 450), (450, 700))
LABELS = sys.argv[1:] or ["gate_450nm", "gate_650nm"]
PLANES = {"B": (0, 0), "Gb": (0, 1), "Gr": (1, 0), "R": (1, 1)}


def newest(label):
    found = sorted(p for p in ROOT.glob(label + "*") if p.is_dir())
    return found[-1] if found else None


def plane(img, parity):
    return img[parity[0]::2, parity[1]::2]


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
    ref = frames[order]
    py, px = np.unravel_index(int(np.argmax(ref)), ref.shape)
    yy, xx = np.mgrid[0:ref.shape[0], 0:ref.shape[1]]
    radius = np.hypot(yy - py, xx - px)
    print("\n=====", directory.name, " peak", (int(px), int(py)))

    pedestal = {}
    for i, (frame, exposure) in enumerate(zip(frames, exposures)):
        line = "  batch {:02d} {:9.0f} us  ".format(i, exposure)
        for name, par in PLANES.items():
            sub = plane(frame, par)
            rsub = plane(radius, par)
            med = float(np.median(sub[rsub > 100]))
            pedestal[(i, name)] = med
            line += "{} {:7.3f}  ".format(name, med)
        far = [float(np.median(plane(frame, p)[plane(radius, p) > 100])) for p in PLANES.values()]
        line += "  mean {:7.3f}".format(float(np.mean(far)))
        print(line)

    print("  dark annuli, mean over planes, batch 00 vs last:")
    for lo, hi in DARK_RINGS:
        vals = []
        for i in (0, len(frames) - 1):
            v = np.mean([float(np.median(plane(frames[i], p)[
                (plane(radius, p) >= lo) & (plane(radius, p) < hi)]))
                for p in PLANES.values()])
            vals.append(v)
        print("     r{}-{}  {:7.3f}  {:7.3f}   delta {:+.3f}".format(lo, hi, vals[0], vals[1], vals[1] - vals[0]))

    print("  flux seam ratios with the measured pedestal (was 64.000):")
    ranked = list(np.argsort(np.asarray(exposures)))
    for short_i, long_i in zip(ranked[:-1], ranked[1:]):
        factor = exposures[long_i] / exposures[short_i]
        if abs(factor - 1.0) < 1e-9:
            tag = "  [x1.00 control]"
        else:
            tag = ""
        print("  {:.0f} -> {:.0f} us  x{:.2f}{}".format(
            exposures[short_i], exposures[long_i], factor, tag))
        for r0, r1 in RINGS:
            ratios = []
            for name, par in PLANES.items():
                s = plane(frames[short_i], par) - pedestal[(short_i, name)]
                l = plane(frames[long_i], par) - pedestal[(long_i, name)]
                fs = plane(frames[short_i], par)
                fl = plane(frames[long_i], par)
                mask = (plane(radius, par) >= r0) & (plane(radius, par) < r1) \
                       & (fs < CEILING) & (fl < CEILING)
                if mask.sum() < 20:
                    continue
                ratios.append((l[mask].sum() / factor) / s[mask].sum())
            if not ratios:
                print("     r{}-{}\t  ---".format(r0, r1))
                continue
            arr = np.asarray(ratios)
            print("     r{}-{}\tflux(measured black) mean {:5.3f}  spread B/Gb/Gr/R "
                  "{:5.3f}..{:5.3f}".format(r0, r1, float(arr.mean()),
                                            float(arr.min()), float(arr.max())))
