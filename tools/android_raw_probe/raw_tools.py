"""Lossless plane decoding and conservative diagnostics for UDC RAW Probe v1.

NumPy outputs retain sensor DN. PNG scaling and black subtraction affect copies only.
Synthetic fixtures test memory layouts; they are never physical RAW observations.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np

RAW_SENSOR, RAW10 = 32, 37


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def local_file(base: Path, name: str) -> Path:
    p = (base / name).resolve()
    if not p.is_relative_to(base.resolve()):
        raise ValueError("Artifact path escapes its run directory")
    return p


def decode_plane(data: bytes, metadata: dict[str, Any]) -> np.ndarray:
    """Decode one Bayer plane, including short last-row padding and explicit offsets."""
    width, height = int(metadata["width"]), int(metadata["height"])
    if width <= 0 or height <= 0 or len(metadata["planes"]) != 1:
        raise ValueError("Expected a positive-size single-plane Bayer image")
    plane = metadata["planes"][0]
    if len(data) != int(plane["saved_bytes"]):
        raise ValueError("Saved byte count mismatch")
    if plane.get("sha256") != hashlib.sha256(data).hexdigest():
        raise ValueError("Plane SHA256 mismatch")
    row, pixel = int(plane["row_stride"]), int(plane["pixel_stride"])
    offset = int(plane["data_offset_bytes"])
    if row <= 0 or offset < 0:
        raise ValueError("Invalid row stride or data offset")
    fmt = int(metadata["format"])
    if fmt == RAW_SENSOR:
        if pixel < 2:
            raise ValueError("RAW_SENSOR requires at least two bytes per sample")
        used = (width - 1) * pixel + 2
    elif fmt == RAW10:
        if width % 4 or height % 2 or pixel != 0:
            raise ValueError("Android RAW10 requires width multiple of 4, even height, pixelStride=0")
        used = (width // 4) * 5
    else:
        raise ValueError(f"Not a Bayer RAW format: {fmt}")
    if row < used or offset + (height - 1) * row + used > len(data):
        raise ValueError("Truncated plane or overlapping image rows")
    if fmt == RAW_SENSOR:
        order = metadata["byte_order"]
        if order not in ("LITTLE_ENDIAN", "BIG_ENDIAN"):
            raise ValueError("Explicit native byte order required")
        dtype = "<u2" if order == "LITTLE_ENDIAN" else ">u2"
        return np.ndarray((height, width), dtype=dtype, buffer=data,
                          offset=offset, strides=(row, pixel)).astype(np.uint16, copy=True)
    packed = np.ndarray((height, width // 4, 5), dtype=np.uint8, buffer=data,
                        offset=offset, strides=(row, 5, 1)).astype(np.uint16)
    pixels = (packed[:, :, :4] << 2) | ((packed[:, :, 4, None] >> np.array([0, 2, 4, 6])) & 3)
    return pixels.reshape(height, width).astype(np.uint16)


def read_image(path: Path) -> tuple[np.ndarray, dict[str, Any]]:
    meta = load_json(path)
    if meta.get("role") != "raw":
        raise ValueError("Companion JPEG/YUV must not be decoded as Bayer RAW")
    plane = meta["planes"][0]
    return decode_plane(local_file(path.parent, plane["file"]).read_bytes(), meta), meta


def serialization_issues(value: Any, path: str = "") -> list[str]:
    issues = []
    if isinstance(value, dict):
        if value.get("serialization_status") == "opaque_value_not_lossless":
            issues.append(path + ": opaque value (type retained)")
        for key, child in value.items():
            if key == "errors" and child:
                issues.append(path + ": metadata key read/serialization errors " + str(list(child)))
            issues.extend(serialization_issues(child, path + "/" + key))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            issues.extend(serialization_issues(child, path + "/" + str(index)))
    return issues


def frame_records(run_path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    run = load_json(run_path / "run.json")
    if run.get("capture_complete") and len(run.get("frames", [])) != run.get("requested_frames"):
        raise ValueError("Completed run has an inconsistent frame count")
    if run.get("schema_version") != 1:
        raise ValueError("Unsupported run schema")
    chars = load_json(run_path / "characteristics.json")["values"]
    if chars.get("android.lens.facing") != 0 or run.get("lens_facing") != "FRONT":
        raise ValueError("Run does not consistently identify a front camera")
    inventory = load_json(run_path / "inventory.json")
    matching = [c for c in inventory["cameras"] if c["camera_id"] == run["camera_id"]]
    if len(matching) != 1 or matching[0]["lens_facing"] != 0:
        raise ValueError("Front ID not supported by the recorded application inventory")
    records = []
    seen = set()
    seen_timestamps = set()
    for frame in run["frames"]:
        index = int(frame["index"])
        if not frame.get("complete") or index in seen:
            raise ValueError("Incomplete or duplicate frame entry")
        seen.add(index)
        raw, meta = read_image(local_file(run_path, frame["raw"]["metadata_file"]))
        result_path = run_path / f"frame_{index:04d}_result.json"
        result_document = load_json(result_path)
        request_document = load_json(run_path / f"frame_{index:04d}_request.json")
        result = result_document["values"]
        request = request_document["values"]
        timestamp = result.get("android.sensor.timestamp")
        if timestamp is None or timestamp in seen_timestamps or meta["timestamp_ns"] != timestamp or frame["sensor_timestamp_ns"] != timestamp:
            raise ValueError("RAW / CaptureResult timestamp mismatch or duplicate timestamp")
        seen_timestamps.add(timestamp)
        if meta["camera_id"] != run["camera_id"] or int(meta["format"]) != run["raw_format"]:
            raise ValueError("RAW camera or format disagrees with its run")
        for role in ("raw", "companion"):
            if role in frame and frame[role]["timestamp_ns"] != timestamp:
                raise ValueError("Companion is not from the same sensor timestamp")
        if "companion" in frame:
            companion = load_json(local_file(run_path, frame["companion"]["metadata_file"]))
            if companion["timestamp_ns"] != timestamp or companion["camera_id"] != run["camera_id"]:
                raise ValueError("Companion file timestamp/camera mismatch")
            for plane in companion["planes"]:
                content = local_file(run_path, plane["file"]).read_bytes()
                if len(content) != plane["saved_bytes"] or hashlib.sha256(content).hexdigest() != plane["sha256"]:
                    raise ValueError("Companion plane integrity failure")
        if request.get("android.sensor.exposureTime") != run["requested_exposure_ns"] or request.get("android.sensor.sensitivity") != run["requested_iso"]:
            raise ValueError("Stored request disagrees with the run manifest")
        actual_ns = result.get("android.sensor.exposureTime")
        actual_iso = result.get("android.sensor.sensitivity")
        manual = (actual_ns is not None and actual_iso is not None
                  and result.get("android.control.aeMode") == 0
                  and abs(actual_ns - run["requested_exposure_ns"]) / run["requested_exposure_ns"] <= .02
                  and abs(actual_iso - run["requested_iso"]) <= max(1, .01 * run["requested_iso"]))
        # Recompute from result; do not trust the app's convenience boolean alone.
        stats = {"index": index, "timestamp_ns": timestamp, "shape_hw": list(raw.shape),
                 "dtype": str(raw.dtype), "observed_min_dn": int(raw.min()), "observed_max_dn": int(raw.max()),
                 "observed_max_bit_length": int(raw.max()).bit_length(),
                 "mean_dn": float(raw.mean()), "std_dn": float(raw.std()),
                 "actual_exposure_ns": actual_ns, "actual_iso": actual_iso,
                 "post_raw_sensitivity_boost": result.get("android.control.postRawSensitivityBoost"),
                 "dynamic_black_level": result.get("android.sensor.dynamicBlackLevel"),
                 "lens_shading_map_returned": result.get("android.statistics.lensShadingCorrectionMap") is not None,
                 "metadata_serialization_issues": serialization_issues(result_document) + serialization_issues(request_document),
                 "manual_readback_within_tolerance": bool(manual),
                 "tile_means_dn": {f"p{y}{x}": float(raw[y::2, x::2].mean()) for y in range(2) for x in range(2)},
                 "plane_sha256": meta["planes"][0]["sha256"]}
        white = chars.get("android.sensor.info.whiteLevel")
        stats["fraction_at_or_above_declared_white"] = float(np.mean(raw >= white)) if white is not None else None
        records.append({"stats": stats, "metadata": meta, "frame": frame})
    return run, records


def preview(raw: np.ndarray, target: Path, title: str) -> None:
    import matplotlib
    matplotlib.use("Agg")
    from matplotlib import pyplot as plt
    # Plot parity tiles separately: color assignment needs sensor-coordinate parity evidence.
    fig, axes = plt.subplots(2, 2, figsize=(10, 8), constrained_layout=True)
    for y in range(2):
        for x in range(2):
            tile = raw[y::2, x::2].astype(np.float64)
            ax = axes[y, x]
            ax.imshow(np.log1p(tile), cmap="gray", interpolation="nearest")
            ax.set_title(f"p{y}{x}; log1p(DN), independent display scaling")
            ax.set_xlabel("2x2 tile x"); ax.set_ylabel("2x2 tile y")
    fig.suptitle(title + "\nNo demosaic, denoising or modification of saved DN")
    fig.savefig(target, dpi=140); plt.close(fig)


def decode_run(run_path: Path, output: Path) -> dict[str, Any]:
    run, records = frame_records(run_path)
    chars = load_json(run_path / "characteristics.json")["values"]
    output.mkdir(parents=True, exist_ok=False)
    mean = m2 = None
    for n, record in enumerate(records, 1):
        i = record["stats"]["index"]
        raw, _ = read_image(local_file(run_path, record["frame"]["raw"]["metadata_file"]))
        np.save(output / f"frame_{i:04d}.npy", raw, allow_pickle=False)
        values = raw.astype(np.float64)
        if mean is None:
            mean = values.copy(); m2 = np.zeros_like(values)
        else:
            delta = values - mean; mean += delta / n; m2 += delta * (values - mean)
    temporal = {"evaluated": False, "note": "Includes illumination/motion variation; not an isolated sensor-noise estimate."}
    if mean is not None:
        np.save(output / "temporal_mean_dn.npy", mean, allow_pickle=False)
    if len(records) > 1:
        std = np.sqrt(m2 / (len(records)-1))
        np.save(output / "temporal_std_dn.npy", std, allow_pickle=False)
        temporal.update(evaluated=True, median_fixed_pixel_std_dn=float(np.median(std)), p95_fixed_pixel_std_dn=float(np.percentile(std, 95)), frames=len(records))
    if records:
        first, _ = read_image(local_file(run_path, records[0]["frame"]["raw"]["metadata_file"]))
        preview(first, output / "first_frame_tiles.png", f"{run_path.name}: {run['condition']}")
    cfa_names = {0: "RGGB", 1: "GRBG", 2: "GBRG", 3: "BGGR", 4: "RGB"}
    summary = {"schema_version": 1, "run_id": run["run_id"], "camera_id": run["camera_id"],
               "capture_complete": run["capture_complete"], "run_succeeded": run["run_succeeded"],
               "validated_raw_frames": len(records), "at_least_10_validated_front_raw_frames": len(records) >= 10,
               "all_manual_readbacks_within_tolerance": bool(records) and all(r["stats"]["manual_readback_within_tolerance"] for r in records),
               "declared_cfa": cfa_names.get(chars.get("android.sensor.info.colorFilterArrangement"), "unknown"),
               "declared_white_level": chars.get("android.sensor.info.whiteLevel"),
               "declared_black_level_pattern": chars.get("android.sensor.blackLevelPattern"),
               "lens_shading_applied": chars.get("android.sensor.info.lensShadingApplied"),
               "cfa_image_origin_verified": False,
               "cfa_note": "Parity tiles are labelled p00/p01/p10/p11. Sensor CFA declaration alone does not prove the output crop origin.",
               "effective_sensor_bit_depth_verified": False,
               "bit_depth_note": "RAW10 packs 10 bits. RAW_SENSOR uses a 16-bit container; white level and observed range are evidence, not an ADC precision measurement.",
               "illumination": {"label": run["condition"], "operator_verified": run["condition_verified_by_operator"], "notes": run["condition_notes"]},
               "optical_diffraction_verified": False,
               "temporal_stability": temporal,
               "metadata_serialization_issues": serialization_issues(load_json(run_path / "characteristics.json")) + [issue for record in records for issue in record["stats"]["metadata_serialization_issues"]],
               "frames": [r["stats"] for r in records]}
    (output / "summary.json").write_text(json.dumps(summary, indent=2, allow_nan=False), encoding="utf-8")
    return summary


def analyze_series(paths: list[Path], output: Path) -> dict[str, Any]:
    batches = [frame_records(p) for p in paths]
    keys = {(r["camera_id"], r["firmware_fingerprint"], r["raw_format"], r["requested_iso"],
             (r["raw_size"]["width"], r["raw_size"]["height"])) for r, records in batches}
    if len(keys) != 1:
        raise ValueError("A series must use the same camera, firmware, format, ISO and image size")
    rows = []
    for run, records in batches:
        values = np.array([r["stats"]["mean_dn"] for r in records], dtype=float)
        rows.append({"run_id": run["run_id"], "scene": run["condition"], "condition_verified": run["condition_verified_by_operator"],
                     "notes": run["condition_notes"], "requested_exposure_ns": run["requested_exposure_ns"],
                     "actual_exposure_ns": float(np.mean([r["stats"]["actual_exposure_ns"] for r in records])) if records and all(r["stats"]["actual_exposure_ns"] is not None for r in records) else None,
                     "actual_iso_values": sorted({r["stats"]["actual_iso"] for r in records}, key=str),
                     "frames": len(records), "all_manual_readbacks_valid": bool(records) and all(r["stats"]["manual_readback_within_tolerance"] for r in records),
                     "capture_complete": run["capture_complete"],
                     "mean_dn": float(values.mean()) if len(values) else None,
                     "temporal_std_of_frame_mean_dn": float(values.std(ddof=1)) if len(values) > 1 else None,
                     "max_saturation_fraction": max((r["stats"]["fraction_at_or_above_declared_white"] for r in records if r["stats"]["fraction_at_or_above_declared_white"] is not None), default=None)})
    eligible = [r for r in rows if r["condition_verified"] and r["capture_complete"] and r["frames"] >= 3 and r["all_manual_readbacks_valid"]]
    levels = []
    for exposure_ns in sorted({r["requested_exposure_ns"] for r in eligible if r["scene"] == "flat"}):
        flats = [r for r in eligible if r["scene"] == "flat" and r["requested_exposure_ns"] == exposure_ns]
        darks = [r for r in eligible if r["scene"] == "dark" and r["requested_exposure_ns"] == exposure_ns]
        if not darks: continue
        flat = float(np.mean([r["mean_dn"] for r in flats])); dark = float(np.mean([r["mean_dn"] for r in darks]))
        levels.append({"requested_exposure_ns": exposure_ns, "actual_exposure_ns": float(np.mean([r["actual_exposure_ns"] for r in flats])),
                       "dark_subtracted_mean_dn": flat-dark, "dark_mean_dn": dark,
                       "flat_runs": [r["run_id"] for r in flats], "dark_runs": [r["run_id"] for r in darks]})
    linearity = {"status": "insufficient_data", "required": "At least 3 exposure levels, each with >=3 verified dark and flat frames at fixed readback ISO; unsaturated source, unchanged geometry and stable illumination.", "levels": levels}
    if len(levels) >= 3 and all(r["max_saturation_fraction"] is not None and r["max_saturation_fraction"] < .01 and r["actual_iso_values"] == [batches[0][0]["requested_iso"]] for r in eligible if r["scene"] in ("dark", "flat")):
        x = np.array([r["actual_exposure_ns"] for r in levels]) / 1e6
        y = np.array([r["dark_subtracted_mean_dn"] for r in levels])
        slope, intercept = np.polyfit(x, y, 1); residual = y-(slope*x+intercept)
        linearity.update(status="descriptive_fit_only", slope_dn_per_ms=float(slope), intercept_dn=float(intercept),
                         max_abs_residual_dn=float(np.max(np.abs(residual))),
                         max_abs_residual_fraction_of_signal_span=float(np.max(np.abs(residual))/np.ptp(y)) if np.ptp(y) > 0 else None,
                         note="No acceptance threshold supplied. This fit cannot independently establish illumination stability or radiometric calibration.")
    report = {"schema_version": 1, "batches": rows, "exposure_linearity": linearity,
              "point_source_runs": [r["run_id"] for r in rows if r["scene"] == "point" and r["condition_verified"]],
              "diffraction_verified": False, "scientific_acceptance": "requires physical review; software does not label spatial structure as diffraction"}
    output.mkdir(parents=True, exist_ok=False)
    (output / "series.json").write_text(json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest="command", required=True)
    dec = subs.add_parser("decode"); dec.add_argument("run", type=Path); dec.add_argument("output", type=Path)
    series = subs.add_parser("series"); series.add_argument("runs", type=Path, nargs="+"); series.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = decode_run(args.run, args.output) if args.command == "decode" else analyze_series(args.runs, args.output)
    print(json.dumps({"output": str(args.output.resolve()), "validated_frames": result.get("validated_raw_frames"), "linearity_status": result.get("exposure_linearity", {}).get("status")}, indent=2))


if __name__ == "__main__":
    main()
