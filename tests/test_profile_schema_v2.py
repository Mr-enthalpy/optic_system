from __future__ import annotations

import json
from pathlib import Path

import pytest

from tasks.artifacts.validation import ValidityOutcome, check_validity
from tasks.profiles import (
    CameraProfile,
    ProfileError,
    PupilProfile,
    import_camera_profile_yaml,
    import_pupil_profile_yaml,
    migrate_camera_profile_v1_to_v2,
    migrate_pupil_profile_v1_to_v2,
)
from test_profile_artifacts import (
    per_band_camera_profile_dict,
    pupil_profile_dict,
)


def _camera_v1() -> dict:
    data = per_band_camera_profile_dict()
    data["schema_version"] = 1
    del data["camera"]["per_wavelength"]["450"]["gain_db"]
    return data


def _pupil_v1() -> dict:
    data = pupil_profile_dict()
    data["schema_version"] = 1
    data["aperture_window"] = [10, 20, 30, 40]
    data["recommended_roi"] = [5, 7, 11, 13]
    return data


def test_camera_v1_read_tracks_source_and_writer_refuses_upgrade(
    tmp_path: Path,
) -> None:
    profile = CameraProfile.from_dict(_camera_v1())

    assert profile.source_schema_version == 1
    assert profile.per_wavelength["450"].gain_db == 0.0
    assert profile.legacy_gain_default_keys == ("450",)
    with pytest.raises(ProfileError, match="migrate_camera_profile_v1_to_v2"):
        profile.to_json(tmp_path / "illegal-v2.json")


def test_camera_v1_migration_is_explicit_and_audited(tmp_path: Path) -> None:
    migrated = migrate_camera_profile_v1_to_v2(
        CameraProfile.from_dict(_camera_v1())
    )
    output = tmp_path / "camera-v2.json"

    migrated.to_json(output)
    data = json.loads(output.read_text(encoding="utf-8"))

    assert migrated.source_schema_version == 2
    assert data["schema_version"] == 2
    assert data["extra"]["migration"]["source_schema_version"] == 1
    assert data["extra"]["migration"]["historical_gain_default_keys"] == [
        "450"
    ]
    assert check_validity("camera_profile", output).outcome is ValidityOutcome.VALID


def test_camera_v1_mixed_mode_requires_author_decision() -> None:
    data = _camera_v1()
    data["exposure_us"] = 100.0
    profile = CameraProfile.from_dict(data)

    with pytest.raises(ProfileError, match="author decision"):
        migrate_camera_profile_v1_to_v2(profile)


def test_camera_v2_requires_explicit_gain() -> None:
    data = per_band_camera_profile_dict()
    del data["camera"]["per_wavelength"]["450"]["gain_db"]

    with pytest.raises(ProfileError, match="gain_db"):
        CameraProfile.from_dict(data)


def test_camera_v2_rejects_mixed_settings_mode() -> None:
    data = per_band_camera_profile_dict()
    data["exposure_us"] = 100.0

    with pytest.raises(ProfileError, match="scalar camera settings"):
        CameraProfile.from_dict(data)


def test_pupil_v1_read_tracks_xywh_and_writer_refuses_upgrade(
    tmp_path: Path,
) -> None:
    profile = PupilProfile.from_dict(_pupil_v1())

    assert profile.source_schema_version == 1
    assert profile.aperture_window == (10, 20, 30, 40)
    with pytest.raises(ProfileError, match="migrate_pupil_profile_v1_to_v2"):
        profile.to_json(tmp_path / "illegal-v2.json")


def test_pupil_v1_migration_converts_xywh_to_xyxy(tmp_path: Path) -> None:
    migrated = migrate_pupil_profile_v1_to_v2(
        PupilProfile.from_dict(_pupil_v1())
    )
    output = tmp_path / "pupil-v2.json"

    migrated.to_json(output)
    data = json.loads(output.read_text(encoding="utf-8"))

    assert migrated.aperture_window == (10, 20, 40, 60)
    assert migrated.recommended_roi == (5, 7, 16, 20)
    assert data["schema_version"] == 2
    assert data["extra"]["migration"]["aperture_window_conversion"] == (
        "xywh_to_xyxy"
    )
    assert check_validity("pupil_profile", output).outcome is ValidityOutcome.VALID


def test_pupil_v2_rejects_xywh_interpretation() -> None:
    data = pupil_profile_dict()
    data["aperture_window"] = [100, 100, 20, 20]

    with pytest.raises(ProfileError, match="XYXY"):
        PupilProfile.from_dict(data)


def test_profile_semantic_failure_has_domain_reason_code(tmp_path: Path) -> None:
    data = pupil_profile_dict()
    del data["lcd_physical_radius"]
    path = tmp_path / "invalid-pupil.json"
    path.write_text(json.dumps(data), encoding="utf-8")

    result = check_validity("pupil_profile", path)

    assert result.outcome is ValidityOutcome.INVALID
    assert result.reason_codes == ("semantic.pupil_profile.invalid",)


def test_yaml_is_import_input_not_artifact_representation(
    tmp_path: Path,
) -> None:
    source = tmp_path / "pupil.yaml"
    output = tmp_path / "pupil.json"
    source.write_text(
        "\n".join(
            (
                "pupil_profile_id: imported-pupil",
                "lcd_coordinate_convention: physical_mono_xy",
                "lcd_display_index: 1",
                "subpixel_axis: 1",
                "lcd_physical_center: [10.0, 20.0]",
                "lcd_physical_radius: 5.0",
                "aperture_window: [5, 15, 15, 25]",
            )
        ),
        encoding="utf-8",
    )

    profile = import_pupil_profile_yaml(source, output)

    assert not hasattr(PupilProfile, "load_yaml")
    assert profile.source_schema_version == 2
    assert check_validity("pupil_profile", output).outcome is ValidityOutcome.VALID
    assert check_validity("pupil_profile", source).outcome is (
        ValidityOutcome.UNREADABLE
    )


def test_camera_yaml_import_writes_canonical_json(tmp_path: Path) -> None:
    source = tmp_path / "camera.yaml"
    output = tmp_path / "camera.json"
    source.write_text(
        "\n".join(
            (
                "camera_profile_id: imported-camera",
                "profile_family: broadband_passthrough",
                "illumination:",
                "  mode: broadband_passthrough",
                "  tls_setpoint_nm: 0",
                "  effective_wavelength_nm: null",
                "lcd_state: {mode: safe_probe_mask}",
                "camera:",
                "  exposure_us: 100.0",
                "  gain_db: 0.0",
                "valid_for: [pupil_scan_broadband]",
            )
        ),
        encoding="utf-8",
    )

    profile = import_camera_profile_yaml(source, output)
    data = json.loads(output.read_text(encoding="utf-8"))

    assert not hasattr(CameraProfile, "load_yaml")
    assert profile.source_schema_version == 2
    assert data["schema_version"] == 2
    assert data["extra"]["import"]["canonical_representation"] == "json"
