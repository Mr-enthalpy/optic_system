"""Half-open ROI geometry in the explicitly supplied frame, without file/device IO."""
from dataclasses import dataclass
import numpy as np


@dataclass(frozen=True)
class ROI:
    """[x, x+width), [y, y+height); x right, y down, array indexing [H,W]."""
    x: int
    y: int
    width: int
    height: int

    def validate(self, shape_hw: tuple[int, int]) -> None:
        values = (self.x, self.y, self.width, self.height)
        if any(isinstance(v, (bool, np.bool_)) or not isinstance(v, (int, np.integer)) for v in values):
            raise ValueError("ROI geometry must contain integers")
        if self.width <= 0 or self.height <= 0:
            raise ValueError("ROI width and height must be positive")
        h, w = shape_hw
        if self.x < 0 or self.y < 0 or self.x + self.width > w or self.y + self.height > h:
            raise ValueError("ROI is outside supplied frame")


def roi_from_center(center_xy: tuple[float, float], shape_hw: tuple[int, int]) -> ROI:
    """Round origin to nearest pixel (ties upward); do not clip to the frame."""
    h, w = shape_hw
    if any(isinstance(v, bool) or not isinstance(v, (int, np.integer)) or v <= 0 for v in (h, w)):
        raise ValueError("ROI shape must contain positive integers [H, W]")
    if len(center_xy) != 2 or not np.all(np.isfinite(center_xy)):
        raise ValueError("center_xy must contain two finite coordinates")
    x = int(np.floor(center_xy[0] - (w - 1) / 2 + 0.5))
    y = int(np.floor(center_xy[1] - (h - 1) / 2 + 0.5))
    return ROI(x, y, int(w), int(h))
