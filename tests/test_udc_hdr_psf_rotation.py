"""Hardware-free checks for the B2 azimuthal analysis."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "scripts" / "udc_hdr_psf"
sys.path.insert(0, str(SCRIPTS))
import rotation_analysis as ra  # noqa: E402

AZ = np.deg2rad(np.arange(0.0, 360.0, 2.0))
RADII = np.array([4.0, 6.0, 8.0, 10.0, 12.0])


def _profile(theta0_deg, modulation=0.25):
    theta0 = np.deg2rad(theta0_deg)
    return (1.0 + modulation * np.cos(4.0 * (AZ - theta0)))[None, :]


def _image(theta0_deg, center_xy=(60.3, 59.6), shape=(121, 121),
           sigma=6.0, modulation=0.25):
    yy, xx = np.mgrid[0:shape[0], 0:shape[1]]
    cx, cy = center_xy
    radius = np.hypot(xx - cx, yy - cy)
    theta = np.arctan2(yy - cy, xx - cx)
    theta0 = np.deg2rad(theta0_deg)
    return 100.0 * np.exp(-radius ** 2 / (2 * sigma ** 2)) * (
        1.0 + modulation * np.cos(4.0 * (theta - theta0)))


def test_subpixel_peak_recovers_a_fractional_center():
    image = _image(0.0, center_xy=(60.3, 59.6), modulation=0.0, sigma=2.5)
    found = ra.subpixel_peak(image)
    assert found["ok"]
    assert abs(found["center_xy"][0] - 60.3) < 0.25
    assert abs(found["center_xy"][1] - 59.6) < 0.25


def test_rotation_shift_has_the_expected_sign():
    a = _profile(0.0)
    b = _profile(20.0)
    found = ra.angular_shift(a, b, AZ, max_deg=45.0, min_corr=0.2, min_rings=1)
    assert found["ok"]
    # b is a rotated by +20 degrees; the shift applied to b is -20 degrees.
    assert abs(found["shift_deg"] + 20.0) < 1.5


def test_ring_maximum_tracks_the_samples_the_profile_uses():
    # A spot with one bright pixel at the centre and a smooth ring: the ring
    # maximum must follow the ring, not the core, because the ceiling check in
    # the session analysis compares it with the ADC ceiling.
    image = _image(0.0, modulation=0.0, sigma=6.0)
    image[60, 60] = 900.0
    polar = ra.polar_modulation_plane(image, (60.3, 59.6), RADII, AZ)
    assert polar["ring_max_dn"][0] < 200.0
    assert polar["ring_max_dn"][0] > polar["ring_max_dn"][-1]
    assert polar["ring_mean_dn"][0] < polar["ring_max_dn"][0]


def test_translation_does_not_change_the_angular_shift():
    a = _image(0.0)
    b = _image(20.0, center_xy=(61.7, 58.9))
    pa = ra.polar_modulation_plane(a, (60.3, 59.6), RADII, AZ)
    pb = ra.polar_modulation_plane(b, (61.7, 58.9), RADII, AZ)
    found = ra.angular_shift(pa["modulation"], pb["modulation"], AZ,
                             valid_a=pa["valid"], valid_b=pb["valid"],
                             max_deg=45.0, min_corr=0.2, min_rings=1)
    assert found["ok"]
    assert abs(found["shift_deg"] + 20.0) < 2.0


def test_fourfold_wrap_keeps_the_reported_shift_inside_the_search():
    a = _profile(0.0)
    b = _profile(70.0)  # 70 degrees is -20 degrees modulo the 90-degree pattern
    found = ra.angular_shift(a, b, AZ, max_deg=45.0, min_corr=0.2, min_rings=1)
    assert found["ok"]
    assert abs(found["shift_deg"] - 20.0) < 1.5


def test_a_flat_profile_is_not_a_measurement():
    a = _profile(0.0, modulation=0.0)
    b = _profile(20.0, modulation=0.0)
    found = ra.angular_shift(a, b, AZ, max_deg=45.0, min_corr=0.2, min_rings=1)
    assert not found["ok"]


def test_rotation_fit_separates_the_two_hypotheses():
    mount = [{"delta_roll_deg": 20.0, "angular_shift_deg": -20.0},
             {"delta_roll_deg": -20.0, "angular_shift_deg": 20.0},
             {"delta_roll_deg": 1.0, "angular_shift_deg": -1.0}]
    found = ra.fit_rotation(mount)
    assert found["ok"]
    assert abs(found["slope"] + 1.0) < 0.05
    assert ra.classify_slope(found["slope"]) == "mount_consistent"

    display = [{"delta_roll_deg": 20.0, "angular_shift_deg": 0.2},
               {"delta_roll_deg": -20.0, "angular_shift_deg": -0.1},
               {"delta_roll_deg": 1.0, "angular_shift_deg": 0.0}]
    found = ra.fit_rotation(display)
    assert found["ok"]
    assert ra.classify_slope(found["slope"]) == "display_consistent"
