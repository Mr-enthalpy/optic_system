from __future__ import annotations

import json
from pathlib import Path

import yaml

from scripts.capture_pupil_scan import run_pupil_scan


def _camera_params(path: Path) -> Path:
    payload = {
        "schema_version": "1.0",
        "plan_id": "bishe_psf_safe_exposure",
        "source_raw_capture_h5": "data/raw/bishe_psf_safe_exposure.h5",
        "frame_dtype_full_scale": 255,
        "global_safe_camera": {
            "exposure_us": 50000.0,
            "gain_db": 0.0,
            "frames_per_capture": 3,
            "roi": None,
        },
        "validity": {
            "exposure_safety_valid": True,
            "psf_exposure_safe": True,
            "scientific_calibration_valid": False,
            "training_ready": False,
        },
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _plan(tmp_path: Path, camera_params_path: Path) -> dict:
    return {
        "plan_id": "status_test",
        "camera_params_source": str(camera_params_path),
        "wavelength": {"wavelength_nm": 550.0, "grating": 1, "settle_ms": 0},
        "lcd": {
            "settle_ms": 0,
            "mode": "procedural_scan",
            "physical_shape": [24, 72],
            "subpixel_axis": 1,
        },
        "scan": {
            "scan_modes": ["bars_x", "blocks"],
            "active_code": 255,
            "background_code": 0,
            "bar_count": 4,
            "block_rows": 2,
            "block_cols": 2,
            "include_baselines": True,
            "store_physical_masks": False,
        },
        "camera": {"frames_per_capture": None},
        "analysis_hint": {"response_metric": "robust_energy"},
        "lock_file": str(tmp_path / "capture.lock"),
        "output": {
            "raw_h5": str(tmp_path / "pupil.h5"),
            "output_dir": str(tmp_path / "pupil_out"),
        },
    }


def test_dry_run_writes_status_when_status_dir_specified(tmp_path: Path) -> None:
    params = _camera_params(tmp_path / "camera_params.json")
    plan = _plan(tmp_path, params)
    status_dir = tmp_path / "status"

    out = run_pupil_scan(plan, dry_run=True, status_dir=status_dir)

    assert out.exists()
    assert (status_dir / "state.json").exists()
    state = json.loads((status_dir / "state.json").read_text(encoding="utf-8"))
    assert state["run_id"] == "status_test"
    assert state["plan_id"] == "status_test"
    assert state["phase"] == "3.1"
    assert state["completed"] is True
    assert state["error"] is None
    assert state["capture_index"] > 0


def test_dry_run_writes_log_jsonl(tmp_path: Path) -> None:
    params = _camera_params(tmp_path / "camera_params.json")
    plan = _plan(tmp_path, params)
    status_dir = tmp_path / "status"

    run_pupil_scan(plan, dry_run=True, status_dir=status_dir)

    assert (status_dir / "log.jsonl").exists()
    logs = (status_dir / "log.jsonl").read_text(encoding="utf-8").strip().split("\n")
    assert len(logs) >= 1
    first = json.loads(logs[0])
    assert "pupil scan started" in first["message"]


def test_dry_run_status_marks_completed(tmp_path: Path) -> None:
    params = _camera_params(tmp_path / "camera_params.json")
    plan = _plan(tmp_path, params)
    status_dir = tmp_path / "status"

    run_pupil_scan(plan, dry_run=True, status_dir=status_dir)

    state = json.loads((status_dir / "state.json").read_text(encoding="utf-8"))
    assert state["completed"] is True


def test_dry_run_no_status_dir_produces_no_files(tmp_path: Path) -> None:
    params = _camera_params(tmp_path / "camera_params.json")
    plan = _plan(tmp_path, params)

    out = run_pupil_scan(plan, dry_run=True, status_dir=None)

    assert out.exists()


def test_dry_run_writes_frame_stats(tmp_path: Path) -> None:
    params = _camera_params(tmp_path / "camera_params.json")
    plan = _plan(tmp_path, params)
    status_dir = tmp_path / "status"

    run_pupil_scan(plan, dry_run=True, status_dir=status_dir)

    assert (status_dir / "frame_stats.json").exists()
    stats = json.loads((status_dir / "frame_stats.json").read_text(encoding="utf-8"))
    assert "max_pixel" in stats
    assert "p99_9" in stats
    assert "shape" in stats
