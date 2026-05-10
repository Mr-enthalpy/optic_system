from __future__ import annotations

from pathlib import Path

import h5py
import numpy as np
import pytest

from tasks.capture_plan import CapturePlan
from tasks.raw_capture_h5 import RawCaptureWriteError, RawCaptureWriter


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
            assert f.attrs["software_version"] == "optic_system phase2"

    def test_plan_json_stored(self, sample_plan: CapturePlan, tmp_h5_path: Path) -> None:
        writer = RawCaptureWriter(tmp_h5_path, sample_plan)
        with writer:
            pass

        with h5py.File(tmp_h5_path, "r") as f:
            plan_json = _h5_str(f["capture/plan_json"])
            assert "test_plan_01" in plan_json

    def test_processing_flags_written(
        self, sample_plan: CapturePlan, tmp_h5_path: Path
    ) -> None:
        writer = RawCaptureWriter(tmp_h5_path, sample_plan)
        with writer:
            pass

        with h5py.File(tmp_h5_path, "r") as f:
            pf = _h5_str(f["capture/processing_flags_json"])
            assert "scientific_calibration_valid" in pf
            assert "phase2_minimal_capture" in pf
            assert "completed" in pf

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
            assert avg[0, 0, 0] == 1.0

            cap = f["capture"]
            assert int(cap["capture_index"][0]) == 0
            assert int(cap["wavelength_index"][0]) == 0
            assert int(cap["mask_index"][0]) == 0
            assert bool(cap["completed"][0]) is True

            cam = f["camera"]
            assert float(cam["exposure_us"][0]) == 10000.0

            tls_ds = f["tls"]
            assert float(tls_ds["wavelength_nm"][0]) == 532.0

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
            "wavelengths": [{"wavelength_nm": 500}],
            "masks": [{"mask_id": "m1"}],
            "camera": {"frames_per_capture": 3},
            "store_burst": True,
        })
        writer = RawCaptureWriter(tmp_h5_path, plan)
        with writer:
            burst = np.ones((3, 120, 160), dtype=np.float64)
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

    def test_mapping_policy_stored(
        self, sample_plan: CapturePlan, tmp_h5_path: Path
    ) -> None:
        writer = RawCaptureWriter(tmp_h5_path, sample_plan)
        with writer:
            pass

        with h5py.File(tmp_h5_path, "r") as f:
            mp = _h5_str(f["lcd/mapping_policy_json"])
            assert "physical_mono" in mp
            assert "display_rgb" in mp

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
