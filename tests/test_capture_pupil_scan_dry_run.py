from __future__ import annotations

import builtins
import json
from pathlib import Path

import h5py
import pytest
import yaml

from scripts.capture_pupil_scan import load_pupil_scan_plan, run_pupil_scan


def _camera_params(path: Path) -> Path:
    payload = {
        "schema_version": "1.0",
        "plan_id": "bishe_exposure_sweep",
        "source_raw_capture_h5": "data/raw/bishe_exposure_sweep.h5",
        "frame_dtype_full_scale": 255,
        "global_safe_camera": {
            "exposure_us": 50000.0,
            "gain_db": 0.0,
            "frames_per_capture": 3,
            "roi": None,
        },
        "validity": {
            "exposure_safety_valid": True,
            "scientific_calibration_valid": False,
            "training_ready": False,
        },
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _plan(tmp_path: Path, camera_params_path: Path) -> dict:
    return {
        "plan_id": "dry_pupil",
        "camera_params_source": str(camera_params_path),
        "wavelength": {"wavelength_nm": 550.0, "grating": 1, "settle_ms": 0},
        "lcd": {
            "settle_ms": 0,
            "mode": "procedural_scan",
            "physical_shape": [24, 72],
            "subpixel_axis": 1,
        },
        "scan": {
            "scan_modes": ["bars_x", "bars_y", "blocks"],
            "active_code": 255,
            "background_code": 0,
            "bar_count": 4,
            "block_rows": 3,
            "block_cols": 4,
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


def test_dry_run_produces_valid_raw_h5(tmp_path: Path) -> None:
    params = _camera_params(tmp_path / "camera_params.json")
    plan = _plan(tmp_path, params)

    out = run_pupil_scan(plan, dry_run=True)

    assert out.exists()
    with h5py.File(out, "r") as f:
        assert f["raw/frames_avg"].shape[0] == 2 + 4 + 4 + 12
        assert f["scan/mask_id"].shape[0] == f["raw/frames_avg"].shape[0]
        assert f["scan/mask_recipe_json"].shape[0] == f["raw/frames_avg"].shape[0]
        assert "masks" not in f


def test_camera_params_source_is_required(tmp_path: Path) -> None:
    plan = _plan(tmp_path, tmp_path / "camera_params.json")
    del plan["camera_params_source"]
    path = tmp_path / "plan.yaml"
    path.write_text(yaml.safe_dump(plan), encoding="utf-8")

    with pytest.raises(ValueError, match="camera_params_source"):
        load_pupil_scan_plan(path)


def test_camera_params_are_copied_into_h5_provenance(tmp_path: Path) -> None:
    params = _camera_params(tmp_path / "camera_params.json")
    plan = _plan(tmp_path, params)

    out = run_pupil_scan(plan, dry_run=True)

    with h5py.File(out, "r") as f:
        raw = f["camera/camera_params_source_json"][()]
        if isinstance(raw, bytes):
            raw = raw.decode()
        provenance = json.loads(raw)
        assert provenance["source"] == str(params)
        assert provenance["overridden"] is False
        assert provenance["camera_params"]["frame_dtype_full_scale"] == 255
        assert f["camera/exposure_us"][()] == 50000.0
        assert f["camera/gain_db"][()] == 0.0
        assert f["camera/frame_dtype_full_scale"][()] == 255


def test_dry_run_requires_no_hardware_imports(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    params = _camera_params(tmp_path / "camera_params.json")
    plan = _plan(tmp_path, params)
    original_import = builtins.__import__

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name.startswith("devices") or name.startswith("capture"):
            raise AssertionError(f"dry-run imported hardware module {name}")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    out = run_pupil_scan(plan, dry_run=True)
    assert out.exists()


def test_store_physical_masks_option(tmp_path: Path) -> None:
    params = _camera_params(tmp_path / "camera_params.json")
    plan = _plan(tmp_path, params)

    out = run_pupil_scan(plan, dry_run=True, store_physical_masks=True)

    with h5py.File(out, "r") as f:
        assert f["masks/masks_physical"].shape[0] == f["raw/frames_avg"].shape[0]
