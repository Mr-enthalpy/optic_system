"""Shared hardware protocol definitions for capture and profile tasks."""

from __future__ import annotations

from typing import Any, Protocol


class CameraBurstDevice(Protocol):
    """Camera capable of acquiring frame bursts."""

    def acquire_burst(self, k: int):
        ...


class CameraParamDevice(Protocol):
    """Camera with settable exposure and gain."""

    def apply_camera_params(self, exposure_us=None, gain_db=None):
        ...

    def read_exposure_bounds_us(self) -> tuple[float, float]:
        ...


class LCDPhysicalMaskDevice(Protocol):
    """LCD capable of displaying physical mono masks."""

    def show_physical_mask(self, mask, *, mask_id: str | None = None) -> None:
        ...

    def physical_shape(self) -> tuple[int, int]:
        ...


class LCDMetadataDevice(Protocol):
    """LCD with metadata and subpixel info."""

    def metadata(self) -> dict[str, Any]:
        ...

    def subpixel_axis(self) -> int:
        ...


class TLSIlluminationDevice(Protocol):
    """TLS capable of wavelength / pass-through control and status."""

    def set_pass_through(self, timeout_s: float = 60.0) -> None:
        ...

    def set_grating(self, grating: int) -> None:
        ...

    def set_wavelength(self, wavelength_nm: float) -> None:
        ...

    def move_and_wait(self, timeout_s: float) -> None:
        ...

    def move(self, timeout_s: float = 60.0) -> None:
        ...

    def wait_until_idle(self, *, timeout_s: float = 60.0, **kwargs) -> None:
        ...

    def status(self) -> dict[str, Any]:
        ...

    def get_status(self) -> Any:
        ...
