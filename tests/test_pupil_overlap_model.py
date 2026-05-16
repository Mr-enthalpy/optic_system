from __future__ import annotations

import numpy as np

from tasks.pupil_geometry_model import (
    create_circular_window_mask,
    ellipse_circle_overlap_area,
    estimate_ellipse_parameters,
    fit_function,
    solve_aperture_from_profiles,
)


def test_overlap_model_piecewise_limits() -> None:
    r = np.array([10.0, 50.0, 100.0])
    area = ellipse_circle_overlap_area(r, a=80.0, b=30.0)

    assert np.isclose(area[0], np.pi * 10.0**2)
    assert np.isclose(area[2], np.pi * 80.0 * 30.0)
    assert area[0] < area[1] < area[2]


def test_ellipse_fit_recovers_synthetic_axes() -> None:
    r = np.linspace(0.0, 120.0, 180)
    y = fit_function(r, k=0.03, a=80.0, b=45.0)

    fit = estimate_ellipse_parameters(y, r)

    assert abs(fit.a - 80.0) < 2.0
    assert abs(fit.b - 45.0) < 2.0
    assert abs(fit.k - 0.03) < 0.002
    assert fit.r_squared > 0.99
    assert fit.pearson > 0.99


def test_circle_profile_fit_recovers_center_and_radius() -> None:
    x = np.linspace(20.0, 180.0, 120)
    y = np.linspace(10.0, 130.0, 110)
    xc, yc, rx, ry = 100.0, 70.0, 55.0, 45.0
    ex = np.sqrt(np.clip(rx**2 - (x - xc) ** 2, 0.0, None))
    ey = np.sqrt(np.clip(ry**2 - (y - yc) ** 2, 0.0, None))

    result = solve_aperture_from_profiles(x, ex, y, ey, smooth_k=1, use_top=0.6)

    assert abs(result["xc"] - xc) < 1.0
    assert abs(result["yc"] - yc) < 1.0
    assert abs(result["r_x"] - rx) < 1.0
    assert abs(result["r_y"] - ry) < 1.0


def test_effective_radius_uses_factor_of_b() -> None:
    b = 45.0
    factor = 0.9
    radius = factor * b
    mask = create_circular_window_mask(
        physical_shape=(100, 120),
        center=(60.0, 50.0),
        radius=radius,
    )

    assert radius == 40.5
    assert mask.shape == (100, 120)
    assert mask[50, 60] == 1
