from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


class IlluminationSpecError(ValueError):
    pass


@dataclass(frozen=True)
class IlluminationSpec:
    mode: str
    effective_wavelength_nm: float | None
    tls_setpoint_nm: float | None
    wavelength_label_nm: float | None = None

    def __post_init__(self) -> None:
        mode = str(self.mode)
        object.__setattr__(self, "mode", mode)
        effective = _optional_float(self.effective_wavelength_nm, "effective_wavelength_nm")
        tls_setpoint = _optional_float(self.tls_setpoint_nm, "tls_setpoint_nm")
        label = _optional_float(self.wavelength_label_nm, "wavelength_label_nm")
        object.__setattr__(self, "effective_wavelength_nm", effective)
        object.__setattr__(self, "tls_setpoint_nm", tls_setpoint)
        object.__setattr__(self, "wavelength_label_nm", label)
        _validate_spec(
            mode=mode,
            effective_wavelength_nm=effective,
            tls_setpoint_nm=tls_setpoint,
            wavelength_label_nm=label,
        )

    @property
    def is_monochromatic(self) -> bool:
        return self.mode == "monochromatic"

    @property
    def is_broadband_passthrough(self) -> bool:
        return self.mode == "broadband_passthrough"

    @property
    def requires_tls_pass_through(self) -> bool:
        return self.is_broadband_passthrough

    @property
    def requires_tls_wavelength_move(self) -> bool:
        return self.is_monochromatic and self.tls_setpoint_nm is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "effective_wavelength_nm": self.effective_wavelength_nm,
            "tls_setpoint_nm": self.tls_setpoint_nm,
            "wavelength_label_nm": self.wavelength_label_nm,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "IlluminationSpec":
        mode = str(data.get("mode") or "")
        if not mode:
            raise IlluminationSpecError("illumination.mode is required")
        return cls(
            mode=mode,
            effective_wavelength_nm=data.get("effective_wavelength_nm"),
            tls_setpoint_nm=data.get("tls_setpoint_nm"),
            wavelength_label_nm=data.get("wavelength_label_nm"),
        )

def normalize_illumination_spec(data: Mapping[str, Any]) -> IlluminationSpec:
    if not isinstance(data, Mapping):
        raise IlluminationSpecError(
            f"illumination spec must be mapping, got {type(data).__name__}"
        )
    if "illumination" in data:
        illum = data["illumination"]
        if not isinstance(illum, Mapping):
            raise IlluminationSpecError("illumination must be a mapping")
        return IlluminationSpec.from_dict(illum)
    if "mode" in data:
        return IlluminationSpec.from_dict(data)
    if "wavelength_nm" in data:
        raise IlluminationSpecError(
            "wavelength_nm compatibility input is no longer supported; use illumination"
        )
    raise IlluminationSpecError("expected illumination mapping")


def apply_illumination_to_tls(
    tls: Any,
    spec: IlluminationSpec,
    *,
    timeout_s: float | None = None,
) -> dict[str, Any]:
    timeout = 60.0 if timeout_s is None else float(timeout_s)
    if spec.requires_tls_pass_through:
        if tls is None:
            raise IlluminationSpecError("TLS pass-through requires a TLS adapter")
        tls.set_pass_through(timeout_s=timeout)
        status = _tls_status(tls)
        return _status_with_illumination(
            status,
            spec=spec,
            tls_action="set_pass_through",
        )
    if spec.requires_tls_wavelength_move:
        if tls is None:
            raise IlluminationSpecError("monochromatic TLS movement requires a TLS adapter")
        _set_tls_wavelength(tls, float(spec.tls_setpoint_nm))
        _move_tls(tls, timeout_s=timeout)
        status = _tls_status(tls)
        return _status_with_illumination(
            status,
            spec=spec,
            tls_action="set_wavelength_and_move",
        )
    if spec.is_monochromatic:
        raise IlluminationSpecError(
            "monochromatic TLS movement requires tls_setpoint_nm"
        )
    return _status_with_illumination(
        _tls_status(tls) if tls is not None else {},
        spec=spec,
        tls_action="none",
    )


def illumination_status_without_tls(spec: IlluminationSpec) -> dict[str, Any]:
    label = spec.wavelength_label_nm
    if label is None:
        label = spec.effective_wavelength_nm
    if label is None and spec.is_broadband_passthrough:
        label = 0.0
    return _status_with_illumination(
        {
            "connected": False,
            "current_wavelength_nm": label,
            "target_wavelength_nm": spec.tls_setpoint_nm,
            "grating": None,
            "moving": False,
        },
        spec=spec,
        tls_action="skipped_no_hardware",
    )


def _validate_spec(
    *,
    mode: str,
    effective_wavelength_nm: float | None,
    tls_setpoint_nm: float | None,
    wavelength_label_nm: float | None,
) -> None:
    if mode == "monochromatic":
        if effective_wavelength_nm is None or effective_wavelength_nm <= 0.0:
            raise IlluminationSpecError(
                "monochromatic effective_wavelength_nm must be > 0"
            )
        if tls_setpoint_nm is not None and tls_setpoint_nm <= 0.0:
            raise IlluminationSpecError("monochromatic tls_setpoint_nm must be > 0")
        return
    if mode == "broadband_passthrough":
        if effective_wavelength_nm is not None:
            raise IlluminationSpecError(
                "broadband_passthrough effective_wavelength_nm must be null"
            )
        if tls_setpoint_nm != 0.0:
            raise IlluminationSpecError(
                "broadband_passthrough tls_setpoint_nm must be 0.0"
            )
        if wavelength_label_nm is not None:
            raise IlluminationSpecError(
                "broadband_passthrough wavelength_label_nm must be null"
            )
        return
    raise IlluminationSpecError(f"unsupported illumination mode: {mode}")


def _optional_float(value: Any, name: str) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        raise IlluminationSpecError(f"{name} must be a number or null") from None


def _set_tls_wavelength(tls: Any, wavelength_nm: float) -> None:
    if hasattr(tls, "set_wavelength"):
        tls.set_wavelength(float(wavelength_nm))
        return
    if hasattr(tls, "set_wavelength_nm"):
        tls.set_wavelength_nm(float(wavelength_nm))
        return
    raise IlluminationSpecError("TLS adapter has no wavelength setter")


def _move_tls(tls: Any, *, timeout_s: float) -> None:
    if hasattr(tls, "move_and_wait"):
        tls.move_and_wait(timeout_s=timeout_s)
        return
    if hasattr(tls, "move"):
        tls.move(timeout_s=timeout_s)
        if hasattr(tls, "wait_until_idle"):
            tls.wait_until_idle(timeout_s=timeout_s)
        return
    raise IlluminationSpecError("TLS adapter has no move method")


def _tls_status(tls: Any) -> dict[str, Any]:
    if tls is None:
        return {}
    if hasattr(tls, "status"):
        status = tls.status()
    elif hasattr(tls, "get_status"):
        status = tls.get_status()
    else:
        return {}
    if isinstance(status, Mapping):
        return dict(status)
    result: dict[str, Any] = {}
    for name in (
        "connected",
        "current_wavelength_nm",
        "target_wavelength_nm",
        "grating",
        "moving",
        "timestamp_ns",
    ):
        if hasattr(status, name):
            result[name] = getattr(status, name)
    return result


def _status_with_illumination(
    status: Mapping[str, Any],
    *,
    spec: IlluminationSpec,
    tls_action: str,
) -> dict[str, Any]:
    out = dict(status)
    out["illumination"] = spec.to_dict()
    out["tls_action"] = tls_action
    return out
