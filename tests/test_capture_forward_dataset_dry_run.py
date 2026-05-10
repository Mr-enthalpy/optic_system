from __future__ import annotations

from pathlib import Path

import h5py
import numpy as np
import pytest

from tasks.capture_plan import CapturePlan
from tasks.capture_forward_dataset import (
    FakeCamera,
    FakeDeviceBundle,
    FakeLCD,
    FakeTLS,
    run_capture_forward_dataset,
)


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
        self, sample_plan: CapturePlan, tmp_h5_path: Path
    ) -> None:
        tls = FakeTLS()
        tls.connect()

        devices = FakeDeviceBundle(
            camera=FakeCamera(height=480, width=640),
            lcd=FakeLCD(height=1080, width_phys=5760),
            tls=tls,
        )

        result = run_capture_forward_dataset(
            plan=sample_plan,
            devices=devices,
            output_path=tmp_h5_path,
            enable_tls=True,
            dry_run=True,
        )

        assert result == tmp_h5_path
        with h5py.File(tmp_h5_path, "r") as f:
            wl = f["tls/wavelength_nm"][()]
            assert wl[0] > 0

    def test_dry_run_writes_correct_capture_count(
        self, sample_plan: CapturePlan, tmp_h5_path: Path
    ) -> None:
        devices = FakeDeviceBundle()
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
            camera=FakeCamera(),
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
            from tasks.capture_plan import CapturePlan
            from tasks.capture_forward_dataset import (
                FakeDeviceBundle,
                run_capture_forward_dataset,
            )
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
