from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class RuntimeMode(str, Enum):
    HARDWARE = "hardware"
    NO_HARDWARE = "no_hardware"
    SYNTHETIC = "synthetic"
    DIAGNOSTIC = "diagnostic"


class RuntimeModeError(ValueError):
    pass


@dataclass(frozen=True)
class RuntimePolicy:
    mode: RuntimeMode
    allow_fake_devices: bool = False
    allow_missing_tls: bool = False
    allow_missing_lcd: bool = False
    allow_missing_camera: bool = False
    allow_raw_fallback: bool = False
    allow_test_settle_override: bool = False
    allow_dry_run_hardware_write: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode.value,
            "allow_fake_devices": bool(self.allow_fake_devices),
            "allow_missing_tls": bool(self.allow_missing_tls),
            "allow_missing_lcd": bool(self.allow_missing_lcd),
            "allow_missing_camera": bool(self.allow_missing_camera),
            "allow_raw_fallback": bool(self.allow_raw_fallback),
            "allow_test_settle_override": bool(self.allow_test_settle_override),
            "allow_dry_run_hardware_write": bool(self.allow_dry_run_hardware_write),
        }


def hardware_runtime_policy() -> RuntimePolicy:
    return RuntimePolicy(mode=RuntimeMode.HARDWARE)


def no_hardware_runtime_policy() -> RuntimePolicy:
    return RuntimePolicy(
        mode=RuntimeMode.NO_HARDWARE,
        allow_fake_devices=True,
        allow_missing_tls=True,
        allow_missing_lcd=True,
        allow_missing_camera=True,
        allow_test_settle_override=True,
    )


def synthetic_runtime_policy() -> RuntimePolicy:
    return RuntimePolicy(
        mode=RuntimeMode.SYNTHETIC,
        allow_fake_devices=True,
        allow_missing_tls=True,
        allow_missing_lcd=True,
        allow_missing_camera=True,
        allow_raw_fallback=True,
        allow_test_settle_override=True,
    )


def diagnostic_runtime_policy() -> RuntimePolicy:
    return RuntimePolicy(
        mode=RuntimeMode.DIAGNOSTIC,
        allow_missing_tls=True,
        allow_raw_fallback=True,
    )


def normalize_runtime_policy(value: RuntimePolicy | str | RuntimeMode | None) -> RuntimePolicy:
    if value is None:
        return hardware_runtime_policy()
    if isinstance(value, RuntimePolicy):
        return value
    try:
        mode = RuntimeMode(value)
    except ValueError:
        raise RuntimeModeError(f"unsupported runtime mode: {value!r}") from None
    if mode == RuntimeMode.HARDWARE:
        return hardware_runtime_policy()
    if mode == RuntimeMode.NO_HARDWARE:
        return no_hardware_runtime_policy()
    if mode == RuntimeMode.SYNTHETIC:
        return synthetic_runtime_policy()
    if mode == RuntimeMode.DIAGNOSTIC:
        return diagnostic_runtime_policy()
    raise RuntimeModeError(f"unsupported runtime mode: {value!r}")


def is_fake_device(obj: Any) -> bool:
    if obj is None:
        return False
    cls_name = obj.__class__.__name__.lower()
    return bool(getattr(obj, "is_fake", False)) or cls_name.startswith("fake")


def validate_required_devices(
    devices: Any,
    *,
    policy: RuntimePolicy,
    require_camera: bool,
    require_lcd: bool,
    require_tls: bool,
) -> None:
    camera = getattr(devices, "camera", None)
    lcd = getattr(devices, "lcd", None)
    tls = getattr(devices, "tls", None)
    if require_camera and camera is None and not policy.allow_missing_camera:
        raise RuntimeModeError("runtime mode requires a camera device")
    if require_lcd and lcd is None and not policy.allow_missing_lcd:
        raise RuntimeModeError("runtime mode requires an LCD device")
    if require_tls and tls is None and not policy.allow_missing_tls:
        raise RuntimeModeError("runtime mode requires a TLS device")


def validate_no_fake_devices(
    devices: Any,
    *,
    policy: RuntimePolicy,
) -> None:
    if policy.allow_fake_devices:
        return
    if is_fake_device(devices):
        raise RuntimeModeError(
            f"runtime mode {policy.mode.value!r} forbids fake device bundle"
        )
    for name, obj in _device_items(devices):
        if is_fake_device(obj):
            raise RuntimeModeError(
                f"runtime mode {policy.mode.value!r} forbids fake {name} device"
            )


def validate_tls_for_illumination(
    illumination: Any,
    tls: Any,
    *,
    policy: RuntimePolicy,
) -> None:
    requires_tls = bool(
        getattr(illumination, "requires_tls_pass_through", False)
        or getattr(illumination, "requires_tls_wavelength_move", False)
    )
    if requires_tls and tls is None and not policy.allow_missing_tls:
        raise RuntimeModeError(
            "illumination requires TLS movement/pass-through in this runtime mode"
        )


def validate_raw_fallback_allowed(
    *,
    allow_raw_fallback: bool,
    policy: RuntimePolicy,
) -> None:
    if allow_raw_fallback and not policy.allow_raw_fallback:
        raise RuntimeModeError(
            "allow_raw_fallback=True requires diagnostic or synthetic runtime mode"
        )


def validate_lcd_settle_policy(
    *,
    lcd_settle_ms: int | float,
    expected_min_settle_ms: int | float | None,
    policy: RuntimePolicy,
) -> None:
    if expected_min_settle_ms is None:
        return
    if float(lcd_settle_ms) < float(expected_min_settle_ms) and not policy.allow_test_settle_override:
        raise RuntimeModeError(
            "below-minimum LCD settle timing requires explicit non-hardware/test policy"
        )


def _device_items(devices: Any) -> list[tuple[str, Any]]:
    if devices is None:
        return []
    if any(hasattr(devices, name) for name in ("camera", "lcd", "tls")):
        return [
            ("camera", getattr(devices, "camera", None)),
            ("lcd", getattr(devices, "lcd", None)),
            ("tls", getattr(devices, "tls", None)),
        ]
    return [(devices.__class__.__name__, devices)]
