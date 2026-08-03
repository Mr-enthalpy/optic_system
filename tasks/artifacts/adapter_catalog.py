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
from .validation_requirements import (
    CAMERA_PROFILE_V1_IDENTITY,
    PUPIL_PROFILE_V1_IDENTITY,
    REQUIRED_CURRENT_WRITER_IDENTITIES,
    REQUIRED_READABLE_IDENTITIES,
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


PROFILE_V1_PROVIDER = SchemaAdapterProvider(
    name="profile_v1",
    identities=frozenset(
        {
            CAMERA_PROFILE_V1_IDENTITY,
            PUPIL_PROFILE_V1_IDENTITY,
        }
    ),
    register=register_profile_v1_adapters,
)

BUILTIN_ADAPTER_PROVIDERS: tuple[SchemaAdapterProvider, ...] = (PROFILE_V1_PROVIDER,)


def build_builtin_schema_registry() -> SchemaAdapterRegistry:
    registry = SchemaAdapterRegistry()
    provider_names: set[str] = set()
    for provider in BUILTIN_ADAPTER_PROVIDERS:
        if provider.name in provider_names:
            raise RuntimeError(f"duplicate adapter provider name {provider.name!r}")
        provider_names.add(provider.name)
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
    provider_names: set[str] = set()
    for provider in providers:
        if provider.name in provider_names:
            raise RuntimeError(f"duplicate adapter provider name {provider.name!r}")
        provider_names.add(provider.name)
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
    declared_identities = frozenset(
        ArtifactIdentity(artifact_type, representation, versions)
        for artifact_type, representation, versions in declared
    )
    if declared_identities != REQUIRED_READABLE_IDENTITIES:
        missing = REQUIRED_READABLE_IDENTITIES - declared_identities
        extra = declared_identities - REQUIRED_READABLE_IDENTITIES
        raise RuntimeError(
            "adapter provider declarations do not match required readable "
            f"identities; missing={sorted(map(repr, missing))}, "
            f"extra={sorted(map(repr, extra))}"
        )
    if not REQUIRED_CURRENT_WRITER_IDENTITIES <= declared_identities:
        missing_writers = REQUIRED_CURRENT_WRITER_IDENTITIES - declared_identities
        raise RuntimeError(
            "current writer identities lack adapters: "
            f"{sorted(map(repr, missing_writers))}"
        )
    owned_manifest_types = {
        identity.artifact_type
        for provider in providers
        for identity in provider.identities
        if identity.representation is ArtifactRepresentation.JSON
    }
    for artifact_type in owned_manifest_types:
        declared_versions = {
            identity.versions.manifest
            for provider in providers
            for identity in provider.identities
            if identity.artifact_type == artifact_type
            and identity.representation is ArtifactRepresentation.JSON
        }
        current = CURRENT_SCHEMA_VERSIONS[artifact_type]
        readable = set(range(MIN_READABLE_SCHEMA_VERSIONS[artifact_type], current + 1))
        if current not in declared_versions:
            raise RuntimeError(
                f"writer current version lacks an adapter for {artifact_type!r}"
            )
        if declared_versions != readable:
            raise RuntimeError(
                f"declared readable versions disagree with compatibility policy "
                f"for {artifact_type!r}"
            )


__all__ = [
    "BUILTIN_ADAPTER_PROVIDERS",
    "PROFILE_V1_PROVIDER",
    "SchemaAdapterProvider",
    "build_builtin_schema_registry",
    "validate_registry_completeness",
]
