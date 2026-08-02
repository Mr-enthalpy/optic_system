from __future__ import annotations

"""Deterministic bootstrap for built-in schema contracts."""

from collections.abc import Callable

from tasks.artifact_versioning import (
    CURRENT_SCHEMA_VERSIONS,
    MIN_READABLE_SCHEMA_VERSIONS,
)
from tasks.profiles.schema_adapters import register_profile_v1_adapters

from .validation import (
    ArtifactRepresentation,
    ArtifactVersionSet,
    SchemaAdapterRegistry,
)


AdapterProvider = Callable[[SchemaAdapterRegistry], None]

BUILTIN_ADAPTER_PROVIDERS: tuple[AdapterProvider, ...] = (
    register_profile_v1_adapters,
)

_BUILTIN_VALIDATED_TYPES = frozenset({"camera_profile", "pupil_profile"})


def build_builtin_schema_registry() -> SchemaAdapterRegistry:
    registry = SchemaAdapterRegistry()
    for provider in BUILTIN_ADAPTER_PROVIDERS:
        provider(registry)
    validate_registry_completeness(registry)
    registry.freeze()
    return registry


def validate_registry_completeness(registry: SchemaAdapterRegistry) -> None:
    """Ensure declared built-in readable windows have exact registered readers."""
    for artifact_type in sorted(_BUILTIN_VALIDATED_TYPES):
        current = CURRENT_SCHEMA_VERSIONS[artifact_type]
        minimum = MIN_READABLE_SCHEMA_VERSIONS[artifact_type]
        for version in range(minimum, current + 1):
            key = (
                artifact_type,
                ArtifactRepresentation.JSON,
                ArtifactVersionSet(manifest=version),
            )
            if registry.get(key) is None:
                raise RuntimeError(
                    f"built-in adapter gap for {artifact_type!r} manifest v{version}"
                )
    for adapter in registry.values():
        if adapter.artifact_type not in _BUILTIN_VALIDATED_TYPES:
            continue
        version = adapter.versions.manifest
        if version is None:
            raise RuntimeError("profile adapter must declare a manifest version")
        minimum = MIN_READABLE_SCHEMA_VERSIONS[adapter.artifact_type]
        current = CURRENT_SCHEMA_VERSIONS[adapter.artifact_type]
        if version < minimum or version > current:
            raise RuntimeError(
                f"adapter version outside declared readable window: {adapter.key!r}"
            )


__all__ = [
    "BUILTIN_ADAPTER_PROVIDERS",
    "build_builtin_schema_registry",
    "validate_registry_completeness",
]
