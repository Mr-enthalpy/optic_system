from __future__ import annotations

import json
from pathlib import Path

import h5py
import numpy as np
import pytest

import tasks.raw_capture_h5 as raw_capture_h5
from tasks.artifact_versioning import payload_schema_version, schema_compat
from tasks.artifacts.validation import ValidityOutcome, check_validity
from tasks.capture_plan import CapturePlan
from tasks.psf.build_full_frame_psf_survey import FullFramePSFSurveyManifest
from tasks.raw_capture_h5 import RawCaptureWriter
from tests.artifact_validation_helpers import (
    _dictionary_manifest,
    _enable_empty_component_table,
    _support_manifest,
    _survey_manifest,
    _write_broadband_survey_h5,
    _write_dictionary_h5,
    _write_historical_v2_raw_capture,
    _write_multi_raw_capture,
    _write_raw_capture,
    _replace_tls_status_json,
    _write_support_report_h5,
    _write_survey_h5,
)

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
    expected_version = (
        payload_schema_version(artifact_type)
        if artifact_type == "raw_capture"
        else schema_compat(artifact_type).current
    )
    assert result.schema_version == expected_version
    if artifact_type == "raw_capture":
        assert result.manifest_schema_version is None
        assert result.payload_schema_version == 3
    else:
        assert result.manifest_schema_version == 2
        assert result.payload_schema_version == 1

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

def test_schema_v1_survey_hdf_rejects_coercive_payload_extent(
    tmp_path: Path,
) -> None:
    path = tmp_path / "survey_coercive_extent_v1.h5"
    manifest = _write_survey_h5(path)
    manifest_data = manifest.to_dict()
    manifest_data["schema_version"] = 1
    manifest_data["source_raw_capture_h5"] = "legacy-raw.h5"
    del manifest_data["source_raw_capture_artifact_id"]
    extent = {
        "mode": "full_sensor",
        "origin_xy": ["0", False],
        "shape_hw": [2.9, 3.7],
        "sensor_shape_hw": [2.9, 3.7],
        "historical_extra": "ignored by the v1 loader",
    }
    with h5py.File(path, "r+") as h5:
        h5.attrs["manifest_schema_version"] = 1
        h5["full_frame_survey/manifest_json"][()] = json.dumps(manifest_data)
        h5["full_frame_survey/camera_frame_extent_json"][()] = json.dumps(extent)

    result = check_validity("full_frame_psf_survey", path)

    assert result.outcome is ValidityOutcome.INVALID
    assert result.reason_codes == ("frame_extent_invalid",)

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
            payload_schema_version("raw_capture") + 1
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
    assert round_tripped.entry_wavelengths_nm == [None, None]
    result = check_validity("full_frame_psf_survey", path)

    assert result.outcome is ValidityOutcome.VALID

def test_schema_v1_broadband_hdf_embedded_manifest_remains_valid(
    tmp_path: Path,
) -> None:
    path = tmp_path / "broadband_survey_v1.h5"
    manifest = _write_broadband_survey_h5(path)
    data = manifest.to_dict()
    data["schema_version"] = 1
    data["source_raw_capture_h5"] = "legacy-raw.h5"
    del data["source_raw_capture_artifact_id"]
    data["entry_wavelengths_nm"] = [float("nan"), float("nan")]
    data["unique_wavelengths_nm"] = [float("nan")]
    with h5py.File(path, "r+") as h5:
        h5.attrs["manifest_schema_version"] = 1
        h5["full_frame_survey/manifest_json"][()] = json.dumps(
            data,
            sort_keys=True,
            allow_nan=True,
        )

    result = check_validity("full_frame_psf_survey", path)

    assert result.outcome is ValidityOutcome.VALID
    assert result.schema_version == 1

def test_schema_v1_dictionary_hdf_remains_readable_without_v2_fields(
    tmp_path: Path,
) -> None:
    path = tmp_path / "dictionary_v1.h5"
    _write_dictionary_h5(path)
    with h5py.File(path, "r+") as h5:
        manifest_value = h5["peak_patch_dictionary/manifest_json"][()]
        manifest_data = json.loads(
            manifest_value.decode("utf-8")
            if isinstance(manifest_value, bytes)
            else str(manifest_value)
        )
        manifest_data["schema_version"] = 1
        del manifest_data["extent_compatibility"]
        manifest_data["source_raw_capture_h5"] = "legacy-raw.h5"
        manifest_data["peak_layout_profile"] = "legacy-layout.json"
        del manifest_data["source_raw_capture_artifact_id"]
        del manifest_data["peak_layout_artifact_id"]
        h5.attrs["manifest_schema_version"] = 1
        h5["peak_patch_dictionary/manifest_json"][()] = json.dumps(
            manifest_data,
            sort_keys=True,
        )
        del h5["peak_patch_dictionary/entry_wavelength_index"]
        del h5["peak_patch_dictionary/extent_compatibility_json"]

    result = check_validity("peak_patch_psf_dictionary", path)

    assert result.outcome is ValidityOutcome.VALID
    assert result.schema_version == 1

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

def test_survey_validator_binds_capture_index_to_wavelength_and_mask(
    tmp_path: Path,
) -> None:
    path = tmp_path / "survey.h5"
    _write_broadband_survey_h5(path)
    with h5py.File(path, "r+") as h5:
        h5["full_frame_survey/capture_indices"][0] = 1

    result = check_validity("full_frame_psf_survey", path)

    assert result.outcome is ValidityOutcome.INVALID
    assert result.reason_codes == ("source_capture_binding_mismatch",)

def test_survey_validator_rejects_non_numeric_frames(tmp_path: Path) -> None:
    path = tmp_path / "survey.h5"
    _write_survey_h5(path)
    with h5py.File(path, "r+") as h5:
        del h5["full_frame_survey/frames_avg"]
        h5["full_frame_survey"].create_dataset(
            "frames_avg",
            data=np.full((1, 2, 3), "bad", dtype="S3"),
        )

    result = check_validity("full_frame_psf_survey", path)

    assert result.outcome is ValidityOutcome.INVALID
    assert result.reason_codes == ("dataset_dtype_invalid",)

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

@pytest.mark.parametrize(
    ("dataset_path", "replacement", "reason_code"),
    [
        (
            "support_analysis/total_corr_energy",
            np.asarray(["bad"], dtype="S3"),
            "dataset_dtype_invalid",
        ),
        (
            "support_analysis/far_field_noise_pixel_count",
            np.asarray([[0.0]], dtype=np.float64),
            "dataset_dtype_invalid",
        ),
        (
            "support_analysis/compact_support_fraction",
            np.asarray([[float("inf")]], dtype=np.float64),
            "dataset_value_invalid",
        ),
    ],
)
def test_support_validator_rejects_invalid_scientific_payload_types(
    tmp_path: Path,
    dataset_path: str,
    replacement: np.ndarray,
    reason_code: str,
) -> None:
    path = tmp_path / "support.h5"
    _write_support_report_h5(path)
    parent_path, name = dataset_path.rsplit("/", 1)
    with h5py.File(path, "r+") as h5:
        del h5[dataset_path]
        h5[parent_path].create_dataset(name, data=replacement)

    result = check_validity("peak_support_analysis_report", path)

    assert result.outcome is ValidityOutcome.INVALID
    assert result.reason_codes == (reason_code,)

def test_support_component_counts_require_integer_dtype(tmp_path: Path) -> None:
    path = tmp_path / "support_components.h5"
    _write_support_report_h5(path)
    _enable_empty_component_table(path)
    assert (
        check_validity("peak_support_analysis_report", path).outcome
        is ValidityOutcome.VALID
    )
    with h5py.File(path, "r+") as h5:
        del h5["components/area"]
        h5["components"].create_dataset(
            "area",
            data=np.asarray([], dtype=np.float64),
        )

    result = check_validity("peak_support_analysis_report", path)

    assert result.outcome is ValidityOutcome.INVALID
    assert result.reason_codes == ("dataset_dtype_invalid",)

def test_dictionary_validator_rejects_wrong_root_artifact_type(tmp_path: Path) -> None:
    path = tmp_path / "dictionary.h5"
    _write_dictionary_h5(path)
    with h5py.File(path, "r+") as h5:
        h5.attrs["artifact_type"] = "full_frame_psf_survey"

    result = check_validity("peak_patch_psf_dictionary", path)

    assert result.outcome is ValidityOutcome.INVALID
    assert result.reason_codes == ("artifact_type_mismatch",)

def test_dictionary_validator_rejects_non_numeric_patches(tmp_path: Path) -> None:
    path = tmp_path / "dictionary.h5"
    _write_dictionary_h5(path)
    with h5py.File(path, "r+") as h5:
        del h5["peak_patch_dictionary/patches"]
        h5["peak_patch_dictionary"].create_dataset(
            "patches",
            data=np.full((1, 1, 2, 2), "bad", dtype="S3"),
        )

    result = check_validity("peak_patch_psf_dictionary", path)

    assert result.outcome is ValidityOutcome.INVALID
    assert result.reason_codes == ("dataset_dtype_invalid",)

def test_dictionary_validator_requires_integer_patch_metadata(tmp_path: Path) -> None:
    path = tmp_path / "dictionary.h5"
    manifest = _write_dictionary_h5(path)
    with h5py.File(path, "r+") as h5:
        del h5["peak_patch_dictionary/patch_origin_xy"]
        h5["peak_patch_dictionary"].create_dataset(
            "patch_origin_xy",
            data=np.asarray(manifest.patch_origin_xy, dtype=np.float64),
        )

    result = check_validity("peak_patch_psf_dictionary", path)

    assert result.outcome is ValidityOutcome.INVALID
    assert result.reason_codes == ("dataset_dtype_invalid",)

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

def test_dictionary_schema_v2_requires_explicit_wavelength_index(
    tmp_path: Path,
) -> None:
    path = tmp_path / "dictionary.h5"
    _write_dictionary_h5(path)
    with h5py.File(path, "r+") as h5:
        del h5["peak_patch_dictionary/entry_wavelength_index"]

    result = check_validity("peak_patch_psf_dictionary", path)

    assert result.outcome is ValidityOutcome.INVALID
    assert result.reason_codes == ("missing_required_path",)

def test_dictionary_validator_binds_capture_to_explicit_source_indices(
    tmp_path: Path,
) -> None:
    path = tmp_path / "dictionary.h5"
    _write_dictionary_h5(path)
    source_plan = {
        "plan_id": "two_mask_plan",
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
    }
    with h5py.File(path, "r+") as h5:
        h5["source/plan_json"][()] = json.dumps(source_plan, sort_keys=True)
        h5["peak_patch_dictionary/entry_capture_indices"][0] = np.asarray(
            [1],
            dtype=np.int64,
        )

    result = check_validity("peak_patch_psf_dictionary", path)

    assert result.outcome is ValidityOutcome.INVALID
    assert result.reason_codes == ("source_capture_binding_mismatch",)

def test_dictionary_validator_rejects_extent_compatibility_disagreement(
    tmp_path: Path,
) -> None:
    path = tmp_path / "dictionary.h5"
    _write_dictionary_h5(path)
    with h5py.File(path, "r+") as h5:
        h5["peak_patch_dictionary/extent_compatibility_json"][()] = json.dumps(
            {
                "matches": False,
                "mismatch_override": True,
                "reason": "not the embedded manifest record",
            },
            sort_keys=True,
        )

    result = check_validity("peak_patch_psf_dictionary", path)

    assert result.outcome is ValidityOutcome.INVALID
    assert result.reason_codes == ("manifest_metadata_mismatch",)


def _replace_dataset(
    h5: h5py.File,
    path: str,
    data: np.ndarray,
    *,
    dtype=None,
) -> None:
    parent_path, name = path.rsplit("/", 1)
    parent = h5[parent_path]
    del parent[name]
    parent.create_dataset(name, data=data, dtype=dtype)


@pytest.mark.parametrize(
    ("path", "data", "dtype"),
    [
        (
            "full_frame_survey/entry_wavelength_nm",
            np.asarray(["550"], dtype=object),
            h5py.string_dtype(encoding="utf-8"),
        ),
        (
            "full_frame_survey/entry_wavelength_nm",
            np.asarray([True], dtype=np.bool_),
            None,
        ),
    ],
)
def test_survey_rejects_non_real_wavelength_dtype(
    tmp_path: Path,
    path: str,
    data: np.ndarray,
    dtype,
) -> None:
    artifact = tmp_path / "survey_bad_wavelength_dtype.h5"
    _write_survey_h5(artifact)
    with h5py.File(artifact, "r+") as h5:
        _replace_dataset(h5, path, data, dtype=dtype)

    result = check_validity("full_frame_psf_survey", artifact)

    assert result.outcome is ValidityOutcome.INVALID
    assert result.reason_codes == ("dataset_dtype_invalid",)
    assert "validator_failed" not in result.reason_codes


@pytest.mark.parametrize(
    ("data", "dtype"),
    [
        (
            np.asarray(["550"], dtype=object),
            h5py.string_dtype(encoding="utf-8"),
        ),
        (np.asarray([550 + 0j], dtype=np.complex64), None),
    ],
)
def test_dictionary_rejects_non_real_wavelength_dtype(
    tmp_path: Path,
    data: np.ndarray,
    dtype,
) -> None:
    artifact = tmp_path / "dictionary_bad_wavelength_dtype.h5"
    _write_dictionary_h5(artifact)
    with h5py.File(artifact, "r+") as h5:
        _replace_dataset(
            h5,
            "peak_patch_dictionary/entry_wavelength_nm",
            data,
            dtype=dtype,
        )

    result = check_validity("peak_patch_psf_dictionary", artifact)

    assert result.outcome is ValidityOutcome.INVALID
    assert result.reason_codes == ("dataset_dtype_invalid",)
    assert "validator_failed" not in result.reason_codes


def test_raw_v3_rejects_complex_averaged_frames(tmp_path: Path) -> None:
    artifact = tmp_path / "raw_complex_average.h5"
    _write_raw_capture(artifact)
    with h5py.File(artifact, "r+") as h5:
        shape = h5["raw/frames_avg"].shape
        _replace_dataset(
            h5,
            "raw/frames_avg",
            np.zeros(shape, dtype=np.complex64),
        )

    result = check_validity("raw_capture", artifact)

    assert result.outcome is ValidityOutcome.INVALID
    assert result.reason_codes == ("dataset_dtype_invalid",)


def test_raw_v3_rejects_complex_burst_frames(tmp_path: Path) -> None:
    artifact = tmp_path / "raw_complex_burst.h5"
    _write_raw_capture(artifact)
    with h5py.File(artifact, "r+") as h5:
        h5["raw"].create_dataset(
            "frames",
            data=np.zeros((1, 1, 2, 3), dtype=np.complex64),
        )

    result = check_validity("raw_capture", artifact)

    assert result.outcome is ValidityOutcome.INVALID
    assert result.reason_codes == ("dataset_dtype_invalid",)


@pytest.mark.parametrize(
    ("path", "data", "dtype"),
    [
        ("capture/capture_index", np.asarray([0.0]), None),
        (
            "capture/wavelength_index",
            np.asarray(["0"], dtype=object),
            h5py.string_dtype(encoding="utf-8"),
        ),
        ("capture/mask_index", np.asarray([True], dtype=np.bool_), None),
    ],
)
def test_raw_v2_rejects_non_integer_index_dtypes(
    tmp_path: Path,
    path: str,
    data: np.ndarray,
    dtype,
) -> None:
    artifact = tmp_path / "raw_v2_bad_index_dtype.h5"
    _write_historical_v2_raw_capture(artifact)
    with h5py.File(artifact, "r+") as h5:
        _replace_dataset(h5, path, data, dtype=dtype)

    result = check_validity("raw_capture", artifact)

    assert result.outcome is ValidityOutcome.INVALID
    assert result.reason_codes == ("metadata_dtype_invalid",)


def test_raw_v3_rejects_non_prefix_completed_bitmap(tmp_path: Path) -> None:
    artifact = tmp_path / "raw_non_prefix_completed.h5"
    _write_multi_raw_capture(artifact, capture_count=2)
    with h5py.File(artifact, "r+") as h5:
        h5["capture/completed"][:] = [True, False, True, False]

    result = check_validity("raw_capture", artifact)

    assert result.outcome is ValidityOutcome.INVALID
    assert result.reason_codes == ("completed_bitmap_not_prefix",)


def test_raw_v3_accepts_prefix_partial_completed_bitmap(tmp_path: Path) -> None:
    artifact = tmp_path / "raw_prefix_partial.h5"
    _write_multi_raw_capture(artifact, capture_count=2)

    result = check_validity("raw_capture", artifact)

    assert result.outcome is ValidityOutcome.VALID


def test_raw_v3_accepts_prefix_rows_with_nonsorted_bound_indices(
    tmp_path: Path,
) -> None:
    artifact = tmp_path / "raw_prefix_nonsorted_indices.h5"
    _write_multi_raw_capture(artifact, capture_count=2)
    with h5py.File(artifact, "r+") as h5:
        h5["capture/capture_index"][:2] = [1, 0]
        h5["capture/wavelength_index"][:2] = [0, 0]
        h5["capture/mask_index"][:2] = [1, 0]
        raw_flags = h5["capture/processing_flags_json"][()]
        flags = json.loads(
            raw_flags.decode("utf-8")
            if isinstance(raw_flags, bytes)
            else str(raw_flags)
        )
        flags["last_completed_capture_index"] = 0
        h5["capture/processing_flags_json"][()] = json.dumps(flags, sort_keys=True)

    result = check_validity("raw_capture", artifact)

    assert result.outcome is ValidityOutcome.VALID


@pytest.mark.parametrize(
    ("artifact_type", "writer"),
    [
        ("full_frame_psf_survey", _write_survey_h5),
        ("peak_support_analysis_report", _write_support_report_h5),
        ("peak_patch_psf_dictionary", _write_dictionary_h5),
    ],
)
@pytest.mark.parametrize(
    ("attribute", "reason_code"),
    [
        ("artifact_type", "artifact_type_missing"),
        ("manifest_schema_version", "manifest_schema_version_missing"),
        ("payload_schema_version", "payload_schema_version_missing"),
    ],
)
def test_current_derived_hdf_requires_root_identity(
    tmp_path: Path,
    artifact_type: str,
    writer,
    attribute: str,
    reason_code: str,
) -> None:
    artifact = tmp_path / f"{artifact_type}_missing_{attribute}.h5"
    writer(artifact)
    with h5py.File(artifact, "r+") as h5:
        del h5.attrs[attribute]

    result = check_validity(artifact_type, artifact)

    assert result.outcome is ValidityOutcome.INVALID
    assert result.reason_codes == (reason_code,)
