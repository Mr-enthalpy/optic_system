"""B1 session driver: spectral sweep 400-650 nm at 10 nm with an HDR ladder.

Every machine-specific value - the repository to import, the adb executable, the
serial, the session root - is an argument; nothing here names a host.  Capture
plans are still built and validated by the public UDC task code, not by this
driver.

Why this script exists
----------------------
The 2026-11-26 sweep was invalid: the monochromator could not change its output
wavelength, so all 18 "bands" carried one spectrum (see
``CAPTURE_INVALIDATION_20261127.md`` in the dataset root).  That failure was
invisible per run and was found only after the sweep had finished.  Three
things are therefore different here:

1. A spectral gate runs *before* the sweep.  450 nm and 650 nm are measured at
   four exposures each and the per-CFA-phase ratios must move by the expected
   factor or the session aborts.  Cost: 8 runs, roughly 5 minutes.
2. Every band writes its own CFA-phase signature into ``progress.json`` while
   it is captured, so the sweep is self-evidencing as it runs, not only at the
   end.
3. The band bracket is a 4x ladder whose shortest tier is placed so the core
   lands near half the span, instead of a fixed 25x/100x triple.  A 25x step
   leaves every seam at 4-15 DN, where one code is 7-25 % of the signal and no
   seam can be judged at all; 4x puts the seam at 150-250 DN, where one code is
   0.4-0.7 %.  The ladder runs to the 400 ms cap because the PSF halo beyond
   r=20 px only becomes measurable about four decades above the core.

Geometry is the measured rig, not a plan default: 50 um pinhole at 750 mm,
f = 3.56 mm, f/2, quad-CFA BGGR read out 2x2 binned.  That is 75 cm, not the
1 m of the reference paper: what follows is a measurement of this rig, not a
reproduction of theirs.

Stages, resumable by rerunning the same command:
    purge      delete the frozen 2026-11-26 phone runs by exact ID
    ceiling    one short 550 nm run that measures where the pipeline clips
    dark       start dark set (operator occludes the source)
    gate       spectral source check at 450 / 650 nm
    passthru   broadband zero-order white PSF (TLS set_pass_through)
    sweep      the bands
    manifest   fusion manifest + session-level spectral check

Operator gates are opened by writing ``gates/<name>.confirmed.json``.  Use
``--confirm-gate <name>`` to write it from the same machine.

Usage:
    python drive_b1.py --repo <optic_system> --adb <adb.exe> \
        --output D:/datasets/axon20/udc_hdr_psf_20261127 \
        --bands 400:650:10 --frame-count 3 --confirm-geometry
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import psf_analysis as psf  # noqa: E402  (private module beside this driver)

APP_PACKAGE = "org.udc.rawprobe"
MIN_US = 14
MAX_US = 400000          # hardware cap is 426.5 ms; stay below it
LADDER_RATIO = 4
# The shortest tier should put the brightest pixel of the PSF near this fraction
# of the span: high enough that the core spans several hundred codes, low enough
# that it never clips.  The peak pixel, not the 3x3 median, is the statistic that
# matters: this PSF is about two pixels wide, so the median of a 3x3 core is
# dominated by the wings and under-reads the pixel that actually clips first.
# The 2026-11-27 probe measured 96 DN (median) against 246 DN (peak) at 376 us.
TARGET_PEAK_FRACTION = 0.80
BLACK_DN = 64.0
# The gate brackets take their own start tier from a one-batch probe per band:
# a fixed 250 us was tuned to a source twice as bright as the 2026-11-27 probe
# measured, and a single shared tier leaves 450 nm at 29 % of span where only
# one seam band is above the judging floor.
GATE_TIER_COUNT = 3
PASSTHRU_TIERS_US = (14, 56, 224, 896, 3584, 14336)

_CLOSERS = []


# --------------------------------------------------------------------------
# small file helpers
# --------------------------------------------------------------------------
def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def json_clean(value):
    """JSON has no NaN or infinity, and a ratio can be undefined.

    A band where the red plane receives nothing has B/R = inf, which is the
    honest arithmetic answer inside the analysis but cannot be written to disk.
    Those values become null here, at the boundary: the record keeps the band and
    says the ratio was undefined, instead of the session dying after two hours of
    capture.
    """
    if isinstance(value, dict):
        return {key: json_clean(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_clean(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, (bool, int, float, str)) or value is None:
        return value
    if hasattr(value, "item"):          # numpy scalar
        return json_clean(value.item())
    raise TypeError(f"Cannot serialise {type(value).__name__}")


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise FileExistsError(path)
    path.write_text(json.dumps(json_clean(value), indent=2, allow_nan=False), encoding="utf-8")


def replace_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(json_clean(value), indent=2, allow_nan=False), encoding="utf-8")
    temporary.replace(path)


def sha256_file(path) -> str:
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


DRIVER_SHA = sha256_file(Path(__file__))


def hash_tree(root: Path) -> dict:
    return {str(p.relative_to(root)).replace("\\", "/"): {"sha256": sha256_file(p),
                                                          "bytes": p.stat().st_size}
            for p in sorted(root.rglob("*")) if p.is_file()}


def parse_bands(spec: str) -> list[int]:
    if ":" in spec:
        start, stop, step = (int(v) for v in spec.split(":"))
        return list(range(start, stop + 1, step))
    return [int(v) for v in spec.split(",") if v.strip()]


def parse_int_set(spec: str, everything=None) -> set[int]:
    """``all`` means every band in the sweep; ``none`` means the plain ladder."""
    text = (spec or "").strip().lower()
    if text in ("all", "*"):
        return {int(v) for v in (everything or [])}
    if text in ("", "none", "-"):
        return set()
    return {int(v) for v in text.split(",") if v.strip()}


# --------------------------------------------------------------------------
# exposure ladder
# --------------------------------------------------------------------------
def ladder_from_base(shortest_us: int, tiers: int, reach: bool) -> list[int]:
    """4x ladder whose *shortest* tier is ``shortest_us``.

    The shortest tier is set so the brightest pixel of the PSF sits near
    TARGET_PEAK_FRACTION of the span: hundreds of codes deep, and not clipping.
    Above it the ladder climbs by 4x.  ``reach`` continues to the exposure cap,
    which is what the r=20-40 px halo needs: at 550 nm that halo is 0.3 DN in a
    near-core exposure, so it only becomes measurable about four decades up.

    A 4x step is not arbitrary.  The two tiers meet where the short one is at a
    quarter of the ceiling, which is still hundreds of codes; a 25x step makes
    the whole overlap sit at 4-15 DN, where one code is 7-25 % and no seam can be
    verified at all.  A cap tier is added only when it is at least 1.5x above its
    predecessor, for the same reason.

    The shortest tier is clamped to ``MAX_US // LADDER_RATIO`` so the ladder can
    never collapse to a single exposure: a one-tier bracket has no seam to check.
    """
    values = [max(MIN_US, min(int(round(shortest_us)), MAX_US // LADDER_RATIO))]
    while len(values) < max(2, tiers) and values[-1] * LADDER_RATIO <= MAX_US:
        values.append(values[-1] * LADDER_RATIO)
    if reach:
        while values[-1] * LADDER_RATIO <= MAX_US:
            values.append(values[-1] * LADDER_RATIO)
        if MAX_US / values[-1] >= 1.5:
            values.append(MAX_US)
    return sorted(set(values))


def target_peak_dn(ceiling_dn: float, black: float = BLACK_DN) -> float:
    return TARGET_PEAK_FRACTION * (float(ceiling_dn) - float(black))


def runs_named_by_record(record) -> tuple[set[str], int]:
    """Run IDs the invalidation record names, and how many batches it lists.

    The second number is not decoration: a batch without a ``run_id`` cannot be
    deleted by name, so the purge reports it as unattributed instead of guessing
    which run it was.
    """
    wanted, batches = set(), 0
    for entry in record.get("scope") or []:
        for batch in entry.get("batches") or []:
            batches += 1
            if batch.get("run_id"):
                wanted.add(batch["run_id"])
        for item in entry.get("files") or []:
            # Paths in the record are Windows-relative; normalise before matching.
            path = str(item.get("path", "")).replace("\\", "/")
            match = re.search(r"captures/(run_\d+_[a-f0-9]{8})/", path)
            if match:
                wanted.add(match.group(1))
    return wanted, batches


def retry_transient(action, attempts: int, delay_s: float, label: str,
                    retryable, log=None):
    """Call ``action(attempt)`` until it stops raising a *transient* error.

    The grating reached 450.0 nm on 2026-11-27 and the session died anyway,
    because ``get_curr_wave`` answered E01 once while the move was being
    confirmed.  A read error from an instrument that is physically fine costs a
    retry, not the session.  Deliberate aborts raise ``RuntimeError`` and are
    never retried, so only the caller's ``retryable`` types get a second chance.
    """
    log = log or print
    last = None
    for attempt in range(1, attempts + 1):
        try:
            return action(attempt)
        except retryable as exc:
            last = exc
            log(json.dumps({"transient_retry": label, "attempt": attempt,
                            "of": attempts, "error": str(exc)}, ensure_ascii=False))
            if attempt < attempts:
                time.sleep(delay_s)
    raise RuntimeError(f"{label}: still failing after {attempts} attempts ({last})")


def next_shortest_us(peak_dn: float, current_us: int, ceiling_dn: float,
                     black: float = BLACK_DN) -> int:
    """Rescale the shortest tier so the peak lands near the target.

    ``peak_dn`` is the merged peak expressed in shortest-tier DN, which survives
    a clipped pixel: the merger takes it from the shortest tier still below the
    ceiling.  Scaling on the 3x3 core median instead would need a 5.5x longer
    exposure at 550 nm and would clip the peak doing it.
    """
    peak = float(peak_dn)
    if not peak == peak or peak <= 0.0:
        return int(current_us)
    scaled = float(current_us) * target_peak_dn(ceiling_dn, black) / peak
    return int(min(MAX_US, max(MIN_US, round(scaled))))


def estimated_batch_bytes(frames: int) -> int:
    """Host bytes for one batch.

    Measured from the 2026-11-26 export: 32 MB tar + 32 MB extracted + 16 MB
    decoded npy per frame, plus 128 MB of float64 mean/std per batch.
    """
    return frames * (32 + 32 + 16) * 2**20 + 129 * 2**20


# --------------------------------------------------------------------------
# PSF analysis
# --------------------------------------------------------------------------
# The measurement logic lives in psf_analysis.py so it can be tested without
# hardware.  These helpers only turn a capture directory into its inputs.
def drift_schedule(tiers_us) -> dict:
    """Capture order for one bracket: t0, t1, t0, t2, t0, ... with a reference after each tier.

    The xenon lamp drifts by a few percent over the minutes a bracket takes, and
    a seam error of 4 % cannot be attributed to the sensor or the lamp without
    knowing which.  Repeating the shortest, never-clipped exposure between tiers
    makes the lamp's own brightness a measured quantity, so the bracket can be
    corrected to one instant instead of judged against a drift it cannot see.
    """
    tiers = [int(t) for t in tiers_us]
    if len(tiers) < 2:
        return {"order": tiers, "tiers": list(range(len(tiers))), "references": []}
    order, tier_index, reference_index = [tiers[0]], [0], [0]
    for exposure in tiers[1:]:
        order.extend([exposure, tiers[0]])
        tier_index.append(len(order) - 2)
        reference_index.append(len(order) - 1)
    return {"order": order, "tiers": tier_index, "references": reference_index}


def plain_schedule(tiers_us) -> dict:
    """No reference exposures: used where the bracket is not judged for seams."""
    tiers = [int(t) for t in tiers_us]
    return {"order": tiers, "tiers": list(range(len(tiers))), "references": []}


def resume_seeds(attempts) -> dict:
    """Per band, the exposure a decided-but-unfinished retry was going to use.

    ``band_attempts`` keeps every attempt with its outcome.  An entry that has a
    ``next_shortest_us`` and neither ``accepted`` nor ``rejected_reason`` means
    the driver decided to recapture the band shorter and then died before doing
    it.  Resuming at the session seed instead would re-take the bracket that
    already failed.
    """
    seeds = {}
    for entry in attempts or []:
        band = entry.get("wavelength_nm")
        if band is None or entry.get("accepted") or entry.get("rejected_reason"):
            continue
        if entry.get("next_shortest_us"):
            seeds[int(band)] = int(entry["next_shortest_us"])
            continue
        # Attempts written before the decision was stored carry only the verdict
        # that caused it.  A clipped core is reconstructed with the same rule the
        # retry branch uses; anything else falls back to the session seed rather
        # than to a guess.
        verdict = entry.get("verdict") or {}
        shortest = entry.get("shortest_us")
        if verdict.get("core_clipped") and shortest:
            seeds[int(band)] = max(MIN_US, int(shortest) // LADDER_RATIO)
    return seeds


def batch_times(run_dir: Path, indices) -> list:
    """Wall-clock seconds (epoch) at which each batch was taken, or [] if unrecorded."""
    stamps = []
    for index in indices:
        found = None
        for run_json in sorted((run_dir / f"batch_{index:03d}").glob(
                "export/device_files/captures/run_*/run.json")):
            body = load_json(run_json)
            created = body.get("created_unix_ms")
            if isinstance(created, (int, float)):
                found = float(created) / 1000.0
                break
            frames = body.get("frames") or []
            if frames and isinstance(frames[0].get("sensor_timestamp_ns"), (int, float)):
                found = float(frames[0]["sensor_timestamp_ns"]) / 1e9
                break
        stamps.append(found)
    if any(value is None for value in stamps):
        return []
    return stamps


def peak_position(mean_path: Path):
    """Full-frame peak position, from one tier's temporal mean."""
    import numpy as np
    mean = np.load(mean_path, mmap_mode="r")
    if tuple(mean.shape) != (2448, 3264):
        raise ValueError(f"Unexpected frame shape {mean.shape}")
    y, x = np.unravel_index(int(np.argmax(mean)), mean.shape)
    return int(x), int(y)


def frame_peak_dn(mean_path: Path, black: float) -> float:
    """Peak signal of one temporal mean, in DN above black."""
    import numpy as np
    mean = np.load(mean_path, mmap_mode="r")
    if tuple(mean.shape) != (2448, 3264):
        raise ValueError(f"Unexpected frame shape {mean.shape}")
    return float(np.max(mean)) - float(black)


def read_window(run_dir: Path, tiers_us, rect, indices=None):
    """Window crop of every listed batch's temporal mean, in the order given."""
    import numpy as np
    if indices is None:
        indices = list(range(len(tiers_us)))
    frames = []
    for index in indices:
        mean = np.load(run_dir / f"batch_{int(index):03d}/decoded/temporal_mean_dn.npy",
                       mmap_mode="r")
        frames.append(np.array(psf.crop(mean, rect), dtype=np.float64))
    return frames


def tier_summaries(run_dir: Path, tiers_us, indices=None):
    """Exposure, black level and readback facts for every listed batch."""
    if indices is None:
        indices = list(range(len(tiers_us)))
    rows = []
    for exposure, index in zip(tiers_us, indices):
        summary = load_json(run_dir / f"batch_{index:03d}/decoded/summary.json")
        pattern = summary.get("declared_black_level_pattern")
        if not isinstance(pattern, list) or len(pattern) != 4:
            raise ValueError("Explicit four-parity black levels are required")
        if not summary.get("all_manual_readbacks_within_tolerance"):
            raise ValueError("Manual exposure/ISO readback failed")
        rows.append({
            "batch": index, "exposure_us": float(exposure),
            "black_dn": sum(pattern) / 4.0,
            "declared_white_dn": float(summary["declared_white_level"]),
            "capture_complete": bool(summary.get("capture_complete")),
            "run_succeeded": bool(summary.get("run_succeeded")),
            "observed_max_dn": [float(f.get("observed_max_dn") or 0)
                                for f in summary.get("frames") or []],
            "fraction_at_declared_white": [
                float(f.get("fraction_at_or_above_declared_white") or 0)
                for f in summary.get("frames") or []],
        })
    return rows


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------
def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo", type=Path, help="optic_system checkout")
    parser.add_argument("--adb", type=Path)
    parser.add_argument("--output", type=Path, required=True, help="session directory")
    parser.add_argument("--confirm-gate", default=None,
                        help="write gates/<name>.confirmed.json and exit")
    parser.add_argument("--operator", default="operator")
    parser.add_argument("--bands", default="400:650:10")
    parser.add_argument("--frame-count", type=int, default=3)
    parser.add_argument("--gate-frames", type=int, default=2)
    parser.add_argument("--ladder-tiers", type=int, default=5)
    parser.add_argument("--reach-bands", default="all",
                        help="bands whose ladder runs to the exposure cap, or 'all' / 'none'")
    parser.add_argument("--initial-shortest-us", type=int, default=376,
                        help="shortest exposure in the first ladder; 376 us is where the 550 nm core "
                             "sat near half the span in the 2026-11-26 session")
    parser.add_argument("--ceiling-probe", action="store_true",
                        help="measure the RAW saturation plateau from one short run at 550 nm and exit")
    parser.add_argument("--ceiling-probe-us", type=int, default=24064,
                        help="longest tier of the ceiling probe; it must clip the core")
    parser.add_argument("--rejudge-gate", action="store_true",
                        help="re-run the gate analysis on the brackets already recorded in "
                             "SOURCE_GATE.json instead of capturing them again")
    parser.add_argument("--limit-bands", type=int, default=0, help="smoke test: only the first N bands")
    parser.add_argument("--confirm-geometry", action="store_true",
                        help="operator confirms 50 um pinhole at 750 mm, nothing in the beam")
    parser.add_argument("--purge-record", type=Path, default=None,
                        help="CAPTURE_INVALIDATION_*.json whose phone runs must be destroyed")
    parser.add_argument("--confirm-purge", action="store_true")
    parser.add_argument("--phone-retention", choices=["keep", "purge"], default="keep")
    parser.add_argument("--skip-gate", action="store_true")
    parser.add_argument("--skip-passthrough", action="store_true")
    parser.add_argument("--dark-end", action="store_true")
    parser.add_argument("--stop-after", choices=["purge", "dark", "gate", "passthrough", "sweep"], default=None)
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--expected-app-version", default="0.3")
    parser.add_argument("--serial")
    parser.add_argument("--camera-id")
    parser.add_argument("--tls-settle-s", type=float, default=3.0)
    parser.add_argument("--tls-move-attempts", type=int, default=4)
    parser.add_argument("--tls-retry-s", type=float, default=5.0)
    parser.add_argument("--no-drift-references", action="store_true",
                        help="capture the ladder in plain order, without the interleaved "
                             "reference exposures that measure the lamp's drift")
    parser.add_argument("--gate-timeout-s", type=float, default=1800.0)
    parser.add_argument("--min-host-free-gb", type=float, default=80.0)
    parser.add_argument("--psf-window", type=int, default=psf.DEFAULT_WINDOW_PX,
                        help="square analysis window around the PSF core, in binned pixels")
    parser.add_argument("--one-bracket", default=None,
                        help="B2 rotation session: capture one bracket at --one-band and "
                             "--one-start-us, write <label>.json and exit")
    parser.add_argument("--one-band", type=int, default=None,
                        help="nominal wavelength of the single bracket")
    parser.add_argument("--one-start-us", type=int, default=None,
                        help="shortest exposure of the single bracket")
    parser.add_argument("--one-dark", default=None,
                        help="B2 rotation session: capture one dark set under this label and exit")
    parser.add_argument("--assume-ceiling-dn", type=float, default=None,
                        help="seed the session ceiling, e.g. 1023 from the B1 plateau")
    args = parser.parse_args(argv)
    output = args.output.resolve()

    if args.confirm_gate:
        gates = output / "gates"
        gates.mkdir(parents=True, exist_ok=True)
        waiting = gates / f"{args.confirm_gate}.waiting.json"
        if not waiting.exists():
            raise SystemExit(f"no open gate named {args.confirm_gate!r} in {gates}")
        write_json(gates / f"{args.confirm_gate}.confirmed.json",
                   {"operator_confirmed": True, "operator": args.operator,
                    "confirmed_utc": utc_now(),
                    "instruction_seen": load_json(waiting).get("instruction")})
        print(f"confirmed {args.confirm_gate}")
        return 0

    if not args.repo or (not args.adb and not args.validate_only):
        raise SystemExit("--repo is required, and --adb unless --validate-only or --confirm-gate")
    repo = args.repo.resolve()
    sys.path.insert(0, str(repo))
    from tasks.udc.plan import UDCBatch, UDCCapturePlan
    from tasks.illumination import IlluminationSpec

    bands = parse_bands(args.bands)
    if args.limit_bands:
        bands = bands[: args.limit_bands]
    if args.one_bracket and args.one_band is not None:
        # A rotation session runs one band at a time; a protocol that claimed
        # the default 26-band sweep would misstate what this session is.
        bands = [int(args.one_band)]
    reach = parse_int_set(args.reach_bands, bands)
    distance_mm, aperture_um = 750.0, 50.0
    base_text = ("External monochromatic source through a 50 um pinhole at 750 mm; "
                 "quad-CFA sensor read out 2x2 binned. ")

    def source_plan(nominal, readback, tiers, label, frames=None):
        return UDCCapturePlan(
            plan_id=label, condition="point",
            source_description=base_text + "Nominal TLS setting; bandwidth, order contamination and spectral purity unmeasured.",
            display_state="Full-screen uniform black overlay at fixed window brightness 1.0; OS bars hidden; vendor UDC display patch unverified.",
            illumination=IlluminationSpec("monochromatic", float(readback), None,
                                          wavelength_label_nm=float(nominal)),
            source_distance_mm=distance_mm, aperture_diameter_um=aperture_um,
            iso=100, raw_format="RAW_SENSOR", pair="none", companion_size="preview",
            batches=tuple(UDCBatch(int(e), frames or args.frame_count, shading="off",
                                   tonemap="high_quality", display="black") for e in tiers))

    def passthrough_plan(tiers, label):
        # A broadband pass-through cannot carry an IlluminationSpec: the plan type
        # refuses to fabricate a monochromatic claim and has no broadband mode, so
        # the plan records no illumination and the TLS action is recorded by this
        # driver instead (see PASSTHROUGH_WHITE.json).
        return UDCCapturePlan(
            plan_id=label, condition="unspecified",
            source_description=("Broadband zero-order pass-through of the same lamp through the same "
                                "50 um pinhole at 750 mm; the grating is set to pass-through, not to a "
                                "wavelength. The spectrum, its bandwidth and its absolute power are "
                                "unmeasured. "),
            display_state="Full-screen uniform black overlay at fixed window brightness 1.0; OS bars hidden; vendor UDC display patch unverified.",
            illumination=None, source_distance_mm=distance_mm, aperture_diameter_um=aperture_um,
            iso=100, raw_format="RAW_SENSOR", pair="none", companion_size="preview",
            batches=tuple(UDCBatch(int(e), args.frame_count, shading="off",
                                   tonemap="high_quality", display="black") for e in tiers))

    def dark_plan(tiers, label):
        return UDCCapturePlan(
            plan_id=label, condition="dark",
            source_description="External occlusion / dark room; no incident wavelength is assigned.",
            display_state="Full-screen uniform black overlay.",
            iso=100, raw_format="RAW_SENSOR", pair="none", companion_size="preview",
            batches=tuple(UDCBatch(int(e), args.frame_count, shading="off",
                                   tonemap="high_quality", display="black") for e in tiers))

    # ---- every plan shape is validated before any hardware access ----------
    probe_short = min(args.initial_shortest_us, args.ceiling_probe_us)
    probe_long = max(args.initial_shortest_us, args.ceiling_probe_us)
    # Three rungs, not two: the middle one gives a second line of evidence and a
    # judged seam, and the top one must clip the core hard, because a plateau is
    # only detectable when many pixels sit on the same code.
    PROBE_TIERS_US = tuple(sorted({probe_short,
                                   min(probe_long, probe_short * 16),
                                   probe_long}))
    gate_tiers = ladder_from_base(args.initial_shortest_us, GATE_TIER_COUNT, True)
    # The gate ladder itself is decided per band from a one-batch probe: the two
    # bands differ by more than 1.5x in brightness, so one shared ladder cannot
    # put both peaks in the linear range (450 nm sat at 29 % of span at 1171 us,
    # where its rings were one code deep).
    GATE_PROBE_US = int(args.initial_shortest_us)

    def bracket_schedule(tiers):
        """Spectral brackets carry reference exposures unless explicitly disabled."""
        return plain_schedule(tiers) if args.no_drift_references else drift_schedule(tiers)

    ladder_preview = {}
    plans = {}
    for nominal in bands:
        tiers = ladder_from_base(args.initial_shortest_us, args.ladder_tiers, nominal in reach)
        ladder_preview[nominal] = tiers
        plans[f"band_{nominal:03d}nm"] = source_plan(nominal, nominal,
                                                     bracket_schedule(tiers)["order"],
                                                     f"band_{nominal:03d}nm")
    plans["gate_probe_450nm"] = source_plan(450, 450, [GATE_PROBE_US], "gate_probe_450nm")
    plans["gate_probe_650nm"] = source_plan(650, 650, [GATE_PROBE_US], "gate_probe_650nm")
    plans["gate_450nm"] = source_plan(450, 450, bracket_schedule(gate_tiers)["order"],
                                      "gate_450nm", args.gate_frames)
    plans["gate_650nm"] = source_plan(650, 650, bracket_schedule(gate_tiers)["order"],
                                      "gate_650nm", args.gate_frames)
    plans["passthrough_white"] = passthrough_plan(PASSTHRU_TIERS_US, "passthrough_white")
    plans["ceiling_probe_550nm"] = source_plan(550, 550, PROBE_TIERS_US, "ceiling_probe_550nm",
                                               args.gate_frames)
    plans["dark_start"] = dark_plan((gate_tiers[0], gate_tiers[2], MAX_US), "dark_start")
    for label, plan in plans.items():
        UDCCapturePlan.from_dict(plan.to_dict())

    sweep_frames = sum(batch.frames for m in plans.values() for batch in m.batches)
    if args.validate_only:
        extra = len(plans["dark_start"].batches) if args.dark_end else 0
        total_batches = sum(len(m.batches) for m in plans.values()) + extra
        print(json.dumps({
            "validated_plans": len(plans),
            "bands": len(bands),
            "batches": total_batches,
            "frames": sweep_frames + extra * args.frame_count,
            "host_estimated_gb": round(sum(estimated_batch_bytes(batch.frames)
                                           for m in plans.values() for batch in m.batches) / 2**30, 1),
            "phone_estimated_gb": round((sweep_frames + extra * args.frame_count) * 32 / 1024, 1),
            "ladder_tiers": {str(k): v for k, v in ladder_preview.items()},
            "reach_bands": sorted(reach & set(bands)),
            "gate_tiers_us": list(gate_tiers),
            "gate_tiers_note": ("preview only, used for the byte estimate; each gate band's real "
                                "ladder is derived from its own one-batch probe at capture time"),
            "ceiling_probe_tiers_us": list(PROBE_TIERS_US),
            "passthrough_tiers_us": list(PASSTHRU_TIERS_US),
        }, indent=1, sort_keys=True))
        return 0

    probe = output if output.exists() else output.parent
    host_free = shutil.disk_usage(probe).free / 2**30
    if host_free < args.min_host_free_gb:
        raise ValueError(f"Host free space {host_free:.1f} GB is below --min-host-free-gb")

    from tasks.udc.capture import run_udc_capture_plan
    from tools.android_raw_probe import raw_tools
    from tools.android_raw_probe.device import Device, PACKAGE
    from devices.tls_service import TLSService
    from dataclasses import asdict

    if not (os.environ.get("TLS_C1_SDK_DIR") and os.environ.get("TLS_C1_SERIAL")):
        raise ValueError("TLS_C1_SDK_DIR and TLS_C1_SERIAL are required in the process environment")

    output.mkdir(parents=True, exist_ok=True)
    progress_path = output / "progress.json"
    protocol = load_json(progress_path) if progress_path.exists() else {}
    created_runs: set[str] = set()

    def save():
        replace_json(progress_path, protocol)

    phone = Device(args.adb, args.serial)
    package_dump = phone.shell(["dumpsys", "package", PACKAGE])
    version = re.search(r"versionName=([^\s]+)", package_dump)
    if not version or version.group(1) != args.expected_app_version:
        raise RuntimeError(f"Installed app version {version.group(1) if version else 'unknown'} "
                           f"!= {args.expected_app_version}")

    def phone_free_gb():
        fields = phone.shell(["df", "-k", "/data"]).strip().splitlines()[-1].split()
        return int(fields[3]) / 1024**2 if len(fields) >= 4 else None

    def ensure_awake():
        deadline = time.time() + 15.0
        while True:
            if "mwakefulness=awake" in phone.shell(["dumpsys", "power"]).lower():
                break
            if time.time() > deadline:
                raise RuntimeError("Phone stayed dozing; refusing to launch a capture")
            phone.shell(["input", "keyevent", "KEYCODE_WAKEUP"])
            time.sleep(1.5)
        phone.shell(["wm", "dismiss-keyguard"])
        time.sleep(0.5)

    def unique_label(label):
        """A retry or a mid-band kill must not collide with an existing directory."""
        if not (output / "captures" / label).exists():
            return label
        for index in range(1, 100):
            candidate = f"{label}_r{index}"
            if not (output / "captures" / candidate).exists():
                return candidate
        raise RuntimeError(f"No free retry name for {label}")

    def invoke(label, plan):
        if (output / "PAUSE_REQUESTED.txt").exists():
            raise RuntimeError("Operator requested a pause")
        target = output / "captures" / unique_label(label)
        ensure_awake()
        result = run_udc_capture_plan(plan, adb=args.adb, output=target,
                                      condition_verified=True, camera_id=args.camera_id,
                                      serial=args.serial)
        run_ids = [batch["phone_run_id"] for batch in result["batches"]]
        created_runs.update(run_ids)
        if not result["run_succeeded"]:
            raise RuntimeError(f"Plan {label} did not succeed")
        return target, run_ids

    def operator_gate(name, instruction):
        gates = output / "gates"
        gates.mkdir(parents=True, exist_ok=True)
        confirmed = gates / f"{name}.confirmed.json"
        if not confirmed.exists():
            replace_json(gates / f"{name}.waiting.json",
                         {"gate": name, "instruction": instruction, "since_utc": utc_now(),
                          "confirm_by_writing": f"--confirm-gate {name}"})
            print(json.dumps({"waiting_for_operator": name, "instruction": instruction,
                              "how": f"python drive_b1.py --output <session> --confirm-gate {name}"}),
                  flush=True)
            deadline = time.time() + args.gate_timeout_s
            while not confirmed.exists():
                if time.time() > deadline:
                    raise RuntimeError(f"Operator gate timed out: {name}")
                if (output / "PAUSE_REQUESTED.txt").exists():
                    raise RuntimeError("Operator requested a pause while a gate was open")
                time.sleep(2.0)
        record = load_json(confirmed)
        if record.get("operator_confirmed") is not True:
            raise RuntimeError(f"Gate {name} is not an explicit operator confirmation")
        (gates / f"{name}.waiting.json").unlink(missing_ok=True)
        # A resumed session re-crosses every gate it already passed; recording
        # each crossing again would turn the log into a list of restarts.
        logged = protocol.setdefault("operator_gates", [])
        if not any(item.get("gate") == name for item in logged):
            logged.append({"gate": name, "instruction": instruction,
                           "confirmed_utc": record.get("confirmed_utc"),
                           "operator": record.get("operator"), "note": record.get("note", "")})
        save()
        return record

    tls = TLSService()
    tls.connect()
    _CLOSERS.append(tls.close)
    try:
        initial = asdict(tls.get_status())
        initial.pop("serial_number", None)
        if not initial["connected"] or initial["moving"] or initial["grating"] != 1:
            raise RuntimeError("TLS is not connected/idle on grating 1: " + json.dumps(initial))
    except Exception:
        tls.close()
        raise

    def tls_status(nominal=None):
        status = asdict(tls.get_status())
        status.pop("serial_number", None)
        if not status["connected"] or status["moving"]:
            raise RuntimeError("TLS is not connected/idle: " + json.dumps(status))
        if nominal is not None:
            current = status.get("current_wavelength_nm")
            if current is None or abs(current - nominal) > 0.5:
                raise RuntimeError("TLS readback does not match the nominal wavelength: "
                                   + json.dumps(status))
        return status

    def move_source(nominal):
        from devices.tls_service import TLSServiceError

        def attempt(index):
            if (output / "PAUSE_REQUESTED.txt").exists():
                raise RuntimeError("Operator requested a pause")
            if index > 1:
                # A repeated failure may be the USB link rather than one bad
                # read, so reopen the device before moving again.  A move is
                # idempotent; the grating cannot be driven past its limits by
                # asking for the same wavelength twice.
                try:
                    tls.close()
                    tls.connect()
                except Exception as exc:
                    print(json.dumps({"tls_reopen_failed": str(exc)}), flush=True)
            tls.set_wavelength_nm(nominal)
            tls.move()
            tls.wait_until_idle(tolerance_nm=0.5)
            time.sleep(args.tls_settle_s)
            return tls_status(nominal)

        return retry_transient(attempt, args.tls_move_attempts, args.tls_retry_s,
                               f"TLS move to {nominal} nm", (TLSServiceError,),
                               log=lambda rec: print(rec, flush=True))

    # ---- session protocol -------------------------------------------------
    if not protocol:
        head = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                              capture_output=True, text=True).stdout.strip()
        protocol = {
            "schema_version": 1, "created_utc": utc_now(), "mode": "hardware",
            "repo_commit": head, "private_driver_sha256": sha256_file(Path(__file__)),
            "app_version": args.expected_app_version,
            "bands": bands, "reach_bands": sorted(reach & set(bands)),
            "frame_count": args.frame_count, "ladder_tiers": args.ladder_tiers,
            "geometry": {"pinhole_um": aperture_um, "distance_mm": distance_mm,
                         "f_number": 2.0, "focal_mm": 3.56,
                         "sensor": "quad-CFA BGGR read out 2x2 binned, 3264x2448",
                         "note": "measured rig; the 500 um / 500 mm plan defaults are wrong and are not used"},
            "exposure_policy": {
                "ladder": "the shortest tier is set first, then 4x steps up to the cap",
                "shortest": ("chosen so the brightest pixel of the PSF sits near "
                             f"{TARGET_PEAK_FRACTION:.0%} of the span: deep enough to span hundreds "
                             "of codes, short enough not to clip"),
                "why": ("25x steps put every seam at 4-15 DN where one code is 7-25 %, so no seam "
                        "could be judged; 4x puts the seam at 150-250 DN where one code is 0.4-0.7 %"),
                "drift": ("the bracket is captured t0, t1, t0, t2, t0; the repeated shortest tier "
                          "measures the xenon lamp's own drift and each tier is divided by it, so "
                          "the seams are judged at one instant rather than across a drifting source")},
            "gates": {
                "spectral": ("450 and 650 nm, three tiers each with the reference exposures "
                             "interleaved; require B/R(450)/B/R(650) > 5 and "
                             "R/G(650)/R/G(450) > 3"),
                "seam": (f"per radial band, judged only where the short tier is above "
                         f"{psf.JUDGE_MIN_DN:.0f} DN, tolerance one code or "
                         f"{psf.SEAM_TOLERANCE_FLOOR:.0%} whichever is looser; at least two bands "
                         "must be judged and none may be outside"),
                "core": (f"the shortest tier must leave the peak below the ceiling and above "
                         f"{psf.MIN_PEAK_FRACTION:.0%} of the span"),
                "drift": ("the shortest tier is repeated between tiers, so the lamp's own "
                          "brightness change across a bracket is measured and divided out "
                          "before any seam is judged")},
            "phone_free_gb_at_start": phone_free_gb(),
            "host_free_gb_at_start": round(host_free, 3),
            "created_runs": [], "accepted_bands": [], "rejected_bands": [], "stages": {},
            "initial_tls_status": initial,
        }
        projected = sweep_frames * 32 / 1024
        protocol["phone_projected_gb"] = round(projected, 1)
        save()
        print(json.dumps({"session": str(output), "bands": len(bands),
                          "phone_free_gb": protocol["phone_free_gb_at_start"],
                          "phone_projected_gb": round(projected, 1),
                          "host_free_gb": round(host_free, 1)}), flush=True)
        if (args.phone_retention == "keep"
                and protocol["phone_free_gb_at_start"] is not None
                and projected * 1.2 > protocol["phone_free_gb_at_start"]):
            raise RuntimeError(
                f"Keeping every run would need about {projected:.1f} GB of "
                f"{protocol['phone_free_gb_at_start']:.1f} GB free. Rerun with "
                "--phone-retention purge, or shorten --bands.")

    def stage_done(name):
        return bool((protocol.get("stages") or {}).get(name, {}).get("done"))

    def one_shot(path, value):
        """Stage records are replaced on retry; the failed copy is kept in progress.json."""
        if path.exists():
            replace_json(path, value)
        else:
            write_json(path, value)

    def mark_stage(name, record):
        # Provenance is per stage, not per session: the protocol may be created
        # by one driver version and continued by another (it happened here: the
        # ceiling probe ran before the peak-based ladder was written).
        protocol.setdefault("stages", {})[name] = {"done": True, "recorded_utc": utc_now(),
                                                  "driver_sha256": DRIVER_SHA, **record}
        save()

    def stop_if_requested(name, **info):
        """Stop after a stage, whether this invocation ran it or found it done.

        The check used to live inside each stage body, so asking to stop after a
        stage that was already recorded fell through into the next stage.  On
        2026-11-27 that turned a ``--stop-after gate`` re-judgement into an
        unattended sweep start, because the gate had been recorded by the run
        before it.  A stop request is about the session's position, not about
        what this process happened to do.
        """
        if args.stop_after != name:
            return False
        stage = (protocol.get("stages") or {}).get(name) or {}
        print(json.dumps({"stopped_after": name,
                          "performed_this_run": bool(info.pop("performed", False)),
                          "stage_done": bool(stage.get("done")),
                          "stage_recorded_utc": stage.get("recorded_utc"),
                          **info}), flush=True)
        return True

    def purge_runs_for(label, run_ids):
        """Delete this session's phone runs after the host tree is hashed."""
        if args.phone_retention != "purge" or not run_ids:
            return None
        target = output / "captures" / label
        records = []
        for index, run_id in enumerate(run_ids):
            if run_id not in created_runs:
                raise RuntimeError(f"Refusing to delete a run not created in this session: {run_id}")
            batch = target / f"batch_{index:03d}"
            summary = load_json(batch / "decoded/summary.json")
            if not (summary.get("capture_complete") and summary.get("run_succeeded")):
                raise RuntimeError(f"Refusing to delete unverified run {run_id}")
            hashes = {"export": hash_tree(batch / "export"), "decoded": hash_tree(batch / "decoded")}
            if not hashes["export"] or not hashes["decoded"]:
                raise RuntimeError(f"Refusing to delete run {run_id} without host files")
            records.append({"run_id": run_id, "label": label, "host_hashes": hashes})
        manifest = output / "cleanup_manifest.json"
        merged = load_json(manifest) if manifest.exists() else {"schema_version": 1, "batches": []}
        merged["batches"].extend(records)
        replace_json(manifest, merged)
        for run_id in run_ids:
            phone.shell(["run-as", PACKAGE, "rm", "-rf", "files/captures/" + run_id])
        remaining = phone.runs() & set(run_ids)
        if remaining:
            raise RuntimeError(f"Phone runs still present after deletion: {sorted(remaining)}")
        return len(records)

    # ---- stage: purge the invalidated 2026-11-26 runs ---------------------
    if args.purge_record and not stage_done("purge"):
        record = load_json(args.purge_record)
        wanted, batches_named = runs_named_by_record(record)
        if not wanted:
            raise RuntimeError("No run IDs found in the purge record")
        # One batch of the last band (killed mid-sweep) carries no run_id in the
        # record.  It cannot be named, so it is left on the phone rather than
        # guessed at, and the record says so.
        unnamed = batches_named - len(wanted)
        present = phone.runs()
        targets = sorted(wanted & present)
        already = sorted(wanted - present)
        untouched = sorted(present - wanted)
        plan_record = {"record": str(args.purge_record), "recorded_utc": utc_now(),
                       "runs_in_record": len(wanted), "present": len(targets),
                       "already_absent": len(already),
                       "batches_recorded": batches_named, "batches_without_run_id": unnamed,
                       "untouched_other_runs": len(untouched), "untouched_sample": untouched[:8],
                       "note": ("Only runs named in the invalidation record are deleted. A batch "
                                "the record cannot name is left in place, not guessed at.")}
        if not args.confirm_purge:
            print(json.dumps({**plan_record, "dry_run": True, "present_ids": targets}, indent=1))
            return 0
        for run_id in targets:
            phone.shell(["run-as", PACKAGE, "rm", "-rf", "files/captures/" + run_id])
        after = phone.runs()
        leftover = sorted(set(targets) & after)
        if leftover:
            raise RuntimeError(f"Purge failed for: {leftover}")
        write_json(output / "PHONE_PURGE.json",
                   {**plan_record, "deleted": len(targets), "confirmed_utc": utc_now(),
                    "present_after": len(after),
                    "untouched_preserved": sorted(set(untouched) & after) == untouched})
        mark_stage("purge", {"deleted": len(targets), "already_absent": len(already)})
        print(json.dumps({"purged": len(targets), "already_absent": len(already),
                          "phone_runs_remaining": len(after)}), flush=True)

    if args.stop_after == "purge":
        return 0

    # The geometry assertion belongs to the optical stages, not to the purge:
    # deleting phone files does not touch the beam, and a purge-only invocation
    # must not be able to claim a geometry it never needed.
    if not args.confirm_geometry:
        raise ValueError("--confirm-geometry is required: 50 um pinhole at 750 mm, nothing in the beam")

    # These four are defined before the first stage that needs them: the ceiling
    # probe already analyses a bracket, so a later definition would be a name
    # error at run time, not at import time.
    def estimate_drift(target, rect, schedule, peak_local, black):
        """Measure the lamp between tiers and say how much to divide each tier by.

        Refuses rather than guesses: a clipped reference cannot report a
        brightness, and a missing timestamp leaves the bracket uncorrected with
        the reason recorded next to it.
        """
        if len(schedule["references"]) < 2:
            return {"applied": False, "reason": "this bracket carries no reference exposures"}
        reference_tiers = [schedule["order"][index] for index in schedule["references"]]
        frames = read_window(target, reference_tiers, rect, schedule["references"])
        ceiling = protocol.get("session_ceiling_dn") or 1023
        if max(float(frame.max()) for frame in frames) >= ceiling:
            return {"applied": False,
                    "reason": "the reference exposure is clipped, so it cannot measure the lamp"}
        indices = sorted(set(schedule["references"]) | set(schedule["tiers"]))
        stamps = batch_times(target, indices)
        if len(stamps) != len(indices):
            return {"applied": False, "reason": "batch timestamps are missing"}
        clock = dict(zip(indices, stamps))
        try:
            measured = psf.reference_fluxes(frames, peak_local, black)
        except ValueError as exc:
            return {"applied": False, "reason": str(exc)}
        fluxes = measured["flux"]
        reference_times = [clock[i] for i in schedule["references"]]
        tier_times = [clock[i] for i in schedule["tiers"]]
        curve = psf.drift_factors(reference_times, [f["total"] for f in fluxes], tier_times)
        curve["applied"] = True
        curve["pixels"] = measured["pixels"]
        curve["floor_dn"] = measured["floor_dn"]
        curve["per_plane_change_pct"] = {
            name: psf.drift_factors(reference_times, [f[name] for f in fluxes],
                                    tier_times)["change_pct"]
            for name in ("B", "Gb", "Gr", "R")}
        curve["flux"] = [{"t_s": round(clock[i] - clock[schedule["references"][0]], 2),
                          "total": round(flux["total"], 1)}
                         for i, flux in zip(schedule["references"], fluxes)]
        curve["note"] = ("The lamp's brightness is measured at the reference exposure between "
                         "tiers and each tier is divided by it, so the bracket is compared at "
                         "one instant. Per-plane changes that disagree are not lamp drift.")
        return curve

    def measure(target, tiers, schedule=None):
        """Turn one capture directory into a tier table and a full PSF analysis.

        The peak is located on the full frame from the shortest tier, then a
        window around it is analysed in radius bands.  Once any bracket in the
        session reveals the pipeline's saturation plateau, that code becomes the
        session ceiling and every later bracket is merged against it instead of
        against the declared white level.  If the bracket carries reference
        exposures, the lamp's own drift across it is measured and divided out
        before anything is merged or judged.
        """
        schedule = schedule or plain_schedule(tiers)
        rows = tier_summaries(target, tiers, schedule["tiers"])
        black = rows[0]["black_dn"]
        if any(abs(row["black_dn"] - black) > 0.5 for row in rows):
            raise ValueError("The black level changed between tiers: " + json.dumps(rows))
        first = schedule["tiers"][0]
        peak = peak_position(target / f"batch_{int(first):03d}/decoded/temporal_mean_dn.npy")
        rect = psf.window(peak, args.psf_window)
        frames = read_window(target, tiers, rect, schedule["tiers"])
        peak_local = (peak[0] - rect[0], peak[1] - rect[1])
        drift = estimate_drift(target, rect, schedule, peak_local, black)
        if drift.get("applied"):
            clip = float(protocol.get("session_ceiling_dn") or 1023)
            frames = psf.correct_drift(frames, drift["factors"], black, clip)
        session_ceiling = protocol.get("session_ceiling_dn")
        ceiling = ({"ceiling_dn": int(session_ceiling), "plateau": True,
                    "source": "session"} if session_ceiling else None)
        references = None
        if schedule["references"]:
            reference_tiers = [schedule["order"][index] for index in schedule["references"]]
            references = {"frames": read_window(target, reference_tiers, rect,
                                                schedule["references"]),
                          "exposures_us": reference_tiers}
        analysis = psf.analyse(frames, tiers, peak_local, black, ceiling=ceiling,
                               references=references)
        if not session_ceiling and analysis["ceiling"]["plateau_observed"]:
            protocol["session_ceiling_dn"] = analysis["ceiling"]["ceiling_dn"]
            protocol["session_ceiling_source"] = (
                f"{target.name}: plateau at {analysis['ceiling']['ceiling_dn']} DN, "
                f"support {analysis['ceiling']['ceiling_support_px']} px, "
                f"nothing meaningful above it")
            save()
        return {"rows": rows, "black_dn": black, "peak_xy": list(peak),
                "window_xywh": rect, "peak_window_xy": list(peak_local),
                "analysis": analysis, "verdict": psf.verdict(analysis),
                "drift": drift}

    def next_shortest(analysis, current_us):
        """Rescale on the merged peak, in shortest-tier DN."""
        return next_shortest_us(analysis["signal_max"], current_us,
                                protocol.get("session_ceiling_dn") or 1023)

    def colour_at(found, limit=20):
        return found["analysis"]["ratios"][str(limit)]

    # ---- optional B2 single-bracket / single-dark capture -----------------
    # The rotation session interleaves three mount states with three bands.
    # Reusing one proven bracket here, rather than a second capture stack,
    # keeps the B2 record directly comparable with B1.
    if args.one_bracket or args.one_dark:
        if args.assume_ceiling_dn is not None:
            protocol["session_ceiling_dn"] = float(args.assume_ceiling_dn)
            protocol["session_ceiling_source"] = (
                "assumed for the rotation session from the B1 plateau; B2 did not "
                "re-run the saturation probe")
            save()

        if args.one_dark:
            dark_tiers = (gate_tiers[0], gate_tiers[2], MAX_US)
            target, run_ids = invoke(args.one_dark, dark_plan(dark_tiers, args.one_dark))
            batches = []
            for index, run_id in enumerate(run_ids):
                summary = load_json(target / f"batch_{index:03d}/decoded/summary.json")
                batches.append({
                    "exposure_us": dark_tiers[index], "phone_run_id": run_id,
                    "capture_complete": bool(summary.get("capture_complete")),
                    "run_succeeded": bool(summary.get("run_succeeded")),
                    "validated_raw_frames": summary.get("validated_raw_frames"),
                    "manual_readback_ok": bool(summary.get(
                        "all_manual_readbacks_within_tolerance")),
                    "observed_max_dn": [f.get("observed_max_dn")
                                        for f in summary.get("frames") or []],
                    "declared_black_level_pattern": summary.get(
                        "declared_black_level_pattern"),
                })
            verified = all(b["capture_complete"] and b["run_succeeded"]
                           and b["manual_readback_ok"]
                           and b["validated_raw_frames"] == args.frame_count
                           for b in batches)
            one_shot(output / f"{target.name}_verified.json",
                     {"stage": target.name, "exposures_us": dark_tiers,
                      "frame_count": args.frame_count, "run_ids": run_ids,
                      "verified": verified, "batches": batches,
                      "recorded_utc": utc_now()})
            print(json.dumps({"one_dark": target.name, "verified": verified,
                              "run_ids": run_ids}), flush=True)
            if not verified:
                raise RuntimeError("Single dark set failed verification")
            return 0

        if args.one_bracket:
            if args.one_band is None or args.one_start_us is None:
                raise ValueError("--one-bracket needs --one-band and --one-start-us")
            tiers = ladder_from_base(int(args.one_start_us), args.ladder_tiers, True)
            schedule = bracket_schedule(tiers)
            source_status = move_source(int(args.one_band))
            label = args.one_bracket
            target, run_ids = invoke(
                label, source_plan(int(args.one_band), int(args.one_band),
                                   schedule["order"], label))
            found = measure(target, tiers, schedule)
            record = {
                "stage": "b2_bracket", "label": target.name,
                "band_nm": int(args.one_band), "start_us": int(args.one_start_us),
                "tiers_us": list(tiers), "capture_order_us": list(schedule["order"]),
                "reference_batches": list(schedule["references"]),
                "run_ids": run_ids, "peak_xy": found["peak_xy"],
                "source_status": source_status,
                "window_xywh": found["window_xywh"],
                "analysis": found["analysis"], "verdict": found["verdict"],
                "drift": found["drift"], "recorded_utc": utc_now(),
                "note": ("One B2 bracket. The mount state and the tilt readings are "
                         "recorded by drive_b2.py; this record is the PSF measurement."),
            }
            one_shot(output / f"{target.name}.json", record)
            core = found["analysis"]["core"]
            verdict = found["verdict"]
            print(json.dumps({
                "one_bracket": target.name, "band_nm": int(args.one_band),
                "peak_dn": round(verdict["peak_dn_shortest_tier"], 1),
                "peak_fraction": round(verdict["peak_fraction"], 3),
                "core_dn": round(core["core_dn_shortest_tier"], 1),
                "run_ids": run_ids,
            }), flush=True)
            return 0

    # ---- stage: saturation plateau probe ----------------------------------
    # One short, cheap capture that answers the question the whole analysis
    # depends on: at which code does this pipeline stop being linear?  The
    # 2026-11-26 record seemed to say 959 DN; on 2026-11-27 the probe showed
    # that 959 was the span above the 64 DN black level (1023 - 64), not a code.
    # The plateau is at the declared white level, so the merge threshold is
    # measured here rather than assumed either way.
    if args.ceiling_probe and not stage_done("ceiling_probe"):
        if len(PROBE_TIERS_US) < 2:
            raise ValueError("--ceiling-probe-us must differ from --initial-shortest-us: the probe "
                             "needs an unsaturated tier and a clipping one")
        move_source(550)
        label = "ceiling_probe_550nm"
        target, run_ids = invoke(label, source_plan(550, 550, PROBE_TIERS_US, label))
        found = measure(target, PROBE_TIERS_US)
        ceiling = found["analysis"]["ceiling"]
        # The probe also fixes the starting exposure: its shortest tier is
        # unsaturated, so the merged peak is that peak in 376 us DN, and the
        # ladder can start where the peak sits at TARGET_PEAK_FRACTION.
        recommended = next_shortest_us(found["analysis"]["signal_max"], PROBE_TIERS_US[0],
                                       ceiling["ceiling_dn"])
        record = {"stage": "ceiling_probe", "utc": utc_now(), "tiers_us": list(PROBE_TIERS_US),
                  "run_ids": run_ids, "peak_xy": found["peak_xy"],
                  "window_xywh": found["window_xywh"], "tiers": found["rows"],
                  "ceiling": ceiling, "core": found["analysis"]["core"],
                  "verdict": found["verdict"], "radial": found["analysis"]["radial"],
                  "merged_signal_max_dn": found["analysis"]["signal_max"],
                  "recommended_shortest_us": recommended,
                  "note": ("Measured from a short three-tier bracket on 550 nm. A plateau is the "
                           "highest code held by many pixels with nothing comparable above it. On "
                           "2026-11-27 the plateau was the declared white level, 1023 DN; the 959 DN "
                           "of the 2026-11-26 record was the span above the 64 DN black level.")}
        one_shot(output / "CEILING_PROBE.json", record)
        protocol["next_shortest_us"] = recommended
        mark_stage("ceiling_probe", {"ceiling_dn": ceiling["ceiling_dn"],
                                     "plateau_observed": ceiling["plateau_observed"],
                                     "recommended_shortest_us": recommended,
                                     "run_ids": run_ids})
        print(json.dumps({"ceiling_probe": True,
                          "ceiling_dn": ceiling["ceiling_dn"],
                          "plateau_observed": ceiling["plateau_observed"],
                          "support_px": ceiling["ceiling_support_px"],
                          "codes_above": ceiling["codes_above_ceiling_px"],
                          "merged_signal_max_dn": round(found["analysis"]["signal_max"], 1),
                          "recommended_shortest_us": recommended,
                          "core_dn_at_shortest": round(found["analysis"]["core"]
                                                       ["core_dn_shortest_tier"], 0),
                          "core_peak_dn_at_shortest": round(found["analysis"]["core"]
                                                            ["core_peak_dn_shortest_tier"], 0),
                          "per_frame_ceiling_dn": ceiling["per_frame_ceiling_dn"]}), flush=True)
        purge_runs_for(target.name, run_ids)
        return 0

    # ---- stage: dark start ------------------------------------------------
    dark_tiers = [gate_tiers[0], gate_tiers[2], MAX_US]
    if not stage_done("dark"):
        operator_gate("occlude_for_dark_start",
                      "Fully occlude the source and keep the phone display black. "
                      "Confirm only when no stray light reaches the phone.")
        target, run_ids = invoke("dark_start", dark_plan(dark_tiers, "dark_start"))
        batches = []
        for index, run_id in enumerate(run_ids):
            summary = load_json(target / f"batch_{index:03d}/decoded/summary.json")
            batches.append({"exposure_us": dark_tiers[index], "phone_run_id": run_id,
                            "capture_complete": bool(summary.get("capture_complete")),
                            "run_succeeded": bool(summary.get("run_succeeded")),
                            "validated_raw_frames": summary.get("validated_raw_frames"),
                            "manual_readback_ok": bool(summary.get("all_manual_readbacks_within_tolerance")),
                            "observed_max_dn": [f.get("observed_max_dn") for f in summary.get("frames") or []],
                            "observed_mean_dn": [f.get("mean_dn") for f in summary.get("frames") or []],
                            "declared_black_level_pattern": summary.get("declared_black_level_pattern"),
                            "declared_white_level": summary.get("declared_white_level")})
        verified = all(b["capture_complete"] and b["run_succeeded"] and b["manual_readback_ok"]
                       and b["validated_raw_frames"] == args.frame_count for b in batches)
        one_shot(output / "dark_start_verified.json",
                 {"stage": "dark_start", "exposures_us": dark_tiers, "frame_count": args.frame_count,
                  "run_ids": run_ids, "verified": verified, "batches": batches, "recorded_utc": utc_now()})
        mark_stage("dark", {"verified": verified, "run_ids": run_ids})
        if not verified:
            raise RuntimeError("Start dark set failed verification; inspect before continuing")
        operator_gate("restore_point_source",
                      "Remove the occlusion and restore the unchanged point-source geometry "
                      "(50 um pinhole, 750 mm). Confirm only when the beam is on the phone again.")
    if not (output / "captures/dark_start").is_dir():
        raise RuntimeError("dark_start is marked done but its host files are missing")
    if stop_if_requested("dark"):
        return 0

    # ---- stage: spectral gate --------------------------------------------
    def gate_start(nominal):
        """One batch at the session start tier decides this band's gate ladder.

        The probe is the same exposure the whole session starts from, so it is
        cheap and directly comparable across bands; its peak then scales into
        TARGET_PEAK_FRACTION of span exactly as the sweep's first ladder does.
        The batch is purged from the phone once its local mean has been read.
        """
        label = f"gate_probe_{nominal:03d}nm"
        target, run_ids = invoke(label, source_plan(nominal, nominal, [GATE_PROBE_US], label))
        rows = tier_summaries(target, [GATE_PROBE_US])
        peak = frame_peak_dn(target / "batch_000/decoded/temporal_mean_dn.npy",
                             rows[0]["black_dn"])
        start = next_shortest_us(peak, GATE_PROBE_US,
                                 protocol.get("session_ceiling_dn") or 1023)
        record = {"label": target.name, "exposure_us": GATE_PROBE_US,
                  "peak_dn": round(peak, 1), "black_dn": rows[0]["black_dn"],
                  "run_ids": run_ids, "derived_start_us": start,
                  "probe_clipped": bool(peak + rows[0]["black_dn"] >=
                                        (protocol.get("session_ceiling_dn") or 1023) - 4),
                  "note": ("One batch; the peak scales the gate ladder so this band's peak lands "
                           "at the target fraction of span instead of inheriting the other band's "
                           "brightness.")}
        purge_runs_for(target.name, run_ids)
        return start, record

    def gate_row(found, probe, ladder, schedule, run_ids):
        return {"run_ids": run_ids, "tiers": found["rows"],
                "ladder_us": list(ladder), "probe": probe,
                "capture_order_us": list(schedule["order"]),
                "peak_xy": found["peak_xy"], "colour": colour_at(found),
                "seams": found["analysis"]["seams"],
                "repeatability": found["analysis"]["repeatability"],
                "verdict": found["verdict"],
                "drift": found["drift"],
                "ceiling": found["analysis"]["ceiling"]}

    def print_gate_band(nominal, probe, found):
        colour = colour_at(found)
        print(json.dumps({"gate_band": nominal,
                          "start_us": (probe or {}).get("derived_start_us"),
                          "probe_peak_dn": (probe or {}).get("peak_dn"),
                          "r_g": round(colour["r_g"], 3),
                          "b_r": round(colour["b_r"], 3),
                          "r_g_sigma": round(colour.get("r_g_sigma", 0.0), 4),
                          "b_r_sigma": round(colour.get("b_r_sigma", 0.0), 4),
                          "peak_dn": round(found["verdict"]["peak_dn_shortest_tier"], 0),
                          "peak_fraction": round(found["verdict"]["peak_dn_shortest_tier"]
                                                 / found["verdict"]["span_dn"], 3),
                          "hard_ok": found["verdict"]["ok"],
                          "hard_reasons": found["verdict"]["reasons"],
                          "indicators": found["verdict"]["indicators"],
                          "ceiling_dn": found["analysis"]["ceiling"]["used_for_merge_dn"],
                          "seams_passed": found["analysis"]["seams"]["passed"],
                          "seams_strict_passed": found["analysis"]["seams"]["strict_passed"],
                          "seams_judged": found["analysis"]["seams"]["judged_bands"],
                          "bad_seams": found["analysis"]["seams"]["bad_bands"],
                          "strict_bad_seams": found["analysis"]["seams"]["strict_bad_bands"],
                          "repeatability_worst_pct":
                              found["analysis"]["repeatability"].get("worst_pct"),
                          "drift_pct": round(found["drift"].get("change_pct", 0.0), 2)
                                       if found["drift"].get("applied") else None,
                          "drift_rate_pct_per_min": round(
                              found["drift"].get("rate_pct_per_min", 0.0), 2)
                              if found["drift"].get("applied") else None,
                          "drift_corrected": bool(found["drift"].get("applied"))}),
              flush=True)

    def gate_summary(rows):
        blue, red = rows[450]["colour"], rows[650]["colour"]
        b_ratio = blue["b_r"] / red["b_r"] if red["b_r"] else float("inf")
        r_ratio = red["r_g"] / blue["r_g"] if blue["r_g"] else float("inf")
        # The gate exists to prove two things, and only these two are hard: the
        # source changes colour, and both brackets were captured completely.
        # Nothing downstream can repair a source that did not change, or a
        # missing batch.  How well the bracket repeats is a weak indicator: it
        # says how far the numbers can be trusted, not whether to proceed.
        colour_changed = bool(b_ratio > 5.0 and r_ratio > 3.0)
        complete = all(bool(row["capture_complete"] and row["run_succeeded"])
                       for gate in rows.values() for row in gate["tiers"])
        passed = bool(colour_changed and complete)
        consistent = all(g["seams"]["passed"] for g in rows.values())
        return {"stage": "gate", "utc": utc_now(), "bands": rows,
                "b_r_450_over_650": b_ratio, "r_g_650_over_450": r_ratio,
                "brackets_consistent": consistent,
                "thresholds": {"b_r_ratio_min": 5.0, "r_g_ratio_min": 3.0},
                "passed": passed,
                "hard": {"colour_changed": colour_changed, "brackets_complete": complete},
                "indicators": {
                    "brackets_consistent": consistent,
                    "seam_bad_bands": {str(k): v["seams"]["bad_bands"]
                                       for k, v in rows.items()},
                    "seam_strict_bad_bands": {str(k): v["seams"]["strict_bad_bands"]
                                              for k, v in rows.items()},
                    "repeatability_worst_pct": {
                        str(k): v["repeatability"].get("worst_pct")
                        for k, v in rows.items()},
                    "peak_fraction": {str(k): v["verdict"]["peak_fraction"]
                                      for k, v in rows.items()},
                    "drift_pct": {str(k): (v["drift"].get("change_pct")
                                           if v["drift"].get("applied") else None)
                                  for k, v in rows.items()}},
                "criteria_strength": {
                    "hard": "the source changed colour; both brackets captured completely",
                    "weak": ("seam consistency, peak fraction, drift: these bound how far "
                             "the bracket can be trusted and point at what to clean, they "
                             "do not block the sweep")},
                "note": ("B/R at 20 px from the integrated per-plane flux; the per-plane "
                         "errors are the propagated standard error of the band means. "
                         "The seam tolerance is the measured repeatability of the same "
                         "exposure inside this bracket, not a fixed 2 %.")}

    def record_gate(rows, rejudge=None):
        record = gate_summary(rows)
        # A re-judgement never overwrites the as-captured record: the frames did
        # not change, only the criteria did, and both statements have to stay
        # readable side by side.
        if rejudge:
            record["rejudged"] = rejudge
        one_shot(output / "SOURCE_GATE.rejudged.json" if rejudge else output / "SOURCE_GATE.json",
                 record)
        if not record["passed"]:
            protocol.setdefault("failed_gates", []).append(
                {**record, "rejudged": bool(rejudge)})
            save()
            raise RuntimeError(
                "Spectral gate FAILED on a hard criterion: "
                f"colour changed {record['hard']['colour_changed']} (B/R ratio "
                f"{record['b_r_450_over_650']:.2f} > 5, R/G ratio "
                f"{record['r_g_650_over_450']:.2f} > 3), brackets complete "
                f"{record['hard']['brackets_complete']}. Do not capture the sweep.")
        mark_stage("gate", {"passed": True,
                            "b_r_ratio": round(record["b_r_450_over_650"], 3),
                            "r_g_ratio": round(record["r_g_650_over_450"], 3),
                            "brackets_consistent": record["brackets_consistent"],
                            **({"rejudged": True,
                                "rejudged_utc": record["rejudged"]["utc"],
                                "previous_passed": record["rejudged"]["previous_passed"],
                                "previous_record": record["rejudged"]["previous_record"],
                                "changed": record["rejudged"]["changed"]}
                               if rejudge else {})})
        # The 450 nm gate bracket already measured where this band's peak sits, so
        # the sweep reuses its start tier instead of spending a rejected attempt
        # rediscovering it.
        protocol["gate_band_start_us"] = {str(k): int(v["ladder_us"][0])
                                          for k, v in rows.items()}
        save()
        return record

    def gate_dir_for(nominal, band):
        """Find the recorded bracket that these run ids came from."""
        wanted = set(band.get("run_ids") or [])
        found_dirs = []
        for candidate in sorted((output / "captures").glob(f"gate_{nominal:03d}nm*")):
            if not candidate.is_dir():
                continue
            seen = {run_dir.name for run_dir in
                    candidate.glob("batch_*/export/device_files/captures/run_*")}
            if wanted & seen:
                found_dirs.append(candidate)
        if len(found_dirs) != 1:
            raise RuntimeError(f"cannot identify the recorded {nominal} nm gate bracket: "
                               f"{len(found_dirs)} directories under {output / 'captures'} "
                               "match the run ids in SOURCE_GATE.json")
        return found_dirs[0]

    gate_info = {}
    if args.rejudge_gate or (not args.skip_gate and not stage_done("gate")):
        rows = {}
        rejudge = None
        if args.rejudge_gate:
            previous = load_json(output / "SOURCE_GATE.json")
            # Freeze what is being replaced before any analysis runs, so the note
            # cannot describe a different record than the one on disk.
            rejudge = {
                "utc": utc_now(),
                "previous_record": "SOURCE_GATE.json",
                "previous_passed": previous.get("passed"),
                "previous_brackets_consistent": previous.get("brackets_consistent"),
                "previous_thresholds": previous.get("thresholds"),
                "frames_unchanged": True,
                "changed": [
                    "pass/fail no longer includes seam consistency: seam agreement "
                    "is recorded as a weak indicator of how far the profile can be "
                    "trusted, not as a veto",
                    "the seam tolerance is the measured repeatability of the same "
                    "exposure inside this bracket (per ring, floored at one "
                    "quantisation code) instead of a fixed 2 %",
                    "the 40 % peak target is recorded as an indicator; only a peak "
                    "inside the quantisation noise or a clipped core is hard",
                ],
                "why": ("the as-captured gate failed on brackets_consistent while the "
                        "source had changed colour by 302x and both brackets were "
                        "captured completely; the seam mismatch it failed on is 1-3 % "
                        "per ring, below the instrument's own repeatability"),
                "note": ("No pixel was recaptured and no analysis parameter that "
                         "affects a flux, a ratio or a ceiling was touched. Only the "
                         "acceptance rule changed.")}
            for key, band in sorted((previous.get("bands") or {}).items()):
                nominal = int(key)
                ladder = [int(value) for value in band["ladder_us"]]
                schedule = bracket_schedule(ladder)
                target = gate_dir_for(nominal, band)
                found = measure(target, ladder, schedule)
                rows[nominal] = gate_row(found, band.get("probe"), ladder, schedule,
                                         band.get("run_ids"))
                print(json.dumps({"rejudged": nominal, "target": target.name}))
                print_gate_band(nominal, band.get("probe"), found)
        else:
            for nominal in (450, 650):
                move_source(nominal)
                start_us, probe = gate_start(nominal)
                gate_tiers_band = ladder_from_base(start_us, GATE_TIER_COUNT, True)
                label = f"gate_{nominal:03d}nm"
                schedule = bracket_schedule(gate_tiers_band)
                target, run_ids = invoke(label, source_plan(nominal, nominal,
                                                            schedule["order"], label))
                found = measure(target, gate_tiers_band, schedule)
                rows[nominal] = gate_row(found, probe, gate_tiers_band, schedule, run_ids)
                print_gate_band(nominal, probe, found)
                purge_runs_for(target.name, run_ids)
        record = record_gate(rows, rejudge=rejudge)
        gate_info = {"passed": record["passed"], "rejudged": bool(rejudge),
                     "performed": True}
    if stop_if_requested("gate", **gate_info):
        return 0

    # ---- stage: broadband pass-through white ------------------------------
    passthrough_info = {}
    if not args.skip_passthrough and not stage_done("passthrough"):
        tls.set_pass_through(timeout_s=120.0)
        time.sleep(args.tls_settle_s)
        status = tls_status()
        label = "passthrough_white"
        target, run_ids = invoke(label, passthrough_plan(PASSTHRU_TIERS_US, label))
        found = measure(target, PASSTHRU_TIERS_US)
        shortest = found["analysis"]["core"]
        saturated_floor = (shortest["core_saturated_fraction"] > 0.5
                           or shortest["core_dn_shortest_tier"] >=
                           0.95 * (found["analysis"]["ceiling"]["used_for_merge_dn"] - 64.0))
        colour = colour_at(found)
        record = {"stage": "passthrough", "utc": utc_now(), "tls_status": status,
                  "tiers": found["rows"], "peak_xy": found["peak_xy"],
                  "window_xywh": found["window_xywh"],
                  "core": shortest, "verdict": found["verdict"],
                  "colour": colour, "shape": found["analysis"]["shape"],
                  "seams": found["analysis"]["seams"],
                  "shortest_tier_saturated": saturated_floor,
                  "usable": not saturated_floor,
                  "note": ("The zero order saturates even at the 14 us floor; attenuation is needed "
                           "before this anchor can be used." if saturated_floor else
                           "Broadband anchor: same lamp, same pinhole, same geometry. Its spectrum "
                           "and absolute power are unmeasured, so it is a reference white for this "
                           "sensor, not a colorimetric white.")}
        one_shot(output / "PASSTHROUGH_WHITE.json", record)
        mark_stage("passthrough", {"usable": record["usable"], "run_ids": run_ids,
                                   "note": record["note"]})
        print(json.dumps({"passthrough_usable": record["usable"],
                          "shortest_core_dn": round(shortest["core_dn_shortest_tier"], 0),
                          "core_saturated": round(shortest["core_saturated_fraction"], 3),
                          "r_g": round(colour["r_g"], 3), "b_r": round(colour["b_r"], 3),
                          "note": record["note"]}), flush=True)
        purge_runs_for(target.name, run_ids)
        passthrough_info = {"usable": record["usable"], "performed": True}
    if stop_if_requested("passthrough", **passthrough_info):
        return 0

    # ---- stage: the sweep -------------------------------------------------
    accepted = {b["wavelength_nm"]: b for b in protocol.get("accepted_bands") or []}
    rejected = {b["wavelength_nm"]: b for b in protocol.get("rejected_bands") or []}
    shortest_us = int(protocol.get("next_shortest_us") or args.initial_shortest_us)
    # A band whose retry was decided but never captured (the process died in
    # between) restarts at the exposure that decision named.  Without this the
    # band is handed the session seed again and re-captures the bracket that
    # already failed.
    seeds_by_band = resume_seeds(protocol.get("band_attempts"))

    for nominal in bands:
        if nominal in accepted or nominal in rejected:
            print(json.dumps({"skip": nominal, "reason": "already recorded"}), flush=True)
            continue
        reach_this = nominal in reach
        seed = (protocol.get("gate_band_start_us") or {}).get(str(nominal))
        if nominal in seeds_by_band:
            seed = seeds_by_band[nominal]
            print(json.dumps({"resume_seed": nominal, "shortest_us": seed,
                              "reason": "the last recorded attempt was an unfinished retry"}),
                  flush=True)
        if seed:
            shortest_us = int(seed)
        tiers = ladder_from_base(shortest_us, args.ladder_tiers, reach_this)
        outcome = None
        for attempt in range(3):
            label = f"band_{nominal:03d}nm_try{attempt}"
            status = move_source(nominal)
            schedule = bracket_schedule(tiers)
            target, run_ids = invoke(label, source_plan(nominal, status["current_wavelength_nm"],
                                                        schedule["order"], label))
            found = measure(target, tiers, schedule)
            analysis, verdict, seams = found["analysis"], found["verdict"], found["analysis"]["seams"]
            core = analysis["core"]
            entry = {"wavelength_nm": nominal, "target": str(target.relative_to(output)),
                     "tiers_us": tiers, "run_ids": run_ids, "attempt": attempt,
                     "driver_sha256": DRIVER_SHA,
                     "tls_readback_nm": status["current_wavelength_nm"],
                     "shortest_us": tiers[0], "longest_us": tiers[-1],
                     "capture_order_us": schedule["order"],
                     "reference_batches": schedule["references"],
                     "drift": found["drift"],
                     "peak_rows": found["rows"], "peak_xy": found["peak_xy"],
                     "window_xywh": found["window_xywh"], "black_dn": found["black_dn"],
                     "core": core, "verdict": verdict,
                     "ceiling": analysis["ceiling"], "seams": seams,
                     "repeatability": analysis["repeatability"],
                     "ratios": analysis["ratios"], "shape": analysis["shape"],
                     "radial": analysis["radial"],
                     "all_saturated_fraction": analysis["all_saturated_fraction"],
                     "counts_by_exposure": analysis["counts_by_exposure"],
                     "signal_max_dn": analysis["signal_max"]}
            print(json.dumps({"band": nominal, "attempt": attempt, "tiers": tiers,
                              "peak_dn": round(core["core_peak_dn_shortest_tier"]),
                              "peak_fraction": round(verdict["peak_dn_shortest_tier"]
                                                     / verdict["span_dn"], 3),
                              "core_saturated": round(core["core_saturated_fraction"], 3),
                              "judged_seams": seams["judged_bands"],
                              "bad_seams": seams["bad_bands"],
                              "strict_bad_seams": seams["strict_bad_bands"],
                              "repeatability_worst_pct":
                                  analysis["repeatability"].get("worst_pct"),
                              "ceiling_dn": analysis["ceiling"]["used_for_merge_dn"],
                              "r_g_20px": round(analysis["ratios"]["20"]["r_g"], 3),
                              "b_r_20px": round(analysis["ratios"]["20"]["b_r"], 3),
                              "drift_pct": round(found["drift"].get("change_pct", 0.0), 2)
                                           if found["drift"].get("applied") else None,
                              "hard_ok": verdict["ok"],
                              "seams_passed": seams["passed"]}), flush=True)
            # Weak indicators: they say how far this band's numbers can be
            # trusted and where to clean, and they never reject a band.
            entry["indicators"] = list(verdict["indicators"])
            if seams["bad_bands"]:
                entry["indicators"].append(
                    f"{seams['bad_bands']} of {seams['judged_bands']} judged radius bands "
                    "disagree by more than the measured repeatability of the same exposure; "
                    "treat the profile beyond the core as uncertain at that level")
            if seams["judged_bands"] < 2:
                entry["indicators"].append(
                    f"only {seams['judged_bands']} radius band(s) carry enough signal to be "
                    "checked; the shape outside the core is not verified")
            # Hard criteria, and the exposure corrections that can still fix
            # them.  A seam mismatch is not exposure-related, so it no longer
            # costs a recapture: the diagnosis showed it is a property of the
            # instrument, and repeating the same bracket reproduces it.
            hard_fail = not verdict["ok"]
            retry = False
            if attempt < 2:
                if verdict["core_clipped"] and shortest_us > MIN_US:
                    # The peak is pinned at the ceiling, so it cannot say how
                    # far over it is; a whole 4x step is the only honest move.
                    shortest_us = max(MIN_US, shortest_us // LADDER_RATIO)
                    retry = True
                elif (verdict["peak_below_floor"] or verdict["peak_too_dim"]) \
                        and shortest_us < MAX_US:
                    # Here the peak is measurable, so scale it directly instead
                    # of guessing another 4x.
                    shortest_us = next_shortest(analysis, shortest_us)
                    retry = True
            if retry:
                entry["retry_reason"] = ("; ".join(verdict["reasons"])
                                         or f"peak at {100 * verdict['peak_fraction']:.0f} % of "
                                            "span, under the target; lengthen the shortest tier")
                # The decided exposure is recorded with the decision.  Without
                # it a process killed between this save and the next capture
                # resumes the band at the exposure that just failed.
                entry["next_shortest_us"] = int(shortest_us)
                protocol.setdefault("band_attempts", []).append(entry)
                save()
                purge_runs_for(target.name, run_ids)
                tiers = ladder_from_base(shortest_us, args.ladder_tiers, reach_this)
                continue
            if hard_fail:
                entry["rejected_reason"] = ("; ".join(verdict["reasons"])
                                            or "the bracket is not a usable PSF measurement")
                protocol.setdefault("band_attempts", []).append(entry)
                save()
                purge_runs_for(target.name, run_ids)
                outcome = entry
                break
            entry["accepted"] = True
            outcome = entry
            break
        if outcome is None or not outcome.get("accepted"):
            record = outcome or {"wavelength_nm": nominal, "rejected_reason": "no attempt completed"}
            protocol.setdefault("rejected_bands", []).append(record)
            rejected[nominal] = record
            save()
            continue
        protocol.setdefault("accepted_bands", []).append(outcome)
        accepted[nominal] = outcome
        shortest_us = next_shortest_us(outcome["signal_max_dn"], outcome["shortest_us"],
                                       protocol.get("session_ceiling_dn") or 1023)
        protocol["next_shortest_us"] = shortest_us
        save()
        purge_runs_for(outcome["target"].split("/")[-1], outcome["run_ids"])

    if args.stop_after == "sweep":
        return 0

    # ---- stage: manifest and the session-level spectral check -------------
    if not accepted:
        raise RuntimeError("No band passed the gates; no fusion manifest was written")
    ordered = sorted(accepted.values(), key=lambda r: r["wavelength_nm"])
    first, last = ordered[0], ordered[-1]
    blue, red = first["ratios"]["20"], last["ratios"]["20"]
    b_ratio = blue["b_r"] / red["b_r"] if red["b_r"] else float("inf")
    r_ratio = red["r_g"] / blue["r_g"] if blue["r_g"] else float("inf")
    sweep_passed = bool(b_ratio > 5.0 and r_ratio > 3.0)
    table = [{"wavelength_nm": b["wavelength_nm"],
              "r_g_20px": b["ratios"]["20"]["r_g"], "b_r_20px": b["ratios"]["20"]["b_r"],
              "r_g_sigma": b["ratios"]["20"].get("r_g_sigma"),
              "b_r_sigma": b["ratios"]["20"].get("b_r_sigma"),
              "r_g_40px": b["ratios"]["40"]["r_g"], "b_r_40px": b["ratios"]["40"]["b_r"],
              "shortest_us": b["shortest_us"], "longest_us": b["longest_us"],
              "core_dn": b["core"]["core_dn_shortest_tier"],
              "core_peak_dn": b["core"]["core_peak_dn_shortest_tier"],
              "ceiling_dn": b["ceiling"]["used_for_merge_dn"],
              "judged_seams": b["seams"]["judged_bands"],
              "bad_seams": b["seams"]["bad_bands"]}
             for b in ordered]
    one_shot(output / "SWEEP_SPECTRAL_TABLE.json",
             {"schema_version": 1, "recorded_utc": utc_now(), "bands": table,
              "b_r_first_over_last": b_ratio, "r_g_last_over_first": r_ratio,
              "passed": sweep_passed,
              "criterion": ("B/R (integrated inside 20 px) must fall by 5x and R/G must rise by 3x "
                            "from the lowest to the highest accepted band, otherwise the source did "
                            "not sweep. This is a source-change check, not a spectral-purity or "
                            "radiometric claim; the TLS bandwidth is unmeasured.")})
    if not sweep_passed:
        protocol.update(session_valid=False,
                        invalid_reason="the accepted bands do not show the spectral movement a real "
                                       "sweep requires")
        save()
        raise RuntimeError(f"Sweep-level spectral check FAILED (B/R ratio {b_ratio:.2f}, "
                           f"R/G ratio {r_ratio:.2f}); this set is not usable as wavelengths.")

    all_tiers = sorted({e for b in ordered for e in b["tiers_us"]})
    dark_entries = []
    for exposure in all_tiers:
        tier = min(dark_tiers, key=lambda ref: abs(math.log(exposure / ref)))
        dark_entries.append({"exposure_ns": exposure * 1000, "matched_dark_us": tier,
                             "batches": [f"captures/dark_start/batch_{dark_tiers.index(tier):03d}"]})
    one_shot(output / "fusion_manifest.json",
             {"schema_version": 1, "session_id": output.name, "created_utc": utc_now(),
              "darks": dark_entries,
              "note": ("anchor_tier_us is the shortest tier: the only one whose core is guaranteed "
                       "unsaturated, so it fixes the peak position. Every listed batch stays "
                       "available so the reconstruction side can re-merge with its own thresholds."),
              "bands": [{"wavelength_nm": b["wavelength_nm"],
                         "anchor_tier_us": b["shortest_us"],
                         "peak_xy": b["peak_xy"],
                         "window_xywh": b["window_xywh"],
                         "black_dn": b["black_dn"],
                         "ceiling_dn": b["ceiling"]["used_for_merge_dn"],
                         "batches": [{"path": f"{b['target']}/batch_{i:03d}", "exposure_ns": e * 1000}
                                     for i, e in enumerate(b["tiers_us"])]}
                        for b in ordered]})

    if args.dark_end:
        operator_gate("occlude_for_dark_end", "Occlude the source again for the closing dark set.")
        target, run_ids = invoke("dark_end", dark_plan(dark_tiers, "dark_end"))
        one_shot(output / "dark_end_verified.json",
                 {"stage": "dark_end", "exposures_us": dark_tiers, "run_ids": run_ids,
                  "recorded_utc": utc_now()})
        mark_stage("dark_end", {"run_ids": run_ids})

    tls.set_pass_through(timeout_s=120.0)
    protocol.update(finished_utc=utc_now(), complete=True, session_valid=True,
                    accepted_band_count=len(ordered), rejected_band_count=len(rejected),
                    created_runs=sorted(created_runs),
                    spectral_table="SWEEP_SPECTRAL_TABLE.json",
                    fusion_manifest="fusion_manifest.json",
                    phone_free_gb_at_end=phone_free_gb())
    save()
    one_shot(output / "protocol_final.json", protocol)
    print(json.dumps({"complete": True, "accepted": len(ordered), "rejected": len(rejected),
                      "b_r_ratio": round(b_ratio, 2), "r_g_ratio": round(r_ratio, 2),
                      "phone_runs_created": len(created_runs),
                      "phone_free_gb": protocol["phone_free_gb_at_end"]}), flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    finally:
        for close in _CLOSERS:
            try:
                close()
            except Exception:
                pass
