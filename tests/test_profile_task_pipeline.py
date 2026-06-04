from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pytest

from tasks.capture_forward_dataset import CaptureFrames
from tasks.profiles import (
    BROADBAND_PASSTHROUGH,
    PER_BAND_PUPIL_OPEN,
    BroadbandCameraCalibrationPlan,
    CameraProfile,
    ExposureCandidate,
    PerBandPupilOpenCalibrationPlan,
    PerBandCalibrationError,
    PupilProfile,
    PupilScanPlan,
    WavelengthCalibrationSpec,
    calibrate_broadband_camera_profile,
    calibrate_per_band_pupil_open_camera_profile,
    run_broadband_pupil_scan,
)
from tasks.profiles.scan_pupil_broadband import _bar_starts


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
        full_scale: float = 255.0,
    ):
        self.lcd = lcd
        self.frame_shape = frame_shape
        self.exposure_us = 100.0
        self.gain_db = 0.0
        self.full_scale = full_scale
        h, w = (lcd.physical_shape() if lcd is not None else (80, 120))
        yy, xx = np.mgrid[:h, :w]
        self.pupil = ((xx - pupil_center[0]) ** 2 + (yy - pupil_center[1]) ** 2 <= pupil_radius ** 2).astype(np.float64)

    def apply_camera_params(self, exposure_us=None, gain_db=None):
        if exposure_us is not None:
            self.exposure_us = float(exposure_us)
        if gain_db is not None:
            self.gain_db = float(gain_db)

    def acquire_burst(self, k: int) -> CaptureFrames:
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


def test_broadband_camera_calibration_uses_tls_pass_through() -> None:
    tls = FakePassThroughTLS()
    camera = SyntheticCamera(lcd=None)
    plan = BroadbandCameraCalibrationPlan(
        camera_profile_id="broadband_scan_safe_v1",
        candidates=[
            ExposureCandidate(exposure_us=200.0, gain_db=0.0),
            ExposureCandidate(exposure_us=6000.0, gain_db=0.0),
            ExposureCandidate(exposure_us=1200.0, gain_db=0.0),
        ],
        frames_per_capture=2,
        full_scale=255.0,
    )

    result = calibrate_broadband_camera_profile(plan, camera=camera, tls=tls)

    assert tls.pass_through_calls == 1
    profile = result.camera_profile
    assert profile.profile_family == BROADBAND_PASSTHROUGH
    assert profile.illumination.tls_setpoint_nm == 0.0
    assert profile.illumination.wavelengths_nm == []
    assert profile.exposure_us is not None


def test_broadband_pupil_scan_outputs_pupil_profile() -> None:
    lcd = SyntheticLCD(shape=(80, 120))
    camera = SyntheticCamera(lcd, pupil_center=(62.0, 37.0), pupil_radius=18.0)
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
        bar_width=6,
        scan_step=4,
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
    assert pupil.extra["illumination_mode"] == BROADBAND_PASSTHROUGH


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
        "wavelengths": [
            {
                "wavelength_nm": 450,
                "candidates": [{"exposure_us": 600}, {"exposure_us": 1200}],
            },
            {
                "wavelength_nm": 550,
                "candidates": [{"exposure_us": 500}, {"exposure_us": 1000}],
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
    assert lcd.last_mask_id == "selected_pupil_open:pupil_profile_scan_v1"


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
