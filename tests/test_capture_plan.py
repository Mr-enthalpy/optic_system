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
            "roi": [0, 0, 640, 480],
        })
        assert cfg.frames_per_capture == 10
        assert cfg.average_burst is False
        assert cfg.exposure_us == 15000.0
        assert cfg.gain_db == 3.5
        assert cfg.roi == (0, 0, 640, 480)

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
        w = TLSWavelengthEntry.from_dict({"wavelength_nm": 532.0})
        assert w.wavelength_nm == 532.0
        assert w.settle_ms == 2000

    def test_from_dict_full(self) -> None:
        w = TLSWavelengthEntry.from_dict({
            "wavelength_nm": 405.0,
            "grating": 2,
            "settle_ms": 5000,
        })
        assert w.wavelength_nm == 405.0
        assert w.grating == 2
        assert w.settle_ms == 5000


class TestCapturePlan:
    def test_from_dict_minimal(self) -> None:
        plan = CapturePlan.from_dict({
            "plan_id": "minimal",
            "wavelengths": [{"wavelength_nm": 500.0}],
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
                "wavelengths": [{"wavelength_nm": 500}],
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
                "wavelengths": [{"wavelength_nm": 500}],
                "masks": [],
            })

    def test_validate_zero_wavelength_allowed(self) -> None:
        plan = CapturePlan.from_dict({
            "plan_id": "p",
            "wavelengths": [{"wavelength_nm": 0.0}],
            "masks": [{"mask_id": "m1"}],
        })
        assert plan.wavelengths[0].wavelength_nm == 0.0

    def test_validate_negative_wavelength_fails(self) -> None:
        with pytest.raises(CapturePlanError, match="wavelength_nm"):
            CapturePlan.from_dict({
                "plan_id": "p",
                "wavelengths": [{"wavelength_nm": -1}],
                "masks": [{"mask_id": "m1"}],
            })

    def test_validate_zero_wavelength_can_coexist_with_positive(self) -> None:
        plan = CapturePlan.from_dict({
            "plan_id": "p",
            "wavelengths": [
                {"wavelength_nm": 450.0},
                {"wavelength_nm": 0.0},
            ],
            "masks": [{"mask_id": "m1"}],
        })
        assert plan.n_wavelengths == 2
        assert plan.wavelengths[0].wavelength_nm == 450.0
        assert plan.wavelengths[1].wavelength_nm == 0.0

    def test_validate_frames_per_capture_fails(self) -> None:
        with pytest.raises(CapturePlanError, match="frames_per_capture"):
            CapturePlan.from_dict({
                "plan_id": "p",
                "wavelengths": [{"wavelength_nm": 500}],
                "masks": [{"mask_id": "m1"}],
                "camera": {"frames_per_capture": 0},
            })

    def test_validate_negative_lcd_settle_fails(self) -> None:
        with pytest.raises(CapturePlanError, match="lcd_settle_ms"):
            CapturePlan.from_dict({
                "plan_id": "p",
                "wavelengths": [{"wavelength_nm": 500}],
                "masks": [{"mask_id": "m1"}],
                "lcd_settle_ms": -1,
            })

    def test_validate_duplicate_wavelength_fails(self) -> None:
        with pytest.raises(CapturePlanError, match="duplicate wavelength"):
            CapturePlan.from_dict({
                "plan_id": "p",
                "wavelengths": [
                    {"wavelength_nm": 500},
                    {"wavelength_nm": 500},
                ],
                "masks": [{"mask_id": "m1"}],
            })

    def test_validate_duplicate_mask_id_fails(self) -> None:
        with pytest.raises(CapturePlanError, match="duplicate mask_id"):
            CapturePlan.from_dict({
                "plan_id": "p",
                "wavelengths": [{"wavelength_nm": 500}],
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
        assert "wavelength_nm" in text

    def test_preserves_extra(self) -> None:
        plan = CapturePlan.from_dict({
            "plan_id": "extra_test",
            "wavelengths": [{"wavelength_nm": 400}],
            "masks": [{"mask_id": "m1"}],
            "extra": {"custom_field": "hello"},
        })
        assert plan.extra == {"custom_field": "hello"}

    def test_n_captures_product(self) -> None:
        plan = CapturePlan.from_dict({
            "plan_id": "prod",
            "wavelengths": [
                {"wavelength_nm": 400},
                {"wavelength_nm": 500},
                {"wavelength_nm": 600},
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
                "wavelength_nm": 500,
                "grating": "bad",
            })

        with pytest.raises(CapturePlanError, match="roi"):
            CameraCaptureConfig.from_dict({
                "roi": [1, 2],
            })

        with pytest.raises(CapturePlanError, match="roi"):
            CameraCaptureConfig.from_dict({
                "roi": "not_a_list",
            })


class TestPlannedPhase3PlanStubs:
    STUB_PLANS = {
        "bishe_psf_roi.yaml": {
            "plan_id": "bishe_psf_roi",
            "phase": "3.2a",
        },
        "bishe_psf_repeatability.yaml": {
            "plan_id": "bishe_psf_repeatability",
            "phase": "3.2b",
        },
        "bishe_dotf_diagnostic.yaml": {
            "plan_id": "bishe_dotf_diagnostic",
            "phase": "3.3",
        },
        "bishe_psf_dictionary.yaml": {
            "plan_id": "bishe_psf_dictionary",
            "phase": "3.4",
        },
    }

    @pytest.mark.parametrize("filename,expected", list(STUB_PLANS.items()))
    def test_stub_yaml_schema_sanity(self, filename: str, expected: dict) -> None:
        import yaml

        plan_dir = Path(__file__).resolve().parents[1] / "plans"
        plan_path = plan_dir / filename
        assert plan_path.exists(), f"plan stub not found: {plan_path}"

        with open(plan_path, "r", encoding="utf-8") as fh:
            doc = yaml.safe_load(fh)

        assert doc is not None, f"empty YAML document: {filename}"
        assert doc.get("plan_id") == expected["plan_id"], filename
        assert doc.get("phase") == expected["phase"], filename
        assert "output" in doc, filename
