"""Hardware-free test of the PSF analysis.

Synthetic brackets with a known PSF, a hard 959 DN ceiling (which is what the
2026-11-26 data shows), 10-bit quantisation, and known per-plane colour.  The
analysis has to recover the colour, detect the real ceiling instead of the
declared 1023, measure a chromatic shape difference, and fail a bracket that is
inconsistent or too dim to judge.

Run:  python test_psf_analysis.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "scripts" / "udc_hdr_psf"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(HERE.parent))  # the repository, for tasks.measurement.hdr
import psf_analysis as pa

BLACK = 64.0
DECLARED_WHITE = 1023
CEILING = 959
SHAPE = (176, 176)
PEAK_WINDOW = (88, 88)
TIERS = [376, 1504, 6016, 24064, 96256, 385024]
CORE_DN_AT_FIRST = 790.0     # what 550 nm did at 660 us; near enough

PLANE_DEFAULT = {"R": 0.22, "Gr": 1.00, "Gb": 0.94, "B": 0.35}
WIDTH_DEFAULT = {"R": 1.10, "Gr": 1.20, "Gb": 1.20, "B": 1.35}


def moffat(radius, width, power=1.5):
    return (1.0 + (radius / width) ** 2) ** (-power)


def bracket(plane_gain=None, plane_width=None, core_dn=CORE_DN_AT_FIRST,
            tier_scale=None, tiers=TIERS):
    """Frames for one bracket, quantised, with a hard ceiling."""
    gain = plane_gain or PLANE_DEFAULT
    width = plane_width or WIDTH_DEFAULT
    radius, yy, xx = pa.radius_grid(SHAPE, PEAK_WINDOW)
    masks = pa.plane_masks(yy, xx)
    frames = []
    for index, exposure in enumerate(tiers):
        signal = np.zeros(SHAPE, dtype=np.float64)
        for name, mask in masks.items():
            signal[mask] = gain[name] * moffat(radius[mask], width[name])
        level = core_dn * exposure / tiers[0]
        if tier_scale and index in tier_scale:
            level *= tier_scale[index]
        frames.append(np.clip(np.round(BLACK + signal * level), 0, CEILING))
    return frames


def flux_truth(plane_gain=None):
    """Integrated per-plane signal over exactly the bands the analysis sums."""
    gain = plane_gain or PLANE_DEFAULT
    radius, yy, xx = pa.radius_grid(SHAPE, PEAK_WINDOW)
    masks = pa.plane_masks(yy, xx)
    out = {}
    for limit in (20, 40):
        band = (radius >= pa.RATIO_MIN_RADIUS_PX) & (radius <= limit)
        out[limit] = {name: gain[name] * float(
            moffat(radius[masks[name] & band], WIDTH_DEFAULT[name]).sum())
            for name in gain}
    return out


def main():
    failures = []

    def check(condition, message):
        print(("  ok   " if condition else "  FAIL ") + message)
        if not condition:
            failures.append(message)

    print("1. ceiling detection")
    frames = bracket()
    info = pa.detect_ceiling(frames, BLACK)
    print(f"   ceiling={info['ceiling_dn']} support={info['ceiling_support_px']} px "
          f"per_frame={info['per_frame_ceiling_dn']}")
    check(info["ceiling_dn"] == CEILING, f"ceiling is the real plateau {CEILING}, "
                                        f"not the declared {DECLARED_WHITE}")
    check(info["ceiling_support_px"] > 50,
          f"the plateau is backed by real pixels ({info['ceiling_support_px']} px)")
    check(info["codes_above_ceiling_px"] <= 4,
          f"nothing meaningful sits above the plateau ({info['codes_above_ceiling_px']} px)")

    print("\n2. colour recovery against a known PSF")
    result = pa.analyse(frames, TIERS, PEAK_WINDOW, BLACK)
    truth = flux_truth()
    for limit in (20, 40):
        got = result["ratios"][str(limit)]
        green_truth = 0.5 * (truth[limit]["Gr"] + truth[limit]["Gb"])
        want_rg = truth[limit]["R"] / green_truth
        want_br = truth[limit]["B"] / truth[limit]["R"]
        print(f"   r<={limit} px  r_g={got['r_g']:.4f} (want {want_rg:.4f})  "
              f"b_r={got['b_r']:.4f} (want {want_br:.4f})")
        check(abs(got["r_g"] / want_rg - 1) < 0.02,
              f"r_g inside r={limit} within 2 % ({abs(got['r_g']/want_rg-1)*100:.2f} %)")
        check(abs(got["b_r"] / want_br - 1) < 0.02,
              f"b_r inside r={limit} within 2 % ({abs(got['b_r']/want_br-1)*100:.2f} %)")

    print("\n3. a chromatic shape difference is visible in the normalised profile")
    shape = result["shape"]
    for name in ("R", "B"):
        outer = [b for b in shape[name] if b["radius_px"] == [8, 12]][0]["fraction"]
        print(f"   {name}: fraction of its own r<=20 flux at r=8-12 px = {outer:.5f}")
    r_outer = [b for b in shape["R"] if b["radius_px"] == [8, 12]][0]["fraction"]
    b_outer = [b for b in shape["B"] if b["radius_px"] == [8, 12]][0]["fraction"]
    check(b_outer > 1.2 * r_outer,
          f"the wider blue PSF puts more of its flux outside (ratio {b_outer/r_outer:.2f}x)")

    print("\n4. bracket consistency")
    seams = result["seams"]
    for pair in seams["pairs"]:
        judged = [b for b in pair["bands"] if b["judged"]]
        line = ", ".join(f"{b['radius_px'][0]}-{b['radius_px'][1]}px "
                         f"({b['short_level_dn']:.0f}DN r={b['ratio']:.4f} "
                         f"tol={b['tolerance']*100:.1f}%)"
                         for b in judged)
        print(f"   {pair['short_us']:.0f}->{pair['long_us']:.0f} us: {line or 'nothing judged'}")
    print(f"   judged={seams['judged_bands']} bad={seams['bad_bands']} "
          f"strict_bad={seams['strict_bad_bands']} passed={seams['passed']} "
          f"strength={seams['strength']}")
    check(seams["passed"], "a consistent bracket passes")
    check(seams["strength"] == "weak",
          "the seam scan is declared a weak criterion, not a veto")
    check(seams["judged_bands"] >= 2, "at least two radius bands are judged")
    judged_rows = [b for pair in seams["pairs"] for b in pair["bands"] if b["judged"]]
    median_ratio = float(np.median([b["ratio"] for b in judged_rows]))
    worst = max(abs(b["ratio"] - 1) for b in judged_rows)
    check(abs(median_ratio - 1) < 0.005,
          f"the median judged ratio is within 0.5 % of unity ({abs(median_ratio-1)*100:.2f} %)")
    check(all(abs(b["ratio"] - 1) <= b["tolerance"] for b in judged_rows),
          f"every judged band is inside its quantisation tolerance (worst {worst*100:.2f} %)")
    unjudged = [b for pair in seams["pairs"] for b in pair["bands"] if not b["judged"]]
    check(len(unjudged) > 0, "bands at the quantisation floor are reported as unjudged, not dropped")

    print("\n5. an injected 5 % tier error is caught")
    broken = bracket(tier_scale={2: 1.05})
    broken_result = pa.analyse(broken, TIERS, PEAK_WINDOW, BLACK)
    print(f"   judged={broken_result['seams']['judged_bands']} "
          f"bad={broken_result['seams']['bad_bands']} passed={broken_result['seams']['passed']}")
    check(broken_result["seams"]["bad_bands"] >= 1, "the broken band is flagged")
    check(not broken_result["seams"]["passed"], "the bracket is rejected")

    print("\n6. a bracket that saturates its core is caught")
    hot = bracket(core_dn=40000.0)
    hot_result = pa.analyse(hot, TIERS, PEAK_WINDOW, BLACK)
    short_frame = hot[0]
    core = np.zeros(SHAPE, dtype=bool)
    radius, _, _ = pa.radius_grid(SHAPE, PEAK_WINDOW)
    core |= radius < 3
    clipped = float(np.mean(short_frame[core] >= CEILING))
    print(f"   shortest tier: {clipped*100:.0f} % of r<3 px at the ceiling, "
          f"all_saturated={hot_result['all_saturated_fraction']:.3f}")
    check(clipped > 0.3, "the synthetic core really is saturated")
    check(hot_result["ceiling"]["ceiling_dn"] == CEILING, "the ceiling is still measured correctly")
    hot_verdict = pa.verdict(hot_result)
    print(f"   verdict ok={hot_verdict['ok']}: {hot_verdict['reasons']}")
    check(not hot_verdict["ok"], "a bracket that clips its core is rejected")
    check(any("clipped" in r for r in hot_verdict["reasons"]),
          "the rejection names the clipping")

    print("\n7. a dim bracket is rejected because the peak has no depth")
    dim = bracket(core_dn=25.0)
    dim_result = pa.analyse(dim, TIERS, PEAK_WINDOW, BLACK)
    dim_verdict = pa.verdict(dim_result)
    print(f"   peak={dim_verdict['peak_dn_shortest_tier']:.0f} DN "
          f"({dim_verdict['peak_dn_shortest_tier']/dim_verdict['span_dn']*100:.1f} % of span) "
          f"at {dim_result['core']['shortest_us']:.0f} us, ok={dim_verdict['ok']}")
    for reason in dim_verdict["reasons"]:
        print(f"     - {reason}")
    check(not dim_verdict["ok"], "a dim bracket is rejected")
    check(any("below" in r for r in dim_verdict["reasons"]),
          "the rejection names the peak depth")
    check(dim_verdict["peak_too_dim"] is True, "the verdict reports the peak rule that fired")

    print("\n8. a bright bracket that never clips is not mistaken for a clipping one")
    # A two-tier bracket whose longest tier stays below the ceiling: nothing
    # clips, so the top code is just a bright smooth tail, not a plateau.
    bright = bracket(core_dn=150.0, tiers=TIERS[:2])
    bright_info = pa.detect_ceiling(bright, BLACK)
    print(f"   top code={bright_info['ceiling_dn']} support={bright_info['ceiling_support_px']} "
          f"plateau={bright_info['plateau']}")
    check(not bright_info["plateau"],
          "a smooth bright tail is not called a plateau")
    bright_result = pa.analyse(bright, TIERS[:2], PEAK_WINDOW, BLACK)
    check(bright_result["ceiling"]["plateau_observed"] is False,
          "the merge then uses the declared white level instead of a false ceiling")
    check(bright_result["ceiling"]["used_for_merge_dn"] == DECLARED_WHITE,
          "and it records the declared white level as the limit it used")

    print("\n9. window arithmetic keeps the CFA parity")
    for peak in ((1632, 1224), (0, 0), (3263, 2447), (5, 9)):
        rect = pa.window(peak, 176)
        x, y, w, h = rect
        check(x % 2 == 0 and y % 2 == 0 and 0 <= x <= 3264 - 176 and 0 <= y <= 2448 - 176,
              f"window for peak {peak} is even-aligned and inside the frame: {rect}")

    print("\n10. lamp drift is measured from the reference exposures and divided out")
    # The 2026-11-27 gate failed one seam by 5.4 % at 450 nm while the xenon lamp
    # had been running for minutes: a source that is 3 % brighter at the next tier
    # reads as a sensor that is 3 % non-linear.  Repeating the shortest tier makes
    # that drift a measurement; the bracket must fail without it and pass with it.
    drift_tiers = TIERS[:4]
    drift_times = [0.0, 30.0, 60.0, 90.0]
    drift_rate_s = 0.06 / 60.0                      # 6 % per minute
    clean = bracket(tiers=drift_tiers)
    drifted = [np.clip(np.round(BLACK + (frame - BLACK) * (1.0 + drift_rate_s * t)), 0, CEILING)
               for frame, t in zip(clean, drift_times)]
    dirty = pa.analyse(drifted, drift_tiers, PEAK_WINDOW, BLACK)
    print(f"   uncorrected: judged={dirty['seams']['judged_bands']} "
          f"bad={dirty['seams']['bad_bands']} passed={dirty['seams']['passed']}")
    check(dirty["seams"]["bad_bands"] >= 1, "a 3 % per-tier drift is caught by the seam scan")
    check(not dirty["seams"]["passed"], "the uncorrected bracket is rejected")
    references = [np.clip(np.round(BLACK + (clean[0] - BLACK) * (1.0 + drift_rate_s * t)),
                          0, CEILING) for t in drift_times]
    measured = pa.reference_fluxes(references, PEAK_WINDOW, BLACK)
    print(f"   reference pixels kept: {measured['pixels']} above {measured['floor_dn']:.0f} DN")
    check(measured["pixels"] >= 16, "enough reference pixels stay above the quantisation floor")
    curve = pa.drift_factors(drift_times, [f["total"] for f in measured["flux"]], drift_times)
    print(f"   measured: {curve['change_pct']:.2f} % over {curve['span_min']:.1f} min, "
          f"rate {curve['rate_pct_per_min']:.2f} %/min, "
          f"linear residual {curve['linear_residual_pct']:.3f} %")
    check(abs(curve["change_pct"] - 9.0) < 0.5,
          f"the measured drift is the injected 9 % ({curve['change_pct']:.2f} %)")
    check(curve["linear_residual_pct"] < 0.5, "a smooth drift fits a straight line")
    fixed = pa.analyse(pa.correct_drift(drifted, curve["factors"], BLACK, CEILING), drift_tiers,
                       PEAK_WINDOW, BLACK)
    print(f"   corrected:   judged={fixed['seams']['judged_bands']} "
          f"bad={fixed['seams']['bad_bands']} passed={fixed['seams']['passed']}")
    check(fixed["seams"]["passed"], "the same bracket passes once the drift is divided out")
    try:
        pa.drift_factors([0.0], [1.0], [0.0])
        check(False, "a single reference is refused")
    except ValueError:
        check(True, "a single reference is refused rather than fitted")

    print("\n11. the seam tolerance is the measured repeatability of one exposure")
    # The bracket repeats its shortest tier, so the same signal is captured
    # several times.  A tier that is 4 % off then has to be judged against that
    # spread, not against a fixed 2 % that sits below the instrument's own noise.
    # The repeats cannot join the merge (one exposure may appear only once), so
    # they arrive as a separate reference set.
    tiers3 = [1000, 4000, 16000]
    clean = bracket(tiers=tiers3)
    reference = bracket(tiers=tiers3)
    scattered_refs = [np.clip(np.round(BLACK + (reference[0] - BLACK) * scale), 0, CEILING)
                      for scale in (0.97, 1.00, 1.03)]
    stepped = bracket(tiers=tiers3, tier_scale={1: 1.04})
    found = pa.analyse(stepped, tiers3, PEAK_WINDOW, BLACK,
                       references={"frames": scattered_refs,
                                   "exposures_us": [tiers3[0]] * 3})
    repeats = found["repeatability"]
    print(f"   references: {len(scattered_refs)} at {repeats['groups'][0]['exposure_us']:.0f} us, "
          f"worst ring spread {repeats['worst_pct']:.2f} %, source={repeats['source']}")
    check(repeats["available"], "the reference exposures make the spread measurable")
    check(repeats["worst_pct"] > 4.0,
          f"the injected 6 % scatter is measured ({repeats['worst_pct']:.1f} %)")
    judged_here = [b for pair in found["seams"]["pairs"] for b in pair["bands"] if b["judged"]]
    check(bool(judged_here) and all(b["tolerance_source"].startswith("measured")
                                    for b in judged_here),
          "every judged band takes its tolerance from the measured spread")
    print(f"   judged={found['seams']['judged_bands']} bad={found['seams']['bad_bands']} "
          f"strict_bad={found['seams']['strict_bad_bands']}")
    for band in judged_here:
        print("     r{}-{}  ratio {:.4f}  tolerance {:.2f} % ({})  {}".format(
            band["radius_px"][0], band["radius_px"][1], band["ratio"],
            100 * band["tolerance"], band["tolerance_source"],
            "within" if band["within"] else "outside"))
    # The tolerance is per ring, so the same 4 % step is tolerated where this
    # ring repeats to more than 4 % and flagged where it repeats to less.  That
    # is the whole point: a flat 2 % would have flagged every ring.
    roomy = [b for b in judged_here if b["tolerance"] > 0.045]
    flagged = [b for b in judged_here if not b["within"]]
    check(bool(roomy) and all(b["within"] for b in roomy),
          "rings whose own repeats scatter more than the error accept it")
    check(bool(flagged) and all(b["tolerance"] <= 0.045 for b in flagged),
          "the rings it still flags are the ones that repeat tighter than the error")
    check(found["seams"]["strict_bad_bands"] > found["seams"]["bad_bands"],
          "the old fixed 2 % rule flags more rings, and stays in the record")

    plain = pa.analyse(bracket(tiers=tiers3), tiers3, PEAK_WINDOW, BLACK)
    print(f"   no reference: available={plain['repeatability']['available']} "
          f"({plain['repeatability']['reason']})")
    check(not plain["repeatability"]["available"],
          "without a repeated exposure the spread is reported as unavailable")
    plain_judged = [b for pair in plain["seams"]["pairs"] for b in pair["bands"]
                    if b["judged"]]
    check(bool(plain_judged) and all(b["tolerance_source"] == "quantisation only"
                                     for b in plain_judged),
          "and the fixed rule stands, labelled as such")

    wild_refs = [np.clip(np.round(BLACK + (reference[0] - BLACK) * scale), 0, CEILING)
                 for scale in (0.80, 1.00, 1.20)]
    wild = pa.analyse(bracket(tiers=tiers3), tiers3, PEAK_WINDOW, BLACK,
                      references={"frames": wild_refs,
                                  "exposures_us": [tiers3[0]] * 3})
    reasons = [b.get("reason") or "" for pair in wild["seams"]["pairs"]
               for b in pair["bands"]]
    print(f"   a 40 % spread: judged={wild['seams']['judged_bands']} "
          f"passed={wild['seams']['passed']}")
    check(any("cannot verify" in reason for reason in reasons),
          "a spread too wide to verify anything is reported as unjudged")
    check(not wild["seams"]["passed"],
          "and the bracket is not called consistent on the strength of it")

    print()
    if failures:
        print(f"{len(failures)} FAILURE(S):")
        for item in failures:
            print("  -", item)
        return 1
    print("all PSF analysis checks passed")
    return 0


def test_suite():
    """Run the whole sectioned suite, so `pytest` covers it as well as by hand."""
    assert main() == 0


if __name__ == "__main__":
    raise SystemExit(main())
