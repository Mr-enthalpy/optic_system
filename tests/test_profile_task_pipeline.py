from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pytest

from tasks.capture_forward_dataset import CaptureFrames
from tasks.profiles import (
    BROADBAND_PASSTHROUGH,
    PER_BAND_PUPIL_OPEN,
    BroadbandCameraCalibrationPlan,
    CameraProfile,
    ExposureCandidate,
    ExposureGainSearchConfig,
    ExposureSearchError,
    PerBandPupilOpenCalibrationPlan,
    PerBandCalibrationError,
    PupilProfile,
    PupilScanPlan,
    WavelengthCalibrationSpec,
    calibrate_broadband_camera_profile,
    calibrate_per_band_pupil_open_camera_profile,
    evaluate_gain_binary_search,
    run_broadband_pupil_scan,
)
from tasks.profiles.scan_pupil_broadband import _bar_starts
from tasks.profiles.scan_pupil_broadband import (
    estimate_ellipse_parameters,
    fit_radius_overlap_function,
)


@dataclass
class FakePassThroughTLS:
    pass_through_calls: int = 0
    wavelengths: list[float] = field(default_factory=list)
    current_wavelength_nm: float | None = None
    target_wavelength_nm: float | None = None
    grating: int | None = None

    def set_pass_through(self, timeout_s: float = 60.0):
        self.pass_through_calls += 1
        self.target_wavelength_nm = 0.0
        self.current_wavelength_nm = 0.0

    def set_grating(self, grating: int) -> None:
        self.grating = int(grating)

    def set_wavelength_nm(self, wavelength_nm: float):
        self.target_wavelength_nm = float(wavelength_nm)
        self.wavelengths.append(float(wavelength_nm))

    def move(self, timeout_s: float = 60.0):
        self.current_wavelength_nm = self.target_wavelength_nm

    def wait_until_idle(self, *, timeout_s: float = 60.0, **kwargs):
        self.current_wavelength_nm = self.target_wavelength_nm

    def get_status(self):
        return {
            "connected": True,
            "current_wavelength_nm": self.current_wavelength_nm,
            "target_wavelength_nm": self.target_wavelength_nm,
            "grating": self.grating,
            "moving": False,
        }


class SyntheticLCD:
    def __init__(self, shape: tuple[int, int] = (80, 120)):
        self._shape = shape
        self.last_mask = np.full(shape, 255, dtype=np.uint8)
        self.last_mask_id = None

    def show_physical_mask(self, mask: np.ndarray, *, mask_id: str | None = None) -> None:
        self.last_mask = np.asarray(mask, dtype=np.uint8)
        self.last_mask_id = mask_id

    def metadata(self) -> dict:
        h, w = self._shape
        return {
            "display_index": 1,
            "physical_shape": [h, w],
            "logical_shape": [h, w // 3],
            "subpixel_axis": 1,
        }

    def physical_shape(self) -> tuple[int, int]:
        return self._shape

    def subpixel_axis(self) -> int:
        return 1


class SyntheticCamera:
    def __init__(
        self,
        lcd: SyntheticLCD | None = None,
        *,
        frame_shape: tuple[int, int] = (24, 32),
        pupil_center: tuple[float, float] = (62.0, 37.0),
        pupil_radius: float = 18.0,
        pupil_axes: tuple[float, float] | None = None,
        full_scale: float = 255.0,
    ):
        self.lcd = lcd
        self.frame_shape = frame_shape
        self.exposure_us = 100.0
        self.gain_db = 0.0
        self.full_scale = full_scale
        self.applied_params: list[tuple[float | None, float | None]] = []
        self.acquire_counts: list[int] = []
        h, w = (lcd.physical_shape() if lcd is not None else (80, 120))
        yy, xx = np.mgrid[:h, :w]
        if pupil_axes is None:
            self.pupil = ((xx - pupil_center[0]) ** 2 + (yy - pupil_center[1]) ** 2 <= pupil_radius ** 2).astype(np.float64)
        else:
            a, b = (float(v) for v in pupil_axes)
            self.pupil = (
                ((xx - pupil_center[0]) ** 2) / max(a ** 2, 1e-12)
                + ((yy - pupil_center[1]) ** 2) / max(b ** 2, 1e-12)
                <= 1.0
            ).astype(np.float64)

    def apply_camera_params(self, exposure_us=None, gain_db=None):
        if exposure_us is not None:
            self.exposure_us = float(exposure_us)
        if gain_db is not None:
            self.gain_db = float(gain_db)
        self.applied_params.append((self.exposure_us, self.gain_db))

    def acquire_burst(self, k: int) -> CaptureFrames:
        self.acquire_counts.append(int(k))
        if self.lcd is None:
            visible_fraction = 1.0
        else:
            transmission = np.asarray(self.lcd.last_mask, dtype=np.float64) / 255.0
            visible_fraction = float(np.sum(transmission * self.pupil) / max(np.sum(self.pupil), 1.0))
        gain_factor = 10 ** (float(self.gain_db) / 20.0)
        signal = 3.0 + 0.018 * float(self.exposure_us) * gain_factor * visible_fraction
        frame = np.full(self.frame_shape, signal, dtype=np.float64)
        burst = np.repeat(frame[None, :, :], int(k), axis=0)
        return CaptureFrames(burst=burst, frames_avg=frame, metadata={"frame_dtype_full_scale": self.full_scale})


def _fast_search(*, min_exposure_us: float, max_exposure_us: float, gains_db: list[float] | None = None) -> ExposureGainSearchConfig:
    return ExposureGainSearchConfig(
        min_exposure_us=min_exposure_us,
        max_exposure_us=max_exposure_us,
        gains_db=gains_db or [0.0],
        iterations=4,
        safety_fraction=0.95,
        camera_param_settle_ms=0.0,
        discard_frames_after_param_change=0,
    )


def test_broadband_camera_calibration_uses_tls_pass_through() -> None:
    tls = FakePassThroughTLS()
    lcd = SyntheticLCD(shape=(80, 120))
    camera = SyntheticCamera(lcd=lcd)
    plan = BroadbandCameraCalibrationPlan(
        camera_profile_id="broadband_scan_safe_v1",
        candidates=[
            ExposureCandidate(exposure_us=200.0, gain_db=0.0),
            ExposureCandidate(exposure_us=6000.0, gain_db=0.0),
            ExposureCandidate(exposure_us=1200.0, gain_db=0.0),
        ],
        exposure_search=_fast_search(min_exposure_us=200.0, max_exposure_us=6000.0),
        frames_per_capture=2,
        full_scale=255.0,
        lcd_settle_ms=0.0,
        allow_test_lcd_settle_below_refresh=True,
    )

    result = calibrate_broadband_camera_profile(plan, camera=camera, lcd=lcd, tls=tls)

    assert tls.pass_through_calls == 1
    assert lcd.last_mask_id == "broadband_camera_calibration_all_transmissive"
    assert np.all(lcd.last_mask == 255)
    profile = result.camera_profile
    assert profile.profile_family == BROADBAND_PASSTHROUGH
    assert profile.illumination.tls_setpoint_nm == 0.0
    assert profile.illumination.wavelengths_nm == []
    assert profile.lcd_state["mode"] == "all_transmissive"
    assert profile.lcd_state["asserted_by_task"] is True
    assert profile.exposure_us is not None
    assert profile.extra["default_selection_policy"] == "low_gain_then_strong_signal_then_long_exposure"
    assert profile.extra["safe_profiles_by_gain"][0]["gain_db"] == 0.0
    assert profile.extra["timing_policy"]["allow_test_lcd_settle_below_refresh"] is True


def test_gain_binary_search_discards_frames_after_each_param_change() -> None:
    camera = SyntheticCamera(lcd=None)

    rows = evaluate_gain_binary_search(
        camera,
        ExposureGainSearchConfig(
            min_exposure_us=200.0,
            max_exposure_us=12000.0,
            gains_db=[0.0, 6.0],
            iterations=3,
            safety_fraction=0.95,
            camera_param_settle_ms=0.0,
            discard_frames_after_param_change=41,
        ),
        frames_per_capture=2,
        full_scale=255.0,
    )

    assert {row.gain_db for row in rows} == {0.0, 6.0}
    assert all(row.metadata["gain_search_method"] == "gain_outer_binary_exposure_inner" for row in rows)
    assert all(row.metadata["search_method"] == "binary" for row in rows)
    assert camera.acquire_counts[0::2] == [41] * len(rows)
    assert camera.acquire_counts[1::2] == [2] * len(rows)


def test_gain_binary_search_records_sorted_order_and_upper_bound_policy() -> None:
    camera = SyntheticCamera(lcd=None)

    rows = evaluate_gain_binary_search(
        camera,
        ExposureGainSearchConfig(
            min_exposure_us=100.0,
            max_exposure_us=1000.0,
            gains_db=[6.0, 0.0],
            iterations=3,
            safety_fraction=0.95,
            camera_param_settle_ms=0.0,
            discard_frames_after_param_change=0,
        ),
        frames_per_capture=2,
        full_scale=255.0,
    )

    assert [params[1] for params in camera.applied_params] == [0.0, 0.0, 6.0, 6.0]
    assert all(row.metadata["configured_gains_db"] == [6.0, 0.0] for row in rows)
    assert all(row.metadata["sorted_gains_db"] == [0.0, 6.0] for row in rows)
    assert all(row.metadata["gain_iteration_order"] == "ascending" for row in rows)
    assert all(
        row.metadata["binary_search_termination"] == "max_exposure_safe_no_extrapolation"
        for row in rows
    )
    assert all(row.metadata["max_exposure_source"] == "config_expected_camera_api_upper_bound" for row in rows)


def test_gain_binary_search_stops_only_on_lower_bound_unsafe() -> None:
    camera = SyntheticCamera(lcd=None)

    rows = evaluate_gain_binary_search(
        camera,
        ExposureGainSearchConfig(
            min_exposure_us=1000.0,
            max_exposure_us=1000.0,
            gains_db=[0.0, 40.0, 50.0],
            iterations=3,
            safety_fraction=0.95,
            camera_param_settle_ms=0.0,
            discard_frames_after_param_change=0,
        ),
        frames_per_capture=2,
        full_scale=255.0,
    )

    assert {row.gain_db for row in rows} == {0.0}
    assert all(row.metadata["gain_search_stopped_after_gain_db"] == 40.0 for row in rows)
    assert all(row.metadata["gain_search_stop_reason"] == "min_exposure_unsafe_at_higher_gain" for row in rows)


def test_gain_binary_search_does_not_swallow_non_lower_bound_errors() -> None:
    class BadShapeOnSecondGainCamera(SyntheticCamera):
        def acquire_burst(self, k: int) -> CaptureFrames:
            if self.gain_db >= 6.0:
                burst = np.zeros((int(k), 4, 4), dtype=np.float64)
                return CaptureFrames(
                    burst=burst,
                    frames_avg=np.zeros((3, 3), dtype=np.float64),
                    metadata={},
                )
            return super().acquire_burst(k)

    camera = BadShapeOnSecondGainCamera(lcd=None)

    with pytest.raises(ExposureSearchError, match="avg_frame shape"):
        evaluate_gain_binary_search(
            camera,
            ExposureGainSearchConfig(
                min_exposure_us=100.0,
                max_exposure_us=1000.0,
                gains_db=[0.0, 6.0],
                iterations=3,
                safety_fraction=0.95,
                camera_param_settle_ms=0.0,
                discard_frames_after_param_change=0,
            ),
            frames_per_capture=2,
            full_scale=255.0,
        )


def test_broadband_pupil_scan_outputs_pupil_profile() -> None:
    lcd = SyntheticLCD(shape=(80, 120))
    camera = SyntheticCamera(lcd, pupil_center=(62.0, 37.0), pupil_axes=(24.0, 16.0))
    camera_profile = CameraProfile.from_dict({
        "camera_profile_id": "broadband_scan_safe_v1",
        "profile_family": BROADBAND_PASSTHROUGH,
        "illumination": {
            "mode": BROADBAND_PASSTHROUGH,
            "tls_setpoint_nm": 0,
            "effective_wavelength_nm": None,
        },
        "lcd_state": {"mode": "all_transmissive"},
        "camera": {"exposure_us": 1200.0, "gain_db": 0.0},
        "valid_for": ["pupil_scan_broadband"],
    })
    plan = PupilScanPlan(
        pupil_profile_id="pupil_profile_scan_v1",
        camera_profile_id="broadband_scan_safe_v1",
        physical_shape=(80, 120),
        lcd_display_index=1,
        subpixel_axis=1,
        frames_per_capture=2,
        lcd_settle_ms=0.0,
        allow_test_lcd_settle_below_refresh=True,
        bar_width=6,
        scan_step=4,
        radius_scan_steps=40,
        radius_factor=0.9,
    )
    tls = FakePassThroughTLS()

    report = run_broadband_pupil_scan(plan, camera_profile=camera_profile, camera=camera, lcd=lcd, tls=tls)

    assert tls.pass_through_calls == 1
    pupil = report.pupil_profile
    assert pupil.pupil_profile_id == "pupil_profile_scan_v1"
    assert abs(pupil.lcd_physical_center[0] - 62.0) < 5.0
    assert abs(pupil.lcd_physical_center[1] - 37.0) < 5.0
    assert pupil.lcd_physical_radius is not None
    assert abs(pupil.lcd_physical_radius - 0.9 * report.fit_quality["ellipse_semi_minor"]) < 1e-9
    assert report.fit_quality["ellipse_semi_major"] >= report.fit_quality["ellipse_semi_minor"] > 0.0
    assert len(report.radii) == 40
    assert pupil.extra["illumination_mode"] == BROADBAND_PASSTHROUGH


def test_ellipse_overlap_fit_recovers_synthetic_axes() -> None:
    radii = np.linspace(0.0, 80.0, 120)
    energies = fit_radius_overlap_function(radii, scale=0.04, semi_major=48.0, semi_minor=28.0)

    fit = estimate_ellipse_parameters(energies, radii)

    assert abs(fit.semi_major - 48.0) < 2.0
    assert abs(fit.semi_minor - 28.0) < 2.0
    assert fit.r_squared > 0.99
    assert fit.pearson > 0.99


def test_pupil_scan_range_xyxy_uses_x0_y0_x1_y1_order() -> None:
    plan = PupilScanPlan(
        pupil_profile_id="pupil_profile_scan_v1",
        camera_profile_id="broadband_scan_safe_v1",
        physical_shape=(80, 120),
        lcd_display_index=1,
        subpixel_axis=1,
        bar_width=6,
        scan_step=20,
        scan_range_xyxy=(10, 20, 90, 70),
        lcd_settle_ms=0.0,
        allow_test_lcd_settle_below_refresh=True,
    )

    assert _bar_starts("x", plan) == [10, 30, 50, 70]
    assert _bar_starts("y", plan) == [20, 40, 60]


def test_per_band_pupil_open_calibration_outputs_profile() -> None:
    lcd = SyntheticLCD(shape=(80, 120))
    camera = SyntheticCamera(lcd)
    tls = FakePassThroughTLS()
    pupil = PupilProfile.from_dict({
        "pupil_profile_id": "pupil_profile_scan_v1",
        "lcd_coordinate_convention": "physical_mono_xy",
        "lcd_display_index": 1,
        "subpixel_axis": 1,
        "lcd_physical_center": [62.0, 37.0],
        "lcd_physical_radius": 16.0,
        "aperture_window": [46, 21, 32, 32],
        "extra": {"physical_shape": [80, 120]},
    })
    plan = PerBandPupilOpenCalibrationPlan.from_dict({
        "camera_profile_id": "per_band_pupil_open_v1",
        "pupil_profile_id": "pupil_profile_scan_v1",
        "frames_per_capture": 2,
        "full_scale": 255,
        "lcd_settle_ms": 0,
        "allow_test_lcd_settle_below_refresh": True,
        "wavelengths": [
            {
                "wavelength_nm": 450,
                "candidates": [{"exposure_us": 600}, {"exposure_us": 1200}],
                "exposure_search": {
                    "min_exposure_us": 600,
                    "max_exposure_us": 1200,
                    "gains_db": [0.0],
                    "iterations": 4,
                    "camera_param_settle_ms": 0,
                    "discard_frames_after_param_change": 0,
                },
            },
            {
                "wavelength_nm": 550,
                "candidates": [{"exposure_us": 500}, {"exposure_us": 1000}],
                "exposure_search": {
                    "min_exposure_us": 500,
                    "max_exposure_us": 1000,
                    "gains_db": [0.0],
                    "iterations": 4,
                    "camera_param_settle_ms": 0,
                    "discard_frames_after_param_change": 0,
                },
            },
        ],
    })

    result = calibrate_per_band_pupil_open_camera_profile(
        plan,
        pupil_profile=pupil,
        camera=camera,
        lcd=lcd,
        tls=tls,
    )

    profile = result.camera_profile
    assert profile.profile_family == PER_BAND_PUPIL_OPEN
    assert profile.depends_on_pupil_profile_id == "pupil_profile_scan_v1"
    assert set(profile.per_wavelength) == {"450", "550"}
    assert tls.wavelengths == [450.0, 550.0]
    assert len(tls.wavelengths) == len(plan.wavelengths)
    assert lcd.last_mask_id == "selected_pupil_open:pupil_profile_scan_v1"
    assert profile.extra["default_selection_policy"] == "low_gain_then_strong_signal_then_long_exposure"
    assert set(profile.extra["safe_profiles_by_wavelength"]) == {"450", "550"}
    assert profile.extra["safe_profiles_by_wavelength"]["450"][0]["gain_db"] == 0.0
    assert profile.extra["timing_policy"]["allow_test_lcd_settle_below_refresh"] is True


def test_profile_scan_stages_resume_from_saved_artifacts(tmp_path: Path) -> None:
    broadband_lcd = SyntheticLCD(shape=(80, 120))
    broadband_camera = SyntheticCamera(broadband_lcd)
    broadband_tls = FakePassThroughTLS()
    broadband_result = calibrate_broadband_camera_profile(
        BroadbandCameraCalibrationPlan(
            camera_profile_id="broadband_scan_safe_v1",
            candidates=[
                ExposureCandidate(exposure_us=1200.0),
                ExposureCandidate(exposure_us=6000.0),
            ],
            exposure_search=_fast_search(min_exposure_us=1200.0, max_exposure_us=6000.0),
            frames_per_capture=2,
            lcd_settle_ms=0.0,
            allow_test_lcd_settle_below_refresh=True,
        ),
        camera=broadband_camera,
        lcd=broadband_lcd,
        tls=broadband_tls,
    )
    broadband_result.write_json(tmp_path / "broadband_result.json")
    broadband_profile_path = tmp_path / "broadband_camera_profile.json"
    broadband_result.camera_profile.to_json(broadband_profile_path)

    scan_lcd = SyntheticLCD(shape=(80, 120))
    scan_camera = SyntheticCamera(scan_lcd, pupil_center=(62.0, 37.0), pupil_axes=(24.0, 16.0))
    loaded_broadband_profile = CameraProfile.load_json(broadband_profile_path)
    scan_report = run_broadband_pupil_scan(
        PupilScanPlan(
            pupil_profile_id="pupil_profile_scan_v1",
            camera_profile_id="broadband_scan_safe_v1",
            physical_shape=(80, 120),
            lcd_display_index=1,
            subpixel_axis=1,
            frames_per_capture=2,
            lcd_settle_ms=0.0,
            allow_test_lcd_settle_below_refresh=True,
            bar_width=6,
            scan_step=4,
            radius_scan_steps=40,
            radius_factor=0.9,
        ),
        camera_profile=loaded_broadband_profile,
        camera=scan_camera,
        lcd=scan_lcd,
        tls=None,
    )
    scan_report.write_json(tmp_path / "pupil_scan_report.json")
    pupil_profile_path = tmp_path / "pupil_profile.json"
    scan_report.pupil_profile.to_json(pupil_profile_path)

    per_band_lcd = SyntheticLCD(shape=(80, 120))
    per_band_camera = SyntheticCamera(per_band_lcd)
    per_band_tls = FakePassThroughTLS()
    loaded_pupil_profile = PupilProfile.load_json(pupil_profile_path)
    per_band_result = calibrate_per_band_pupil_open_camera_profile(
        PerBandPupilOpenCalibrationPlan.from_dict({
            "camera_profile_id": "per_band_pupil_open_v1",
            "pupil_profile_id": "pupil_profile_scan_v1",
            "frames_per_capture": 2,
            "full_scale": 255,
            "lcd_settle_ms": 0,
            "allow_test_lcd_settle_below_refresh": True,
            "wavelengths": [
                {
                    "wavelength_nm": 550,
                    "candidates": [{"exposure_us": 500}, {"exposure_us": 1000}],
                    "exposure_search": {
                        "min_exposure_us": 500,
                        "max_exposure_us": 1000,
                        "gains_db": [0.0],
                        "iterations": 4,
                        "camera_param_settle_ms": 0,
                        "discard_frames_after_param_change": 0,
                    },
                },
            ],
        }),
        pupil_profile=loaded_pupil_profile,
        camera=per_band_camera,
        lcd=per_band_lcd,
        tls=per_band_tls,
    )
    per_band_result.write_json(tmp_path / "per_band_result.json")

    assert broadband_tls.pass_through_calls == 1
    assert per_band_tls.pass_through_calls == 0
    assert per_band_tls.wavelengths == [550.0]
    assert per_band_result.camera_profile.depends_on_pupil_profile_id == "pupil_profile_scan_v1"
    assert (tmp_path / "broadband_result.json").exists()
    assert (tmp_path / "pupil_scan_report.json").exists()
    assert (tmp_path / "per_band_result.json").exists()


def test_per_band_calibration_rejects_zero_wavelength() -> None:
    lcd = SyntheticLCD(shape=(80, 120))
    camera = SyntheticCamera(lcd)
    pupil = PupilProfile.from_dict({
        "pupil_profile_id": "pupil_profile_scan_v1",
        "lcd_coordinate_convention": "physical_mono_xy",
        "lcd_display_index": 1,
        "subpixel_axis": 1,
        "lcd_physical_center": [62.0, 37.0],
        "lcd_physical_radius": 16.0,
        "extra": {"physical_shape": [80, 120]},
    })
    plan = PerBandPupilOpenCalibrationPlan(
        camera_profile_id="per_band_pupil_open_v1",
        pupil_profile_id="pupil_profile_scan_v1",
        wavelengths=[
            WavelengthCalibrationSpec(
                wavelength_nm=0.0,
                candidates=[ExposureCandidate(exposure_us=1000.0)],
            )
        ],
    )

    with pytest.raises(PerBandCalibrationError, match="positive"):
        calibrate_per_band_pupil_open_camera_profile(
            plan,
            pupil_profile=pupil,
            camera=camera,
            lcd=lcd,
            tls=None,
        )
