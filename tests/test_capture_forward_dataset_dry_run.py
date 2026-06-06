from __future__ import annotations

import json
from pathlib import Path

import h5py
import numpy as np
import pytest

from tasks.capture_plan import CapturePlan, CapturePlanError
from tasks.capture_forward_dataset import run_capture_forward_dataset
from tasks.testing import (
    FakeCamera,
    FakeDeviceBundle,
    FakeLCD,
    FakeTLS,
)


def _mono_entry(wavelength_nm: float, **extra) -> dict:
    return {
        "illumination": {
            "mode": "monochromatic",
            "effective_wavelength_nm": float(wavelength_nm),
            "tls_setpoint_nm": float(wavelength_nm),
        },
        **extra,
    }


def _label_entry(wavelength_nm: float, **extra) -> dict:
    return {
        "illumination": {
            "mode": "monochromatic",
            "effective_wavelength_nm": float(wavelength_nm),
            "tls_setpoint_nm": None,
        },
        **extra,
    }


def _pass_entry(**extra) -> dict:
    return {
        "illumination": {
            "mode": "broadband_passthrough",
            "effective_wavelength_nm": None,
            "tls_setpoint_nm": 0.0,
        },
        **extra,
    }


def _h5_str(dset) -> str:
    val = dset[()]
    if isinstance(val, bytes):
        return val.decode()
    if isinstance(val, np.ndarray):
        val = val.flat[0]
        if isinstance(val, bytes):
            return val.decode()
        return str(val)
    return str(val)


class TestCaptureForwardDatasetDryRun:
    def test_dry_run_produces_valid_hdf5(
        self, sample_plan: CapturePlan, tmp_h5_path: Path
    ) -> None:
        devices = FakeDeviceBundle(
            camera=FakeCamera(height=480, width=640),
            lcd=FakeLCD(height=1080, width_phys=5760),
            tls=None,
        )

        result = run_capture_forward_dataset(
            plan=sample_plan,
            devices=devices,
            output_path=tmp_h5_path,
            enable_tls=False,
            dry_run=True,
        )

        assert result == tmp_h5_path
        assert tmp_h5_path.exists()

        with h5py.File(tmp_h5_path, "r") as f:
            assert "raw/frames_avg" in f
            assert f["raw/frames_avg"].shape[0] == 4

            pf = _h5_str(f["capture/processing_flags_json"])
            assert "completed" in pf

            assert f["capture/capture_index"][0] >= 0
            assert f["capture/completed"][:].all()

    def test_dry_run_with_tls(
        self, tmp_h5_path: Path
    ) -> None:
        plan = CapturePlan.from_dict({
            "plan_id": "tls_dry_run",
            "wavelengths": [
                _mono_entry(532.0, grating=1, settle_ms=3000),
                _mono_entry(633.0, grating=1, settle_ms=2000),
            ],
            "masks": [
                {"mask_id": "mask_a", "family_id": "f1"},
                {"mask_id": "mask_b", "family_id": "f1"},
            ],
            "camera": {"frames_per_capture": 5},
            "lcd_settle_ms": 500,
            "store_burst": False,
        })
        tls = FakeTLS()
        tls.connect()

        devices = FakeDeviceBundle(
            camera=FakeCamera(height=480, width=640),
            lcd=FakeLCD(height=1080, width_phys=5760),
            tls=tls,
        )

        result = run_capture_forward_dataset(
            plan=plan,
            devices=devices,
            output_path=tmp_h5_path,
            enable_tls=True,
            dry_run=True,
        )

        assert result == tmp_h5_path
        with h5py.File(tmp_h5_path, "r") as f:
            illum_raw = f["illumination/illumination_json"][0]
            illum = json.loads(
                illum_raw.decode("utf-8") if isinstance(illum_raw, bytes) else str(illum_raw)
            )
            assert illum["effective_wavelength_nm"] > 0

    def test_dry_run_with_tls_pass_through(
        self, tmp_h5_path: Path
    ) -> None:
        plan = CapturePlan.from_dict({
            "plan_id": "pass_through_test",
            "wavelengths": [_pass_entry()],
            "masks": [{"mask_id": "m1"}],
            "camera": {"frames_per_capture": 1},
        })
        tls = FakeTLS()
        tls.connect()

        devices = FakeDeviceBundle(
            camera=FakeCamera(height=240, width=320),
            lcd=FakeLCD(height=600, width_phys=2400),
            tls=tls,
        )

        run_capture_forward_dataset(
            plan=plan,
            devices=devices,
            output_path=tmp_h5_path,
            enable_tls=True,
            dry_run=True,
        )

        assert tls._target_nm == 0.0
        assert tls._current_nm == 0.0
        with h5py.File(tmp_h5_path, "r") as f:
            assert float(f["illumination"]["tls_setpoint_nm"][0]) == 0.0
            raw_status = f["tls/status_json"][0]
            status = json.loads(raw_status.decode() if isinstance(raw_status, bytes) else str(raw_status))
            assert float(status["target_wavelength_nm"]) == 0.0

    def test_dry_run_writes_correct_capture_count(
        self, sample_plan: CapturePlan, tmp_h5_path: Path
    ) -> None:
        devices = FakeDeviceBundle(
            camera=FakeCamera(height=480, width=640),
            lcd=FakeLCD(height=1080, width_phys=5760),
        )
        run_capture_forward_dataset(
            plan=sample_plan,
            devices=devices,
            output_path=tmp_h5_path,
            enable_tls=False,
            dry_run=True,
        )

        with h5py.File(tmp_h5_path, "r") as f:
            assert f["capture/capture_index"][-1] == 3
            for i in range(4):
                assert bool(f["capture/completed"][i]) is True

    def test_dry_run_mask_shapes_preserved(
        self, sample_plan: CapturePlan, tmp_h5_path: Path,
        mono_mask_arrays: list[np.ndarray]
    ) -> None:
        plan_dict = sample_plan.to_dict()
        plan_dict["masks"][0]["array"] = mono_mask_arrays[0]
        plan_dict["masks"][1]["array"] = mono_mask_arrays[1]
        plan = CapturePlan.from_dict(plan_dict)

        devices = FakeDeviceBundle(
            camera=FakeCamera(height=480, width=640),
            lcd=FakeLCD(height=1080, width_phys=5760),
        )

        run_capture_forward_dataset(
            plan=plan,
            devices=devices,
            output_path=tmp_h5_path,
            enable_tls=False,
            dry_run=True,
        )

        with h5py.File(tmp_h5_path, "r") as f:
            dset = f["masks/masks_physical"]
            assert dset.shape[1] == 1080
            assert dset.shape[2] == 5760

    def test_tls_unavailable_error(
        self, sample_plan: CapturePlan, tmp_h5_path: Path
    ) -> None:
        from tasks.capture_forward_dataset import TLSUnavailableError

        devices = FakeDeviceBundle(tls=None)

        with pytest.raises(TLSUnavailableError):
            run_capture_forward_dataset(
            plan=sample_plan,
                devices=devices,
                output_path=tmp_h5_path,
                enable_tls=True,
                dry_run=True,
            )

    def test_dry_run_no_hardware_imports(self) -> None:
        import sys
        fake_modules = {
            "devices.camera_service": None,
            "devices.frame_stream": None,
            "devices.lcd_backend": None,
            "capture.frame_capture": None,
        }
        for mod in fake_modules:
            sys.modules.pop(mod, None)

        try:
            from tasks.capture_forward_dataset import run_capture_forward_dataset
            from tasks.testing import FakeDeviceBundle
        except ImportError as e:
            pytest.fail(f"imports should be hardware-free: {e}")

    def test_index_table_correct(
        self, sample_plan: CapturePlan, tmp_h5_path: Path
    ) -> None:
        devices = FakeDeviceBundle(
            camera=FakeCamera(height=240, width=320),
            lcd=FakeLCD(height=600, width_phys=2400),
        )

        run_capture_forward_dataset(
            plan=sample_plan,
            devices=devices,
            output_path=tmp_h5_path,
            enable_tls=False,
            dry_run=True,
        )

        with h5py.File(tmp_h5_path, "r") as f:
            ci = f["capture/capture_index"][:]
            wi = f["capture/wavelength_index"][:]
            mi = f["capture/mask_index"][:]

            for idx in range(4):
                assert ci[idx] == idx
                assert wi[idx] == idx // 2
                assert mi[idx] == idx % 2

    def test_store_burst_writes_raw_frames(
        self, tmp_h5_path: Path
    ) -> None:
        plan = CapturePlan.from_dict({
            "plan_id": "burst_frames_test",
            "wavelengths": [_label_entry(500)],
            "masks": [{"mask_id": "m1"}],
            "camera": {"frames_per_capture": 3},
            "store_burst": True,
        })

        devices = FakeDeviceBundle(
            camera=FakeCamera(height=240, width=320, seed=1),
            lcd=FakeLCD(height=600, width_phys=2400),
        )

        run_capture_forward_dataset(
            plan=plan,
            devices=devices,
            output_path=tmp_h5_path,
            enable_tls=False,
            dry_run=True,
        )

        with h5py.File(tmp_h5_path, "r") as f:
            assert "raw/frames" in f
            dset = f["raw/frames"]
            assert int(dset.shape[0]) == 1
            assert int(dset.shape[1]) == 3
            assert int(dset.shape[2]) == 240
            assert int(dset.shape[3]) == 320

            assert "raw/frames_avg" in f
            avg_dset = f["raw/frames_avg"]
            assert int(avg_dset.shape[1]) == 240
            assert int(avg_dset.shape[2]) == 320

    def test_writes_2d_avg_when_store_burst_false(
        self, tmp_h5_path: Path
    ) -> None:
        plan = CapturePlan.from_dict({
            "plan_id": "noburst_avg_test",
            "wavelengths": [_label_entry(500)],
            "masks": [{"mask_id": "m1"}],
            "camera": {"frames_per_capture": 3},
            "store_burst": False,
        })

        devices = FakeDeviceBundle(
            camera=FakeCamera(height=240, width=320, seed=2),
            lcd=FakeLCD(height=600, width_phys=2400),
        )

        run_capture_forward_dataset(
            plan=plan,
            devices=devices,
            output_path=tmp_h5_path,
            enable_tls=False,
            dry_run=True,
        )

        with h5py.File(tmp_h5_path, "r") as f:
            avg_dset = f["raw/frames_avg"]
            assert int(avg_dset.shape[1]) == 240
            assert int(avg_dset.shape[2]) == 320

    def test_tls_grating_is_set(
        self, tmp_h5_path: Path
    ) -> None:
        plan = CapturePlan.from_dict({
            "plan_id": "grating_test",
            "wavelengths": [_mono_entry(532, grating=3)],
            "masks": [{"mask_id": "m1"}],
            "camera": {"frames_per_capture": 1},
        })

        tls = FakeTLS()
        tls.connect()

        devices = FakeDeviceBundle(
            camera=FakeCamera(height=240, width=320),
            lcd=FakeLCD(height=600, width_phys=2400),
            tls=tls,
        )

        run_capture_forward_dataset(
            plan=plan,
            devices=devices,
            output_path=tmp_h5_path,
            enable_tls=True,
            dry_run=True,
        )

        assert tls._grating == 3

    def test_optional_run_status_written(
        self,
        sample_plan: CapturePlan,
        tmp_h5_path: Path,
        tmp_path: Path,
    ) -> None:
        from diagnostics.run_status import RunStatusReader

        devices = FakeDeviceBundle(
            camera=FakeCamera(height=240, width=320),
            lcd=FakeLCD(height=1080, width_phys=5760),
        )
        status_dir = tmp_path / "run_status" / "dry_run_001"

        run_capture_forward_dataset(
            plan=sample_plan,
            devices=devices,
            output_path=tmp_h5_path,
            enable_tls=False,
            dry_run=True,
            status_dir=status_dir,
            run_id="dry_run_001",
        )

        status = RunStatusReader(status_dir).read()
        assert status is not None
        assert status.run_id == "dry_run_001"
        assert status.plan_id == sample_plan.plan_id
        assert status.phase == "completed"
        assert status.capture_index == sample_plan.n_captures
        assert status.completed is True
        assert status.error is None

    def test_hardware_materialization_rejects_missing_mask(
        self, sample_plan: CapturePlan, tmp_h5_path: Path
    ) -> None:
        from tasks.capture_forward_dataset import _materialize_masks

        with pytest.raises(CapturePlanError, match="placeholder"):
            _materialize_masks(sample_plan, allow_placeholder=False,
                              placeholder_shape=(60, 180))

    def test_wrong_mask_shape_rejected(
        self, tmp_h5_path: Path
    ) -> None:
        plan = CapturePlan.from_dict({
            "plan_id": "shape_test",
            "wavelengths": [_label_entry(500)],
            "masks": [{"mask_id": "bad_shape", "array": np.zeros((100, 200), dtype=np.uint8)}],
            "camera": {"frames_per_capture": 1},
        })

        devices = FakeDeviceBundle(
            camera=FakeCamera(height=240, width=320),
            lcd=FakeLCD(height=600, width_phys=2400),
        )

        with pytest.raises(CapturePlanError, match="shape"):
            run_capture_forward_dataset(
                plan=plan,
                devices=devices,
                output_path=tmp_h5_path,
                enable_tls=False,
                dry_run=True,
            )

    def test_camera_params_applied_and_stored_in_hdf5(
        self, tmp_h5_path: Path
    ) -> None:
        plan = CapturePlan.from_dict({
            "plan_id": "camera_params_test",
            "wavelengths": [_label_entry(500)],
            "masks": [{"mask_id": "m1"}],
            "camera": {
                "frames_per_capture": 2,
                "exposure_us": 5000.0,
                "gain_db": 3.0,
            },
        })

        cam = FakeCamera(height=240, width=320)
        devices = FakeDeviceBundle(
            camera=cam,
            lcd=FakeLCD(height=600, width_phys=2400),
        )

        run_capture_forward_dataset(
            plan=plan,
            devices=devices,
            output_path=tmp_h5_path,
            enable_tls=False,
            dry_run=True,
        )

        assert cam.exposure_us == 5000.0
        assert cam.gain_db == 3.0

        with h5py.File(tmp_h5_path, "r") as f:
            req_exp = f["camera/requested_exposure_us"]
            req_gain = f["camera/requested_gain_db"]
            rb_exp = f["camera/readback_exposure_us"]
            rb_gain = f["camera/readback_gain_db"]

            assert float(req_exp[0]) == 5000.0
            assert float(req_gain[0]) == 3.0
            assert float(rb_exp[0]) == 5000.0
            assert float(rb_gain[0]) == 3.0

            assert "requested_exposure_us" in f["camera"]
            assert "requested_gain_db" in f["camera"]
            assert "readback_exposure_us" in f["camera"]
            assert "readback_gain_db" in f["camera"]

    def test_camera_params_not_applied_when_none(
        self, tmp_h5_path: Path
    ) -> None:
        plan = CapturePlan.from_dict({
            "plan_id": "no_camera_params_test",
            "wavelengths": [_label_entry(500)],
            "masks": [{"mask_id": "m1"}],
            "camera": {"frames_per_capture": 2},
        })

        cam = FakeCamera(height=240, width=320)
        devices = FakeDeviceBundle(
            camera=cam,
            lcd=FakeLCD(height=600, width_phys=2400),
        )

        run_capture_forward_dataset(
            plan=plan,
            devices=devices,
            output_path=tmp_h5_path,
            enable_tls=False,
            dry_run=True,
        )

        with h5py.File(tmp_h5_path, "r") as f:
            assert float(f["camera/requested_exposure_us"][0]) == -1.0
            assert float(f["camera/readback_exposure_us"][0]) == -1.0
