"""Re-run one recorded band's analysis from the frames already on disk.

No capture, no rewrite of the protocol: it reads the band entry, re-measures the
same window, and prints the numbers next to the ones that were recorded when the
band was accepted.  Its purpose is to answer "what does the current analysis
code say about this band" without re-running a bracket, so a statistic that was
computed by an earlier version can be replaced by a measurement rather than by a
memory.

    python reanalyse_band.py <session root> <wavelength_nm>
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import psf_analysis as psf  # noqa: E402
import drive_b1 as drv  # noqa: E402


def main() -> int:
    session = Path(sys.argv[1]).resolve()
    band = int(sys.argv[2])
    protocol = drv.load_json(session / "progress.json")
    entries = [b for b in protocol.get("accepted_bands") or []
               if b["wavelength_nm"] == band]
    if not entries:
        raise SystemExit(f"no accepted band at {band} nm in {session}")
    entry = entries[0]
    target = session / entry["target"]
    tiers = [int(t) for t in entry["tiers_us"]]
    order = [int(t) for t in entry["capture_order_us"]]
    # The bracket interleaves reference exposures with the tiers, so the tier
    # batches are not simply 0..n-1.  drift_schedule is the module-level form of
    # the driver's bracket_schedule with references enabled.
    schedule = drv.drift_schedule(tiers)
    references = list(entry["reference_batches"])
    if references and [int(v) for v in schedule["references"]] != [int(v) for v in references]:
        raise SystemExit(f"recorded reference batches {references} do not match the drift "
                         f"schedule {schedule['references']}")
    rect = entry["window_xywh"]
    peak = tuple(entry["peak_xy"])
    peak_local = (peak[0] - rect[0], peak[1] - rect[1])
    black = float(entry["black_dn"])
    ceiling_dn = int(protocol["session_ceiling_dn"])

    frames = drv.read_window(target, tiers, rect, schedule["tiers"])
    drift = entry.get("drift") or {}
    if drift.get("applied"):
        frames = psf.correct_drift(frames, drift["factors"], black, ceiling_dn)
    reference = None
    if references:
        reference_tiers = [order[index] for index in references]
        reference = {"frames": drv.read_window(target, reference_tiers, rect, references),
                     "exposures_us": reference_tiers}
    analysis = psf.analyse(frames, tiers, peak_local, black,
                           ceiling={"ceiling_dn": ceiling_dn, "plateau": True,
                                    "source": "session"},
                           references=reference)
    fresh = psf.verdict(analysis)

    stored = entry["verdict"]
    print(json.dumps({
        "band": band, "target": entry["target"],
        "peak_dn": {"stored": stored["peak_dn_shortest_tier"],
                    "fresh": fresh["peak_dn_shortest_tier"]},
        "peak_fraction": {"stored": stored["peak_fraction"],
                          "fresh": fresh["peak_fraction"]},
        "core_saturated": {"stored": stored["core_saturated_fraction"],
                           "fresh": fresh["core_saturated_fraction"]},
        "r_g_20px": {"stored": entry["ratios"]["20"]["r_g"],
                     "fresh": analysis["ratios"]["20"]["r_g"]},
        "b_r_20px": {"stored": entry["ratios"]["20"]["b_r"],
                     "fresh": analysis["ratios"]["20"]["b_r"]},
        "repeatability_worst_pct": {
            "stored": (entry.get("repeatability") or {}).get("worst_pct"),
            "fresh": analysis["repeatability"].get("worst_pct"),
            "fresh_worst_ring": analysis["repeatability"].get("worst_ring"),
            "fresh_source": analysis["repeatability"].get("source")},
        "seams": {"stored_judged": entry["seams"]["judged_bands"],
                  "stored_bad": entry["seams"]["bad_bands"],
                  "stored_strict_bad": entry["seams"]["strict_bad_bands"],
                  "fresh_judged": analysis["seams"]["judged_bands"],
                  "fresh_bad": analysis["seams"]["bad_bands"],
                  "fresh_strict_bad": analysis["seams"]["strict_bad_bands"]},
        "bad_bands": [
            {"pair_us": [pair["short_us"], pair["long_us"]],
             "radius_px": row["radius_px"], "ratio": row["ratio"],
             "tolerance": row["tolerance"], "level_dn": row["short_level_dn"]}
            for pair in analysis["seams"]["pairs"] for row in pair["bands"]
            if row["judged"] and not row["within"]],
    }, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
