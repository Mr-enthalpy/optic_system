from __future__ import annotations

from typing import Optional

import numpy as np

from .lcd_backend import LCDBackend
from .lcd_debug_patterns import build_debug_pattern


class LCDService:
    """
    LCD device abstraction.

    The canonical physical representation is a mono mask shaped as `(H, 3W)`.
    Only this service packs that physical mono mask into the display RGB buffer
    shaped as `(H, W, 3)`.
    """

    def __init__(
        self,
        *,
        backend: Optional[LCDBackend] = None,
        display_index: Optional[int] = None,
        transmissive_code: int = 255,
        opaque_code: int = 0,
    ):
        self._backend = backend
        self._display_index = display_index
        self.transmissive_code = int(transmissive_code)
        self.opaque_code = int(opaque_code)

        self._last_mode: str | None = None
        self._last_mask_id: str | None = None

    def initialize(self) -> None:
        if self._backend is None:
            self._backend = LCDBackend(display_index=self._display_index)

    def get_metadata(self) -> dict[str, object]:
        self.initialize()
        assert self._backend is not None
        backend_metadata = self._backend.get_metadata()
        reported_shape = backend_metadata["reported_shape"]
        height, width, _ = reported_shape
        return {
            **backend_metadata,
            "physical_shape": (height, width * 3),
            "transmissive_code": self.transmissive_code,
            "opaque_code": self.opaque_code,
            "current_mode": self._last_mode,
            "current_mask_id": self._last_mask_id,
        }

    def mono_to_rgb(self, mask: np.ndarray) -> np.ndarray:
        metadata = self.get_metadata()
        height, width_phys = metadata["physical_shape"]

        mono = np.asarray(mask)
        if mono.dtype != np.uint8:
            mono = mono.astype(np.uint8)
        if mono.shape != (height, width_phys):
            raise ValueError(
                f"Expected mono mask shape {(height, width_phys)}, got {tuple(mono.shape)}"
            )
        return mono.reshape(height, width_phys // 3, 3).copy()

    def rgb_to_mono(self, rgb: np.ndarray) -> np.ndarray:
        metadata = self.get_metadata()
        reported_shape = metadata["reported_shape"]

        frame = np.asarray(rgb)
        if frame.dtype != np.uint8:
            frame = frame.astype(np.uint8)
        if frame.shape != reported_shape:
            raise ValueError(
                f"Expected RGB buffer shape {reported_shape}, got {tuple(frame.shape)}"
            )
        height, width, _ = reported_shape
        return frame.reshape(height, width * 3).copy()

    def show_rgb_buffer(
        self,
        rgb: np.ndarray,
        *,
        mode: str = "rgb_buffer",
        mask_id: str | None = None,
    ) -> None:
        self.initialize()
        assert self._backend is not None
        self._backend.show(np.asarray(rgb, dtype=np.uint8))
        self._last_mode = mode
        self._last_mask_id = mask_id

    def show_mono_mask(
        self,
        mask: np.ndarray,
        *,
        mask_id: str | None = None,
        mode: str = "mono_mask",
    ) -> np.ndarray:
        rgb = self.mono_to_rgb(mask)
        self.show_rgb_buffer(rgb, mode=mode, mask_id=mask_id)
        return rgb

    def make_all_transmissive_mask(self) -> np.ndarray:
        metadata = self.get_metadata()
        height, width_phys = metadata["physical_shape"]
        return np.full((height, width_phys), self.transmissive_code, dtype=np.uint8)

    def make_all_opaque_mask(self) -> np.ndarray:
        metadata = self.get_metadata()
        height, width_phys = metadata["physical_shape"]
        return np.full((height, width_phys), self.opaque_code, dtype=np.uint8)

    def show_all_transmissive(self) -> np.ndarray:
        mask = self.make_all_transmissive_mask()
        return self.show_mono_mask(mask, mask_id="all_transmissive", mode="all_transmissive")

    def show_all_opaque(self) -> np.ndarray:
        mask = self.make_all_opaque_mask()
        return self.show_mono_mask(mask, mask_id="all_opaque", mode="all_opaque")

    def make_debug_pattern(self, pattern_name: str) -> np.ndarray:
        metadata = self.get_metadata()
        height, width_phys = metadata["physical_shape"]
        return build_debug_pattern(
            pattern_name,
            height=height,
            width_phys=width_phys,
            transmissive_code=self.transmissive_code,
            opaque_code=self.opaque_code,
        )

    def show_debug_pattern(self, pattern_name: str) -> np.ndarray:
        mask = self.make_debug_pattern(pattern_name)
        return self.show_mono_mask(mask, mask_id=pattern_name, mode="debug_pattern")

    def close(self) -> None:
        if self._backend is not None:
            self._backend.close()
            self._backend = None
        self._last_mode = None
        self._last_mask_id = None
