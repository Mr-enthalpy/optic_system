#!/usr/bin/env python3
"""
Inspect Phase 3.0.5b PSF-safe exposure sweep HDF5.

Prints a table of every sweep row and the final safe parameters.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import h5py
import numpy as np


def _reldir() -> Path:
    return Path(__file__).resolve().parents[1]


def _h5_str_scalar(dset) -> str:
    val = dset[()]
    if isinstance(val, np.ndarray):
        val = val.flat[0]
    if isinstance(val, bytes):
        return val.decode()
    return str(val)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Inspect exposure sweep HDF5",
    )
    parser.add_argument(
        "h5_path", nargs="?", default="data/raw/bishe_psf_safe_exposure.h5",
        help="Path to exposure sweep HDF5",
    )
    parser.add_argument(
        "--json", default="outputs/exposure_calibration/camera_params_psf_safe.json",
        help="Path to camera_params_psf_safe.json",
    )
    parser.add_argument(
        "--short", action="store_true",
        help="Only show final safe parameters, not full table",
    )
    args = parser.parse_args()

    h5_path = _reldir() / args.h5_path
    if not h5_path.exists():
        print(f"HDF5 not found: {h5_path}", file=sys.stderr)
        sys.exit(1)

    with h5py.File(h5_path, "r") as f:
        plan_id = _h5_str_scalar(f["capture/plan_id"])
        n = f["sweep/exposure_us"].shape[0]
        print(f"plan_id: {plan_id}")
        print(f"n_sweeps: {n}")
        full_scale = f["sweep"].attrs.get("frame_dtype_full_scale", 255)
        print(f"full_scale: {full_scale}")
        print()

        if not args.short:
            print(f"{'WL(nm)':>7s}  {'Exp(us)':>10s}  {'Gain(dB)':>8s}  "
                  f"{'MaxBurst':>8s}  {'MaxAvg':>7s}  {'P99.9':>7s}  "
                  f"{'SatCntB':>7s}  {'SatFracB':>8s}  "
                  f"{'pSig':>7s}  {'PSFSafe':>7s}  {'LowSig':>7s}")
            print("-" * 112)

            exp_arr = f["sweep/exposure_us"][:]
            gain_arr = f["sweep/gain_db"][:]
            wl_arr = f["sweep/wavelength_nm"][:]
            max_arr = f["sweep/max_pixel"][:]
            max_avg_arr = f["sweep/max_pixel_avg"][:] if "max_pixel_avg" in f["sweep"] else max_arr
            p99_arr = f["sweep/p99_9"][:]
            sat_count_arr = f["sweep/saturated_pixel_count"][:]
            sf_arr = f["sweep/saturated_fraction"][:]
            psf_safe_arr = f["sweep/psf_safe"][:]
            psig_arr = f["sweep/p_signal"][:]
            low_arr = f["sweep/low_signal"][:]

            for i in range(n):
                print(f"{wl_arr[i]:7.1f}  {exp_arr[i]:10.1f}  {gain_arr[i]:8.1f}  "
                      f"{max_arr[i]:8.1f}  {max_avg_arr[i]:7.1f}  "
                      f"{p99_arr[i]:7.1f}  {int(sat_count_arr[i]):7d}  "
                      f"{sf_arr[i]:8.4f}  {psig_arr[i]:7.1f}  "
                      f"{str(psf_safe_arr[i]):>7s}  {str(low_arr[i]):>7s}")

        print()

        pf_raw = _h5_str_scalar(f["capture/processing_flags_json"])
        pf = json.loads(pf_raw)
        print(f"completed: {pf.get('completed')}")
        print(f"error: {pf.get('error')}")
        print(f"n_sweeps_written: {pf.get('n_sweeps_written')}")

    json_path = _reldir() / args.json
    if json_path.exists():
        print()
        with open(json_path, "r", encoding="utf-8") as jf:
            params = json.load(jf)
        gsc = params.get("global_safe_camera", {})
        print(f"selection_reason: {params.get('selection_reason')}")
        print(f"global_safe_camera:")
        print(f"  exposure_us: {gsc.get('exposure_us')}")
        print(f"  gain_db: {gsc.get('gain_db')}")
        print(f"  gain_elevated: {gsc.get('gain_elevated')}")
        print(f"  frames_per_capture: {gsc.get('frames_per_capture')}")
        print(f"exposure_safety_valid: {params['validity'].get('exposure_safety_valid')}")
        print(f"psf_exposure_safe: {params['validity'].get('psf_exposure_safe')}")
        print(f"scientific_calibration_valid: {params['validity'].get('scientific_calibration_valid')}")
    else:
        print(f"\n(camera_params_psf_safe.json not found at {json_path})")


if __name__ == "__main__":
    main()
