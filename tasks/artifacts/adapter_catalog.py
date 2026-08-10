from __future__ import annotations

"""Built-in schema registrations for the small validation layer."""

from tasks.profiles.schema_adapters import register_profile_v1_adapters

from .validation import SchemaAdapterRegistry


def build_builtin_schema_registry() -> SchemaAdapterRegistry:
    registry = SchemaAdapterRegistry()
    register_profile_v1_adapters(registry)
    registry.freeze()
    return registry


__all__ = [
    "build_builtin_schema_registry",
]
