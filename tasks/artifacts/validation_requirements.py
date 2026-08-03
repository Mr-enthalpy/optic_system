from __future__ import annotations

"""Independent composition requirements for the built-in validation stack."""

from .validation import (
    ArtifactIdentity,
    ArtifactRepresentation,
    ArtifactVersionSet,
)

CAMERA_PROFILE_V1_IDENTITY = ArtifactIdentity(
    "camera_profile",
    ArtifactRepresentation.JSON,
    ArtifactVersionSet(manifest=1),
)

PUPIL_PROFILE_V1_IDENTITY = ArtifactIdentity(
    "pupil_profile",
    ArtifactRepresentation.JSON,
    ArtifactVersionSet(manifest=1),
)

# These sets are an authority independent of provider declarations. Removing a
# provider cannot shrink the requirements that bootstrap must satisfy.
REQUIRED_READABLE_IDENTITIES = frozenset(
    {CAMERA_PROFILE_V1_IDENTITY, PUPIL_PROFILE_V1_IDENTITY}
)

REQUIRED_CURRENT_WRITER_IDENTITIES = frozenset(
    {CAMERA_PROFILE_V1_IDENTITY, PUPIL_PROFILE_V1_IDENTITY}
)

REQUIRED_IDENTIFYING_REPRESENTATIONS = frozenset({ArtifactRepresentation.JSON})


__all__ = [
    "CAMERA_PROFILE_V1_IDENTITY",
    "PUPIL_PROFILE_V1_IDENTITY",
    "REQUIRED_CURRENT_WRITER_IDENTITIES",
    "REQUIRED_IDENTIFYING_REPRESENTATIONS",
    "REQUIRED_READABLE_IDENTITIES",
]
