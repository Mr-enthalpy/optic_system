from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from .illumination import (
    IlluminationSpec,
    IlluminationSpecError,
    illumination_nominal_wavelength_nm,
    normalize_illumination_spec,
)


class CapturePlanError(ValueError):
    pass


@dataclass
class CameraCaptureConfig:
    frames_per_capture: int = 1
    average_burst: bool = True
    exposure_us: float | None = None
    gain_db: float | None = None
    frame_extent: dict[str, Any] | None = None
    trigger_mode: str | None = None

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> CameraCaptureConfig:
        if "roi" in d or "camera_roi" in d:
            raise CapturePlanError(
                "camera.roi / camera.camera_roi are no longer supported; "
                "use camera.frame_extent"
            )
        extent_value = d.get("frame_extent")
        return cls(
            frames_per_capture=int(d.get("frames_per_capture", 1)),
            average_burst=bool(d.get("average_burst", True)),
            exposure_us=_optional_float(d.get("exposure_us")),
            gain_db=_optional_float(d.get("gain_db")),
            frame_extent=_optional_camera_frame_extent(extent_value),
            trigger_mode=_optional_str(d.get("trigger_mode")),
        )

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "frames_per_capture": self.frames_per_capture,
            "average_burst": self.average_burst,
        }
        if self.exposure_us is not None:
            result["exposure_us"] = self.exposure_us
        if self.gain_db is not None:
            result["gain_db"] = self.gain_db
        if self.frame_extent is not None:
            result["frame_extent"] = dict(self.frame_extent)
        if self.trigger_mode is not None:
            result["trigger_mode"] = self.trigger_mode
        return result


@dataclass
class LCDMaskEntry:
    mask_id: str
    path: str | None = None
    array: np.ndarray | None = None
    family_id: str | None = None
    family_params: dict[str, Any] | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> LCDMaskEntry:
        mask_id = _require_str(d, "mask_id")
        array = d.get("array")
        if array is not None and not isinstance(array, np.ndarray):
            array = np.asarray(array, dtype=np.uint8)
        return cls(
            mask_id=mask_id,
            path=_optional_str(d.get("path")),
            array=array,
            family_id=_optional_str(d.get("family_id")),
            family_params=_optional_dict(d.get("family_params")),
            extra=_optional_dict(d.get("extra")) or {},
        )

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"mask_id": self.mask_id}
        if self.path is not None:
            result["path"] = self.path
        if self.family_id is not None:
            result["family_id"] = self.family_id
        if self.family_params is not None:
            result["family_params"] = self.family_params
        if self.extra:
            result["extra"] = self.extra
        return result


@dataclass
class IlluminationEntry:
    """Capture-plan row entry carrying an explicit illumination spec."""

    illumination: IlluminationSpec
    grating: int | None = None
    settle_ms: int = 2000
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def nominal_wavelength_nm(self) -> float:
        return illumination_nominal_wavelength_nm(self.illumination)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> IlluminationEntry:
        if "wavelength_nm" in d:
            raise CapturePlanError(
                "wavelength_nm compatibility input is no longer supported; use illumination"
            )
        try:
            spec = normalize_illumination_spec(d)
        except IlluminationSpecError as exc:
            raise CapturePlanError(str(exc)) from exc
        return cls(
            illumination=spec,
            grating=_optional_int(d.get("grating")),
            settle_ms=int(d.get("settle_ms", 2000)),
            extra=_optional_dict(d.get("extra")) or {},
        )

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "settle_ms": self.settle_ms,
            "illumination": self.illumination.to_dict(),
        }
        if self.grating is not None:
            result["grating"] = self.grating
        if self.extra:
            result["extra"] = self.extra
        return result


@dataclass
class CapturePlan:
    plan_id: str
    requires: dict[str, Any] = field(default_factory=dict)
    wavelengths: list[IlluminationEntry] = field(default_factory=list)
    masks: list[LCDMaskEntry] = field(default_factory=list)
    camera: CameraCaptureConfig = field(default_factory=CameraCaptureConfig)
    lcd_settle_ms: int = 500
    output_path: str = ""
    store_burst: bool = False
    notes: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> CapturePlan:
        plan_id = _require_str(d, "plan_id")
        requires = _optional_dict(d.get("requires")) or {}
        wavelengths = [
            IlluminationEntry.from_dict(w)
            for w in _require_list(d, "wavelengths")
        ]
        masks = [
            LCDMaskEntry.from_dict(m) for m in _require_list(d, "masks")
        ]
        camera = CameraCaptureConfig.from_dict(d.get("camera") or {})
        lcd_settle_ms = int(d.get("lcd_settle_ms", 500))
        output_path = str(d.get("output_path") or "")
        store_burst = bool(d.get("store_burst", False))
        notes = _optional_str(d.get("notes"))
        extra = _optional_dict(d.get("extra")) or {}

        plan = cls(
            plan_id=plan_id,
            requires=requires,
            wavelengths=wavelengths,
            masks=masks,
            camera=camera,
            lcd_settle_ms=lcd_settle_ms,
            output_path=output_path,
            store_burst=store_burst,
            notes=notes,
            extra=extra,
        )
        plan.validate()
        return plan

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "plan_id": self.plan_id,
            "requires": self.requires,
            "wavelengths": [w.to_dict() for w in self.wavelengths],
            "masks": [m.to_dict() for m in self.masks],
            "camera": self.camera.to_dict(),
            "lcd_settle_ms": self.lcd_settle_ms,
            "output_path": self.output_path,
            "store_burst": self.store_burst,
        }
        if self.notes is not None:
            result["notes"] = self.notes
        if self.extra:
            result["extra"] = self.extra
        return result

    def to_json(self, path: str | Path | None = None) -> str:
        text = json.dumps(self.to_dict(), indent=2, default=_json_default)
        if path is not None:
            Path(path).write_text(text, encoding="utf-8")
        return text

    @classmethod
    def load_json(cls, path: str | Path) -> CapturePlan:
        text = Path(path).read_text(encoding="utf-8")
        d = json.loads(text)
        return cls.from_dict(d)

    @classmethod
    def load_yaml(cls, path: str | Path) -> CapturePlan:
        try:
            import yaml
        except ImportError:
            raise CapturePlanError(
                "PyYAML is required for YAML plan loading. "
                "Install it with: pip install PyYAML"
            )
        text = Path(path).read_text(encoding="utf-8")
        d = yaml.safe_load(text)
        if not isinstance(d, dict):
            raise CapturePlanError(f"YAML root must be a mapping, got {type(d).__name__}")
        return cls.from_dict(d)

    def validate(self) -> None:
        if not self.plan_id:
            raise CapturePlanError("plan_id must not be empty")
        if len(self.wavelengths) == 0:
            raise CapturePlanError("wavelengths list must not be empty")
        if len(self.masks) == 0:
            raise CapturePlanError("masks list must not be empty")
        if self.camera.frames_per_capture < 1:
            raise CapturePlanError("frames_per_capture must be >= 1")
        if self.lcd_settle_ms < 0:
            raise CapturePlanError("lcd_settle_ms must be >= 0")

        for i, w in enumerate(self.wavelengths):
            if w.settle_ms < 0:
                raise CapturePlanError(
                    f"wavelengths[{i}].settle_ms must be >= 0, got {w.settle_ms}"
                )

        for i, m in enumerate(self.masks):
            if not m.mask_id:
                raise CapturePlanError(f"masks[{i}].mask_id must not be empty")

        seen_wavelengths: set[float] = set()
        for w in self.wavelengths:
            if w.nominal_wavelength_nm in seen_wavelengths:
                raise CapturePlanError(
                    f"duplicate wavelength {w.nominal_wavelength_nm} in plan"
                )
            seen_wavelengths.add(w.nominal_wavelength_nm)

        seen_mask_ids: set[str] = set()
        for m in self.masks:
            if m.mask_id in seen_mask_ids:
                raise CapturePlanError(
                    f"duplicate mask_id {m.mask_id!r} in plan"
                )
            seen_mask_ids.add(m.mask_id)

    @property
    def n_wavelengths(self) -> int:
        return len(self.wavelengths)

    @property
    def n_masks(self) -> int:
        return len(self.masks)

    @property
    def n_captures(self) -> int:
        return self.n_wavelengths * self.n_masks

    def resolved_illumination_specs(self) -> list[IlluminationSpec]:
        return [entry.illumination for entry in self.wavelengths]


def _require_key(d: dict[str, Any], key: str) -> Any:
    if key not in d:
        raise CapturePlanError(f"missing required key {key!r}")
    return d[key]


def _require_str(d: dict[str, Any], key: str) -> str:
    value = _require_key(d, key)
    if not isinstance(value, str) or not value.strip():
        raise CapturePlanError(f"{key!r} must be a non-empty string")
    return value.strip()


def _require_list(d: dict[str, Any], key: str) -> list[Any]:
    value = _require_key(d, key)
    if not isinstance(value, list):
        raise CapturePlanError(f"{key!r} must be a list")
    return value


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped if stripped else None


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        raise CapturePlanError(
            f"expected int or null, got {type(value).__name__}: {value!r}"
        ) from None


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        raise CapturePlanError(
            f"expected float or null, got {type(value).__name__}: {value!r}"
        ) from None


def _optional_dict(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    return None


def _optional_camera_frame_extent(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, dict):
        origin = value.get("origin_xy", [0, 0])
        shape = value.get("shape_hw")
        if shape is None:
            raise CapturePlanError("frame_extent.shape_hw is required")
        result: dict[str, Any] = {
            "mode": str(value.get("mode") or "acquired_frame"),
            "origin_xy": list(_int_pair(origin, "frame_extent.origin_xy")),
            "shape_hw": list(_int_pair(shape, "frame_extent.shape_hw")),
            "sensor_shape_hw": None,
        }
        sensor_shape = value.get("sensor_shape_hw")
        if sensor_shape is not None:
            result["sensor_shape_hw"] = list(
                _int_pair(sensor_shape, "frame_extent.sensor_shape_hw")
            )
        return result
    raise CapturePlanError(
        f"camera.frame_extent must be a mapping, got {type(value).__name__}: {value!r}"
    )


def _int_pair(value: Any, name: str) -> tuple[int, int]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise CapturePlanError(f"{name} must contain two integers")
    try:
        return (int(value[0]), int(value[1]))
    except (TypeError, ValueError):
        raise CapturePlanError(
            f"{name} elements must be ints, got {value!r}"
        ) from None


def _json_default(obj: Any) -> Any:
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.floating):
        return float(obj)
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")
