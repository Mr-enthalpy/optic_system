from __future__ import annotations

from dataclasses import dataclass

import numpy as np


class Event:
    """Base controller event."""


@dataclass(frozen=True)
class StatusMessage(Event):
    level: str
    message: str


@dataclass(frozen=True)
class CameraError(Event):
    source: str
    message: str


@dataclass(frozen=True)
class LCDError(Event):
    source: str
    message: str


@dataclass(frozen=True)
class TLSError(Event):
    source: str
    message: str


@dataclass(frozen=True)
class PreviewFrameUpdated(Event):
    preview_bgr: np.ndarray


@dataclass(frozen=True)
class PreviewStatsUpdated(Event):
    max_pixel: float
    frame_seq: int
    timestamp_ns: int
    width: int
    height: int
    stride: int
    pixel_format: str


@dataclass(frozen=True)
class CameraSettingsApplied(Event):
    requested_settings: dict[str, float]
    applied_settings: dict[str, float]


@dataclass(frozen=True)
class CameraSettingsRefreshed(Event):
    settings: dict[str, float]


@dataclass(frozen=True)
class LCDStatusChanged(Event):
    connected: bool
    current_mode: str | None
    current_mask_id: str | None
    reported_shape: tuple[int, int, int] | None
    physical_shape: tuple[int, int] | None


@dataclass(frozen=True)
class LCDMaskShown(Event):
    mask_id: str | None
    physical_shape: tuple[int, int]
    packed_shape: tuple[int, int, int]


@dataclass(frozen=True)
class LCDAllTransmissiveShown(Event):
    physical_shape: tuple[int, int]
    packed_shape: tuple[int, int, int]


@dataclass(frozen=True)
class LCDAllOpaqueShown(Event):
    physical_shape: tuple[int, int]
    packed_shape: tuple[int, int, int]


@dataclass(frozen=True)
class LCDDebugPatternShown(Event):
    pattern_name: str
    physical_shape: tuple[int, int]
    packed_shape: tuple[int, int, int]


@dataclass(frozen=True)
class TLSConnected(Event):
    device_id: int | None


@dataclass(frozen=True)
class TLSDisconnected(Event):
    device_id: int | None


@dataclass(frozen=True)
class TLSStatusUpdated(Event):
    connected: bool
    device_id: int | None
    current_wavelength_nm: float | None
    target_wavelength_nm: float | None
    grating: int | None
    moving: bool
    last_error: str | None


@dataclass(frozen=True)
class TLSWavelengthTargetSet(Event):
    target_wavelength_nm: float


@dataclass(frozen=True)
class TLSMoveStarted(Event):
    target_wavelength_nm: float | None


@dataclass(frozen=True)
class TLSMoveFinished(Event):
    current_wavelength_nm: float | None
    target_wavelength_nm: float | None
