"""Azimuthal analysis for the B2 rotation session.

The B1 sweep measured radial shape and plane ratios.  B2 asks a different
question: does a structure in the PSF keep its azimuth in the sensor frame when
the phone is rolled, or does it stay fixed in the laboratory frame?

Two conventions matter here.

1. Coordinates are ``(x, y)`` in binned pixels, matching ``peak_position`` in
   the B1 driver.  Arrays are indexed ``[y, x]``.
2. ``angular_shift`` returns the shift that has to be applied to profile B to
   match profile A, i.e. ``theta0_a - theta0_b`` in the azimuth grid.  A
   structure fixed to the display therefore gives 0; a structure fixed to the
   pinhole/mount gives a slope of magnitude 1 against the measured roll.  The
   *sign* of that slope depends on how the accelerometer defines a positive
   roll, so :func:`classify_slope` judges the magnitude and the report states
   the sign the session actually saw.

The functions are deliberately small and numpy-only.  They do not know about
capture plans, phone runs or the session record.
"""
from __future__ import annotations

import numpy as np

# Quad-CFA BGGR on the binned grid: plane name -> (y parity, x parity).
PLANES = {"B": (0, 0), "Gb": (1, 0), "Gr": (0, 1), "R": (1, 1)}


def wrap_deg(angle):
    """Signed angle in (-180, 180]."""
    return (float(angle) + 180.0) % 360.0 - 180.0


def plane_image(image, plane):
    """Return one CFA plane and its parity offsets ``(py, px)``."""
    try:
        py, px = PLANES[plane]
    except KeyError as exc:
        raise ValueError(f"unknown plane {plane!r}; expected one of {sorted(PLANES)}") from exc
    return np.asarray(image)[py::2, px::2], py, px


def _bilinear(image, yy, xx):
    """Bilinear samples of a 2-D array at float coordinates; NaN outside."""
    image = np.asarray(image, dtype=float)
    height, width = image.shape
    y0 = np.floor(yy).astype(int)
    x0 = np.floor(xx).astype(int)
    y1, x1 = y0 + 1, x0 + 1
    inside = (y0 >= 0) & (x0 >= 0) & (y1 < height) & (x1 < width)
    out = np.full(np.broadcast(yy, xx).shape, np.nan, dtype=float)
    if not np.any(inside):
        return out
    wy = yy - y0
    wx = xx - x0
    out[inside] = (
        image[y0[inside], x0[inside]] * (1 - wy[inside]) * (1 - wx[inside])
        + image[y0[inside], x1[inside]] * (1 - wy[inside]) * wx[inside]
        + image[y1[inside], x0[inside]] * wy[inside] * (1 - wx[inside])
        + image[y1[inside], x1[inside]] * wy[inside] * wx[inside]
    )
    return out


def subpixel_peak(image, peak_xy=None, half=2, max_offset=0.75):
    """Refine a peak with a local quadratic fit.

    A 2x2-binned peak is an integer pixel.  One pixel of centre error is 18
    degrees at r=3 px, so the integer peak is an anchor, not a final centre.
    The fit uses a ``(2*half+1)^2`` patch and returns the vertex of the fitted
    quadratic.  It refuses, rather than extrapolating, when the curvature has
    the wrong sign or the vertex is more than ``max_offset`` from the anchor.
    """
    image = np.asarray(image, dtype=float)
    if peak_xy is None:
        y, x = np.unravel_index(int(np.argmax(image)), image.shape)
    else:
        x, y = int(peak_xy[0]), int(peak_xy[1])
    height, width = image.shape
    if not (half <= x < width - half and half <= y < height - half):
        return {"peak_xy": (x, y), "center_xy": (float(x), float(y)),
                "offset_xy": (0.0, 0.0), "ok": False,
                "reason": "peak too close to the image edge"}

    dy, dx = np.mgrid[-half:half + 1, -half:half + 1]
    patch = image[y - half:y + half + 1, x - half:x + half + 1]
    finite = np.isfinite(patch)
    if finite.sum() < 10:
        return {"peak_xy": (x, y), "center_xy": (float(x), float(y)),
                "offset_xy": (0.0, 0.0), "ok": False,
                "reason": "too few finite pixels around the peak"}

    columns = np.column_stack([
        np.ones(finite.sum()),
        dx[finite], dy[finite],
        (dx ** 2)[finite], (dx * dy)[finite], (dy ** 2)[finite],
    ])
    coef, *_ = np.linalg.lstsq(columns, patch[finite], rcond=None)
    _, gx, gy, axx, axy, ayy = coef
    hessian = np.array([[2 * axx, axy], [axy, 2 * ayy]], dtype=float)
    determinant = float(np.linalg.det(hessian))
    if determinant <= 0 or axx >= 0 or ayy >= 0:
        return {"peak_xy": (x, y), "center_xy": (float(x), float(y)),
                "offset_xy": (0.0, 0.0), "ok": False,
                "reason": "the fitted patch is not a local maximum"}

    offset = -0.5 * np.linalg.solve(hessian, np.array([gx, gy], dtype=float))
    if not np.all(np.isfinite(offset)) or np.max(np.abs(offset)) > max_offset:
        return {"peak_xy": (x, y), "center_xy": (float(x), float(y)),
                "offset_xy": (0.0, 0.0), "ok": False,
                "reason": "the fitted vertex is outside the allowed offset"}

    return {"peak_xy": (x, y), "center_xy": (x + float(offset[0]), y + float(offset[1])),
            "offset_xy": (float(offset[0]), float(offset[1])), "ok": True,
            "reason": "quadratic vertex"}


def core_centroid(image, center_xy, radius=3.0, black=0.0):
    """Intensity centroid inside a small core, used as a cross-check only.

    A centroid over a large aperture is pulled by the asymmetric wings.  The
    small-core version stays near the peak and gives a model-free second
    opinion on the sub-pixel position.
    """
    image = np.asarray(image, dtype=float)
    cx, cy = float(center_xy[0]), float(center_xy[1])
    yy, xx = np.mgrid[0:image.shape[0], 0:image.shape[1]]
    mask = np.hypot(yy - cy, xx - cx) <= radius
    weights = np.where(mask, np.maximum(image - float(black), 0.0), 0.0)
    total = float(weights.sum())
    if total <= 0:
        return {"center_xy": (cx, cy), "ok": False, "reason": "no positive core flux"}
    return {
        "center_xy": (float((weights * xx).sum() / total),
                      float((weights * yy).sum() / total)),
        "ok": True,
        "reason": "small-core centroid",
        "total_dn": total,
    }


def refine_center(image, peak_xy=None, black=0.0, half=2, centroid_radius=5.0,
                  iterations=20, tolerance=1e-4, max_drift=2.0):
    """Sub-pixel centre: integer peak, then an iterated full-disc centroid.

    The intensity maximum is *not* the centre of symmetry when the PSF carries a
    four-fold pattern: with 25 % azimuthal modulation the brightest pixel sits up
    to a pixel away from the geometric centre.  A local quadratic fit inherits
    that displacement, and one pixel is 18 degrees at r=3 px.  A centroid over a
    full disc averages ``cos(4 theta)`` to zero, so the modulation cancels and
    only the radial falloff sets the first moment.  The disc is iterated from the
    peak (the anchor) until it stops moving.

    The quadratic vertex is kept as a cross-check.  If the two disagree by more
    than ``max_drift`` the estimate is reported as unconverged, because that
    usually means a second lobe or a badly placed anchor rather than a genuine
    sub-pixel centre.
    """
    image = np.asarray(image, dtype=float)
    quadratic = subpixel_peak(image, peak_xy=peak_xy, half=half)
    anchor = (float(quadratic["center_xy"][0]), float(quadratic["center_xy"][1]))
    center = anchor
    trajectory = []
    converged = False
    for _ in range(int(iterations)):
        step = core_centroid(image, center, radius=centroid_radius, black=black)
        if not step.get("ok"):
            break
        new = (float(step["center_xy"][0]), float(step["center_xy"][1]))
        movement = float(np.hypot(new[0] - center[0], new[1] - center[1]))
        trajectory.append([new[0], new[1], movement])
        center = new
        if movement <= tolerance:
            converged = True
            break
    drift = float(np.hypot(center[0] - anchor[0], center[1] - anchor[1]))
    return {
        "center_xy": center,
        "anchor_xy": anchor,
        "anchor_source": "quadratic vertex" if quadratic["ok"] else "integer peak",
        "drift_from_anchor_px": drift,
        "converged": converged,
        "usable": bool(converged and drift <= max_drift),
        "centroid_radius_px": float(centroid_radius),
        "quadratic": quadratic,
        "trajectory": trajectory,
    }


def polar_modulation_plane(plane, center_plane_xy, radii, azimuths, black=0.0,
                           min_ring_dn=5.0):
    """Azimuthal modulation of one CFA plane on a polar grid.

    ``plane`` is already the sublattice (every other binned pixel).  Radial
    distances are given in *binned* pixels, so they are divided by two when
    converted to plane-pixel coordinates.  Each ring is divided by its own
    azimuthal mean; this removes the radial falloff and leaves the angular
    structure that the rotation test needs.
    """
    plane = np.asarray(plane, dtype=float)
    radii = np.asarray(radii, dtype=float)
    azimuths = np.asarray(azimuths, dtype=float)
    cx, cy = float(center_plane_xy[0]), float(center_plane_xy[1])
    rr = radii[:, None] / 2.0
    xx = cx + rr * np.cos(azimuths)[None, :]
    yy = cy + rr * np.sin(azimuths)[None, :]
    values = _bilinear(plane, yy, xx) - float(black)

    ring_mean = np.full(len(radii), np.nan, dtype=float)
    ring_max = np.full(len(radii), np.nan, dtype=float)
    valid = np.zeros(len(radii), dtype=bool)
    for index in range(len(radii)):
        ring = values[index]
        if np.all(np.isfinite(ring)):
            ring_mean[index] = float(ring.mean())
            ring_max[index] = float(ring.max())
            valid[index] = ring_mean[index] > float(min_ring_dn)
    modulation = np.full_like(values, np.nan)
    for index in np.flatnonzero(valid):
        modulation[index] = (values[index] - ring_mean[index]) / ring_mean[index]
    return {"modulation": modulation, "ring_mean_dn": ring_mean,
            "ring_max_dn": ring_max, "valid": valid, "radii_px": radii,
            "azimuths_rad": azimuths}


def polar_modulation(image, center_xy, plane, radii, azimuths, black=0.0,
                     min_ring_dn=5.0):
    """CFA-aware wrapper around :func:`polar_modulation_plane`."""
    plane_data, py, px = plane_image(image, plane)
    center_plane = ((float(center_xy[0]) - px) / 2.0,
                    (float(center_xy[1]) - py) / 2.0)
    result = polar_modulation_plane(plane_data, center_plane, radii, azimuths,
                                    black=black, min_ring_dn=min_ring_dn)
    result.update({"plane": plane, "parity": (py, px),
                   "center_plane_xy": center_plane})
    return result


def angular_shift(modulation_a, modulation_b, azimuths_rad, valid_a=None, valid_b=None,
                  max_deg=45.0, min_corr=0.2, min_rings=2):
    """Circular cross-correlation between two azimuthal profiles.

    Returns the shift applied to B that best matches A.  The search is limited
    to ``+/- max_deg``; with a four-fold PSF a rotation of 20 degrees is 80
    degrees of harmonic phase but only 20 degrees of pattern shift, and keeping
    the search below 45 degrees avoids the 90-degree alias.
    """
    a = np.asarray(modulation_a, dtype=float)
    b = np.asarray(modulation_b, dtype=float)
    azimuths = np.asarray(azimuths_rad, dtype=float)
    if a.shape != b.shape:
        raise ValueError(f"profiles differ in shape: {a.shape} vs {b.shape}")
    if a.shape[1] != len(azimuths):
        raise ValueError("azimuth grid does not match the profile width")
    if len(azimuths) < 3:
        raise ValueError("need at least three azimuth samples")
    step = float(np.median(np.diff(np.sort(azimuths))))
    if step <= 0:
        raise ValueError("azimuth grid is not increasing")
    bins = int(round(np.deg2rad(max_deg) / step))
    mask_a = np.ones_like(a, dtype=bool) if valid_a is None else np.asarray(valid_a, dtype=bool)[:, None]
    mask_b = np.ones_like(b, dtype=bool) if valid_b is None else np.asarray(valid_b, dtype=bool)[:, None]

    shifts = np.arange(-bins, bins + 1, dtype=int)
    correlations = np.full(len(shifts), np.nan, dtype=float)
    for index, shift in enumerate(shifts):
        rolled = np.roll(b, shift, axis=1)
        rolled_mask = np.roll(mask_b, shift, axis=1)
        mask = mask_a & rolled_mask & np.isfinite(a) & np.isfinite(rolled)
        if int((mask.sum(axis=1) >= 3).sum()) < min_rings:
            continue
        counts = np.maximum(mask.sum(axis=1, keepdims=True), 1)
        mean_a = np.where(mask, a, 0.0).sum(axis=1, keepdims=True) / counts
        mean_b = np.where(mask, rolled, 0.0).sum(axis=1, keepdims=True) / counts
        av = np.where(mask, a - mean_a, 0.0)
        bv = np.where(mask, rolled - mean_b, 0.0)
        denominator = float(np.sqrt((av * av).sum() * (bv * bv).sum()))
        if denominator > 0:
            correlations[index] = float((av * bv).sum() / denominator)

    if not np.any(np.isfinite(correlations)):
        return {"ok": False, "reason": "no finite correlation", "shift_deg": None,
                "peak_corr": None, "curve_shift_deg": [], "curve_corr": []}

    best = int(np.nanargmax(correlations))
    peak = float(correlations[best])
    refined = float(shifts[best])
    if 0 < best < len(shifts) - 1:
        y0, y1, y2 = correlations[best - 1], correlations[best], correlations[best + 1]
        denominator = y0 - 2 * y1 + y2
        if np.isfinite(denominator) and abs(denominator) > 1e-12:
            refined += float(np.clip(0.5 * (y0 - y2) / denominator, -0.5, 0.5))

    ok = bool(peak >= min_corr and np.isfinite(peak))
    return {
        "ok": ok,
        "reason": "cross-correlation peak" if ok else "correlation below threshold",
        "shift_deg": float(refined * np.rad2deg(step)),
        "peak_corr": peak,
        "curve_shift_deg": (shifts * np.rad2deg(step)).tolist(),
        "curve_corr": correlations.tolist(),
    }


def harmonic_coefficients(modulation, azimuths_rad, orders=range(2, 11)):
    """Complex amplitude of each azimuthal order, averaged over rings.

    ``modulation`` is ``[ring, azimuth]``, already normalised by each ring's own
    mean.  Rotating a pattern by ``theta0`` multiplies order ``k``'s coefficient
    by ``exp(-i k theta0)``, which is what makes the phases usable below.

    Rings are weighted by their own amplitude, so a flat ring (no pattern, only
    noise) does not dilute the phase, and the mean is taken on the complex
    coefficients rather than on their phases, so rings that disagree cancel
    instead of being selected from.
    """
    values = np.asarray(modulation, dtype=float)
    theta = np.asarray(azimuths_rad, dtype=float)
    if values.ndim != 2:
        raise ValueError("modulation must be [ring, azimuth]")
    if values.shape[1] != len(theta):
        raise ValueError("azimuth grid does not match the profile width")
    rows = values[np.isfinite(values).all(axis=1)]
    if rows.shape[0] == 0:
        return {}
    found = {}
    for order in orders:
        coeff = rows @ np.exp(-1j * int(order) * theta) / rows.shape[1]
        weight = np.abs(coeff)
        total = float(weight.sum())
        if total <= 0:
            continue
        found[int(order)] = complex(np.sum(coeff * weight) / total)
    return found


def fit_shift_from_harmonics(first, second, orders=None, search_deg=90.0,
                             step_deg=0.02):
    """Rotation that maps ``second`` onto ``first``, from several orders at once.

    A rigid rotation by ``D`` moves order ``k``'s phase by ``k D``.  Fitting one
    ``D`` to orders 2..10 jointly is why this differs from
    :func:`angular_shift`: a pattern with ``N`` periodic spike directions has
    correlation peaks every ``360/N`` degrees, so a single global maximum can sit
    on a neighbouring spike, while orders that are not multiples of ``N`` break
    that degeneracy.  ``phase_rms_deg`` is the scatter of the orders around the
    fitted rotation - a direct test of whether the change was a rigid rotation at
    all, rather than an assumption that it was.

    A pattern that really is a pure ``N``-fold comb stays ambiguous to
    ``360/N``; the fit then returns one of the equivalent answers and the
    scatter does not warn about it, because no order can tell them apart.
    """
    shared = sorted(set(first) & set(second))
    if orders is not None:
        keep = {int(order) for order in orders}
        shared = [order for order in shared if order in keep]
    weights = {k: abs(first[k]) * abs(second[k]) for k in shared}
    usable = [k for k in shared if weights[k] > 0]
    if len(usable) < 3:
        return {"ok": False, "reason": "need at least three shared orders",
                "shift_deg": None, "phase_rms_deg": None, "orders": shared}

    grid = np.arange(-float(search_deg), float(search_deg) + 1e-9, float(step_deg))
    cost = np.zeros_like(grid)
    norm = 0.0
    for k in usable:
        base = np.angle(second[k]) - np.angle(first[k])
        residual = np.angle(np.exp(1j * (base - np.deg2rad(k * grid))))
        cost += weights[k] * residual ** 2
        norm += weights[k]
    best = int(np.argmin(cost))
    return {"ok": True, "shift_deg": float(grid[best]),
            "phase_rms_deg": float(np.degrees(np.sqrt(cost[best] / norm))),
            "orders": usable}


def fit_rotation(pairs, weights=None):
    """Fit ``angular_shift = slope * delta_roll`` across measured pairs.

    ``pairs`` is an iterable of mappings with ``delta_roll_deg`` and
    ``angular_shift_deg``.  The slope is fitted through the origin because both
    hypotheses predict zero shift at zero rotation; an intercept is reported
    separately as a diagnostic for a constant estimator bias.
    """
    rows = list(pairs)
    x = np.array([float(row["delta_roll_deg"]) for row in rows], dtype=float)
    y = np.array([float(row["angular_shift_deg"]) for row in rows], dtype=float)
    if weights is None:
        w = np.ones_like(x)
    else:
        w = np.asarray(weights, dtype=float)
        if len(w) != len(x):
            raise ValueError("weights do not match pairs")
    finite = np.isfinite(x) & np.isfinite(y) & np.isfinite(w) & (w > 0)
    x, y, w = x[finite], y[finite], w[finite]
    if len(x) < 2 or float((w * x * x).sum()) <= 0:
        return {"ok": False, "reason": "need at least two non-zero rotations",
                "slope": None, "intercept": None, "rms_deg": None, "n": int(len(x))}
    slope = float((w * x * y).sum() / (w * x * x).sum())
    design = np.column_stack([x, np.ones_like(x)])
    weighted = design * np.sqrt(w)[:, None]
    coef, *_ = np.linalg.lstsq(weighted, y * np.sqrt(w), rcond=None)
    residual = y - (slope * x)
    rms = float(np.sqrt((w * residual * residual).sum() / w.sum()))
    return {"ok": True, "slope": slope, "intercept": float(coef[1]),
            "rms_deg": rms, "n": int(len(x)), "max_abs_roll_deg": float(np.max(np.abs(x))),
            "note": ("slope is the shift per unit phone roll; display-fixed predicts 0, "
                     "pinhole/mount-fixed predicts a slope of magnitude 1")}


def classify_slope(slope, tolerance=0.30):
    """Name the hypothesis a fitted slope is consistent with, or say unresolved."""
    if slope is None or not np.isfinite(slope):
        return "unresolved"
    if abs(slope) <= tolerance:
        return "display_consistent"
    if abs(abs(slope) - 1.0) <= tolerance:
        return "mount_consistent"
    return "unresolved"
