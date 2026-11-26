"""Read the Axon 20's tilt about the optical axis over ADB, with no app change.

The phone has an ICM20600 accelerometer that is already streaming (the system
pedometer holds a connection), so `dumpsys sensorservice` prints its last 50
samples without us registering anything and without waking the screen. 50
samples at ~100 Hz is a 0.5 s window, which is enough: rotate the mount, wait a
second, read.

Angles are derived from gravity g expressed in the device frame:

    roll   = atan2(gx, -gy)      rotation about the device z axis, i.e. about
                                 the camera optical axis (camera looks along -z)
    tilt   = asin(gz / |g|)      how far the screen plane leans out of vertical
    |g|                          sanity check; must stay near 9.8

Only the *change* in roll between two readings means anything. The absolute
value depends on how the mount happens to sit, so the first reading of a session
is recorded as the 0 deg reference.

Usage, on the capture host:
    python read_tilt.py --adb <adb.exe> --serial <serial>
    python read_tilt.py --label mount0 --out <session root>
    python read_tilt.py --watch 8 --label rot20 --settle 4

Output is appended to outputs_tilt_YYYYMMDD.jsonl next to this script's --out
directory, one JSON object per reading.
"""
import argparse
import json
import math
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# adb and the device serial come from the command line: this script is used on
# whichever capture host is wired to the phone, and a serial baked in here would
# silently address the wrong device on another one.
SENSOR = "ICM20600 Accelerometer Non-wakeup: last 50 events"
LINE = re.compile(r"^\s*\d+ \(ts=([\d.]+), wall=([\d:.]+)\)\s+(-?[\d.]+),\s+(-?[\d.]+),\s+(-?[\d.]+),")


def dumpsys(adb="adb", serial=None):
    command = [adb] + (["-s", serial] if serial else []) + \
        ["shell", "dumpsys", "sensorservice"]
    p = subprocess.run(command, capture_output=True, text=True, timeout=60)
    if p.returncode != 0:
        raise SystemExit(f"adb failed: {p.stderr.strip()}")
    return p.stdout.splitlines()


def samples(lines):
    start = None
    for i, ln in enumerate(lines):
        if SENSOR in ln:
            start = i
            break
    if start is None:
        raise SystemExit(f"sensor block not found: {SENSOR!r}. "
                         "Is the accelerometer still held open?")
    out = []
    for ln in lines[start + 1:start + 80]:
        m = LINE.match(ln)
        if m:
            out.append({"ts": float(m.group(1)), "wall": m.group(2),
                        "g": [float(m.group(3)), float(m.group(4)), float(m.group(5))]})
        elif out:
            break
    if not out:
        raise SystemExit("no samples parsed from the accel block")
    return out


def angles(g):
    gx, gy, gz = g
    norm = math.sqrt(gx * gx + gy * gy + gz * gz)
    roll = math.degrees(math.atan2(gx, -gy))
    tilt = math.degrees(math.asin(max(-1.0, min(1.0, gz / norm)))) if norm else float("nan")
    return roll, tilt, norm


def reading(label=None, tail=10, adb="adb", serial=None):
    s = samples(dumpsys(adb, serial))
    rows = [angles(x["g"]) for x in s]
    block = sorted(r[0] for r in rows)
    full_span = block[-1] - block[0]
    # The block is a rolling ~3.3 s buffer: right after a rotation it still holds
    # pre-rotation samples, so only the newest `tail` values describe the present.
    recent = sorted(r[0] for r in rows[-tail:])
    n = len(recent)
    med = recent[n // 2] if n % 2 else 0.5 * (recent[n // 2 - 1] + recent[n // 2])
    rec = {
        "utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "label": label,
        "samples": len(rows),
        "tail_used": n,
        "sample_span_s": round(s[-1]["ts"] - s[0]["ts"], 3),
        "roll_deg": round(med, 3),
        "roll_spread_tail_deg": round(recent[-1] - recent[0], 3),
        "roll_spread_block_deg": round(full_span, 3),
        "buffer_mixed": bool(full_span > 1.0),
        "tilt_deg": round(sum(r[1] for r in rows) / len(rows), 3),
        "g_norm": round(sum(r[2] for r in rows) / len(rows), 3),
        "median_g": [round(sorted(x["g"][k] for x in s)[len(s) // 2], 4) for k in range(3)],
    }
    return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", default=None)
    ap.add_argument("--watch", type=int, default=1)
    ap.add_argument("--gap", type=float, default=1.0)
    ap.add_argument("--tail", type=int, default=10,
                    help="newest samples used for the reported angle (10 = ~0.7 s)")
    ap.add_argument("--settle", type=float, default=0.0,
                    help="seconds to wait before reading; the buffer is ~3.3 s long, "
                         "so use >=4 after moving the mount")
    ap.add_argument("--adb", default="adb", help="adb executable to use")
    ap.add_argument("--serial", default=None,
                    help="device serial; omit when exactly one device is attached")
    ap.add_argument("--out", default=".", help="directory the jsonl log is written to")
    a = ap.parse_args()

    if a.settle:
        time.sleep(a.settle)
    outdir = Path(a.out)
    outdir.mkdir(parents=True, exist_ok=True)
    log = outdir / f"tilt_{datetime.now().strftime('%Y%m%d')}.jsonl"

    recs = []
    for i in range(a.watch):
        rec = reading(a.label, a.tail, a.adb, a.serial)
        recs.append(rec)
        flag = "  [BUFFER MIXED: wait ~4 s and reread]" if rec["buffer_mixed"] else ""
        print(f"{rec['utc']}  roll={rec['roll_deg']:+7.3f} deg  "
              f"tilt={rec['tilt_deg']:+6.2f} deg  |g|={rec['g_norm']:.2f}  "
              f"n={rec['samples']}/{rec['tail_used']} span={rec['sample_span_s']:.2f}s  "
              f"tail_spread={rec['roll_spread_tail_deg']:.3f}  "
              f"block_spread={rec['roll_spread_block_deg']:.3f}{flag}", flush=True)
        with log.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
        if i + 1 < a.watch:
            time.sleep(a.gap)

    if len(recs) > 1:
        v = [r["roll_deg"] for r in recs]
        print(f"\nroll over {len(v)} readings: min {min(v):+.3f}  max {max(v):+.3f}  "
              f"range {max(v)-min(v):.3f} deg")
    print(f"log -> {log}")


if __name__ == "__main__":
    main()
