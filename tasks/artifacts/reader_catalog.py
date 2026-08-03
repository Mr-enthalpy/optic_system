from __future__ import annotations

"""Declarative composition root for representation readers."""

from collections.abc import Callable, Iterable
from dataclasses import dataclass

from .validation import (
    ArtifactRepresentation,
    RepresentationReader,
    RepresentationReaderRegistry,
    _HDF5ProbeReader,
    _JSONRepresentationReader,
)
from .validation_requirements import REQUIRED_IDENTIFYING_REPRESENTATIONS

ReaderFactory = Callable[[], RepresentationReader]


@dataclass(frozen=True)
class RepresentationReaderProvider:
    name: str
    representation: ArtifactRepresentation
    build: ReaderFactory

    def __post_init__(self) -> None:
        if not self.name or self.name != self.name.strip():
            raise ValueError("reader provider name must be canonical and non-empty")
        if not isinstance(self.representation, ArtifactRepresentation):
            raise ValueError("reader provider representation is invalid")
        if not callable(self.build):
            raise ValueError("reader provider factory must be callable")


HDF5_SIGNATURE_PROVIDER = RepresentationReaderProvider(
    name="hdf5_signature_sentinel",
    representation=ArtifactRepresentation.HDF5,
    build=_HDF5ProbeReader,
)

JSON_READER_PROVIDER = RepresentationReaderProvider(
    name="json",
    representation=ArtifactRepresentation.JSON,
    build=_JSONRepresentationReader,
)

BUILTIN_READER_PROVIDERS: tuple[RepresentationReaderProvider, ...] = (
    HDF5_SIGNATURE_PROVIDER,
    JSON_READER_PROVIDER,
)


def build_representation_reader_registry(
    providers: Iterable[RepresentationReaderProvider],
) -> RepresentationReaderRegistry:
    registry = RepresentationReaderRegistry()
    names: set[str] = set()
    for provider in providers:
        if provider.name in names:
            raise RuntimeError(f"duplicate reader provider name {provider.name!r}")
        names.add(provider.name)
        reader = provider.build()
        if reader.representation is not provider.representation:
            raise RuntimeError(
                f"reader provider {provider.name!r} returned the wrong representation"
            )
        registry.register(reader)
    installed_identifying_representations = frozenset(
        reader.representation
        for reader in registry.readers
        if reader.identifies_representation
    )
    missing = (
        REQUIRED_IDENTIFYING_REPRESENTATIONS - installed_identifying_representations
    )
    if missing:
        raise RuntimeError(
            "required identifying representation readers are missing: "
            f"{sorted(item.value for item in missing)}"
        )
    registry.freeze()
    return registry


def build_builtin_representation_reader_registry() -> RepresentationReaderRegistry:
    return build_representation_reader_registry(BUILTIN_READER_PROVIDERS)


__all__ = [
    "BUILTIN_READER_PROVIDERS",
    "HDF5_SIGNATURE_PROVIDER",
    "JSON_READER_PROVIDER",
    "RepresentationReaderProvider",
    "build_builtin_representation_reader_registry",
    "build_representation_reader_registry",
]
