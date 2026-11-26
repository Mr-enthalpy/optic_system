"""Azimuthal analysis of the B2 rotation session.

B2 captured three mount states (P1 -> P2 -> P3) at three wavelengths.  P1 and
P3 are meant to be the same orientation, so their difference is the *measured*
null: whatever angular shift the estimator reports there is bias, not physics.

Everything is derived from files the capture already wrote:

* ``brackets.jsonl`` - which mount state each bracket belongs to, and the
  accelerometer roll that was read while the mount sat there;
* ``captures/<label>/batch_000/decoded/temporal_mean_dn.npy`` - the shortest
  exposure of that bracket, which puts the core near 80 % of the span;
* ``<label>.json`` - the peak position and the black level of that bracket.

Nothing is rotated to align anything.  The centre is only translated, because
the mount was physically re-centred between states and the remaining offset is
a few pixels; the angle is the signal.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import rotation_analysis as ra  # noqa: E402

BANDS = (450, 550, 650)
STATES = ("P1", "P2", "P3")
PLANES = ("Gr", "Gb", "B", "R")
SHORTEST_BATCH = 0
# The B1 PSF is compact: 52 DN at r=2-3 px, 30 DN at 3-5, 4 DN at 5-8.  The
# polar grid therefore stops at 6 px, where a ring still has signal.
RADII_PX = np.arange(1.5, 6.01, 0.5)
AZIMUTH_STEP_DEG = 2.0
AZIMUTHS = np.deg2rad(np.arange(0.0, 360.0, AZIMUTH_STEP_DEG))
PATCH_HALF = 28            # must exceed the largest radius plus interpolation slack
MIN_PEAK_CORR = 0.2
NULL_TOLERANCE_DEG = 5.0


def read_jsonl(path):
    rows = []
    if not Path(path).exists():
        return rows
    for line in Path(path).read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def find_bracket_record(session, label):
    """The newest per-bracket JSON the driver wrote for ``label``."""
    best = None
    for path in Path(session).glob(f"{label}*.json"):
        if path.name.endswith("_verified.json"):
            continue
        try:
            record = json.loads(path.read_text(encoding="utf-8-sig"))
        except Exception:
            continue
        if record.get("stage") != "b2_bracket":
            continue
        if best is None or path.stat().st_mtime_ns > best[0].stat().st_mtime_ns:
            best = (path, record)
    return best


def bracket_plan(session):
    """Every bracket in log order, with the mount roll that was in force.

    A resumed session can place the same state twice at slightly different
    angles, so the roll is taken from the state row that precedes the bracket,
    not from a per-state dictionary.
    """
    rows = read_jsonl(Path(session) / "brackets.jsonl")
    state_roll = {}
    plan = []
    for row in rows:
        kind = row.get("kind")
        if kind == "state":
            settled = row.get("settled") or {}
            if settled.get("roll_deg") is not None:
                state_roll[row["state"]] = float(settled["roll_deg"])
        elif kind == "bracket":
            state = row.get("state")
            if state not in state_roll:
                continue
            plan.append({
                "label": row.get("label") or f"{state}_{row.get('band_nm')}nm",
                "state": state, "band_nm": int(row["band_nm"]),
                "roll_deg": state_roll[state],
            })
    return plan


def measure_bracket(session, label, plane="Gr", radii=RADII_PX, azimuths=AZIMUTHS):
    """Sub-pixel centre and one plane's azimuthal modulation for one bracket.

    The shortest tier is the one whose core is below the ceiling, and the
    bracket also repeats that tier between the longer exposures.  Those repeats
    are averaged: the B1 record shows their centres agree to ~0.03 px, so
    averaging is free signal-to-noise for the faint rings at r = 5-6 px.

    A clipped core is refused rather than measured.  On a saturated patch the
    global maximum is the first code at the ceiling, which can sit tens of
    pixels from the spot; the B1 probe found exactly that on the long tiers
    (a 20 px "centre" at 64x exposure).
    """
    found = find_bracket_record(session, label)
    if found is None:
        return {"ok": False, "reason": f"no bracket record for {label}"}
    path, record = found
    capture = Path(session) / "captures" / path.stem
    references = record.get("reference_batches") or [SHORTEST_BATCH]
    mean_paths = [capture / f"batch_{int(index):03d}" / "decoded"
                  / "temporal_mean_dn.npy" for index in references]
    missing = [item.name for item in mean_paths if not item.exists()]
    if missing:
        return {"ok": False, "reason": f"missing temporal mean for {label}",
                "capture": path.stem, "batches": list(references)}
    black = float(record["analysis"]["ceiling"]["black_dn"])
    ceiling = record["analysis"]["ceiling"].get("ceiling_dn")

    first = np.load(mean_paths[0], mmap_mode="r")
    iy, ix = np.unravel_index(int(np.argmax(first)), first.shape)   # row, column
    # Even-origin patch: the CFA parity of the full frame is preserved, so the
    # plane sublattice is the same one the driver analysed.
    y0 = max(0, int(iy) - PATCH_HALF) & ~1
    x0 = max(0, int(ix) - PATCH_HALF) & ~1
    y1 = min(first.shape[0], y0 + 2 * PATCH_HALF + 1)
    x1 = min(first.shape[1], x0 + 2 * PATCH_HALF + 1)
    if (y1 - y0) < 2 * PATCH_HALF or (x1 - x0) < 2 * PATCH_HALF:
        return {"ok": False, "reason": "peak too close to the frame edge",
                "capture": path.stem}

    patch = np.zeros((y1 - y0, x1 - x0), dtype=np.float64)
    for index, mean_path in enumerate(mean_paths):
        mean = first if index == 0 else np.load(mean_path, mmap_mode="r")
        patch += np.asarray(mean[y0:y1, x0:x1], dtype=np.float64)
    patch /= float(len(mean_paths))
    peak_dn = float(patch.max())
    if ceiling is not None and peak_dn >= float(ceiling) - 0.5:
        return {"ok": False,
                "reason": (f"clipped core for {label}: peak {peak_dn:.1f} DN sits at the "
                           f"{float(ceiling):.0f} DN ceiling, so the maximum is not a peak"),
                "capture": path.stem, "peak_dn": peak_dn}

    refined = ra.refine_center(patch, peak_xy=(int(ix) - x0, int(iy) - y0),
                               black=black)
    center_local = refined["center_xy"]
    if not refined["usable"]:
        return {"ok": False,
                "reason": (f"centre estimate did not settle for {label}: "
                           f"converged={refined['converged']}, "
                           f"drift={refined['drift_from_anchor_px']:.2f} px"),
                "capture": path.stem, "quadratic": refined["quadratic"]}
    center_global = (center_local[0] + x0, center_local[1] + y0)

    polar = ra.polar_modulation(patch, center_local, plane, radii, azimuths,
                               black=black)
    return {
        "ok": True, "capture": path.stem, "record_path": path.name,
        "black_dn": black, "peak_dn": peak_dn,
        "batches": [int(index) for index in references],
        "peak_xy_int": [int(ix), int(iy)],
        "center_xy": [float(center_global[0]), float(center_global[1])],
        "center_offset_px": [float(center_global[0] - ix),
                             float(center_global[1] - iy)],
        "refine": {key: refined[key] for key in
                   ("anchor_xy", "anchor_source", "drift_from_anchor_px",
                    "converged", "usable", "centroid_radius_px")},
        "plane": plane,
        "modulation": polar["modulation"],
        "ring_mean_dn": polar["ring_mean_dn"].tolist(),
        "valid_rings": polar["valid"].tolist(),
        "radii_px": polar["radii_px"].tolist(),
        "azimuths_rad": polar["azimuths_rad"].tolist(),
    }


def pair_rows(session, planes=PLANES, radii=RADII_PX, azimuths=AZIMUTHS,
              min_corr=MIN_PEAK_CORR):
    """Angular shift for every (band, state pair, plane) combination."""
    plan = bracket_plan(session)
    by_key = {(item["band_nm"], item["state"]): item for item in plan}
    profiles = {}
    for item in plan:
        for plane in planes:
            measured = measure_bracket(session, item["label"], plane=plane,
                                       radii=radii, azimuths=azimuths)
            profiles[(item["band_nm"], item["state"], plane)] = measured

    rows = []
    for band in sorted({item["band_nm"] for item in plan}):
        for state_a, state_b in ((STATES[0], STATES[1]),
                                 (STATES[1], STATES[2]),
                                 (STATES[0], STATES[2])):
            key_a, key_b = by_key.get((band, state_a)), by_key.get((band, state_b))
            if key_a is None or key_b is None:
                continue
            delta = float(ra.wrap_deg(key_b["roll_deg"] - key_a["roll_deg"]))
            for plane in planes:
                a = profiles[(band, state_a, plane)]
                b = profiles[(band, state_b, plane)]
                if not (a.get("ok") and b.get("ok")):
                    rows.append({"band_nm": band, "state_a": state_a,
                                 "state_b": state_b, "plane": plane,
                                 "delta_roll_deg": delta, "ok": False,
                                 "reason": a.get("reason") or b.get("reason")})
                    continue
                found = ra.angular_shift(a["modulation"], b["modulation"], azimuths,
                                         valid_a=a["valid_rings"],
                                         valid_b=b["valid_rings"],
                                         max_deg=45.0, min_corr=min_corr)
                rows.append({
                    "band_nm": band, "state_a": state_a, "state_b": state_b,
                    "plane": plane, "delta_roll_deg": delta,
                    "ok": bool(found["ok"]), "angular_shift_deg": found["shift_deg"],
                    "peak_corr": found["peak_corr"], "reason": found["reason"],
                    "centre_a": a["center_xy"], "centre_b": b["center_xy"],
                    "valid_rings": int(np.sum(a["valid_rings"])),
                })
    return rows, profiles


def summarise(rows, planes=PLANES):
    """Fit the slope per plane and pooled, and check the same-angle null."""
    fits = {}
    for plane in planes:
        subset = [row for row in rows if row["plane"] == plane and row["ok"]]
        fits[plane] = ra.fit_rotation(subset) if subset else {
            "ok": False, "reason": "no usable pair", "slope": None}
        fits[plane]["n"] = len(subset)
    pooled = [row for row in rows if row["ok"]]
    fits["pooled"] = ra.fit_rotation(pooled) if pooled else {
        "ok": False, "reason": "no usable pair", "slope": None}
    fits["pooled"]["n"] = len(pooled)
    for name, fit in fits.items():
        if fit.get("ok"):
            fit["class"] = ra.classify_slope(fit["slope"])

    nulls = [row for row in rows
             if {row["state_a"], row["state_b"]} == {STATES[0], STATES[2]} and row["ok"]]
    null_shift = None
    if nulls:
        null_shift = float(np.median([row["angular_shift_deg"] for row in nulls]))
    by_band = {}
    for band in sorted({row["band_nm"] for row in rows}):
        subset = [row for row in rows if row["band_nm"] == band and row["ok"]]
        fit = ra.fit_rotation(subset) if subset else {"ok": False, "slope": None}
        if fit.get("ok"):
            fit["class"] = ra.classify_slope(fit["slope"])
        by_band[str(band)] = fit
    return {"by_plane": fits, "by_band": by_band,
            "null_shift_deg": null_shift,
            "null_ok": (None if null_shift is None
                        else bool(abs(null_shift) <= NULL_TOLERANCE_DEG)),
            "null_tolerance_deg": NULL_TOLERANCE_DEG}


def verdict(summary):
    """Name what the fitted slope supports, without forcing a verdict."""
    pooled = summary["by_plane"]["pooled"]
    if not pooled.get("ok"):
        return {"class": "unresolved",
                "reason": pooled.get("reason") or "no usable pairs"}
    plane_names = [name for name in summary["by_plane"] if name != "pooled"]
    classes = {name: summary["by_plane"][name]["class"] for name in plane_names
               if summary["by_plane"][name].get("ok")}
    out = {"class": pooled["class"], "slope": pooled["slope"],
           "per_plane": classes, "n": pooled["n"]}
    if summary["null_ok"] is False:
        out["class"] = "unresolved"
        out["reason"] = (f"the same-angle pair still shows {summary['null_shift_deg']:+.1f} deg; "
                         f"the estimator bias exceeds {NULL_TOLERANCE_DEG:.0f} deg, so a slope "
                         "cannot be attributed to the mount")
    if len(set(classes.values())) > 1:
        out.setdefault("reason", "planes disagree on the hypothesis")
        out["class"] = "unresolved"
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--session", type=Path, required=True)
    ap.add_argument("--output", type=Path, default=None,
                    help="JSON result path (default: <session>/angular_analysis.json)")
    ap.add_argument("--planes", default="Gr,Gb,B,R")
    ap.add_argument("--min-corr", type=float, default=MIN_PEAK_CORR)
    args = ap.parse_args(argv)

    planes = tuple(item.strip() for item in args.planes.split(",") if item.strip())
    for plane in planes:
        if plane not in PLANES:
            raise SystemExit(f"unknown plane {plane!r}; expected one of {PLANES}")
    rows, profiles = pair_rows(args.session, planes=planes, min_corr=args.min_corr)
    summary = summarise(rows, planes=planes)
    centres = {key: profile.get("center_xy")
               for key, profile in profiles.items() if profile.get("ok")}
    result = {
        "session": str(args.session),
        "radii_px": RADII_PX.tolist(),
        "azimuth_step_deg": AZIMUTH_STEP_DEG,
        "pairs": rows,
        "summary": summary,
        "verdict": verdict(summary),
        "centres_xy": {f"{band}_{state}_{plane}": value
                       for (band, state, plane), value in centres.items()},
        "note": ("delta_roll is measured by the phone accelerometer; the shift is what "
                 "has to be applied to the second profile to match the first. "
                 "Display-fixed structure predicts slope 0, pinhole/mount-fixed -1."),
    }
    output = args.output or Path(args.session) / "angular_analysis.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"output": str(output),
                      "pairs": len(rows),
                      "usable": sum(1 for row in rows if row["ok"]),
                      "summary": summary["by_plane"]["pooled"],
                      "verdict": result["verdict"]}, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
