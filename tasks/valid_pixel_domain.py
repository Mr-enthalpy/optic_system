"""Shared valid-pixel-domain policy resolution.

A *valid pixel domain* marks which camera pixels participate in a measurement
decision (saturation safety, energy centroids, support analysis).  Excluding a
small set of known-bad pixels (stuck/hot edge rows, a defective corner) keeps a
single defective pixel from polluting a full-frame decision.

This module is the single source of truth for the policy vocabulary so that the
exposure-search, sensor-energy-center, and diffraction-support pipelines all
accept and record the same ``valid_pixel_domain`` policies.

This is *valid-pixel-domain-aware calibration and analysis*: bad pixels are
excluded from measurement decisions and provenance is recorded.  It is **not**
end-to-end bad-pixel correction of the scientific PSF data (interpolation /
median replacement of defective pixels in processed PSF products is a separate,
later concern that belongs to the PSF-dictionary layer, not here).

Policy vocabulary (``valid_pixel_domain`` is a mapping with a ``type`` key):

* ``{"type": "full_frame"}`` -- keep every pixel (the default when omitted).
* ``{"type": "exclude_top_rows", "top_rows": N}`` -- drop the top ``N`` rows
  (``N`` must be a positive integer).
* ``{"type": "exclude_xyxy", "xyxy": [x0, y0, x1, y1]}`` -- drop a rectangle
  (``x1 > x0`` and ``y1 > y0``; out-of-bounds rectangles are rejected, not
  clipped).

Any policy may also carry ``large_exclusion_override`` / ``large_exclusion_reason``
to lift the ``MAX_EXCLUDED_FRACTION`` cap for a documented large sensor defect.
The override lifts *only* the exclusion-fraction cap; it never relaxes coordinate
validity, field completeness, or the "at least one valid pixel" requirement.

A caller may instead pass an explicit boolean ``valid_pixel_mask`` array, but
not both a policy and a mask.
"""

from __future__ import annotations

import copy
import hashlib
import struct
from typing import Any

import numpy as np


FULL_FRAME = "full_frame"
EXCLUDE_TOP_ROWS = "exclude_top_rows"
EXCLUDE_XYXY = "exclude_xyxy"
EXPLICIT_MASK = "explicit_mask"

KNOWN_POLICY_TYPES = frozenset({FULL_FRAME, EXCLUDE_TOP_ROWS, EXCLUDE_XYXY})

# Default upper bound on the fraction of the frame a policy may exclude before an
# explicit large_exclusion_override is required.
MAX_EXCLUDED_FRACTION = 0.01

# Provenance record schema version emitted by describe_valid_pixel_domain.
VALID_PIXEL_DOMAIN_RECORD_SCHEMA_VERSION = 1

# Common optional keys any policy may carry.
_OVERRIDE_KEYS = frozenset({"large_exclusion_override", "large_exclusion_reason"})
_ALLOWED_KEYS_BY_TYPE: dict[str, frozenset[str]] = {
    FULL_FRAME: frozenset({"type"}) | _OVERRIDE_KEYS,
    EXCLUDE_TOP_ROWS: frozenset({"type", "top_rows"}) | _OVERRIDE_KEYS,
    EXCLUDE_XYXY: frozenset({"type", "xyxy"}) | _OVERRIDE_KEYS,
}


class ValidPixelDomainError(ValueError):
    """Raised when a valid-pixel-domain policy or mask is invalid."""


def coerce_valid_pixel_domain(value: Any) -> dict[str, Any] | None:
    """Validate and canonicalize a plan-supplied ``valid_pixel_domain`` value.

    Performs all *frame-shape-independent* validation up front (fail-fast at plan
    parse time), and returns a canonical dict containing only known keys.  Returns
    ``None`` when ``value`` is ``None``.
    """
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValidPixelDomainError("valid_pixel_domain must be a mapping or null")

    policy_type = value.get("type", FULL_FRAME)
    if policy_type is None:
        policy_type = FULL_FRAME
    policy_type = str(policy_type)
    if policy_type not in KNOWN_POLICY_TYPES:
        raise ValidPixelDomainError(
            f"unsupported valid_pixel_domain.type: {policy_type!r}; "
            f"known types: {sorted(KNOWN_POLICY_TYPES)}"
        )

    unknown = set(value) - _ALLOWED_KEYS_BY_TYPE[policy_type]
    if unknown:
        raise ValidPixelDomainError(
            f"unknown valid_pixel_domain field(s) for type {policy_type!r}: "
            f"{sorted(unknown)}"
        )

    canonical: dict[str, Any] = {"type": policy_type}

    if policy_type == EXCLUDE_TOP_ROWS:
        if "top_rows" not in value:
            raise ValidPixelDomainError(
                "exclude_top_rows requires an integer top_rows field"
            )
        top_rows = _require_int(value["top_rows"], "valid_pixel_domain.top_rows")
        if top_rows <= 0:
            raise ValidPixelDomainError("valid_pixel_domain.top_rows must be > 0")
        canonical["top_rows"] = top_rows
    elif policy_type == EXCLUDE_XYXY:
        if "xyxy" not in value:
            raise ValidPixelDomainError(
                "exclude_xyxy requires a four-integer xyxy field"
            )
        x0, y0, x1, y1 = _int_quad(value["xyxy"], "valid_pixel_domain.xyxy")
        if x1 <= x0 or y1 <= y0:
            raise ValidPixelDomainError(
                "valid_pixel_domain.xyxy must satisfy x1 > x0 and y1 > y0"
            )
        if x0 < 0 or y0 < 0:
            raise ValidPixelDomainError(
                "valid_pixel_domain.xyxy coordinates must be non-negative"
            )
        canonical["xyxy"] = [x0, y0, x1, y1]

    override, reason = _coerce_override(value)
    if override:
        canonical["large_exclusion_override"] = True
        canonical["large_exclusion_reason"] = reason
    elif "large_exclusion_override" in value or "large_exclusion_reason" in value:
        # Explicit false override: keep it visible but drop empty reason.
        canonical["large_exclusion_override"] = False

    return canonical


class _ResolvedCore:
    """Internal resolution result without the (expensive) mask digest."""

    __slots__ = (
        "mask",
        "frame_shape_hw",
        "resolved_policy",
        "requested_policy",
        "valid_pixel_count",
        "excluded_pixel_count",
        "excluded_fraction",
        "large_exclusion_override_requested",
        "large_exclusion_override_applied",
        "large_exclusion_reason",
        "max_excluded_fraction",
    )

    def __init__(self, **kwargs: Any) -> None:
        for name in self.__slots__:
            setattr(self, name, kwargs[name])


def _resolve_valid_pixel_mask_core(
    shape: tuple[int, int],
    valid_pixel_domain: dict[str, Any] | None,
    valid_pixel_mask: np.ndarray | None,
    *,
    max_excluded_fraction: float,
    explicit_mask_large_exclusion_override: bool,
    explicit_mask_large_exclusion_reason: str | None,
) -> _ResolvedCore:
    """Validate inputs and build the mask + counts, but do NOT hash the mask.

    The SHA-256 digest of a native-sensor mask is expensive (~5 MB per 2048x2448
    frame); it is only needed when writing provenance, so the mask-only and
    per-frame paths use this core and skip it.
    """
    _validate_max_excluded_fraction(max_excluded_fraction)
    h, w = _validate_frame_shape(shape)
    if valid_pixel_domain is not None and valid_pixel_mask is not None:
        raise ValidPixelDomainError(
            "pass either valid_pixel_domain or valid_pixel_mask, not both"
        )

    override_requested = False
    reason: str | None = None
    requested_policy: dict[str, Any] | None = None
    resolved_policy: dict[str, Any]
    explicit_mask = valid_pixel_mask is not None

    if explicit_mask:
        mask = np.array(valid_pixel_mask, dtype=bool, copy=True, order="C")
        if mask.shape != (h, w):
            raise ValidPixelDomainError(
                f"valid_pixel_mask shape {mask.shape} does not match {(h, w)}"
            )
        override_requested, reason = _coerce_explicit_override(
            explicit_mask_large_exclusion_override,
            explicit_mask_large_exclusion_reason,
        )
        resolved_policy = {"type": EXPLICIT_MASK}
    else:
        if (
            explicit_mask_large_exclusion_override
            or explicit_mask_large_exclusion_reason is not None
        ):
            raise ValidPixelDomainError(
                "explicit_mask_large_exclusion_* applies only to the "
                "valid_pixel_mask path; use large_exclusion_override in the policy"
            )
        canonical = coerce_valid_pixel_domain(valid_pixel_domain)
        requested_policy = dict(valid_pixel_domain) if valid_pixel_domain else None
        if not canonical:
            resolved_policy = {"type": FULL_FRAME}
            mask = np.ones((h, w), dtype=bool)
        else:
            override_requested = bool(canonical.get("large_exclusion_override", False))
            reason = canonical.get("large_exclusion_reason")
            resolved_policy = canonical
            mask = _build_mask_from_policy(canonical, h, w)

    valid_count = int(np.count_nonzero(mask))
    total = int(mask.size)
    excluded_count = total - valid_count
    excluded_fraction = excluded_count / total if total else 0.0
    over_cap = excluded_fraction > float(max_excluded_fraction)
    # The override is *applied* only when it was requested and actually needed
    # to pass the cap.  A defensive override on an in-cap policy is recorded as
    # requested-but-not-applied rather than silently claiming it was used.
    override_applied = bool(override_requested and over_cap)

    if valid_count == 0:
        raise ValidPixelDomainError("valid_pixel_domain leaves zero valid pixels")
    if over_cap and not override_requested:
        if explicit_mask:
            hint = (
                "set explicit_mask_large_exclusion_override=True with a non-empty "
                "explicit_mask_large_exclusion_reason"
            )
        else:
            hint = (
                "set large_exclusion_override=True with a non-empty "
                "large_exclusion_reason in valid_pixel_domain"
            )
        raise ValidPixelDomainError(
            f"valid_pixel_domain excludes {excluded_fraction:.4f} of the frame, "
            f"above the max_excluded_fraction cap {float(max_excluded_fraction):.4f}; "
            + hint
        )

    if explicit_mask and override_requested:
        resolved_policy["large_exclusion_override"] = True
        resolved_policy["large_exclusion_reason"] = reason

    return _ResolvedCore(
        mask=mask,
        frame_shape_hw=(h, w),
        resolved_policy=resolved_policy,
        requested_policy=requested_policy,
        valid_pixel_count=valid_count,
        excluded_pixel_count=excluded_count,
        excluded_fraction=excluded_fraction,
        large_exclusion_override_requested=override_requested,
        large_exclusion_override_applied=override_applied,
        large_exclusion_reason=reason,
        max_excluded_fraction=float(max_excluded_fraction),
    )


def resolve_valid_pixel_domain(
    shape: tuple[int, int],
    valid_pixel_domain: dict[str, Any] | None = None,
    valid_pixel_mask: np.ndarray | None = None,
    *,
    max_excluded_fraction: float = MAX_EXCLUDED_FRACTION,
    explicit_mask_large_exclusion_override: bool = False,
    explicit_mask_large_exclusion_reason: str | None = None,
) -> "ResolvedValidPixelDomain":
    """Resolve a policy or explicit mask into a mask plus full provenance.

    Enforces the frame-shape-dependent constraints: out-of-bounds rectangles are
    rejected, at least one valid pixel must remain, and the excluded fraction must
    not exceed ``max_excluded_fraction`` unless an override is set.  For the policy
    path the override lives in the policy dict; for the explicit-mask path use
    ``explicit_mask_large_exclusion_override`` / ``explicit_mask_large_exclusion_reason``.

    This computes the mask digest and is the entry point for writing provenance.
    Callers that only need the mask should use :func:`resolve_valid_pixel_mask`.
    """
    core = _resolve_valid_pixel_mask_core(
        shape,
        valid_pixel_domain,
        valid_pixel_mask,
        max_excluded_fraction=max_excluded_fraction,
        explicit_mask_large_exclusion_override=explicit_mask_large_exclusion_override,
        explicit_mask_large_exclusion_reason=explicit_mask_large_exclusion_reason,
    )
    return ResolvedValidPixelDomain(
        mask=core.mask,
        frame_shape_hw=core.frame_shape_hw,
        resolved_policy=core.resolved_policy,
        requested_policy=core.requested_policy,
        valid_pixel_count=core.valid_pixel_count,
        excluded_pixel_count=core.excluded_pixel_count,
        excluded_fraction=core.excluded_fraction,
        mask_digest=valid_pixel_mask_digest(core.mask),
        large_exclusion_override_requested=core.large_exclusion_override_requested,
        large_exclusion_override_applied=core.large_exclusion_override_applied,
        large_exclusion_reason=core.large_exclusion_reason,
        max_excluded_fraction=core.max_excluded_fraction,
    )


def resolve_valid_pixel_mask(
    shape: tuple[int, int],
    valid_pixel_domain: dict[str, Any] | None = None,
    valid_pixel_mask: np.ndarray | None = None,
    *,
    max_excluded_fraction: float = MAX_EXCLUDED_FRACTION,
    explicit_mask_large_exclusion_override: bool = False,
    explicit_mask_large_exclusion_reason: str | None = None,
) -> np.ndarray:
    """Resolve a ``[H, W]`` boolean valid-pixel mask.

    ``True`` marks a pixel that participates in the decision.  Provide either a
    ``valid_pixel_domain`` policy or an explicit ``valid_pixel_mask`` array, not
    both.  Returns an all-``True`` mask when neither is given.  Does NOT compute
    the mask digest (cheap path for per-frame / per-probe use).
    """
    return _resolve_valid_pixel_mask_core(
        shape,
        valid_pixel_domain,
        valid_pixel_mask,
        max_excluded_fraction=max_excluded_fraction,
        explicit_mask_large_exclusion_override=explicit_mask_large_exclusion_override,
        explicit_mask_large_exclusion_reason=explicit_mask_large_exclusion_reason,
    ).mask


def describe_valid_pixel_domain(
    *,
    frame_shape: tuple[int, int],
    valid_pixel_domain: dict[str, Any] | None = None,
    valid_pixel_mask: np.ndarray | None = None,
    max_excluded_fraction: float = MAX_EXCLUDED_FRACTION,
    explicit_mask_large_exclusion_override: bool = False,
    explicit_mask_large_exclusion_reason: str | None = None,
) -> dict[str, Any]:
    """Return a JSON-serializable resolved provenance record for the domain.

    ``frame_shape`` is required: the record always reflects the actual resolved
    mask (counts, fraction, digest), never just the requested policy text.
    """
    resolved = resolve_valid_pixel_domain(
        frame_shape,
        valid_pixel_domain,
        valid_pixel_mask,
        max_excluded_fraction=max_excluded_fraction,
        explicit_mask_large_exclusion_override=explicit_mask_large_exclusion_override,
        explicit_mask_large_exclusion_reason=explicit_mask_large_exclusion_reason,
    )
    return resolved.to_record()


def valid_pixel_mask_digest(mask: np.ndarray) -> str:
    """Return a stable sha256 digest identifying the resolved boolean mask.

    The digest identifies the *resolved mask*, not the policy text: two different
    policies that resolve to the same mask deliberately produce the same digest.
    A version prefix and fixed-width shape encoding guard against layout ambiguity
    and future serialization changes.
    """
    canonical = np.ascontiguousarray(mask, dtype=np.uint8)
    if canonical.ndim != 2:
        raise ValidPixelDomainError("valid_pixel_mask must be 2D for digesting")
    h, w = int(canonical.shape[0]), int(canonical.shape[1])
    payload = (
        b"optic_system.valid_pixel_mask.v1\0"
        + struct.pack(">II", h, w)
        + canonical.tobytes(order="C")
    )
    return "sha256:" + hashlib.sha256(payload).hexdigest()


class ResolvedValidPixelDomain:
    """Resolved valid-pixel mask plus reproducible provenance."""

    __slots__ = (
        "mask",
        "frame_shape_hw",
        "_resolved_policy",
        "_requested_policy",
        "valid_pixel_count",
        "excluded_pixel_count",
        "excluded_fraction",
        "mask_digest",
        "large_exclusion_override_requested",
        "large_exclusion_override_applied",
        "large_exclusion_reason",
        "max_excluded_fraction",
    )

    def __init__(
        self,
        *,
        mask: np.ndarray,
        frame_shape_hw: tuple[int, int],
        resolved_policy: dict[str, Any],
        requested_policy: dict[str, Any] | None,
        valid_pixel_count: int,
        excluded_pixel_count: int,
        excluded_fraction: float,
        mask_digest: str,
        large_exclusion_override_requested: bool,
        large_exclusion_override_applied: bool,
        large_exclusion_reason: str | None,
        max_excluded_fraction: float,
    ) -> None:
        mask = np.ascontiguousarray(mask, dtype=bool)
        mask.setflags(write=False)
        self.mask = mask
        self.frame_shape_hw = frame_shape_hw
        # Store private deep copies so the frozen mask/digest cannot drift from a
        # caller mutating a policy dict after resolution; the public accessors
        # return fresh copies too.
        self._resolved_policy = copy.deepcopy(resolved_policy)
        self._requested_policy = (
            copy.deepcopy(requested_policy) if requested_policy is not None else None
        )
        self.valid_pixel_count = valid_pixel_count
        self.excluded_pixel_count = excluded_pixel_count
        self.excluded_fraction = excluded_fraction
        self.mask_digest = mask_digest
        self.large_exclusion_override_requested = large_exclusion_override_requested
        self.large_exclusion_override_applied = large_exclusion_override_applied
        self.large_exclusion_reason = large_exclusion_reason
        self.max_excluded_fraction = max_excluded_fraction

    @property
    def resolved_policy(self) -> dict[str, Any]:
        return copy.deepcopy(self._resolved_policy)

    @property
    def requested_policy(self) -> dict[str, Any] | None:
        if self._requested_policy is None:
            return None
        return copy.deepcopy(self._requested_policy)

    def to_record(self) -> dict[str, Any]:
        record: dict[str, Any] = {
            "schema_version": VALID_PIXEL_DOMAIN_RECORD_SCHEMA_VERSION,
            "type": str(self._resolved_policy.get("type")),
            "frame_shape_hw": [int(self.frame_shape_hw[0]), int(self.frame_shape_hw[1])],
            "resolved_policy": copy.deepcopy(self._resolved_policy),
            "valid_pixel_count": int(self.valid_pixel_count),
            "excluded_pixel_count": int(self.excluded_pixel_count),
            "excluded_fraction": float(self.excluded_fraction),
            "max_excluded_fraction": float(self.max_excluded_fraction),
            "mask_digest": self.mask_digest,
            "large_exclusion_override_requested": bool(
                self.large_exclusion_override_requested
            ),
            "large_exclusion_override_applied": bool(
                self.large_exclusion_override_applied
            ),
            "large_exclusion_reason": self.large_exclusion_reason,
        }
        if (
            self._requested_policy is not None
            and self._requested_policy != self._resolved_policy
        ):
            record["requested_policy"] = copy.deepcopy(self._requested_policy)
        return record


def _build_mask_from_policy(canonical: dict[str, Any], h: int, w: int) -> np.ndarray:
    mask = np.ones((h, w), dtype=bool)
    policy_type = canonical["type"]
    if policy_type == FULL_FRAME:
        return mask
    if policy_type == EXCLUDE_TOP_ROWS:
        top_rows = int(canonical["top_rows"])
        if top_rows >= h:
            raise ValidPixelDomainError(
                f"valid_pixel_domain.top_rows {top_rows} covers the whole frame height {h}"
            )
        mask[:top_rows, :] = False
        return mask
    if policy_type == EXCLUDE_XYXY:
        x0, y0, x1, y1 = canonical["xyxy"]
        if x1 > w or y1 > h:
            raise ValidPixelDomainError(
                f"valid_pixel_domain.xyxy {canonical['xyxy']} is out of bounds for "
                f"frame shape {(h, w)}"
            )
        mask[y0:y1, x0:x1] = False
        return mask
    raise ValidPixelDomainError(f"unsupported valid_pixel_domain.type: {policy_type}")


def _validate_max_excluded_fraction(max_excluded_fraction: float) -> None:
    import math

    value = float(max_excluded_fraction)
    if not math.isfinite(value) or value < 0.0 or value > 1.0:
        raise ValidPixelDomainError(
            "max_excluded_fraction must be a finite value in [0, 1]"
        )


def _coerce_explicit_override(
    override: bool,
    reason: str | None,
) -> tuple[bool, str | None]:
    if not isinstance(override, bool):
        raise ValidPixelDomainError(
            "explicit_mask_large_exclusion_override must be a boolean"
        )
    if override:
        if not isinstance(reason, str) or not reason.strip():
            raise ValidPixelDomainError(
                "explicit_mask_large_exclusion_override requires a non-empty "
                "explicit_mask_large_exclusion_reason"
            )
        return True, reason.strip()
    if reason is not None:
        raise ValidPixelDomainError(
            "explicit_mask_large_exclusion_reason requires "
            "explicit_mask_large_exclusion_override=True"
        )
    return False, None


def _coerce_override(value: dict[str, Any]) -> tuple[bool, str | None]:
    override = value.get("large_exclusion_override", False)
    if not isinstance(override, bool):
        raise ValidPixelDomainError(
            "valid_pixel_domain.large_exclusion_override must be a boolean"
        )
    reason = value.get("large_exclusion_reason")
    if override:
        if not isinstance(reason, str) or not reason.strip():
            raise ValidPixelDomainError(
                "large_exclusion_override requires a non-empty large_exclusion_reason"
            )
        return True, reason.strip()
    if reason is not None:
        raise ValidPixelDomainError(
            "large_exclusion_reason requires large_exclusion_override=True"
        )
    return False, None


def _require_int(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidPixelDomainError(f"{name} must be an integer")
    return int(value)


def _validate_frame_shape(shape: Any) -> tuple[int, int]:
    if not isinstance(shape, (list, tuple)) or len(shape) != 2:
        raise ValidPixelDomainError("frame shape must contain two integers")
    h = _require_int(shape[0], "frame_shape[0]")
    w = _require_int(shape[1], "frame_shape[1]")
    if h <= 0 or w <= 0:
        raise ValidPixelDomainError("frame shape dimensions must be positive")
    return h, w


def _int_quad(value: Any, name: str) -> tuple[int, int, int, int]:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise ValidPixelDomainError(f"{name} must contain four integers")
    for item in value:
        if isinstance(item, bool) or not isinstance(item, int):
            raise ValidPixelDomainError(f"{name} must contain four integers")
    return (int(value[0]), int(value[1]), int(value[2]), int(value[3]))
