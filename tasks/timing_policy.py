"""Shared timing policy for hardware settle and frame discard across tasks."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class LCDSettlePolicy:
    """Policy for LCD mask settle timing."""
    settle_ms: float = 20.0
    allow_below_refresh: bool = False


@dataclass(frozen=True)
class TLSSettlePolicy:
    """Policy for TLS wavelength settle timing."""
    settle_ms: int = 2000


@dataclass(frozen=True)
class CameraParameterSettlePolicy:
    """Policy for camera exposure/gain parameter settle."""
    settle_ms: float = 300.0
    discard_frames: int = 80


@dataclass(frozen=True)
class TaskTimingPolicy:
    """Aggregate timing policy for a hardware task."""

    lcd: LCDSettlePolicy = field(default_factory=LCDSettlePolicy)
    tls: TLSSettlePolicy = field(default_factory=TLSSettlePolicy)
    camera_param: CameraParameterSettlePolicy = field(default_factory=CameraParameterSettlePolicy)

    def to_dict(self) -> dict[str, Any]:
        return {
            "lcd_settle_ms": self.lcd.settle_ms,
            "lcd_allow_below_refresh": self.lcd.allow_below_refresh,
            "tls_settle_ms": self.tls.settle_ms,
            "camera_param_settle_ms": self.camera_param.settle_ms,
            "camera_discard_frames": self.camera_param.discard_frames,
        }
