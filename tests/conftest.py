from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from tasks.capture_plan import (
    CameraCaptureConfig,
    CapturePlan,
    IlluminationEntry,
    LCDMaskEntry,
)
from tasks.testing import (
    FakeCamera,
    FakeDeviceBundle,
    FakeLCD,
    FakeTLS,
)


@pytest.fixture
def tmp_h5_path() -> Path:
    d = tempfile.mkdtemp(prefix="optsys_test_")
    yield Path(d) / "test_output.h5"
    import shutil
    try:
        shutil.rmtree(d)
    except Exception:
        pass


@pytest.fixture
def sample_plan_dict() -> dict[str, Any]:
    return {
        "plan_id": "test_plan_01",
        "wavelengths": [
            {
                "illumination": {
                    "mode": "monochromatic",
                    "effective_wavelength_nm": 532.0,
                    "tls_setpoint_nm": None,
                },
                "grating": 1,
                "settle_ms": 3000,
            },
            {
                "illumination": {
                    "mode": "monochromatic",
                    "effective_wavelength_nm": 633.0,
                    "tls_setpoint_nm": None,
                },
                "grating": 1,
                "settle_ms": 2000,
            },
        ],
        "masks": [
            {"mask_id": "mask_a", "family_id": "f1"},
            {"mask_id": "mask_b", "family_id": "f1"},
        ],
        "camera": {
            "frames_per_capture": 5,
        },
        "lcd_settle_ms": 500,
        "store_burst": False,
    }


@pytest.fixture
def sample_plan(sample_plan_dict: dict[str, Any]) -> CapturePlan:
    return CapturePlan.from_dict(sample_plan_dict)


@pytest.fixture
def fake_devices() -> FakeDeviceBundle:
    return FakeDeviceBundle(
        camera=FakeCamera(height=480, width=640),
        lcd=FakeLCD(height=1080, width_phys=5760),
        tls=FakeTLS(),
    )


@pytest.fixture
def fake_devices_no_tls() -> FakeDeviceBundle:
    return FakeDeviceBundle(
        camera=FakeCamera(),
        lcd=FakeLCD(height=60, width_phys=180),
        tls=None,
    )


@pytest.fixture
def mono_mask_arrays() -> list[np.ndarray]:
    return [
        np.full((1080, 5760), 128, dtype=np.uint8),
        np.full((1080, 5760), 200, dtype=np.uint8),
    ]


@pytest.fixture
def sample_plan_json(tmp_path: Path, sample_plan: CapturePlan) -> Path:
    p = tmp_path / "test_plan.json"
    sample_plan.to_json(p)
    return p


@pytest.fixture
def sample_plan_yaml(tmp_path: Path, sample_plan_dict: dict[str, Any]) -> Path:
    try:
        import yaml
    except ImportError:
        pytest.skip("PyYAML not available")
    p = tmp_path / "test_plan.yaml"
    p.write_text(yaml.safe_dump(sample_plan_dict), encoding="utf-8")
    return p


@pytest.fixture
def measured(tmp_path):
    """Synthetic measured-data IO fixture; not a real optical calibration."""
    from tasks.profiles import CameraProfile, PupilProfile
    from tasks.raw_capture_h5 import RawCaptureWriter
    from tasks.psf import build_full_frame_psf_survey
    shape = (9, 11)
    pupil = PupilProfile.from_dict({
        "pupil_profile_id": "pupil", "lcd_coordinate_convention": "physical_mono_xy",
        "lcd_display_index": 0, "subpixel_axis": 1, "lcd_physical_center": [4, 2],
        "lcd_physical_radius": 2, "extra": {"physical_shape": [6, 9]},
    })
    camera = CameraProfile.from_dict({
        "camera_profile_id": "bands", "profile_family": "per_band_pupil_open",
        "depends_on": {"pupil_profile_id": "pupil"},
        "illumination": {"mode": "monochromatic", "wavelengths_nm": [450, 650]},
        "lcd_state": {"mode": "selected_pupil_open", "pupil_profile_id": "pupil"},
        "camera": {"per_wavelength": {
            "450": {"exposure_us": 100, "gain_db": 0},
            "650": {"exposure_us": 200, "gain_db": 0},
        }}, "valid_for": ["psf_survey_capture"],
    })
    pupil.to_json(tmp_path / "pupil.json")
    camera.to_json(tmp_path / "camera.json")
    plan = CapturePlan.from_dict({
        "plan_id": "fixed_display_response", "requires": {"pupil_profile_id": "pupil", "camera_profile_id": "bands"},
        "wavelengths": [{"illumination": {"mode": "monochromatic", "effective_wavelength_nm": w}} for w in (450, 650)],
        "masks": [{"mask_id": "fixed"}, {"mask_id": "other"}],
        "camera": {"frames_per_capture": 1}, "lcd_settle_ms": 0,
        "extra": {"capture_role": "survey_capture"},
    })
    raw = tmp_path / "raw.h5"
    base = np.zeros(shape, dtype=np.float32)
    base[3:6, 4:7] = np.arange(1, 10).reshape(3, 3)
    extent = {"mode": "full_sensor", "origin_xy": [0, 0], "shape_hw": list(shape), "sensor_shape_hw": list(shape)}
    with RawCaptureWriter(raw, plan) as writer:
        writer.write_lcd_metadata({"display_index": 0, "physical_shape": [6, 9], "subpixel_axis": 1})
        writer.write_physical_masks([np.ones((6, 9), np.uint8)] * 2)
        for i in range(4):
            writer.append_capture(i, i // 2, i % 2, None, base * (i + 1),
                {"frame_extent": extent, "exposure_us": 100 + 100 * (i // 2), "gain_db": 0},
                requested_exposure_us=100 + 100 * (i // 2), requested_gain_db=0)
    survey = tmp_path / "survey.h5"
    kwargs = dict(source_raw_capture_h5=raw, output_h5=survey,
        pupil_profile_manifest=tmp_path / "pupil.json", camera_profile_manifest=tmp_path / "camera.json")
    build_full_frame_psf_survey(**kwargs)
    return raw, survey, base, kwargs, plan, camera, pupil
