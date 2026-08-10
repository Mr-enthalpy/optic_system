from __future__ import annotations

import json
from pathlib import Path

import h5py
import numpy as np
import pytest
import tasks.raw_capture_h5 as raw_capture_module

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
            assert int(f.attrs["raw_capture_schema_version"]) == 3
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
            assert pf["raw_capture_schema_version"] == 3
            assert pf["capture_role"] == "minimal_capture"
            assert "phase" not in pf
            assert pf["capture_complete"] is False
            assert pf["run_succeeded"] is True
            assert pf["n_captures_written"] == 0
            assert pf["n_captures_total"] == sample_plan.n_captures

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
                wi = ci // sample_plan.n_masks
                mi = ci % sample_plan.n_masks
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
            pf = json.loads(_h5_str(f["capture/processing_flags_json"]))
            assert pf["capture_complete"] is True
            assert pf["run_succeeded"] is True

    def test_rejects_duplicate_committed_capture(
        self, sample_plan: CapturePlan, tmp_h5_path: Path
    ) -> None:
        writer = RawCaptureWriter(tmp_h5_path, sample_plan)
        with writer:
            writer.append_capture(
                capture_index=0,
                wavelength_index=0,
                mask_index=0,
                frames=np.array([]),
                frames_avg=np.ones((2, 3), dtype=np.float32),
                camera_meta={},
            )
            with pytest.raises(RawCaptureWriteError, match="already been committed"):
                writer.append_capture(
                    capture_index=0,
                    wavelength_index=0,
                    mask_index=0,
                    frames=np.array([]),
                    frames_avg=np.ones((2, 3), dtype=np.float32),
                    camera_meta={},
                )

    def test_rejects_capture_index_that_disagrees_with_plan_schedule(
        self, sample_plan: CapturePlan, tmp_h5_path: Path
    ) -> None:
        writer = RawCaptureWriter(tmp_h5_path, sample_plan)
        with writer:
            with pytest.raises(RawCaptureWriteError, match="Cartesian schedule"):
                writer.append_capture(
                    capture_index=1,
                    wavelength_index=0,
                    mask_index=0,
                    frames=np.array([]),
                    frames_avg=np.ones((2, 3), dtype=np.float32),
                    camera_meta={},
                )

    def test_rejects_out_of_order_capture_commit(
        self, sample_plan: CapturePlan, tmp_h5_path: Path
    ) -> None:
        writer = RawCaptureWriter(tmp_h5_path, sample_plan)
        with writer:
            with pytest.raises(RawCaptureWriteError, match="wavelength-major order"):
                writer.append_capture(
                    capture_index=2,
                    wavelength_index=1,
                    mask_index=0,
                    frames=np.array([]),
                    frames_avg=np.ones((2, 3), dtype=np.float32),
                    camera_meta={},
                )

    def test_failure_records_partial_data(
        self, sample_plan: CapturePlan, tmp_h5_path: Path
    ) -> None:
        writer = RawCaptureWriter(tmp_h5_path, sample_plan)
        with pytest.raises(RuntimeError, match="simulated failure"):
            with writer:
                writer.append_capture(
                    capture_index=0, wavelength_index=0, mask_index=0,
                    frames=np.array([]),
                    frames_avg=np.ones((240, 320), dtype=np.float64),
                    camera_meta={},
                )
                raise RuntimeError("simulated failure")

        with h5py.File(tmp_h5_path, "r") as f:
            pf = json.loads(_h5_str(f["capture/processing_flags_json"]))
            assert pf["capture_complete"] is False
            assert pf["run_succeeded"] is False
            assert pf["error"] == "simulated failure"
            assert pf["n_captures_written"] == 1

    def test_metadata_failure_does_not_commit_row(
        self,
        sample_plan: CapturePlan,
        tmp_h5_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        def reject_camera_metadata(*_args, **_kwargs):
            raise ValueError("invalid camera metadata")

        writer = RawCaptureWriter(tmp_h5_path, sample_plan)
        writer.open()
        monkeypatch.setattr(
            raw_capture_module,
            "camera_frame_extent_from_camera_metadata",
            reject_camera_metadata,
        )

        with pytest.raises(ValueError, match="invalid camera metadata"):
            writer.append_capture(
                capture_index=0,
                wavelength_index=0,
                mask_index=0,
                frames=np.array([]),
                frames_avg=np.ones((2, 3), dtype=np.float32),
                camera_meta={},
            )

        assert writer._file is not None
        assert bool(writer._file["capture/completed"][0]) is False
        writer.finalize(error="invalid camera metadata")

    def test_failure_after_partial_hdf5_writes_does_not_commit_row(
        self,
        sample_plan: CapturePlan,
        tmp_h5_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        writer = RawCaptureWriter(tmp_h5_path, sample_plan)
        writer.open()

        def reject_wavelength_metadata(*_args, **_kwargs) -> None:
            raise RawCaptureWriteError("simulated shared metadata failure")

        monkeypatch.setattr(
            writer,
            "_write_or_validate_wavelength_metadata",
            reject_wavelength_metadata,
        )
        frame = np.arange(12, dtype=np.float32).reshape(3, 4)

        with pytest.raises(RawCaptureWriteError, match="shared metadata failure"):
            writer.append_capture(
                capture_index=0,
                wavelength_index=0,
                mask_index=0,
                frames=np.array([]),
                frames_avg=frame,
                camera_meta={"exposure_us": 123.0},
            )

        assert writer._file is not None
        np.testing.assert_array_equal(writer._file["raw/frames_avg"][0], frame)
        assert float(writer._file["camera/readback_exposure_us"][0]) == 123.0
        assert bool(writer._file["capture/completed"][0]) is False
        writer.finalize(error="simulated shared metadata failure")

        with h5py.File(tmp_h5_path, "r") as f:
            flags = json.loads(_h5_str(f["capture/processing_flags_json"]))
            assert flags["n_captures_written"] == 0
            assert flags["last_completed_capture_index"] == -1

    def test_committed_frame_shape_cannot_be_resized_by_later_capture(
        self, sample_plan: CapturePlan, tmp_h5_path: Path
    ) -> None:
        sample_plan.store_burst = True
        writer = RawCaptureWriter(tmp_h5_path, sample_plan)
        writer.open()
        first_frame = np.arange(16, dtype=np.float32).reshape(4, 4)
        first_burst = np.stack(
            [first_frame + index for index in range(sample_plan.camera.frames_per_capture)]
        )
        writer.append_capture(
            capture_index=0,
            wavelength_index=0,
            mask_index=0,
            frames=first_burst,
            frames_avg=first_frame,
            camera_meta={},
        )

        with pytest.raises(RawCaptureWriteError, match="spatial shape cannot change"):
            writer.append_capture(
                capture_index=1,
                wavelength_index=0,
                mask_index=1,
                frames=np.ones(
                    (sample_plan.camera.frames_per_capture, 2, 2),
                    dtype=np.float32,
                ),
                frames_avg=np.ones((2, 2), dtype=np.float32),
                camera_meta={},
            )
        writer.finalize(error="frame shape changed")

        with h5py.File(tmp_h5_path, "r") as f:
            assert f["raw/frames_avg"].shape == (sample_plan.n_captures, 4, 4)
            assert f["raw/frames"].shape == (
                sample_plan.n_captures,
                sample_plan.camera.frames_per_capture,
                4,
                4,
            )
            np.testing.assert_array_equal(f["raw/frames_avg"][0], first_frame)
            np.testing.assert_array_equal(f["raw/frames"][0], first_burst)
            np.testing.assert_array_equal(
                f["capture/completed"][:],
                np.array([True, False, False, False]),
            )
            flags = json.loads(_h5_str(f["capture/processing_flags_json"]))
            assert flags["n_captures_written"] == 1

    def test_rejects_changes_to_committed_wavelength_metadata(
        self, sample_plan: CapturePlan, tmp_h5_path: Path
    ) -> None:
        writer = RawCaptureWriter(tmp_h5_path, sample_plan)
        writer.open()
        initial_tls = {"grating": 1, "timestamp_ns": 111, "state": "ready"}
        writer.append_capture(
            capture_index=0,
            wavelength_index=0,
            mask_index=0,
            frames=np.array([]),
            frames_avg=np.ones((3, 4), dtype=np.float32),
            camera_meta={},
            tls_status=initial_tls,
        )

        with pytest.raises(RawCaptureWriteError, match="shared wavelength metadata"):
            writer.append_capture(
                capture_index=1,
                wavelength_index=0,
                mask_index=1,
                frames=np.array([]),
                frames_avg=np.full((3, 4), 2.0, dtype=np.float32),
                camera_meta={},
                tls_status={"grating": 1, "timestamp_ns": 222, "state": "changed"},
            )
        writer.finalize(error="wavelength metadata changed")

        with h5py.File(tmp_h5_path, "r") as f:
            assert int(f["tls/timestamp_ns"][0]) == 111
            assert json.loads(_h5_array_str(f["tls/status_json"])) == initial_tls
            np.testing.assert_array_equal(
                f["capture/completed"][:],
                np.array([True, False, False, False]),
            )

    def test_complete_capture_can_record_later_run_failure(
        self, sample_plan: CapturePlan, tmp_h5_path: Path
    ) -> None:
        writer = RawCaptureWriter(tmp_h5_path, sample_plan)
        writer.open()
        for capture_index in range(sample_plan.n_captures):
            writer.append_capture(
                capture_index=capture_index,
                wavelength_index=capture_index // sample_plan.n_masks,
                mask_index=capture_index % sample_plan.n_masks,
                frames=np.array([]),
                frames_avg=np.ones((2, 3), dtype=np.float32),
                camera_meta={},
            )
        writer.finalize(error="post-capture failure")

        with h5py.File(tmp_h5_path, "r") as f:
            pf = json.loads(_h5_str(f["capture/processing_flags_json"]))
            assert pf["capture_complete"] is True
            assert pf["run_succeeded"] is False
            assert pf["error"] == "post-capture failure"

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
