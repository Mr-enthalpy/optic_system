from __future__ import annotations

import pytest

from tasks.storage_config import (
    DEFAULT_STORAGE_ROOT,
    STORAGE_ROOT_ENV,
    StorageConfig,
    StorageConfigError,
    load_storage_config,
)


def _write_config(tmp_path, base_dir):
    config = tmp_path / "storage.local.yaml"
    config.write_text(
        "storage_roots:\n"
        f"  {DEFAULT_STORAGE_ROOT}: {base_dir}\n",
        encoding="utf-8",
    )
    return config


def test_load_from_yaml(tmp_path):
    base = tmp_path / "data_root"
    base.mkdir()
    config = _write_config(tmp_path, base)

    cfg = load_storage_config(config_path=config, env={})

    assert cfg.base_dir() == base


def test_env_override_takes_priority(tmp_path):
    yaml_base = tmp_path / "yaml_root"
    yaml_base.mkdir()
    env_base = tmp_path / "env_root"
    env_base.mkdir()
    config = _write_config(tmp_path, yaml_base)

    cfg = load_storage_config(
        config_path=config, env={STORAGE_ROOT_ENV: str(env_base)}
    )

    assert cfg.base_dir() == env_base


def test_env_only_without_config(tmp_path):
    env_base = tmp_path / "env_only"
    env_base.mkdir()

    cfg = load_storage_config(
        config_path=tmp_path / "missing.yaml", env={STORAGE_ROOT_ENV: str(env_base)}
    )

    assert cfg.base_dir() == env_base


def test_unconfigured_raises(tmp_path):
    with pytest.raises(StorageConfigError):
        load_storage_config(config_path=tmp_path / "missing.yaml", env={})


def test_relative_env_root_rejected(tmp_path):
    with pytest.raises(StorageConfigError):
        load_storage_config(
            config_path=tmp_path / "missing.yaml",
            env={STORAGE_ROOT_ENV: "relative/path"},
        )


def test_resolve_and_relativize_roundtrip(tmp_path):
    base = tmp_path / "root"
    cfg = StorageConfig(roots={DEFAULT_STORAGE_ROOT: base})

    resolved = cfg.resolve("survey/run_001.h5")

    assert resolved == (base / "survey" / "run_001.h5").resolve()
    assert cfg.relativize(resolved) == "survey/run_001.h5"


def test_resolve_rejects_absolute_rel_path(tmp_path):
    cfg = StorageConfig(roots={DEFAULT_STORAGE_ROOT: tmp_path})

    with pytest.raises(StorageConfigError):
        cfg.resolve(str(tmp_path / "abs.h5"))


@pytest.mark.parametrize("rel_path", ["../escape.h5", "a/../../escape.h5"])
def test_resolve_rejects_parent_escape(tmp_path, rel_path):
    base = tmp_path / "root"
    base.mkdir()
    cfg = StorageConfig(roots={DEFAULT_STORAGE_ROOT: base})

    with pytest.raises(StorageConfigError, match="escapes storage_root"):
        cfg.resolve(rel_path)


def test_resolve_rejects_windows_drive_qualified_rel_path(tmp_path):
    cfg = StorageConfig(roots={DEFAULT_STORAGE_ROOT: tmp_path})

    with pytest.raises(StorageConfigError, match="without a drive or root"):
        cfg.resolve(r"C:\\escape.h5")


def test_direct_constructor_rejects_relative_root():
    with pytest.raises(StorageConfigError, match="must be an absolute path"):
        StorageConfig(roots={DEFAULT_STORAGE_ROOT: "relative/root"})


def test_direct_constructor_normalizes_and_freezes_roots(tmp_path):
    base = tmp_path / "root"
    base.mkdir()
    cfg = StorageConfig(roots={DEFAULT_STORAGE_ROOT: base / "."})

    assert cfg.base_dir() == base.resolve()
    with pytest.raises(TypeError):
        cfg.roots["other"] = tmp_path / "other"


def test_named_secondary_root_is_supported(tmp_path):
    primary = tmp_path / "primary"
    secondary = tmp_path / "secondary"
    cfg = StorageConfig(
        roots={DEFAULT_STORAGE_ROOT: primary, "secondary": secondary}
    )

    assert cfg.resolve("artifact.h5", storage_root="secondary") == (
        secondary / "artifact.h5"
    ).resolve()


def test_resolve_unknown_root(tmp_path):
    cfg = StorageConfig(roots={DEFAULT_STORAGE_ROOT: tmp_path})

    with pytest.raises(StorageConfigError):
        cfg.resolve("x.h5", storage_root="secondary")


def test_relativize_outside_root(tmp_path):
    base = tmp_path / "root"
    base.mkdir()
    cfg = StorageConfig(roots={DEFAULT_STORAGE_ROOT: base})

    with pytest.raises(StorageConfigError):
        cfg.relativize(tmp_path / "elsewhere" / "x.h5")
