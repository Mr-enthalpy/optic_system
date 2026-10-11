from dataclasses import replace
import json

import h5py
import numpy as np
import pytest

from tasks.capture_plan import CapturePlan
from tasks.capture_dataset import CaptureFrames, run_capture_dataset
from tasks.testing import FakeDeviceBundle, FakeLCD
from tasks.profiles import CameraProfile, PupilProfile
from tasks.psf import ROI, build_full_frame_psf_survey, extract_roi_psf, relative_response, roi_from_center
from tasks.psf.sensor_energy_center import derive_sensor_energy_center_profile, estimate_frame_energy_center
from tasks.psf.sensor_energy_center import SensorEnergyCenterProfile
from tasks.raw_capture_h5 import RawCaptureWriter


@pytest.fixture
def measured(tmp_path):
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


def test_raw_survey_roi_preserves_amplitude_and_metadata(measured):
    raw, survey, base, *_ = measured
    before = raw.read_bytes()
    from scripts.inspect_raw_capture import capture_summary
    summary = capture_summary(raw)
    assert summary["processing_flags"]["capture_complete"] is True
    assert summary["processing_flags"]["run_succeeded"] is True
    assert [item["effective_wavelength_nm"] for item in summary["illumination"]] == [450, 650]
    assert summary["camera_settings"]["readback_exposure_us"] == [100, 100, 200, 200]
    a = extract_roi_psf(survey, wavelength_index=0, display_index=0, roi=ROI(4, 3, 3, 3))
    b = extract_roi_psf(survey, wavelength_index=1, display_index=0, roi=ROI(4, 3, 3, 3))
    np.testing.assert_array_equal(a.psf, base[3:6, 4:7])
    np.testing.assert_array_equal(b.psf, a.psf * 3)
    assert b.metadata["wavelength_nm"] == 650
    assert b.metadata["display_state_id"] == "fixed"
    assert b.metadata["camera_settings"]["readback_exposure_us"] == 200
    assert b.metadata["source_capture_index"] == 2
    assert b.metadata["lcd_metadata"]["physical_shape"] == [6, 9]
    assert b.metadata["illumination"]["mode"] == "monochromatic"
    assert b.metadata["roi_xywh"] == [4, 3, 3, 3]
    np.testing.assert_allclose(relative_response(b, exposure_us=200), b.psf / 200)
    assert relative_response(b, normalize_sum=True).sum() == pytest.approx(1)
    assert raw.read_bytes() == before
    with h5py.File(survey) as f:
        np.testing.assert_array_equal(f["full_frame_survey/frames_avg"][0], base)


def test_center_profile_roi_and_coordinate_rejection(measured, tmp_path):
    _, survey, _, *_ = measured
    profile = derive_sensor_energy_center_profile(survey, tmp_path / "center.json")
    assert SensorEnergyCenterProfile.load_json(tmp_path / "center.json").to_dict() == profile.to_dict()
    result = extract_roi_psf(survey, wavelength_index=0, display_index=1, center_profile=profile, roi_shape_hw=(3, 5))
    assert result.psf.shape == (3, 5)
    assert result.metadata["center_source"]["profile_id"] == profile.center_profile_id
    assert profile.per_entry_total_corr_energy == [45, 90, 135, 180]
    assert profile.per_entry_background_value == [0] * 4
    assert not any(profile.per_entry_fallback_used)
    for bad in (replace(profile, coordinate_frame="acquired_frame"), replace(profile, camera_frame_shape=(10, 11)), replace(profile, source_survey_h5=str(tmp_path / "other.h5"))):
        with pytest.raises(ValueError):
            extract_roi_psf(survey, wavelength_index=0, display_index=0, center_profile=bad, roi_shape_hw=(3, 3))


@pytest.mark.parametrize("roi", [ROI(-1, 0, 3, 3), ROI(0, 0, 12, 3), ROI(0, 0, 3, 10), ROI(0, 0, 0, 3), ROI(0.5, 0, 3, 3)])
def test_roi_geometry_rejected(measured, roi):
    with pytest.raises(ValueError):
        extract_roi_psf(measured[1], wavelength_index=0, display_index=0, roi=roi)


@pytest.mark.parametrize("wi,di", [(-1, 0), (2, 0), (0, 2), (0, -1), (True, 0), (0.5, 0)])
def test_wrong_indices_rejected(measured, wi, di):
    with pytest.raises(ValueError):
        extract_roi_psf(measured[1], wavelength_index=wi, display_index=di, roi=ROI(0, 0, 3, 3))


def test_survey_rejects_corrupt_shape_and_versions(measured):
    _, survey, *_ = measured
    with h5py.File(survey, "r+") as f:
        f["full_frame_survey/camera_frame_extent_json"][()] = json.dumps({"mode": "full_sensor", "origin_xy": [0, 0], "shape_hw": [9, 12], "sensor_shape_hw": [9, 12]})
    with pytest.raises(ValueError, match="extent"):
        extract_roi_psf(survey, wavelength_index=0, display_index=0, roi=ROI(0, 0, 3, 3))


@pytest.mark.parametrize("damage", ["version", "index", "failure", "dimensions"])
def test_raw_conversion_rejects_invalid_run(measured, tmp_path, damage):
    raw, _, _, kwargs, *_ = measured
    with h5py.File(raw, "r+") as f:
        if damage == "version": f.attrs["raw_capture_schema_version"] = 2
        elif damage == "index": f["capture/wavelength_index"][0] = 99
        elif damage == "dimensions":
            del f["capture/mask_index"]
            f["capture"].create_dataset("mask_index", data=[0])
        else:
            flags = json.loads(f["capture/processing_flags_json"][()].decode())
            flags["run_succeeded"] = False
            f["capture/processing_flags_json"][()] = json.dumps(flags)
    with pytest.raises(ValueError):
        build_full_frame_psf_survey(**{**kwargs, "output_h5": tmp_path / "invalid.h5"})


def test_no_measurement_overwrite(measured):
    raw, survey, _, kwargs, plan, *_ = measured
    before = raw.read_bytes()
    with pytest.raises(ValueError): build_full_frame_psf_survey(**{**kwargs, "output_h5": raw})
    with pytest.raises(ValueError): build_full_frame_psf_survey(**kwargs)
    with pytest.raises(ValueError): build_full_frame_psf_survey(**{**kwargs, "manifest_path": raw})
    with pytest.raises(ValueError): derive_sensor_energy_center_profile(survey, raw)
    with pytest.raises(FileExistsError): RawCaptureWriter(raw, plan).open()
    assert raw.read_bytes() == before


@pytest.mark.parametrize("damage", ["duplicate", "dimensions", "float_indices"])
def test_roi_rejects_invalid_survey_indices(measured, damage):
    survey = measured[1]
    with h5py.File(survey, "r+") as f:
        grp = f["full_frame_survey"]
        if damage == "duplicate":
            grp["mask_index"][1] = 0
        else:
            del grp["wavelength_index"]
            grp.create_dataset("wavelength_index", data=([0] if damage == "dimensions" else [0.0, 0.0, 1.0, 1.0]))
    with pytest.raises(ValueError):
        extract_roi_psf(survey, wavelength_index=0, display_index=0, roi=ROI(0, 0, 3, 3))


def test_valid_domain_and_fallback():
    frame = np.zeros((5, 7))
    frame[0, 0], frame[2, 3] = 1000, 10
    valid = np.ones(frame.shape, bool)
    valid[0, 0] = False
    estimate = estimate_frame_energy_center(frame, valid_pixel_mask=valid)
    assert estimate.center_xy == (3, 2)
    assert estimate.total_corr_energy == 10
    assert estimate_frame_energy_center(np.ones((5, 7))).fallback_used
    with pytest.raises(ValueError): estimate_frame_energy_center(frame, valid_pixel_mask=valid.astype(int))
    assert roi_from_center((3, 2), (3, 5)) == ROI(1, 1, 5, 3)


def test_capture_applies_per_band_profile_and_records_readback(measured, tmp_path):
    _, _, _, _, plan, camera_profile, pupil_profile = measured
    from tasks.profiles.scan_pupil_broadband import circular_window_mask
    mask = circular_window_mask((6, 9), center=(4, 2), radius=2)
    for entry in plan.masks: entry.array = mask
    plan.camera.frame_extent = {"mode": "full_sensor", "origin_xy": [0, 0], "shape_hw": [9, 11], "sensor_shape_hw": [9, 11]}
    class Camera:
        is_fake = True
        def __init__(self): self.settings = {}; self.applied = []
        def apply_camera_params(self, **kw): self.settings = kw; self.applied.append(kw)
        def read_camera_params(self): return {**self.settings, "exposure_us": self.settings["exposure_us"] + 1}
        def acquire_burst(self, k):
            frames = np.ones((k, 9, 11)) * self.settings["exposure_us"]
            return CaptureFrames(frames, frames.mean(0), {})
    camera = Camera()
    lcd = FakeLCD(height=6, width_phys=9)
    bundle = FakeDeviceBundle(camera=camera, lcd=lcd, tls=None)
    raw = tmp_path / "profile_capture.h5"
    run_capture_dataset(plan, bundle, raw, runtime_policy="no_hardware", camera_profile=camera_profile, pupil_profile=pupil_profile)
    assert [s["exposure_us"] for s in camera.applied] == [100, 200]
    with h5py.File(raw) as f:
        np.testing.assert_array_equal(f["camera/readback_exposure_us"], [101, 101, 201, 201])
        assert json.loads(f["capture/processing_flags_json"][()].decode())["capture_complete"]
    with pytest.raises(ValueError):
        run_capture_dataset(plan, bundle, tmp_path / "missing.h5", runtime_policy="no_hardware")
