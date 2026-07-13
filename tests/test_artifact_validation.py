from __future__ import annotations

import json
from pathlib import Path

import h5py
import numpy as np
import pytest

import tasks.artifacts.validation as artifact_validation
from tasks.artifact_versioning import schema_compat
from tasks.artifacts.validation import (
    VALIDATOR_REGISTRY,
    ValidityOutcome,
    check_validity,
)
from tasks.profiles import CameraProfile, PupilProfile
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
        entry_illumination_json=['{"mode":"monochromatic"}'],
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
        component_policy={"analysis_mode": "energy_only"},
        entry_mask_ids=["mask_1"],
        entry_wavelengths_nm=[550.0],
    )


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
    shape = (2, 3)
    with h5py.File(path, "w") as h5:
        h5.attrs["artifact_type"] = "raw_capture"
        h5.attrs["raw_capture_schema_version"] = schema_compat("raw_capture").current
        raw = h5.require_group("raw")
        raw.create_dataset("frames_avg", data=np.zeros((1, *shape), dtype=np.float32))
        masks = h5.require_group("masks")
        masks.create_dataset("masks_physical", data=np.zeros((1, 2, 3), dtype=np.uint8))
        _write_text_array(masks, "mask_id", ["mask_1"])
        _write_text_array(masks, "family_id", [""])
        _write_text_array(masks, "family_params_json", ["{}"])
        masks.create_dataset("has_mask_array", data=np.asarray([True], dtype=bool))
        illumination = h5.require_group("illumination")
        _write_text_array(illumination, "illumination_json", ['{"mode":"monochromatic"}'])
        illumination.create_dataset("tls_setpoint_nm", data=np.asarray([550.0]))
        illumination.create_dataset("effective_wavelength_nm", data=np.asarray([550.0]))
        camera = h5.require_group("camera")
        _write_text_array(camera, "frame_extent_json", [json.dumps(_extent(shape))])
        capture = h5.require_group("capture")
        capture.create_dataset("capture_index", data=np.asarray([0], dtype=np.int64))
        capture.create_dataset("wavelength_index", data=np.asarray([0], dtype=np.int64))
        capture.create_dataset("mask_index", data=np.asarray([0], dtype=np.int64))
        capture.create_dataset("burst_count", data=np.asarray([1], dtype=np.int64))
        capture.create_dataset("completed", data=np.asarray([True], dtype=bool))
        _write_scalar_text(
            capture,
            "plan_json",
            _source_plan_json(),
        )
        _write_scalar_text(
            capture,
            "processing_flags_json",
            json.dumps({"raw_capture_schema_version": 2}),
        )


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

    unreadable_hdf = tmp_path / "broken.h5"
    unreadable_hdf.write_bytes(b"not an HDF5 payload")
    assert check_validity("raw_capture", unreadable_hdf).outcome is ValidityOutcome.UNREADABLE


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

    support = _support_manifest()
    support.entry_wavelengths_nm = []
    with pytest.raises(DiffractionSupportAnalysisError, match="equal length"):
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


def test_raw_capture_validator_rejects_invalid_completed_index(tmp_path: Path) -> None:
    path = tmp_path / "raw_capture.h5"
    _write_raw_capture(path)
    with h5py.File(path, "r+") as h5:
        h5["capture/mask_index"][0] = 9

    result = check_validity("raw_capture", path)

    assert result.outcome is ValidityOutcome.INVALID
    assert result.reason_codes == ("mask_index_out_of_bounds",)


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
        h5.require_group("components").create_dataset(
            "entry_index", data=np.asarray([0], dtype=np.int64)
        )

    result = check_validity("peak_support_analysis_report", path)

    assert result.outcome is ValidityOutcome.INVALID
    assert result.reason_codes == ("component_table_incomplete",)


def test_dictionary_validator_rejects_wrong_root_artifact_type(tmp_path: Path) -> None:
    path = tmp_path / "dictionary.h5"
    _write_dictionary_h5(path)
    with h5py.File(path, "r+") as h5:
        h5.attrs["artifact_type"] = "full_frame_psf_survey"

    result = check_validity("peak_patch_psf_dictionary", path)

    assert result.outcome is ValidityOutcome.INVALID
    assert result.reason_codes == ("artifact_type_mismatch",)


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
                        }
                    }
                ],
                "masks": [{"mask_id": source_mask_id}],
            }
        )

    result = check_validity("peak_patch_psf_dictionary", path)

    assert result.outcome is ValidityOutcome.INVALID
    assert result.reason_codes == (reason_code,)
