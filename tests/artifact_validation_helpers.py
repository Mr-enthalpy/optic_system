from __future__ import annotations

import json
from pathlib import Path

import h5py
import numpy as np

import tasks.raw_capture_h5 as raw_capture_h5
from tasks.capture_plan import CapturePlan
from tasks.raw_capture_h5 import RawCaptureWriter

from tasks.artifact_versioning import schema_compat
from tasks.profiles import CameraProfile, PupilProfile
from tasks.psf.analyze_diffraction_support import PeakSupportAnalysisManifest
from tasks.psf.build_full_frame_psf_survey import FullFramePSFSurveyManifest
from tasks.psf.build_peak_patch_psf_dictionary import PeakPatchPSFDictionaryManifest
from tasks.psf.derive_peak_layout_profile import PeakLayoutProfileManifest
from tasks.psf.sensor_energy_center import SensorEnergyCenterProfile

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
            "artifact_type": "pupil_profile",
            "schema_version": schema_compat("pupil_profile").current,
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
            "artifact_type": "camera_profile",
            "schema_version": schema_compat("camera_profile").current,
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
        source_survey_artifact_id="survey_validation_1",
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
        source_survey_artifact_id="survey_validation_1",
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
        source_raw_capture_artifact_id="raw_capture_validation_1",
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
        source_raw_capture_artifact_id="raw_capture_validation_1",
        peak_layout_artifact_id="layout_validation_1",
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
        extent_compatibility={
            "matches": True,
            "mismatch_override": False,
            "reason": None,
        },
        peak_ids=["peak_1"],
        patch_shape_hw=[[2, 2]],
        patch_origin_xy=[[1, 0]],
        applied_background_policy="none",
        applied_normalization_policy="none",
    )

def _support_manifest(shape: tuple[int, int] = (2, 3)) -> PeakSupportAnalysisManifest:
    return PeakSupportAnalysisManifest(
        report_id="support_validation_v1",
        source_survey_artifact_id="survey_validation_1",
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
        data[field] = [None] * len(values)
    if artifact_type == "full_frame_psf_survey":
        illumination = json.dumps(
            {
                "mode": "broadband_passthrough",
                "effective_wavelength_nm": None,
                "tls_setpoint_nm": 0.0,
                "wavelength_label_nm": None,
            },
            sort_keys=True,
        )
        data["illumination_mode"] = "broadband_passthrough"
        data["entry_illumination_json"] = [
            illumination for _ in data["entry_wavelengths_nm"]
        ]
    elif artifact_type == "peak_patch_psf_dictionary":
        data["illumination_mode"] = "broadband_passthrough"
    return data

_STRING_DTYPE = h5py.string_dtype(encoding="utf-8")

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
        h5.attrs["manifest_schema_version"] = schema_compat("full_frame_psf_survey").current
        h5.attrs["payload_schema_version"] = 1
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
        source_raw_capture_artifact_id="raw_capture_validation_1",
        pupil_profile_id=None,
        camera_profile_id=None,
        illumination_mode="broadband_passthrough",
        entry_wavelengths_nm=[None, None],
        entry_illumination_json=[illumination_json, illumination_json],
        entry_mask_ids=["mask_1", "mask_2"],
        unique_wavelengths_nm=[None],
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
        h5.attrs["manifest_schema_version"] = schema_compat("full_frame_psf_survey").current
        h5.attrs["payload_schema_version"] = 1
        h5.attrs["survey_id"] = manifest.survey_id
        group = h5.require_group("full_frame_survey")
        group.create_dataset("frames_avg", data=np.zeros((2, *shape), dtype=np.float32))
        _write_text_array(group, "entry_mask_ids", manifest.entry_mask_ids)
        group.create_dataset(
            "entry_wavelength_nm",
            data=np.asarray(
                [np.nan if value is None else value for value in manifest.entry_wavelengths_nm],
                dtype=np.float64,
            ),
        )
        _write_text_array(group, "entry_illumination_json", manifest.entry_illumination_json)
        _write_text_array(group, "unique_mask_ids", manifest.unique_mask_ids)
        group.create_dataset(
            "unique_wavelength_nm",
            data=np.asarray(
                [np.nan if value is None else value for value in manifest.unique_wavelengths_nm],
                dtype=np.float64,
            ),
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
        h5.attrs["manifest_schema_version"] = schema_compat(
            "peak_support_analysis_report"
        ).current
        h5.attrs["payload_schema_version"] = 1
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

def _enable_empty_component_table(path: Path) -> None:
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
        components = h5.require_group("components")
        for name in ("entry_index", "component_id", "area"):
            components.create_dataset(name, data=np.asarray([], dtype=np.int64))
        components.create_dataset(
            "bbox_xyxy",
            data=np.empty((0, 4), dtype=np.int64),
        )
        for name in ("centroid_xy", "centroid_xy_abs", "centroid_xy_rel"):
            components.create_dataset(name, data=np.empty((0, 2), dtype=np.float64))
        for name in (
            "tau",
            "energy",
            "peak_value",
            "mean_value",
            "max_radius",
            "max_radius_from_energy_center",
            "wavelength_nm",
        ):
            components.create_dataset(name, data=np.asarray([], dtype=np.float64))
        components.create_dataset(
            "is_far_field",
            data=np.asarray([], dtype=np.bool_),
        )
        _write_text_array(components, "mask_id", [])

def _write_dictionary_h5(path: Path) -> PeakPatchPSFDictionaryManifest:
    manifest = _dictionary_manifest()
    with h5py.File(path, "w") as h5:
        h5.attrs["artifact_type"] = "peak_patch_psf_dictionary"
        h5.attrs["manifest_schema_version"] = schema_compat(
            "peak_patch_psf_dictionary"
        ).current
        h5.attrs["payload_schema_version"] = 1
        h5.attrs["dictionary_id"] = manifest.dictionary_id
        group = h5.require_group("peak_patch_dictionary")
        group.create_dataset("patches", data=np.zeros((1, 1, 2, 2), dtype=np.float32))
        _write_text_array(group, "entry_mask_ids", manifest.entry_mask_ids)
        group.create_dataset("entry_mask_index", data=np.asarray([0], dtype=np.int64))
        group.create_dataset(
            "entry_wavelength_index",
            data=np.asarray([0], dtype=np.int64),
        )
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
            "extent_compatibility_json",
            json.dumps(manifest.extent_compatibility),
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
