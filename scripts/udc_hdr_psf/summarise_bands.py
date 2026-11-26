"""Per-band summary of a B1 session, printed as the record's own numbers.

Reads only ``progress.json``: every quantity below was measured during the
session and is stored there, so this cannot disagree with the record.  It exists
so the per-band report is a transcription rather than a re-derivation.

    python summarise_bands.py <session root>
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def main() -> int:
    session = Path(sys.argv[1]).resolve()
    protocol = load(session / "progress.json")
    accepted = sorted(protocol.get("accepted_bands") or [],
                      key=lambda b: b["wavelength_nm"])
    rejected = protocol.get("rejected_bands") or []

    print(f"session: {session.name}")
    print(f"ceiling: {protocol.get('session_ceiling_dn')} DN, "
          f"black: {(accepted or [{}])[0].get('black_dn', 'n/a')}")
    print()
    header = ("| nm | peak DN | peak % span | core@ceil | r_g | b_r | drift % | "
              "drift %/min | fit resid % | repeat % (stored) | seams judged/bad/strict |")
    print(header)
    print("|" + "---|" * 12)
    noisy_drift = []
    for band in accepted:
        verdict = band["verdict"]
        drift = band.get("drift") or {}
        seams = band["seams"]
        ratios = band["ratios"]["20"]
        repeat = band.get("repeatability") or {}
        rep = ("n/a" if "worst_pct" not in repeat
               else f"{repeat['worst_pct']:.2f} (r{repeat.get('worst_ring', '?')})")
        if drift.get("applied"):
            residual = drift.get("linear_residual_pct", 0.0)
            if abs(residual) > abs(drift.get("change_pct") or 0.0):
                noisy_drift.append(band["wavelength_nm"])
        print("| {nm} | {peak:.0f} | {frac:.1f} | {clip:.2f} | {rg:.4f} | {br:.4f} | "
              "{d} | {rate} | {resid} | {rep} | {j}/{b}/{s} |".format(
                  nm=band["wavelength_nm"],
                  peak=verdict["peak_dn_shortest_tier"],
                  frac=100 * (verdict["peak_fraction"] or 0.0),
                  clip=verdict["core_saturated_fraction"],
                  rg=ratios["r_g"], br=ratios["b_r"],
                  d=("n/a" if not drift.get("applied")
                     else f"{drift['change_pct']:+.2f}"),
                  rate=("n/a" if not drift.get("applied")
                        else f"{drift.get('rate_pct_per_min', 0.0):+.2f}"),
                  resid=("n/a" if not drift.get("applied")
                         else f"{drift.get('linear_residual_pct', 0.0):.2f}"),
                  rep=rep,
                  j=seams["judged_bands"], b=seams["bad_bands"],
                  s=seams["strict_bad_bands"]))
    print()
    print("repeat % is the stored per-ring spread.  A band recorded before the "
          "level-unit fix stores the spread of the ring *sum* instead of the "
          "per-pixel level; reanalyse_band.py re-measures it from the frames.")
    if noisy_drift:
        print("drift is noise-limited in: " + ", ".join(f"{nm} nm" for nm in noisy_drift) +
              " (the straight-line residual exceeds the change it reports, so the "
              "correction it applied is within the estimator's own scatter)")

    by_nm = {band["wavelength_nm"]: band for band in accepted}
    if 450 in by_nm and 650 in by_nm:
        blue, red = by_nm[450]["ratios"]["20"], by_nm[650]["ratios"]["20"]
        print()
        print("across the range:")
        print(f"  B/R(450)  = {blue['b_r']:.4f}")
        print(f"  B/R(650)  = {red['b_r']:.4f}")
        print(f"  B/R ratio = {blue['b_r'] / red['b_r']:.2f}x")
        print(f"  R/G(450)  = {blue['r_g']:.4f}")
        print(f"  R/G(650)  = {red['r_g']:.4f}")
        print(f"  R/G ratio = {red['r_g'] / blue['r_g']:.2f}x")

    print()
    print("weak indicators, per band:")
    for band in accepted:
        items = list(band.get("indicators") or [])
        if not items:
            print(f"  {band['wavelength_nm']} nm: none")
            continue
        print(f"  {band['wavelength_nm']} nm:")
        for item in items:
            print(f"    - {item}")

    if rejected:
        print()
        print("rejected bands:")
        for band in rejected:
            print(f"  {band['wavelength_nm']} nm: {band.get('rejected_reason')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
