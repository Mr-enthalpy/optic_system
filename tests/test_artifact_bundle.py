from __future__ import annotations

from pathlib import Path

import pytest

from tasks.artifact_versioning import schema_compat
from tasks.artifacts.bundle import (
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


def test_bundle_json_roundtrip_and_local_payload_validation(tmp_path: Path) -> None:
    bundle, generation, _ = _bundle_for_profile(tmp_path)
    manifest_path = generation / "bundle.manifest.json"

    bundle.write_json_atomic(manifest_path)
    loaded = ArtifactBundleManifest.load_json(manifest_path)
    result = validate_bundle(loaded, generation)

    assert loaded.to_dict() == bundle.to_dict()
    assert result.outcome is ValidityOutcome.VALID
    assert result.schema_version == bundle.schema_version


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
