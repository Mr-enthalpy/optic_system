from __future__ import annotations

import os
from typing import Optional

import numpy as np

from .lcd_backend import LCDBackend
from .lcd_debug_patterns import build_debug_pattern


class LCDService:
    """
    LCD device abstraction with configurable subpixel axis.

    The canonical physical mono representation depends on ``subpixel_axis``:

    - ``subpixel_axis=0``: physical mono ``[3H, W]``; RGB buffer ``[H, W, 3]``
    - ``subpixel_axis=1``: physical mono ``[H, 3W]``; RGB buffer ``[H, W, 3]``

    ``subpixel_axis`` can be set explicitly or via the environment variable
    ``OPTIC_SYSTEM_LCD_SUBPIXEL_AXIS``.  If ``subpixel_axis`` is ``None``,
    defaults to axis=1 (legacy width-tripling).
    """

    def __init__(
        self,
        *,
        backend: Optional[LCDBackend] = None,
        display_index: Optional[int] = None,
        subpixel_axis: int | None = None,
        transmissive_code: int = 255,
        opaque_code: int = 0,
    ):
        self._backend = backend
        self._display_index = display_index

        if subpixel_axis is None:
            env_val = os.environ.get("OPTIC_SYSTEM_LCD_SUBPIXEL_AXIS", "")
            if env_val in ("0", "1"):
                subpixel_axis = int(env_val)
            else:
                subpixel_axis = 1  # legacy default: width-tripling
        if subpixel_axis not in (0, 1):
            raise ValueError(f"subpixel_axis must be 0 or 1, got {subpixel_axis}")
        self.subpixel_axis = int(subpixel_axis)

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

        if self.subpixel_axis == 0:
            phys_h, phys_w = height * 3, width
            logical_h, logical_w = height, width
        else:
            phys_h, phys_w = height, width * 3
            logical_h, logical_w = height, width

        return {
            **backend_metadata,
            "subpixel_axis": self.subpixel_axis,
            "logical_shape": (logical_h, logical_w),
            "physical_shape": (phys_h, phys_w),
            "transmissive_code": self.transmissive_code,
            "opaque_code": self.opaque_code,
            "current_mode": self._last_mode,
            "current_mask_id": self._last_mask_id,
        }

    # ----- physics ↔ RGB packing -----

    def mono_to_rgb(self, mask: np.ndarray) -> np.ndarray:
        metadata = self.get_metadata()
        phys_h, phys_w = metadata["physical_shape"]

        mono = np.asarray(mask)
        if mono.dtype != np.uint8:
            mono = mono.astype(np.uint8)
        if mono.shape != (phys_h, phys_w):
            raise ValueError(
                f"Expected mono mask shape {(phys_h, phys_w)}, got {tuple(mono.shape)}"
            )

        if self.subpixel_axis == 0:
            rgb = mono.reshape(phys_h // 3, 3, phys_w).transpose(0, 2, 1).copy()
        else:
            rgb = mono.reshape(phys_h, phys_w // 3, 3).copy()
        return rgb

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
        if self.subpixel_axis == 0:
            mono = frame.transpose(1, 0, 2).reshape(height * 3, width).copy()
        else:
            mono = frame.reshape(height, width * 3).copy()
        return mono

    # ----- display -----

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

    # ----- pattern helpers -----

    def make_all_transmissive_mask(self) -> np.ndarray:
        metadata = self.get_metadata()
        h, w = metadata["physical_shape"]
        return np.full((h, w), self.transmissive_code, dtype=np.uint8)

    def make_all_opaque_mask(self) -> np.ndarray:
        metadata = self.get_metadata()
        h, w = metadata["physical_shape"]
        return np.full((h, w), self.opaque_code, dtype=np.uint8)

    def show_all_transmissive(self) -> np.ndarray:
        mask = self.make_all_transmissive_mask()
        return self.show_mono_mask(mask, mask_id="all_transmissive", mode="all_transmissive")

    def show_all_opaque(self) -> np.ndarray:
        mask = self.make_all_opaque_mask()
        return self.show_mono_mask(mask, mask_id="all_opaque", mode="all_opaque")

    def make_debug_pattern(self, pattern_name: str) -> np.ndarray:
        metadata = self.get_metadata()
        h, w = metadata["physical_shape"]
        return build_debug_pattern(
            pattern_name,
            height=h,
            width_phys=w,
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
