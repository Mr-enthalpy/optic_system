"""Inspect the explicit RawCapture v3 observation contract, without hardware."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import h5py
from tasks.raw_capture_h5 import RAW_CAPTURE_SCHEMA_VERSION, _capture_schedule_is_complete
from tasks.measurement.json_io import decode_h5_string


def capture_summary(path: str | Path) -> dict:
    with h5py.File(path, "r") as f:
        if f.attrs.get("raw_capture_schema_version") != RAW_CAPTURE_SCHEMA_VERSION:
            raise ValueError("unsupported RawCapture version; explicit conversion required")
        def read_json(name):
            return json.loads(decode_h5_string(f[name][()]))
        def array_info(name):
            return {"shape": list(f[name].shape), "dtype": str(f[name].dtype)} if name in f else None
        flags = read_json("capture/processing_flags_json")
        plan = read_json("capture/plan_json")
        completed = f["capture/completed"][()]
        indices = [f[f"capture/{name}"][()] for name in ("capture_index", "wavelength_index", "mask_index")]
        schedule_complete = (
            completed.dtype.kind == "b" and all(a.shape == completed.shape and a.dtype.kind in "iu" for a in indices)
            and _capture_schedule_is_complete(completed, *indices,
                n_wavelengths=len(plan["wavelengths"]), n_masks=len(plan["masks"]))
        )
        capture_succeeded = (
            flags.get("capture_complete") is True and flags.get("run_succeeded") is True
            and schedule_complete and "raw/frames_avg" in f
            and f["raw/frames_avg"].ndim == 3
            and f["raw/frames_avg"].shape[0] == len(completed)
        )
        return {
            "plan_id": decode_h5_string(f["capture/plan_id"][()]),
            "raw_capture_schema_version": int(f.attrs["raw_capture_schema_version"]),
            "capture_role": decode_h5_string(f.attrs["capture_role"]),
            "runtime_mode": decode_h5_string(f["capture/runtime_mode"][()]),
            "processing_flags": flags,
            "completed_rows": int(completed.sum()),
            "expected_rows": len(plan["wavelengths"]) * len(plan["masks"]),
            "capture_succeeded": bool(capture_succeeded),
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
    parser.add_argument("--require-success", action="store_true", help="fail unless capture flags, schedule and frame count indicate success; does not certify optical validity")
    args = parser.parse_args(argv)
    try:
        summary = capture_summary(args.path)
    except (OSError, ValueError, KeyError) as exc:
        parser.exit(2, f"RawCapture inspection failed: {exc}\n")
    print(json.dumps(summary, indent=2, sort_keys=True))
    if args.require_success and not summary["capture_succeeded"]:
        parser.exit(2, "RawCapture is failed or incomplete; retain it for diagnostics\n")
    if args.verbose:
        with h5py.File(args.path, "r") as f:
            f.visititems(lambda name, item: print(name, item.shape, item.dtype) if isinstance(item, h5py.Dataset) else None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
