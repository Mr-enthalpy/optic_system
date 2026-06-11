from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType, SimpleNamespace
from typing import Any, Mapping

import numpy as np


class LcdMaskFamiliesUnavailableError(ImportError):
    """Raised when the optional lcd_mask_families package is unavailable."""


@dataclass(frozen=True)
class RenderedCaptureMask:
    """optic_system-local representation of a rendered external mask spec."""

    mask: np.ndarray
    mask_id: str
    mask_hash: str
    family_id: str
    family_version: str
    grid: Mapping[str, Any]
    projection: Mapping[str, Any]
    renderer: Mapping[str, Any]
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def capture_metadata(self) -> dict[str, Any]:
        """Return JSON-friendly identity metadata for raw capture/run status."""
        return {
            "mask_id": self.mask_id,
            "mask_hash": self.mask_hash,
            "family_id": self.family_id,
            "family_version": self.family_version,
            "grid": dict(self.grid),
            "projection": dict(self.projection),
            "renderer": dict(self.renderer),
            "metadata": dict(self.metadata),
        }


def is_lcd_mask_families_available() -> bool:
    """Return True when the optional mask-family package can be imported."""
    try:
        _load_api()
    except LcdMaskFamiliesUnavailableError:
        return False
    return True


def render_mask_instance_file(path: str | Path, *, backend: str = "numpy") -> RenderedCaptureMask:
    """Render one lcd_mask_families v0.1 mask instance spec file."""
    api = _load_api()
    spec_path = Path(path)
    spec = api.load_mask_instance_spec(spec_path)
    rendered = api.render_mask_instance(spec, backend=backend)
    return _to_capture_mask(
        rendered,
        api=api,
        backend=backend,
        source_spec_path=spec_path,
    )


def render_mask_sequence_file(path: str | Path, *, backend: str = "numpy") -> list[RenderedCaptureMask]:
    """Render an lcd_mask_families v0.1 mask sequence spec file in order."""
    api = _load_api()
    spec_path = Path(path)
    sequence = api.load_mask_sequence_spec(spec_path)
    rendered = api.render_mask_sequence(sequence, backend=backend)
    return [
        _to_capture_mask(item, api=api, backend=backend, source_spec_path=spec_path)
        for item in rendered
    ]


def _load_api() -> SimpleNamespace:
    try:
        module = importlib.import_module("lcd_mask_families")
    except ModuleNotFoundError as exc:
        if exc.name == "lcd_mask_families":
            raise LcdMaskFamiliesUnavailableError(
                "Optional dependency 'lcd_mask_families' is not installed. "
                "Install the v0.1 package, for example with "
                "'pip install -e C:\\Users\\hanni\\PycharmProjects\\lcd_mask_families', "
                "before using capture.mask_family_adapter."
            ) from exc
        raise

    required = (
        "CONTRACT_VERSION",
        "__version__",
        "load_mask_instance_spec",
        "load_mask_sequence_spec",
        "render_mask_instance",
        "render_mask_sequence",
    )
    missing = [name for name in required if not hasattr(module, name)]
    if missing:
        raise LcdMaskFamiliesUnavailableError(
            "Installed 'lcd_mask_families' does not expose the expected v0.1 "
            f"public API: missing {', '.join(missing)}"
        )
    if str(module.CONTRACT_VERSION) != "lcd_mask_families.v0.1":
        raise LcdMaskFamiliesUnavailableError(
            "Unsupported lcd_mask_families contract version "
            f"{module.CONTRACT_VERSION!r}; expected 'lcd_mask_families.v0.1'."
        )
    return SimpleNamespace(
        contract_version=str(module.CONTRACT_VERSION),
        package_version=str(module.__version__),
        load_mask_instance_spec=module.load_mask_instance_spec,
        load_mask_sequence_spec=module.load_mask_sequence_spec,
        render_mask_instance=module.render_mask_instance,
        render_mask_sequence=module.render_mask_sequence,
    )


def _to_capture_mask(
    rendered: Any,
    *,
    api: SimpleNamespace,
    backend: str,
    source_spec_path: Path,
) -> RenderedCaptureMask:
    mask = np.asarray(rendered.mask)
    renderer = {
        "name": "lcd_mask_families",
        "contract_version": api.contract_version,
        "package_version": api.package_version,
        "backend": backend,
        "adapter": "optic_system.capture.mask_family_adapter",
        "adapter_role": "experimental_handoff_consumer",
    }
    metadata = _plain_mapping(getattr(rendered, "metadata", {}))
    metadata["source_spec_path"] = str(source_spec_path)
    metadata["source_spec_uri"] = source_spec_path.resolve().as_uri()
    return RenderedCaptureMask(
        mask=mask,
        mask_id=str(rendered.mask_id),
        mask_hash=str(rendered.hash),
        family_id=str(rendered.family_id),
        family_version=str(rendered.family_version),
        grid=_freeze_mapping(_to_dict(rendered.grid)),
        projection=_freeze_mapping(_to_dict(rendered.projection)),
        renderer=_freeze_mapping(renderer),
        metadata=_freeze_mapping(metadata),
    )


def _to_dict(value: Any) -> dict[str, Any]:
    if hasattr(value, "to_dict"):
        value = value.to_dict()
    return _plain_mapping(value)


def _plain_mapping(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        return dict(value)
    return {str(key): _plain_value(val) for key, val in value.items()}


def _plain_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return _plain_mapping(value)
    if isinstance(value, tuple):
        return [_plain_value(item) for item in value]
    if isinstance(value, list):
        return [_plain_value(item) for item in value]
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, np.ndarray):
        return value.tolist()
    return value


def _freeze_mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    return MappingProxyType(dict(value))
