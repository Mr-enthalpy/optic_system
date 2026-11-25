"""Exposure-bracket fusion in original DN, without device IO or demosaic.

Each sample is converted to reference-exposure-equivalent signal above dark.
Unsaturated samples are combined with a headroom and exposure weight, so a
nearly clipped sample cannot dominate a seam. Saturated samples are excluded;
if every sample is saturated, the reported value is a lower bound.
"""
from dataclasses import dataclass
import numpy as np


@dataclass(frozen=True)
class HDRBracket:
    signal_dn: np.ndarray
    source_exposure_ns: np.ndarray
    saturated_mask: np.ndarray
    reference_exposure_ns: int
    counts_by_exposure: tuple[int, ...]
    seam_ratios: tuple[dict, ...]
    all_saturated_fraction: float
    negative_fraction: float


def _frames(values, shape, name):
    arrays = [np.asarray(value, dtype=np.float64) for value in values]
    if not arrays:
        raise ValueError(f"{name} must be nonempty")
    for array in arrays:
        if array.shape != shape:
            raise ValueError(f"{name} arrays must share one [H,W] shape")
        if not np.all(np.isfinite(array)):
            raise ValueError(f"{name} arrays must be finite")
    return arrays


def merge_exposure_bracket(frames, exposures_ns, *, darks=None, black_level=0.0,
                           white_level=1023.0, saturation_margin_dn=0.0,
                           min_ratio_signal_dn=4.0) -> HDRBracket:
    """Fuse native-resolution frames; output is above-dark, reference-exposure DN.

    ``darks`` is an optional sequence of same-shape dark means paired with
    ``frames`` by index. Without darks, ``black_level`` is subtracted instead.
    Saturation is judged on original DN at ``white_level - saturation_margin_dn``.
    """
    if isinstance(white_level, bool) or isinstance(black_level, bool):
        raise ValueError("black_level and white_level must be numbers, not booleans")
    white = float(white_level); black = float(black_level)
    if not np.isfinite(white) or not np.isfinite(black) or white <= black:
        raise ValueError("white_level must exceed a finite black_level")
    margin = float(saturation_margin_dn)
    if not np.isfinite(margin) or margin < 0:
        raise ValueError("saturation_margin_dn must be finite and nonnegative")
    threshold = white - margin
    if threshold <= black:
        raise ValueError("saturation threshold must exceed black_level")

    exposures = np.asarray(exposures_ns, dtype=np.int64)
    if exposures.ndim != 1 or not len(exposures) or np.any(exposures <= 0):
        raise ValueError("exposures_ns must be a nonempty sequence of positive integers")
    if len(np.unique(exposures)) != len(exposures):
        raise ValueError("exposures_ns must not repeat an exposure")

    raw = [np.asarray(frame, dtype=np.float64) for frame in frames]
    if len(raw) != len(exposures):
        raise ValueError("frames and exposures_ns must have the same length")
    shape = raw[0].shape
    if len(shape) != 2:
        raise ValueError("frames must be 2-D [H,W] arrays")
    raw = _frames(raw, shape, "frames")
    if darks is None:
        dark = [np.full(shape, black, dtype=np.float64) for _ in exposures]
    else:
        if len(darks) != len(exposures):
            raise ValueError("darks must pair one-to-one with frames")
        dark = _frames(darks, shape, "darks")

    reference = int(exposures.min())
    span = threshold - black
    numerator = np.zeros(shape, dtype=np.float64)
    denominator = np.zeros(shape, dtype=np.float64)
    saturated = np.zeros(shape, dtype=bool)
    all_saturated = np.ones(shape, dtype=bool)
    lower_bound = np.full(shape, -np.inf, dtype=np.float64)

    for frame, darkness, exposure in zip(raw, dark, exposures):
        is_saturated = frame >= threshold
        headroom = np.maximum(threshold - frame, 0.0)
        weight = np.where(is_saturated, 0.0,
                          (headroom / span) * (float(exposure) / reference))
        signal = frame - darkness
        scaled = signal * (reference / float(exposure))
        numerator += weight * scaled
        denominator += weight
        saturated |= is_saturated
        all_saturated &= is_saturated
        bound = (threshold - darkness) * (reference / float(exposure))
        lower_bound = np.where(is_saturated, np.maximum(lower_bound, bound), lower_bound)

    valid = denominator > 0
    signal_dn = np.zeros(shape, dtype=np.float64)
    np.divide(numerator, denominator, out=signal_dn, where=valid)
    signal_dn = np.where(all_saturated, lower_bound, signal_dn)

    weights = []
    for frame, exposure in zip(raw, exposures):
        is_saturated = frame >= threshold
        headroom = np.maximum(threshold - frame, 0.0)
        weights.append(np.where(is_saturated, 0.0,
                                (headroom / span) * (float(exposure) / reference)))
    dominant = np.argmax(np.stack(weights, axis=0), axis=0)
    source = exposures[dominant].astype(np.int64)
    source = np.where(all_saturated, reference, source)

    counts = tuple(int(np.count_nonzero((source == exposure) & ~all_saturated)) for exposure in exposures)
    seams = []
    order = np.argsort(exposures)
    for first, second in zip(order[:-1], order[1:]):
        short, long = int(exposures[first]), int(exposures[second])
        overlap = ((raw[first] < threshold) & (raw[second] < threshold)
                   & (raw[first] - dark[first] >= min_ratio_signal_dn)
                   & (raw[second] - dark[second] >= min_ratio_signal_dn))
        pixels = int(np.count_nonzero(overlap))
        if pixels:
            ratio = ((raw[second][overlap] - dark[second][overlap]) / long) / \
                    ((raw[first][overlap] - dark[first][overlap]) / short)
            seams.append({"short_exposure_ns": short, "long_exposure_ns": long, "pixels": pixels,
                          "median_ratio": float(np.median(ratio)),
                          "p05_ratio": float(np.percentile(ratio, 5)),
                          "p95_ratio": float(np.percentile(ratio, 95)),
                          "fraction_within_2pct": float(np.mean(np.abs(ratio - 1) <= 0.02))})
        else:
            seams.append({"short_exposure_ns": short, "long_exposure_ns": long, "pixels": 0,
                          "median_ratio": None, "p05_ratio": None, "p95_ratio": None,
                          "fraction_within_2pct": None})

    return HDRBracket(signal_dn=signal_dn, source_exposure_ns=source, saturated_mask=all_saturated,
                      reference_exposure_ns=reference, counts_by_exposure=counts,
                      seam_ratios=tuple(seams),
                      all_saturated_fraction=float(np.mean(all_saturated)),
                      negative_fraction=float(np.mean(signal_dn < 0)))
