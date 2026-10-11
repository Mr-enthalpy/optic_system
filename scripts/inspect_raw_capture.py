"""Inspect the explicit RawCapture v3 observation contract, without hardware."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import h5py
from tasks.raw_capture_h5 import RAW_CAPTURE_SCHEMA_VERSION
from tasks.measurement.json_io import decode_h5_string


def capture_summary(path: str | Path) -> dict:
    with h5py.File(path, "r") as f:
        if f.attrs.get("raw_capture_schema_version") != RAW_CAPTURE_SCHEMA_VERSION:
            raise ValueError("unsupported RawCapture version; explicit conversion required")
        def read_json(name):
            return json.loads(decode_h5_string(f[name][()]))
        def array_info(name):
            return {"shape": list(f[name].shape), "dtype": str(f[name].dtype)} if name in f else None
        return {
            "plan_id": decode_h5_string(f["capture/plan_id"][()]),
            "raw_capture_schema_version": int(f.attrs["raw_capture_schema_version"]),
            "capture_role": decode_h5_string(f.attrs["capture_role"]),
            "runtime_mode": decode_h5_string(f["capture/runtime_mode"][()]),
            "processing_flags": read_json("capture/processing_flags_json"),
            "illumination": [json.loads(decode_h5_string(v)) for v in f["illumination/illumination_json"][()]],
            "display_state_ids": [decode_h5_string(v) for v in f["masks/mask_id"][()]],
            "masks_physical": array_info("masks/masks_physical"),
            "frames_avg": array_info("raw/frames_avg"),
            "frames_burst": array_info("raw/frames"),
            "camera_settings": {name: f["camera/" + name][()].tolist() for name in (
                "requested_exposure_us", "requested_gain_db", "readback_exposure_us", "readback_gain_db")},
            "lcd_metadata": read_json("lcd/metadata_json"),
            "profile_requirements": read_json("profiles/requirements_json"),
        }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path")
    parser.add_argument("--verbose", action="store_true", help="also list dataset shapes")
    args = parser.parse_args(argv)
    print(json.dumps(capture_summary(args.path), indent=2, sort_keys=True))
    if args.verbose:
        with h5py.File(args.path, "r") as f:
            f.visititems(lambda name, item: print(name, item.shape, item.dtype) if isinstance(item, h5py.Dataset) else None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
