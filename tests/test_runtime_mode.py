from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import h5py
import numpy as np
import pytest

from tasks.capture_forward_dataset import (
    FakeCamera,
    FakeDeviceBundle,
    FakeLCD,
    FakeTLS,
    run_capture_forward_dataset,
)
from tasks.capture_plan import CapturePlan
from tasks.illumination import IlluminationSpec, illumination_from_legacy_wavelength_nm
from tasks.profiles import BroadbandCameraCalibrationPlan, calibrate_broadband_camera_profile
from tasks.runtime_mode import (
    RuntimeModeError,
    diagnostic_runtime_policy,
    hardware_runtime_policy,
    no_hardware_runtime_policy,
    validate_no_fake_devices,
    validate_raw_fallback_allowed,
    validate_required_devices,
    validate_tls_for_illumination,
)


class DummyCamera:
    def acquire_burst(self, k: int):
        burst = np.ones((int(k), 4, 5), dtype=np.float64)
        return SimpleNamespace(
            burst=burst,
            frames_avg=burst.mean(axis=0),
            metadata={"frame_shape": [4, 5]},
        )


class DummyLCD:
    def show_physical_mask(self, mask, *, mask_id=None):
        pass

    def metadata(self):
        return {"physical_shape": [4, 12], "subpixel_axis": 1}

    def physical_shape(self):
        return (4, 12)

    def subpixel_axis(self):
        return 1


def _plan_dict() -> dict:
    return {
        "plan_id": "runtime_mode_capture",
        "wavelengths": [{"wavelength_nm": 532.0}],
        "masks": [{"mask_id": "m0"}],
        "camera": {"frames_per_capture": 2},
    }


def test_hardware_policy_forbids_fake_devices():
    with pytest.raises(RuntimeModeError, match="fake"):
        validate_no_fake_devices(
            FakeDeviceBundle(camera=FakeCamera(), lcd=FakeLCD()),
            policy=hardware_runtime_policy(),
        )


def test_no_hardware_policy_allows_fake_devices():
    validate_no_fake_devices(
        FakeDeviceBundle(camera=FakeCamera(), lcd=FakeLCD()),
        policy=no_hardware_runtime_policy(),
    )


def test_missing_required_tls_rejected_in_hardware_mode():
    devices = SimpleNamespace(camera=DummyCamera(), lcd=DummyLCD(), tls=None)

    with pytest.raises(RuntimeModeError, match="TLS"):
        validate_required_devices(
            devices,
            policy=hardware_runtime_policy(),
            require_camera=True,
            require_lcd=True,
            require_tls=True,
        )


def test_missing_tls_allowed_in_no_hardware_when_no_movement_required():
    illumination = IlluminationSpec(
        mode="label_only",
        effective_wavelength_nm=532.0,
        tls_setpoint_nm=None,
        wavelength_label_nm=532.0,
    )

    validate_tls_for_illumination(
        illumination,
        None,
        policy=no_hardware_runtime_policy(),
    )


def test_broadband_pass_through_without_tls_rejected_in_hardware_mode():
    illumination = illumination_from_legacy_wavelength_nm(0.0)

    with pytest.raises(RuntimeModeError, match="TLS"):
        validate_tls_for_illumination(
            illumination,
            None,
            policy=hardware_runtime_policy(),
        )


def test_positive_wavelength_without_tls_is_label_only_in_no_hardware(tmp_path: Path):
    plan = CapturePlan.from_dict(_plan_dict())
    devices = FakeDeviceBundle(
        camera=FakeCamera(height=4, width=5),
        lcd=FakeLCD(height=4, width_phys=12),
    )
    output = tmp_path / "label_only.h5"

    run_capture_forward_dataset(
        plan,
        devices,
        output,
        dry_run=True,
        runtime_policy="no_hardware",
    )

    with h5py.File(output, "r") as f:
        status = json.loads(f["tls/status_json"][0])
    assert status["illumination"]["mode"] == "label_only"
    assert status["illumination"]["tls_setpoint_nm"] is None


def test_capture_records_runtime_policy_metadata(tmp_path: Path):
    plan = CapturePlan.from_dict(_plan_dict())
    devices = FakeDeviceBundle(
        camera=FakeCamera(height=4, width=5),
        lcd=FakeLCD(height=4, width_phys=12),
    )
    output = tmp_path / "runtime_policy_metadata.h5"

    run_capture_forward_dataset(
        plan,
        devices,
        output,
        dry_run=True,
        runtime_policy="no_hardware",
    )

    with h5py.File(output, "r") as f:
        raw_mode = f["capture/runtime_mode"][()]
        mode = raw_mode.decode() if isinstance(raw_mode, bytes) else str(raw_mode)
        raw_policy = f["capture/runtime_policy_json"][()]
        policy = json.loads(raw_policy.decode() if isinstance(raw_policy, bytes) else str(raw_policy))

    assert mode == "no_hardware"
    assert policy["mode"] == "no_hardware"
    assert policy["allow_fake_devices"] is True
    assert policy["allow_missing_tls"] is True


def test_positive_wavelength_without_tls_rejected_in_hardware_mode(tmp_path: Path):
    plan = CapturePlan.from_dict(_plan_dict())
    devices = SimpleNamespace(camera=DummyCamera(), lcd=DummyLCD(), tls=None)

    with pytest.raises(RuntimeModeError, match="TLS"):
        run_capture_forward_dataset(
            plan,
            devices,
            tmp_path / "hardware_missing_tls.h5",
            runtime_policy="hardware",
        )


def test_raw_fallback_requires_diagnostic_or_synthetic_policy():
    with pytest.raises(RuntimeModeError, match="allow_raw_fallback"):
        validate_raw_fallback_allowed(
            allow_raw_fallback=True,
            policy=hardware_runtime_policy(),
        )
    validate_raw_fallback_allowed(
        allow_raw_fallback=True,
        policy=diagnostic_runtime_policy(),
    )


def test_capture_fake_devices_rejected_in_explicit_hardware_mode(tmp_path: Path):
    plan = CapturePlan.from_dict(_plan_dict())
    devices = FakeDeviceBundle(
        camera=FakeCamera(height=4, width=5),
        lcd=FakeLCD(height=4, width_phys=12),
        tls=FakeTLS(),
    )

    with pytest.raises(RuntimeModeError, match="fake"):
        run_capture_forward_dataset(
            plan,
            devices,
            tmp_path / "fake_hardware.h5",
            dry_run=True,
            runtime_policy="hardware",
        )


def test_profile_task_rejects_missing_tls_in_hardware_mode():
    plan = BroadbandCameraCalibrationPlan(
        camera_profile_id="runtime_profile",
        physical_shape=(4, 12),
        candidates=[],
    )

    with pytest.raises(RuntimeModeError, match="TLS"):
        calibrate_broadband_camera_profile(
            plan,
            camera=DummyCamera(),
            lcd=DummyLCD(),
            tls=None,
            runtime_policy="hardware",
        )
