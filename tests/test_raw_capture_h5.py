from __future__ import annotations

import json
from pathlib import Path

import h5py
import numpy as np
import pytest

from tasks.capture_plan import CapturePlan
from tasks.raw_capture_h5 import (
    RawCaptureWriteError,
    RawCaptureWriter,
    RawFrameStoragePolicy,
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


def _h5_array_str(dset, index: int = 0) -> str:
    val = dset[index]
    if isinstance(val, bytes):
        return val.decode()
    return str(val)


class TestRawCaptureWriter:
    def test_creates_hdf5_with_expected_groups(
        self, sample_plan: CapturePlan, tmp_h5_path: Path
    ) -> None:
        writer = RawCaptureWriter(tmp_h5_path, sample_plan)
        with writer:
            pass

        assert tmp_h5_path.exists()
        with h5py.File(tmp_h5_path, "r") as f:
            assert "raw" in f
            assert "masks" in f
            assert "tls" in f
            assert "camera" in f
            assert "lcd" in f
            assert "capture" in f

    def test_attrs_set(self, sample_plan: CapturePlan, tmp_h5_path: Path) -> None:
        writer = RawCaptureWriter(tmp_h5_path, sample_plan)
        with writer:
            pass

        with h5py.File(tmp_h5_path, "r") as f:
            assert f.attrs["plan_id"] == "test_plan_01"
            assert f.attrs["software_version"] == "optic_system"
            assert int(f.attrs["raw_capture_schema_version"]) == 2
            assert f.attrs["capture_role"] == "minimal_capture"

    def test_plan_json_stored(self, sample_plan: CapturePlan, tmp_h5_path: Path) -> None:
        writer = RawCaptureWriter(tmp_h5_path, sample_plan)
        with writer:
            pass

        with h5py.File(tmp_h5_path, "r") as f:
            plan_json = _h5_str(f["capture/plan_json"])
            assert "test_plan_01" in plan_json

    def test_profile_requirements_stored(self, tmp_h5_path: Path) -> None:
        plan = CapturePlan.from_dict({
            "plan_id": "profiled_capture",
            "requires": {
                "pupil_profile_id": "pupil_profile_v1",
                "camera_profile_id": "per_band_pupil_open_v1",
            },
            "wavelengths": [{
                "illumination": {
                    "mode": "monochromatic",
                    "effective_wavelength_nm": 550.0,
                    "tls_setpoint_nm": None,
                }
            }],
            "masks": [{"mask_id": "m1"}],
        })

        writer = RawCaptureWriter(tmp_h5_path, plan)
        with writer:
            pass

        with h5py.File(tmp_h5_path, "r") as f:
            assert _h5_str(f["profiles/pupil_profile_id"]) == "pupil_profile_v1"
            assert _h5_str(f["profiles/camera_profile_id"]) == "per_band_pupil_open_v1"
            requirements = _h5_str(f["profiles/requirements_json"])
            assert "per_band_pupil_open_v1" in requirements

    def test_capture_role_from_plan_extra(self, sample_plan: CapturePlan, tmp_h5_path: Path) -> None:
        plan_dict = sample_plan.to_dict()
        plan_dict["extra"] = {"capture_role": "profile_capture"}
        plan = CapturePlan.from_dict(plan_dict)

        writer = RawCaptureWriter(tmp_h5_path, plan)
        with writer:
            pass

        with h5py.File(tmp_h5_path, "r") as f:
            assert f.attrs["capture_role"] == "profile_capture"
            pf = json.loads(_h5_str(f["capture/processing_flags_json"]))
            assert pf["capture_role"] == "profile_capture"

    def test_processing_flags_written(
        self, sample_plan: CapturePlan, tmp_h5_path: Path
    ) -> None:
        writer = RawCaptureWriter(tmp_h5_path, sample_plan)
        with writer:
            pass

        with h5py.File(tmp_h5_path, "r") as f:
            pf = json.loads(_h5_str(f["capture/processing_flags_json"]))
            assert pf["scientific_calibration_valid"] is False
            assert pf["raw_capture_schema_version"] == 2
            assert pf["capture_role"] == "minimal_capture"
            assert "phase" not in pf
            assert pf["completed"] is True

    def test_writes_physical_masks(
        self, sample_plan: CapturePlan, tmp_h5_path: Path
    ) -> None:
        arrays = [
            np.full((100, 300), 128, dtype=np.uint8),
            np.full((100, 300), 200, dtype=np.uint8),
        ]
        sample_plan_dict = sample_plan.to_dict()
        sample_plan_dict["masks"][0]["array"] = arrays[0]
        sample_plan_dict["masks"][1]["array"] = arrays[1]
        plan = CapturePlan.from_dict(sample_plan_dict)

        writer = RawCaptureWriter(tmp_h5_path, plan)
        with writer:
            writer.write_physical_masks(arrays)

        with h5py.File(tmp_h5_path, "r") as f:
            dset = f["masks/masks_physical"]
            assert dset.shape[0] == 2
            assert dset.shape[1] == 100

            mask_ids = f["masks/mask_id"][()]
            assert mask_ids[0].decode() if isinstance(mask_ids[0], bytes) else str(mask_ids[0]) == "mask_a"

    def test_masks_must_be_2d(
        self, sample_plan: CapturePlan, tmp_h5_path: Path
    ) -> None:
        writer = RawCaptureWriter(tmp_h5_path, sample_plan)
        with pytest.raises(RawCaptureWriteError, match="2D"):
            writer.open()
            writer.write_physical_masks([np.zeros((1, 2, 3))])

    def test_incremental_append(
        self, sample_plan: CapturePlan, tmp_h5_path: Path
    ) -> None:
        writer = RawCaptureWriter(tmp_h5_path, sample_plan)
        with writer:
            cam_meta = {"exposure_us": 10000.0, "gain_db": 1.5}
            tls = {"wavelength_nm": 532.0, "grating": 1, "timestamp_ns": 11111}

            writer.append_capture(
                capture_index=0,
                wavelength_index=0,
                mask_index=0,
                frames=np.array([]),
                frames_avg=np.ones((240, 320), dtype=np.float64),
                camera_meta=cam_meta,
                tls_status=tls,
                lcd_display_timestamp_ns=22222,
            )
            assert writer.n_written == 1

        with h5py.File(tmp_h5_path, "r") as f:
            avg = f["raw/frames_avg"]
            assert avg.shape[0] == 4
            assert avg.dtype == np.float32
            assert avg[0, 0, 0] == 1.0
            assert "storage_policy_json" in f["raw"].attrs

            cap = f["capture"]
            assert int(cap["capture_index"][0]) == 0
            assert int(cap["wavelength_index"][0]) == 0
            assert int(cap["mask_index"][0]) == 0
            assert bool(cap["completed"][0]) is True

            cam = f["camera"]
            assert float(cam["readback_exposure_us"][0]) == 10000.0

            illum_ds = f["illumination"]
            raw_illumination = illum_ds["illumination_json"][0]
            illumination = json.loads(
                raw_illumination.decode("utf-8")
                if isinstance(raw_illumination, bytes)
                else str(raw_illumination)
            )
            assert illumination["mode"] == "monochromatic"
            assert illumination["effective_wavelength_nm"] == 532.0

    def test_writes_camera_frame_extent_without_legacy_alias(
        self, sample_plan: CapturePlan, tmp_h5_path: Path
    ) -> None:
        writer = RawCaptureWriter(tmp_h5_path, sample_plan)
        with writer:
            writer.append_capture(
                capture_index=0,
                wavelength_index=0,
                mask_index=0,
                frames=np.array([]),
                frames_avg=np.ones((12, 16), dtype=np.float64),
                camera_meta={
                    "frame_extent": {
                        "mode": "acquired_frame",
                        "origin_xy": [3, 4],
                        "shape_hw": [12, 16],
                        "sensor_shape_hw": [100, 120],
                    },
                },
            )

        with h5py.File(tmp_h5_path, "r") as f:
            assert "camera/frame_extent_json" in f
            frame_extent = _h5_array_str(f["camera/frame_extent_json"])
            assert "camera/roi_json" not in f
            assert '"origin_xy": [' in frame_extent
            assert '"shape_hw": [' in frame_extent
            assert '"sensor_shape_hw": [' in frame_extent
            assert '"source": "camera_metadata"' in frame_extent

    def test_all_captures_written(
        self, sample_plan: CapturePlan, tmp_h5_path: Path
    ) -> None:
        writer = RawCaptureWriter(tmp_h5_path, sample_plan)
        with writer:
            for ci in range(sample_plan.n_captures):
                wi = ci % sample_plan.n_wavelengths
                mi = ci // sample_plan.n_wavelengths
                writer.append_capture(
                    capture_index=ci,
                    wavelength_index=wi,
                    mask_index=mi,
                    frames=np.array([]),
                    frames_avg=np.ones((240, 320), dtype=np.float64) * ci,
                    camera_meta={},
                )
            assert writer.n_written == 4

        with h5py.File(tmp_h5_path, "r") as f:
            assert bool(f["capture/completed"][:].all())
            pf = _h5_str(f["capture/processing_flags_json"])
            assert "completed" in pf.lower()

    def test_failure_records_partial_data(
        self, sample_plan: CapturePlan, tmp_h5_path: Path
    ) -> None:
        writer = RawCaptureWriter(tmp_h5_path, sample_plan)
        try:
            writer.open()
            writer.append_capture(
                capture_index=0, wavelength_index=0, mask_index=0,
                frames=np.array([]),
                frames_avg=np.ones((240, 320), dtype=np.float64),
                camera_meta={},
            )
            raise RuntimeError("simulated failure")
        except RuntimeError:
            writer.finalize(completed=False, error="simulated failure",
                            last_completed_capture_index=0)

        with h5py.File(tmp_h5_path, "r") as f:
            pf = _h5_str(f["capture/processing_flags_json"])
            assert "false" in pf.lower()
            assert "simulated failure" in pf

    def test_no_burst_dataset_when_store_burst_false(
        self, sample_plan: CapturePlan, tmp_h5_path: Path
    ) -> None:
        sample_plan.store_burst = False
        writer = RawCaptureWriter(tmp_h5_path, sample_plan)
        writer.open()
        writer.close()

        with h5py.File(tmp_h5_path, "r") as f:
            assert "frames" not in f["raw"]
            assert "frames_avg" in f["raw"]

    def test_burst_dataset_when_store_burst_true(
        self, tmp_h5_path: Path
    ) -> None:
        plan = CapturePlan.from_dict({
            "plan_id": "burst_test",
            "wavelengths": [{
                "illumination": {
                    "mode": "monochromatic",
                    "effective_wavelength_nm": 500.0,
                    "tls_setpoint_nm": None,
                }
            }],
            "masks": [{"mask_id": "m1"}],
            "camera": {"frames_per_capture": 3},
            "store_burst": True,
        })
        writer = RawCaptureWriter(tmp_h5_path, plan)
        with writer:
            burst = np.ones((3, 120, 160), dtype=np.uint16)
            writer.append_capture(
                capture_index=0, wavelength_index=0, mask_index=0,
                frames=burst,
                frames_avg=burst.mean(axis=0),
                camera_meta={},
            )

        with h5py.File(tmp_h5_path, "r") as f:
            assert "frames" in f["raw"]
            assert int(f["raw/frames"].shape[0]) == 1
            assert int(f["raw/frames"].shape[1]) == 3
            assert f["raw/frames"].dtype == np.uint16
            assert f["raw"].attrs["burst_stored_dtype"] == "preserve_input"

    def test_custom_raw_storage_policy_controls_dtype_and_compression(
        self, sample_plan: CapturePlan, tmp_h5_path: Path
    ) -> None:
        policy = RawFrameStoragePolicy(
            frames_avg_stored_dtype="float64",
            burst_stored_dtype="float32",
            compression=None,
        )
        sample_plan.store_burst = True
        writer = RawCaptureWriter(tmp_h5_path, sample_plan, storage_policy=policy)

        with writer:
            burst = np.ones((5, 16, 20), dtype=np.uint16)
            writer.append_capture(
                capture_index=0,
                wavelength_index=0,
                mask_index=0,
                frames=burst,
                frames_avg=burst.mean(axis=0),
                camera_meta={},
            )

        with h5py.File(tmp_h5_path, "r") as f:
            assert f["raw/frames_avg"].dtype == np.float64
            assert f["raw/frames"].dtype == np.float32
            assert f["raw/frames_avg"].compression is None
            assert f["raw/frames"].compression is None
            policy_json = f["raw"].attrs["storage_policy_json"]
            assert "frames_avg_stored_dtype" in policy_json

    def test_mapping_policy_stored(
        self, sample_plan: CapturePlan, tmp_h5_path: Path
    ) -> None:
        writer = RawCaptureWriter(tmp_h5_path, sample_plan)
        with writer:
            writer.write_lcd_metadata({"subpixel_axis": 1})

        with h5py.File(tmp_h5_path, "r") as f:
            mp = _h5_str(f["lcd/mapping_policy_json"])
            assert "physical_mono" in mp
            assert "display_rgb" in mp
            assert "subpixel_axis" in mp
            md = _h5_str(f["lcd/metadata_json"])
            assert "subpixel_axis" in md

    def test_rejects_variable_shape_masks(
        self, sample_plan: CapturePlan, tmp_h5_path: Path
    ) -> None:
        writer = RawCaptureWriter(tmp_h5_path, sample_plan)
        writer.open()
        with pytest.raises(RawCaptureWriteError, match="same shape"):
            writer.write_physical_masks([
                np.zeros((100, 300), dtype=np.uint8),
                np.zeros((200, 300), dtype=np.uint8),
            ])
