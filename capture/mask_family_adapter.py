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


class MaskFamilyEmbeddingError(ValueError):
    """Raised when a rendered mask cannot be strictly embedded in LCD space."""


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


@dataclass(frozen=True)
class RenderedPhysicalMask:
    """Full physical LCD mask produced by strict PupilProfile placement."""

    local_mask: np.ndarray
    physical_mask: np.ndarray
    mask_id: str
    mask_hash: str
    family_id: str
    family_version: str
    grid: Mapping[str, Any]
    projection: Mapping[str, Any]
    renderer: Mapping[str, Any]
    pupil_profile: Mapping[str, Any]
    placement: Mapping[str, Any]
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        local = np.array(self.local_mask, copy=True)
        physical = np.array(self.physical_mask, copy=True)
        local.setflags(write=False)
        physical.setflags(write=False)
        object.__setattr__(self, "local_mask", local)
        object.__setattr__(self, "physical_mask", physical)

    def capture_metadata(self) -> dict[str, Any]:
        """Return JSON-friendly physical placement metadata."""
        return {
            "usage_scope": "physical_lcd_embedding",
            "physical_placement_implemented": True,
            "mask_id": self.mask_id,
            "mask_hash": self.mask_hash,
            "family_id": self.family_id,
            "family_version": self.family_version,
            "grid": dict(self.grid),
            "projection": dict(self.projection),
            "renderer": dict(self.renderer),
            **dict(self.pupil_profile),
            "placement": dict(self.placement),
            "placement_window_xyxy": list(self.placement["placement_window_xyxy"]),
            "lcd_shape_hw": list(self.placement["lcd_shape_hw"]),
            "outside_value": self.placement["outside_value"],
            "local_mask_shape_hw": list(self.placement["local_mask_shape_hw"]),
            "physical_mask_shape_hw": list(self.placement["physical_mask_shape_hw"]),
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


def embed_rendered_mask_for_pupil_profile(
    rendered: RenderedCaptureMask,
    pupil_profile: Any,
    *,
    lcd_shape_hw: tuple[int, int],
    outside_value: int | float = 0,
) -> RenderedPhysicalMask:
    """Strictly place a local pupil mask into a full physical LCD array."""
    if not isinstance(rendered, RenderedCaptureMask):
        raise MaskFamilyEmbeddingError("rendered must be a RenderedCaptureMask")

    profile = _normalize_embedding_pupil_profile(pupil_profile)
    local = np.asarray(rendered.mask)
    lcd_h, lcd_w = _normalize_lcd_shape_hw(lcd_shape_hw)
    x0, y0, x1, y1 = _validate_aperture_window(
        profile["aperture_window"],
        lcd_h=lcd_h,
        lcd_w=lcd_w,
    )
    _validate_embed_input(rendered, local, window=(x0, y0, x1, y1))
    outside = _normalize_outside_value(outside_value, local.dtype)

    physical = np.full((lcd_h, lcd_w), outside, dtype=local.dtype)
    physical[y0:y1, x0:x1] = local

    renderer = dict(rendered.renderer)
    renderer["profile_binding"] = "pupil_profile_strict_aperture_window"
    renderer["physical_placement_implemented"] = True
    renderer["capture_gate"] = "requires_pupil_profile_aperture_window"
    placement = {
        "placement_policy": "strict_aperture_window_xyxy",
        "placement_window_xyxy": [x0, y0, x1, y1],
        "lcd_shape_hw": [lcd_h, lcd_w],
        "outside_value": outside,
        "local_mask_shape_hw": [int(local.shape[0]), int(local.shape[1])],
        "physical_mask_shape_hw": [lcd_h, lcd_w],
        "coordinate_frame_policy": "local_pupil_grid_exact_shape",
    }
    metadata = dict(rendered.metadata)
    metadata["physical_embedding_note"] = (
        "Local mask was assigned into the validated PupilProfile aperture "
        "window without shape conversion."
    )
    return RenderedPhysicalMask(
        local_mask=local,
        physical_mask=physical,
        mask_id=rendered.mask_id,
        mask_hash=rendered.mask_hash,
        family_id=rendered.family_id,
        family_version=rendered.family_version,
        grid=rendered.grid,
        projection=rendered.projection,
        renderer=_freeze_mapping(renderer),
        pupil_profile=_freeze_mapping(profile),
        placement=_freeze_mapping(placement),
        metadata=_freeze_mapping(metadata),
    )


def render_and_embed_mask_instance_file_for_pupil_profile(
    path: str | Path,
    pupil_profile: Any,
    *,
    lcd_shape_hw: tuple[int, int],
    backend: str = "numpy",
    outside_value: int | float = 0,
) -> RenderedPhysicalMask:
    """Render one spec and strictly embed it into a full LCD-shaped array."""
    rendered = render_mask_instance_file(path, backend=backend)
    return embed_rendered_mask_for_pupil_profile(
        rendered,
        pupil_profile,
        lcd_shape_hw=lcd_shape_hw,
        outside_value=outside_value,
    )


def render_and_embed_mask_sequence_file_for_pupil_profile(
    path: str | Path,
    pupil_profile: Any,
    *,
    lcd_shape_hw: tuple[int, int],
    backend: str = "numpy",
    outside_value: int | float = 0,
) -> list[RenderedPhysicalMask]:
    """Render a sequence and strictly embed each mask in order."""
    return [
        embed_rendered_mask_for_pupil_profile(
            rendered,
            pupil_profile,
            lcd_shape_hw=lcd_shape_hw,
            outside_value=outside_value,
        )
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


def _normalize_embedding_pupil_profile(value: Any) -> dict[str, Any]:
    if value is None:
        raise MaskFamilyEmbeddingError(
            "physical LCD embedding requires a PupilProfile"
        )
    if hasattr(value, "to_dict"):
        value = value.to_dict()
    if not isinstance(value, Mapping):
        raise MaskFamilyEmbeddingError(
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
        raise MaskFamilyEmbeddingError(
            "pupil_profile is missing required fields: " + ", ".join(missing)
        )
    if data.get("aperture_window") is None:
        raise MaskFamilyEmbeddingError(
            "physical LCD embedding requires PupilProfile.aperture_window; "
            "center/radius-only placement is not implemented"
        )

    subpixel_axis = int(data["subpixel_axis"])
    if subpixel_axis not in {0, 1}:
        raise MaskFamilyEmbeddingError("subpixel_axis must be 0 or 1")
    result = {
        "pupil_profile_id": str(data["pupil_profile_id"]),
        "lcd_coordinate_convention": str(data["lcd_coordinate_convention"]),
        "lcd_display_index": int(data["lcd_display_index"]),
        "subpixel_axis": subpixel_axis,
        "lcd_physical_center": _number_list_for_embedding(
            data["lcd_physical_center"],
            "lcd_physical_center",
            2,
        ),
        "aperture_window": _integer_list_for_embedding(
            data["aperture_window"],
            "aperture_window",
            4,
        ),
    }
    if data.get("lcd_physical_radius") is not None:
        result["lcd_physical_radius"] = float(data["lcd_physical_radius"])
    return result


def _normalize_lcd_shape_hw(value: Any) -> tuple[int, int]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise MaskFamilyEmbeddingError("lcd_shape_hw must be a 2-value tuple")
    if not all(isinstance(item, int) and not isinstance(item, bool) for item in value):
        raise MaskFamilyEmbeddingError("lcd_shape_hw values must be integers")
    h, w = int(value[0]), int(value[1])
    if h <= 0 or w <= 0:
        raise MaskFamilyEmbeddingError("lcd_shape_hw values must be positive")
    return h, w


def _validate_aperture_window(
    value: Any,
    *,
    lcd_h: int,
    lcd_w: int,
) -> tuple[int, int, int, int]:
    x0, y0, x1, y1 = _integer_list_for_embedding(value, "aperture_window", 4)
    if x1 <= x0:
        raise MaskFamilyEmbeddingError("aperture_window requires x1 > x0")
    if y1 <= y0:
        raise MaskFamilyEmbeddingError("aperture_window requires y1 > y0")
    if x0 < 0 or y0 < 0:
        raise MaskFamilyEmbeddingError("aperture_window coordinates must be non-negative")
    if x1 > lcd_w or y1 > lcd_h:
        raise MaskFamilyEmbeddingError("aperture_window is outside lcd_shape_hw")
    return x0, y0, x1, y1


def _validate_embed_input(
    rendered: RenderedCaptureMask,
    local: np.ndarray,
    *,
    window: tuple[int, int, int, int],
) -> None:
    if local.ndim != 2:
        raise MaskFamilyEmbeddingError(
            f"local mask must be 2D, got shape {local.shape}"
        )
    if local.dtype != np.dtype("uint8"):
        raise MaskFamilyEmbeddingError(
            f"physical LCD embedding supports uint8 masks, got {local.dtype}"
        )
    projection_dtype = str(rendered.projection.get("output_dtype", ""))
    if projection_dtype != "uint8":
        raise MaskFamilyEmbeddingError(
            f"physical LCD embedding requires projection output_dtype='uint8', got {projection_dtype!r}"
        )
    frame = str(rendered.grid.get("coordinate_frame", ""))
    if frame not in {"normalized_lcd_pupil", "pixel_index"}:
        raise MaskFamilyEmbeddingError(
            f"unsupported coordinate_frame for physical embedding: {frame!r}"
        )
    grid_shape = rendered.grid.get("shape_hw")
    if list(grid_shape or []) != [int(local.shape[0]), int(local.shape[1])]:
        raise MaskFamilyEmbeddingError(
            "rendered grid shape_hw must match local mask shape"
        )
    x0, y0, x1, y1 = window
    window_h = y1 - y0
    window_w = x1 - x0
    if local.shape[0] != window_h:
        raise MaskFamilyEmbeddingError(
            f"local mask height {local.shape[0]} does not match aperture_window height {window_h}"
        )
    if local.shape[1] != window_w:
        raise MaskFamilyEmbeddingError(
            f"local mask width {local.shape[1]} does not match aperture_window width {window_w}"
        )


def _normalize_outside_value(value: int | float, dtype: np.dtype) -> int | float:
    if dtype != np.dtype("uint8"):
        raise MaskFamilyEmbeddingError(f"unsupported physical mask dtype: {dtype}")
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        raise MaskFamilyEmbeddingError("outside_value must be numeric") from None
    if not np.isfinite(numeric) or not numeric.is_integer():
        raise MaskFamilyEmbeddingError("outside_value must be an integer uint8 value")
    if numeric < 0 or numeric > 255:
        raise MaskFamilyEmbeddingError("outside_value must be in [0, 255] for uint8 masks")
    return int(numeric)


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


def _number_list_for_embedding(value: Any, name: str, length: int) -> list[float]:
    if not isinstance(value, (list, tuple)) or len(value) != length:
        raise MaskFamilyEmbeddingError(f"{name} must contain {length} values")
    try:
        return [float(item) for item in value]
    except (TypeError, ValueError):
        raise MaskFamilyEmbeddingError(f"{name} values must be numeric") from None


def _integer_list_for_embedding(value: Any, name: str, length: int) -> list[int]:
    if not isinstance(value, (list, tuple)) or len(value) != length:
        raise MaskFamilyEmbeddingError(f"{name} must contain {length} integer values")
    result: list[int] = []
    for item in value:
        if not isinstance(item, int) or isinstance(item, bool):
            raise MaskFamilyEmbeddingError(f"{name} values must be integers")
        result.append(int(item))
    return result


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
