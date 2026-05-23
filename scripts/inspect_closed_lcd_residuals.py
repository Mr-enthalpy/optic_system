#!/usr/bin/env python3
"""Inspect a closed-LCD averaged-frame residual HDF5."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import h5py
import numpy as np


def inspect_closed_lcd_residuals(noise_h5: str | Path) -> dict[str, Any]:
    path = Path(noise_h5)
    with h5py.File(str(path), "r") as f:
        crops = f["closed_lcd/crops_avg10"]
        residuals = f["closed_lcd/residuals_avg10"]
        mean = f["closed_lcd/mean_avg10"]
        wavelengths = np.asarray(f["metadata/wavelengths_nm"][()], dtype=np.float64).tolist()
        exposure = np.asarray(f["metadata/exposure_us"][()], dtype=np.float64).tolist()
        gain = np.asarray(f["metadata/gain_db"][()], dtype=np.float64).tolist()
        out = {
            "path": str(path),
            "release_name": str(f.attrs.get("release_name", "")),
            "release_type": str(f.attrs.get("release_type", "")),
            "crops_avg10_shape": list(crops.shape),
            "mean_avg10_shape": list(mean.shape),
            "residuals_avg10_shape": list(residuals.shape),
            "wavelengths_nm": wavelengths,
            "exposure_us": exposure,
            "gain_db": gain,
            "n_repeats": int(f["metadata/n_repeats"][()]),
            "n_avg_frames": int(f["metadata/n_avg_frames"][()]),
            "roi_name": _decode(f["metadata/roi_name"][()]),
            "source_mask_id": _decode(f["metadata/source_mask_id"][()]),
            "is_sensor_dark": bool(f["metadata/is_sensor_dark"][()]),
            "is_single_frame_burst": bool(f["metadata/is_single_frame_burst"][()]),
            "is_closed_lcd_residual": bool(f["metadata/is_closed_lcd_residual"][()]),
            "per_wavelength": [],
        }
        residual_values = residuals[()]
        crops_values = crops[()]
        for i, wl in enumerate(wavelengths):
            local = residual_values[i]
            out["per_wavelength"].append(
                {
                    "wavelength_nm": float(wl),
                    "mean_count": float(np.mean(crops_values[i])),
                    "residual_mean": float(np.mean(local)),
                    "residual_std": float(np.std(local)),
                    "residual_abs_p95": float(np.percentile(np.abs(local), 95.0)),
                }
            )
    return out


def _decode(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return str(value)


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect closed-LCD residual HDF5")
    parser.add_argument("--noise-h5", required=True)
    args = parser.parse_args()
    print(json.dumps(inspect_closed_lcd_residuals(args.noise_h5), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
