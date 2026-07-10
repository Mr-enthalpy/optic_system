"""Shared valid-pixel-domain policy resolution.

A *valid pixel domain* marks which camera pixels participate in a measurement
decision (saturation safety, energy centroids, support analysis).  Excluding a
small set of known-bad pixels (stuck/hot edge rows, a defective corner) keeps a
single defective pixel from polluting a full-frame decision.

This module is the single source of truth for the policy vocabulary so that the
exposure-search, sensor-energy-center, and diffraction-support pipelines all
accept and record the same ``valid_pixel_domain`` policies.

Policy vocabulary (``valid_pixel_domain`` is a mapping with a ``type`` key):

* ``{"type": "full_frame"}`` -- keep every pixel (the default when omitted).
* ``{"type": "exclude_top_rows", "top_rows": N}`` -- drop the top ``N`` rows.
* ``{"type": "exclude_xyxy", "xyxy": [x0, y0, x1, y1]}`` -- drop a rectangle.

A caller may instead pass an explicit boolean ``valid_pixel_mask`` array, but
not both a policy and a mask.
"""

from __future__ import annotations

from typing import Any

import numpy as np


FULL_FRAME = "full_frame"
EXCLUDE_TOP_ROWS = "exclude_top_rows"
EXCLUDE_XYXY = "exclude_xyxy"
EXPLICIT_MASK = "explicit_mask"


class ValidPixelDomainError(ValueError):
    """Raised when a valid-pixel-domain policy or mask is invalid."""


def resolve_valid_pixel_mask(
    shape: tuple[int, int],
    valid_pixel_domain: dict[str, Any] | None = None,
    valid_pixel_mask: np.ndarray | None = None,
) -> np.ndarray:
    """Resolve a ``[H, W]`` boolean valid-pixel mask.

    ``True`` marks a pixel that participates in the decision.  Provide either a
    ``valid_pixel_domain`` policy or an explicit ``valid_pixel_mask`` array, not
    both.  Returns an all-``True`` mask when neither is given.
    """
    h, w = int(shape[0]), int(shape[1])
    if valid_pixel_domain is not None and valid_pixel_mask is not None:
        raise ValidPixelDomainError(
            "pass either valid_pixel_domain or valid_pixel_mask, not both"
        )

    if valid_pixel_mask is not None:
        mask = np.asarray(valid_pixel_mask, dtype=bool)
        if mask.shape != (h, w):
            raise ValidPixelDomainError(
                f"valid_pixel_mask shape {mask.shape} does not match {(h, w)}"
            )
        if not np.any(mask):
            raise ValidPixelDomainError("valid_pixel_mask leaves zero valid pixels")
        return mask

    mask = np.ones((h, w), dtype=bool)
    if not valid_pixel_domain:
        return mask

    policy_type = str(valid_pixel_domain.get("type") or FULL_FRAME)
    if policy_type == FULL_FRAME:
        return mask
    if policy_type == EXCLUDE_TOP_ROWS:
        top_rows = int(valid_pixel_domain.get("top_rows", 0))
        if top_rows < 0:
            raise ValidPixelDomainError("valid_pixel_domain.top_rows must be non-negative")
        if top_rows > 0:
            mask[:top_rows, :] = False
    elif policy_type == EXCLUDE_XYXY:
        x0, y0, x1, y1 = _int_quad(valid_pixel_domain.get("xyxy"), "valid_pixel_domain.xyxy")
        x0 = max(0, min(w, x0))
        x1 = max(0, min(w, x1))
        y0 = max(0, min(h, y0))
        y1 = max(0, min(h, y1))
        if x1 > x0 and y1 > y0:
            mask[y0:y1, x0:x1] = False
    else:
        raise ValidPixelDomainError(f"unsupported valid_pixel_domain.type: {policy_type}")

    if not np.any(mask):
        raise ValidPixelDomainError("valid_pixel_domain leaves zero valid pixels")
    return mask


def coerce_valid_pixel_domain(value: Any) -> dict[str, Any] | None:
    """Validate a plan-supplied ``valid_pixel_domain`` value, returning it as a dict."""
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValidPixelDomainError("valid_pixel_domain must be a mapping or null")
    return dict(value)


def describe_valid_pixel_domain(
    valid_pixel_domain: dict[str, Any] | None = None,
    valid_pixel_mask: np.ndarray | None = None,
) -> dict[str, Any]:
    """Return a JSON-serializable provenance record for the resolved domain."""
    if valid_pixel_domain is not None:
        return dict(valid_pixel_domain)
    if valid_pixel_mask is not None:
        m = np.asarray(valid_pixel_mask)
        return {"type": EXPLICIT_MASK, "shape_hw": [int(m.shape[0]), int(m.shape[1])]}
    return {"type": FULL_FRAME}


def _int_quad(value: Any, name: str) -> tuple[int, int, int, int]:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise ValidPixelDomainError(f"{name} must contain four integers")
    return (int(value[0]), int(value[1]), int(value[2]), int(value[3]))
