"""Dense ROI measurements. Coordinates are sensor pixels, x right / y down."""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

import h5py
import numpy as np

from tasks.measurement.frame_source import open_full_frame_survey_source
from tasks.measurement.json_io import decode_h5_string
from .sensor_energy_center import SensorEnergyCenterProfile, validate_center_profile_for_frame_source


@dataclass(frozen=True)
class ROI:
    """Half-open sensor rectangle [x, x+width), [y, y+height)."""
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
            raise ValueError("ROI is outside sensor frame")


def roi_from_center(center_xy: tuple[float, float], shape_hw: tuple[int, int]) -> ROI:
    """Round origin to nearest pixel (ties upward); do not clip to the sensor."""
    h, w = shape_hw
    if any(isinstance(v, bool) or not isinstance(v, (int, np.integer)) or v <= 0 for v in (h, w)):
        raise ValueError("ROI shape must contain positive integers [H, W]")
    if len(center_xy) != 2 or not np.all(np.isfinite(center_xy)):
        raise ValueError("center_xy must contain two finite coordinates")
    x = int(np.floor(center_xy[0] - (w - 1) / 2 + 0.5))
    y = int(np.floor(center_xy[1] - (h - 1) / 2 + 0.5))
    return ROI(x, y, int(w), int(h))


@dataclass
class MeasuredROIPSF:
    psf: np.ndarray  # [roi.height, roi.width], original measured amplitude
    roi: ROI
    metadata: dict[str, Any]


def extract_roi_psf(
    survey_h5: str | Path,
    *,
    wavelength_index: int,
    display_index: int,
    roi: ROI | None = None,
    center_profile: SensorEnergyCenterProfile | None = None,
    roi_shape_hw: tuple[int, int] | None = None,
) -> MeasuredROIPSF:
    """Select by original capture-plan indices; reject missing/ambiguous entries.

    Supply a sensor ROI, or a center profile and experiment-selected ROI shape.
    Extraction never normalizes, corrects, or writes either measurement file.
    """
    for name, value in (("wavelength_index", wavelength_index), ("display_index", display_index)):
        if isinstance(value, (bool, np.bool_)) or not isinstance(value, (int, np.integer)) or value < 0:
            raise ValueError(f"{name} must be a non-negative integer")
    if (roi is None) == (center_profile is None):
        raise ValueError("supply either ROI or center_profile")
    if roi is not None and roi_shape_hw is not None:
        raise ValueError("roi_shape_hw is only used with center_profile")
    path = Path(survey_h5)
    with h5py.File(path, "r") as f:
        source = open_full_frame_survey_source(f, path)
        desc = source.descriptor
        if desc.coordinate_frame != "sensor_full_frame":
            raise ValueError("ROI extraction requires sensor_full_frame coordinates")
        if center_profile is not None:
            validate_center_profile_for_frame_source(
                center_profile, coordinate_frame=desc.coordinate_frame,
                camera_frame_extent=desc.camera_frame_extent_dict(), frame_shape=desc.frame_shape,
            )
            if Path(center_profile.source_survey_h5).resolve() != path.resolve():
                raise ValueError("center profile source survey does not match")
            if roi_shape_hw is None:
                raise ValueError("roi_shape_hw is required with center_profile")
            roi = roi_from_center(center_profile.center_xy, roi_shape_hw)
        assert roi is not None
        roi.validate(desc.frame_shape)
        grp = f["full_frame_survey"]
        wi = np.asarray(grp["wavelength_index"])
        mi = np.asarray(grp["mask_index"])
        if wi.shape != (desc.frame_count,) or mi.shape != (desc.frame_count,):
            raise ValueError("survey index dimensions do not match frames")
        if not np.issubdtype(wi.dtype, np.integer) or not np.issubdtype(mi.dtype, np.integer) or np.any(wi < 0) or np.any(mi < 0):
            raise ValueError("survey indices must be non-negative integers")
        rows = np.flatnonzero((wi == wavelength_index) & (mi == display_index))
        if len(rows) != 1:
            raise ValueError("wavelength/display index must select exactly one measurement")
        row = int(rows[0])
        frame = source.read_frame(row)
        if not np.all(np.isfinite(frame)):
            raise ValueError("measurement contains non-finite intensities")
        metadata = {
            "source_survey_h5": str(path.resolve()), "source_entry_index": row,
            "source_raw_capture_h5": decode_h5_string(f["source/raw_capture_h5"][()]),
            "source_capture_index": int(grp["capture_indices"][row]),
            "coordinate_frame": desc.coordinate_frame,
            "camera_frame_extent": desc.camera_frame_extent_dict(),
            "wavelength_index": int(wavelength_index), "display_index": int(display_index),
            "display_state_id": desc.mask_ids[row],
            "illumination": json.loads(desc.entry_illumination_json[row]),
            "wavelength_nm": desc.entry_wavelengths_nm[row],
            "camera_settings": json.loads(decode_h5_string(grp["camera_settings_json"][row])),
            "lcd_metadata": json.loads(decode_h5_string(f["lcd/metadata_json"][()])),
            "roi_xywh": [roi.x, roi.y, roi.width, roi.height],
            "center_source": ({"profile_id": center_profile.center_profile_id, "center_xy": list(center_profile.center_xy)}
                              if center_profile else {"type": "explicit_sensor_roi"}),
            "normalization": "none",
        }
        return MeasuredROIPSF(frame[roi.y:roi.y + roi.height, roi.x:roi.x + roi.width].copy(), roi, metadata)


def relative_response(measurement: MeasuredROIPSF, *, exposure_us: float | None = None, normalize_sum: bool = False) -> np.ndarray:
    """Explicit analysis copy; exposure scaling does not correct gain/source power."""
    values = np.asarray(measurement.psf, dtype=np.float64).copy()
    if exposure_us is not None:
        if not np.isfinite(exposure_us) or exposure_us <= 0:
            raise ValueError("exposure_us must be finite and positive")
        values /= exposure_us
    if normalize_sum:
        total = float(values.sum())
        if not np.isfinite(total) or total <= 0:
            raise ValueError("normalization requires positive finite total intensity")
        values /= total
    return values
