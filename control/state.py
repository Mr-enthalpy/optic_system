from __future__ import annotations

from dataclasses import dataclass, field, replace
from threading import Lock
from typing import Optional

import numpy as np


@dataclass(frozen=True)
class CameraSettingSnapshot:
    name: str
    min_value: float
    max_value: float
    value: float


@dataclass
class SessionState:
    camera_open: bool = False
    stream_running: bool = False

    sidecar_running: bool = False
    sidecar_owned: bool = False
    sidecar_pid: Optional[int] = None
    sidecar_rep_addr: str = "tcp://127.0.0.1:6101"

    camera_serial: Optional[str] = None
    camera_settings: dict[str, float] = field(default_factory=dict)
    camera_setting_ranges: dict[str, tuple[float, float]] = field(default_factory=dict)

    latest_preview_bgr: Optional[np.ndarray] = None
    latest_max_pixel: float = 0.0
    latest_frame_seq: Optional[int] = None
    latest_frame_timestamp_ns: Optional[int] = None

    pixel_format: Optional[str] = None
    frame_width: int = 0
    frame_height: int = 0
    frame_stride: int = 0

    lcd_connected: bool = False
    lcd_display_index: Optional[int] = None
    lcd_reported_shape: tuple[int, int, int] | None = None
    lcd_physical_shape: tuple[int, int] | None = None
    lcd_current_mode: Optional[str] = None
    lcd_current_mask_id: Optional[str] = None
    lcd_transmissive_code: Optional[int] = None
    lcd_opaque_code: Optional[int] = None
    lcd_last_error: Optional[str] = None

    last_error: Optional[str] = None


class StateStore:
    def __init__(self):
        self._lock = Lock()
        self._state = SessionState()

    def get(self) -> SessionState:
        with self._lock:
            return self._copy_state()

    def update(self, **kwargs) -> SessionState:
        with self._lock:
            for key, value in kwargs.items():
                setattr(self._state, key, value)
            return self._copy_state()

    def _copy_state(self) -> SessionState:
        return replace(
            self._state,
            camera_settings=dict(self._state.camera_settings),
            camera_setting_ranges=dict(self._state.camera_setting_ranges),
        )
