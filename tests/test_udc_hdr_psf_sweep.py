"""Offline check of the B1 driver's capture-directory plumbing.

No hardware, no repository import: it builds a fake run tree with the same
layout ``run_udc_capture_plan`` writes, then exercises the three module-level
helpers the driver uses to read it (peak location, window crop, tier summaries)
and the analysis that consumes them.  The point is to catch a wrong path, a
wrong summary field name or a shape mismatch *before* a wired session, because
those failures only surface after the first capture has already been taken.

The synthetic frames are sparse: only the window is written, so three 8-megapixel
tiers cost no real disk.
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "scripts" / "udc_hdr_psf"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(HERE.parent))  # the repository, for tasks.measurement.hdr
import psf_analysis as pa  # noqa: E402
import drive_b1 as drv  # noqa: E402

SHAPE = (2448, 3264)
WINDOW = 176
PEAK = (1224, 1632)                 # (y, x), the sensor centre
BLACK = 64
CEILING = 959
TIERS_US = (376.0, 1504.0, 6016.0)
FAILURES = []


def check(condition, message):
    print(("  ok   " if condition else "  FAIL ") + message)
    if not condition:
        FAILURES.append(message)


def write_tier(root: Path, index: int, exposure_us: float):
    """One fake batch: temporal mean, summary, and an empty export dir."""
    batch = root / f"batch_{index:03d}"
    (batch / "decoded").mkdir(parents=True)
    (batch / "export").mkdir()
    mean = np.lib.format.open_memmap(batch / "decoded/temporal_mean_dn.npy",
                                     mode="w+", dtype="<u2", shape=SHAPE)
    mean[:] = BLACK
    yy, xx = np.mgrid[0:SHAPE[0], 0:SHAPE[1]]
    radius = np.hypot(yy - PEAK[0], xx - PEAK[1])
    # A compact PSF with a bright core and a 1/r^2 tail, scaled by exposure so
    # the bracket has a real seam; clipping at the measured plateau.
    signal = 900.0 * np.exp(-(radius / 1.6) ** 2) + 60.0 * 60.0 / (radius + 1.0) ** 2
    scale = exposure_us / TIERS_US[0]
    values = np.clip(BLACK + signal * scale, 0, CEILING).astype(np.uint16)
    window = (slice(PEAK[0] - WINDOW // 2, PEAK[0] + WINDOW // 2),
              slice(PEAK[1] - WINDOW // 2, PEAK[1] + WINDOW // 2))
    mean[window] = values[window]
    del mean
    summary = {
        "run_succeeded": True, "capture_complete": True,
        "declared_black_level_pattern": [BLACK] * 4,
        "declared_white_level": 1023,
        "all_manual_readbacks_within_tolerance": True,
        "frames": [{"observed_max_dn": float(values.max()),
                    "fraction_at_or_above_declared_white": 0.0}],
    }
    (batch / "decoded/summary.json").write_text(json.dumps(summary), encoding="utf-8")


def main() -> int:
    root = Path(tempfile.mkdtemp(prefix="b1_offline_"))
    try:
        print("1. the driver reads a capture directory without a hardware session")
        for index, exposure in enumerate(TIERS_US):
            write_tier(root, index, exposure)
        peak = drv.peak_position(root / "batch_000/decoded/temporal_mean_dn.npy")
        # The planted core is a disk of equal values, so argmax may pick any of
        # its pixels; what matters is that it lands on the core, not which one.
        check(abs(peak[0] - PEAK[1]) <= 2 and abs(peak[1] - PEAK[0]) <= 2,
              f"peak found at {peak}, within 2 px of the planted core {(PEAK[1], PEAK[0])}")
        rect = pa.window(peak, WINDOW)
        check(rect[2] == WINDOW and rect[3] == WINDOW, f"window is {rect[2]}x{rect[3]}")
        check(rect[0] % 2 == 0 and rect[1] % 2 == 0,
              "the window keeps the CFA parity it was aligned to")

        print("2. tier summaries carry exposure, black level and readback")
        rows = drv.tier_summaries(root, TIERS_US)
        check(len(rows) == len(TIERS_US), f"{len(rows)} tiers read")
        check([r["exposure_us"] for r in rows] == list(TIERS_US), "exposures are in ladder order")
        check(all(r["black_dn"] == BLACK for r in rows), "black level is the declared pattern")
        check(all(r["capture_complete"] and r["run_succeeded"] for r in rows),
              "every tier is complete and succeeded")

        print("3. the analysis runs on the real file layout")
        frames = drv.read_window(root, TIERS_US, rect)
        peak_local = (peak[0] - rect[0], peak[1] - rect[1])
        analysis = pa.analyse(frames, list(TIERS_US), peak_local, BLACK,
                              ceiling={"ceiling_dn": CEILING, "plateau": True,
                                       "source": "test"})
        print(f"   core={analysis['core']['core_dn_shortest_tier']:.0f} DN at "
              f"{analysis['core']['shortest_us']:.0f} us, "
              f"judged seams={analysis['seams']['judged_bands']}, "
              f"bad={analysis['seams']['bad_bands']}")
        check(analysis["core"]["core_pixels"] > 0, "the core annulus has pixels")
        check(analysis["core"]["core_dn_shortest_tier"] > 0, "the core has depth")
        check(analysis["seams"]["judged_bands"] >= 2, "at least two seam bands are judged")
        check(analysis["seams"]["passed"], "a clean synthetic bracket passes the seam scan")

        print("4. records survive a band where a colour ratio is undefined")
        poisoned = dict(analysis)
        poisoned["ratios"] = {k: {"r_g": float("nan"), "b_r": float("inf"),
                                  "green": 0.0, "limit_px": int(k)}
                              for k in analysis["ratios"]}
        target = root / "record.json"
        drv.write_json(target, {"band": 400, "ratios": poisoned["ratios"]})
        written = json.loads(target.read_text(encoding="utf-8"))
        check(written["ratios"]["20"]["b_r"] is None,
              "an infinite ratio is written as null, not an invalid number")
        check(drv.load_json(target)["band"] == 400, "the record is still readable")
        print("5. the exposure ladder always has a seam to check")
        for shortest, tiers, reach, want in (
            (376, 5, False, [376, 1504, 6016, 24064, 96256]),
            (376, 5, True, [376, 1504, 6016, 24064, 96256, 385024]),
            (1000, 3, False, [1000, 4000, 16000]),
            (14, 9, True, [14, 56, 224, 896, 3584, 14336, 57344, 229376, 400000]),
            # A shortest tier at the cap cannot climb; the ladder must still
            # keep two rungs, or there is no pair to compare.
            (400000, 5, False, [100000, 400000]),
            (300000, 5, True, [100000, 400000]),
        ):
            got = drv.ladder_from_base(shortest, tiers, reach)
            check(got == want, f"shortest={shortest} tiers={tiers} reach={reach} -> {got}")
        for shortest in (14, 376, 5000, 400000):
            for tiers in (2, 5, 6):
                for reach in (False, True):
                    got = drv.ladder_from_base(shortest, tiers, reach)
                    ok = (len(got) >= 2 and got == sorted(set(got))
                          and all(drv.MIN_US <= v <= drv.MAX_US for v in got)
                          and all(b <= drv.LADDER_RATIO * a for a, b in zip(got, got[1:])))
                    if not ok:
                        FAILURES.append(f"ladder {shortest}/{tiers}/{reach} -> {got}")
        check(not any("ladder" in f for f in FAILURES),
              "every ladder is ordered, in range and at least two tiers long")

        print("6. the purge only ever names runs the record identifies")
        record = {"scope": [
            {"dir": "band_400nm_try0",
             "batches": [{"run_id": "run_1795697935275_d0c2059c"},
                         {"run_id": "run_1795697939000_aaaaaaaa"}],
             "files": [{"path": "batch_000\\decoded\\frame_0000.npy"}]},
            {"dir": "band_570nm_try0",
             "batches": [{"run_id": None}],
             "files": [{"path": "batch_000\\export\\device_files\\captures\\run_1795700000000_bbbbbbbb\\run.json"}]},
        ]}
        named, batches = drv.runs_named_by_record(record)
        check(len(named) == 3 and batches == 3,
              f"three runs from three batches, one found only by path ({len(named)}/{batches})")
        check(named == {"run_1795697935275_d0c2059c", "run_1795697939000_aaaaaaaa",
                        "run_1795700000000_bbbbbbbb"}, f"exact run IDs ({sorted(named)})")
        empty, empty_batches = drv.runs_named_by_record({})
        check(empty == set() and empty_batches == 0, "an empty record names nothing")
        check(batches - len(named) == 0, "the unattributed count is batches minus named runs")
        # The geometry assertion must not gate the purge: a purge-only run
        # cannot confirm a beam it never uses, and it failed exactly that way.
        source = (SCRIPTS / "drive_b1.py").read_text(encoding="utf-8")
        check(source.index('if args.stop_after == "purge"')
              < source.index("--confirm-geometry is required"),
              "the purge stage runs before the geometry assertion")

        print("7. the shortest tier is scaled on the peak, not the core median")
        # The 2026-11-27 probe: 96 DN core median against a 246 DN peak at
        # 376 us.  Scaling on the median asks for 5.5x more light and clips the
        # peak; scaling on the peak asks for 3.1x and lands just under the top.
        probe_median, probe_peak, probe_us = 95.7, 246.3, 376
        target = drv.target_peak_dn(1023)
        check(abs(target - 0.8 * 959.0) < 1e-9, f"peak target is 80% of the span ({target:.1f})")
        got = drv.next_shortest_us(probe_peak, probe_us, 1023)
        check(abs(got - 1167) <= 12, f"probe recommendation is near 1.2 ms ({got} us)")
        on_median = drv.next_shortest_us(probe_median, probe_us, 1023)
        check(on_median > 2.5 * got,
              f"the median would have asked for {on_median} us, {on_median / got:.1f}x the peak-based value")
        check(got * probe_peak / probe_us <= 1023,
              "the recommended tier leaves the probe peak below the ceiling")
        check(drv.next_shortest_us(float("nan"), 500, 1023) == 500,
              "an undefined peak leaves the exposure unchanged")
        check(drv.next_shortest_us(1e-9, 500, 1023) == drv.MAX_US,
              "a near-black peak is clamped at the exposure cap")
        check(drv.next_shortest_us(1e9, 500, 1023) == drv.MIN_US,
              "a huge peak is clamped at the exposure floor")
        # A ceiling measured below the declared white must move the target with it.
        check(drv.target_peak_dn(959) < target, "a lower measured ceiling lowers the target")
        check(drv.next_shortest_us(probe_peak, probe_us, 959) < got,
              "a lower measured ceiling asks for a shorter tier")

        print("8. a transient SDK read error costs a retry, not the session")
        # 2026-11-27: the grating reached 450.0 nm, then get_curr_wave answered
        # E01 once and the whole session aborted.  That exact shape is tested.
        class Flaky(RuntimeError):
            pass

        logs = []
        remaining = {"left": 1}

        def move_once(attempt):
            if remaining["left"]:
                remaining["left"] -= 1
                raise Flaky("SDK operation failed: get_curr_wave: E01")
            return attempt

        got = drv.retry_transient(move_once, 4, 0.0, "TLS move to 450 nm", (Flaky,),
                                  log=logs.append)
        check(got == 2, f"the move succeeds on the retry after a read error (attempt {got})")
        check(len(logs) == 1 and "E01" in logs[0],
              "the transient failure is recorded with its SDK code")

        def always_fails(attempt):
            raise Flaky("SDK operation failed: get_curr_wave: E01")

        try:
            drv.retry_transient(always_fails, 3, 0.0, "TLS move to 650 nm", (Flaky,),
                                log=logs.append)
        except RuntimeError as exc:
            check("after 3 attempts" in str(exc),
                  "a persistent failure still ends the session, and says how often it tried")
        else:
            check(False, "a persistent failure must not be reported as success")

        def deliberate(attempt):
            raise RuntimeError("Operator requested a pause")

        try:
            drv.retry_transient(deliberate, 3, 0.0, "TLS move", (Flaky,), log=logs.append)
        except RuntimeError as exc:
            check("pause" in str(exc), "a deliberate abort is never retried")
        else:
            check(False, "a deliberate abort must propagate")

        # The retry has to be wired into the move itself, not merely available:
        # a helper nobody calls would have passed all of the above.
        source = (SCRIPTS / "drive_b1.py").read_text(encoding="utf-8")
        body = source[source.index("def move_source"):]
        body = body[:body.index("# ---- session protocol")]
        check("retry_transient" in body, "move_source goes through retry_transient")
        check("tls.close()" in body and "tls.connect()" in body,
              "a repeat failure reopens the device instead of reusing a dead handle")

        print("9. every argument the driver reads is one it declares")
        import ast
        source = (SCRIPTS / "drive_b1.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        declared = {node.args[0].value.lstrip("-").replace("-", "_")
                    for node in ast.walk(tree)
                    if isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "add_argument"
                    and node.args and isinstance(node.args[0], ast.Constant)}
        read = {node.attr for node in ast.walk(tree)
                if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
                and node.value.id == "args"}
        missing = sorted(read - declared)
        check(not missing, f"no argument is read without being declared (missing: {missing})")

        print("10. no stage calls a helper that is defined further down")
        # A nested function is only bound when its `def` executes, so a stage that
        # calls one defined lower in main() fails at run time, after the capture,
        # not at import.  The ceiling probe did exactly that once.
        main_fn = next(node for node in tree.body
                       if isinstance(node, ast.FunctionDef) and node.name == "main")
        defined = {node.name: node.lineno for node in ast.walk(main_fn)
                   if isinstance(node, ast.FunctionDef) and node is not main_fn}
        early = []
        pending = list(main_fn.body)
        while pending:
            node = pending.pop()
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id in defined and node.lineno < defined[node.func.id]):
                early.append(f"{node.func.id}() at line {node.lineno} < def at "
                             f"{defined[node.func.id]}")
            pending.extend(ast.iter_child_nodes(node))
        check(not early, f"every helper is defined before the stage that calls it ({early})")

        print("11. the drift schedule repeats the shortest tier between tiers")
        schedule = drv.drift_schedule([376, 1504, 6016])
        check(schedule["order"] == [376, 1504, 376, 6016, 376],
              f"capture order is t0,t1,t0,t2,t0 ({schedule['order']})")
        check(schedule["tiers"] == [0, 1, 3],
              f"the tier batches are 0,1,3 ({schedule['tiers']})")
        check(schedule["references"] == [0, 2, 4],
              f"the reference batches are 0,2,4 ({schedule['references']})")
        check(all(schedule["order"][i] == 376 for i in schedule["references"]),
              "every reference is the shortest tier")
        flat = drv.plain_schedule([376, 1504, 6016])
        check(flat["order"] == [376, 1504, 6016] and flat["references"] == [],
              "the plain schedule has no references")
        check(drv.drift_schedule([376])["references"] == [],
              "a single-tier bracket needs no reference")
        peak_dn = drv.frame_peak_dn(root / "batch_000/decoded/temporal_mean_dn.npy", BLACK)
        check(peak_dn == CEILING - BLACK, f"the frame peak reads the planted, clipped core ({peak_dn:.1f} DN)")

        print("12. hard criteria veto; weak indicators only describe")
        # A criterion that measures how far the data can be trusted must not
        # double as a veto: the 2026-11-27 gate failed on a seam that sits
        # below the instrument's own reproducibility while the source had
        # changed colour by 300x.  The split is asserted on the source so it
        # cannot drift back silently.
        helpers = {node.name: node for node in ast.walk(main_fn)
                   if isinstance(node, ast.FunctionDef)}
        passed_src = None
        for node in ast.walk(helpers["gate_summary"]):
            if (isinstance(node, ast.Assign) and len(node.targets) == 1
                    and isinstance(node.targets[0], ast.Name)
                    and node.targets[0].id == "passed"):
                passed_src = ast.get_source_segment(source, node.value)
        check(passed_src is not None, "the gate summary still decides a pass/fail")
        check("colour_changed" in passed_src and "complete" in passed_src,
              f"the gate passes on the hard criteria only ({passed_src})")
        check("consistent" not in passed_src,
              "seam consistency is recorded but is not part of the pass/fail")
        check('verdict["ok"] and seams["passed"]' not in source,
              "a band is no longer accepted only when its seams also pass")
        check('hard_fail = not verdict["ok"]' in source,
              "band rejection is keyed on the hard verdict alone")
        check('"strength": "weak"' in (SCRIPTS / "psf_analysis.py").read_text(encoding="utf-8"),
              "the seam scan declares itself a weak criterion in the record")

        print("13. re-judging reads the recorded gate and never recaptures it")
        # The as-captured SOURCE_GATE.json stays as written; the re-judged one
        # goes beside it.  The re-judge branch must be analysis-only: a single
        # invoke() there would spend two brackets of lamp time and overwrite the
        # frames the note claims did not change.
        rejudge_nodes = [node for node in ast.walk(main_fn)
                         if isinstance(node, ast.If)
                         and (ast.get_source_segment(source, node.test) or "").strip()
                         == "args.rejudge_gate"]
        check(len(rejudge_nodes) == 1, f"exactly one re-judge branch ({len(rejudge_nodes)})")
        if rejudge_nodes:
            # Only the body: the else branch is the normal capture path, which
            # legitimately invokes the plan and purges afterwards.
            called = {node.func.id for statement in rejudge_nodes[0].body
                      for node in ast.walk(statement)
                      if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}
            check("measure" in called, "it re-measures the recorded bracket")
            check("invoke" not in called and "purge_runs_for" not in called,
                  "it neither captures nor deletes anything")
            reads = "\n".join(ast.get_source_segment(source, statement) or ""
                              for statement in rejudge_nodes[0].body)
            check('SOURCE_GATE.json' in reads and 'rejudged.json' not in reads.split("one_shot")[0],
                  "it reads the as-captured record and does not read its own output")
        record_src = ast.get_source_segment(source, helpers["record_gate"]) or ""
        check("SOURCE_GATE.rejudged.json" in record_src,
              "the re-judged record is written to its own file")
        check("previous_passed" in record_src and "changed" in record_src,
              "the re-judged record carries the previous verdict and what changed")

        print("14. a stop request stops even when the stage was already recorded")
        # The stop check used to sit inside each stage body, so
        # '--stop-after gate' on an already-recorded gate fell through into the
        # sweep: that is how an unattended capture started on 2026-11-27.  The
        # guard is that the per-stage stops go through one helper, and the only
        # raw comparisons left are at the function's own top level.
        inline = [node for node in ast.walk(main_fn)
                  if isinstance(node, ast.Compare)
                  and isinstance(node.ops[0], ast.Eq)
                  and isinstance(node.left, ast.Attribute)
                  and node.left.attr == "stop_after"]
        check(len(inline) == 2,
              f"only the purge and sweep stops compare stop_after directly ({len(inline)})")
        for name in ("dark", "gate", "passthrough"):
            check(f'stop_if_requested("{name}"' in source,
                  f"the {name} stop goes through stop_if_requested")
        check("def stop_if_requested" in source and "stage_done" in
              (ast.get_source_segment(source, helpers["stop_if_requested"]) or ""),
              "the helper reads the recorded stage rather than only what this run did")

        print("15. a decided-but-unfinished retry resumes at its own exposure")
        # On 2026-11-27 a band was mid-retry when an ADB export timed out.  The
        # decision to recapture it shorter was already saved, so a resume must
        # use it instead of the session seed, or it re-takes the failed bracket.
        attempts = [
            {"wavelength_nm": 500, "attempt": 0, "next_shortest_us": 789,
             "retry_reason": "core clipped"},
            {"wavelength_nm": 450, "accepted": True, "next_shortest_us": 3224},
            {"wavelength_nm": 650, "rejected_reason": "core clips at any exposure"},
            {"wavelength_nm": 550, "retry_reason": "no decision recorded"},
            {"wavelength_nm": 600, "shortest_us": 2000, "retry_reason": "no decision",
             "verdict": {"core_clipped": True}},
        ]
        seeds = drv.resume_seeds(attempts)
        check(seeds == {500: 789, 600: 500},
              f"the stored decision is used, and a legacy clip is reconstructed ({seeds})")
        check(drv.resume_seeds(None) == {}, "an absent record has no seeds")
        check('entry["next_shortest_us"] = int(shortest_us)' in source,
              "the retry branch records the exposure it decided on")

        return 0 if not FAILURES else 1
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_suite():
    """Run the whole sectioned suite, so `pytest` covers it as well as by hand."""
    assert main() == 0


def test_a_repeated_gate_confirmation_keeps_the_earlier_one_as_history():
    """A resumed session re-enters a state after the mount has moved.

    The bench command has to work twice for the same gate name, and the first
    confirmation must end up as history rather than stay where the next wait
    would accept it.
    """
    root = Path(tempfile.mkdtemp())
    try:
        gates = root / "gates"
        gates.mkdir(parents=True)
        (gates / "mount_P1.waiting.json").write_text(
            json.dumps({"gate": "mount_P1", "instruction": "place the mount"}),
            encoding="utf-8")
        argv = ["--output", str(root), "--confirm-gate", "mount_P1", "--operator", "tester"]
        assert drv.main(argv) == 0
        first = json.loads((gates / "mount_P1.confirmed.json").read_text(encoding="utf-8-sig"))
        assert first["operator_confirmed"] is True
        assert first["instruction_seen"] == "place the mount"
        # The mount moves and the gate is opened again; the same command runs.
        assert drv.main(argv) == 0
        second = json.loads((gates / "mount_P1.confirmed.json").read_text(encoding="utf-8-sig"))
        assert second["operator_confirmed"] is True
        history = sorted((gates / "history").glob("mount_P1.confirmed.*.json"))
        assert len(history) == 1
        kept = json.loads(history[0].read_text(encoding="utf-8-sig"))
        assert kept["operator_confirmed"] is True
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
