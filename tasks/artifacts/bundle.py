from __future__ import annotations

"""Local artifact-bundle and payload-integrity primitives.

This module deliberately stops at one artifact generation directory.  It can
describe and verify payload inventory, but it does not register artifacts,
choose a current generation, or make scientific-trust decisions.
"""

import hashlib
import json
import os
import re
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from types import MappingProxyType
from typing import Any

import h5py

from tasks.artifact_versioning import (
    NewerSchemaVersionError,
    SchemaCompatibilityError,
    read_schema_version,
)

from .validation import (
    ValidityOutcome,
    ValidityResult,
    check_validity,
    read_validated_manifest_mapping,
)


_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_HASH_CHUNK_BYTES = 1024 * 1024
_JSON_MEDIA_TYPE = "application/json"
_HDF5_MEDIA_TYPE = "application/x-hdf5"
ARTIFACT_BUNDLE_SCHEMA_VERSION = 1


class ArtifactBundleError(ValueError):
    """Raised when a local artifact-bundle record is structurally invalid."""


@dataclass(frozen=True)
class ArtifactLocation:
    """Storage-root-relative location of one artifact generation directory."""

    storage_root: str
    rel_path: str

    def __post_init__(self) -> None:
        if not isinstance(self.storage_root, str) or not self.storage_root.strip():
            raise ArtifactBundleError("storage_root must be a non-empty string")
        object.__setattr__(self, "storage_root", self.storage_root.strip())
        object.__setattr__(
            self,
            "rel_path",
            _canonical_relative_path(self.rel_path, field_name="rel_path"),
        )

    def to_dict(self) -> dict[str, str]:
        """Return the stable serialization form used by future catalog records."""
        return {"storage_root": self.storage_root, "rel_path": self.rel_path}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ArtifactLocation":
        """Build a location after validating its storage-root-relative path."""
        if not isinstance(data, Mapping):
            raise ArtifactBundleError("artifact location must be a mapping")
        return cls(
            storage_root=_required_string(data, "storage_root"),
            rel_path=_required_string(data, "rel_path"),
        )


@dataclass(frozen=True)
class ArtifactPayload:
    """Immutable inventory record for one file beneath a generation directory."""

    rel_path: str
    media_type: str
    size_bytes: int
    sha256: str

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        """Validate this payload inventory record without reading the file."""
        object.__setattr__(
            self,
            "rel_path",
            _canonical_relative_path(self.rel_path, field_name="payload.rel_path"),
        )
        if not isinstance(self.media_type, str) or not self.media_type.strip():
            raise ArtifactBundleError("payload.media_type must be a non-empty string")
        object.__setattr__(self, "media_type", self.media_type.strip())
        if isinstance(self.size_bytes, bool) or not isinstance(self.size_bytes, int):
            raise ArtifactBundleError("payload.size_bytes must be an integer")
        if self.size_bytes < 0:
            raise ArtifactBundleError("payload.size_bytes must be nonnegative")
        if not isinstance(self.sha256, str) or not _SHA256_RE.fullmatch(self.sha256):
            raise ArtifactBundleError(
                "payload.sha256 must use sha256:<64 lowercase hexadecimal characters>"
            )

    def to_dict(self) -> dict[str, Any]:
        """Return the stable payload-inventory serialization."""
        return {
            "rel_path": self.rel_path,
            "media_type": self.media_type,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ArtifactPayload":
        """Deserialize one payload record without accepting implicit coercions."""
        if not isinstance(data, Mapping):
            raise ArtifactBundleError("payload record must be a mapping")
        return cls(
            rel_path=_required_string(data, "rel_path"),
            media_type=_required_string(data, "media_type"),
            size_bytes=_required_int(data, "size_bytes"),
            sha256=_required_string(data, "sha256"),
        )


@dataclass(frozen=True)
class ArtifactBundleManifest:
    """Payload inventory for one immutable artifact generation directory."""

    artifact_id: str
    artifact_type: str
    schema_version: int
    payloads: Mapping[str, ArtifactPayload]
    bundle_schema_version: int = ARTIFACT_BUNDLE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        self.validate()
        payloads = {
            name: payload
            for name, payload in self.payloads.items()
        }
        object.__setattr__(self, "payloads", MappingProxyType(payloads))

    def validate(self) -> None:
        """Validate only the bundle record, without reading payload files."""
        if not isinstance(self.artifact_id, str) or not self.artifact_id.strip():
            raise ArtifactBundleError("artifact_id must be a non-empty string")
        if not isinstance(self.artifact_type, str) or not self.artifact_type.strip():
            raise ArtifactBundleError("artifact_type must be a non-empty string")
        if isinstance(self.bundle_schema_version, bool) or not isinstance(
            self.bundle_schema_version,
            int,
        ):
            raise ArtifactBundleError("bundle_schema_version must be an integer")
        if self.bundle_schema_version != ARTIFACT_BUNDLE_SCHEMA_VERSION:
            raise ArtifactBundleError(
                "bundle_schema_version is not supported by this reader"
            )
        if isinstance(self.schema_version, bool) or not isinstance(
            self.schema_version,
            int,
        ):
            raise ArtifactBundleError("schema_version must be an integer")
        try:
            read_schema_version(
                {"schema_version": self.schema_version},
                self.artifact_type,
            )
        except SchemaCompatibilityError as exc:
            raise ArtifactBundleError(
                "artifact_type or schema_version is not supported"
            ) from exc
        if not isinstance(self.payloads, Mapping) or not self.payloads:
            raise ArtifactBundleError("payloads must be a non-empty mapping")
        payload_paths: dict[str, str] = {}
        for name, payload in self.payloads.items():
            if not isinstance(name, str) or not name.strip():
                raise ArtifactBundleError("payload names must be non-empty strings")
            if not isinstance(payload, ArtifactPayload):
                raise ArtifactBundleError(
                    f"payload {name!r} must be an ArtifactPayload record"
                )
            payload.validate()
            other_role = payload_paths.get(payload.rel_path)
            if other_role is not None:
                raise ArtifactBundleError(
                    "payload rel_path values must be unique across roles: "
                    f"{other_role!r} and {name!r} both reference {payload.rel_path!r}"
                )
            payload_paths[payload.rel_path] = name

    def to_dict(self) -> dict[str, Any]:
        """Serialize the artifact schema and its stable payload inventory."""
        return {
            "bundle_schema_version": self.bundle_schema_version,
            "artifact_id": self.artifact_id,
            "artifact_type": self.artifact_type,
            "schema_version": self.schema_version,
            "payloads": {
                name: self.payloads[name].to_dict()
                for name in sorted(self.payloads)
            },
        }

    def to_json(self, path: str | Path | None = None) -> str:
        """Return JSON and atomically write it when ``path`` is supplied."""
        text = json.dumps(self.to_dict(), indent=2, sort_keys=True) + "\n"
        if path is not None:
            _write_text_atomic(Path(path), text)
        return text

    def write_json_atomic(self, path: str | Path) -> None:
        """Atomically write this manifest using temp-file replacement."""
        _write_text_atomic(Path(path), self.to_json())

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ArtifactBundleManifest":
        """Deserialize one bundle manifest with strict field validation."""
        if not isinstance(data, Mapping):
            raise ArtifactBundleError("bundle manifest JSON root must be a mapping")
        payload_data = data.get("payloads")
        if not isinstance(payload_data, Mapping):
            raise ArtifactBundleError("payloads must be a non-empty mapping")
        payloads: dict[str, ArtifactPayload] = {}
        for name, value in payload_data.items():
            if not isinstance(name, str) or not name.strip():
                raise ArtifactBundleError("payload names must be non-empty strings")
            payloads[name] = ArtifactPayload.from_dict(value)
        return cls(
            bundle_schema_version=_required_int(data, "bundle_schema_version"),
            artifact_id=_required_string(data, "artifact_id"),
            artifact_type=_required_string(data, "artifact_type"),
            schema_version=_required_int(data, "schema_version"),
            payloads=payloads,
        )

    @classmethod
    def load_json(cls, path: str | Path) -> "ArtifactBundleManifest":
        """Load a bundle manifest without resolving its payload locations."""
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except UnicodeError as exc:
            raise ArtifactBundleError(
                "bundle manifest JSON is not valid UTF-8"
            ) from exc
        except OSError as exc:
            raise ArtifactBundleError("bundle manifest could not be read") from exc
        except json.JSONDecodeError as exc:
            raise ArtifactBundleError("bundle manifest JSON could not be parsed") from exc
        return cls.from_dict(data)


def compute_file_sha256(path: str | Path) -> str:
    """Return a streaming, canonical SHA-256 digest for one payload file."""
    digest = hashlib.sha256()
    try:
        with Path(path).open("rb") as source:
            while chunk := source.read(_HASH_CHUNK_BYTES):
                digest.update(chunk)
    except OSError as exc:
        raise ArtifactBundleError("payload file could not be read for SHA-256") from exc
    return f"sha256:{digest.hexdigest()}"


def inspect_payload(
    path: str | Path,
    media_type: str,
    *,
    rel_path: str | None = None,
) -> ArtifactPayload:
    """Build an inventory record from a file without loading it into memory."""
    payload_path = Path(path)
    try:
        size_bytes = payload_path.stat().st_size
    except OSError as exc:
        raise ArtifactBundleError("payload file could not be inspected") from exc
    if not payload_path.is_file():
        raise ArtifactBundleError("payload location must be a regular file")
    return ArtifactPayload(
        rel_path=rel_path if rel_path is not None else payload_path.name,
        media_type=media_type,
        size_bytes=int(size_bytes),
        sha256=compute_file_sha256(payload_path),
    )


def validate_bundle(
    manifest: ArtifactBundleManifest | str | Path,
    generation_dir: str | Path,
) -> ValidityResult:
    """Validate one local bundle and dispatch its fixed ``data`` payload.

    Bundle schema v1 fixes the primary payload role as ``"data"``. It is
    validated using the bundle's declared ``artifact_type``; no validator is
    selected from a filename or media type. A bundle without that role can
    still have a sound inventory, but is ``unsupported`` for full artifact
    validation because no primary payload contract was declared.
    """
    loaded = _load_bundle_for_validation(manifest)
    if isinstance(loaded, ValidityResult):
        return loaded
    bundle = loaded
    artifact_type = bundle.artifact_type
    generation = Path(generation_dir)
    if not generation.exists() or not generation.is_dir():
        return _bundle_result(
            artifact_type,
            ValidityOutcome.UNREADABLE,
            bundle.schema_version,
            "generation_directory_missing",
            "bundle generation directory is unavailable",
        )
    root = generation.resolve()
    resolved_payloads: dict[str, Path] = {}
    normalized_path_roles: dict[str, str] = {}
    physical_payloads: list[tuple[str, Path]] = []
    for name, payload in bundle.payloads.items():
        try:
            candidate = _resolve_payload(root, payload.rel_path)
        except ArtifactBundleError:
            return _bundle_result(
                artifact_type,
                ValidityOutcome.INVALID,
                bundle.schema_version,
                "payload_path_escape",
                "bundle payload path escapes its generation directory",
            )
        resolved_key = os.path.normcase(os.path.normpath(str(candidate)))
        aliased_role = normalized_path_roles.get(resolved_key)
        if aliased_role is not None:
            return _bundle_result(
                artifact_type,
                ValidityOutcome.INVALID,
                bundle.schema_version,
                "payload_role_alias",
                f"payload roles {aliased_role!r} and {name!r} resolve to the same file",
            )
        normalized_path_roles[resolved_key] = name
        if not candidate.is_file():
            return _bundle_result(
                artifact_type,
                ValidityOutcome.UNREADABLE,
                bundle.schema_version,
                "payload_missing",
                f"payload {name!r} is missing or is not a regular file",
            )
        for other_role, other_path in physical_payloads:
            try:
                same_file = candidate.samefile(other_path)
            except OSError:
                same_file = False
            if same_file:
                return _bundle_result(
                    artifact_type,
                    ValidityOutcome.INVALID,
                    bundle.schema_version,
                    "payload_role_alias",
                    f"payload roles {other_role!r} and {name!r} reference the same physical file",
                )
        physical_payloads.append((name, candidate))
        try:
            size_bytes = candidate.stat().st_size
        except OSError:
            return _bundle_result(
                artifact_type,
                ValidityOutcome.UNREADABLE,
                bundle.schema_version,
                "payload_unreadable",
                f"payload {name!r} could not be inspected",
            )
        if int(size_bytes) != payload.size_bytes:
            return _bundle_result(
                artifact_type,
                ValidityOutcome.INVALID,
                bundle.schema_version,
                "payload_size_mismatch",
                f"payload {name!r} size does not match the bundle manifest",
            )
        try:
            digest = compute_file_sha256(candidate)
        except ArtifactBundleError:
            return _bundle_result(
                artifact_type,
                ValidityOutcome.UNREADABLE,
                bundle.schema_version,
                "payload_unreadable",
                f"payload {name!r} could not be hashed",
            )
        if digest != payload.sha256:
            return _bundle_result(
                artifact_type,
                ValidityOutcome.INVALID,
                bundle.schema_version,
                "payload_digest_mismatch",
                f"payload {name!r} SHA-256 does not match the bundle manifest",
            )
        resolved_payloads[name] = candidate

    manifest_sidecar = resolved_payloads.get("manifest_sidecar")
    if manifest_sidecar is not None:
        sidecar_media_result = _validate_declared_payload_media_type(
            bundle,
            payload_name="manifest_sidecar",
            payload=bundle.payloads["manifest_sidecar"],
            path=manifest_sidecar,
            required_media_type=_JSON_MEDIA_TYPE,
        )
        if sidecar_media_result is not None:
            return sidecar_media_result

    primary = resolved_payloads.get("data")
    if primary is None:
        return _bundle_result(
            artifact_type,
            ValidityOutcome.UNSUPPORTED,
            bundle.schema_version,
            "primary_payload_not_declared",
            "bundle does not declare a primary payload for artifact validation",
        )
    primary_media_result = _validate_declared_payload_media_type(
        bundle,
        payload_name="data",
        payload=bundle.payloads["data"],
        path=primary,
    )
    if primary_media_result is not None:
        return primary_media_result
    payload_result = check_validity(artifact_type, primary)
    if payload_result.schema_version != bundle.schema_version:
        if payload_result.ok:
            return _bundle_result(
                artifact_type,
                ValidityOutcome.INVALID,
                bundle.schema_version,
                "payload_schema_version_mismatch",
                "primary payload schema_version does not match the bundle manifest",
            )
        return payload_result
    if not payload_result.ok:
        return payload_result

    if manifest_sidecar is not None:
        sidecar_result = _validate_manifest_sidecar_consistency(
            bundle,
            primary_payload=primary,
            sidecar_payload=manifest_sidecar,
        )
        if sidecar_result is not None:
            return sidecar_result
    return payload_result


def _validate_declared_payload_media_type(
    bundle: ArtifactBundleManifest,
    *,
    payload_name: str,
    payload: ArtifactPayload,
    path: Path,
    required_media_type: str | None = None,
) -> ValidityResult | None:
    """Verify a declared canonical media type against payload bytes.

    This uses the HDF5 signature and strict UTF-8 JSON parsing, never a filename
    suffix. Media type remains inventory metadata only; artifact validation still
    dispatches solely from ``bundle.artifact_type``.
    """
    if required_media_type is not None and payload.media_type != required_media_type:
        return _bundle_result(
            bundle.artifact_type,
            ValidityOutcome.INVALID,
            bundle.schema_version,
            "payload_media_type_mismatch",
            f"payload {payload_name!r} must declare {required_media_type}",
        )
    actual_media_type = _detect_payload_media_type(path)
    if actual_media_type is None:
        return _bundle_result(
            bundle.artifact_type,
            ValidityOutcome.UNREADABLE,
            bundle.schema_version,
            "payload_representation_unreadable",
            f"payload {payload_name!r} is neither readable JSON nor HDF5",
        )
    if payload.media_type != actual_media_type:
        return _bundle_result(
            bundle.artifact_type,
            ValidityOutcome.INVALID,
            bundle.schema_version,
            "payload_media_type_mismatch",
            f"payload {payload_name!r} media_type does not match its representation",
        )
    return None


def _detect_payload_media_type(path: Path) -> str | None:
    """Return the canonical representation media type without using a filename."""
    try:
        if h5py.is_hdf5(path):
            return _HDF5_MEDIA_TYPE
    except OSError:
        return None
    try:
        json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return _JSON_MEDIA_TYPE


def _validate_manifest_sidecar_consistency(
    bundle: ArtifactBundleManifest,
    *,
    primary_payload: Path,
    sidecar_payload: Path,
) -> ValidityResult | None:
    """Require a declared JSON manifest sidecar to equal the primary manifest.

    Payload hashes prove that files have not changed since inventory creation;
    they do not prove that two individually intact files describe the same
    generation.  This comparison closes that gap for artifacts with canonical
    embedded HDF5 manifests and for JSON-primary artifacts.
    """
    primary = read_validated_manifest_mapping(bundle.artifact_type, primary_payload)
    if isinstance(primary, ValidityResult):
        if primary.outcome is ValidityOutcome.UNREADABLE:
            return _bundle_result(
                bundle.artifact_type,
                ValidityOutcome.UNREADABLE,
                bundle.schema_version,
                "primary_manifest_unreadable",
                "primary payload manifest could not be read for sidecar comparison",
            )
        if primary.outcome is ValidityOutcome.UNSUPPORTED:
            return _bundle_result(
                bundle.artifact_type,
                ValidityOutcome.UNSUPPORTED,
                bundle.schema_version,
                "manifest_sidecar_unsupported",
                "primary payload has no canonical manifest for sidecar comparison",
            )
        return _bundle_result(
            bundle.artifact_type,
            ValidityOutcome.INVALID,
            bundle.schema_version,
            "manifest_sidecar_mismatch",
            "primary payload manifest is not structurally valid for sidecar comparison",
        )

    sidecar = read_validated_manifest_mapping(
        bundle.artifact_type,
        sidecar_payload,
        require_json=True,
    )
    if isinstance(sidecar, ValidityResult):
        if sidecar.outcome is ValidityOutcome.UNREADABLE:
            return _bundle_result(
                bundle.artifact_type,
                ValidityOutcome.UNREADABLE,
                bundle.schema_version,
                "manifest_sidecar_unreadable",
                "manifest_sidecar could not be parsed",
            )
        return _bundle_result(
            bundle.artifact_type,
            ValidityOutcome.INVALID,
            bundle.schema_version,
            "manifest_sidecar_mismatch",
            "manifest_sidecar does not satisfy the primary artifact manifest contract",
        )

    primary_mapping, primary_schema_version = primary
    sidecar_mapping, sidecar_schema_version = sidecar
    if (
        primary_schema_version != bundle.schema_version
        or sidecar_schema_version != bundle.schema_version
        or _canonical_manifest_mapping(primary_mapping)
        != _canonical_manifest_mapping(sidecar_mapping)
    ):
        return _bundle_result(
            bundle.artifact_type,
            ValidityOutcome.INVALID,
            bundle.schema_version,
            "manifest_sidecar_mismatch",
            "manifest_sidecar does not match the primary payload embedded manifest",
        )
    return None


def _canonical_manifest_mapping(data: Mapping[str, Any]) -> str:
    """Return stable JSON for exact generation-manifest comparison."""
    return json.dumps(
        data,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _load_bundle_for_validation(
    manifest: ArtifactBundleManifest | str | Path,
) -> ArtifactBundleManifest | ValidityResult:
    if isinstance(manifest, ArtifactBundleManifest):
        try:
            manifest.validate()
        except ArtifactBundleError as exc:
            return _bundle_result(
                manifest.artifact_type,
                ValidityOutcome.INVALID,
                manifest.schema_version,
                "bundle_manifest_invalid",
                str(exc),
            )
        return manifest
    path = Path(manifest)
    if not path.exists():
        return _bundle_result(
            "artifact_bundle",
            ValidityOutcome.UNREADABLE,
            None,
            "bundle_manifest_missing",
            "bundle manifest location does not exist",
        )
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except UnicodeError:
        return _bundle_result(
            "artifact_bundle",
            ValidityOutcome.UNREADABLE,
            None,
            "bundle_manifest_unreadable",
            "bundle manifest JSON is not valid UTF-8",
        )
    except OSError:
        return _bundle_result(
            "artifact_bundle",
            ValidityOutcome.UNREADABLE,
            None,
            "bundle_manifest_unreadable",
            "bundle manifest could not be read",
        )
    except json.JSONDecodeError:
        return _bundle_result(
            "artifact_bundle",
            ValidityOutcome.UNREADABLE,
            None,
            "bundle_manifest_unreadable",
            "bundle manifest JSON could not be parsed",
        )
    artifact_type = raw.get("artifact_type") if isinstance(raw, Mapping) else "artifact_bundle"
    if isinstance(raw, Mapping):
        bundle_schema_version = raw.get("bundle_schema_version")
        if isinstance(bundle_schema_version, bool) or not isinstance(
            bundle_schema_version,
            int,
        ):
            return _bundle_result(
                artifact_type if isinstance(artifact_type, str) else "artifact_bundle",
                ValidityOutcome.INVALID,
                None,
                "bundle_schema_version_invalid",
                "bundle_schema_version must be an explicit integer",
            )
        if bundle_schema_version > ARTIFACT_BUNDLE_SCHEMA_VERSION:
            return _bundle_result(
                artifact_type if isinstance(artifact_type, str) else "artifact_bundle",
                ValidityOutcome.UNSUPPORTED,
                None,
                "bundle_schema_newer_than_supported",
                "bundle manifest envelope requires a newer reader",
            )
        if bundle_schema_version != ARTIFACT_BUNDLE_SCHEMA_VERSION:
            return _bundle_result(
                artifact_type if isinstance(artifact_type, str) else "artifact_bundle",
                ValidityOutcome.INVALID,
                None,
                "bundle_schema_version_invalid",
                "bundle_schema_version is not supported",
            )
    if isinstance(raw, Mapping) and isinstance(artifact_type, str):
        schema_version = raw.get("schema_version")
        if isinstance(schema_version, int) and not isinstance(schema_version, bool):
            try:
                read_schema_version(
                    {"schema_version": schema_version},
                    artifact_type,
                )
            except NewerSchemaVersionError:
                return _bundle_result(
                    artifact_type,
                    ValidityOutcome.UNSUPPORTED,
                    schema_version,
                    "schema_newer_than_supported",
                    "bundle artifact schema requires a newer reader",
                )
            except SchemaCompatibilityError:
                pass
    try:
        return ArtifactBundleManifest.from_dict(raw)
    except ArtifactBundleError as exc:
        return _bundle_result(
            artifact_type if isinstance(artifact_type, str) else "artifact_bundle",
            ValidityOutcome.INVALID,
            None,
            "bundle_manifest_invalid",
            str(exc),
        )


def _bundle_result(
    artifact_type: str,
    outcome: ValidityOutcome,
    schema_version: int | None,
    reason_code: str,
    error: str,
) -> ValidityResult:
    return ValidityResult(
        artifact_type=artifact_type,
        outcome=outcome,
        schema_version=schema_version,
        reason_codes=(reason_code,),
        errors=(error,),
    )


def _resolve_payload(generation_dir: Path, rel_path: str) -> Path:
    candidate = (generation_dir / Path(rel_path)).resolve()
    try:
        candidate.relative_to(generation_dir)
    except ValueError as exc:
        raise ArtifactBundleError("payload path escapes generation directory") from exc
    return candidate


def _canonical_relative_path(value: str | Path, *, field_name: str) -> str:
    if not isinstance(value, (str, Path)):
        raise ArtifactBundleError(f"{field_name} must be a path string")
    raw = str(value).strip()
    if not raw:
        raise ArtifactBundleError(f"{field_name} must be non-empty")
    native = Path(raw)
    windows = PureWindowsPath(raw)
    if (
        native.is_absolute()
        or native.drive
        or native.root
        or windows.is_absolute()
        or windows.drive
        or windows.root
    ):
        raise ArtifactBundleError(f"{field_name} must be relative without a drive or root")
    parts = windows.parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise ArtifactBundleError(
            f"{field_name} must not contain current-directory or parent traversal segments"
        )
    return "/".join(parts)


def _required_string(data: Mapping[str, Any], field_name: str) -> str:
    value = data.get(field_name)
    if not isinstance(value, str) or not value.strip():
        raise ArtifactBundleError(f"{field_name} must be a non-empty string")
    return value.strip()


def _required_int(data: Mapping[str, Any], field_name: str) -> int:
    value = data.get(field_name)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ArtifactBundleError(f"{field_name} must be an integer")
    return value


def _write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temp:
            temp_name = temp.name
            temp.write(text)
            temp.flush()
            os.fsync(temp.fileno())
        os.replace(temp_name, path)
    except OSError as exc:
        raise ArtifactBundleError("bundle manifest could not be written atomically") from exc
    finally:
        if temp_name is not None:
            try:
                Path(temp_name).unlink(missing_ok=True)
            except OSError:
                pass


__all__ = [
    "ARTIFACT_BUNDLE_SCHEMA_VERSION",
    "ArtifactBundleError",
    "ArtifactBundleManifest",
    "ArtifactLocation",
    "ArtifactPayload",
    "compute_file_sha256",
    "inspect_payload",
    "validate_bundle",
]
