"""Hardware-free checks for the B2 azimuthal session analysis."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "scripts" / "udc_hdr_psf"
sys.path.insert(0, str(SCRIPTS))
import analyse_b2 as ab  # noqa: E402

SHAPE = (121, 121)
BLACK = 64.0
CENTRE = (60.2, 59.7)


def _image(theta0_deg, centre=CENTRE, modulation=0.25, sigma=3.0):
    """A compact four-fold spot on the binned grid, in DN."""
    yy, xx = np.mgrid[0:SHAPE[0], 0:SHAPE[1]]
    radius = np.hypot(xx - centre[0], yy - centre[1])
    theta = np.arctan2(yy - centre[1], xx - centre[0])
    theta0 = np.deg2rad(theta0_deg)
    core = 700.0 * np.exp(-radius ** 2 / (2 * sigma ** 2))
    return BLACK + core * (1.0 + modulation * np.cos(4.0 * (theta - theta0)))


def _session(tmp_path, thetas, rolls, band=450, centre=CENTRE):
    """Write a minimal session tree: log, per-bracket records, temporal means."""
    session = tmp_path / "session"
    session.mkdir()
    rows = []
    for state, roll in zip(ab.STATES, rolls):
        rows.append({"kind": "state", "state": state,
                     "settled": {"roll_deg": roll}, "angle": {"delta_roll_deg": 0.0}})
        label = f"{state}_{band}nm"
        capture = session / "captures" / label / "batch_000" / "decoded"
        capture.mkdir(parents=True)
        image = _image(thetas[state], centre=centre)
        np.save(capture / "temporal_mean_dn.npy", image)
        (session / f"{label}.json").write_text(json.dumps({
            "stage": "b2_bracket", "label": label, "band_nm": band,
            "peak_xy": [int(np.argmax(image) % SHAPE[1]),
                        int(np.argmax(image) // SHAPE[1])],
            "reference_batches": [0],
            "run_ids": [f"run_{state}"],
            "analysis": {"ceiling": {"black_dn": BLACK, "ceiling_dn": 1023}},
        }))
        rows.append({"kind": "bracket", "state": state, "band_nm": band,
                     "label": label})
    (session / "brackets.jsonl").write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n")
    return session


def test_bracket_plan_takes_the_roll_that_precedes_each_bracket(tmp_path):
    log = tmp_path / "brackets.jsonl"
    rows = [
        {"kind": "state", "state": "P1", "settled": {"roll_deg": -6.0}},
        {"kind": "bracket", "state": "P1", "band_nm": 450, "label": "P1_450nm"},
        # A resumed session replaces P1 at a slightly different angle.
        {"kind": "state", "state": "P1", "settled": {"roll_deg": -6.8}},
        {"kind": "bracket", "state": "P1", "band_nm": 550, "label": "P1_550nm"},
        {"kind": "bracket", "state": "P2", "band_nm": 450, "label": "P2_450nm"},
    ]
    log.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
    session = tmp_path
    plan = ab.bracket_plan(session)
    assert [item["label"] for item in plan] == ["P1_450nm", "P1_550nm"]
    assert plan[0]["roll_deg"] == -6.0
    assert plan[1]["roll_deg"] == -6.8


def test_subpixel_centre_and_translation_are_separated(tmp_path):
    session = _session(tmp_path, {"P1": 0.0, "P2": -20.0, "P3": -1.0},
                       [0.0, 20.0, 1.0])
    measured = ab.measure_bracket(session, "P2_450nm", plane="Gr")
    assert measured["ok"]
    assert abs(measured["center_xy"][0] - CENTRE[0]) < 0.4
    assert abs(measured["center_xy"][1] - CENTRE[1]) < 0.4
    assert sum(measured["valid_rings"]) >= 2


def test_mount_fixed_structure_gives_a_slope_of_magnitude_one(tmp_path):
    # A structure fixed to the pinhole appears at sensor azimuth theta0 - roll.
    session = _session(tmp_path, {"P1": 0.0, "P2": -20.0, "P3": -1.0},
                       [0.0, 20.0, 1.0])
    rows, _ = ab.pair_rows(session, planes=("Gr",))
    summary = ab.summarise(rows, planes=("Gr",))
    fit = summary["by_plane"]["pooled"]
    assert fit["ok"]
    assert abs(fit["slope"] - 1.0) < 0.15
    assert fit["class"] == "mount_consistent"
    assert summary["null_ok"] is True


def test_display_fixed_structure_gives_zero_slope(tmp_path):
    session = _session(tmp_path, {"P1": 5.0, "P2": 5.0, "P3": 5.0},
                       [0.0, 20.0, 1.0])
    rows, _ = ab.pair_rows(session, planes=("Gr",))
    summary = ab.summarise(rows, planes=("Gr",))
    fit = summary["by_plane"]["pooled"]
    assert fit["ok"]
    assert abs(fit["slope"]) < 0.15
    assert fit["class"] == "display_consistent"


def test_a_translated_spot_does_not_fake_a_rotation(tmp_path):
    # Same azimuth everywhere, but each state sits at a different pixel: the
    # translation must be absorbed by the centre estimate, not read as angle.
    session = _session(tmp_path, {"P1": 0.0, "P2": 0.0, "P3": 0.0},
                       [0.0, 20.0, 1.0])
    for delta, label in (((2.0, -1.5), "P2_450nm"), ((-1.0, 2.0), "P3_450nm")):
        capture = (session / "captures" / label / "batch_000" / "decoded"
                   / "temporal_mean_dn.npy")
        centre = (CENTRE[0] + delta[0], CENTRE[1] + delta[1])
        np.save(capture, _image(0.0, centre=centre))
    rows, _ = ab.pair_rows(session, planes=("Gr",))
    summary = ab.summarise(rows, planes=("Gr",))
    assert abs(summary["by_plane"]["pooled"]["slope"]) < 0.10


def test_a_large_same_angle_bias_is_called_unresolved(tmp_path):
    # The mount returns to P1 but the pattern does not: 25 degrees of angular
    # change with a 0.5 degree roll change is unexplained, so the same-angle
    # pair is biased and the verdict must refuse to name a hypothesis.
    session = _session(tmp_path, {"P1": 0.0, "P2": -20.0, "P3": 25.0},
                       [0.0, 20.0, 0.5])
    rows, _ = ab.pair_rows(session, planes=("Gr",))
    summary = ab.summarise(rows, planes=("Gr",))
    assert summary["null_ok"] is False
    result = ab.verdict(summary)
    assert result["class"] == "unresolved"
    assert "estimator bias" in result["reason"]


def test_a_clipped_core_is_refused(tmp_path):
    session = _session(tmp_path, {"P1": 0.0, "P2": -20.0, "P3": -1.0},
                       [0.0, 20.0, 1.0])
    record_path = session / "P1_450nm.json"
    record = json.loads(record_path.read_text())
    record["analysis"]["ceiling"]["ceiling_dn"] = 700
    record_path.write_text(json.dumps(record))
    measured = ab.measure_bracket(session, "P1_450nm", plane="Gr")
    assert not measured["ok"]
    assert "clipped core" in measured["reason"]


def test_reference_batches_are_averaged(tmp_path):
    session = _session(tmp_path, {"P1": 0.0, "P2": -20.0, "P3": -1.0},
                       [0.0, 20.0, 1.0])
    first = (session / "captures" / "P1_450nm" / "batch_000" / "decoded"
             / "temporal_mean_dn.npy")
    second = session / "captures" / "P1_450nm" / "batch_002" / "decoded"
    second.mkdir(parents=True)
    rng = np.random.default_rng(3)
    noisy = np.load(first) + rng.normal(0.0, 20.0, SHAPE)
    np.save(second / "temporal_mean_dn.npy", noisy)
    record_path = session / "P1_450nm.json"
    record = json.loads(record_path.read_text())
    record["reference_batches"] = [0, 2]
    record_path.write_text(json.dumps(record))
    measured = ab.measure_bracket(session, "P1_450nm", plane="Gr")
    assert measured["batches"] == [0, 2]
    assert measured["ok"]
    # The average of the clean frame and a noisy copy stays near the clean one.
    assert abs(measured["center_xy"][0] - CENTRE[0]) < 0.3
    assert abs(measured["center_xy"][1] - CENTRE[1]) < 0.3


def test_a_bracket_without_a_temporal_mean_is_not_usable(tmp_path):
    session = _session(tmp_path, {"P1": 0.0, "P2": -20.0, "P3": -1.0},
                       [0.0, 20.0, 1.0])
    (session / "captures" / "P2_450nm" / "batch_000" / "decoded"
     / "temporal_mean_dn.npy").unlink()
    measured = ab.measure_bracket(session, "P2_450nm", plane="Gr")
    assert not measured["ok"]
    assert "missing" in measured["reason"]
    rows, _ = ab.pair_rows(session, planes=("Gr",))
    touching_p2 = [row for row in rows if "P2" in (row["state_a"], row["state_b"])]
    assert touching_p2 and all(not row["ok"] for row in touching_p2)
    # Only the same-angle pair is left, so no slope can be fitted at all.
    assert ab.verdict(ab.summarise(rows, planes=("Gr",)))["class"] == "unresolved"
