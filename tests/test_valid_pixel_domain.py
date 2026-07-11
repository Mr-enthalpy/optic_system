from __future__ import annotations

import numpy as np
import pytest

from tasks.valid_pixel_domain import (
    EXPLICIT_MASK,
    FULL_FRAME,
    MAX_EXCLUDED_FRACTION,
    ValidPixelDomainError,
    coerce_valid_pixel_domain,
    describe_valid_pixel_domain,
    resolve_valid_pixel_domain,
    resolve_valid_pixel_mask,
    valid_pixel_mask_digest,
)


def test_resolve_full_frame_keeps_every_pixel() -> None:
    mask = resolve_valid_pixel_mask((4, 5))
    assert mask.shape == (4, 5)
    assert mask.dtype == bool
    assert mask.all()

    mask2 = resolve_valid_pixel_mask((4, 5), {"type": "full_frame"})
    assert mask2.all()


def test_resolve_exclude_top_rows_small_fraction() -> None:
    # 1 row of 2048 == 0.049% < 1% cap.
    mask = resolve_valid_pixel_mask((2048, 2448), {"type": "exclude_top_rows", "top_rows": 1})
    assert not mask[:1, :].any()
    assert mask[1:, :].all()


def test_resolve_exclude_xyxy_excludes_rectangle() -> None:
    mask = resolve_valid_pixel_mask(
        (2048, 2448), {"type": "exclude_xyxy", "xyxy": [1, 1, 3, 4]}
    )
    assert not mask[1:4, 1:3].any()
    assert mask[0, 0]
    assert mask[-1, -1]


def test_resolve_explicit_mask_validates_shape_and_nonempty() -> None:
    good = np.ones((3, 3), dtype=bool)
    good[0, 0] = False
    assert resolve_valid_pixel_mask(
        (3, 3), valid_pixel_mask=good, max_excluded_fraction=1.0
    )[0, 0] == False  # noqa: E712

    with pytest.raises(ValidPixelDomainError, match="does not match"):
        resolve_valid_pixel_mask((3, 3), valid_pixel_mask=np.ones((2, 2), dtype=bool))

    with pytest.raises(ValidPixelDomainError, match="zero valid pixels"):
        resolve_valid_pixel_mask((3, 3), valid_pixel_mask=np.zeros((3, 3), dtype=bool))


def test_resolve_rejects_policy_and_mask_together() -> None:
    with pytest.raises(ValidPixelDomainError, match="not both"):
        resolve_valid_pixel_mask(
            (3, 3),
            {"type": "full_frame"},
            np.ones((3, 3), dtype=bool),
        )


def test_resolve_rejects_top_rows_covering_whole_frame() -> None:
    with pytest.raises(ValidPixelDomainError, match="whole frame height"):
        resolve_valid_pixel_mask((4, 5), {"type": "exclude_top_rows", "top_rows": 4})


def test_resolve_rejects_unknown_policy_type() -> None:
    with pytest.raises(ValidPixelDomainError, match="unsupported valid_pixel_domain.type"):
        resolve_valid_pixel_mask((4, 5), {"type": "exclude_moon"})


# --- strict coercion / canonicalization -----------------------------------


def test_coerce_none_and_full_frame() -> None:
    assert coerce_valid_pixel_domain(None) is None
    assert coerce_valid_pixel_domain({"type": "full_frame"}) == {"type": "full_frame"}


def test_coerce_rejects_non_mapping() -> None:
    with pytest.raises(ValidPixelDomainError, match="must be a mapping"):
        coerce_valid_pixel_domain([1, 2, 3])


def test_coerce_rejects_unknown_field() -> None:
    with pytest.raises(ValidPixelDomainError, match="unknown valid_pixel_domain field"):
        coerce_valid_pixel_domain({"type": "exclude_top_rows", "top_row": 1})


def test_coerce_requires_top_rows_present() -> None:
    with pytest.raises(ValidPixelDomainError, match="requires an integer top_rows"):
        coerce_valid_pixel_domain({"type": "exclude_top_rows"})


def test_coerce_rejects_nonpositive_top_rows() -> None:
    with pytest.raises(ValidPixelDomainError, match="top_rows must be > 0"):
        coerce_valid_pixel_domain({"type": "exclude_top_rows", "top_rows": 0})


def test_coerce_rejects_inverted_and_empty_rectangle() -> None:
    with pytest.raises(ValidPixelDomainError, match="x1 > x0 and y1 > y0"):
        coerce_valid_pixel_domain({"type": "exclude_xyxy", "xyxy": [5, 5, 5, 9]})
    with pytest.raises(ValidPixelDomainError, match="x1 > x0 and y1 > y0"):
        coerce_valid_pixel_domain({"type": "exclude_xyxy", "xyxy": [9, 1, 3, 4]})


def test_coerce_rejects_negative_rectangle() -> None:
    with pytest.raises(ValidPixelDomainError, match="non-negative"):
        coerce_valid_pixel_domain({"type": "exclude_xyxy", "xyxy": [-1, 0, 3, 4]})


def test_coerce_override_requires_reason() -> None:
    with pytest.raises(ValidPixelDomainError, match="large_exclusion_reason"):
        coerce_valid_pixel_domain(
            {"type": "exclude_top_rows", "top_rows": 5, "large_exclusion_override": True}
        )


def test_coerce_override_with_reason_canonicalized() -> None:
    canonical = coerce_valid_pixel_domain(
        {
            "type": "exclude_top_rows",
            "top_rows": 5,
            "large_exclusion_override": True,
            "large_exclusion_reason": "documented sensor edge defect",
        }
    )
    assert canonical["large_exclusion_override"] is True
    assert canonical["large_exclusion_reason"] == "documented sensor edge defect"


def test_coerce_policy_reason_without_override_rejected() -> None:
    with pytest.raises(ValidPixelDomainError, match="requires large_exclusion_override=True"):
        coerce_valid_pixel_domain(
            {
                "type": "exclude_top_rows",
                "top_rows": 5,
                "large_exclusion_reason": "orphan reason",
            }
        )


# --- out-of-bounds resolution ---------------------------------------------


def test_resolve_rejects_out_of_bounds_rectangle() -> None:
    with pytest.raises(ValidPixelDomainError, match="out of bounds"):
        resolve_valid_pixel_mask(
            (2048, 2448), {"type": "exclude_xyxy", "xyxy": [1, 1, 3, 5000]}
        )


# --- exclusion-fraction cap ------------------------------------------------


def test_resolve_rejects_over_cap_exclusion() -> None:
    # 200 rows of 2048 == 9.7% > 1% cap.
    with pytest.raises(ValidPixelDomainError, match="max_excluded_fraction"):
        resolve_valid_pixel_mask(
            (2048, 2448), {"type": "exclude_top_rows", "top_rows": 200}
        )


def test_resolve_allows_over_cap_with_override() -> None:
    mask = resolve_valid_pixel_mask(
        (2048, 2448),
        {
            "type": "exclude_top_rows",
            "top_rows": 200,
            "large_exclusion_override": True,
            "large_exclusion_reason": "documented sensor edge defect",
        },
    )
    assert not mask[:200, :].any()
    assert mask[200:, :].all()


# --- resolved provenance record --------------------------------------------


def test_describe_requires_frame_shape_keyword() -> None:
    with pytest.raises(TypeError):
        describe_valid_pixel_domain({"type": "full_frame"})  # type: ignore[call-arg]


def test_describe_full_frame_record() -> None:
    record = describe_valid_pixel_domain(frame_shape=(2048, 2448))
    assert record["type"] == FULL_FRAME
    assert record["frame_shape_hw"] == [2048, 2448]
    assert record["valid_pixel_count"] == 2048 * 2448
    assert record["excluded_pixel_count"] == 0
    assert record["excluded_fraction"] == 0.0
    assert record["max_excluded_fraction"] == MAX_EXCLUDED_FRACTION
    assert record["mask_digest"].startswith("sha256:")
    assert record["large_exclusion_override"] is False


def test_describe_records_resolved_counts() -> None:
    record = describe_valid_pixel_domain(
        frame_shape=(2048, 2448),
        valid_pixel_domain={"type": "exclude_top_rows", "top_rows": 1},
    )
    assert record["excluded_pixel_count"] == 2448
    assert record["valid_pixel_count"] == 2048 * 2448 - 2448
    assert 0 < record["excluded_fraction"] < MAX_EXCLUDED_FRACTION
    assert record["resolved_policy"] == {"type": "exclude_top_rows", "top_rows": 1}


def test_describe_explicit_mask_has_digest() -> None:
    mask = np.ones((7, 9), dtype=bool)
    mask[0, 0] = False
    record = describe_valid_pixel_domain(
        frame_shape=(7, 9), valid_pixel_mask=mask, max_excluded_fraction=1.0
    )
    assert record["type"] == EXPLICIT_MASK
    assert record["frame_shape_hw"] == [7, 9]
    assert record["excluded_pixel_count"] == 1
    assert record["mask_digest"] == valid_pixel_mask_digest(mask)


# --- digest ----------------------------------------------------------------


def test_digest_stable_and_versioned() -> None:
    mask = np.ones((4, 5), dtype=bool)
    digest = valid_pixel_mask_digest(mask)
    assert digest.startswith("sha256:")
    assert digest == valid_pixel_mask_digest(mask.copy())


def test_digest_sensitive_to_shape() -> None:
    a = np.ones((4, 5), dtype=bool)
    b = np.ones((5, 4), dtype=bool)
    assert valid_pixel_mask_digest(a) != valid_pixel_mask_digest(b)


def test_digest_identifies_resolved_mask_not_policy() -> None:
    # Two different policies that resolve to the same mask share a digest.
    m1 = resolve_valid_pixel_mask((2048, 2448), {"type": "exclude_top_rows", "top_rows": 1})
    m2 = resolve_valid_pixel_mask(
        (2048, 2448), {"type": "exclude_xyxy", "xyxy": [0, 0, 2448, 1]}
    )
    assert valid_pixel_mask_digest(m1) == valid_pixel_mask_digest(m2)


def test_resolved_domain_object_fields() -> None:
    resolved = resolve_valid_pixel_domain(
        (2048, 2448), {"type": "exclude_top_rows", "top_rows": 1}
    )
    assert resolved.valid_pixel_count == 2048 * 2448 - 2448
    assert resolved.excluded_pixel_count == 2448
    assert resolved.frame_shape_hw == (2048, 2448)
    assert resolved.large_exclusion_override is False


# --- explicit-mask override channel ----------------------------------------


def test_explicit_mask_over_cap_requires_override() -> None:
    mask = np.ones((100, 100), dtype=bool)
    mask[:5, :] = False  # 5% > 1% cap
    with pytest.raises(ValidPixelDomainError, match="max_excluded_fraction"):
        resolve_valid_pixel_mask((100, 100), valid_pixel_mask=mask)


def test_explicit_mask_override_requires_reason() -> None:
    mask = np.ones((100, 100), dtype=bool)
    mask[:5, :] = False
    with pytest.raises(ValidPixelDomainError, match="explicit_mask_large_exclusion_reason"):
        resolve_valid_pixel_mask(
            (100, 100),
            valid_pixel_mask=mask,
            explicit_mask_large_exclusion_override=True,
        )


def test_explicit_mask_override_with_reason_allows_and_records() -> None:
    mask = np.ones((100, 100), dtype=bool)
    mask[:5, :] = False
    record = describe_valid_pixel_domain(
        frame_shape=(100, 100),
        valid_pixel_mask=mask,
        explicit_mask_large_exclusion_override=True,
        explicit_mask_large_exclusion_reason="documented sensor edge defect",
    )
    assert record["type"] == EXPLICIT_MASK
    assert record["large_exclusion_override"] is True
    assert record["large_exclusion_reason"] == "documented sensor edge defect"
    assert record["excluded_pixel_count"] == 500


def test_explicit_mask_reason_without_override_rejected() -> None:
    mask = np.ones((10, 10), dtype=bool)
    with pytest.raises(ValidPixelDomainError, match="requires"):
        resolve_valid_pixel_mask(
            (10, 10),
            valid_pixel_mask=mask,
            explicit_mask_large_exclusion_reason="oops",
        )


def test_explicit_override_rejected_on_policy_path() -> None:
    with pytest.raises(ValidPixelDomainError, match="applies only to the"):
        resolve_valid_pixel_mask(
            (100, 100),
            {"type": "full_frame"},
            explicit_mask_large_exclusion_override=True,
            explicit_mask_large_exclusion_reason="n/a",
        )


def test_explicit_mask_is_copied_not_shared() -> None:
    mask = np.ones((10, 10), dtype=bool)
    resolved = resolve_valid_pixel_domain(
        (10, 10), valid_pixel_mask=mask, max_excluded_fraction=1.0
    )
    digest_before = resolved.mask_digest
    mask[0, 0] = False  # mutate caller's array after resolution
    assert resolved.mask[0, 0]  # resolved mask unaffected
    assert resolved.mask_digest == digest_before


# --- max_excluded_fraction validation --------------------------------------


@pytest.mark.parametrize("bad", [float("nan"), -0.1, 1.5])
def test_max_excluded_fraction_must_be_in_unit_interval(bad: float) -> None:
    with pytest.raises(ValidPixelDomainError, match="max_excluded_fraction"):
        resolve_valid_pixel_mask((10, 10), {"type": "full_frame"}, max_excluded_fraction=bad)
