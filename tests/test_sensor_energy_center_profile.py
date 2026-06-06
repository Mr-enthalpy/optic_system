from __future__ import annotations

import json
from pathlib import Path

import h5py
import numpy as np
import pytest

from tasks.psf import (
    DiffractionSupportAnalysisError,
    PeakLayoutProfileError,
    SensorEnergyCenterError,
    SensorEnergyCenterProfile,
    analyze_diffraction_support,
    derive_peak_layout_profile,
    derive_sensor_energy_center_profile,
    estimate_frame_energy_center,
)


def _decode(value) -> str:
    return value.decode("utf-8") if isinstance(value, bytes) else str(value)


def _gaussian_frame(
    shape: tuple[int, int],
    *,
    center_xy: tuple[float, float],
    amplitude: float = 100.0,
    sigma: float = 3.0,
    background: float = 7.0,
) -> np.ndarray:
    h, w = shape
    yy, xx = np.mgrid[:h, :w]
    cx, cy = center_xy
    return background + amplitude * np.exp(
        -(((xx - cx) ** 2 + (yy - cy) ** 2) / (2.0 * sigma ** 2))
    )


def _extent(shape: tuple[int, int]) -> dict:
    h, w = shape
    return {
        "mode": "full_sensor",
        "origin_xy": [0, 0],
        "shape_hw": [h, w],
        "sensor_shape_hw": [h, w],
    }


def _write_survey(
    path: Path,
    frames: np.ndarray,
    *,
    mask_ids: list[str] | None = None,
    wavelengths_nm: list[float] | None = None,
    extent: dict | None = None,
) -> None:
    arr = np.asarray(frames, dtype=np.float64)
    if arr.ndim == 2:
        arr = arr[np.newaxis, :, :]
    n, h, w = arr.shape
    mask_ids = mask_ids or [f"mask_{i}" for i in range(n)]
    wavelengths_nm = wavelengths_nm or [550.0 for _ in range(n)]
    extent = extent or _extent((h, w))
    string_dtype = h5py.string_dtype(encoding="utf-8")
    with h5py.File(str(path), "w") as f:
        g = f.require_group("full_frame_survey")
        g.create_dataset("frames_avg", data=arr)
        g.create_dataset("entry_mask_ids", data=np.asarray(mask_ids, dtype=object), dtype=string_dtype)
        g.create_dataset("entry_wavelength_nm", data=np.asarray(wavelengths_nm, dtype=np.float64))
        g.create_dataset("entry_illumination_json", data=np.asarray([
            json.dumps({"mode": "monochromatic", "effective_wavelength_nm": float(wl)})
            for wl in wavelengths_nm
        ], dtype=object), dtype=string_dtype)
        g.create_dataset("camera_frame_extent_json", data=json.dumps(extent), dtype=string_dtype)
        g.create_dataset(
            "manifest_json",
            data=json.dumps({
                "coordinate_frame": "sensor_full_frame" if extent.get("mode") == "full_sensor" else "acquired_frame",
                "camera_frame_extent": extent,
                "entry_mask_ids": mask_ids,
                "entry_wavelengths_nm": wavelengths_nm,
            }),
            dtype=string_dtype,
        )


def _write_raw_frames(path: Path, frames: np.ndarray) -> None:
    arr = np.asarray(frames, dtype=np.float64)
    if arr.ndim == 2:
        arr = arr[np.newaxis, :, :]
    string_dtype = h5py.string_dtype(encoding="utf-8")
    with h5py.File(str(path), "w") as f:
        raw = f.require_group("raw")
        raw.create_dataset("frames_avg", data=arr)
        raw.create_dataset("mask_id", data=np.asarray(["raw_mask"], dtype=object), dtype=string_dtype)
        raw.create_dataset("wavelength_nm", data=np.asarray([550.0], dtype=np.float64))
        camera = f.require_group("camera")
        camera.create_dataset(
            "frame_extent_json",
            data=[json.dumps({
                "mode": "full_sensor",
                "origin_xy": [0, 0],
                "shape_hw": [int(arr.shape[1]), int(arr.shape[2])],
                "sensor_shape_hw": [int(arr.shape[1]), int(arr.shape[2])],
            })],
            dtype=string_dtype,
        )


def _center_profile(
    tmp_path: Path,
    *,
    source_survey_h5: Path,
    center_xy: tuple[float, float],
    frame_shape: tuple[int, int],
    camera_frame_extent: dict | None = None,
) -> Path:
    profile = SensorEnergyCenterProfile(
        center_profile_id="center_profile_v1",
        source_survey_h5=str(source_survey_h5),
        coordinate_frame="sensor_full_frame",
        camera_frame_extent=camera_frame_extent or _extent(frame_shape),
        camera_frame_shape=frame_shape,
        center_xy=center_xy,
        estimator_name="test",
        bg_policy={"method": "percentile", "percentile": 5.0},
        corr_policy={"formula": "corr = max(frame - bg, 0)"},
        aggregation_policy={"method": "arithmetic_mean", "single_global_origin": True},
        per_entry_center_xy=[center_xy],
        per_entry_mask_ids=["mask_a"],
        per_entry_wavelengths_nm=[550.0],
        per_entry_background_value=[0.0],
        per_entry_total_corr_energy=[1.0],
        per_entry_fallback_used=[False],
        per_wavelength_mean_center_xy={"550": center_xy},
        per_wavelength_center_std_xy={"550": (0.0, 0.0)},
        global_center_std_xy=(0.0, 0.0),
        max_center_deviation_px=0.0,
    )
    path = tmp_path / "sensor_energy_center_profile.json"
    profile.to_json(path)
    return path


def test_gaussian_frame_estimates_known_sensor_energy_center() -> None:
    frame = _gaussian_frame((64, 80), center_xy=(42.25, 29.5), background=3.0)

    estimate = estimate_frame_energy_center(frame)

    assert abs(estimate.center_xy[0] - 42.25) < 0.05
    assert abs(estimate.center_xy[1] - 29.5) < 0.05
    assert estimate.background_value > 0.0


def test_background_offset_does_not_shift_center_after_correction() -> None:
    base = _gaussian_frame((64, 80), center_xy=(30.0, 25.0), background=0.0)
    shifted = base + 500.0

    base_center = estimate_frame_energy_center(base).center_xy
    shifted_center = estimate_frame_energy_center(shifted).center_xy

    assert np.allclose(base_center, shifted_center, atol=1e-6)


def test_derives_one_global_center_with_per_wavelength_diagnostics(tmp_path: Path) -> None:
    centers = [(30.0, 25.0), (32.0, 25.0), (31.0, 27.0)]
    frames = np.stack([_gaussian_frame((64, 80), center_xy=c) for c in centers])
    survey_h5 = tmp_path / "survey.h5"
    output_json = tmp_path / "sensor_energy_center_profile.json"
    _write_survey(
        survey_h5,
        frames,
        mask_ids=["mask_a", "mask_b", "mask_c"],
        wavelengths_nm=[450.0, 550.0, 550.0],
    )

    profile = derive_sensor_energy_center_profile(
        survey_h5,
        output_json,
        center_profile_id="center_profile_v1",
    )

    assert profile.center_profile_id == "center_profile_v1"
    assert np.allclose(profile.center_xy, np.mean(np.asarray(centers), axis=0), atol=0.05)
    assert set(profile.per_wavelength_mean_center_xy) == {"450", "550"}
    assert profile.aggregation_policy["single_global_origin"] is True
    assert profile.aggregation_policy["per_wavelength_origins"] is False
    assert len(profile.per_entry_background_value) == 3
    assert len(profile.per_entry_total_corr_energy) == 3
    assert profile.per_entry_fallback_used == [False, False, False]
    assert all(value > 0.0 for value in profile.per_entry_total_corr_energy)
    assert output_json.exists()


def test_flat_frame_records_peak_fallback_marker(tmp_path: Path) -> None:
    survey_h5 = tmp_path / "survey.h5"
    output_json = tmp_path / "sensor_energy_center_profile.json"
    _write_survey(survey_h5, np.full((16, 20), 5.0, dtype=np.float64))

    profile = derive_sensor_energy_center_profile(survey_h5, output_json)

    assert profile.per_entry_total_corr_energy == [0.0]
    assert profile.per_entry_fallback_used == [True]
    assert profile.center_xy == (0.0, 0.0)


def test_valid_pixel_domain_excludes_contaminating_region(tmp_path: Path) -> None:
    frame = _gaussian_frame((64, 80), center_xy=(40.0, 32.0), amplitude=40.0)
    frame[0:6, 0:6] += 5000.0
    survey_h5 = tmp_path / "survey.h5"
    output_json = tmp_path / "sensor_energy_center_profile.json"
    _write_survey(survey_h5, frame)

    contaminated = derive_sensor_energy_center_profile(
        survey_h5,
        tmp_path / "contaminated_center.json",
    )
    filtered = derive_sensor_energy_center_profile(
        survey_h5,
        output_json,
        valid_pixel_domain={"type": "exclude_top_rows", "top_rows": 8},
    )

    assert contaminated.center_xy[0] < 10.0
    assert abs(filtered.center_xy[0] - 40.0) < 0.1
    assert abs(filtered.center_xy[1] - 32.0) < 0.1
    assert filtered.bg_policy["valid_pixel_domain"] == {
        "type": "exclude_top_rows",
        "top_rows": 8,
    }


def test_rejects_raw_frames_avg_input(tmp_path: Path) -> None:
    raw_h5 = tmp_path / "raw.h5"
    frame = _gaussian_frame((16, 20), center_xy=(8.0, 6.0))
    _write_raw_frames(raw_h5, frame)

    with pytest.raises(
        SensorEnergyCenterError,
        match="SensorEnergyCenterProfile requires FullFramePSFSurvey",
    ):
        derive_sensor_energy_center_profile(raw_h5, tmp_path / "center.json")


def test_support_analysis_uses_sensor_energy_center_profile_for_radius(tmp_path: Path) -> None:
    frame = np.full((64, 64), 0.02, dtype=np.float64)
    frame[10:12, 40:42] += 5.0
    survey_h5 = tmp_path / "survey.h5"
    report_h5 = tmp_path / "support.h5"
    _write_survey(survey_h5, frame, mask_ids=["mask_a"], wavelengths_nm=[550.0])
    center_path = _center_profile(
        tmp_path,
        source_survey_h5=survey_h5,
        center_xy=(30.0, 10.0),
        frame_shape=(64, 64),
    )

    manifest = analyze_diffraction_support(
        survey_h5,
        report_h5,
        tau_values=[1.0],
        support_radii=[100],
        far_field_radius=9.0,
        min_component_area=1,
        center_profile=center_path,
    )

    assert manifest.radial_policy["center_policy"] == "sensor_energy_center_profile"
    assert manifest.radial_policy["center_profile_id"] == "center_profile_v1"
    assert manifest.radial_policy["center_xy"] == [30.0, 10.0]
    with h5py.File(str(report_h5), "r") as f:
        assert np.allclose(f["support_analysis/center_xy"][0], [30.0, 10.0])
        centroid_abs = f["components/centroid_xy_abs"][0]
        centroid_rel = f["components/centroid_xy_rel"][0]
        assert np.allclose(centroid_rel, centroid_abs - np.asarray([30.0, 10.0]))
        assert float(f["components/max_radius_from_energy_center"][0]) > 9.0


def test_center_profile_extent_mismatch_is_rejected_downstream(tmp_path: Path) -> None:
    survey_h5 = tmp_path / "survey.h5"
    report_h5 = tmp_path / "support.h5"
    _write_survey(survey_h5, _gaussian_frame((32, 32), center_xy=(12.0, 14.0)))
    bad_extent = _extent((32, 32))
    bad_extent["origin_xy"] = [10, 0]
    center_path = _center_profile(
        tmp_path,
        source_survey_h5=survey_h5,
        center_xy=(12.0, 14.0),
        frame_shape=(32, 32),
        camera_frame_extent=bad_extent,
    )

    with pytest.raises(DiffractionSupportAnalysisError, match="camera_frame_extent"):
        analyze_diffraction_support(
            survey_h5,
            report_h5,
            tau_values=[1.0],
            support_radii=[100],
            center_profile=center_path,
        )


def test_peak_layout_records_center_relative_coordinates(tmp_path: Path) -> None:
    frame = np.full((32, 32), 0.01, dtype=np.float64)
    frame[10, 18] = 100.0
    survey_h5 = tmp_path / "survey.h5"
    layout_json = tmp_path / "peak_layout.json"
    _write_survey(survey_h5, frame)
    center_path = _center_profile(
        tmp_path,
        source_survey_h5=survey_h5,
        center_xy=(16.0, 8.0),
        frame_shape=(32, 32),
    )

    layout = derive_peak_layout_profile(
        survey_h5=survey_h5,
        output_json=layout_json,
        patch_shape_hw=(5, 5),
        threshold_sigma=1.0,
        min_area=1,
        center_profile=center_path,
    )

    assert layout.center_profile_id == "center_profile_v1"
    assert layout.energy_center_xy == [16.0, 8.0]
    assert layout.center_xy_rel is not None
    assert np.allclose(
        np.asarray(layout.center_xy_rel[0]),
        np.asarray(layout.center_xy[0]) - np.asarray([16.0, 8.0]),
    )


def test_sensor_energy_center_profile_introduces_no_roi_artifact_contract(tmp_path: Path) -> None:
    survey_h5 = tmp_path / "survey.h5"
    output_json = tmp_path / "sensor_energy_center_profile.json"
    _write_survey(survey_h5, _gaussian_frame((32, 32), center_xy=(12.0, 14.0)))

    profile = derive_sensor_energy_center_profile(survey_h5, output_json)
    text = profile.to_json()
    keys = set()

    def collect_keys(value):
        if isinstance(value, dict):
            for key, item in value.items():
                keys.add(str(key))
                collect_keys(item)
        elif isinstance(value, list):
            for item in value:
                collect_keys(item)

    collect_keys(json.loads(text))
    assert not any(key.startswith("roi") for key in keys)
    assert "roi_" not in output_json.name
