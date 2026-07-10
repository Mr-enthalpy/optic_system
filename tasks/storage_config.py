from __future__ import annotations

"""Storage-location config layer: named storage root -> absolute base dir.

The repo never stores data or absolute data paths. Instead, artifacts are
addressed by ``(storage_root, rel_path)`` and resolved to an absolute path
through a machine-specific, gitignored config file (``config/storage.local.yaml``)
or the ``OPTIC_SYSTEM_DATA_ROOT`` environment override.

Design decisions (locked):
- single storage root named ``primary``;
- config file ``config/storage.local.yaml`` (gitignored) + env override;
- no hardcoded drive letters anywhere; hard error if unconfigured.
"""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


DEFAULT_STORAGE_ROOT = "primary"
STORAGE_ROOT_ENV = "OPTIC_SYSTEM_DATA_ROOT"
DEFAULT_CONFIG_RELPATH = "config/storage.local.yaml"


class StorageConfigError(RuntimeError):
    """Raised when storage configuration is missing or invalid."""


def _repo_root() -> Path:
    # tasks/storage_config.py -> repo root is two parents up.
    return Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class StorageConfig:
    """Resolved mapping of named storage roots to absolute base directories."""

    roots: Mapping[str, Path]

    def resolve(self, rel_path: str | Path, *, storage_root: str = DEFAULT_STORAGE_ROOT) -> Path:
        """Resolve ``(storage_root, rel_path)`` to an absolute path.

        ``rel_path`` must be relative; absolute paths are rejected to keep the
        catalog machine-portable.
        """
        base = self._require_root(storage_root)
        rel = Path(rel_path)
        if rel.is_absolute():
            raise StorageConfigError(
                f"rel_path must be relative, got absolute path {rel_path!r}"
            )
        return (base / rel).resolve()

    def relativize(self, abs_path: str | Path, *, storage_root: str = DEFAULT_STORAGE_ROOT) -> str:
        """Express an absolute path as a rel_path under ``storage_root``.

        Raises if the path is not located under the named root. Returns a
        POSIX-style relative string for stable, cross-platform catalog storage.
        """
        base = self._require_root(storage_root)
        resolved = Path(abs_path).resolve()
        try:
            rel = resolved.relative_to(base)
        except ValueError:
            raise StorageConfigError(
                f"path {resolved} is not under storage_root {storage_root!r} ({base})"
            ) from None
        return rel.as_posix()

    def base_dir(self, storage_root: str = DEFAULT_STORAGE_ROOT) -> Path:
        return self._require_root(storage_root)

    def _require_root(self, storage_root: str) -> Path:
        if storage_root not in self.roots:
            raise StorageConfigError(
                f"storage_root {storage_root!r} is not configured; "
                f"known roots: {sorted(self.roots)}"
            )
        return self.roots[storage_root]


def _load_yaml_roots(config_path: Path) -> dict[str, Path]:
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - PyYAML is a declared dep
        raise StorageConfigError("PyYAML is required to read storage config") from exc
    try:
        data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise StorageConfigError(f"failed to read storage config: {exc}") from exc
    if not isinstance(data, dict):
        raise StorageConfigError("storage config root must be a mapping")
    roots_raw = data.get("storage_roots")
    if not isinstance(roots_raw, dict) or not roots_raw:
        raise StorageConfigError(
            "storage config must define a non-empty 'storage_roots' mapping"
        )
    roots: dict[str, Path] = {}
    for name, value in roots_raw.items():
        if not isinstance(value, str) or not value.strip():
            raise StorageConfigError(
                f"storage_roots[{name!r}] must be a non-empty string path"
            )
        base = Path(value.strip()).expanduser()
        if not base.is_absolute():
            raise StorageConfigError(
                f"storage_roots[{name!r}] must be an absolute path, got {value!r}"
            )
        roots[str(name)] = base
    return roots


def load_storage_config(
    *,
    config_path: str | Path | None = None,
    env: Mapping[str, str] | None = None,
) -> StorageConfig:
    """Load storage configuration from env override and/or the local YAML file.

    Resolution order for the ``primary`` root:
    1. ``OPTIC_SYSTEM_DATA_ROOT`` environment variable (highest priority);
    2. ``config/storage.local.yaml`` ``storage_roots`` mapping.

    Raises ``StorageConfigError`` if neither source configures ``primary``.
    """
    env = os.environ if env is None else env
    roots: dict[str, Path] = {}

    if config_path is None:
        config_path = _repo_root() / DEFAULT_CONFIG_RELPATH
    else:
        config_path = Path(config_path)
    if config_path.exists():
        roots.update(_load_yaml_roots(config_path))

    env_root = env.get(STORAGE_ROOT_ENV)
    if env_root and env_root.strip():
        base = Path(env_root.strip()).expanduser()
        if not base.is_absolute():
            raise StorageConfigError(
                f"{STORAGE_ROOT_ENV} must be an absolute path, got {env_root!r}"
            )
        roots[DEFAULT_STORAGE_ROOT] = base

    if DEFAULT_STORAGE_ROOT not in roots:
        raise StorageConfigError(
            "no storage root configured; set the "
            f"{STORAGE_ROOT_ENV} environment variable or create "
            f"{DEFAULT_CONFIG_RELPATH} with a storage_roots.{DEFAULT_STORAGE_ROOT} entry"
        )
    return StorageConfig(roots=roots)
