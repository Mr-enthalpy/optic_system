"""Contract test: raw capture HDF5 schema matches RawCaptureWriter output."""

from pathlib import Path

import h5py
import numpy as np
import pytest

from tasks.capture_plan import CapturePlan
from tasks.artifacts.validation import ValidityOutcome, check_validity
from tasks.raw_capture_h5 import RawCaptureWriter


def _sample_plan() -> CapturePlan:
    return CapturePlan.from_dict({
        "plan_id": "schema_contract_test",
        "wavelengths": [{
            "illumination": {
                "mode": "monochromatic",
                "effective_wavelength_nm": 550.0,
                "tls_setpoint_nm": 550.0,
            }
        }],
        "masks": [{"mask_id": "m1"}],
    })


def _minimal_h5(tmp_path: Path) -> Path:
    plan = _sample_plan()
    path = tmp_path / "raw.h5"
    writer = RawCaptureWriter(path, plan)
    with writer:
        writer.write_physical_masks([np.ones((3, 6), dtype=np.uint8)])
        writer.append_capture(
            capture_index=0,
            wavelength_index=0,
            mask_index=0,
            frames=None,
            frames_avg=np.ones((20, 20), dtype=np.float64),
            camera_meta={},
            lcd_display_timestamp_ns=0,
        )
    return path


REQUIRED_DATASETS = [
    # /raw
    "raw/frames_avg",
    # /masks
    "masks/masks_physical",
    "masks/mask_id",
    "masks/family_id",
    "masks/family_params_json",
    "masks/has_mask_array",
    # /illumination
    "illumination/illumination_json",
    "illumination/tls_setpoint_nm",
    "illumination/effective_wavelength_nm",
    # /tls
    "tls/grating",
    "tls/settle_ms",
    "tls/timestamp_ns",
    "tls/status_json",
    # /camera
    "camera/requested_exposure_us",
    "camera/requested_gain_db",
    "camera/readback_exposure_us",
    "camera/readback_gain_db",
    "camera/frame_extent_json",
    "camera/timestamp_ns",
    "camera/status_json",
    # /lcd
    "lcd/settle_ms",
    "lcd/display_timestamp_ns",
    "lcd/mapping_policy_json",
    "lcd/metadata_json",
    # /profiles
    "profiles/requirements_json",
    "profiles/pupil_profile_id",
    "profiles/camera_profile_id",
    # /capture
    "capture/capture_index",
    "capture/wavelength_index",
    "capture/mask_index",
    "capture/burst_count",
    "capture/completed",
    "capture/plan_json",
    "capture/plan_id",
    "capture/runtime_mode",
    "capture/runtime_policy_json",
    "capture/processing_flags_json",
]

OBSOLETE_PATHS = [
    "camera/exposure_us",
    "camera/gain_db",
    "camera/roi",
    "camera/camera_roi",
    "tls/wavelength_nm",
    "illumination/nominal_wavelength_nm",
]


def test_all_required_datasets_exist(tmp_path: Path) -> None:
    path = _minimal_h5(tmp_path)
    with h5py.File(path, "r") as f:
        for ds_path in REQUIRED_DATASETS:
            parts = ds_path.split("/")
            grp: h5py.Group = f
            for part in parts[:-1]:
                assert part in grp, f"group {part} missing in path {ds_path}"
                grp = grp[part]
            assert parts[-1] in grp, f"dataset {ds_path} missing"


def test_obsolete_datasets_do_not_exist(tmp_path: Path) -> None:
    path = _minimal_h5(tmp_path)
    with h5py.File(path, "r") as f:
        for ds_path in OBSOLETE_PATHS:
            assert ds_path not in f, f"obsolete path {ds_path} must not exist"


def test_camera_has_requested_and_readback_fields(tmp_path: Path) -> None:
    path = _minimal_h5(tmp_path)
    with h5py.File(path, "r") as f:
        cam = f["camera"]
        for name in ("requested_exposure_us", "requested_gain_db",
                     "readback_exposure_us", "readback_gain_db"):
            assert name in cam, f"camera/{name} missing"


def test_root_attrs_present(tmp_path: Path) -> None:
    path = _minimal_h5(tmp_path)
    with h5py.File(path, "r") as f:
        assert f.attrs["software_version"] == "optic_system"
        assert f.attrs["raw_capture_schema_version"] == 2
        assert f.attrs["capture_role"] == "minimal_capture"
        assert "plan_id" in f.attrs
        assert "created_at_ns" in f.attrs
        assert "hdf5_writer_version" in f.attrs
        assert f.attrs["artifact_type"] == "raw_capture"


def test_minimal_raw_capture_is_structurally_valid(tmp_path: Path) -> None:
    result = check_validity("raw_capture", _minimal_h5(tmp_path))

    assert result.outcome is ValidityOutcome.VALID
