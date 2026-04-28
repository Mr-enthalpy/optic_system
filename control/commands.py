from __future__ import annotations

from dataclasses import dataclass

import numpy as np


class Command:
    """Base controller command."""


@dataclass(frozen=True)
class ApplyCameraSettings(Command):
    settings: dict[str, float]


@dataclass(frozen=True)
class RefreshCameraSettings(Command):
    pass


@dataclass(frozen=True)
class SetLCDAllTransmissive(Command):
    pass


@dataclass(frozen=True)
class SetLCDAllOpaque(Command):
    pass


@dataclass(frozen=True)
class ShowLCDMonoMask(Command):
    mask: np.ndarray
    mask_id: str | None = None


@dataclass(frozen=True)
class ShowLCDDebugPattern(Command):
    pattern_name: str


@dataclass(frozen=True)
class Shutdown(Command):
    force: bool = False
