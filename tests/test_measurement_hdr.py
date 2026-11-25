"""Synthetic exposure brackets only; no phone or optical measurement."""
import numpy as np
import pytest

from tasks.measurement.hdr import merge_exposure_bracket


def bracket(signal, exposures, *, black=64.0, white=1023.0, dark_extra=0.0, drift=None):
    signal = np.asarray(signal, dtype=float)
    exposures = np.asarray(exposures, dtype=float)
    scale = exposures / exposures.min()
    if drift is None:
        drift = np.ones_like(scale)
    raws = [np.clip(black + dark_extra + signal * factor * gain, 0, white)
            for factor, gain in zip(scale, drift)]
    darks = [np.full(signal.shape, black + dark_extra) for _ in exposures]
    return raws, darks


def test_recovers_four_decades_and_ignores_saturated_samples():
    levels = 10.0 ** np.linspace(-2, np.log10(400), 40).reshape(4, 10)
    exposures = [1000, 25000, 100000]
    raws, darks = bracket(levels, exposures)
    merged = merge_exposure_bracket(raws, exposures, darks=darks, black_level=64, white_level=1023)
    reachable = levels < 900
    np.testing.assert_allclose(merged.signal_dn[reachable], levels[reachable], rtol=1e-12, atol=1e-12)
    assert merged.reference_exposure_ns == 1000
    assert merged.all_saturated_fraction == 0.0
    assert merged.negative_fraction == 0
    for seam in merged.seam_ratios:
        assert seam["median_ratio"] == pytest.approx(1.0, abs=1e-12)


def test_clipped_long_sample_is_excluded_not_averaged():
    signal = np.array([[10.0, 500.0]])
    exposures = [1000, 25000]
    raws, darks = bracket(signal, exposures)
    merged = merge_exposure_bracket(raws, exposures, darks=darks, black_level=64, white_level=1023)
    np.testing.assert_allclose(merged.signal_dn, signal, rtol=1e-12)
    assert merged.source_exposure_ns[0, 0] == 25000
    assert merged.source_exposure_ns[0, 1] == 1000


def test_all_saturated_samples_report_a_lower_bound():
    signal = np.array([[5000.0]])
    exposures = [1000, 25000, 100000]
    raws, darks = bracket(signal, exposures)
    merged = merge_exposure_bracket(raws, exposures, darks=darks, black_level=64, white_level=1023)
    assert merged.saturated_mask[0, 0]
    assert merged.signal_dn[0, 0] == pytest.approx(1023.0 - 64.0)
    assert merged.source_exposure_ns[0, 0] == 1000


def test_common_exposure_scale_does_not_change_signal():
    levels = np.array([[0.05, 3.0, 200.0]])
    first, darks = bracket(levels, [1000, 25000, 100000])
    second, _ = bracket(levels, [7000, 175000, 700000])
    a = merge_exposure_bracket(first, [1000, 25000, 100000], darks=darks, black_level=64, white_level=1023)
    b = merge_exposure_bracket(second, [7000, 175000, 700000], black_level=64, white_level=1023)
    np.testing.assert_allclose(a.signal_dn, b.signal_dn, rtol=1e-12)


def test_seam_ratio_detects_a_ten_percent_illumination_change():
    levels = np.full((8, 8), 2.0)
    raws, darks = bracket(levels, [1000, 25000], drift=[1.0, 1.10])
    merged = merge_exposure_bracket(raws, [1000, 25000], darks=darks, black_level=64, white_level=1023,
                                    min_ratio_signal_dn=1.0)
    seam = merged.seam_ratios[0]
    assert seam["median_ratio"] == pytest.approx(1.10, abs=1e-12)
    assert seam["fraction_within_2pct"] == 0.0


def test_matched_dark_is_subtracted_before_scaling():
    signal = np.array([[0.0, 1.0], [20.0, 250.0]])
    dark_pattern = np.array([[61.0, 64.0], [66.0, 64.0]])
    exposures = [1000, 30000]
    raws = [np.clip(dark_pattern + signal * (e / 1000.0), 0, 1023) for e in exposures]
    merged = merge_exposure_bracket(raws, exposures, darks=[dark_pattern, dark_pattern],
                                    black_level=64, white_level=1023, min_ratio_signal_dn=0.5)
    np.testing.assert_allclose(merged.signal_dn, signal, rtol=1e-12, atol=1e-12)


@pytest.mark.parametrize("change", [
    {"exposures_ns": [0, 1000]},
    {"exposures_ns": [1000, 1000]},
    {"exposures_ns": [1000]},
])
def test_invalid_exposures_are_rejected(change):
    with pytest.raises(ValueError):
        merge_exposure_bracket([np.zeros((2, 2)), np.zeros((2, 2))], **change)


def test_mismatched_shapes_darks_and_nonfinite_inputs_are_rejected():
    with pytest.raises(ValueError):
        merge_exposure_bracket([np.zeros((2, 2)), np.zeros((3, 2))], [1000, 2000])
    with pytest.raises(ValueError):
        merge_exposure_bracket([np.zeros((2, 2))], [1000], darks=[])
    with pytest.raises(ValueError):
        merge_exposure_bracket([np.array([[np.nan]])], [1000])
    with pytest.raises(ValueError):
        merge_exposure_bracket([np.zeros((2, 2))], [1000], black_level=1023, white_level=1023)
