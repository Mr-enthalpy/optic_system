"""Drive the B2 rotation session: three mount states x three bands.

The session is deliberately separate from B1.  It captures one bracket at each
of three mount states (P1 -> P2 -> P3), with the tilt read before, during and
after every bracket.  P1 and P3 should be close; the actual difference is
measured, not assumed.  The PSF brackets themselves are captured by the
proven B1 driver in ``--one-bracket`` mode, so the two sessions share the same
ladder, drift references and analysis.

The wrapper owns only the things B2 adds: operator prompts, the tilt record,
and the mapping from mount state to band.  It has no camera SDK calls.
"""
from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import read_tilt as tilt  # noqa: E402
import rotation_analysis as ra  # noqa: E402

BANDS = (450, 550, 650)
# B1 measured the 550 nm start; 450 and 650 need five tiers to reach the cap.
LADDER_TIERS = {450: 5, 550: 6, 650: 5}
STATES = ("P1", "P2", "P3")


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_starts(text):
    out = {}
    for item in text.split(","):
        name, value = item.split(":", 1)
        out[int(name)] = int(value)
    return out


def append_jsonl(path, record):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(json_safe(record), ensure_ascii=False,
                                allow_nan=False) + "\n")


def json_safe(value):
    if isinstance(value, dict):
        return {key: json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def write_once(path, value):
    if path.exists():
        raise SystemExit(f"refusing to overwrite existing {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")


def stable_history(history, spread_deg=0.05, change_deg=0.10):
    """Whether the newest samples are flat enough to start a bracket.

    The holder can creep toward vertical after it is set.  Waiting for a fixed
    number of seconds is not the same as waiting for the motion to stop, so the
    criterion is the observed spread and the change across the window.
    """
    if len(history) < 5:
        return False
    recent = history[-5:]
    rolls = [row["roll_deg"] for row in recent]
    tilts = [row["tilt_deg"] for row in recent]
    if max(rolls) - min(rolls) > spread_deg:
        return False
    if max(tilts) - min(tilts) > spread_deg:
        return False
    if abs(recent[-1]["roll_deg"] - history[0]["roll_deg"]) > change_deg:
        return False
    if abs(recent[-1]["tilt_deg"] - history[0]["tilt_deg"]) > change_deg:
        return False
    return True


def read_tilt(adb, serial, label, lock=None):
    if lock is None:
        record = tilt.reading(label, adb=str(adb), serial=serial)
    else:
        with lock:
            record = tilt.reading(label, adb=str(adb), serial=serial)
    record["monotonic_s"] = round(time.monotonic(), 3)
    record["recorded_utc"] = utc_now()
    return record


def wait_for_settle(adb, serial, log_path, label, timeout_s, gap_s,
                    spread_deg, change_deg):
    """Wait for the mount to stop moving, not for a fixed delay."""
    print(f"waiting for {label} to settle (timeout {timeout_s:.0f} s)...", flush=True)
    deadline = time.time() + timeout_s
    history = []
    while time.time() < deadline:
        try:
            record = read_tilt(adb, serial, label)
        except Exception as exc:  # adb can hiccup; retry rather than abort the session
            print(f"  tilt read failed: {exc}", flush=True)
            time.sleep(max(1.0, gap_s))
            continue
        append_jsonl(log_path, record)
        if record.get("buffer_mixed"):
            # The rolling sensor buffer still holds pre-rotation samples.
            time.sleep(max(4.0, gap_s))
            continue
        history.append(record)
        print(f"  roll={record['roll_deg']:+7.3f}  tilt={record['tilt_deg']:+6.2f}  "
              f"tail_spread={record['roll_spread_tail_deg']:.3f}", flush=True)
        if stable_history(history, spread_deg, change_deg):
            return record
        time.sleep(max(1.0, gap_s))
    raise SystemExit(f"{label} did not settle within {timeout_s:.0f} s")


class TiltLogger:
    """Poll the accelerometer while a bracket is being captured.

    A slow rebound can move the spot between tiers even when the start and end
    readings look acceptable.  The logger records the whole bracket; the PSF
    analysis independently checks the per-batch centre trajectory.
    """

    def __init__(self, adb, serial, log_path, context, poll_s=5.0):
        self.adb = adb
        self.serial = serial
        self.log_path = log_path
        self.context = context
        self.poll_s = poll_s
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        while not self._stop.is_set():
            try:
                record = read_tilt(self.adb, self.serial, self.context, self._lock)
                record["context"] = self.context
                append_jsonl(self.log_path, record)
            except Exception as exc:
                append_jsonl(self.log_path, {"recorded_utc": utc_now(),
                                             "context": self.context,
                                             "error": str(exc)})
            self._stop.wait(self.poll_s)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, exc_type, exc, tb):
        self._stop.set()
        self._thread.join(timeout=10.0)
        return False


def driver_command(args, label, band=None, start_us=None, dark=False):
    command = [
        args.python, str(args.driver),
        "--repo", str(args.repo),
        "--adb", str(args.adb),
        "--output", str(args.output),
        "--confirm-geometry",
        "--phone-retention", "keep",
        "--psf-window", str(args.psf_window),
        "--expected-app-version", args.expected_app_version,
        "--min-host-free-gb", str(args.min_host_free_gb),
    ]
    if args.serial:
        command += ["--serial", args.serial]
    if args.camera_id:
        command += ["--camera-id", args.camera_id]
    if dark:
        command += ["--bands", "450:650:100", "--one-dark", label]
    else:
        command += ["--bands", str(band),
                    "--one-bracket", label,
                    "--one-band", str(band),
                    "--one-start-us", str(start_us),
                    "--ladder-tiers", str(LADDER_TIERS[band]),
                    "--assume-ceiling-dn", str(args.assume_ceiling_dn)]
    return command


def ask(prompt):
    print("\n" + prompt, flush=True)
    input("Press Enter when the mount is in place and the pinhole is re-centred... ")


def confirm_gate(output, name, instruction, timeout_s, interactive):
    """Wait for an explicit operator confirmation.

    Remote sessions cannot use ``input()``: the process runs from a non-
    interactive shell.  The B1 driver already established the gate-file
    convention, so B2 uses the same files and the same confirmation command:
    ``drive_b1.py --output <session> --confirm-gate <name>`` writes
    ``gates/<name>.confirmed.json`` with ``operator_confirmed: true``.
    """
    if interactive:
        ask(instruction)
        return {"operator_confirmed": True, "operator": "stdin", "confirmed_utc": utc_now()}
    gates = output / "gates"
    gates.mkdir(parents=True, exist_ok=True)
    waiting = gates / f"{name}.waiting.json"
    confirmed = gates / f"{name}.confirmed.json"
    waiting.write_text(json.dumps({
        "gate": name, "instruction": instruction, "since_utc": utc_now(),
        "confirm_by_writing": f"--confirm-gate {name}",
        "how": f"python drive_b1.py --output <session> --confirm-gate {name}",
    }, indent=2), encoding="utf-8")
    print(f"\nGATE {name}: {instruction}", flush=True)
    print(f"  confirm with: --confirm-gate {name}", flush=True)
    deadline = time.time() + timeout_s
    while not confirmed.exists():
        if (output / "PAUSE_REQUESTED.txt").exists():
            raise SystemExit("operator requested a pause")
        if time.time() > deadline:
            raise SystemExit(f"operator gate {name!r} timed out")
        time.sleep(2.0)
    record = json.loads(confirmed.read_text(encoding="utf-8-sig"))
    if record.get("operator_confirmed") is not True:
        raise SystemExit(f"gate {name!r} is not an explicit operator confirmation")
    waiting.unlink(missing_ok=True)
    return record


def latest_bracket_record(output, label):
    """Read the per-bracket JSON the driver wrote for ``label``.

    ``invoke`` makes the capture directory unique, so a repeated label becomes
    ``P1_450nm_2``; the newest matching record is the one that was just
    measured.
    """
    candidates = []
    for path in sorted(output.glob(f"{label}*.json")):
        if path.name.endswith("_verified.json"):
            continue
        try:
            record = json.loads(path.read_text(encoding="utf-8-sig"))
        except Exception:
            continue
        if record.get("stage") == "b2_bracket":
            candidates.append((path.stat().st_mtime_ns, path, record))
    if not candidates:
        return None
    candidates.sort()
    return candidates[-1][2]


def wrap_deg(angle):
    """Signed angle in (-180, 180]."""
    return ra.wrap_deg(angle)


# B1 judged colour separation at r <= 20 px; these are its thresholds.
SPECTRAL_LIMIT_PX = 20
SPECTRAL_B_G_FACTOR = 5.0
SPECTRAL_R_G_FACTOR = 3.0


def spectral_sanity(records, limit=SPECTRAL_LIMIT_PX):
    """Whether the 450 and 650 nm brackets were actually different colours.

    B1's failure mode was a source that never changed colour: every band then
    carries one green-dominant spectrum and the "wavelength dependence" is an
    artefact of the labels.  B2 asks about angular structure, which serialises
    even at one spectrum, so this is reported and can be accepted explicitly
    rather than aborting a session that is still usable.
    """
    values = {}
    for band in (450, 650):
        record = records.get(band)
        try:
            ratios = record["analysis"]["ratios"][str(limit)]
        except (KeyError, TypeError):
            return {"ok": False, "reason": f"no plane ratios for {band} nm",
                    "values": values}
        values[band] = {key: ratios.get(key) for key in ("b_g", "r_g", "b_r")}
    try:
        b_factor = values[450]["b_g"] / values[650]["b_g"]
        r_factor = values[650]["r_g"] / values[450]["r_g"]
    except (KeyError, TypeError, ZeroDivisionError):
        return {"ok": False, "reason": "plane ratios are not usable numbers",
                "values": values}
    ok = bool(b_factor > SPECTRAL_B_G_FACTOR and r_factor > SPECTRAL_R_G_FACTOR)
    return {
        "ok": ok, "values": values,
        "b_g_factor": float(b_factor), "r_g_factor": float(r_factor),
        "thresholds": {"b_g": SPECTRAL_B_G_FACTOR, "r_g": SPECTRAL_R_G_FACTOR},
        "reason": ("450 and 650 nm are spectrally separated" if ok else
                   "450 and 650 nm are not separated by the B1 thresholds; the three "
                   "bands are effectively one spectrum"),
    }


def angle_note(state, settled_roll, reference_roll, min_deg=10.0, max_deg=45.0):
    """Describe the measured roll change of one state against the reference.

    P2 is the rotating state and has to be far enough from P1 to be
    discriminating but below the 45-degree alias.  P3 is the same-angle repeat:
    a small residual is expected and is used as the measured null, so it is
    never a reason to stop; a large residual only trades the null for a second
    rotation pair.
    """
    delta = wrap_deg(settled_roll - reference_roll)
    out = {"state": state, "delta_roll_deg": round(delta, 4)}
    if state == "P1":
        return out
    if state == "P2":
        out["usable"] = bool(min_deg <= abs(delta) <= max_deg)
        if abs(delta) < min_deg:
            out["problem"] = (f"|delta roll| = {abs(delta):.1f} deg is below {min_deg:.0f} deg; "
                              "the two orientations are too close to separate")
        elif abs(delta) > max_deg:
            out["problem"] = (f"|delta roll| = {abs(delta):.1f} deg exceeds {max_deg:.0f} deg; "
                              "a four-fold PSF aliases and the pairing is ambiguous")
        return out
    out["usable"] = True
    out["null_ok"] = bool(abs(delta) <= 5.0)
    if abs(delta) > 10.0:
        out["problem"] = (f"P1-P3 residual is {delta:+.1f} deg; this is no longer a "
                          "same-angle null but a second rotation pair")
    return out


def dark_already_done(output, label):
    """Whether a dark set was captured and verified by the driver."""
    path = output / f"{label}_verified.json"
    try:
        record = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return False
    return record.get("verified") is True


def read_jsonl(path):
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def recorded_state_roll(bracket_path, state):
    """The settled roll already on record for one mount state, if any."""
    for row in reversed(read_jsonl(bracket_path)):
        if row.get("kind") == "state" and row.get("state") == state:
            settled = row.get("settled") or {}
            if settled.get("roll_deg") is not None:
                return float(settled["roll_deg"])
    return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", type=Path, required=True)
    ap.add_argument("--adb", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--driver", type=Path, default=HERE / "drive_b1.py")
    ap.add_argument("--python", default=sys.executable)
    ap.add_argument("--serial", default=None)
    ap.add_argument("--camera-id", default=None)
    ap.add_argument("--starts", default="450:3224,550:784,650:2151")
    ap.add_argument("--assume-ceiling-dn", type=float, default=1023.0)
    ap.add_argument("--psf-window", type=int, default=176)
    ap.add_argument("--settle-timeout-s", type=float, default=180.0)
    ap.add_argument("--settle-gap-s", type=float, default=1.0)
    ap.add_argument("--stable-spread-deg", type=float, default=0.05)
    ap.add_argument("--stable-change-deg", type=float, default=0.10)
    ap.add_argument("--tilt-poll-s", type=float, default=5.0)
    ap.add_argument("--min-host-free-gb", type=float, default=20.0)
    ap.add_argument("--expected-app-version", default="0.3")
    ap.add_argument("--no-dark", action="store_true",
                    help="skip the opening and closing dark sets")
    ap.add_argument("--interactive", action="store_true",
                    help="prompt on stdin instead of waiting for gate files")
    ap.add_argument("--gate-timeout-s", type=float, default=3600.0,
                    help="how long an operator gate may stay open before the session stops")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the planned commands and exit without hardware")
    args = ap.parse_args(argv)

    starts = parse_starts(args.starts)
    for band in BANDS:
        if band not in starts:
            raise SystemExit(f"missing start exposure for {band} nm in --starts")
    args.output = args.output.resolve()
    args.repo = args.repo.resolve()
    args.output.mkdir(parents=True, exist_ok=True)
    plan_path = args.output / "B2_PLAN.json"
    tilt_path = args.output / "tilt.jsonl"
    bracket_path = args.output / "brackets.jsonl"

    plan = {
        "schema_version": 1,
        "session": "b2_rotation",
        "created_utc": utc_now(),
        "bands_nm": list(BANDS),
        "starts_us": {str(k): starts[k] for k in BANDS},
        "ladder_tiers": {str(k): LADDER_TIERS[k] for k in BANDS},
        "states": list(STATES),
        "assume_ceiling_dn": args.assume_ceiling_dn,
        "note": ("P1 and P3 should be close but the actual difference is measured; "
                 "the analysis uses delta_roll, not a target angle."),
    }
    if not args.dry_run:
        if plan_path.exists():
            existing = json.loads(plan_path.read_text(encoding="utf-8-sig"))
            if (existing.get("bands_nm") != list(BANDS)
                    or existing.get("starts_us") != {str(k): starts[k] for k in BANDS}):
                raise SystemExit(f"{plan_path} belongs to a different plan; "
                                 "use a new session directory")
            print(f"resuming session: {args.output}", flush=True)
        else:
            write_once(plan_path, plan)
            print(f"session: {args.output}", flush=True)

    commands = []
    for state in STATES:
        for band in BANDS:
            commands.append(driver_command(args, f"{state}_{band}nm", band, starts[band]))
    if args.dry_run:
        print(json.dumps({"plan": plan, "commands": commands}, indent=1))
        return 0

    if not args.no_dark:
        if dark_already_done(args.output, "dark_start"):
            print("opening dark set already verified; skipping", flush=True)
        else:
            confirm_gate(args.output, "dark_start",
                         "Occlude the source completely for the opening dark set.",
                         args.gate_timeout_s, args.interactive)
            subprocess.run(driver_command(args, "dark_start", dark=True), check=True)
            confirm_gate(args.output, "source_on",
                         "Remove the occlusion and restore the point source.",
                         args.gate_timeout_s, args.interactive)

    settled_rolls = {}
    for state in STATES:
        pending = [band for band in BANDS
                   if latest_bracket_record(args.output, f"{state}_{band}nm") is None]
        if not pending:
            roll = recorded_state_roll(bracket_path, state)
            if roll is not None:
                settled_rolls.setdefault(state, roll)
            print(json.dumps({"state": state, "skipped": "all three brackets already "
                                                      "recorded"}), flush=True)
            continue
        if state == "P1":
            prompt = ("Set the mount to the first orientation (P1).  Tighten the "
                      "temporary limit and re-centre the pinhole if needed.")
        elif state == "P2":
            prompt = ("Rotate the mount by about 20 degrees in one direction, "
                      "re-centre the pinhole, and tighten the limit.")
        else:
            prompt = ("Rotate back toward the P1 orientation (within a few degrees), "
                      "re-centre the pinhole, and tighten the limit.")
        confirm_gate(args.output, f"mount_{state}", prompt,
                     args.gate_timeout_s, args.interactive)
        settled = wait_for_settle(args.adb, args.serial, tilt_path, f"settle_{state}",
                                  args.settle_timeout_s, args.settle_gap_s,
                                  args.stable_spread_deg, args.stable_change_deg)
        settled_rolls[state] = settled["roll_deg"]
        reference = settled_rolls.get(STATES[0])
        if reference is None:
            reference = recorded_state_roll(bracket_path, STATES[0])
            if reference is None:
                reference = settled["roll_deg"]
        note = angle_note(state, settled["roll_deg"], reference)
        if note.get("usable") is False:
            # The angle is measured, not aimed at, so an out-of-range pair is a
            # real fact about the data.  It is not silently accepted: the
            # operator has to either fix the mount or say the pair stays.
            print(json.dumps({"warning": note["problem"]}), flush=True)
            confirm_gate(args.output, f"accept_angle_{state}",
                         f"{note['problem']}.  Adjust the mount and re-confirm, or accept "
                         "the measured angle and continue.", args.gate_timeout_s,
                         args.interactive)
            settled = read_tilt(args.adb, args.serial, f"accept_{state}")
            append_jsonl(tilt_path, settled)
            settled_rolls[state] = settled["roll_deg"]
            note = angle_note(state, settled["roll_deg"], reference)
            note["accepted"] = True
        append_jsonl(bracket_path, {"kind": "state", "state": state,
                                    "settled": settled, "angle": note,
                                    "recorded_utc": utc_now()})
        print(json.dumps({"state": state, "angle": note,
                          "pending_bands_nm": pending}), flush=True)

        records = {}
        for band in BANDS:
            label = f"{state}_{band}nm"
            found = latest_bracket_record(args.output, label)
            if found is None:
                before = read_tilt(args.adb, args.serial, f"before_{label}")
                append_jsonl(tilt_path, before)
                with TiltLogger(args.adb, args.serial, tilt_path, f"during_{label}",
                                poll_s=args.tilt_poll_s):
                    subprocess.run(driver_command(args, label, band, starts[band]),
                                   check=True)
                after = read_tilt(args.adb, args.serial, f"after_{label}")
                append_jsonl(tilt_path, after)
                found = latest_bracket_record(args.output, label)
                if found is None:
                    raise SystemExit(f"the driver returned without a {label} record")
                record = {
                    "kind": "bracket", "state": state, "band_nm": band,
                    "label": label,
                    "roll_before_deg": before["roll_deg"],
                    "roll_after_deg": after["roll_deg"],
                    "tilt_before_deg": before["tilt_deg"],
                    "tilt_after_deg": after["tilt_deg"],
                    "roll_change_deg": round(after["roll_deg"] - before["roll_deg"], 4),
                    "tilt_change_deg": round(after["tilt_deg"] - before["tilt_deg"], 4),
                    "peak_xy": found.get("peak_xy"),
                    "window_xywh": found.get("window_xywh"),
                    "run_ids": found.get("run_ids"),
                    "source_wavelength_nm": (found.get("source_status") or {}).get(
                        "current_wavelength_nm"),
                    "recorded_utc": utc_now(),
                }
                append_jsonl(bracket_path, record)
                print(json.dumps(record), flush=True)
            records[band] = found

        sanity = spectral_sanity(records)
        already = any(row.get("kind") == "spectral_sanity" and row.get("state") == state
                      for row in read_jsonl(bracket_path))
        if state == STATES[0] and not already:
            sanity["state"] = state
            append_jsonl(bracket_path, {"kind": "spectral_sanity", **sanity})
            print(json.dumps({"spectral_sanity": sanity}), flush=True)
            if not sanity["ok"]:
                confirm_gate(args.output, "accept_spectral_sanity",
                             f"{sanity['reason']}.  Fix the source and re-confirm, or "
                             "accept that the three bands are one spectrum for the "
                             "rotation test.", args.gate_timeout_s, args.interactive)

    if not args.no_dark:
        confirm_gate(args.output, "dark_end",
                     "Occlude the source completely for the closing dark set.",
                     args.gate_timeout_s, args.interactive)
        subprocess.run(driver_command(args, "dark_end", dark=True), check=True)

    write_once(args.output / "B2_DONE.json", {"finished_utc": utc_now(),
                                              "tilt_log": str(tilt_path.name),
                                              "bracket_log": str(bracket_path.name)})
    print("B2 capture finished", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
