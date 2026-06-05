from __future__ import annotations

import json
from pathlib import Path

import h5py
import numpy as np
import pytest

from tasks.capture_plan import CapturePlan
from tasks.profiles import CameraProfile, PupilProfile
from tasks.psf import (
    FullFramePSFSurveyError,
    PeakPatchPSFDictionaryError,
    build_full_frame_psf_survey,
    build_peak_patch_psf_dictionary,
    derive_peak_layout_profile,
    export_peak_patch_dictionary_to_lcd_forward,
    render_peak_patch_dense_view,
)
from tasks.psf.derive_peak_layout_profile import PeakLayoutProfileError
from tasks.raw_capture_h5 import RawCaptureWriter


def _h5_str(dset) -> str:
    val = dset[()]
    if isinstance(val, bytes):
        return val.decode()
    return str(val)


def _raw_psf_capture(
    tmp_path: Path,
    *,
    with_profiles: bool = True,
    wavelengths: list[float] | None = None,
    camera_frame_extent: dict | None = None,
    sensor_shape_hw: list[int] | None = None,
) -> Path:
    wavelengths = wavelengths or [450.0, 550.0]
    requires = (
        {
            "pupil_profile_id": "pupil_profile_v1",
            "camera_profile_id": "per_band_pupil_open_v1",
        }
        if with_profiles
        else {}
    )
    plan = CapturePlan.from_dict({
        "plan_id": "peak_patch_psf_raw",
        "requires": requires,
        "illumination": {
            "mode": "monochromatic",
            "wavelengths_nm": wavelengths,
        },
        "wavelengths": [
            {
                "illumination": {
                    "mode": "monochromatic",
                    "effective_wavelength_nm": float(wl),
                    "tls_setpoint_nm": float(wl),
                }
            }
            for wl in wavelengths
        ],
        "masks": [
            {"mask_id": "mask_a"},
            {"mask_id": "mask_b"},
        ],
        "camera": {"frames_per_capture": 2},
    })
    path = tmp_path / "raw_capture.h5"
    with RawCaptureWriter(path, plan) as writer:
        writer.write_physical_masks([
            np.full((3, 6), 10, dtype=np.uint8),
            np.full((3, 6), 20, dtype=np.uint8),
        ])
        for ci in range(plan.n_captures):
            frame = np.ones((20, 20), dtype=np.float64)
            frame[6, 5] = 100 + ci
            frame[12, 14] = 80 + ci
            writer.append_capture(
                capture_index=ci,
                wavelength_index=ci % plan.n_wavelengths,
                mask_index=ci // plan.n_wavelengths,
                frames=None,
                frames_avg=frame,
                camera_meta={
                    "frame_extent": camera_frame_extent or {
                        "mode": "full_sensor",
                        "origin_xy": [0, 0],
                        "shape_hw": [20, 20],
                        "sensor_shape_hw": sensor_shape_hw or [20, 20],
                    },
                    "status": (
                        {"sensor_shape_hw": sensor_shape_hw}
                        if sensor_shape_hw is not None
                        else {}
                    )
                },
            )
    return path


def _write_profile_manifests(tmp_path: Path, wavelengths: list[int] | None = None) -> tuple[Path, Path]:
    wavelengths = wavelengths or [450, 550]
    pupil = PupilProfile.from_dict({
        "pupil_profile_id": "pupil_profile_v1",
        "lcd_coordinate_convention": "physical_mono_xy",
        "lcd_display_index": 1,
        "subpixel_axis": 1,
        "lcd_physical_center": [10.0, 20.0],
        "lcd_physical_radius": 5.0,
    })
    camera = CameraProfile.from_dict({
        "camera_profile_id": "per_band_pupil_open_v1",
        "profile_family": "per_band_pupil_open",
        "depends_on": {"pupil_profile_id": "pupil_profile_v1"},
        "illumination": {
            "mode": "monochromatic",
            "wavelengths_nm": wavelengths,
        },
        "lcd_state": {
            "mode": "selected_pupil_open",
            "pupil_profile_id": "pupil_profile_v1",
        },
        "camera": {
            "per_wavelength": {
                str(wl): {"exposure_us": 100.0 + wl, "gain_db": 0.0}
                for wl in wavelengths
            }
        },
        "valid_for": ["psf_dictionary_capture"],
    })
    pupil_path = tmp_path / "pupil_profile.json"
    camera_path = tmp_path / "camera_profile.json"
    pupil.to_json(pupil_path)
    camera.to_json(camera_path)
    return pupil_path, camera_path


def _survey_and_layout(tmp_path: Path) -> tuple[Path, Path, Path]:
    raw_path = _raw_psf_capture(tmp_path, sensor_shape_hw=[20, 20])
    pupil_manifest, camera_manifest = _write_profile_manifests(tmp_path)
    survey_path = tmp_path / "survey.h5"
    build_full_frame_psf_survey(
        source_raw_capture_h5=raw_path,
        output_h5=survey_path,
        survey_id="survey_v1",
        pupil_profile_manifest=pupil_manifest,
        camera_profile_manifest=camera_manifest,
    )
    layout_path = tmp_path / "peak_layout.json"
    derive_peak_layout_profile(
        survey_h5=survey_path,
        output_json=layout_path,
        peak_layout_id="peak_layout_v1",
        patch_shape_hw=(5, 5),
        threshold_sigma=1.0,
        min_area=1,
    )
    return raw_path, survey_path, layout_path


def test_builds_full_frame_survey_as_scout_artifact(tmp_path: Path) -> None:
    raw_path = _raw_psf_capture(tmp_path, sensor_shape_hw=[20, 20])
    pupil_manifest, camera_manifest = _write_profile_manifests(tmp_path)
    survey_path = tmp_path / "survey.h5"

    manifest = build_full_frame_psf_survey(
        source_raw_capture_h5=raw_path,
        output_h5=survey_path,
        survey_id="survey_v1",
        pupil_profile_manifest=pupil_manifest,
        camera_profile_manifest=camera_manifest,
    )

    assert manifest.full_frame_role == "scout"
    assert manifest.entry_mask_ids == ["mask_a", "mask_a", "mask_b", "mask_b"]
    assert manifest.unique_wavelengths_nm == [450.0, 550.0]
    with h5py.File(survey_path, "r") as f:
        assert f["full_frame_survey/frames_avg"].shape == (4, 20, 20)
        raw_illumination = f["full_frame_survey/entry_illumination_json"][0]
        illumination = json.loads(
            raw_illumination.decode("utf-8")
            if isinstance(raw_illumination, bytes)
            else str(raw_illumination)
        )
        assert illumination["mode"] == "monochromatic"
        assert illumination["effective_wavelength_nm"] == 450.0
        assert _h5_str(f["profiles/pupil_profile_id"]) == "pupil_profile_v1"
        assert json.loads(_h5_str(f["full_frame_survey/manifest_json"]))["full_frame_role"] == "scout"


def test_full_frame_survey_defaults_to_confirmed_full_sensor(tmp_path: Path) -> None:
    raw_path = _raw_psf_capture(
        tmp_path,
        camera_frame_extent={
            "mode": "acquired_frame",
            "origin_xy": [5, 7],
            "shape_hw": [20, 20],
            "sensor_shape_hw": [40, 40],
        },
    )
    pupil_manifest, camera_manifest = _write_profile_manifests(tmp_path)

    with pytest.raises(FullFramePSFSurveyError, match="full-sensor"):
        build_full_frame_psf_survey(
            source_raw_capture_h5=raw_path,
            output_h5=tmp_path / "survey.h5",
            pupil_profile_manifest=pupil_manifest,
            camera_profile_manifest=camera_manifest,
        )


def test_peak_layout_rejects_raw_frames_avg_input(tmp_path: Path) -> None:
    raw_path = _raw_psf_capture(tmp_path, sensor_shape_hw=[20, 20])

    with pytest.raises(PeakLayoutProfileError, match="full_frame_survey/frames_avg"):
        derive_peak_layout_profile(
            survey_h5=raw_path,
            output_json=tmp_path / "peak_layout.json",
            peak_layout_id="peak_layout_v1",
        )


def test_derives_peak_layout_profile_from_survey(tmp_path: Path) -> None:
    _, survey_path, layout_path = _survey_and_layout(tmp_path)

    layout = derive_peak_layout_profile(
        survey_h5=survey_path,
        output_json=layout_path,
        peak_layout_id="peak_layout_v1",
        patch_shape_hw=(5, 5),
        threshold_sigma=1.0,
        min_area=1,
    )

    assert layout.peak_layout_id == "peak_layout_v1"
    assert layout.peak_ids == ["peak_0000", "peak_0001"]
    assert layout.patch_shape_hw == [[5, 5], [5, 5]]
    assert layout.coordinate_frame == "sensor_full_frame"
    assert layout.camera_frame_extent == {
        "mode": "full_sensor",
        "origin_xy": [0, 0],
        "shape_hw": [20, 20],
        "sensor_shape_hw": [20, 20],
        "source": "camera_metadata",
    }
    assert layout.survey_mask_ids == ["mask_a", "mask_b"]
    assert layout.survey_wavelengths_nm == [450.0, 550.0]
    assert layout.validity_scope == {
        "mask_scope": "survey_only",
        "wavelength_scope": "survey_only",
    }
    assert (
        layout.detection_policy["algorithm_role"]
        == "first_pass_high_energy_layout_baseline"
    )
    assert "low-energy stable far-field peaks" in layout.detection_policy["known_limitation"]
    assert layout.valid_mask_ids == ["mask_a", "mask_b"]
    assert layout_path.exists()


def test_builds_peak_patch_dictionary_from_raw_capture_and_layout(tmp_path: Path) -> None:
    raw_path, _, layout_path = _survey_and_layout(tmp_path)
    pupil_manifest, camera_manifest = _write_profile_manifests(tmp_path)
    dictionary_path = tmp_path / "peak_patch_dictionary.h5"

    manifest = build_peak_patch_psf_dictionary(
        source_raw_capture_h5=raw_path,
        peak_layout_profile=layout_path,
        output_h5=dictionary_path,
        dictionary_id="peak_patch_dict_v1",
        pupil_profile_manifest=pupil_manifest,
        camera_profile_manifest=camera_manifest,
    )

    assert manifest.dictionary_id == "peak_patch_dict_v1"
    assert manifest.entry_wavelengths_nm == [450.0, 550.0, 450.0, 550.0]
    assert manifest.unique_mask_ids == ["mask_a", "mask_b"]
    with h5py.File(dictionary_path, "r") as f:
        assert f["peak_patch_dictionary/patches"].shape == (4, 2, 5, 5)
        assert f["peak_patch_dictionary/patches"].dtype == np.float32
        assert "psf_dictionary/frames_avg" not in f
        assert list(f["peak_patch_dictionary/entry_capture_indices"][3]) == [3]
        assert f["peak_patch_dictionary/patch_origin_xy"].shape == (2, 2)
        assert _h5_str(f["peak_patch_dictionary/coordinate_frame"]) == "sensor_full_frame"
        assert _h5_str(f["source/peak_layout_profile"]) == str(layout_path)


def test_peak_patch_dictionary_rejects_profile_missing_raw_wavelength(tmp_path: Path) -> None:
    raw_path, _, layout_path = _survey_and_layout(tmp_path)
    pupil_manifest, camera_manifest = _write_profile_manifests(tmp_path, wavelengths=[450])

    with pytest.raises(PeakPatchPSFDictionaryError, match="550"):
        build_peak_patch_psf_dictionary(
            source_raw_capture_h5=raw_path,
            peak_layout_profile=layout_path,
            output_h5=tmp_path / "peak_patch_dictionary.h5",
            pupil_profile_manifest=pupil_manifest,
            camera_profile_manifest=camera_manifest,
        )


def test_peak_patch_dictionary_rejects_camera_frame_extent_mismatch(tmp_path: Path) -> None:
    _, _, layout_path = _survey_and_layout(tmp_path)
    raw_path = _raw_psf_capture(
        tmp_path,
        camera_frame_extent={
            "mode": "acquired_frame",
            "origin_xy": [10, 20],
            "shape_hw": [20, 20],
            "sensor_shape_hw": [40, 40],
        },
    )
    pupil_manifest, camera_manifest = _write_profile_manifests(tmp_path)

    with pytest.raises(PeakPatchPSFDictionaryError, match="camera_frame_extent"):
        build_peak_patch_psf_dictionary(
            source_raw_capture_h5=raw_path,
            peak_layout_profile=layout_path,
            output_h5=tmp_path / "peak_patch_dictionary.h5",
            pupil_profile_manifest=pupil_manifest,
            camera_profile_manifest=camera_manifest,
            allow_acquired_frame_only=True,
        )


def test_exports_peak_patch_dictionary_to_lcd_forward(tmp_path: Path) -> None:
    raw_path, _, layout_path = _survey_and_layout(tmp_path)
    pupil_manifest, camera_manifest = _write_profile_manifests(tmp_path)
    dictionary_path = tmp_path / "peak_patch_dictionary.h5"
    build_peak_patch_psf_dictionary(
        source_raw_capture_h5=raw_path,
        peak_layout_profile=layout_path,
        output_h5=dictionary_path,
        pupil_profile_manifest=pupil_manifest,
        camera_profile_manifest=camera_manifest,
    )
    output_path = tmp_path / "lcd_forward_peak_patch.h5"

    export_peak_patch_dictionary_to_lcd_forward(
        dictionary_h5=dictionary_path,
        output_h5=output_path,
        include_dense_diagnostic=True,
    )

    with h5py.File(output_path, "r") as f:
        assert f["psf_peak_patches"].shape == (4, 2, 5, 5)
        assert f["peak_table/patch_origin_xy"].shape == (2, 2)
        assert _h5_str(f["peak_table/coordinate_frame"]) == "sensor_full_frame"
        assert list(f["entries/wavelength_nm"][:]) == [450.0, 550.0, 450.0, 550.0]
        assert _h5_str(f["source/raw_capture_h5"]) == str(raw_path)
        assert _h5_str(f["source/dictionary_h5"]) == str(dictionary_path)
        assert f["exports/dense_diagnostic/psf_dense"].shape == (4, 20, 20)
        metadata = json.loads(_h5_str(f["metadata_json"]))
        assert metadata["exports"]["dense_diagnostic_view"]["enabled"] is True


def test_render_peak_patch_dense_view_uses_recorded_coordinates() -> None:
    patches = np.ones((1, 2, 3, 3), dtype=np.float32)
    origins = np.asarray([[1, 2], [5, 6]], dtype=np.int64)

    dense = render_peak_patch_dense_view(
        patches,
        patch_origin_xy=origins,
        frame_shape=(10, 10),
    )

    assert dense.shape == (1, 10, 10)
    assert dense[0, 2:5, 1:4].sum() == 9
    assert dense[0, 6:9, 5:8].sum() == 9
