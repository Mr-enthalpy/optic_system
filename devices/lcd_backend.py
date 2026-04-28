from __future__ import annotations

import os
from typing import Optional

import numpy as np
import pygame


def _find_lcd_index() -> int:
    pygame.display.init()
    sizes = pygame.display.get_desktop_sizes()
    display_count = pygame.display.get_num_displays()
    if display_count <= 0 or not sizes:
        raise RuntimeError("No SDL display was detected for the LCD backend")
    return 1 if display_count > 1 else 0


def _validate_rgb_buffer(frame: np.ndarray, size: tuple[int, int]) -> None:
    if frame.dtype != np.uint8:
        raise TypeError(f"Expected uint8 RGB buffer, got {frame.dtype!r}")
    if frame.ndim != 3 or frame.shape[2] != 3:
        raise ValueError("Expected RGB buffer with shape (H, W, 3)")

    height, width = frame.shape[:2]
    if (width, height) != size:
        raise ValueError(
            f"RGB buffer size {width}x{height} does not match LCD size {size[0]}x{size[1]}"
        )


class LCDBackend:
    """
    Minimal pygame-backed LCD display adapter.

    This backend only knows how to display a packed RGB framebuffer sized as
    `(H, W, 3)`. Physical mono subpixel semantics belong in `LCDService`.
    """

    def __init__(self, display_index: Optional[int] = None):
        pygame.display.init()

        if display_index is None:
            display_index = _find_lcd_index()

        sizes = pygame.display.get_desktop_sizes()
        if display_index < 0 or display_index >= len(sizes):
            raise ValueError(f"Invalid LCD display index {display_index}")

        os.environ["SDL_VIDEO_FULLSCREEN_DISPLAY"] = str(display_index)
        pygame.init()

        self._display_index = int(display_index)
        self._width, self._height = sizes[self._display_index]
        flags = pygame.HWSURFACE | pygame.DOUBLEBUF | pygame.NOFRAME
        self._screen = pygame.display.set_mode(
            (self._width, self._height),
            flags=flags,
            display=self._display_index,
        )

    @property
    def size(self) -> tuple[int, int]:
        return self._width, self._height

    def get_metadata(self) -> dict[str, object]:
        return {
            "display_index": self._display_index,
            "display_size": (self._width, self._height),
            "reported_shape": (self._height, self._width, 3),
        }

    def show(self, rgb: np.ndarray) -> None:
        _validate_rgb_buffer(rgb, self.size)
        pygame.event.pump()
        surface = pygame.surfarray.make_surface(rgb.swapaxes(0, 1))
        self._screen.blit(surface, (0, 0))
        pygame.display.flip()

    def close(self) -> None:
        pygame.display.quit()
        pygame.quit()
