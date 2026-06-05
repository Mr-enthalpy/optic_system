from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from tasks.capture_plan import (
    CameraCaptureConfig,
    CapturePlan,
    CapturePlanError,
    LCDMaskEntry,
    TLSWavelengthEntry,
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
            "mode": "label_only",
            "effective_wavelength_nm": float(wavelength_nm),
            "tls_setpoint_nm": None,
            "wavelength_label_nm": float(wavelength_nm),
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


class TestCameraCaptureConfig:
    def test_defaults(self) -> None:
        cfg = CameraCaptureConfig()
        assert cfg.frames_per_capture == 1
        assert cfg.average_burst is True
        assert cfg.exposure_us is None

    def test_from_dict(self) -> None:
        cfg = CameraCaptureConfig.from_dict({
            "frames_per_capture": 10,
            "average_burst": False,
            "exposure_us": 15000.0,
            "gain_db": 3.5,
            "frame_extent": {
                "mode": "acquired_frame",
                "origin_xy": [0, 0],
                "shape_hw": [480, 640],
            },
        })
        assert cfg.frames_per_capture == 10
        assert cfg.average_burst is False
        assert cfg.exposure_us == 15000.0
        assert cfg.gain_db == 3.5
        assert cfg.frame_extent == {
            "mode": "acquired_frame",
            "origin_xy": [0, 0],
            "shape_hw": [480, 640],
            "sensor_shape_hw": None,
        }
    def test_legacy_roi_input_is_rejected(self) -> None:
        with pytest.raises(CapturePlanError, match="camera.frame_extent"):
            CameraCaptureConfig.from_dict({"roi": [1, 2, 30, 40]})
        with pytest.raises(CapturePlanError, match="camera.frame_extent"):
            CameraCaptureConfig.from_dict({"camera_roi": [1, 2, 30, 40]})

    def test_from_dict_empty(self) -> None:
        cfg = CameraCaptureConfig.from_dict({})
        assert cfg.frames_per_capture == 1

    def test_to_dict_roundtrip(self) -> None:
        d = {"frames_per_capture": 7, "average_burst": True}
        cfg = CameraCaptureConfig.from_dict(d)
        out = cfg.to_dict()
        assert out["frames_per_capture"] == 7
        assert out["average_burst"] is True


class TestLCDMaskEntry:
    def test_from_dict_minimal(self) -> None:
        m = LCDMaskEntry.from_dict({"mask_id": "test_mask"})
        assert m.mask_id == "test_mask"
        assert m.path is None
        assert m.array is None

    def test_from_dict_full(self) -> None:
        m = LCDMaskEntry.from_dict({
            "mask_id": "m1",
            "path": "data/m1.npy",
            "family_id": "fam_a",
            "family_params": {"k": 1},
        })
        assert m.mask_id == "m1"
        assert m.path == "data/m1.npy"
        assert m.family_id == "fam_a"
        assert m.family_params == {"k": 1}

    def test_to_dict(self) -> None:
        m = LCDMaskEntry.from_dict({"mask_id": "m1"})
        d = m.to_dict()
        assert d["mask_id"] == "m1"


class TestTLSWavelengthEntry:
    def test_from_dict(self) -> None:
        w = TLSWavelengthEntry.from_dict(_mono_entry(532.0))
        assert w.wavelength_nm == 532.0
        assert w.settle_ms == 2000

    def test_from_dict_full(self) -> None:
        w = TLSWavelengthEntry.from_dict(_mono_entry(405.0, grating=2, settle_ms=5000))
        assert w.wavelength_nm == 405.0
        assert w.grating == 2
        assert w.settle_ms == 5000

    def test_wavelength_nm_input_is_rejected(self) -> None:
        with pytest.raises(CapturePlanError, match="wavelength_nm compatibility"):
            TLSWavelengthEntry.from_dict({"wavelength_nm": 532.0})


class TestCapturePlan:
    def test_from_dict_minimal(self) -> None:
        plan = CapturePlan.from_dict({
            "plan_id": "minimal",
            "wavelengths": [_label_entry(500.0)],
            "masks": [{"mask_id": "m1"}],
            "camera": {"frames_per_capture": 1},
        })
        assert plan.plan_id == "minimal"
        assert plan.n_wavelengths == 1
        assert plan.n_masks == 1
        assert plan.n_captures == 1

    def test_from_dict_full(self, sample_plan_dict: dict) -> None:
        plan = CapturePlan.from_dict(sample_plan_dict)
        assert plan.plan_id == "test_plan_01"
        assert plan.n_wavelengths == 2
        assert plan.n_masks == 2
        assert plan.n_captures == 4
        assert plan.camera.frames_per_capture == 5
        assert plan.lcd_settle_ms == 500

    def test_validate_empty_mask_id_fails(self) -> None:
        with pytest.raises(CapturePlanError, match="mask_id"):
            CapturePlan.from_dict({
                "plan_id": "p",
                "wavelengths": [_label_entry(500)],
                "masks": [{"mask_id": ""}],
            })

    def test_validate_empty_wavelengths_fails(self) -> None:
        with pytest.raises(CapturePlanError, match="wavelengths"):
            CapturePlan.from_dict({
                "plan_id": "p",
                "wavelengths": [],
                "masks": [{"mask_id": "m1"}],
            })

    def test_validate_empty_masks_fails(self) -> None:
        with pytest.raises(CapturePlanError, match="masks"):
            CapturePlan.from_dict({
                "plan_id": "p",
                "wavelengths": [_label_entry(500)],
                "masks": [],
            })

    def test_validate_wavelength_nm_input_fails(self) -> None:
        with pytest.raises(CapturePlanError, match="wavelength_nm compatibility"):
            CapturePlan.from_dict({
                "plan_id": "p",
                "wavelengths": [{"wavelength_nm": -1}],
                "masks": [{"mask_id": "m1"}],
            })

    def test_validate_zero_wavelength_input_fails(self) -> None:
        with pytest.raises(CapturePlanError, match="wavelength_nm compatibility"):
            CapturePlan.from_dict({
                "plan_id": "pass_through",
                "wavelengths": [{"wavelength_nm": 0.0}],
                "masks": [{"mask_id": "m1"}],
            })

    def test_explicit_pass_through_illumination_parses(self) -> None:
        plan = CapturePlan.from_dict({
            "plan_id": "pass_through",
            "wavelengths": [_pass_entry()],
            "masks": [{"mask_id": "m1"}],
        })

        assert plan.wavelengths[0].wavelength_nm == 0.0
        assert "wavelength_nm" not in plan.to_dict()["wavelengths"][0]

    def test_validate_frames_per_capture_fails(self) -> None:
        with pytest.raises(CapturePlanError, match="frames_per_capture"):
            CapturePlan.from_dict({
                "plan_id": "p",
                "wavelengths": [_label_entry(500)],
                "masks": [{"mask_id": "m1"}],
                "camera": {"frames_per_capture": 0},
            })

    def test_validate_negative_lcd_settle_fails(self) -> None:
        with pytest.raises(CapturePlanError, match="lcd_settle_ms"):
            CapturePlan.from_dict({
                "plan_id": "p",
                "wavelengths": [_label_entry(500)],
                "masks": [{"mask_id": "m1"}],
                "lcd_settle_ms": -1,
            })

    def test_validate_duplicate_wavelength_fails(self) -> None:
        with pytest.raises(CapturePlanError, match="duplicate wavelength"):
            CapturePlan.from_dict({
                "plan_id": "p",
                "wavelengths": [
                    _label_entry(500),
                    _label_entry(500),
                ],
                "masks": [{"mask_id": "m1"}],
            })

    def test_validate_duplicate_mask_id_fails(self) -> None:
        with pytest.raises(CapturePlanError, match="duplicate mask_id"):
            CapturePlan.from_dict({
                "plan_id": "p",
                "wavelengths": [_label_entry(500)],
                "masks": [
                    {"mask_id": "dup"},
                    {"mask_id": "dup"},
                ],
            })

    def test_load_json(self, sample_plan_json: Path) -> None:
        plan = CapturePlan.load_json(sample_plan_json)
        assert plan.plan_id == "test_plan_01"

    def test_load_yaml(self, sample_plan_yaml: Path) -> None:
        plan = CapturePlan.load_yaml(sample_plan_yaml)
        assert plan.plan_id == "test_plan_01"

    def test_to_json_serializable(self, sample_plan: CapturePlan) -> None:
        text = sample_plan.to_json()
        assert "test_plan_01" in text
        assert "illumination" in text

    def test_preserves_extra(self) -> None:
        plan = CapturePlan.from_dict({
            "plan_id": "extra_test",
            "wavelengths": [_label_entry(400)],
            "masks": [{"mask_id": "m1"}],
            "extra": {"custom_field": "hello"},
        })
        assert plan.extra == {"custom_field": "hello"}

    def test_preserves_profile_requirements(self) -> None:
        plan = CapturePlan.from_dict({
            "plan_id": "requires_test",
            "requires": {
                "pupil_profile_id": "pupil_profile_v1",
                "camera_profile_id": "per_band_pupil_open_v1",
            },
            "wavelengths": [_label_entry(500)],
            "masks": [{"mask_id": "m1"}],
        })

        assert plan.requires["pupil_profile_id"] == "pupil_profile_v1"
        assert plan.to_dict()["requires"]["camera_profile_id"] == "per_band_pupil_open_v1"

    def test_n_captures_product(self) -> None:
        plan = CapturePlan.from_dict({
            "plan_id": "prod",
            "wavelengths": [
                _label_entry(400),
                _label_entry(500),
                _label_entry(600),
            ],
            "masks": [
                {"mask_id": "a"},
                {"mask_id": "b"},
                {"mask_id": "c"},
                {"mask_id": "d"},
            ],
        })
        assert plan.n_captures == 12
        assert plan.n_wavelengths == 3
        assert plan.n_masks == 4

    def test_invalid_optional_fields_raise(self) -> None:
        with pytest.raises(CapturePlanError, match="float"):
            CameraCaptureConfig.from_dict({"exposure_us": "not_a_number"})

        with pytest.raises(CapturePlanError, match="int"):
            TLSWavelengthEntry.from_dict({
                **_label_entry(500),
                "grating": "bad",
            })

        with pytest.raises(CapturePlanError, match="frame_extent"):
            CameraCaptureConfig.from_dict({
                "frame_extent": [1, 2],
            })

        with pytest.raises(CapturePlanError, match="frame_extent"):
            CameraCaptureConfig.from_dict({
                "frame_extent": "not_a_list",
            })
