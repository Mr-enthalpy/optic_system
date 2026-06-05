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
    LCDMaskEntry,
    TLSWavelengthEntry,
)
from tasks.capture_forward_dataset import (
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
                    "mode": "label_only",
                    "effective_wavelength_nm": 532.0,
                    "tls_setpoint_nm": None,
                    "wavelength_label_nm": 532.0,
                },
                "grating": 1,
                "settle_ms": 3000,
            },
            {
                "illumination": {
                    "mode": "label_only",
                    "effective_wavelength_nm": 633.0,
                    "tls_setpoint_nm": None,
                    "wavelength_label_nm": 633.0,
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
            "average_burst": True,
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
