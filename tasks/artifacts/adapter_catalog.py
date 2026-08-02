from __future__ import annotations

"""Deterministic, declaration-driven bootstrap for built-in contracts."""

from collections.abc import Callable
from dataclasses import dataclass

from tasks.artifact_versioning import (
    CURRENT_SCHEMA_VERSIONS,
    MIN_READABLE_SCHEMA_VERSIONS,
)
from tasks.profiles.schema_adapters import register_profile_v1_adapters

from .validation import (
    ArtifactIdentity,
    ArtifactRepresentation,
    ArtifactVersionSet,
    SchemaAdapterRegistry,
)


AdapterProviderCallback = Callable[[SchemaAdapterRegistry], None]


@dataclass(frozen=True)
class SchemaAdapterProvider:
    """One provider's complete declared identity set and registration callback."""

    name: str
    identities: frozenset[ArtifactIdentity]
    register: AdapterProviderCallback

    def __post_init__(self) -> None:
        if not self.name or self.name != self.name.strip():
            raise ValueError("provider name must be canonical and non-empty")
        if not self.identities:
            raise ValueError("provider must declare at least one identity")
        if not callable(self.register):
            raise ValueError("provider register callback must be callable")


def _manifest_window_identities(artifact_type: str) -> frozenset[ArtifactIdentity]:
    minimum = MIN_READABLE_SCHEMA_VERSIONS[artifact_type]
    current = CURRENT_SCHEMA_VERSIONS[artifact_type]
    return frozenset(
        ArtifactIdentity(
            artifact_type,
            ArtifactRepresentation.JSON,
            ArtifactVersionSet(manifest=version),
        )
        for version in range(minimum, current + 1)
    )


PROFILE_V1_PROVIDER = SchemaAdapterProvider(
    name="profile_v1",
    identities=(
        _manifest_window_identities("camera_profile")
        | _manifest_window_identities("pupil_profile")
    ),
    register=register_profile_v1_adapters,
)

BUILTIN_ADAPTER_PROVIDERS: tuple[SchemaAdapterProvider, ...] = (
    PROFILE_V1_PROVIDER,
)


def build_builtin_schema_registry() -> SchemaAdapterRegistry:
    registry = SchemaAdapterRegistry()
    for provider in BUILTIN_ADAPTER_PROVIDERS:
        before = frozenset(registry.adapters)
        provider.register(registry)
        after = frozenset(registry.adapters)
        added = after - before
        expected = frozenset(
            (identity.artifact_type, identity.representation, identity.versions)
            for identity in provider.identities
        )
        if added != expected:
            raise RuntimeError(
                f"adapter provider {provider.name!r} registration differs from "
                "its declared identity set"
            )
    validate_registry_completeness(registry, BUILTIN_ADAPTER_PROVIDERS)
    registry.freeze()
    return registry


def validate_registry_completeness(
    registry: SchemaAdapterRegistry,
    providers: tuple[SchemaAdapterProvider, ...] = BUILTIN_ADAPTER_PROVIDERS,
) -> None:
    """Validate completeness from provider declarations, without a type side table."""
    declared: set[tuple[str, ArtifactRepresentation, ArtifactVersionSet]] = set()
    for provider in providers:
        for identity in provider.identities:
            key = (identity.artifact_type, identity.representation, identity.versions)
            if key in declared:
                raise RuntimeError(
                    f"adapter identity declared by multiple providers: {key!r}"
                )
            declared.add(key)
            if registry.get(key) is None:
                raise RuntimeError(
                    f"built-in adapter gap for provider {provider.name!r}: {key!r}"
                )
    undeclared = set(registry.adapters) - declared
    if undeclared:
        raise RuntimeError(
            f"built-in registry contains undeclared adapter identities: "
            f"{sorted(map(repr, undeclared))}"
        )


__all__ = [
    "BUILTIN_ADAPTER_PROVIDERS",
    "PROFILE_V1_PROVIDER",
    "SchemaAdapterProvider",
    "build_builtin_schema_registry",
    "validate_registry_completeness",
]
