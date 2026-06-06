"""Fake hardware devices for no-hardware tests and dry runs."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import numpy as np


class FakeDeviceError(RuntimeError):
    pass


@dataclass
class FakeCaptureFrames:
    burst: np.ndarray
    frames_avg: np.ndarray
    metadata: dict[str, Any] = field(default_factory=dict)


class FakeCamera:
    is_fake = True

    def __init__(self, *, seed: int = 42, height: int = 480, width: int = 640,
                 exposure_us: float | None = None, gain_db: float | None = None):
        self._rng = np.random.default_rng(seed)
        self._h = height
        self._w = width
        self.exposure_us = exposure_us
        self.gain_db = gain_db

    def apply_camera_params(self, exposure_us=None, gain_db=None):
        if exposure_us is not None:
            self.exposure_us = float(exposure_us)
        if gain_db is not None:
            self.gain_db = float(gain_db)

    def read_camera_params(self) -> dict[str, Any]:
        return {"exposure_us": self.exposure_us, "gain_db": self.gain_db}

    def acquire_burst(self, k: int) -> FakeCaptureFrames:
        burst = self._rng.normal(128, 40, (k, self._h, self._w)).astype(np.float64)
        avg = burst.mean(axis=0, dtype=np.float64)
        return FakeCaptureFrames(
            burst=burst,
            frames_avg=avg,
            metadata={
                "exposure_us": self.exposure_us,
                "gain_db": self.gain_db,
                "frame_extent": {
                    "mode": "unknown",
                    "origin_xy": [0, 0],
                    "shape_hw": [self._h, self._w],
                    "sensor_shape_hw": None,
                },
                "timestamp_ns": time.monotonic_ns(),
                "status": {"source": "fake"},
                "frame_shape": [self._h, self._w],
                "acquisition": "burst",
                "n": k,
            },
        )


class FakeLCD:
    is_fake = True

    def __init__(self, *, height: int = 60, width_phys: int = 180, subpixel_axis: int = 1):
        self._h = height
        self._w = width_phys
        self._subpixel_axis = subpixel_axis
        self.last_mask: np.ndarray | None = None
        self.last_mask_id: str | None = None

    def show_physical_mask(self, mask: np.ndarray, *, mask_id: str | None = None) -> None:
        mask = np.asarray(mask)
        if mask.ndim != 2:
            raise FakeDeviceError(f"mask must be 2D [H, 3W], got shape {mask.shape}")
        self.last_mask = mask.copy()
        self.last_mask_id = mask_id

    def metadata(self) -> dict[str, Any]:
        return {
            "display_index": 0,
            "physical_shape": [self._h, self._w],
            "logical_shape": (self._h // 3, self._w) if self._subpixel_axis == 0 else (self._h, self._w // 3),
            "subpixel_axis": self._subpixel_axis,
            "transmissive_code": 255,
            "opaque_code": 0,
        }

    def physical_shape(self) -> tuple[int, int]:
        return (self._h, self._w)

    def subpixel_axis(self) -> int:
        return self._subpixel_axis


class FakeTLS:
    is_fake = True

    def __init__(self):
        self._current_nm: float | None = None
        self._target_nm: float | None = None
        self._grating: int | None = None
        self._connected = False

    def connect(self) -> None:
        self._connected = True

    def set_grating(self, grating: int) -> None:
        self._grating = int(grating)

    def set_wavelength(self, wavelength_nm: float) -> None:
        self._target_nm = float(wavelength_nm)

    def set_pass_through(self, timeout_s: float) -> None:
        self._target_nm = 0.0
        self._current_nm = 0.0

    def move_and_wait(self, timeout_s: float) -> None:
        self._current_nm = self._target_nm

    def status(self) -> dict[str, Any]:
        return {
            "connected": self._connected,
            "current_wavelength_nm": self._current_nm,
            "target_wavelength_nm": self._target_nm,
            "grating": self._grating,
            "moving": False,
            "timestamp_ns": time.monotonic_ns(),
        }


class FakeDeviceBundle:
    is_fake = True

    def __init__(self, camera=None, lcd=None, tls=None):
        self.camera = camera or FakeCamera()
        self.lcd = lcd or FakeLCD()
        self.tls = tls
