from __future__ import annotations

import json
import os
from pathlib import Path

import h5py
import numpy as np
import pytest

from tasks.artifact_versioning import schema_compat
from tasks.artifacts.bundle import (
    ARTIFACT_BUNDLE_SCHEMA_VERSION,
    ArtifactBundleError,
    ArtifactBundleManifest,
    ArtifactLocation,
    ArtifactPayload,
    compute_file_sha256,
    inspect_payload,
    validate_bundle,
)
from tasks.artifacts.validation import ValidityOutcome
from tasks.profiles import PupilProfile
from tasks.psf.build_full_frame_psf_survey import FullFramePSFSurveyManifest


_STRING_DTYPE = h5py.string_dtype(encoding="utf-8")


def _pupil_profile() -> PupilProfile:
    return PupilProfile.from_dict(
        {
            "pupil_profile_id": "bundle_pupil_v1",
            "lcd_coordinate_convention": "physical_mono_xy",
            "lcd_display_index": 1,
            "subpixel_axis": 1,
            "lcd_physical_center": [10.0, 20.0],
            "lcd_physical_radius": 5.0,
        }
    )


def _bundle_for_profile(tmp_path: Path) -> tuple[ArtifactBundleManifest, Path, Path]:
    generation = tmp_path / "generation"
    generation.mkdir()
    data_path = generation / "profile.json"
    _pupil_profile().to_json(data_path)
    payload = inspect_payload(
        data_path,
        "application/json",
        rel_path="profile.json",
    )
    bundle = ArtifactBundleManifest(
        artifact_id="bundle_pupil_v1",
        artifact_type="pupil_profile",
        schema_version=schema_compat("pupil_profile").current,
        payloads={"data": payload},
    )
    return bundle, generation, data_path


def _extent(shape: tuple[int, int]) -> dict[str, object]:
    height, width = shape
    return {
        "mode": "full_sensor",
        "origin_xy": [0, 0],
        "shape_hw": [height, width],
        "sensor_shape_hw": [height, width],
    }


def _write_text_array(group: h5py.Group, name: str, values: list[str]) -> None:
    group.create_dataset(
        name,
        data=np.asarray(values, dtype=object),
        dtype=_STRING_DTYPE,
    )


def _survey_manifest() -> FullFramePSFSurveyManifest:
    shape = (2, 3)
    return FullFramePSFSurveyManifest(
        survey_id="bundle_survey_v1",
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


def _write_survey_h5(path: Path, manifest: FullFramePSFSurveyManifest) -> None:
    with h5py.File(path, "w") as h5:
        h5.attrs["artifact_type"] = "full_frame_psf_survey"
        h5.attrs["schema_version"] = schema_compat("full_frame_psf_survey").current
        h5.attrs["survey_id"] = manifest.survey_id
        group = h5.require_group("full_frame_survey")
        group.create_dataset("frames_avg", data=np.zeros((1, 2, 3), dtype=np.float32))
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
        group.create_dataset("mask_index", data=np.asarray([0], dtype=np.int64))
        group.create_dataset("wavelength_index", data=np.asarray([0], dtype=np.int64))
        group.create_dataset("capture_indices", data=np.asarray([0], dtype=np.int64))
        group.create_dataset("frame_shape", data=np.asarray(manifest.frame_shape, dtype=np.int64))
        group.create_dataset(
            "camera_frame_extent_json",
            data=json.dumps(manifest.camera_frame_extent, sort_keys=True),
            dtype=_STRING_DTYPE,
        )
        group.create_dataset(
            "survey_policy_json",
            data=json.dumps(manifest.survey_policy, sort_keys=True),
            dtype=_STRING_DTYPE,
        )
        group.create_dataset("manifest_json", data=manifest.to_json(), dtype=_STRING_DTYPE)
        source = h5.require_group("source")
        source.create_dataset(
            "plan_json",
            data=json.dumps(
                {
                    "plan_id": "bundle_plan_v1",
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
                },
                sort_keys=True,
            ),
            dtype=_STRING_DTYPE,
        )


def _bundle_for_survey(
    tmp_path: Path,
    *,
    sidecar_data: dict[str, object] | None = None,
) -> tuple[ArtifactBundleManifest, Path, FullFramePSFSurveyManifest]:
    generation = tmp_path / "survey_generation"
    generation.mkdir()
    manifest = _survey_manifest()
    data_path = generation / "survey.h5"
    _write_survey_h5(data_path, manifest)
    sidecar_path = generation / "survey.manifest.json"
    sidecar_path.write_text(
        json.dumps(sidecar_data or manifest.to_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    bundle = ArtifactBundleManifest(
        artifact_id=manifest.survey_id,
        artifact_type="full_frame_psf_survey",
        schema_version=schema_compat("full_frame_psf_survey").current,
        payloads={
            "data": inspect_payload(data_path, "application/x-hdf5", rel_path="survey.h5"),
            "manifest_sidecar": inspect_payload(
                sidecar_path,
                "application/json",
                rel_path="survey.manifest.json",
            ),
        },
    )
    return bundle, generation, manifest


def test_bundle_json_roundtrip_and_local_payload_validation(tmp_path: Path) -> None:
    bundle, generation, _ = _bundle_for_profile(tmp_path)
    manifest_path = generation / "bundle.manifest.json"

    bundle.write_json_atomic(manifest_path)
    loaded = ArtifactBundleManifest.load_json(manifest_path)
    result = validate_bundle(loaded, generation)

    assert loaded.to_dict() == bundle.to_dict()
    assert loaded.bundle_schema_version == ARTIFACT_BUNDLE_SCHEMA_VERSION
    assert result.outcome is ValidityOutcome.VALID
    assert result.schema_version == bundle.schema_version


def test_bundle_validates_matching_hdf5_embedded_manifest_sidecar(tmp_path: Path) -> None:
    bundle, generation, _ = _bundle_for_survey(tmp_path)

    result = validate_bundle(bundle, generation)

    assert result.outcome is ValidityOutcome.VALID


def test_bundle_rejects_hdf5_primary_with_wrong_declared_media_type(
    tmp_path: Path,
) -> None:
    bundle, generation, _ = _bundle_for_survey(tmp_path)
    data = bundle.payloads["data"]
    bundle = ArtifactBundleManifest(
        artifact_id=bundle.artifact_id,
        artifact_type=bundle.artifact_type,
        schema_version=bundle.schema_version,
        payloads={
            **bundle.payloads,
            "data": ArtifactPayload(
                rel_path=data.rel_path,
                media_type="text/plain",
                size_bytes=data.size_bytes,
                sha256=data.sha256,
            ),
        },
    )

    result = validate_bundle(bundle, generation)

    assert result.outcome is ValidityOutcome.INVALID
    assert result.reason_codes == ("payload_media_type_mismatch",)


def test_bundle_rejects_json_primary_with_wrong_declared_media_type(
    tmp_path: Path,
) -> None:
    bundle, generation, _ = _bundle_for_profile(tmp_path)
    data = bundle.payloads["data"]
    bundle = ArtifactBundleManifest(
        artifact_id=bundle.artifact_id,
        artifact_type=bundle.artifact_type,
        schema_version=bundle.schema_version,
        payloads={
            "data": ArtifactPayload(
                rel_path=data.rel_path,
                media_type="application/x-hdf5",
                size_bytes=data.size_bytes,
                sha256=data.sha256,
            ),
        },
    )

    result = validate_bundle(bundle, generation)

    assert result.outcome is ValidityOutcome.INVALID
    assert result.reason_codes == ("payload_media_type_mismatch",)


def test_bundle_rejects_manifest_sidecar_with_non_json_media_type(
    tmp_path: Path,
) -> None:
    bundle, generation, _ = _bundle_for_survey(tmp_path)
    sidecar = bundle.payloads["manifest_sidecar"]
    bundle = ArtifactBundleManifest(
        artifact_id=bundle.artifact_id,
        artifact_type=bundle.artifact_type,
        schema_version=bundle.schema_version,
        payloads={
            **bundle.payloads,
            "manifest_sidecar": ArtifactPayload(
                rel_path=sidecar.rel_path,
                media_type="text/plain",
                size_bytes=sidecar.size_bytes,
                sha256=sidecar.sha256,
            ),
        },
    )

    result = validate_bundle(bundle, generation)

    assert result.outcome is ValidityOutcome.INVALID
    assert result.reason_codes == ("payload_media_type_mismatch",)


def test_bundle_generation_id_need_not_equal_payload_native_id(tmp_path: Path) -> None:
    bundle, generation, _ = _bundle_for_survey(tmp_path)
    bundle = ArtifactBundleManifest(
        artifact_id="generation_20260713_001",
        artifact_type=bundle.artifact_type,
        schema_version=bundle.schema_version,
        payloads=bundle.payloads,
    )

    result = validate_bundle(bundle, generation)

    assert result.outcome is ValidityOutcome.VALID


def test_bundle_rejects_payload_roles_that_share_one_path(tmp_path: Path) -> None:
    bundle, _, _ = _bundle_for_profile(tmp_path)
    data = bundle.payloads["data"]

    with pytest.raises(ArtifactBundleError, match="unique across roles"):
        ArtifactBundleManifest(
            artifact_id=bundle.artifact_id,
            artifact_type=bundle.artifact_type,
            schema_version=bundle.schema_version,
            payloads={"data": data, "manifest_sidecar": data},
        )


@pytest.mark.skipif(os.name != "nt", reason="Windows case-alias behavior")
def test_bundle_rejects_case_alias_to_same_payload_on_windows(tmp_path: Path) -> None:
    bundle, generation, data_path = _bundle_for_profile(tmp_path)
    data = bundle.payloads["data"]
    case_alias = inspect_payload(
        data_path,
        "application/json",
        rel_path="PROFILE.JSON",
    )
    aliased_bundle = ArtifactBundleManifest(
        artifact_id=bundle.artifact_id,
        artifact_type=bundle.artifact_type,
        schema_version=bundle.schema_version,
        payloads={"data": data, "manifest_sidecar": case_alias},
    )

    result = validate_bundle(aliased_bundle, generation)

    assert result.outcome is ValidityOutcome.INVALID
    assert result.reason_codes == ("payload_role_alias",)


def test_bundle_rejects_hard_link_alias_between_payload_roles(
    tmp_path: Path,
) -> None:
    bundle, generation, data_path = _bundle_for_profile(tmp_path)
    sidecar_path = generation / "profile-sidecar.json"
    try:
        os.link(data_path, sidecar_path)
    except OSError as exc:
        pytest.skip(f"hard links unavailable: {exc}")
    aliased_bundle = ArtifactBundleManifest(
        artifact_id=bundle.artifact_id,
        artifact_type=bundle.artifact_type,
        schema_version=bundle.schema_version,
        payloads={
            "data": bundle.payloads["data"],
            "manifest_sidecar": inspect_payload(
                sidecar_path,
                "application/json",
                rel_path="profile-sidecar.json",
            ),
        },
    )

    result = validate_bundle(aliased_bundle, generation)

    assert result.outcome is ValidityOutcome.INVALID
    assert result.reason_codes == ("payload_role_alias",)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda data: data.__setitem__("survey_id", "other_survey"),
        lambda data: data.__setitem__("artifact_type", "peak_patch_psf_dictionary"),
        lambda data: data.__setitem__("schema_version", 99),
        lambda data: data.__setitem__(
            "survey_policy",
            {"background": "changed", "normalization": "none"},
        ),
    ],
)
def test_bundle_rejects_manifest_sidecar_hdf5_disagreement(
    tmp_path: Path,
    mutate,
) -> None:
    sidecar_data = _survey_manifest().to_dict()
    mutate(sidecar_data)
    bundle, generation, _ = _bundle_for_survey(tmp_path, sidecar_data=sidecar_data)

    result = validate_bundle(bundle, generation)

    assert result.outcome is ValidityOutcome.INVALID
    assert result.reason_codes == ("manifest_sidecar_mismatch",)


def test_payload_inspection_uses_canonical_streaming_digest(tmp_path: Path) -> None:
    data_path = tmp_path / "payload.bin"
    data_path.write_bytes(b"abc")

    payload = inspect_payload(data_path, "application/octet-stream")

    assert payload.size_bytes == 3
    assert payload.sha256 == (
        "sha256:ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    )
    assert compute_file_sha256(data_path) == payload.sha256


@pytest.mark.parametrize(
    "rel_path",
    ["../escape.json", "nested/../../escape.json", "C:\\escape.json", "/escape.json"],
)
def test_bundle_rejects_payload_path_traversal_and_absolute_paths(rel_path: str) -> None:
    with pytest.raises(ArtifactBundleError, match="relative|traversal"):
        ArtifactPayload(
            rel_path=rel_path,
            media_type="application/json",
            size_bytes=0,
            sha256="sha256:" + "0" * 64,
        )


def test_artifact_location_rejects_absolute_path() -> None:
    with pytest.raises(ArtifactBundleError, match="relative"):
        ArtifactLocation(storage_root="primary", rel_path="C:\\data\\artifact")


def test_bundle_validation_rejects_missing_payload(tmp_path: Path) -> None:
    bundle, generation, data_path = _bundle_for_profile(tmp_path)
    data_path.unlink()

    result = validate_bundle(bundle, generation)

    assert result.outcome is ValidityOutcome.UNREADABLE
    assert result.reason_codes == ("payload_missing",)


def test_bundle_validation_rejects_digest_mismatch(tmp_path: Path) -> None:
    bundle, generation, data_path = _bundle_for_profile(tmp_path)
    size = data_path.stat().st_size
    data_path.write_bytes(b"x" * size)

    result = validate_bundle(bundle, generation)

    assert result.outcome is ValidityOutcome.INVALID
    assert result.reason_codes == ("payload_digest_mismatch",)


def test_bundle_without_explicit_primary_payload_is_unsupported(tmp_path: Path) -> None:
    bundle, generation, data_path = _bundle_for_profile(tmp_path)
    sidecar = ArtifactPayload(
        rel_path="profile.json",
        media_type="application/json",
        size_bytes=data_path.stat().st_size,
        sha256=compute_file_sha256(data_path),
    )
    inventory_only = ArtifactBundleManifest(
        artifact_id=bundle.artifact_id,
        artifact_type=bundle.artifact_type,
        schema_version=bundle.schema_version,
        payloads={"manifest_sidecar": sidecar},
    )

    result = validate_bundle(inventory_only, generation)

    assert result.outcome is ValidityOutcome.UNSUPPORTED
    assert result.reason_codes == ("primary_payload_not_declared",)


def test_bundle_atomic_write_leaves_no_temporary_manifest(tmp_path: Path) -> None:
    bundle, generation, _ = _bundle_for_profile(tmp_path)
    manifest_path = generation / "bundle.manifest.json"

    bundle.to_json(manifest_path)

    assert manifest_path.exists()
    assert ArtifactBundleManifest.load_json(manifest_path).artifact_id == bundle.artifact_id
    assert list(generation.glob(".bundle.manifest.json.*.tmp")) == []


def test_bundle_non_utf8_manifest_is_unreadable_not_an_exception(tmp_path: Path) -> None:
    manifest_path = tmp_path / "bundle.manifest.json"
    manifest_path.write_bytes(b"\xff\xfe")

    result = validate_bundle(manifest_path, tmp_path)

    assert result.outcome is ValidityOutcome.UNREADABLE
    assert result.reason_codes == ("bundle_manifest_unreadable",)
    with pytest.raises(ArtifactBundleError, match="UTF-8"):
        ArtifactBundleManifest.load_json(manifest_path)


def test_bundle_with_newer_artifact_schema_is_unsupported(tmp_path: Path) -> None:
    manifest_path = tmp_path / "bundle.manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "bundle_schema_version": ARTIFACT_BUNDLE_SCHEMA_VERSION,
                "artifact_id": "future_pupil_generation",
                "artifact_type": "pupil_profile",
                "schema_version": schema_compat("pupil_profile").current + 1,
                "payloads": {
                    "data": {
                        "rel_path": "profile.json",
                        "media_type": "application/json",
                        "size_bytes": 0,
                        "sha256": "sha256:" + "0" * 64,
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    result = validate_bundle(manifest_path, tmp_path)

    assert result.outcome is ValidityOutcome.UNSUPPORTED
    assert result.reason_codes == ("schema_newer_than_supported",)


def test_bundle_with_newer_envelope_schema_is_unsupported(tmp_path: Path) -> None:
    manifest_path = tmp_path / "bundle.manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "bundle_schema_version": ARTIFACT_BUNDLE_SCHEMA_VERSION + 1,
                "artifact_id": "future_bundle_generation",
                "artifact_type": "pupil_profile",
                "schema_version": schema_compat("pupil_profile").current,
                "payloads": {
                    "data": {
                        "rel_path": "profile.json",
                        "media_type": "application/json",
                        "size_bytes": 0,
                        "sha256": "sha256:" + "0" * 64,
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    result = validate_bundle(manifest_path, tmp_path)

    assert result.outcome is ValidityOutcome.UNSUPPORTED
    assert result.reason_codes == ("bundle_schema_newer_than_supported",)


def test_bundle_requires_explicit_envelope_schema_version(tmp_path: Path) -> None:
    bundle, generation, _ = _bundle_for_profile(tmp_path)
    data = bundle.to_dict()
    del data["bundle_schema_version"]
    manifest_path = generation / "bundle.manifest.json"
    manifest_path.write_text(json.dumps(data), encoding="utf-8")

    result = validate_bundle(manifest_path, generation)

    assert result.outcome is ValidityOutcome.INVALID
    assert result.reason_codes == ("bundle_schema_version_invalid",)
