from __future__ import annotations

import numpy as np
import pytest

from tasks.valid_pixel_domain import (
    EXPLICIT_MASK,
    FULL_FRAME,
    ValidPixelDomainError,
    coerce_valid_pixel_domain,
    describe_valid_pixel_domain,
    resolve_valid_pixel_mask,
)


def test_resolve_full_frame_keeps_every_pixel() -> None:
    mask = resolve_valid_pixel_mask((4, 5))
    assert mask.shape == (4, 5)
    assert mask.dtype == bool
    assert mask.all()

    mask2 = resolve_valid_pixel_mask((4, 5), {"type": "full_frame"})
    assert mask2.all()


def test_resolve_exclude_top_rows() -> None:
    mask = resolve_valid_pixel_mask((4, 5), {"type": "exclude_top_rows", "top_rows": 2})
    assert not mask[:2, :].any()
    assert mask[2:, :].all()


def test_resolve_exclude_xyxy_clamps_and_excludes_rectangle() -> None:
    mask = resolve_valid_pixel_mask((6, 6), {"type": "exclude_xyxy", "xyxy": [1, 1, 3, 4]})
    assert not mask[1:4, 1:3].any()
    assert mask[0, 0]
    assert mask[5, 5]


def test_resolve_explicit_mask_validates_shape_and_nonempty() -> None:
    good = np.ones((3, 3), dtype=bool)
    good[0, 0] = False
    assert resolve_valid_pixel_mask((3, 3), valid_pixel_mask=good)[0, 0] == False  # noqa: E712

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


def test_resolve_rejects_domain_that_empties_frame() -> None:
    with pytest.raises(ValidPixelDomainError, match="zero valid pixels"):
        resolve_valid_pixel_mask((4, 5), {"type": "exclude_top_rows", "top_rows": 4})


def test_resolve_rejects_unknown_policy_type() -> None:
    with pytest.raises(ValidPixelDomainError, match="unsupported valid_pixel_domain.type"):
        resolve_valid_pixel_mask((4, 5), {"type": "exclude_moon"})


def test_describe_records_policy_or_mask_provenance() -> None:
    assert describe_valid_pixel_domain() == {"type": FULL_FRAME}
    policy = {"type": "exclude_top_rows", "top_rows": 8}
    assert describe_valid_pixel_domain(policy) == policy
    record = describe_valid_pixel_domain(valid_pixel_mask=np.ones((7, 9), dtype=bool))
    assert record == {"type": EXPLICIT_MASK, "shape_hw": [7, 9]}


def test_coerce_valid_pixel_domain() -> None:
    assert coerce_valid_pixel_domain(None) is None
    assert coerce_valid_pixel_domain({"type": "full_frame"}) == {"type": "full_frame"}
    with pytest.raises(ValidPixelDomainError, match="must be a mapping"):
        coerce_valid_pixel_domain([1, 2, 3])
