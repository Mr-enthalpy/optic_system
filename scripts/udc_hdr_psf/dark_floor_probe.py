"""Measure the dark floor in the same window and planes as each accepted band.

The band ratios are computed from integrated per-plane flux inside r <= 20 px.
When a band's light lands on a plane that barely responds to it (450 nm light on
red pixels, 650 nm light on blue pixels) that flux is only a few percent of the
dominant plane, and the question "is that light, or an offset?" decides whether
the ratio means anything.  The dark set is the answer: it was captured with the
source occluded, in the same geometry, before the gate.

    python dark_floor_probe.py <session root> [dark dir]

Prints, per band, the band's own per-plane flux next to the dark set's flux in
the same window at the closest dark exposure.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import psf_analysis as psf  # noqa: E402


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def plane_fluxes(mean_path: Path, rect, peak_local, black: float, limit: float = 20.0):
    mean = np.load(mean_path, mmap_mode="r")
    signal = np.asarray(psf.crop(mean, rect), dtype=np.float64) - black
    radius, yy, xx = psf.radius_grid(signal.shape, peak_local)
    radial = psf.radial_table(signal, radius, psf.plane_masks(yy, xx))
    flux, _sigma = psf.plane_flux(radial)
    keys = sorted(flux)
    inside = [k for k in keys if k <= limit]
    total = {plane: sum(flux[k][plane] for k in inside) for plane in radial}
    pixels = {plane: sum(b["pixels"] for b in radial[plane]
                         if b["radius_px"][1] <= limit
                         and b["radius_px"][1] > psf.RATIO_MIN_RADIUS_PX)
              for plane in radial}
    return total, pixels


def main() -> int:
    session = Path(sys.argv[1]).resolve()
    dark_dir = session / "captures" / (sys.argv[2] if len(sys.argv) > 2 else "dark_start")
    protocol = load(session / "progress.json")
    plan = load(dark_dir / "plan.json")
    exposure = plan["batches"][0]["exposure_us"]
    mean_path = dark_dir / "batch_000" / "decoded" / "temporal_mean_dn.npy"
    print(f"dark: {dark_dir.name}, {exposure} us, window measured per band")
    print()
    header = "| nm | band tier0 | plane | band flux | dark flux | dark/band | band mean/px |"
    print(header)
    print("|" + "---|" * 7)
    for band in sorted(protocol["accepted_bands"], key=lambda b: b["wavelength_nm"]):
        rect = band["window_xywh"]
        peak = band["peak_xy"]
        peak_local = (peak[0] - rect[0], peak[1] - rect[1])
        dark_flux, dark_px = plane_fluxes(mean_path, rect, peak_local,
                                          float(band["black_dn"]))
        stored = {}
        for plane, rings in band["radial"].items():
            used = [r for r in rings
                    if r["radius_px"][1] <= 20 and r["radius_px"][1] > psf.RATIO_MIN_RADIUS_PX
                    and r["pixels"] > 0 and r["mean"] == r["mean"]]
            stored[plane] = sum(r["mean"] * r["pixels"] for r in used)
        for plane in ("B", "Gr", "Gb", "R"):
            band_flux = stored[plane]
            floor = dark_flux[plane]
            share = floor / band_flux if band_flux else float("nan")
            print(f"| {band['wavelength_nm']} | {band['tiers_us'][0]} | {plane} | "
                  f"{band_flux:.1f} | {floor:.1f} | {100 * share:.1f} % | "
                  f"{band_flux / dark_px[plane]:.3f} |")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
