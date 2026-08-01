from __future__ import annotations

import re


class ArtifactIdentityError(ValueError):
    pass


_ARTIFACT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")


def validate_artifact_id(value: object, field: str = "artifact_id") -> str:
    """Return one canonical artifact ID or reject path-like/location values."""
    if not isinstance(value, str) or not _ARTIFACT_ID_RE.fullmatch(value):
        raise ArtifactIdentityError(
            f"{field} must be a canonical artifact ID, not a path"
        )
    return value


__all__ = ["ArtifactIdentityError", "validate_artifact_id"]
