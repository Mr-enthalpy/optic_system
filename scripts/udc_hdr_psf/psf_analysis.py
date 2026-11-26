"""PSF-domain analysis for the UDC point-spread session.

Everything here is derived from the archived 2026-11-26 measurement
(``SEAM_DIAGNOSIS.json``), which is still valid as *geometry* even though its
wavelength sweep was invalid: the source never changed colour, so all bands
carried one green-dominant spectrum, but the lens/sensor PSF at that spectrum
was measured normally.

What that record says, and what it forces
----------------------------------------
1. The PSF is compact.  At the base tier, median signal above black by radius
   is 726 DN at r<1, 148 at r=1-2, 52 at r=2-3, 30 at r=3-5, 4 at r=5-8,
   1.7 at r=8-12, 1.0 at r=12-20, 0.3 at r=20-40, 0 at r=40-80.
   A ring at r=15-80 px therefore measures the black level, not the PSF; the
   earlier driver's ``phase_ring(15, 80)`` was measuring ~1 DN of nothing.
   Analysis here works in radius bands instead.

2. The RAW pipeline saturates at 959 DN, not at the declared white level 1023.
   In the archived 550 nm bracket the 25x and 100x tiers both give an identical
   959.0 DN median at r<3, while the 25x tier is still linear (678.7 DN) at
   r=3-5.  A hard plateau.  ``fraction_at_or_above_declared_white`` is therefore
   always ~0 and cannot detect it, so the ceiling is measured from the data.
   Treating 959 as unsaturated would mix clipped samples into the merge.

3. The seam criterion has to depend on signal level.  At the base->25x seam the
   overlap pixels carry 4-15 DN, where one code is 7-25 %, so a 2 % rule is
   below the quantisation floor and no bracket can satisfy it.  Seams are
   judged only in radius bands where the short tier is above 30 DN.

The consequence for the experiment: the bracket must span about four decades
(core 700 DN at a few hundred microseconds, r=20-40 halo near 0.3 DN, so
~400 ms), and the PSF shape must be compared band by band in radius.
"""
from __future__ import annotations

import numpy as np

# Radius bands in pixels, on the 2x2-binned 3264x2448 readout.  The upper edge
# of 80 px is the largest radius that fits a 176 px analysis window.
RADII_PX = ((0, 1), (1, 2), (2, 3), (3, 5), (5, 8), (8, 12), (12, 20), (20, 40), (40, 80))
# The seam scan uses finer bands than the PSF table: an exposure transition only
# exists inside a narrow radial shell, so a coarse band averages the transition
# together with the faint tail and reports a meaningless median.  The bands are
# kept wide enough to hold about twenty lattice pixels each, which is the least
# that supports a median.
SEAM_RADII_PX = ((0, 2), (2, 4), (4, 6), (6, 8), (8, 11), (11, 14), (14, 18),
                 (18, 24), (24, 32), (32, 45), (45, 60), (60, 80))
# Quad-CFA BGGR at the binned grid: (y % 2, x % 2) -> colour plane.
PHASES = (("R", 1, 1), ("Gr", 0, 1), ("Gb", 1, 0), ("B", 0, 0))

# A radius band needs this many pixels before its ratio is reported at all.
SEAM_MIN_PIXELS = 20
# Below this short-tier level one 10-bit code exceeds 5 %, so no fractional
# tolerance can be verified there.
JUDGE_MIN_DN = 20.0
# The seam tolerance is not a constant any more.  It is the larger of one
# quantisation code at that level and the spread that the *same* exposure shows
# between its own repeats inside the same bracket.  2 % was below this
# instrument's reproducibility for a diffracted spot sampled at 1.6 um: five
# repeats of one exposure scatter by 1-3 % per ring (see REPEATABILITY_*), and
# no drift model or extra frame averaging closes that gap because two thirds of
# the scatter is common to a batch.
SEAM_TOLERANCE_FLOOR = 0.02
# A repeatability worse than this cannot verify anything: the criterion would be
# vacuous, so the band is reported as unjudged instead of passed.
REPEATABILITY_MAX_PCT = 25.0
# The same exposure has to be captured this many times before its spread means
# anything.
REPEATABILITY_MIN_FRAMES = 3
# Plane ratios are integrated from this radius outward: inside r=2 the annuli
# hold 1 and 4 pixels, so which plane owns the peak pixel decides the ratio.
RATIO_MIN_RADIUS_PX = 2.0
# Default analysis window; the PSF reaches the floor well inside it.
DEFAULT_WINDOW_PX = 176
# The shortest tier has to put the peak inside the linear range but high enough
# that the surrounding rings are not a handful of codes.  Measured on the peak,
# not on the 3x3 median: this PSF is one or two pixels wide, so at 450 nm the
# peak sits 17.7x above its 3x3 median, and a median rule asks for an exposure
# that clips the peak it is supposed to protect (2026-11-27 gate: 3650 us would
# have been needed for a 50 DN median, at which the peak is 883 DN, 92 % of span).
MIN_PEAK_FRACTION = 0.40
# Below this fraction the peak is within a few codes of the noise floor and the
# core ratio is not a measurement.  This is the hard floor; MIN_PEAK_FRACTION is
# only the target, and missing it is recorded rather than fatal.
HARD_MIN_PEAK_FRACTION = 0.05
# A core that is clipping in the shortest tier has no measured peak at all.
MAX_CORE_SATURATED_FRACTION = 0.05


def window(peak_xy, size: int, shape=(2448, 3264)):
    """Half-open (x, y, width, height) window of odd-free even size around a peak."""
    x = int(min(max(0, peak_xy[0] - size // 2), shape[1] - size))
    y = int(min(max(0, peak_xy[1] - size // 2), shape[0] - size))
    return [x - x % 2, y - y % 2, size, size]


def crop(array, rect):
    x, y, w, h = rect
    return np.asarray(array[y:y + h, x:x + w])


def radius_grid(shape, peak_xy):
    """Radius and parity masks for a window whose peak is at ``peak_xy`` in *window* coordinates."""
    height, width = shape
    cx, cy = peak_xy
    yy = np.arange(height)[:, None]
    xx = np.arange(width)[None, :]
    radius = np.hypot(yy - cy, xx - cx)
    return radius, yy, xx


def plane_masks(yy, xx):
    return {name: ((yy % 2) == py) & ((xx % 2) == px) for name, py, px in PHASES}


# --------------------------------------------------------------------------
# saturation ceiling
# --------------------------------------------------------------------------
def detect_ceiling(frames, black: float, min_pixels: int = 8, planes=None):
    """Measure where the RAW pipeline stops being linear, from the frames.

    The declared white level is not the ceiling: the 2026-11-26 data flattens at
    959 DN while the characteristics still declare 1023.  A plateau is not just
    the highest occupied code, because an unsaturated frame also has a highest
    occupied code.  It is the highest code that is (a) held by at least
    ``min_pixels`` pixels and (b) followed by a gap: essentially nothing is
    recorded above it.  A smooth noise tail fails (b); a clipping plateau passes.

    Returns the global ceiling, the per-frame values, and per-plane ceilings,
    because the four CFA planes may clip at different codes.
    """
    per_frame = []
    occupied_counts = {}
    for frame in frames:
        values, counts = np.unique(np.asarray(frame), return_counts=True)
        occupied = values[counts >= min_pixels]
        per_frame.append(int(occupied.max()) if occupied.size else int(values.max()))
        for value, count in zip(values, counts):
            occupied_counts[int(value)] = occupied_counts.get(int(value), 0) + int(count)
    ceiling = max(per_frame)
    support = occupied_counts.get(ceiling, 0)
    previous = occupied_counts.get(ceiling - 1, 0)
    above = sum(count for value, count in occupied_counts.items() if ceiling < value < 1023)
    bright = sum(count for value, count in occupied_counts.items()
                 if value > black + 0.5 * (ceiling - black))
    # A plateau piles up on one code, has almost nothing above it, and holds a
    # real share of the bright pixels.  A smooth tail fails all three: it
    # spreads over neighbouring codes and its top code carries only a sliver.
    plateau = (support >= min_pixels
               and above <= max(4, support // 4)
               and (previous == 0 or support >= 3 * previous)
               and support >= 0.25 * max(bright, 1))
    plane_ceiling = {}
    if planes is not None:
        yy, xx = planes
        for name, mask in plane_masks(yy, xx).items():
            values, counts = np.unique(np.asarray(frames[-1])[mask], return_counts=True)
            occupied = values[counts >= max(2, min_pixels // 4)]
            plane_ceiling[name] = int(occupied.max()) if occupied.size else int(values.max())
    return {"ceiling_dn": ceiling, "per_frame_ceiling_dn": per_frame,
            "ceiling_support_px": support, "codes_above_ceiling_px": above,
            "black_dn": float(black), "span_dn": float(ceiling - black),
            "plane_ceiling_dn": plane_ceiling, "plateau": bool(plateau),
            "evidence": ("a plateau is the highest code held by at least %d pixels with no "
                         "comparable population above it; the declared white level is not used "
                         "because this pipeline flattens early" % min_pixels)}


# --------------------------------------------------------------------------
# merged PSF
# --------------------------------------------------------------------------
def merge(frames, exposures_us, black: float, ceiling: float, margin: float = 0.0):
    """Fuse the bracket, treating the measured ceiling as saturation."""
    from tasks.measurement.hdr import merge_exposure_bracket
    return merge_exposure_bracket(
        frames, [int(round(e)) * 1000 for e in exposures_us],
        black_level=float(black), white_level=float(ceiling),
        saturation_margin_dn=float(margin))


def radial_table(signal, radius, plane_mask, radii=RADII_PX):
    """Per plane, per radius band: mean/median/spread of the merged signal.

    ``signal`` is the merged array, above dark, in reference-exposure DN.
    """
    out = {}
    for name, pm in plane_mask.items():
        bands = []
        for r0, r1 in radii:
            mask = pm & (radius >= r0) & (radius < r1)
            values = signal[mask]
            bands.append({
                "radius_px": [r0, r1], "pixels": int(values.size),
                "mean": float(values.mean()) if values.size else float("nan"),
                "median": float(np.median(values)) if values.size else float("nan"),
                "std": float(values.std()) if values.size else float("nan"),
                "sem": float(values.std() / np.sqrt(values.size)) if values.size > 1 else float("nan"),
            })
        out[name] = bands
    return out


def plane_flux(radial, radii=RADII_PX):
    """Integrated per-plane signal and its standard error, per radius limit.

    Flux, not a ring average, because the spectral content is proportional to
    the number of photons a plane receives; the ring average conflates that with
    the shape.  Bands that hold no pixel of a given plane (the r<1 and r<2 bands
    hold one and four pixels in total) are skipped instead of poisoning the sum
    with a NaN.
    """
    limits = sorted({r1 for _, r1 in radii})
    flux, sigma = {}, {}
    for limit in limits:
        flux[limit], sigma[limit] = {}, {}
        for name, bands in radial.items():
            used = [b for b in bands
                    if b["radius_px"][1] <= limit and b["radius_px"][1] > RATIO_MIN_RADIUS_PX
                    and b["pixels"] > 0 and b["mean"] == b["mean"]]
            flux[limit][name] = float(sum(b["mean"] * b["pixels"] for b in used))
            sigma[limit][name] = float(np.sqrt(sum((b["sem"] * b["pixels"]) ** 2
                                                   for b in used if b["sem"] == b["sem"])))
    return flux, sigma


def colour_ratios(flux, limit, sigma=None):
    """R/G and B/R from the integrated flux inside ``limit``, with an error."""
    planes = flux[limit]
    green = 0.5 * (planes["Gr"] + planes["Gb"])
    out = {"r_g": planes["R"] / green if green else float("nan"),
           "b_g": planes["B"] / green if green else float("nan"),
           "b_r": planes["B"] / planes["R"] if planes["R"] else float("nan"),
           "green": green, "limit_px": limit}
    if sigma is not None:
        errors = sigma[limit]
        green_sigma = 0.5 * np.hypot(errors["Gr"], errors["Gb"])
        def relative(numerator, denominator, numerator_sigma, denominator_sigma):
            if not numerator or not denominator:
                return float("nan")
            return float(np.hypot(numerator_sigma / numerator,
                                  denominator_sigma / denominator))
        out["r_g_sigma"] = relative(planes["R"], green, errors["R"], green_sigma)
        out["b_r_sigma"] = relative(planes["B"], planes["R"], errors["B"], errors["R"])
    return out


# --------------------------------------------------------------------------
# source drift: measured with reference exposures, then divided out
# --------------------------------------------------------------------------
DRIFT_FLUX_LIMIT_PX = 20.0
# Pixels used for the drift estimate must sit this far above dark in *every*
# reference.  Below it a single code is 5-50 % of the signal and an integer
# frame cannot record a 4 % change at all, which biases the measured drift low.
DRIFT_FLOOR_DN = 8.0


def reference_fluxes(frames, peak_xy_window, black: float, floor_dn: float = DRIFT_FLOOR_DN,
                     limit_px=DRIFT_FLUX_LIMIT_PX):
    """Brightness of each reference exposure, on one fixed set of pixels.

    The set is every pixel inside ``limit_px`` that is at least ``floor_dn`` above
    dark in all references, and it is the same for each frame, so the sums differ
    only by the lamp.  Summing the whole disc instead would dilute real drift with
    wing pixels sitting at one or two codes; taking a single pixel would carry its
    own shot noise.
    """
    signals = [np.asarray(frame, dtype=np.float64) - black for frame in frames]
    radius, yy, xx = radius_grid(signals[0].shape, peak_xy_window)
    keep = radius <= limit_px
    for signal in signals:
        keep = keep & (signal >= floor_dn)
    if int(keep.sum()) < 16:
        raise ValueError(f"only {int(keep.sum())} pixels stay above {floor_dn:g} DN in every "
                         "reference: the reference exposure is too dim to measure the lamp")
    fluxes = []
    for signal in signals:
        entry = {"total": float(signal[keep].sum())}
        for name, mask in plane_masks(yy, xx).items():
            entry[name] = float(signal[keep & mask].sum())
        fluxes.append(entry)
    return {"pixels": int(keep.sum()), "floor_dn": float(floor_dn),
            "limit_px": float(limit_px), "flux": fluxes}


def drift_factors(reference_times_s, reference_flux, tier_times_s):
    """Brightness factor at each tier time, normalised to the first reference.

    No functional form is assumed: between two measured references the factor is
    interpolated linearly, and outside the measured span the nearest value is
    held.  A straight-line fit is also reported, because its residual says
    whether the lamp drifted smoothly enough for that interpolation to mean
    anything -- on 2026-11-27 a 4 % seam error first looked like sensor
    non-linearity, and this is the test that separates the two.
    """
    times = np.asarray(reference_times_s, dtype=float)
    flux = np.asarray(reference_flux, dtype=float)
    if times.size < 2:
        raise ValueError("drift needs at least two reference captures")
    if not np.all(np.isfinite(flux)) or not flux[0]:
        raise ValueError("drift needs finite, non-zero reference fluxes")
    origin = float(times[0])
    offsets = times - origin
    scale = flux / flux[0]
    tier_offsets = np.asarray(tier_times_s, dtype=float) - origin
    factors = [float(np.interp(offset, offsets, scale)) for offset in tier_offsets]
    slope, intercept = np.polyfit(offsets, scale, 1)
    fitted = slope * offsets + intercept
    residual = float(np.max(np.abs(scale - fitted) / np.where(scale, scale, 1.0))) * 100.0
    return {"factors": factors,
            "rate_pct_per_min": float(slope * 60.0 * 100.0),
            "change_pct": float((scale[-1] - 1.0) * 100.0),
            "span_min": float(offsets[-1] / 60.0),
            "linear_residual_pct": residual,
            "reference_times_s": [float(t) for t in offsets],
            "reference_factors": [float(s) for s in scale]}


def correct_drift(frames, factors, black: float, ceiling: float):
    """Divide each frame's signal by the lamp's brightness when it was taken.

    The black level is not light and must not be divided: doing so would shift
    every tier by a different constant and fake the very seam error the
    correction exists to remove.  A pixel that had already saturated keeps its
    recorded value instead of being divided down, otherwise a clipped pixel would
    fall below the ceiling and be read as a real measurement.
    """
    out = []
    for frame, factor in zip(frames, factors):
        frame = np.asarray(frame, dtype=np.float64)
        fixed = float(black) + (frame - float(black)) / float(factor)
        out.append(np.where(frame >= float(ceiling), frame, fixed))
    return out


def shape_fractions(radial, planes=("R", "Gr", "Gb", "B")):
    """Each plane's radial profile normalised to its own total inside r=20.

    This is the quantity a spectral-coding claim needs: not how bright a plane
    is, but whether the *shape* of its PSF differs between wavelengths.
    """
    out = {}
    for name in planes:
        bands = radial[name]
        used = [b for b in bands if b["radius_px"][1] <= 20 and b["pixels"] > 0
                and b["mean"] == b["mean"]]
        total = sum(b["mean"] * b["pixels"] for b in used)
        out[name] = [{"radius_px": b["radius_px"],
                      "fraction": (b["mean"] * b["pixels"] / total) if total else float("nan")}
                     for b in bands]
    return out


# --------------------------------------------------------------------------
# how well the instrument repeats itself, and the seam scan that depends on it
# --------------------------------------------------------------------------
def _band_key(pair):
    return "{}-{}".format(int(pair[0]), int(pair[1]))


def repeatability(frames, exposures_us, radius, black: float, ceiling: float,
                  radii=SEAM_RADII_PX, min_frames: int = REPEATABILITY_MIN_FRAMES):
    """Ring by ring, how much one exposure's own repeats disagree.

    A bracket repeats its shortest tier between tiers, so the same signal is
    captured several times.  Whatever differs between those repeats is the
    measurement's own reproducibility, and a seam criterion tighter than that
    can only reject good data.  The spread is the observed range rather than a
    standard deviation, because it is used directly as a tolerance: it should
    be the envelope actually seen, not a width that assumes a distribution.

    Flux, not a median: on this PSF the median of an annulus discards all but
    the middle pixel of a steeply falling profile and its own repeatability is
    about twice as wide as the flux sum's.
    """
    frames = [np.asarray(frame, dtype=np.float64) for frame in frames]
    groups = {}
    for index, exposure in enumerate(exposures_us):
        groups.setdefault(round(float(exposure), 3), []).append(index)
    repeated = {exposure: indices for exposure, indices in groups.items()
                if len(indices) >= min_frames}
    if not repeated:
        return {"available": False,
                "reason": (f"no exposure is captured at least {min_frames} times in this "
                           "bracket, so the tolerance cannot be measured"),
                "bands": {}}
    bands = {}
    for r0, r1 in radii:
        ring = (radius >= r0) & (radius < r1)
        spreads, levels, fluxes, counts = [], [], [], []
        for exposure, indices in repeated.items():
            sums, means = [], []
            for index in indices:
                usable = ring & (frames[index] < ceiling)
                if int(usable.sum()) < SEAM_MIN_PIXELS:
                    continue
                values = frames[index][usable] - black
                sums.append(float(values.sum()))
                # Per pixel, so the level is comparable with the seam scan's
                # JUDGE_MIN_DN.  The ring sum is kept separately: a faint ring
                # can sum to hundreds of DN while every pixel in it is a code
                # above dark, and its spread says nothing about the PSF.
                means.append(float(values.mean()))
            if len(sums) < min_frames:
                continue
            array = np.asarray(sums, dtype=float)
            level = float(np.mean(means))
            if level <= 0 or array.mean() <= 0:
                continue
            spreads.append(100.0 * float(array.max() - array.min()) / float(array.mean()))
            levels.append(level)
            fluxes.append(float(array.mean()))
            counts.append(len(sums))
        if not spreads:
            continue
        bands[_band_key((r0, r1))] = {
            "range_pct": round(max(spreads), 3),
            "level_dn": round(float(np.mean(levels)), 2),
            "flux_dn": round(float(np.mean(fluxes)), 1),
            "repeats": int(max(counts))}
    # The headline is taken over the rings that carry enough signal to be judged
    # at all.  A ring holding 0.3 DN scatters by more than 100 % of itself, which
    # is true but says nothing about the PSF: it is a handful of codes above
    # dark, and the seam scan refuses to judge it anyway.
    judgeable = {key: band for key, band in bands.items()
                 if band["level_dn"] >= JUDGE_MIN_DN}
    return {"available": bool(judgeable),
            "reason": (None if judgeable else
                       f"no ring is above {JUDGE_MIN_DN:.0f} DN, so the tolerance cannot be "
                       "measured at a level where it would be used"),
            "groups": [{"exposure_us": exposure, "repeats": len(indices)}
                       for exposure, indices in sorted(repeated.items())],
            "bands": bands,
            "worst_pct": round(max((b["range_pct"] for b in judgeable.values()), default=0.0), 3),
            "worst_ring": (max(judgeable, key=lambda k: judgeable[k]["range_pct"])
                           if judgeable else None),
            "worst_pct_all_rings": round(max((b["range_pct"] for b in bands.values()),
                                             default=0.0), 3),
            "note": ("Range of the ring flux between captures of one exposure inside this "
                     "bracket. It is the floor under any seam tolerance: energy moves "
                     "between adjacent rings when a spot this small shifts by a few "
                     "hundredths of a pixel, while the total is stable. worst_pct is "
                     f"over rings at or above {JUDGE_MIN_DN:.0f} DN; the rings below that "
                     "are listed in bands but cannot be judged whatever their spread.")}


def annulus_seams(frames, exposures_us, radius, black: float, ceiling: float,
                  radii=SEAM_RADII_PX, null_pct=None,
                  strict_floor: float = SEAM_TOLERANCE_FLOOR):
    """Per tier pair and radius band, does the long tier equal the short one?

    The tolerance is the larger of one quantisation code at that level and the
    spread the same exposure shows between its own repeats (``null_pct``).  A
    fixed 2 % was below this instrument's reproducibility for shape, so it
    rejected well-exposed brackets; the measured floor replaces it.

    The old fixed-tolerance judgement is still computed and reported as
    ``within_strict`` / ``strict_bad_bands`` so the stricter statement stays in
    the record and can be read without the new tolerance.

    Every band that cannot be judged is reported with ``judged=False`` and a
    reason rather than silently dropped.
    """
    null_pct = null_pct or {}
    frames = [np.asarray(frame, dtype=np.float64) for frame in frames]
    order = np.argsort(np.asarray(exposures_us, dtype=float))
    pairs = []
    for first, second in zip(order[:-1], order[1:]):
        short_us = float(exposures_us[first])
        long_us = float(exposures_us[second])
        factor = long_us / short_us
        short_signal = frames[first] - black
        long_signal = frames[second] - black
        rows = []
        for r0, r1 in radii:
            ring = (radius >= r0) & (radius < r1)
            usable = ring & (frames[first] < ceiling) & (frames[second] < ceiling)
            pixels = int(usable.sum())
            if pixels < SEAM_MIN_PIXELS:
                rows.append({"radius_px": [r0, r1], "pixels": pixels,
                             "judged": False, "reason": "too_few_pixels"})
                continue
            short_level = float(short_signal[usable].mean())
            long_level = float(long_signal[usable].mean())
            ratio = (long_level / factor) / short_level if short_level > 0 else float("inf")
            lit = usable & (short_signal >= 4.0) & (long_signal >= 4.0)
            median_ratio = None
            if int(lit.sum()) >= SEAM_MIN_PIXELS:
                short_median = float(np.median(short_signal[lit]))
                if short_median > 0:
                    median_ratio = (float(np.median(long_signal[lit])) / factor) / short_median
            measured = null_pct.get(_band_key((r0, r1)))
            floor = 1.0 / short_level if short_level > 0 else 1.0
            strict_tolerance = max(strict_floor, floor)
            # Without a repeat the tolerance cannot be measured, so the old fixed
            # rule stands and the record says so.
            tolerance = strict_tolerance if measured is None else max(floor, measured / 100.0)
            if measured is not None and measured > REPEATABILITY_MAX_PCT:
                judged = False
                reason = (f"the same exposure repeats only to {measured:.1f} %, so this "
                          "band cannot verify anything")
            elif short_level < JUDGE_MIN_DN:
                judged = False
                reason = f"short tier below {JUDGE_MIN_DN:.0f} DN"
            else:
                judged = True
                reason = "ok"
            clipped = 1.0 - pixels / max(int(ring.sum()), 1)
            rows.append({"radius_px": [r0, r1], "pixels": pixels,
                         "short_level_dn": round(short_level, 2),
                         "long_level_dn": round(long_level, 2),
                         "ratio": round(ratio, 5),
                         "ratio_median": None if median_ratio is None else round(median_ratio, 5),
                         "clipped_fraction": round(clipped, 4),
                         "judged": judged,
                         "tolerance": round(tolerance, 4) if judged else None,
                         "tolerance_strict": round(strict_tolerance, 4) if judged else None,
                         "tolerance_source": ("measured repeatability %.2f %%" % measured
                                              if measured is not None else
                                              "quantisation only"),
                         "within": (abs(ratio - 1) <= tolerance) if judged else None,
                         "within_strict": (abs(ratio - 1) <= strict_tolerance) if judged else None,
                         "reason": reason})
        pairs.append({"short_us": short_us, "long_us": long_us, "factor": factor,
                      "bands": rows})
    judged = [row for pair in pairs for row in pair["bands"] if row["judged"]]
    bad = [row for row in judged if row["within"] is False]
    strict_bad = [row for row in judged if row["within_strict"] is False]
    return {"pairs": pairs, "judged_bands": len(judged),
            "bad_bands": len(bad),
            "strict_bad_bands": len(strict_bad),
            "passed": len(judged) >= 2 and not bad,
            "strict_passed": len(judged) >= 2 and not strict_bad,
            "strength": "weak",
            "criterion": (f"at least two radius bands judged where the short tier is above "
                          f"{JUDGE_MIN_DN:.0f} DN, each within one quantisation code or the "
                          "measured repeatability of the same exposure, whichever is looser, "
                          "and none outside. This describes how far the bracket can be "
                          "trusted; it does not decide whether the band is usable.")}


def verdict(result, min_peak_fraction: float = MIN_PEAK_FRACTION,
            floor_peak_fraction: float = HARD_MIN_PEAK_FRACTION,
            max_core_saturated: float = MAX_CORE_SATURATED_FRACTION):
    """Is this bracket usable as a PSF measurement, and how good is it?

    Two conditions are hard, because nothing downstream can repair them:

    * the shortest tier clips the core, so the peak is a lower bound and the
      core ratio is wrong rather than noisy;
    * the peak sits within a few codes of the noise floor, so the core ratio is
      not a measurement at all.

    Missing the 80 % target is *not* hard.  A band whose peak lands at 45 % of
    span is a usable band with a noisier core, and reporting it as a rejection
    would throw away a wavelength for a reason that is about exposure choice,
    not about the source.  It is recorded as an indicator, which is also the
    cleaning hint: lengthen the shortest tier.

    The depth test uses the peak because the peak is the quantity the outer rings
    scale with, and because at 450 nm the 3x3 median reads 17.7x below it: a
    median floor rejected a bracket whose peak was already 29 % of span and whose
    ratio had passed by 307x.  The 3x3 median is still recorded for context.
    """
    core = result["core"]
    span = float(result["ceiling"]["span_dn"])
    peak = float(core["core_peak_dn_shortest_tier"])
    hard, indicators = [], []
    clipped = core["core_saturated_fraction"] > max_core_saturated
    below_floor = peak < floor_peak_fraction * span
    too_dim = peak < min_peak_fraction * span
    if clipped:
        hard.append(f"core clipped in the {core['shortest_us']:.0f} us tier "
                    f"({core['core_saturated_fraction']:.0%} of core pixels at the ceiling); "
                    "add a shorter tier")
    if below_floor:
        hard.append(f"peak is only {peak:.0f} DN at {core['shortest_us']:.0f} us, below "
                    f"{floor_peak_fraction:.0%} of the {span:.0f} DN span "
                    f"({floor_peak_fraction * span:.0f} DN); the core ratio is inside the "
                    "quantisation noise")
    if too_dim and not below_floor:
        indicators.append(f"peak is {peak:.0f} DN = {100 * peak / span:.0f} % of span at "
                          f"{core['shortest_us']:.0f} us, under the {min_peak_fraction:.0%} "
                          "target; a longer shortest tier would sharpen the core")
    return {"ok": not hard, "reasons": hard, "indicators": indicators,
            "core_clipped": bool(clipped),
            "peak_below_floor": bool(below_floor),
            "peak_too_dim": bool(too_dim),
            "min_peak_fraction": float(min_peak_fraction),
            "floor_peak_fraction": float(floor_peak_fraction),
            "span_dn": span,
            "peak_dn_shortest_tier": peak,
            "peak_fraction": peak / span if span else None,
            "core_dn_shortest_tier": core["core_dn_shortest_tier"],
            "core_peak_dn_shortest_tier": core["core_peak_dn_shortest_tier"],
            "core_saturated_fraction": core["core_saturated_fraction"]}


# --------------------------------------------------------------------------
# one call that produces everything the driver records
# --------------------------------------------------------------------------
def analyse(frames, exposures_us, peak_xy_window, black: float, radii=RADII_PX,
            ceiling=None, seam_radii=SEAM_RADII_PX, references=None):
    """Full analysis of one bracket.

    ``frames`` are window crops, ``peak_xy_window`` is the peak position inside
    the same window coordinates.  The ceiling is measured here unless supplied.

    ``references`` optionally carries the interleaved reference exposures as
    ``{"frames": [...], "exposures_us": [...]}``.  They are not part of the HDR
    merge - a bracket cannot contain the same exposure twice - but they are the
    same signal captured again, so they measure how well this instrument repeats
    and set the seam tolerance.
    """
    frames = [np.asarray(frame) for frame in frames]
    radius, yy, xx = radius_grid(frames[0].shape, peak_xy_window)
    masks = plane_masks(yy, xx)
    ceiling_info = dict(ceiling) if ceiling else detect_ceiling(frames, black, planes=(yy, xx))
    # A session ceiling arrives as just a code; the span it implies has to be
    # present too, because the verdict is stated as a fraction of the span.
    ceiling_info.setdefault("black_dn", float(black))
    ceiling_info.setdefault("span_dn", float(ceiling_info["ceiling_dn"]) - float(black))
    # If no frame ever reaches the plateau, the ceiling was not observed in this
    # bracket: nothing clipped, so the declared white level is the honest upper
    # bound and the record says the plateau is unproven.
    ceiling_dn = ceiling_info["ceiling_dn"] if ceiling_info.get("plateau") else None
    merged = merge(frames, exposures_us, black,
                   ceiling_dn if ceiling_dn is not None else 1023.0)
    radial = radial_table(merged.signal_dn, radius, masks, radii)
    flux, sigma = plane_flux(radial, radii)
    limits = sorted(flux)
    ratios = {str(limit): colour_ratios(flux, limit, sigma) for limit in limits}
    shortest = int(np.argmin(np.asarray(exposures_us, dtype=float)))
    core_mask = radius <= 1.5
    used_ceiling = ceiling_dn if ceiling_dn is not None else 1023.0
    core_values = np.asarray(frames[shortest])[core_mask]
    core = {"shortest_us": float(exposures_us[shortest]),
            "core_dn_shortest_tier": float(np.median(core_values) - black),
            "core_peak_dn_shortest_tier": float(np.max(core_values) - black),
            "core_saturated_fraction": float(np.mean(core_values >= used_ceiling)),
            "core_pixels": int(core_values.size)}
    # Measured before the seam scan, because the seam tolerance is this number.
    # The interleaved reference exposures are the honest source: they are the
    # same signal captured again, uncorrected, so their spread is what survives
    # any drift model.
    if references and references.get("frames"):
        repeats = repeatability(references["frames"], references["exposures_us"],
                                radius, black, used_ceiling, seam_radii)
        repeats["source"] = "interleaved reference exposures"
    else:
        repeats = repeatability(frames, exposures_us, radius, black, used_ceiling,
                                seam_radii)
        repeats["source"] = "repeated tiers inside the bracket"
    return {"ceiling": {**ceiling_info, "used_for_merge_dn": used_ceiling,
                        "plateau_observed": ceiling_dn is not None},
            "core": core,
            "radial": radial,
            "flux": {str(k): v for k, v in flux.items()},
            "ratios": ratios, "shape": shape_fractions(radial),
            "repeatability": repeats,
            "seams": annulus_seams(frames, exposures_us, radius, black,
                                   used_ceiling, seam_radii,
                                   null_pct={key: band["range_pct"]
                                             for key, band in repeats["bands"].items()}),
            "all_saturated_fraction": merged.all_saturated_fraction,
            "counts_by_exposure": list(merged.counts_by_exposure),
            "negative_fraction": merged.negative_fraction,
            "signal_max": float(np.max(merged.signal_dn)),
            "radii": [list(r) for r in radii]}
