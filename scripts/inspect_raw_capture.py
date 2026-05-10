"""
Print structure and key metadata of a raw capture HDF5 file.

No scientific interpretation — purely structural inspection.

Usage::

    python scripts/inspect_raw_capture.py data/raw/example.h5
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import h5py
import numpy as np


def _h5_str(val) -> str:
    if isinstance(val, bytes):
        return val.decode()
    if isinstance(val, np.ndarray) and val.size == 1:
        return _h5_str(val.flat[0])
    return str(val)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Inspect a raw capture HDF5 file"
    )
    parser.add_argument("path", help="path to raw_capture.h5")
    parser.add_argument("--verbose", action="store_true",
                       help="list all datasets with shapes")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    path = Path(args.path)
    if not path.exists():
        print(f"Error: file not found: {path}")
        return 1

    with h5py.File(path, "r") as f:

        header(f, "Plan")
        plan_json = _h5_str(f.get("capture/plan_json", ""))
        plan_id = _h5_str(f.attrs.get("plan_id", _h5_str(f.get("capture/plan_id", ""))))
        print(f"  plan_id:         {plan_id}")

        header(f, "Capture index")
        n_captures = _ds_len(f, "capture/capture_index")
        n_completed = int(np.sum(f["capture/completed"][:] == 1)
                         if "capture/completed" in f else 0)
        print(f"  n_captures:      {n_captures}")
        print(f"  n_completed:     {n_completed}")

        header(f, "Wavelengths")
        if "tls/wavelength_nm" in f:
            wl = f["tls/wavelength_nm"][:]
            grat = f["tls/grating"][:] if "tls/grating" in f else None
            n_wl = len(wl)
            print(f"  n_wavelengths:   {n_wl}")
            for i in range(min(n_wl, 10)):
                g_val = int(grat[i]) if grat is not None else -1
                print(f"  [{i}] {float(wl[i]):.1f} nm  grating={g_val}")
        else:
            print("  (no TLS data)")

        header(f, "Masks")
        if "masks/mask_id" in f:
            mask_ids = f["masks/mask_id"][:]
            n_masks = len(mask_ids)
            print(f"  n_masks:         {n_masks}")
            for i in range(min(n_masks, 10)):
                mid = _h5_str(mask_ids[i])
                print(f"  [{i}] {mid}")
        if "masks/masks_physical" in f:
            d = f["masks/masks_physical"]
            print(f"  mask_shape:      {d.shape[1]}×{d.shape[2]}")

        header(f, "Frames")
        if "raw/frames_avg" in f:
            d = f["raw/frames_avg"]
            print(f"  frames_avg:      {d.shape[1]}×{d.shape[2]}  ({d.shape[0]} captures)")
        if "raw/frames" in f:
            d = f["raw/frames"]
            print(f"  frames (burst):  {d.shape[1]} frames, {d.shape[2]}×{d.shape[3]}  ({d.shape[0]} captures)")
        store_burst = f["raw"].attrs.get("store_burst", False) if "raw" in f else False
        fps = f["raw"].attrs.get("frames_per_capture", 0) if "raw" in f else 0
        print(f"  store_burst:     {store_burst}")
        print(f"  fps_per_capture: {fps}")

        header(f, "Camera")
        if "camera/timestamp_ns" in f:
            cam_ts = f["camera/timestamp_ns"][:]
            print(f"  timestamps:      {len(cam_ts)} entries")
            if "camera/status_json" in f and len(f["camera/status_json"][:]) > 0:
                print(f"  status[0]:       {_h5_str(f['camera/status_json'][0])[:120]}")

        header(f, "LCD")
        if "lcd/display_timestamp_ns" in f:
            print(f"  timestamps:      {len(f['lcd/display_timestamp_ns'][:])} entries")
        if "lcd/mapping_policy_json" in f:
            print(f"  mapping_policy:  {_h5_str(f['lcd/mapping_policy_json'])[:100]}")

        header(f, "TLS")
        if "tls/status_json" in f and len(f["tls/status_json"][:]) > 0:
            print(f"  status[0]:       {_h5_str(f['tls/status_json'][0])[:120]}")

        header(f, "Processing flags")
        pf_str = _h5_str(f.get("capture/processing_flags_json", "{}"))
        import json as _json
        try:
            pf = _json.loads(pf_str)
            for k, v in pf.items():
                print(f"  {k}:{' '*(30-len(k))}{v}")
        except Exception:
            print(f"  {pf_str}")

        if args.verbose:
            header(f, "All groups and datasets")
            _print_tree(f)

    return 0


def header(f: h5py.File, title: str) -> None:
    print(f"\n{'─' * 56}")
    print(f"  {title}")


def _ds_len(f: h5py.File, name: str) -> int:
    if name in f:
        return len(f[name])
    return 0


def _print_tree(f: h5py.File, prefix: str = "") -> None:
    for key in sorted(f.keys()):
        obj = f[key]
        if isinstance(obj, h5py.Group):
            print(f"  {prefix}{key}/")
            _print_tree(obj, prefix=prefix + key + "/")
        else:
            print(f"  {prefix}{key}  shape={obj.shape}  dtype={obj.dtype}")


if __name__ == "__main__":
    raise SystemExit(main())
