from __future__ import annotations

import json
from pathlib import Path

import h5py
import pytest

from tasks.capture_forward_dataset import run_capture_forward_dataset
from tasks.testing import (
    FakeCamera,
    FakeDeviceBundle,
    FakeLCD,
)
from tasks.capture_plan import CapturePlan, CapturePlanError
from tasks.illumination import (
    IlluminationSpec,
    IlluminationSpecError,
    apply_illumination_to_tls,
    normalize_illumination_spec,
)


class RecordingTLS:
    def __init__(self):
        self.calls: list[tuple[str, float]] = []
        self.target: float | None = None
        self.current: float | None = None

    def set_pass_through(self, timeout_s: float) -> None:
        self.calls.append(("set_pass_through", float(timeout_s)))
        self.target = 0.0
        self.current = 0.0

    def set_wavelength(self, wavelength_nm: float) -> None:
        self.calls.append(("set_wavelength", float(wavelength_nm)))
        self.target = float(wavelength_nm)

    def move_and_wait(self, timeout_s: float) -> None:
        self.calls.append(("move_and_wait", float(timeout_s)))
        self.current = self.target

    def status(self) -> dict:
        return {
            "connected": True,
            "current_wavelength_nm": self.current,
            "target_wavelength_nm": self.target,
            "moving": False,
        }


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


def test_numeric_legacy_wavelength_input_is_rejected():
    with pytest.raises(IlluminationSpecError, match="mapping"):
        normalize_illumination_spec(550.0)  # type: ignore[arg-type]


def test_wavelength_nm_mapping_input_is_rejected():
    with pytest.raises(IlluminationSpecError, match="wavelength_nm compatibility"):
        normalize_illumination_spec({"wavelength_nm": 550.0})


def test_explicit_monochromatic_validates_positive_wavelength():
    spec = normalize_illumination_spec(_mono_entry(450.0))

    assert spec.mode == "monochromatic"
    assert spec.effective_wavelength_nm == 450.0
    assert spec.tls_setpoint_nm == 450.0
    assert spec.requires_tls_wavelength_move is True


def test_explicit_broadband_passthrough_validates_setpoint_zero():
    spec = normalize_illumination_spec(_pass_entry())

    assert spec.mode == "broadband_passthrough"
    assert spec.effective_wavelength_nm is None
    assert spec.tls_setpoint_nm == 0.0
    assert spec.requires_tls_pass_through is True


def test_invalid_explicit_broadband_passthrough_rejects_effective_wavelength():
    with pytest.raises(IlluminationSpecError, match="effective_wavelength_nm"):
        IlluminationSpec(
            mode="broadband_passthrough",
            effective_wavelength_nm=550.0,
            tls_setpoint_nm=0.0,
        )


def test_monochromatic_without_setpoint_does_not_request_tls():
    spec = IlluminationSpec(
        mode="monochromatic",
        effective_wavelength_nm=550.0,
        tls_setpoint_nm=None,
    )

    assert spec.requires_tls_pass_through is False
    assert spec.requires_tls_wavelength_move is False


def test_pass_through_helper_calls_set_pass_through_not_set_wavelength():
    tls = RecordingTLS()
    status = apply_illumination_to_tls(
        tls,
        normalize_illumination_spec(_pass_entry()),
        timeout_s=12.0,
    )

    assert tls.calls == [("set_pass_through", 12.0)]
    assert status["tls_action"] == "set_pass_through"
    assert status["illumination"]["mode"] == "broadband_passthrough"


def test_monochromatic_helper_calls_wavelength_move_path():
    tls = RecordingTLS()
    status = apply_illumination_to_tls(
        tls,
        normalize_illumination_spec(_mono_entry(532.0)),
        timeout_s=3.0,
    )

    assert tls.calls == [("set_wavelength", 532.0), ("move_and_wait", 3.0)]
    assert status["tls_action"] == "set_wavelength_and_move"
    assert status["illumination"]["mode"] == "monochromatic"


def test_legacy_capture_plan_wavelength_nm_is_rejected():
    with pytest.raises(CapturePlanError, match="wavelength_nm compatibility"):
        CapturePlan.from_dict({
            "plan_id": "legacy_plan",
            "wavelengths": [
                {"wavelength_nm": 0.0},
                {"wavelength_nm": 450.0},
            ],
            "masks": [{"mask_id": "m1"}],
        })


def test_explicit_illumination_plan_entry_parses_without_wavelength_nm():
    plan = CapturePlan.from_dict({
        "plan_id": "explicit_plan",
        "wavelengths": [_mono_entry(650.0)],
        "masks": [{"mask_id": "m1"}],
    })

    assert plan.wavelengths[0].illumination.effective_wavelength_nm == 650.0
    assert "wavelength_nm" not in plan.to_dict()["wavelengths"][0]
    assert plan.resolved_illumination_specs()[0].mode == "monochromatic"


def test_raw_status_metadata_records_illumination_mode(tmp_path: Path):
    plan = CapturePlan.from_dict({
        "plan_id": "metadata_illumination",
        "wavelengths": [_pass_entry()],
        "masks": [{"mask_id": "m1"}],
        "camera": {"frames_per_capture": 1},
    })
    tls = RecordingTLS()
    devices = FakeDeviceBundle(
        camera=FakeCamera(height=24, width=32),
        lcd=FakeLCD(height=60, width_phys=180),
        tls=tls,
    )
    output = tmp_path / "out.h5"

    run_capture_forward_dataset(
        plan=plan,
        devices=devices,
        output_path=output,
        enable_tls=True,
        dry_run=True,
    )

    with h5py.File(output, "r") as f:
        status_raw = f["tls/status_json"][0]
        status = json.loads(
            status_raw.decode() if isinstance(status_raw, bytes) else str(status_raw)
        )

    assert status["illumination"]["mode"] == "broadband_passthrough"
    assert status["illumination"]["effective_wavelength_nm"] is None
    assert status["illumination"]["tls_setpoint_nm"] == 0.0
    assert status["tls_action"] == "set_pass_through"


def test_monochromatic_without_tls_records_illumination(tmp_path: Path):
    plan = CapturePlan.from_dict({
        "plan_id": "metadata_monochromatic",
        "wavelengths": [_label_entry(550.0)],
        "masks": [{"mask_id": "m1"}],
        "camera": {"frames_per_capture": 1},
    })
    devices = FakeDeviceBundle(
        camera=FakeCamera(height=24, width=32),
        lcd=FakeLCD(height=60, width_phys=180),
        tls=None,
    )
    output = tmp_path / "mono_no_tls.h5"

    run_capture_forward_dataset(
        plan=plan,
        devices=devices,
        output_path=output,
        enable_tls=False,
        dry_run=True,
    )

    with h5py.File(output, "r") as f:
        status_raw = f["tls/status_json"][0]
        status = json.loads(
            status_raw.decode() if isinstance(status_raw, bytes) else str(status_raw)
        )

    assert status["illumination"]["mode"] == "monochromatic"
    assert status["illumination"]["effective_wavelength_nm"] == 550.0
    assert status["illumination"]["tls_setpoint_nm"] is None
    assert status["tls_action"] == "skipped_no_hardware"
