from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType, SimpleNamespace
from typing import Any, Mapping

import numpy as np


class LcdMaskFamiliesUnavailableError(ImportError):
    """Raised when the optional lcd_mask_families package is unavailable."""


class MaskFamilyProfileError(ValueError):
    """Raised when capture-intended rendering lacks PupilProfile metadata."""


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
    usage_scope: str = "dry_run_profile_unaware"
    pupil_profile: Mapping[str, Any] | None = None

    def capture_metadata(self) -> dict[str, Any]:
        """Return JSON-friendly identity metadata for raw capture/run status."""
        result: dict[str, Any] = {
            "mask_id": self.mask_id,
            "mask_hash": self.mask_hash,
            "family_id": self.family_id,
            "family_version": self.family_version,
            "grid": dict(self.grid),
            "projection": dict(self.projection),
            "renderer": dict(self.renderer),
            "metadata": dict(self.metadata),
            "usage_scope": self.usage_scope,
        }
        if self.pupil_profile is not None:
            result.update(dict(self.pupil_profile))
        return result


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


def render_mask_instance_file_for_pupil_profile(
    path: str | Path,
    pupil_profile: Any,
    *,
    backend: str = "numpy",
) -> RenderedCaptureMask:
    """Render one spec and bind identity metadata to an optic_system PupilProfile.

    This is the capture-intended gate for mask-family specs. It validates that
    the caller supplied effective LCD pupil geometry, but it does not yet
    implement resampling or embedding into a full physical LCD frame.
    """
    profile = _normalize_pupil_profile(pupil_profile)
    rendered = render_mask_instance_file(path, backend=backend)
    return _with_pupil_profile(rendered, profile)


def render_mask_sequence_file_for_pupil_profile(
    path: str | Path,
    pupil_profile: Any,
    *,
    backend: str = "numpy",
) -> list[RenderedCaptureMask]:
    """Render a sequence and bind each mask to an optic_system PupilProfile."""
    profile = _normalize_pupil_profile(pupil_profile)
    return [
        _with_pupil_profile(rendered, profile)
        for rendered in render_mask_sequence_file(path, backend=backend)
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


def _with_pupil_profile(
    rendered: RenderedCaptureMask,
    profile: Mapping[str, Any],
) -> RenderedCaptureMask:
    renderer = dict(rendered.renderer)
    renderer["profile_binding"] = "pupil_profile_metadata_only"
    renderer["physical_placement_implemented"] = False
    renderer["capture_gate"] = "requires_pupil_profile"
    metadata = dict(rendered.metadata)
    metadata["profile_binding_note"] = (
        "PupilProfile identity and geometry are recorded; physical resampling/"
        "embedding into the LCD pupil is not implemented by this adapter yet."
    )
    return RenderedCaptureMask(
        mask=rendered.mask,
        mask_id=rendered.mask_id,
        mask_hash=rendered.mask_hash,
        family_id=rendered.family_id,
        family_version=rendered.family_version,
        grid=rendered.grid,
        projection=rendered.projection,
        renderer=_freeze_mapping(renderer),
        metadata=_freeze_mapping(metadata),
        usage_scope="pupil_profile_metadata_bound",
        pupil_profile=_freeze_mapping(profile),
    )


def _normalize_pupil_profile(value: Any) -> dict[str, Any]:
    if value is None:
        raise MaskFamilyProfileError(
            "lcd_mask_families masks intended for capture require a PupilProfile"
        )
    if hasattr(value, "to_dict"):
        value = value.to_dict()
    if not isinstance(value, Mapping):
        raise MaskFamilyProfileError(
            "pupil_profile must be a PupilProfile or mapping with LCD pupil geometry"
        )
    data = _plain_mapping(value)
    required = (
        "pupil_profile_id",
        "lcd_coordinate_convention",
        "lcd_display_index",
        "subpixel_axis",
        "lcd_physical_center",
    )
    missing = [key for key in required if data.get(key) is None]
    if missing:
        raise MaskFamilyProfileError(
            "pupil_profile is missing required fields: " + ", ".join(missing)
        )
    if data.get("lcd_physical_radius") is None and data.get("aperture_window") is None:
        raise MaskFamilyProfileError(
            "pupil_profile requires lcd_physical_radius or aperture_window"
        )
    result = {
        "pupil_profile_id": str(data["pupil_profile_id"]),
        "lcd_coordinate_convention": str(data["lcd_coordinate_convention"]),
        "lcd_display_index": int(data["lcd_display_index"]),
        "subpixel_axis": int(data["subpixel_axis"]),
        "lcd_physical_center": _number_list(data["lcd_physical_center"], "lcd_physical_center", 2),
    }
    if data.get("lcd_physical_radius") is not None:
        result["lcd_physical_radius"] = float(data["lcd_physical_radius"])
    if data.get("aperture_window") is not None:
        result["aperture_window"] = _number_list(data["aperture_window"], "aperture_window", 4, as_int=True)
    return result


def _number_list(
    value: Any,
    name: str,
    length: int,
    *,
    as_int: bool = False,
) -> list[int] | list[float]:
    if not isinstance(value, (list, tuple)) or len(value) != length:
        raise MaskFamilyProfileError(f"{name} must contain {length} values")
    if as_int:
        return [int(item) for item in value]
    return [float(item) for item in value]


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
