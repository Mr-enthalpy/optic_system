from __future__ import annotations

import json
from pathlib import Path

from scripts.calibrate_psf_safe_exposure import run_psf_safe_exposure


def _psf_safe_plan(tmp_path: Path, *, wavelengths=None) -> dict:
    if wavelengths is None:
        wavelengths = [{"wavelength_nm": 550.0}]
    return {
        "plan_id": "test_psf_status",
        "wavelengths": wavelengths,
        "lcd": {
            "mode": "all_transmissive",
            "settle_ms": 0,
            "display_index": -1,
        },
        "camera_search": {
            "exposure_us_start": 10000.0,
            "exposure_us_min": 1000.0,
            "exposure_us_step_factor": 0.5,
            "gain_db_min": 0.0,
            "gain_db_max": 18.0,
            "gain_db_step_db": 6.0,
            "frames_per_setting": 3,
        },
        "psf_safety": {},
        "signal": {
            "percentile": 99.0,
            "min_signal_fraction_threshold": 0.05,
            "min_dynamic_range_fraction": 0.02,
        },
        "output": {
            "raw_h5": str(tmp_path / "exposure_sweep.h5"),
            "camera_params_json": str(tmp_path / "camera_params_psf_safe.json"),
        },
        "lock_file": str(tmp_path / "lock.lock"),
    }


def test_dry_run_writes_status_when_status_dir_specified(tmp_path: Path) -> None:
    plan = _psf_safe_plan(tmp_path)
    status_dir = tmp_path / "status"

    h5_path, result = run_psf_safe_exposure(
        plan, None, None, None, dry_run=True, status_dir=status_dir,
    )

    assert h5_path.exists()
    assert (status_dir / "state.json").exists()
    state = json.loads((status_dir / "state.json").read_text(encoding="utf-8"))
    assert state["plan_id"] == plan["plan_id"]
    assert state["phase"] == "3.0.5b"


def test_dry_run_writes_log_jsonl(tmp_path: Path) -> None:
    plan = _psf_safe_plan(tmp_path)
    status_dir = tmp_path / "status"

    run_psf_safe_exposure(plan, None, None, None, dry_run=True, status_dir=status_dir)

    assert (status_dir / "log.jsonl").exists()
    logs = (status_dir / "log.jsonl").read_text(encoding="utf-8").strip().split("\n")
    assert len(logs) >= 1


def test_dry_run_status_marks_failed_when_no_safe_setting(tmp_path: Path) -> None:
    """FakeCamera with exposure_us_start=10000, gain_db_min=0 saturates all
    wavelengths at gain_min; the sweep finds no safe setting."""
    plan = _psf_safe_plan(tmp_path)
    status_dir = tmp_path / "status"

    run_psf_safe_exposure(plan, None, None, None, dry_run=True, status_dir=status_dir)

    state = json.loads((status_dir / "state.json").read_text(encoding="utf-8"))
    assert state["completed"] is False
    assert state["error"] is not None


def test_dry_run_no_status_dir_produces_no_files(tmp_path: Path) -> None:
    plan = _psf_safe_plan(tmp_path)
    status_dir = tmp_path / "status"

    run_psf_safe_exposure(plan, None, None, None, dry_run=True, status_dir=None)

    assert not (status_dir / "state.json").exists()


def test_dry_run_writes_frame_stats(tmp_path: Path) -> None:
    plan = _psf_safe_plan(tmp_path)
    status_dir = tmp_path / "status"

    run_psf_safe_exposure(plan, None, None, None, dry_run=True, status_dir=status_dir)

    assert (status_dir / "frame_stats.json").exists()
    stats = json.loads((status_dir / "frame_stats.json").read_text(encoding="utf-8"))
    assert "peak_pixel_burst" in stats
    assert "p_signal" in stats
    assert "psf_safe" in stats
    assert "saturated_fraction" not in stats
    assert "saturated_pixel_count" not in stats
    assert "max_pixel" not in stats
