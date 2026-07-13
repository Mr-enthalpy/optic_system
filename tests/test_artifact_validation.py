from __future__ import annotations

import json
from pathlib import Path

import h5py
import numpy as np
import pytest

import tasks.artifacts.validation as artifact_validation
import tasks.raw_capture_h5 as raw_capture_h5
from tasks.artifact_versioning import schema_compat
from tasks.artifacts.validation import (
    VALIDATOR_REGISTRY,
    ValidityOutcome,
    check_validity,
)
from tasks.capture_plan import CapturePlan
from tasks.profiles import CameraProfile, PupilProfile
from tasks.profiles.camera_profile import ProfileError
from tasks.psf.analyze_diffraction_support import (
    DiffractionSupportAnalysisError,
    PeakSupportAnalysisManifest,
)
from tasks.psf.build_full_frame_psf_survey import (
    FullFramePSFSurveyError,
    FullFramePSFSurveyManifest,
)
from tasks.psf.build_peak_patch_psf_dictionary import (
    PeakPatchPSFDictionaryError,
    PeakPatchPSFDictionaryManifest,
)
from tasks.psf.derive_peak_layout_profile import (
    PeakLayoutProfileError,
    PeakLayoutProfileManifest,
)
from tasks.psf.sensor_energy_center import (
    SensorEnergyCenterError,
    SensorEnergyCenterProfile,
)
from tasks.raw_capture_h5 import RawCaptureWriter


_STRING_DTYPE = h5py.string_dtype(encoding="utf-8")


def _extent(shape: tuple[int, int]) -> dict[str, object]:
    height, width = shape
    return {
        "mode": "full_sensor",
        "origin_xy": [0, 0],
        "shape_hw": [height, width],
        "sensor_shape_hw": [height, width],
    }


def _pupil_profile() -> PupilProfile:
    return PupilProfile.from_dict(
        {
            "pupil_profile_id": "pupil_validation_v1",
            "lcd_coordinate_convention": "physical_mono_xy",
            "lcd_display_index": 1,
            "subpixel_axis": 1,
            "lcd_physical_center": [10.0, 20.0],
            "lcd_physical_radius": 5.0,
        }
    )


def _camera_profile() -> CameraProfile:
    return CameraProfile.from_dict(
        {
            "camera_profile_id": "camera_validation_v1",
            "profile_family": "broadband_passthrough",
            "illumination": {
                "mode": "broadband_passthrough",
                "tls_setpoint_nm": 0,
                "effective_wavelength_nm": None,
                "source": "test",
            },
            "lcd_state": {"mode": "safe_probe_mask"},
            "camera": {"exposure_us": 100.0, "gain_db": 0.0},
            "valid_for": ["pupil_scan_broadband"],
        }
    )


def _sensor_center_profile(shape: tuple[int, int] = (2, 3)) -> SensorEnergyCenterProfile:
    return SensorEnergyCenterProfile(
        center_profile_id="center_validation_v1",
        source_survey_h5="survey.h5",
        coordinate_frame="sensor_full_frame",
        camera_frame_extent=_extent(shape),
        center_xy=(1.0, 0.5),
        estimator_name="energy_weighted",
        bg_policy={"method": "percentile", "percentile": 5.0},
        corr_policy={"formula": "corr=max(frame-bg,0)"},
        aggregation_policy={"method": "mean"},
        per_entry_center_xy=[(1.0, 0.5)],
        per_entry_mask_ids=["mask_1"],
        per_entry_wavelengths_nm=[550.0],
        per_entry_background_value=[0.0],
        per_entry_total_corr_energy=[1.0],
        per_entry_fallback_used=[False],
        per_wavelength_mean_center_xy={"550": (1.0, 0.5)},
        per_wavelength_center_std_xy={"550": (0.0, 0.0)},
        global_center_std_xy=(0.0, 0.0),
        max_center_deviation_px=0.0,
        camera_frame_shape=shape,
    )


def _layout_manifest(shape: tuple[int, int] = (2, 3)) -> PeakLayoutProfileManifest:
    return PeakLayoutProfileManifest(
        peak_layout_id="layout_validation_v1",
        source_survey_h5="survey.h5",
        frame_shape=shape,
        coordinate_frame="sensor_full_frame",
        camera_frame_extent=_extent(shape),
        peak_ids=["peak_1"],
        center_xy=[[1.5, 0.5]],
        patch_shape_hw=[[2, 2]],
        patch_origin_xy=[[1, 0]],
        stability_score=[0.75],
        amplitude_range=[[1.0, 2.0]],
        local_background_stats=[{"median": 0.0, "std": 0.0}],
        survey_wavelengths_nm=[550.0],
        survey_mask_ids=["mask_1"],
        valid_wavelengths_nm=[550.0],
        valid_mask_ids=["mask_1"],
        validity_scope={"mask_scope": "survey_only"},
        detection_policy={"algorithm": "test"},
        center_xy_rel=[[0.5, 0.0]],
    )


def _survey_manifest(shape: tuple[int, int] = (2, 3)) -> FullFramePSFSurveyManifest:
    return FullFramePSFSurveyManifest(
        survey_id="survey_validation_v1",
        source_raw_capture_h5="raw_capture.h5",
        pupil_profile_id=None,
        camera_profile_id=None,
        illumination_mode="monochromatic",
        entry_wavelengths_nm=[550.0],
        entry_illumination_json=[
            json.dumps(
                {
                    "mode": "monochromatic",
                    "effective_wavelength_nm": 550.0,
                    "tls_setpoint_nm": 550.0,
                    "wavelength_label_nm": 550.0,
                },
                sort_keys=True,
            )
        ],
        entry_mask_ids=["mask_1"],
        unique_wavelengths_nm=[550.0],
        unique_mask_ids=["mask_1"],
        frame_shape=shape,
        camera_frame_extent=_extent(shape),
        survey_policy={"background": "none", "normalization": "none"},
        full_frame_role="scout",
    )


def _dictionary_manifest(shape: tuple[int, int] = (2, 3)) -> PeakPatchPSFDictionaryManifest:
    return PeakPatchPSFDictionaryManifest(
        dictionary_id="dictionary_validation_v1",
        source_raw_capture_h5="raw_capture.h5",
        peak_layout_profile="layout.json",
        pupil_profile_id=None,
        camera_profile_id=None,
        illumination_mode="monochromatic",
        entry_wavelengths_nm=[550.0],
        entry_mask_ids=["mask_1"],
        unique_wavelengths_nm=[550.0],
        unique_mask_ids=["mask_1"],
        frame_shape=shape,
        camera_frame_extent=_extent(shape),
        peak_layout_coordinate_frame="sensor_full_frame",
        peak_layout_camera_frame_extent=_extent(shape),
        peak_ids=["peak_1"],
        patch_shape_hw=[[2, 2]],
        patch_origin_xy=[[1, 0]],
        applied_background_policy="none",
        applied_normalization_policy="none",
    )


def _support_manifest(shape: tuple[int, int] = (2, 3)) -> PeakSupportAnalysisManifest:
    return PeakSupportAnalysisManifest(
        report_id="support_validation_v1",
        source_survey_h5="survey.h5",
        frame_shape=shape,
        coordinate_frame="sensor_full_frame",
        camera_frame_extent=_extent(shape),
        tau_values=[0.5],
        support_radii=[1.0],
        bg_policy={"method": "percentile"},
        corr_policy={"formula": "corr=max(psf-bg,0)"},
        radial_policy={"center_policy": "frame_center"},
        component_policy={
            "analysis_mode": "energy_only",
            "component_table_written": False,
        },
        entry_mask_ids=["mask_1"],
        entry_wavelengths_nm=[550.0],
    )


_BROADBAND_WAVELENGTH_FIELDS = {
    "full_frame_psf_survey": ("entry_wavelengths_nm", "unique_wavelengths_nm"),
    "sensor_energy_center_profile": ("per_entry_wavelengths_nm",),
    "peak_support_analysis_report": ("entry_wavelengths_nm",),
    "peak_layout_profile": ("survey_wavelengths_nm", "valid_wavelengths_nm"),
    "peak_patch_psf_dictionary": ("entry_wavelengths_nm", "unique_wavelengths_nm"),
}


def _artifact_for_type(artifact_type: str):
    return {
        "full_frame_psf_survey": _survey_manifest,
        "sensor_energy_center_profile": _sensor_center_profile,
        "peak_support_analysis_report": _support_manifest,
        "peak_layout_profile": _layout_manifest,
        "peak_patch_psf_dictionary": _dictionary_manifest,
    }[artifact_type]()


def _versioned_manifest_for_type(artifact_type: str) -> dict[str, object]:
    artifact = _artifact_for_type(artifact_type)
    data = artifact.to_dict()
    for field in _BROADBAND_WAVELENGTH_FIELDS[artifact_type]:
        values = data[field]
        assert isinstance(values, list)
        data[field] = [float("nan")] * len(values)
    return data


def _write_scalar_text(group: h5py.Group, name: str, text: str) -> None:
    group.create_dataset(name, data=text, dtype=_STRING_DTYPE)


def _write_text_array(group: h5py.Group, name: str, values: list[str]) -> None:
    group.create_dataset(
        name,
        data=np.asarray(values, dtype=object),
        dtype=_STRING_DTYPE,
    )


def _source_plan_json() -> str:
    return json.dumps(
        {
            "plan_id": "validation_plan_v1",
            "wavelengths": [
                {
                    "settle_ms": 0,
                    "illumination": {
                        "mode": "monochromatic",
                        "effective_wavelength_nm": 550.0,
                        "tls_setpoint_nm": 550.0,
                        "wavelength_label_nm": 550.0,
                    },
                }
            ],
            "masks": [{"mask_id": "mask_1"}],
        },
        sort_keys=True,
    )


def _write_derived_source_plan(h5: h5py.File) -> None:
    source = h5.require_group("source")
    _write_scalar_text(source, "plan_json", _source_plan_json())


def _write_raw_capture(path: Path) -> None:
    plan = CapturePlan.from_dict(
        {
            "plan_id": "validation_plan_v1",
            "wavelengths": [
                {
                    "settle_ms": 0,
                    "illumination": {
                        "mode": "monochromatic",
                        "effective_wavelength_nm": 550.0,
                        "tls_setpoint_nm": 550.0,
                        "wavelength_label_nm": 550.0,
                    },
                }
            ],
            "masks": [{"mask_id": "mask_1"}],
            "camera": {"frames_per_capture": 1},
            "store_burst": False,
        }
    )
    with RawCaptureWriter(path, plan) as writer:
        writer.write_runtime_metadata({"mode": "test"})
        writer.write_lcd_metadata({"subpixel_axis": 1})
        writer.write_physical_masks([np.ones((2, 3), dtype=np.uint8)])
        writer.append_capture(
            capture_index=0,
            wavelength_index=0,
            mask_index=0,
            frames=None,
            frames_avg=np.zeros((2, 3), dtype=np.float32),
            camera_meta={},
        )


def _multi_capture_plan() -> CapturePlan:
    return CapturePlan.from_dict(
        {
            "plan_id": "multi_capture_validation_plan_v1",
            "wavelengths": [
                {
                    "illumination": {
                        "mode": "monochromatic",
                        "effective_wavelength_nm": wavelength,
                        "tls_setpoint_nm": wavelength,
                        "wavelength_label_nm": wavelength,
                    }
                }
                for wavelength in (500.0, 600.0)
            ],
            "masks": [{"mask_id": "mask_1"}, {"mask_id": "mask_2"}],
            "camera": {"frames_per_capture": 1},
            "store_burst": False,
        }
    )


def _write_multi_raw_capture(path: Path, *, capture_count: int = 4) -> None:
    plan = _multi_capture_plan()
    with RawCaptureWriter(path, plan) as writer:
        writer.write_lcd_metadata({"subpixel_axis": 1})
        writer.write_physical_masks(
            [
                np.ones((2, 3), dtype=np.uint8),
                np.ones((2, 3), dtype=np.uint8),
            ]
        )
        for capture_index in range(capture_count):
            wavelength_index = capture_index // plan.n_masks
            mask_index = capture_index % plan.n_masks
            writer.append_capture(
                capture_index=capture_index,
                wavelength_index=wavelength_index,
                mask_index=mask_index,
                frames=None,
                frames_avg=np.zeros((2, 3), dtype=np.float32),
                camera_meta={},
            )


def _replace_tls_status_json(path: Path, values: list[str]) -> None:
    with h5py.File(path, "r+") as h5:
        del h5["tls/status_json"]
        h5["tls"].create_dataset(
            "status_json",
            data=np.asarray(values, dtype=object),
            dtype=_STRING_DTYPE,
        )


def _write_historical_v2_raw_capture(path: Path) -> None:
    """Create a pre-v3 writer fixture without fabricating a new v2 contract."""
    _write_raw_capture(path)
    with h5py.File(path, "r+") as h5:
        h5.attrs["raw_capture_schema_version"] = 2
        del h5.attrs["artifact_type"]
        flags_value = h5["capture/processing_flags_json"][()]
        flags_text = flags_value.decode("utf-8") if isinstance(flags_value, bytes) else str(flags_value)
        flags = json.loads(flags_text)
        flags["raw_capture_schema_version"] = 2
        flags["completed"] = bool(h5["capture/completed"][:].all())
        del flags["capture_complete"]
        del flags["run_succeeded"]
        del flags["n_captures_written"]
        del flags["n_captures_total"]
        h5["capture/processing_flags_json"][()] = json.dumps(flags, sort_keys=True)

        # These metadata fields were initialized later by the v2 writer and
        # therefore cannot be required by its compatibility validator.
        h5["masks/mask_id"][0] = ""
        h5["masks/family_id"][0] = ""
        h5["masks/family_params_json"][0] = ""
        h5["lcd/mapping_policy_json"][0] = ""
        h5["lcd/metadata_json"][0] = ""


def _write_survey_h5(path: Path) -> FullFramePSFSurveyManifest:
    manifest = _survey_manifest()
    with h5py.File(path, "w") as h5:
        h5.attrs["artifact_type"] = "full_frame_psf_survey"
        h5.attrs["schema_version"] = schema_compat("full_frame_psf_survey").current
        h5.attrs["survey_id"] = manifest.survey_id
        group = h5.require_group("full_frame_survey")
        group.create_dataset("frames_avg", data=np.zeros((1, 2, 3), dtype=np.float32))
        _write_text_array(group, "entry_mask_ids", manifest.entry_mask_ids)
        group.create_dataset(
            "entry_wavelength_nm", data=np.asarray(manifest.entry_wavelengths_nm)
        )
        _write_text_array(group, "entry_illumination_json", manifest.entry_illumination_json)
        _write_text_array(group, "unique_mask_ids", manifest.unique_mask_ids)
        group.create_dataset(
            "unique_wavelength_nm", data=np.asarray(manifest.unique_wavelengths_nm)
        )
        group.create_dataset("mask_index", data=np.asarray([0], dtype=np.int64))
        group.create_dataset("wavelength_index", data=np.asarray([0], dtype=np.int64))
        group.create_dataset("capture_indices", data=np.asarray([0], dtype=np.int64))
        group.create_dataset("frame_shape", data=np.asarray(manifest.frame_shape, dtype=np.int64))
        _write_scalar_text(
            group,
            "camera_frame_extent_json",
            json.dumps(manifest.camera_frame_extent),
        )
        _write_scalar_text(group, "survey_policy_json", json.dumps(manifest.survey_policy))
        _write_scalar_text(group, "manifest_json", manifest.to_json())
        _write_derived_source_plan(h5)
    return manifest


def _write_broadband_survey_h5(path: Path) -> FullFramePSFSurveyManifest:
    shape = (2, 3)
    illumination = {
        "mode": "broadband_passthrough",
        "effective_wavelength_nm": None,
        "tls_setpoint_nm": 0.0,
        "wavelength_label_nm": None,
    }
    illumination_json = json.dumps(illumination, sort_keys=True)
    manifest = FullFramePSFSurveyManifest(
        survey_id="survey_broadband_validation_v1",
        source_raw_capture_h5="raw_capture.h5",
        pupil_profile_id=None,
        camera_profile_id=None,
        illumination_mode="broadband_passthrough",
        entry_wavelengths_nm=[float("nan"), float("nan")],
        entry_illumination_json=[illumination_json, illumination_json],
        entry_mask_ids=["mask_1", "mask_2"],
        unique_wavelengths_nm=[float("nan")],
        unique_mask_ids=["mask_1", "mask_2"],
        frame_shape=shape,
        camera_frame_extent=_extent(shape),
        survey_policy={"background": "none", "normalization": "none"},
        full_frame_role="scout",
    )
    source_plan = {
        "plan_id": "broadband_validation_plan_v1",
        "wavelengths": [{"settle_ms": 0, "illumination": illumination}],
        "masks": [{"mask_id": "mask_1"}, {"mask_id": "mask_2"}],
    }
    with h5py.File(path, "w") as h5:
        h5.attrs["artifact_type"] = "full_frame_psf_survey"
        h5.attrs["schema_version"] = schema_compat("full_frame_psf_survey").current
        h5.attrs["survey_id"] = manifest.survey_id
        group = h5.require_group("full_frame_survey")
        group.create_dataset("frames_avg", data=np.zeros((2, *shape), dtype=np.float32))
        _write_text_array(group, "entry_mask_ids", manifest.entry_mask_ids)
        group.create_dataset(
            "entry_wavelength_nm",
            data=np.asarray(manifest.entry_wavelengths_nm, dtype=np.float64),
        )
        _write_text_array(group, "entry_illumination_json", manifest.entry_illumination_json)
        _write_text_array(group, "unique_mask_ids", manifest.unique_mask_ids)
        group.create_dataset(
            "unique_wavelength_nm",
            data=np.asarray(manifest.unique_wavelengths_nm, dtype=np.float64),
        )
        group.create_dataset("mask_index", data=np.asarray([0, 1], dtype=np.int64))
        group.create_dataset("wavelength_index", data=np.asarray([0, 0], dtype=np.int64))
        group.create_dataset("capture_indices", data=np.asarray([0, 1], dtype=np.int64))
        group.create_dataset("frame_shape", data=np.asarray(shape, dtype=np.int64))
        _write_scalar_text(group, "camera_frame_extent_json", json.dumps(_extent(shape)))
        _write_scalar_text(group, "survey_policy_json", json.dumps(manifest.survey_policy))
        _write_scalar_text(group, "manifest_json", manifest.to_json())
        source = h5.require_group("source")
        _write_scalar_text(source, "plan_json", json.dumps(source_plan, sort_keys=True))
    return manifest


def _write_support_report_h5(path: Path) -> PeakSupportAnalysisManifest:
    manifest = _support_manifest()
    with h5py.File(path, "w") as h5:
        h5.attrs["artifact_type"] = "peak_support_analysis_report"
        h5.attrs["schema_version"] = schema_compat(
            "peak_support_analysis_report"
        ).current
        group = h5.require_group("support_analysis")
        group.create_dataset("tau_values", data=np.asarray(manifest.tau_values))
        group.create_dataset("support_radii", data=np.asarray(manifest.support_radii))
        group.create_dataset("frame_shape", data=np.asarray(manifest.frame_shape, dtype=np.int64))
        group.create_dataset("background_value", data=np.asarray([0.0]))
        group.create_dataset("center_xy", data=np.asarray([[1.0, 0.5]]))
        group.create_dataset("total_corr_energy", data=np.asarray([1.0]))
        group.create_dataset("compact_support_energy", data=np.asarray([[1.0]]))
        group.create_dataset("compact_support_fraction", data=np.asarray([[1.0]]))
        group.create_dataset("far_field_noise_energy", data=np.asarray([[0.0]]))
        group.create_dataset("far_field_significant_energy", data=np.asarray([[0.0]]))
        group.create_dataset("far_field_noise_pixel_count", data=np.asarray([[0]], dtype=np.int64))
        group.create_dataset(
            "far_field_significant_pixel_count", data=np.asarray([[0]], dtype=np.int64)
        )
        metadata = h5.require_group("metadata")
        _write_scalar_text(metadata, "manifest_json", manifest.to_json_text())
    return manifest


def _write_dictionary_h5(path: Path) -> PeakPatchPSFDictionaryManifest:
    manifest = _dictionary_manifest()
    with h5py.File(path, "w") as h5:
        h5.attrs["artifact_type"] = "peak_patch_psf_dictionary"
        h5.attrs["schema_version"] = schema_compat(
            "peak_patch_psf_dictionary"
        ).current
        h5.attrs["dictionary_id"] = manifest.dictionary_id
        group = h5.require_group("peak_patch_dictionary")
        group.create_dataset("patches", data=np.zeros((1, 1, 2, 2), dtype=np.float32))
        _write_text_array(group, "entry_mask_ids", manifest.entry_mask_ids)
        group.create_dataset("entry_mask_index", data=np.asarray([0], dtype=np.int64))
        group.create_dataset(
            "entry_wavelength_nm", data=np.asarray(manifest.entry_wavelengths_nm)
        )
        capture_indices = group.create_dataset(
            "entry_capture_indices",
            shape=(1,),
            dtype=h5py.vlen_dtype(np.dtype("int64")),
        )
        capture_indices[0] = np.asarray([0], dtype=np.int64)
        _write_text_array(group, "unique_mask_ids", manifest.unique_mask_ids)
        group.create_dataset(
            "unique_wavelength_nm", data=np.asarray(manifest.unique_wavelengths_nm)
        )
        _write_text_array(group, "peak_id", manifest.peak_ids)
        group.create_dataset("peak_center_xy", data=np.asarray([[1.5, 0.5]]))
        group.create_dataset(
            "patch_origin_xy", data=np.asarray(manifest.patch_origin_xy, dtype=np.int64)
        )
        group.create_dataset(
            "patch_shape_hw", data=np.asarray(manifest.patch_shape_hw, dtype=np.int64)
        )
        group.create_dataset("frame_shape", data=np.asarray(manifest.frame_shape, dtype=np.int64))
        _write_scalar_text(group, "coordinate_frame", manifest.peak_layout_coordinate_frame)
        _write_scalar_text(
            group,
            "camera_frame_extent_json",
            json.dumps(manifest.camera_frame_extent),
        )
        _write_scalar_text(
            group,
            "peak_layout_camera_frame_extent_json",
            json.dumps(manifest.peak_layout_camera_frame_extent),
        )
        _write_scalar_text(
            group,
            "background_policy_json",
            json.dumps({"applied": manifest.applied_background_policy}),
        )
        _write_scalar_text(
            group,
            "normalization_policy_json",
            json.dumps({"applied": manifest.applied_normalization_policy}),
        )
        _write_scalar_text(group, "manifest_json", manifest.to_json())
        _write_derived_source_plan(h5)
    return manifest


def test_validation_outcomes_distinguish_unsupported_and_invalid(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "pupil.json"
    _pupil_profile().to_json(path)

    monkeypatch.delitem(VALIDATOR_REGISTRY, "pupil_profile")
    unsupported = check_validity("pupil_profile", path)
    assert unsupported.outcome is ValidityOutcome.UNSUPPORTED
    assert unsupported.reason_codes == ("validator_not_implemented",)

    unknown = check_validity("not_an_artifact", path)
    assert unknown.outcome is ValidityOutcome.UNSUPPORTED
    assert unknown.reason_codes == ("unknown_artifact_type",)

    mismatched = check_validity("camera_profile", path)
    assert mismatched.outcome is ValidityOutcome.INVALID
    assert mismatched.reason_codes == ("artifact_type_mismatch",)


def test_unexpected_validator_failure_is_unsupported(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "pupil.json"
    _pupil_profile().to_json(path)

    def _broken_loader(_data: dict[str, object]) -> object:
        raise RuntimeError("validator bug")

    monkeypatch.setattr(
        artifact_validation,
        "_class_loader",
        lambda *_args: _broken_loader,
    )

    result = check_validity("pupil_profile", path)

    assert result.outcome is ValidityOutcome.UNSUPPORTED
    assert result.reason_codes == ("validator_failed",)


def test_validation_reports_legacy_and_unreadable_locations(tmp_path: Path) -> None:
    legacy_path = tmp_path / "legacy.json"
    data = _pupil_profile().to_dict()
    del data["schema_version"]
    legacy_path.write_text(json.dumps(data), encoding="utf-8")
    assert check_validity("pupil_profile", legacy_path).outcome is ValidityOutcome.LEGACY_UNVERSIONED

    unreadable_json = tmp_path / "broken.json"
    unreadable_json.write_text("{", encoding="utf-8")
    assert check_validity("pupil_profile", unreadable_json).outcome is ValidityOutcome.UNREADABLE

    non_utf8_json = tmp_path / "non_utf8.json"
    non_utf8_json.write_bytes(b"\xff\xfe")
    non_utf8_result = check_validity("pupil_profile", non_utf8_json)
    assert non_utf8_result.outcome is ValidityOutcome.UNREADABLE
    assert non_utf8_result.reason_codes == ("json_unreadable",)

    unreadable_hdf = tmp_path / "broken.h5"
    unreadable_hdf.write_bytes(b"not an HDF5 payload")
    assert check_validity("raw_capture", unreadable_hdf).outcome is ValidityOutcome.UNREADABLE


@pytest.mark.parametrize("root", ["[]", '"profile"', "1"])
def test_parsed_non_mapping_json_root_is_invalid(
    tmp_path: Path,
    root: str,
) -> None:
    path = tmp_path / "invalid_root.json"
    path.write_text(root, encoding="utf-8")

    result = check_validity("pupil_profile", path)

    assert result.outcome is ValidityOutcome.INVALID
    assert result.reason_codes == ("json_root_invalid",)


@pytest.mark.parametrize("token", ["NaN", "Infinity", "-Infinity"])
def test_nonstandard_json_numeric_constants_are_invalid(
    tmp_path: Path,
    token: str,
) -> None:
    data = _camera_profile().to_dict()
    text = json.dumps(data, allow_nan=False)
    exposure_field = '"exposure_us": 100.0'
    assert exposure_field in text
    text = text.replace(exposure_field, f'"exposure_us": {token}')
    path = tmp_path / "camera_profile.json"
    path.write_text(text, encoding="utf-8")

    result = check_validity("camera_profile", path)

    assert result.outcome is ValidityOutcome.INVALID
    assert result.reason_codes == ("json_number_nonfinite",)


@pytest.mark.parametrize("artifact_type", sorted(_BROADBAND_WAVELENGTH_FIELDS))
def test_schema_v1_broadband_nan_manifests_remain_valid(
    tmp_path: Path,
    artifact_type: str,
) -> None:
    data = _versioned_manifest_for_type(artifact_type)
    data["schema_version"] = 1
    path = tmp_path / f"{artifact_type}.v1.json"
    path.write_text(json.dumps(data, allow_nan=True), encoding="utf-8")

    result = check_validity(artifact_type, path)

    assert result.outcome is ValidityOutcome.VALID
    assert result.schema_version == 1


@pytest.mark.parametrize("artifact_type", sorted(_BROADBAND_WAVELENGTH_FIELDS))
def test_schema_v2_rejects_nonstandard_broadband_nan_json(
    tmp_path: Path,
    artifact_type: str,
) -> None:
    data = _versioned_manifest_for_type(artifact_type)
    data["schema_version"] = 2
    path = tmp_path / f"{artifact_type}.v2.json"
    path.write_text(json.dumps(data, allow_nan=True), encoding="utf-8")

    result = check_validity(artifact_type, path)

    assert result.outcome is ValidityOutcome.INVALID
    assert result.reason_codes == ("json_number_nonfinite",)


@pytest.mark.parametrize("artifact_type", sorted(_BROADBAND_WAVELENGTH_FIELDS))
def test_schema_v2_serializes_broadband_wavelength_as_null(
    artifact_type: str,
) -> None:
    artifact = _artifact_for_type(artifact_type)
    for field in _BROADBAND_WAVELENGTH_FIELDS[artifact_type]:
        values = getattr(artifact, field)
        setattr(artifact, field, [float("nan")] * len(values))
    data = artifact.to_dict()

    text = json.dumps(data, allow_nan=False)

    assert data["schema_version"] == 2
    assert "NaN" not in text


@pytest.mark.parametrize("field", ["exposure_us", "gain_db"])
@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_camera_profile_rejects_nonfinite_settings_before_serialization(
    field: str,
    value: float,
) -> None:
    data = _camera_profile().to_dict()
    data["camera"][field] = value

    with pytest.raises(ProfileError, match="finite"):
        CameraProfile.from_dict(data, legacy_mode=False)


def test_json_artifact_validators_accept_complete_structures(tmp_path: Path) -> None:
    artifacts = [
        ("camera_profile", _camera_profile()),
        ("pupil_profile", _pupil_profile()),
        ("sensor_energy_center_profile", _sensor_center_profile()),
        ("peak_layout_profile", _layout_manifest()),
        ("full_frame_psf_survey", _survey_manifest()),
        ("peak_patch_psf_dictionary", _dictionary_manifest()),
    ]
    for artifact_type, artifact in artifacts:
        path = tmp_path / f"{artifact_type}.json"
        artifact.to_json(path)
        result = check_validity(artifact_type, path)
        assert result.outcome is ValidityOutcome.VALID
        assert result.schema_version == schema_compat(artifact_type).current


@pytest.mark.parametrize(
    "field",
    [
        "per_entry_background_value",
        "per_entry_total_corr_energy",
        "per_entry_fallback_used",
    ],
)
def test_sensor_center_strict_validation_requires_diagnostic_arrays(
    tmp_path: Path,
    field: str,
) -> None:
    data = _sensor_center_profile().to_dict()
    del data[field]
    path = tmp_path / "sensor_energy_center_profile.json"
    path.write_text(json.dumps(data, allow_nan=False), encoding="utf-8")

    result = check_validity("sensor_energy_center_profile", path)

    assert result.outcome is ValidityOutcome.INVALID
    assert result.reason_codes == ("serialized_field_missing",)
    with pytest.raises(SensorEnergyCenterError, match=field):
        SensorEnergyCenterProfile.from_dict(data, legacy_mode=False)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("per_entry_background_value", float("nan"), "must be finite"),
        ("per_entry_total_corr_energy", float("inf"), "must be finite"),
        ("per_entry_total_corr_energy", -1.0, "must be nonnegative"),
        ("per_entry_fallback_used", 1, "must be boolean"),
    ],
)
def test_sensor_center_validate_rejects_invalid_diagnostic_values(
    field: str,
    value: object,
    message: str,
) -> None:
    profile = _sensor_center_profile()
    setattr(profile, field, [value])

    with pytest.raises(SensorEnergyCenterError, match=message):
        profile.validate()


def test_sensor_center_strict_validation_rejects_negative_corrected_energy(
    tmp_path: Path,
) -> None:
    data = _sensor_center_profile().to_dict()
    data["per_entry_total_corr_energy"] = [-1.0]
    path = tmp_path / "sensor_energy_center_profile.json"
    path.write_text(json.dumps(data, allow_nan=False), encoding="utf-8")

    result = check_validity("sensor_energy_center_profile", path)

    assert result.outcome is ValidityOutcome.INVALID
    assert result.reason_codes == ("serialized_contract_rejected",)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda data: data.__setitem__("frame_shape", [2.9, 3.7]),
        lambda data: data.__setitem__("peak_ids", [123]),
        lambda data: data.__setitem__("patch_shape_hw", [[1.9, 2.8]]),
        lambda data: data.__setitem__("patch_origin_xy", [[0.7, 0]]),
        lambda data: data.__setitem__("stability_score", ["0.75"]),
    ],
)
def test_strict_validation_rejects_coercive_peak_layout_fields(
    tmp_path: Path,
    mutate,
) -> None:
    data = _layout_manifest().to_dict()
    mutate(data)
    path = tmp_path / "layout.json"
    path.write_text(json.dumps(data), encoding="utf-8")

    result = check_validity("peak_layout_profile", path)

    assert result.outcome is ValidityOutcome.INVALID
    assert result.reason_codes == ("serialized_type_invalid",)


@pytest.mark.parametrize(
    "camera_frame_extent",
    [
        {
            "mode": "full_sensor",
            "origin_xy": ["0", False],
            "shape_hw": [2, 3],
            "sensor_shape_hw": [2, 3],
        },
        {
            "mode": "full_sensor",
            "origin_xy": [0, 0],
            "shape_hw": [2.9, 3.7],
            "sensor_shape_hw": [2, 3],
        },
        {
            "mode": "full_sensor",
            "origin_xy": [0, 0],
            "shape_hw": [2, 3],
            "sensor_shape_hw": [2, 3],
            "unexpected": 1,
        },
    ],
)
def test_schema_v2_rejects_coercive_camera_frame_extent_fields(
    tmp_path: Path,
    camera_frame_extent: dict[str, object],
) -> None:
    data = _survey_manifest().to_dict()
    data["camera_frame_extent"] = camera_frame_extent
    path = tmp_path / "survey.json"
    path.write_text(json.dumps(data, allow_nan=False), encoding="utf-8")

    result = check_validity("full_frame_psf_survey", path)

    assert result.outcome is ValidityOutcome.INVALID
    assert result.reason_codes == ("serialized_type_invalid",)


def test_schema_v1_retains_compatibility_camera_extent_coercion(
    tmp_path: Path,
) -> None:
    data = _survey_manifest().to_dict()
    data["schema_version"] = 1
    data["camera_frame_extent"] = {
        "mode": "full_sensor",
        "origin_xy": ["0", False],
        "shape_hw": [2.9, 3.7],
        "sensor_shape_hw": [2.9, 3.7],
        "historical_extra": "ignored by the v1 loader",
    }
    path = tmp_path / "survey.v1.json"
    path.write_text(json.dumps(data, allow_nan=False), encoding="utf-8")

    result = check_validity("full_frame_psf_survey", path)

    assert result.outcome is ValidityOutcome.VALID
    assert result.schema_version == 1


def test_json_manifest_validate_methods_reject_structural_contradictions() -> None:
    center = _sensor_center_profile()
    center.center_xy = (float("nan"), 0.0)
    with pytest.raises(SensorEnergyCenterError, match="center_xy"):
        center.validate()

    layout = _layout_manifest()
    layout.patch_origin_xy = [[2, 0]]
    with pytest.raises(PeakLayoutProfileError, match="outside"):
        layout.validate()

    survey = _survey_manifest()
    survey.unique_mask_ids = ["wrong"]
    with pytest.raises(FullFramePSFSurveyError, match="unique_mask_ids"):
        survey.validate()

    dictionary = _dictionary_manifest()
    dictionary.applied_normalization_policy = ""
    with pytest.raises(PeakPatchPSFDictionaryError, match="normalization"):
        dictionary.validate()

    dictionary = _dictionary_manifest()
    dictionary.unique_mask_ids = ["wrong"]
    with pytest.raises(PeakPatchPSFDictionaryError, match="unique_mask_ids"):
        dictionary.validate()

    dictionary = _dictionary_manifest()
    dictionary.unique_wavelengths_nm = [560.0]
    with pytest.raises(PeakPatchPSFDictionaryError, match="unique_wavelengths_nm"):
        dictionary.validate()

    support = _support_manifest()
    support.entry_wavelengths_nm = []
    with pytest.raises(DiffractionSupportAnalysisError, match="equal length"):
        support.validate()

    support = _support_manifest()
    support.component_policy["component_table_written"] = True
    with pytest.raises(DiffractionSupportAnalysisError, match="disagree"):
        support.validate()


@pytest.mark.parametrize(
    ("artifact_type", "writer"),
    [
        ("raw_capture", _write_raw_capture),
        ("full_frame_psf_survey", _write_survey_h5),
        ("peak_support_analysis_report", _write_support_report_h5),
        ("peak_patch_psf_dictionary", _write_dictionary_h5),
    ],
)
def test_hdf5_artifact_validators_accept_minimal_structures(
    tmp_path: Path,
    artifact_type: str,
    writer,
) -> None:
    path = tmp_path / f"{artifact_type}.h5"
    writer(path)

    result = check_validity(artifact_type, path)

    assert result.outcome is ValidityOutcome.VALID
    assert result.schema_version == schema_compat(artifact_type).current


def test_schema_v2_survey_hdf_rejects_coercive_payload_extent(
    tmp_path: Path,
) -> None:
    path = tmp_path / "survey_coercive_extent_v2.h5"
    _write_survey_h5(path)
    extent = {
        "mode": "full_sensor",
        "origin_xy": ["0", False],
        "shape_hw": [2.9, 3.7],
        "sensor_shape_hw": [2, 3],
    }
    with h5py.File(path, "r+") as h5:
        h5["full_frame_survey/camera_frame_extent_json"][()] = json.dumps(extent)

    result = check_validity("full_frame_psf_survey", path)

    assert result.outcome is ValidityOutcome.INVALID
    assert result.reason_codes == ("frame_extent_invalid",)


def test_schema_v1_survey_hdf_retains_compatibility_payload_extent(
    tmp_path: Path,
) -> None:
    path = tmp_path / "survey_coercive_extent_v1.h5"
    manifest = _write_survey_h5(path)
    manifest_data = manifest.to_dict()
    manifest_data["schema_version"] = 1
    extent = {
        "mode": "full_sensor",
        "origin_xy": ["0", False],
        "shape_hw": [2.9, 3.7],
        "sensor_shape_hw": [2.9, 3.7],
        "historical_extra": "ignored by the v1 loader",
    }
    with h5py.File(path, "r+") as h5:
        h5.attrs["schema_version"] = 1
        h5["full_frame_survey/manifest_json"][()] = json.dumps(manifest_data)
        h5["full_frame_survey/camera_frame_extent_json"][()] = json.dumps(extent)

    result = check_validity("full_frame_psf_survey", path)

    assert result.outcome is ValidityOutcome.VALID
    assert result.schema_version == 1


def test_raw_capture_validator_rejects_invalid_completed_index(tmp_path: Path) -> None:
    path = tmp_path / "raw_capture.h5"
    _write_raw_capture(path)
    with h5py.File(path, "r+") as h5:
        h5["capture/mask_index"][0] = 9

    result = check_validity("raw_capture", path)

    assert result.outcome is ValidityOutcome.INVALID
    assert result.reason_codes == ("mask_index_out_of_bounds",)


def test_raw_capture_validator_requires_complete_v3_schema_contract(tmp_path: Path) -> None:
    path = tmp_path / "raw_capture.h5"
    _write_raw_capture(path)
    with h5py.File(path, "r+") as h5:
        del h5["tls/status_json"]

    result = check_validity("raw_capture", path)

    assert result.outcome is ValidityOutcome.INVALID
    assert result.reason_codes == ("missing_required_path",)


def test_raw_capture_validator_requires_v3_root_artifact_type(tmp_path: Path) -> None:
    path = tmp_path / "raw_capture.h5"
    _write_raw_capture(path)
    with h5py.File(path, "r+") as h5:
        del h5.attrs["artifact_type"]

    result = check_validity("raw_capture", path)

    assert result.outcome is ValidityOutcome.INVALID
    assert result.reason_codes == ("artifact_type_missing",)


def test_raw_capture_validator_reports_newer_schema_as_unsupported(
    tmp_path: Path,
) -> None:
    path = tmp_path / "newer_raw_capture.h5"
    _write_raw_capture(path)
    with h5py.File(path, "r+") as h5:
        h5.attrs["raw_capture_schema_version"] = (
            schema_compat("raw_capture").current + 1
        )

    result = check_validity("raw_capture", path)

    assert result.outcome is ValidityOutcome.UNSUPPORTED
    assert result.reason_codes == ("schema_newer_than_supported",)


def test_raw_capture_validator_accepts_historical_v2_contract(tmp_path: Path) -> None:
    path = tmp_path / "historical_v2_raw_capture.h5"
    _write_historical_v2_raw_capture(path)

    result = check_validity("raw_capture", path)

    assert result.outcome is ValidityOutcome.VALID
    assert result.schema_version == 2


def test_raw_capture_validator_accepts_incomplete_capture_with_complete_schema(
    tmp_path: Path,
) -> None:
    path = tmp_path / "partial_raw_capture.h5"
    plan = CapturePlan.from_dict(
        {
            "plan_id": "partial_validation_plan_v1",
            "wavelengths": [
                {
                    "illumination": {
                        "mode": "monochromatic",
                        "effective_wavelength_nm": 550.0,
                        "tls_setpoint_nm": 550.0,
                        "wavelength_label_nm": 550.0,
                    }
                }
            ],
            "masks": [{"mask_id": "mask_1"}, {"mask_id": "mask_2"}],
            "camera": {"frames_per_capture": 1},
            "store_burst": False,
        }
    )
    writer = RawCaptureWriter(path, plan)
    writer.open()
    writer.write_lcd_metadata({"subpixel_axis": 1})
    writer.write_physical_masks(
        [np.ones((2, 3), dtype=np.uint8), np.ones((2, 3), dtype=np.uint8)]
    )
    writer.append_capture(
        capture_index=0,
        wavelength_index=0,
        mask_index=0,
        frames=None,
        frames_avg=np.zeros((2, 3), dtype=np.float32),
        camera_meta={},
    )
    writer.finalize()

    result = check_validity("raw_capture", path)

    assert result.outcome is ValidityOutcome.VALID


def test_raw_capture_validator_rejects_tls_status_length_without_committed_rows(
    tmp_path: Path,
) -> None:
    path = tmp_path / "raw_tls_status_empty.h5"
    _write_multi_raw_capture(path, capture_count=0)
    _replace_tls_status_json(path, [])

    result = check_validity("raw_capture", path)

    assert result.outcome is ValidityOutcome.INVALID
    assert result.reason_codes == ("entry_count_mismatch",)


def test_raw_capture_validator_rejects_tls_status_length_with_committed_row(
    tmp_path: Path,
) -> None:
    path = tmp_path / "raw_tls_status_short.h5"
    _write_multi_raw_capture(path, capture_count=1)
    _replace_tls_status_json(path, ["{}"])

    result = check_validity("raw_capture", path)

    assert result.outcome is ValidityOutcome.INVALID
    assert result.reason_codes == ("entry_count_mismatch",)


def test_raw_capture_validator_rejects_unreadable_tls_status_entry(
    tmp_path: Path,
) -> None:
    path = tmp_path / "raw_tls_status_invalid_json.h5"
    _write_multi_raw_capture(path, capture_count=1)
    _replace_tls_status_json(path, ["{", "{}"])

    result = check_validity("raw_capture", path)

    assert result.outcome is ValidityOutcome.UNREADABLE
    assert result.reason_codes == ("tls_status_unreadable",)


def test_raw_capture_v3_rejects_coercive_frame_extent_json(
    tmp_path: Path,
) -> None:
    path = tmp_path / "raw_coercive_frame_extent.h5"
    _write_raw_capture(path)
    extent = {
        "mode": "full_sensor",
        "origin_xy": ["0", False],
        "shape_hw": [2.9, 3.7],
        "sensor_shape_hw": [2, 3],
    }
    with h5py.File(path, "r+") as h5:
        h5["camera/frame_extent_json"][0] = json.dumps(extent)

    result = check_validity("raw_capture", path)

    assert result.outcome is ValidityOutcome.INVALID
    assert result.reason_codes == ("frame_extent_invalid",)


def test_raw_capture_validator_binds_last_completed_index_to_committed_row(
    tmp_path: Path,
) -> None:
    path = tmp_path / "partial_raw_capture_wrong_last_index.h5"
    plan = CapturePlan.from_dict(
        {
            "plan_id": "partial_last_index_validation_v1",
            "wavelengths": [
                {
                    "illumination": {
                        "mode": "monochromatic",
                        "effective_wavelength_nm": 550.0,
                        "tls_setpoint_nm": 550.0,
                        "wavelength_label_nm": 550.0,
                    }
                }
            ],
            "masks": [{"mask_id": "mask_1"}, {"mask_id": "mask_2"}],
            "camera": {"frames_per_capture": 1},
            "store_burst": False,
        }
    )
    with RawCaptureWriter(path, plan) as writer:
        writer.write_lcd_metadata({"subpixel_axis": 1})
        writer.write_physical_masks(
            [
                np.ones((2, 3), dtype=np.uint8),
                np.ones((2, 3), dtype=np.uint8),
            ]
        )
        writer.append_capture(
            capture_index=0,
            wavelength_index=0,
            mask_index=0,
            frames=None,
            frames_avg=np.zeros((2, 3), dtype=np.float32),
            camera_meta={},
        )

    with h5py.File(path, "r+") as h5:
        flags_value = h5["capture/processing_flags_json"][()]
        flags = json.loads(
            flags_value.decode("utf-8")
            if isinstance(flags_value, bytes)
            else str(flags_value)
        )
        flags["last_completed_capture_index"] = 1
        h5["capture/processing_flags_json"][()] = json.dumps(flags, sort_keys=True)

    result = check_validity("raw_capture", path)

    assert result.outcome is ValidityOutcome.INVALID
    assert result.reason_codes == ("processing_flags_mismatch",)


def test_raw_capture_validator_rejects_duplicate_capture_index(
    tmp_path: Path,
) -> None:
    path = tmp_path / "duplicate_capture_index.h5"
    _write_multi_raw_capture(path)
    with h5py.File(path, "r+") as h5:
        h5["capture/capture_index"][1] = 0

    result = check_validity("raw_capture", path)

    assert result.outcome is ValidityOutcome.INVALID
    assert result.reason_codes == ("capture_index_duplicate",)


def test_raw_capture_validator_rejects_duplicate_partial_combination(
    tmp_path: Path,
) -> None:
    path = tmp_path / "duplicate_partial_combination.h5"
    _write_multi_raw_capture(path, capture_count=2)
    with h5py.File(path, "r+") as h5:
        h5["capture/mask_index"][1] = 0

    result = check_validity("raw_capture", path)

    assert result.outcome is ValidityOutcome.INVALID
    assert result.reason_codes == ("capture_combination_duplicate",)


def test_raw_capture_validator_rejects_full_bitmap_with_missing_combination(
    tmp_path: Path,
) -> None:
    path = tmp_path / "missing_complete_combination.h5"
    _write_multi_raw_capture(path)
    with h5py.File(path, "r+") as h5:
        h5["capture/wavelength_index"][3] = 0
        h5["capture/mask_index"][3] = 0

    result = check_validity("raw_capture", path)

    assert result.outcome is ValidityOutcome.INVALID
    assert result.reason_codes == ("capture_schedule_incomplete",)


def test_raw_capture_validator_rejects_capture_index_schedule_mismatch(
    tmp_path: Path,
) -> None:
    path = tmp_path / "capture_schedule_mismatch.h5"
    _write_multi_raw_capture(path)
    with h5py.File(path, "r+") as h5:
        h5["capture/capture_index"][0] = 1
        h5["capture/capture_index"][1] = 0

    result = check_validity("raw_capture", path)

    assert result.outcome is ValidityOutcome.INVALID
    assert result.reason_codes == ("capture_schedule_mismatch",)


def test_raw_writer_failed_append_does_not_commit_partial_row(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "failed_append_raw_capture.h5"
    plan = CapturePlan.from_dict(
        {
            "plan_id": "failed_append_plan_v1",
            "wavelengths": [
                {
                    "illumination": {
                        "mode": "monochromatic",
                        "effective_wavelength_nm": 550.0,
                        "tls_setpoint_nm": 550.0,
                        "wavelength_label_nm": 550.0,
                    }
                }
            ],
            "masks": [{"mask_id": "mask_1"}],
            "camera": {"frames_per_capture": 1},
            "store_burst": False,
        }
    )

    def _fail_extent(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("extent conversion failed")

    with pytest.raises(RuntimeError, match="extent conversion failed"):
        with RawCaptureWriter(path, plan) as writer:
            writer.write_lcd_metadata({"subpixel_axis": 1})
            writer.write_physical_masks([np.ones((2, 3), dtype=np.uint8)])
            monkeypatch.setattr(
                raw_capture_h5,
                "camera_frame_extent_from_camera_metadata",
                _fail_extent,
            )
            writer.append_capture(
                capture_index=0,
                wavelength_index=0,
                mask_index=0,
                frames=None,
                frames_avg=np.zeros((2, 3), dtype=np.float32),
                camera_meta={},
            )

    with h5py.File(path, "r") as h5:
        assert not bool(h5["capture/completed"][:].any())
        flags_value = h5["capture/processing_flags_json"][()]
        flags = json.loads(
            flags_value.decode("utf-8")
            if isinstance(flags_value, bytes)
            else str(flags_value)
        )
        assert flags["n_captures_written"] == 0
        assert flags["capture_complete"] is False
        assert flags["run_succeeded"] is False
        assert flags["error"] == "extent conversion failed"
    assert check_validity("raw_capture", path).outcome is ValidityOutcome.VALID


def test_raw_writer_all_rows_committed_then_run_failure_remains_valid(
    tmp_path: Path,
) -> None:
    path = tmp_path / "complete_capture_failed_run.h5"
    plan = CapturePlan.from_dict(
        {
            "plan_id": "complete_capture_failed_run_v1",
            "wavelengths": [
                {
                    "illumination": {
                        "mode": "monochromatic",
                        "effective_wavelength_nm": 550.0,
                        "tls_setpoint_nm": 550.0,
                        "wavelength_label_nm": 550.0,
                    }
                }
            ],
            "masks": [{"mask_id": "mask_1"}, {"mask_id": "mask_2"}],
            "camera": {"frames_per_capture": 1},
            "store_burst": False,
        }
    )

    with pytest.raises(RuntimeError, match="post-capture failure"):
        with RawCaptureWriter(path, plan) as writer:
            writer.write_lcd_metadata({"subpixel_axis": 1})
            writer.write_physical_masks(
                [
                    np.ones((2, 3), dtype=np.uint8),
                    np.ones((2, 3), dtype=np.uint8),
                ]
            )
            for capture_index in range(plan.n_captures):
                writer.append_capture(
                    capture_index=capture_index,
                    wavelength_index=0,
                    mask_index=capture_index,
                    frames=None,
                    frames_avg=np.zeros((2, 3), dtype=np.float32),
                    camera_meta={},
                )
            raise RuntimeError("post-capture failure")

    with h5py.File(path, "r") as h5:
        assert bool(h5["capture/completed"][:].all())
        flags_value = h5["capture/processing_flags_json"][()]
        flags = json.loads(
            flags_value.decode("utf-8")
            if isinstance(flags_value, bytes)
            else str(flags_value)
        )
        assert flags["n_captures_written"] == plan.n_captures
        assert flags["capture_complete"] is True
        assert flags["run_succeeded"] is False
        assert flags["error"] == "post-capture failure"
    assert check_validity("raw_capture", path).outcome is ValidityOutcome.VALID


def test_raw_capture_validator_requires_burst_payload_when_store_burst_is_true(
    tmp_path: Path,
) -> None:
    path = tmp_path / "burst_raw_capture.h5"
    plan = CapturePlan.from_dict(
        {
            "plan_id": "burst_validation_plan_v1",
            "wavelengths": [
                {
                    "illumination": {
                        "mode": "monochromatic",
                        "effective_wavelength_nm": 550.0,
                        "tls_setpoint_nm": 550.0,
                        "wavelength_label_nm": 550.0,
                    }
                }
            ],
            "masks": [{"mask_id": "mask_1"}],
            "camera": {"frames_per_capture": 1},
            "store_burst": True,
        }
    )
    with RawCaptureWriter(path, plan) as writer:
        writer.write_lcd_metadata({"subpixel_axis": 1})
        writer.write_physical_masks([np.ones((2, 3), dtype=np.uint8)])
        burst = np.ones((1, 2, 3), dtype=np.uint16)
        writer.append_capture(
            capture_index=0,
            wavelength_index=0,
            mask_index=0,
            frames=burst,
            frames_avg=burst.mean(axis=0),
            camera_meta={},
        )

    assert check_validity("raw_capture", path).outcome is ValidityOutcome.VALID
    with h5py.File(path, "r+") as h5:
        del h5["raw/frames"]

    result = check_validity("raw_capture", path)

    assert result.outcome is ValidityOutcome.INVALID
    assert result.reason_codes == ("missing_required_path",)


def test_broadband_survey_uses_nan_sentinel_and_validates_against_source_identity(
    tmp_path: Path,
) -> None:
    path = tmp_path / "broadband_survey.h5"
    manifest = _write_broadband_survey_h5(path)

    manifest.validate()
    manifest_text = manifest.to_json()
    manifest_data = json.loads(manifest_text)
    assert "NaN" not in manifest_text
    assert manifest_data["entry_wavelengths_nm"] == [None, None]
    assert manifest_data["unique_wavelengths_nm"] == [None]
    round_tripped = FullFramePSFSurveyManifest.from_dict(
        manifest_data,
        legacy_mode=False,
    )
    assert all(np.isnan(value) for value in round_tripped.entry_wavelengths_nm)
    result = check_validity("full_frame_psf_survey", path)

    assert result.outcome is ValidityOutcome.VALID


def test_schema_v1_broadband_hdf_embedded_manifest_remains_valid(
    tmp_path: Path,
) -> None:
    path = tmp_path / "broadband_survey_v1.h5"
    manifest = _write_broadband_survey_h5(path)
    data = manifest.to_dict()
    data["schema_version"] = 1
    data["entry_wavelengths_nm"] = [float("nan"), float("nan")]
    data["unique_wavelengths_nm"] = [float("nan")]
    with h5py.File(path, "r+") as h5:
        h5.attrs["schema_version"] = 1
        h5["full_frame_survey/manifest_json"][()] = json.dumps(
            data,
            sort_keys=True,
            allow_nan=True,
        )

    result = check_validity("full_frame_psf_survey", path)

    assert result.outcome is ValidityOutcome.VALID
    assert result.schema_version == 1


def test_broadband_dictionary_json_uses_null_and_validates(tmp_path: Path) -> None:
    manifest = _dictionary_manifest()
    manifest.illumination_mode = "broadband_passthrough"
    manifest.entry_wavelengths_nm = [float("nan")]
    manifest.unique_wavelengths_nm = [float("nan")]
    path = tmp_path / "broadband_dictionary.json"

    text = manifest.to_json(path)
    data = json.loads(text)

    assert "NaN" not in text
    assert data["entry_wavelengths_nm"] == [None]
    assert data["unique_wavelengths_nm"] == [None]
    assert (
        check_validity("peak_patch_psf_dictionary", path).outcome
        is ValidityOutcome.VALID
    )


def test_survey_validator_rejects_hdf5_manifest_disagreement(tmp_path: Path) -> None:
    path = tmp_path / "survey.h5"
    _write_survey_h5(path)
    with h5py.File(path, "r+") as h5:
        h5["full_frame_survey/entry_mask_ids"][0] = "other_mask"

    result = check_validity("full_frame_psf_survey", path)

    assert result.outcome is ValidityOutcome.INVALID
    assert result.reason_codes == ("manifest_metadata_mismatch",)


@pytest.mark.parametrize(
    ("dataset", "reason_code"),
    [
        ("full_frame_survey/mask_index", "mask_index_out_of_bounds"),
        ("full_frame_survey/wavelength_index", "wavelength_index_out_of_bounds"),
        ("full_frame_survey/capture_indices", "capture_index_out_of_bounds"),
    ],
)
def test_survey_validator_rejects_source_plan_index_overflow(
    tmp_path: Path,
    dataset: str,
    reason_code: str,
) -> None:
    path = tmp_path / "survey.h5"
    _write_survey_h5(path)
    with h5py.File(path, "r+") as h5:
        h5[dataset][0] = 9

    result = check_validity("full_frame_psf_survey", path)

    assert result.outcome is ValidityOutcome.INVALID
    assert result.reason_codes == (reason_code,)


def test_survey_validator_rejects_source_plan_identity_mismatch(tmp_path: Path) -> None:
    path = tmp_path / "survey.h5"
    _write_survey_h5(path)
    with h5py.File(path, "r+") as h5:
        h5["source/plan_json"][()] = json.dumps(
            {
                "wavelengths": [
                    {
                        "illumination": {
                            "mode": "monochromatic",
                            "effective_wavelength_nm": 550.0,
                            "tls_setpoint_nm": 550.0,
                            "wavelength_label_nm": 550.0,
                        }
                    }
                ],
                "masks": [{"mask_id": "other_mask"}],
            }
        )

    result = check_validity("full_frame_psf_survey", path)

    assert result.outcome is ValidityOutcome.INVALID
    assert result.reason_codes == ("entry_mask_id_mismatch",)


def test_support_validator_rejects_incomplete_component_table(tmp_path: Path) -> None:
    path = tmp_path / "support.h5"
    _write_support_report_h5(path)
    with h5py.File(path, "r+") as h5:
        manifest_value = h5["metadata/manifest_json"][()]
        manifest_data = json.loads(
            manifest_value.decode("utf-8")
            if isinstance(manifest_value, bytes)
            else str(manifest_value)
        )
        manifest_data["component_policy"] = {
            "analysis_mode": "component_table",
            "component_table_written": True,
        }
        h5["metadata/manifest_json"][()] = json.dumps(
            manifest_data,
            sort_keys=True,
        )
        h5.require_group("components").create_dataset(
            "entry_index", data=np.asarray([0], dtype=np.int64)
        )

    result = check_validity("peak_support_analysis_report", path)

    assert result.outcome is ValidityOutcome.INVALID
    assert result.reason_codes == ("component_table_incomplete",)


def test_support_validator_rejects_components_for_energy_only_report(
    tmp_path: Path,
) -> None:
    path = tmp_path / "support_energy_only.h5"
    _write_support_report_h5(path)
    with h5py.File(path, "r+") as h5:
        h5.require_group("components")

    result = check_validity("peak_support_analysis_report", path)

    assert result.outcome is ValidityOutcome.INVALID
    assert result.reason_codes == ("component_table_presence_mismatch",)


def test_dictionary_validator_rejects_wrong_root_artifact_type(tmp_path: Path) -> None:
    path = tmp_path / "dictionary.h5"
    _write_dictionary_h5(path)
    with h5py.File(path, "r+") as h5:
        h5.attrs["artifact_type"] = "full_frame_psf_survey"

    result = check_validity("peak_patch_psf_dictionary", path)

    assert result.outcome is ValidityOutcome.INVALID
    assert result.reason_codes == ("artifact_type_mismatch",)


@pytest.mark.parametrize(
    ("dataset", "value"),
    [
        ("peak_patch_dictionary/unique_mask_ids", "other_mask"),
        ("peak_patch_dictionary/unique_wavelength_nm", 560.0),
    ],
)
def test_dictionary_validator_rejects_unique_metadata_disagreement(
    tmp_path: Path,
    dataset: str,
    value: object,
) -> None:
    path = tmp_path / "dictionary.h5"
    _write_dictionary_h5(path)
    with h5py.File(path, "r+") as h5:
        h5[dataset][0] = value

    result = check_validity("peak_patch_psf_dictionary", path)

    assert result.outcome is ValidityOutcome.INVALID
    assert result.reason_codes == ("manifest_metadata_mismatch",)


@pytest.mark.parametrize(
    ("mutation", "reason_code"),
    [
        (
            lambda h5: h5["peak_patch_dictionary/entry_mask_index"].__setitem__(0, 9),
            "mask_index_out_of_bounds",
        ),
        (
            lambda h5: h5["peak_patch_dictionary/entry_capture_indices"].__setitem__(
                0,
                np.asarray([9], dtype=np.int64),
            ),
            "capture_index_out_of_bounds",
        ),
    ],
)
def test_dictionary_validator_rejects_source_plan_index_overflow(
    tmp_path: Path,
    mutation,
    reason_code: str,
) -> None:
    path = tmp_path / "dictionary.h5"
    _write_dictionary_h5(path)
    with h5py.File(path, "r+") as h5:
        mutation(h5)

    result = check_validity("peak_patch_psf_dictionary", path)

    assert result.outcome is ValidityOutcome.INVALID
    assert result.reason_codes == (reason_code,)


@pytest.mark.parametrize(
    ("source_mask_id", "source_wavelength_nm", "reason_code"),
    [
        ("other_mask", 550.0, "entry_mask_id_mismatch"),
        ("mask_1", 560.0, "entry_wavelength_mismatch"),
    ],
)
def test_dictionary_validator_rejects_source_plan_entry_identity_mismatch(
    tmp_path: Path,
    source_mask_id: str,
    source_wavelength_nm: float,
    reason_code: str,
) -> None:
    path = tmp_path / "dictionary.h5"
    _write_dictionary_h5(path)
    with h5py.File(path, "r+") as h5:
        h5["source/plan_json"][()] = json.dumps(
            {
                "wavelengths": [
                    {
                        "illumination": {
                            "mode": "monochromatic",
                            "effective_wavelength_nm": source_wavelength_nm,
                            "tls_setpoint_nm": source_wavelength_nm,
                            "wavelength_label_nm": source_wavelength_nm,
                        }
                    }
                ],
                "masks": [{"mask_id": source_mask_id}],
            }
        )

    result = check_validity("peak_patch_psf_dictionary", path)

    assert result.outcome is ValidityOutcome.INVALID
    assert result.reason_codes == (reason_code,)
