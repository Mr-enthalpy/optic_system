from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

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
        "psf_safety": {"rule": "all_frames_all_pixels_strictly_below_full_scale"},
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


def test_dry_run_status_marks_failed_when_no_safe_setting(tmp_path: Path, monkeypatch):
    plan = _psf_safe_plan(tmp_path)
    plan["camera_search"]["exposure_us_min"] = 10000.0
    plan["camera_search"]["exposure_us_start"] = 10000.0
    plan["camera_search"]["gain_db_min"] = 0.0
    plan["camera_search"]["gain_db_max"] = 0.0

    class _FailingFakeCamera:
        def apply_camera_params(self, exposure_us=None, gain_db=None): pass
        def acquire_burst(self, k: int):
            burst = np.full((k, 4, 4), 260.0, dtype=np.float64)
            avg = burst.mean(axis=0, dtype=np.float64)
            return SimpleNamespace(
                frames_avg=avg, burst=burst,
                metadata={"frame_dtype_full_scale": 255},
            )

    monkeypatch.setattr(
        "scripts.calibrate_psf_safe_exposure._make_fake_adapter",
        lambda: _FailingFakeCamera(),
    )

    status_dir = tmp_path / "status"

    run_psf_safe_exposure(plan, None, None, None, dry_run=True, status_dir=status_dir)

    state = json.loads((status_dir / "state.json").read_text(encoding="utf-8"))
    assert state["completed"] is False
    assert state["error"] is not None
    assert state["latest_frame_preview"] is not None
    assert (status_dir / state["latest_frame_preview"]).exists()
    assert state["frame_stats"] == "frame_stats.json"
    stats = json.loads((status_dir / "frame_stats.json").read_text(encoding="utf-8"))
    assert stats["preview_kind"] == "bound_search"
    assert stats["psf_safe"] is False

    import h5py

    with h5py.File(plan["output"]["raw_h5"], "r") as f:
        assert f["sweep/exposure_us"].shape[0] >= 1
        assert bool(f["sweep/psf_safe"][0]) is False


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


def test_hardware_mode_without_tls_fails_by_default(tmp_path: Path) -> None:
    plan = _psf_safe_plan(
        tmp_path,
        wavelengths=[
            {"wavelength_nm": 450.0},
            {"wavelength_nm": 550.0},
        ],
    )
    status_dir = tmp_path / "status"

    with pytest.raises(RuntimeError, match="TLS service is required"):
        run_psf_safe_exposure(
            plan,
            camera_service=None,
            lcd_service=None,
            tls_service=None,
            dry_run=False,
            status_dir=status_dir,
        )

    state = json.loads((status_dir / "state.json").read_text(encoding="utf-8"))
    assert state["completed"] is False
    assert "cannot prove cross-wavelength safety" in state["error"]


def test_tls_moves_once_per_wavelength_not_per_candidate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wavelengths = [
        {"wavelength_nm": 450.0, "grating": 1, "settle_ms": 0},
        {"wavelength_nm": 550.0, "grating": 1, "settle_ms": 0},
        {"wavelength_nm": 650.0, "grating": 1, "settle_ms": 0},
    ]
    plan = _psf_safe_plan(tmp_path, wavelengths=wavelengths)
    plan["camera_search"].update({
        "exposure_us_start": 10000.0,
        "exposure_us_min": 2500.0,
        "exposure_us_step_factor": 0.5,
        "gain_db_min": 0.0,
        "gain_db_max": 0.0,
        "gain_db_step_db": 6.0,
        "frames_per_setting": 2,
    })

    class FakeFrameStreamClient:
        def __init__(self, recv_timeout_ms: int):
            self.recv_timeout_ms = recv_timeout_ms

    class FakeFrameCaptureHelper:
        def __init__(self, frame_stream):
            self.frame_stream = frame_stream

    class FakeCameraService:
        def __init__(self):
            self.apply_params_call_count = 0
            self.started = False

        def start_stream(self):
            self.started = True

        def stop_stream(self):
            self.started = False

    class FakeCameraAdapter:
        def __init__(self, capture_helper, camera_service):
            self._camera = camera_service

        def apply_camera_params(self, exposure_us=None, gain_db=None):
            self._camera.apply_params_call_count += 1

        def acquire_burst(self, k: int):
            frame = np.linspace(10.0, 100.0, 16, dtype=np.float64).reshape(4, 4)
            burst = np.stack([frame for _ in range(k)], axis=0)
            return SimpleNamespace(
                frames_avg=frame,
                burst=burst,
                metadata={"frame_dtype_full_scale": 255},
            )

    class FakeLCD:
        def make_all_transmissive_mask(self):
            return np.full((4, 12), 255, dtype=np.uint8)

        def show_mono_mask(self, mask, *, mask_id=None, mode="mono_mask"):
            return np.zeros((4, 4, 3), dtype=np.uint8)

        def get_metadata(self):
            return {
                "display_index": 1,
                "logical_shape": (4, 4),
                "physical_shape": (4, 12),
                "subpixel_axis": 1,
            }

    class FakeTLS:
        def __init__(self):
            self.move_call_count = 0
            self.wait_call_count = 0
            self.set_wavelength_calls: list[float] = []
            self.grating = None
            self.target = None

        def set_grating(self, grating: int):
            self.grating = int(grating)

        def set_wavelength_nm(self, wavelength_nm: float):
            self.target = float(wavelength_nm)
            self.set_wavelength_calls.append(self.target)

        def move(self, timeout_s: float = 60.0):
            self.move_call_count += 1
            return self.get_status()

        def wait_until_idle(self, timeout_s: float = 60.0):
            self.wait_call_count += 1
            return self.get_status()

        def get_status(self):
            return SimpleNamespace(
                current_wavelength_nm=self.target,
                target_wavelength_nm=self.target,
                grating=self.grating,
                moving=False,
            )

    monkeypatch.setattr(
        "devices.frame_stream.FrameStreamClient",
        FakeFrameStreamClient,
    )
    monkeypatch.setattr(
        "capture.frame_capture.FrameCaptureHelper",
        FakeFrameCaptureHelper,
    )
    monkeypatch.setattr(
        "tasks.capture_forward_dataset.CameraCaptureAdapter",
        FakeCameraAdapter,
    )

    fake_camera = FakeCameraService()
    fake_tls = FakeTLS()

    run_psf_safe_exposure(
        plan,
        camera_service=fake_camera,
        lcd_service=FakeLCD(),
        tls_service=fake_tls,
        dry_run=False,
    )

    exposure_candidates = [10000.0, 5000.0, 2500.0]
    assert fake_tls.move_call_count == 2 * len(wavelengths)
    assert fake_tls.wait_call_count == 2 * len(wavelengths)
    assert fake_tls.set_wavelength_calls == [450.0, 550.0, 650.0, 450.0, 550.0, 650.0]
    assert fake_camera.apply_params_call_count >= len(wavelengths)
