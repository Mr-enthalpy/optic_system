#!/usr/bin/env python3
"""Validate the closed-LCD averaged-frame residual release contract."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import h5py
import numpy as np


EXPECTED_WAVELENGTHS = np.asarray([450.0, 550.0, 650.0], dtype=np.float64)
EXPECTED_EXPOSURE_US = np.asarray([779.6875, 487.3046875, 2241.6015625], dtype=np.float64)
EXPECTED_GAIN_DB = np.asarray([0.0, 0.0, 0.0], dtype=np.float64)
FORBIDDEN_CLAIMS = {
    "real camera noise",
    "read noise",
    "sensor-only dark",
    "complete noise model",
}


def validate_closed_lcd_residual_release(release_dir: str | Path | None = None, noise_h5: str | Path | None = None) -> dict[str, Any]:
    if release_dir is None and noise_h5 is None:
        raise ValueError("release_dir or noise_h5 is required")
    release_path = Path(release_dir) if release_dir is not None else None
    h5_path = Path(noise_h5) if noise_h5 is not None else release_path / "closed_lcd_roi512_avg10_residuals.h5"  # type: ignore[operator]
    errors: list[str] = []
    warnings: list[str] = []
    if not h5_path.exists():
        raise FileNotFoundError(h5_path)

    with h5py.File(str(h5_path), "r") as f:
        _require(f, "closed_lcd/crops_avg10", errors)
        _require(f, "closed_lcd/mean_avg10", errors)
        _require(f, "closed_lcd/residuals_avg10", errors)
        _require(f, "closed_lcd/std_across_repeats", errors)
        _require(f, "metadata/wavelengths_nm", errors)
        if errors:
            return _result(False, errors, warnings)

        crops = np.asarray(f["closed_lcd/crops_avg10"][()], dtype=np.float64)
        mean = np.asarray(f["closed_lcd/mean_avg10"][()], dtype=np.float64)
        residuals = np.asarray(f["closed_lcd/residuals_avg10"][()], dtype=np.float64)
        std_map = np.asarray(f["closed_lcd/std_across_repeats"][()], dtype=np.float64)
        wavelengths = np.asarray(f["metadata/wavelengths_nm"][()], dtype=np.float64)
        exposure = np.asarray(f["metadata/exposure_us"][()], dtype=np.float64)
        gain = np.asarray(f["metadata/gain_db"][()], dtype=np.float64)

        if crops.shape != (3, 5, 512, 512):
            errors.append(f"crops_avg10 shape must be [3,5,512,512], got {list(crops.shape)}")
        if mean.shape != (3, 512, 512):
            errors.append(f"mean_avg10 shape must be [3,512,512], got {list(mean.shape)}")
        if residuals.shape != crops.shape:
            errors.append(f"residuals_avg10 shape must match crops_avg10, got {list(residuals.shape)}")
        if std_map.shape != mean.shape:
            errors.append(f"std_across_repeats shape must match mean_avg10, got {list(std_map.shape)}")
        if not np.allclose(wavelengths, EXPECTED_WAVELENGTHS, rtol=0, atol=1e-9):
            errors.append(f"wavelengths must equal {EXPECTED_WAVELENGTHS.tolist()}, got {wavelengths.tolist()}")
        if not np.allclose(exposure, EXPECTED_EXPOSURE_US, rtol=0, atol=1e-6):
            errors.append(f"exposure_us does not match Phase 3.4 dictionary exposure: {exposure.tolist()}")
        if not np.allclose(gain, EXPECTED_GAIN_DB, rtol=0, atol=1e-9):
            errors.append(f"gain_db must be all zero, got {gain.tolist()}")
        if int(f["metadata/n_repeats"][()]) != 5:
            errors.append("n_repeats must be 5")
        if int(f["metadata/n_avg_frames"][()]) != 10:
            errors.append("n_avg_frames must be 10")
        if _decode(f["metadata/roi_name"][()]) != "roi_512":
            errors.append("roi_name must be roi_512")
        if list(np.asarray(f["metadata/roi_shape"][()], dtype=np.int64)) != [512, 512]:
            errors.append("roi_shape must be [512, 512]")
        if _decode(f["metadata/source_mask_id"][()]) != "all_closed_window":
            errors.append("source_mask_id must be all_closed_window")
        if bool(f["metadata/is_sensor_dark"][()]):
            errors.append("is_sensor_dark must be false")
        if bool(f["metadata/is_single_frame_burst"][()]):
            errors.append("is_single_frame_burst must be false")
        if not bool(f["metadata/is_closed_lcd_residual"][()]):
            errors.append("is_closed_lcd_residual must be true")

        forbidden = {_decode(x) for x in f["metadata/forbidden_claims"][()]}
        if not FORBIDDEN_CLAIMS.issubset(forbidden):
            errors.append(f"forbidden_claims missing required claims: {sorted(FORBIDDEN_CLAIMS - forbidden)}")
        if not np.all(np.isfinite(crops)) or not np.all(np.isfinite(mean)) or not np.all(np.isfinite(residuals)):
            errors.append("arrays contain NaN or Inf")
        if np.max(crops) >= 255.0:
            errors.append("crops contain saturated values >= 255")
        if not np.allclose(residuals, crops - mean[:, np.newaxis, :, :], rtol=1e-6, atol=1e-5):
            errors.append("residuals_avg10 must equal crops_avg10 - mean_avg10")
        if not np.allclose(std_map, np.std(crops, axis=1), rtol=1e-6, atol=1e-5):
            errors.append("std_across_repeats must equal std(crops_avg10, axis=1)")
        residual_mean = np.mean(residuals, axis=(1, 2, 3))
        if not np.all(np.abs(residual_mean) < 1e-5):
            errors.append(f"residuals per wavelength must have near-zero mean, got {residual_mean.tolist()}")
        if "closed_lcd/residuals_256" in f and f["closed_lcd/residuals_256"].shape != (3, 5, 256, 256):
            errors.append(f"residuals_256 shape must be [3,5,256,256], got {list(f['closed_lcd/residuals_256'].shape)}")

    if release_path is not None:
        for rel in [
            "README.md",
            "DATASET.md",
            "LIMITATIONS.md",
            "REPRODUCE.md",
            "CHANGELOG.md",
            "manifest.json",
            "closed_lcd_roi512_avg10_stats.csv",
            "figures/residual_histograms.png",
            "figures/residual_power_spectrum.png",
            "provenance/source_files.json",
            "provenance/extraction_log.json",
            "provenance/checksum_manifest.json",
            "provenance/raw_h5_paths.txt",
        ]:
            if not (release_path / rel).exists():
                errors.append(f"release file missing: {rel}")
        readme = (release_path / "README.md").read_text(encoding="utf-8") if (release_path / "README.md").exists() else ""
        if "complete real sensor noise model" not in readme:
            warnings.append("README does not explicitly contain the complete-sensor-noise limitation wording")
        manifest_path = release_path / "manifest.json"
        if manifest_path.exists():
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if manifest.get("release_type") != "closed_lcd_avg10_roi_residual":
                errors.append("manifest release_type mismatch")
            if manifest.get("is_sensor_dark") is not False:
                errors.append("manifest must state is_sensor_dark=false")
    return _result(not errors, errors, warnings)


def _require(f: h5py.File, path: str, errors: list[str]) -> None:
    if path not in f:
        errors.append(f"missing dataset: {path}")


def _result(ok: bool, errors: list[str], warnings: list[str]) -> dict[str, Any]:
    return {"ok": bool(ok), "errors": errors, "warnings": warnings}


def _decode(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return str(value)


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate closed-LCD residual release")
    parser.add_argument("--release-dir")
    parser.add_argument("--noise-h5")
    args = parser.parse_args()
    result = validate_closed_lcd_residual_release(args.release_dir, args.noise_h5)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    if not result["ok"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
