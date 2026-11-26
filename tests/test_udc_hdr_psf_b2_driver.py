"""Hardware-free checks for the B2 session wrapper."""
from __future__ import annotations

import json as json_module
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "scripts" / "udc_hdr_psf"
sys.path.insert(0, str(SCRIPTS))
import drive_b2 as b2  # noqa: E402


def _args(tmp_path):
    return SimpleNamespace(
        python=sys.executable,
        driver=SCRIPTS / "drive_b1.py",
        repo=tmp_path,
        adb=tmp_path / "adb.exe",
        output=tmp_path / "session",
        psf_window=176,
        expected_app_version="0.3",
        min_host_free_gb=20.0,
        serial="serial",
        camera_id=None,
        assume_ceiling_dn=1023.0,
    )


def test_parse_starts_round_trips():
    assert b2.parse_starts("450:3224,550:784,650:2151") == {
        450: 3224, 550: 784, 650: 2151,
    }


def test_driver_command_names_the_single_bracket(tmp_path):
    command = b2.driver_command(_args(tmp_path), "P2_550nm", 550, 784)
    assert "--one-bracket" in command
    assert command[command.index("--one-bracket") + 1] == "P2_550nm"
    assert command[command.index("--one-band") + 1] == "550"
    assert command[command.index("--ladder-tiers") + 1] == "6"
    assert command[command.index("--bands") + 1] == "550"
    assert "--assume-ceiling-dn" in command


def test_dark_command_uses_the_dark_branch(tmp_path):
    command = b2.driver_command(_args(tmp_path), "dark_start", dark=True)
    assert "--one-dark" in command
    assert "--one-bracket" not in command
    assert command[command.index("--bands") + 1] == "450:650:100"


def test_settle_needs_a_flat_window():
    flat = [{"roll_deg": -26.0, "tilt_deg": -3.0} for _ in range(6)]
    assert b2.stable_history(flat)
    creeping = [{"roll_deg": -26.0 + 0.03 * index, "tilt_deg": -3.0}
                for index in range(6)]
    assert not b2.stable_history(creeping)
    noisy_tilt = [{"roll_deg": -26.0, "tilt_deg": -3.0 + 0.02 * index}
                  for index in range(6)]
    assert not b2.stable_history(noisy_tilt)


def test_dry_run_does_not_touch_hardware(tmp_path, capsys):
    output = tmp_path / "session"
    code = b2.main([
        "--repo", str(tmp_path), "--adb", str(tmp_path / "adb.exe"),
        "--output", str(output), "--dry-run",
    ])
    assert code == 0
    text = capsys.readouterr().out
    assert "P1_450nm" in text
    assert "P3_650nm" in text
    assert not (output / "B2_PLAN.json").exists()


def test_non_finite_values_become_null():
    assert b2.json_safe({"roll": float("nan"), "tilt": -3.0}) == {
        "roll": None, "tilt": -3.0,
    }


def test_angle_note_accepts_a_discriminating_pair():
    note = b2.angle_note("P2", -26.5, -6.0)
    assert note["usable"] is True
    assert abs(note["delta_roll_deg"] + 20.5) < 1e-9
    assert "problem" not in note


def test_angle_note_flags_a_pair_that_is_too_close():
    note = b2.angle_note("P2", -8.0, -6.0)
    assert note["usable"] is False
    assert "below 10 deg" in note["problem"]


def test_angle_note_flags_a_pair_past_the_alias():
    note = b2.angle_note("P2", 60.0, -6.0)
    assert note["usable"] is False
    assert "exceeds 45 deg" in note["problem"]


def test_angle_note_wraps_across_the_half_turn():
    note = b2.angle_note("P2", -179.0, 179.0)
    assert abs(note["delta_roll_deg"] - 2.0) < 1e-9


def test_p3_small_residual_is_a_null_not_a_failure():
    note = b2.angle_note("P3", -7.5, -6.0)
    assert note["usable"] is True
    assert note["null_ok"] is True
    assert "problem" not in note


def test_p3_large_residual_is_reported_as_a_second_pair():
    note = b2.angle_note("P3", 8.0, -6.0)
    assert note["usable"] is True
    assert note["null_ok"] is False
    assert "second rotation pair" in note["problem"]


def test_spectral_sanity_reads_the_plane_ratios():
    def record(b_g, r_g):
        return {"analysis": {"ratios": {"20": {"b_g": b_g, "r_g": r_g, "b_r": 1.0}}}}
    found = b2.spectral_sanity({450: record(12.0, 0.2), 650: record(1.0, 4.0)})
    assert found["ok"] is True
    assert found["b_g_factor"] == 12.0
    assert found["r_g_factor"] == 20.0

    flat = b2.spectral_sanity({450: record(2.0, 1.0), 650: record(2.0, 1.0)})
    assert flat["ok"] is False
    assert "one spectrum" in flat["reason"]

    missing = b2.spectral_sanity({450: record(2.0, 1.0)})
    assert missing["ok"] is False
    assert "no plane ratios" in missing["reason"]


def test_latest_bracket_record_prefers_the_newest_capture(tmp_path):
    output = tmp_path / "session"
    output.mkdir()
    first = output / "P1_450nm.json"
    first.write_text(json_module.dumps({"stage": "b2_bracket", "run_ids": ["a"]}))
    second = output / "P1_450nm_2.json"
    second.write_text(json_module.dumps({"stage": "b2_bracket", "run_ids": ["b"]}))
    (output / "P1_450nm_verified.json").write_text(json_module.dumps({"verified": True}))
    os.utime(first, ns=(1_000_000_000, 1_000_000_000))
    os.utime(second, ns=(2_000_000_000, 2_000_000_000))
    assert b2.latest_bracket_record(output, "P1_450nm")["run_ids"] == ["b"]
    assert b2.latest_bracket_record(output, "P2_450nm") is None


def test_dark_already_done_needs_verified_true(tmp_path):
    output = tmp_path / "session"
    output.mkdir()
    assert not b2.dark_already_done(output, "dark_start")
    (output / "dark_start_verified.json").write_text(
        json_module.dumps({"verified": False}))
    assert not b2.dark_already_done(output, "dark_start")
    (output / "dark_start_verified.json").write_text(
        json_module.dumps({"verified": True}))
    assert b2.dark_already_done(output, "dark_start")


def test_confirm_gate_uses_the_b1_gate_files(tmp_path):
    output = tmp_path / "session"
    gates = output / "gates"
    gates.mkdir(parents=True)
    (gates / "mount_P1.confirmed.json").write_text(json_module.dumps(
        {"operator_confirmed": True, "operator": "tester",
         "confirmed_utc": "2026-11-27T00:00:00+00:00"}))
    record = b2.confirm_gate(output, "mount_P1", "place the mount", 1.0, False)
    assert record["operator"] == "tester"
    assert not (gates / "mount_P1.waiting.json").exists()


def test_confirm_gate_rejects_a_file_that_is_not_a_confirmation(tmp_path):
    output = tmp_path / "session"
    gates = output / "gates"
    gates.mkdir(parents=True)
    (gates / "mount_P1.confirmed.json").write_text(
        json_module.dumps({"operator_confirmed": False}))
    try:
        b2.confirm_gate(output, "mount_P1", "place the mount", 1.0, False)
    except SystemExit as exc:
        assert "not an explicit operator confirmation" in str(exc)
    else:
        raise AssertionError("a false confirmation must not pass the gate")


def test_confirm_gate_writes_the_waiting_record_before_timing_out(tmp_path):
    output = tmp_path / "session"
    try:
        b2.confirm_gate(output, "mount_P2", "rotate the mount", 0.0, False)
    except SystemExit as exc:
        assert "timed out" in str(exc)
    else:
        raise AssertionError("a gate with no confirmation must time out")
    waiting = json_module.loads((output / "gates" / "mount_P2.waiting.json").read_text())
    assert waiting["instruction"] == "rotate the mount"
    assert waiting["confirm_by_writing"] == "--confirm-gate mount_P2"


def test_recorded_state_roll_reads_the_last_state_row(tmp_path):
    log = tmp_path / "brackets.jsonl"
    log.write_text("\n".join(json_module.dumps(row) for row in [
        {"kind": "state", "state": "P1", "settled": {"roll_deg": -6.0},
         "angle": {"delta_roll_deg": 0.0}},
        {"kind": "bracket", "state": "P1", "band_nm": 450},
        {"kind": "state", "state": "P1", "settled": {"roll_deg": -6.4},
         "angle": {"delta_roll_deg": 0.0}},
    ]) + "\n")
    assert b2.recorded_state_roll(log, "P1") == -6.4
    assert b2.recorded_state_roll(log, "P4") is None
