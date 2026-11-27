"""B2 rotation from the phases of the azimuthal harmonics.

``analyse_b2.py`` cross-correlates the whole azimuthal profile and takes the
largest correlation inside a 45 degree window.  That window was justified by a
four-fold pattern, and the measured spot is not four-fold: it carries periodic
spike directions in many azimuths, so its circular autocorrelation has a peak
every ``360/N`` degrees and the largest one is not necessarily the rotation.
This script estimates the rotation from the *phases* of orders 2 to 10 instead,
jointly, which uses every order at once and cannot be pulled onto a neighbouring
spike by the strongest one.

Both scripts read the same session and the same measured profiles; neither
replaces the other's numbers.  This one needs the session tree only.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

import analyse_b2 as an
import rotation_analysis as ra

RADII_PX = np.arange(3.0, 10.01, 0.25)
AZIMUTH_STEP_DEG = 0.5
AZIMUTHS = np.deg2rad(np.arange(0.0, 360.0, AZIMUTH_STEP_DEG))
ORDERS = tuple(range(2, 11))


def combined_coefficients(session, label):
    """Order coefficients for one bracket, and the same per CFA plane.

    The planes are combined by adding their complex coefficients: where they
    agree in phase the sum keeps the full amplitude, and where they disagree the
    result is attenuated, which is a consistency check on its own.
    """
    per_plane = {}
    for plane in an.PLANES:
        measured = an.measure_bracket(session, label, plane=plane, radii=RADII_PX,
                                      azimuths=AZIMUTHS)
        if not measured.get("ok"):
            per_plane[plane] = None
            continue
        per_plane[plane] = ra.harmonic_coefficients(measured["modulation"], AZIMUTHS,
                                                    orders=ORDERS)
    total = {}
    for order in ORDERS:
        values = [found[order] for found in per_plane.values()
                  if found and order in found]
        if len(values) >= 2:
            total[order] = complex(np.sum(values))
    return total, per_plane


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--session", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--search-deg", type=float, default=90.0,
                        help="half-width of the rotation search; larger than the "
                             "45 deg four-fold limit because the neighbouring-spike "
                             "ambiguity is handled by the orders, not the window")
    parser.add_argument("--min-orders", type=int, default=3)
    args = parser.parse_args(argv)

    plan = an.bracket_plan(args.session)
    coefficients = {}
    per_plane = {}
    print(f"{'band':>4} {'state':>5}  orders  mean amplitude (relative to k=2)")
    for item in plan:
        total, planes = combined_coefficients(args.session, item["label"])
        if not total:
            print(f"{item['band_nm']:>4} {item['state']:>5}  no usable profile")
            continue
        coefficients[(item["band_nm"], item["state"])] = total
        per_plane[(item["band_nm"], item["state"])] = planes
        reference = abs(total.get(2, 1.0)) or 1.0
        shown = " ".join(f"k{k}:{abs(total[k]) / reference:4.2f}"
                         for k in sorted(total))
        print(f"{item['band_nm']:>4} {item['state']:>5}  {len(total):6d}  {shown}")

    rolls = {(item["band_nm"], item["state"]): item["roll_deg"] for item in plan}
    rows = []
    print(f"\n{'band':>4} {'pair':>7} {'droll':>8} {'shift':>8} {'phase rms':>10}  "
          "per-plane shift")
    for band in sorted({item["band_nm"] for item in plan}):
        for state_a, state_b in ((an.STATES[0], an.STATES[1]),
                                 (an.STATES[1], an.STATES[2]),
                                 (an.STATES[0], an.STATES[2])):
            first = coefficients.get((band, state_a))
            second = coefficients.get((band, state_b))
            if not (first and second):
                continue
            found = ra.fit_shift_from_harmonics(first, second,
                                                search_deg=args.search_deg)
            if not found["ok"] or len(found["orders"]) < args.min_orders:
                continue
            delta_roll = ra.wrap_deg(rolls[(band, state_b)] - rolls[(band, state_a)])
            singles = []
            for plane in an.PLANES:
                one = per_plane[(band, state_a)].get(plane)
                two = per_plane[(band, state_b)].get(plane)
                if not (one and two):
                    continue
                single = ra.fit_shift_from_harmonics(one, two,
                                                    search_deg=args.search_deg)
                if single["ok"]:
                    singles.append(single["shift_deg"])
            rows.append({"band_nm": band, "state_a": state_a, "state_b": state_b,
                         "label_a": next(item["label"] for item in plan
                                         if item["band_nm"] == band
                                         and item["state"] == state_a),
                         "label_b": next(item["label"] for item in plan
                                         if item["band_nm"] == band
                                         and item["state"] == state_b),
                         "delta_roll_deg": delta_roll,
                         "angular_shift_deg": found["shift_deg"],
                         "phase_rms_deg": found["phase_rms_deg"],
                         "orders": found["orders"],
                         "per_plane_shift_deg": singles})
            print(f"{band:>4} {state_a}->{state_b:>4} {delta_roll:8.2f} "
                  f"{found['shift_deg']:8.2f} {found['phase_rms_deg']:10.2f}  "
                  + " ".join(f"{value:+6.1f}" for value in singles))

    by_band = {}
    for band in sorted({row["band_nm"] for row in rows}):
        subset = [row for row in rows if row["band_nm"] == band]
        fitted = ra.fit_rotation(subset)
        fitted["class"] = ra.classify_slope(fitted.get("slope"))
        by_band[str(band)] = fitted
    pooled = ra.fit_rotation(rows)
    pooled["class"] = ra.classify_slope(pooled.get("slope"))

    null = [row for row in rows
            if {row["state_a"], row["state_b"]} == {an.STATES[0], an.STATES[2]}]
    null_shift = float(np.median([row["angular_shift_deg"] for row in null])) if null else None
    null_ok = bool(null and abs(null_shift) <= an.NULL_TOLERANCE_DEG)

    result = {
        "session": str(args.session),
        "method": ("rotation from the joint phase of azimuthal orders "
                   f"{ORDERS[0]}..{ORDERS[-1]} over r = {RADII_PX[0]:.1f}-"
                   f"{RADII_PX[-1]:.1f} px, coefficients combined across the four "
                   "CFA planes; shift is what has to be applied to the second "
                   "profile to match the first, so mount-fixed predicts "
                   "-delta_roll and display-fixed predicts 0"),
        "radii_px": RADII_PX.tolist(),
        "azimuth_step_deg": AZIMUTH_STEP_DEG,
        "orders": list(ORDERS),
        "pairs": rows,
        "by_band": by_band,
        "pooled": pooled,
        "null_shift_deg": null_shift,
        "null_ok": null_ok,
        "null_tolerance_deg": an.NULL_TOLERANCE_DEG,
    }
    output = args.output or Path(args.session) / "harmonic_rotation.json"
    output.write_text(json.dumps(result, indent=2, ensure_ascii=False),
                      encoding="utf-8")

    print()
    for band, block in by_band.items():
        print(f"  {band} nm  slope={block['slope']:7.3f}  rms={block['rms_deg']:5.2f}  "
              f"n={block['n']:2d}  {block['class']}")
    print(f"  pooled   slope={pooled['slope']:7.3f}  rms={pooled['rms_deg']:5.2f}  "
          f"n={pooled['n']:2d}  {pooled['class']}")
    print(f"  same-angle null {null_shift:+.2f} deg  ok={null_ok}")
    print(json.dumps({"output": str(output)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
