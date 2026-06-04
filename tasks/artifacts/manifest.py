from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping, TypeVar

import h5py

from .json_io import decode_h5_string, json_dumps_stable


@dataclass(frozen=True)
class ArtifactRef:
    artifact_type: str
    artifact_id: str
    path: str | None = None
    schema_version: int | None = None


@dataclass(frozen=True)
class ArtifactManifestBase:
    artifact_type: str
    schema_version: int
    artifact_id: str
    source_artifacts: tuple[ArtifactRef, ...] = ()
    notes: str | None = None


TManifest = TypeVar("TManifest", bound=ArtifactManifestBase)


def manifest_to_json_dict(manifest: ArtifactManifestBase | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(manifest, ArtifactManifestBase):
        return asdict(manifest)
    return dict(manifest)


def manifest_from_json_dict(data: Mapping[str, Any]) -> ArtifactManifestBase:
    refs = tuple(
        ArtifactRef(
            artifact_type=str(ref["artifact_type"]),
            artifact_id=str(ref["artifact_id"]),
            path=(str(ref["path"]) if ref.get("path") is not None else None),
            schema_version=(
                int(ref["schema_version"])
                if ref.get("schema_version") is not None else None
            ),
        )
        for ref in data.get("source_artifacts", ())
    )
    return ArtifactManifestBase(
        artifact_type=str(data["artifact_type"]),
        schema_version=int(data["schema_version"]),
        artifact_id=str(data["artifact_id"]),
        source_artifacts=refs,
        notes=(str(data["notes"]) if data.get("notes") is not None else None),
    )


def read_manifest_json(source: h5py.Group | h5py.File | str | Path) -> dict[str, Any]:
    if isinstance(source, (str, Path)):
        data = json.loads(Path(source).read_text(encoding="utf-8"))
    else:
        if "metadata/manifest_json" in source:
            raw = source["metadata/manifest_json"][()]
        elif "manifest_json" in source:
            raw = source["manifest_json"][()]
        else:
            raise ValueError("manifest_json not found")
        data = json.loads(decode_h5_string(raw))
    if not isinstance(data, dict):
        raise ValueError("manifest_json must decode to a mapping")
    return data


def write_manifest_json(group: h5py.Group, manifest: ArtifactManifestBase | Mapping[str, Any]) -> None:
    string_dtype = h5py.string_dtype(encoding="utf-8")
    if "manifest_json" in group:
        del group["manifest_json"]
    group.create_dataset(
        "manifest_json",
        data=json_dumps_stable(manifest_to_json_dict(manifest)),
        dtype=string_dtype,
    )
