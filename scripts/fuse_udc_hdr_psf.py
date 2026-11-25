"""Fuse per-band exposure brackets into unit-total ROI PSF kernels.

The session manifest maps nominal wavelengths to already exported/decoded
batches. Native frames are fused first, then a measured far-exposure outer-ring
background is subtracted, then an even-origin 2x2 cell average is optionally
formed and each kernel is divided by its signed total.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def load_json(path: Path):
    with Path(path).open("r", encoding="utf-8") as stream:
        return json.load(stream)


def _batch(session: Path, item) -> Path:
    if isinstance(item, str):
        return session / item
    return session / f"batch_{int(item):03d}"


def _run_and_roi(session: Path, entry: dict, roi, *, expect_condition: str):
    import numpy as np
    directory = _batch(session, entry.get("path", entry.get("batch_index")))
    summary = load_json(directory / "decoded/summary.json")
    run_dir = directory / "export/device_files/captures" / summary["run_id"]
    run = load_json(run_dir / "run.json")
    if not summary.get("capture_complete") or not summary.get("run_succeeded"):
        raise ValueError(f"Batch {directory.name} is not a completed successful capture")
    if run.get("requested_exposure_ns") != int(entry["exposure_ns"]):
        raise ValueError(f"Batch {directory.name} exposure disagrees with the manifest")
    if run.get("condition") != expect_condition or run.get("condition_verified_by_operator") is not True:
        raise ValueError(f"Batch {directory.name} is not an operator-confirmed {expect_condition} capture")
    mean = np.load(directory / "decoded/temporal_mean_dn.npy", mmap_mode="r")
    values = np.asarray(mean[roi.y:roi.y+roi.height, roi.x:roi.x+roi.width], dtype=np.float64)
    if values.shape != (roi.height, roi.width):
        raise ValueError(f"Batch {directory.name} is smaller than the requested ROI")
    settings = tuple(run.get(key) for key in ("requested_shading_mode", "requested_tonemap_mode",
                                              "requested_display_panel", "requested_companion_size"))
    characteristics = load_json(run_dir / "characteristics.json").get("values", {})
    return run, values, settings, characteristics


def _dark_mean(session: Path, entries, roi):
    import numpy as np
    if not entries:
        raise ValueError("A matched dark batch is required for this exposure")
    means = []
    exposures = set()
    for index in entries:
        directory = _batch(session, index)
        summary = load_json(directory / "decoded/summary.json")
        run = load_json(directory / "export/device_files/captures" / summary["run_id"] / "run.json")
        if not summary.get("capture_complete") or not summary.get("run_succeeded"):
            raise ValueError(f"Dark batch {directory.name} is not a completed successful capture")
        if run.get("condition") != "dark" or run.get("condition_verified_by_operator") is not True:
            raise ValueError(f"Batch {directory.name} is not an operator-confirmed dark capture")
        exposures.add(int(run["requested_exposure_ns"]))
        mean = np.load(directory / "decoded/temporal_mean_dn.npy", mmap_mode="r")
        means.append(np.asarray(mean[roi.y:roi.y+roi.height, roi.x:roi.x+roi.width], dtype=np.float64))
    if len(exposures) != 1:
        raise ValueError("Paired dark batches must share one requested exposure")
    if len(means) == 1:
        return means[0], 0.0, exposures.pop()
    return np.mean(means, axis=0), float(np.mean(means[-1] - means[0])), exposures.pop()


def _unit_total(kernel):
    import numpy as np
    total = float(kernel.sum())
    if not np.isfinite(total) or abs(total) <= 1e-12:
        raise ValueError("Kernel has a zero, nonfinite or numerically unusable signed total")
    unit = kernel / total
    negative_mass = float(np.abs(unit[unit < 0]).sum())
    return unit, negative_mass, total


def _cell_average(values, cell: int):
    import numpy as np
    h, w = values.shape
    if h % cell or w % cell:
        raise ValueError("ROI extent must be divisible by the cell size")
    return values.reshape(h // cell, cell, w // cell, cell).mean(axis=(1, 3))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session", required=True, help="UDC plan output directory with batch_NNN/decoded")
    parser.add_argument("--manifest", required=True, help="session manifest mapping wavelengths to batches")
    parser.add_argument("--output", required=True, help="new .npz kernel file")
    parser.add_argument("--diagnostics", help="optional new .json diagnostics file")
    parser.add_argument("--ring-width", type=int, default=16, help="outer-ring width in native pixels")
    parser.add_argument("--cell-bin", type=int, default=2, help="even-origin cell average, 1 disables it")
    args = parser.parse_args(argv)
    output, diagnostics = Path(args.output), Path(args.diagnostics) if args.diagnostics else None
    if output.suffix != ".npz" or output.exists():
        parser.error("--output must be a new .npz path")
    if diagnostics is not None and (diagnostics.suffix != ".json" or diagnostics.exists()):
        parser.error("--diagnostics must be a new .json path")
    if args.cell_bin < 1 or args.ring_width < 1:
        parser.error("--cell-bin and --ring-width must be positive")

    import numpy as np
    from tasks.measurement.hdr import merge_exposure_bracket
    from tasks.measurement.roi import ROI

    session = Path(args.session)
    manifest = load_json(Path(args.manifest))
    if manifest.get("schema_version") != 1:
        raise ValueError("Expected manifest schema_version=1")
    roi = ROI(*map(int, manifest["roi_xywh"]))
    if args.cell_bin > 1 and (roi.x % args.cell_bin or roi.y % args.cell_bin):
        raise ValueError("Cell averaging requires an ROI origin aligned to the cell grid")
    if args.ring_width * 2 >= min(roi.width, roi.height):
        raise ValueError("Ring width is too large for the ROI")

    bands = manifest.get("bands") or []
    if not bands:
        raise ValueError("Manifest contains no bands")
    dark_table = {int(entry["exposure_ns"]): entry.get("batches", []) for entry in manifest.get("darks", [])}

    wavelengths, iso_values, base_us, run_ids = [], [], [], []
    native_kernels, cell_kernels = [], []
    backgrounds, negative_native, negative_cell = [], [], []
    below_background, all_saturated, source_counts = [], [], []
    seam_table, exposure_table, settings_table = [], [], []
    dark_drift_dn = []
    dark_scale_table = []

    ring = np.zeros((roi.height, roi.width), dtype=bool)
    ring[:args.ring_width, :] = True; ring[-args.ring_width:, :] = True
    ring[:, :args.ring_width] = True; ring[:, -args.ring_width:] = True

    expected = None
    for band in bands:
        batches = band.get("batches") or []
        if len(batches) < 2:
            raise ValueError("Each band needs at least two exposures")
        exposures = [int(item["exposure_ns"]) for item in batches]
        if exposures != sorted(exposures) or len(set(exposures)) != len(exposures):
            raise ValueError("Band exposures must be strictly increasing")
        if expected is None:
            expected = exposures
        elif exposures != expected:
            raise ValueError("All bands must share the same exposure bracket")

        frames, settings, ids, band_chars = [], [], [], []
        white_level = black_level = None
        band_iso = None
        for item in batches:
            run, values, request, characteristics = _run_and_roi(session, item, roi, expect_condition="point")
            if band_iso is None:
                band_iso = int(run["requested_iso"])
            if band_iso != int(run["requested_iso"]):
                raise ValueError("Bands disagree on ISO")
            if white_level is None:
                white_level = characteristics.get("android.sensor.info.whiteLevel")
            if white_level != characteristics.get("android.sensor.info.whiteLevel"):
                raise ValueError("Bands disagree on the declared white level")
            if black_level is None:
                black_level = characteristics.get("android.sensor.blackLevelPattern")
            if black_level != characteristics.get("android.sensor.blackLevelPattern"):
                raise ValueError("Bands disagree on the declared black level pattern")
            frames.append(values); settings.append(request); band_chars.append(characteristics)
            ids.append(run["run_id"])
        if len(set(settings)) != 1:
            raise ValueError("A band mixes different shading/tonemap/display/companion settings")
        if not isinstance(white_level, (int, float)) or not isinstance(black_level, list):
            raise ValueError("Declared white/black level is missing")
        black = float(np.mean(black_level))

        darks, dark_scaled_from = [], []
        for item, exposure in zip(batches, exposures):
            dark, drift, measured = _dark_mean(session, dark_table.get(exposure), roi)
            if measured != exposure:
                dark = black + (dark - black) * (exposure / measured)
            dark_scaled_from.append(None if measured == exposure else measured)
            darks.append(dark); dark_drift_dn.append(drift)

        merged = merge_exposure_bracket(frames, exposures, darks=darks, black_level=black,
                                        white_level=float(white_level))
        far_raw, far_dark = frames[-1], darks[-1]
        usable = ring & (far_raw < float(white_level))
        if not np.any(usable):
            raise ValueError("No unsaturated outer-ring pixels remain in the longest exposure")
        background = float(np.median(far_raw[usable] - far_dark[usable])) * (merged.reference_exposure_ns / exposures[-1])
        corrected = merged.signal_dn - background
        native, native_negative, native_total = _unit_total(corrected)
        if args.cell_bin == 1:
            cell, cell_negative, cell_total = native, native_negative, native_total
        else:
            cell, cell_negative, cell_total = _unit_total(_cell_average(corrected, args.cell_bin))

        wavelengths.append(float(band["wavelength_nm"]))
        iso_values.append(band_iso)
        base_us.append(merged.reference_exposure_ns / 1000.0)
        run_ids.append(ids); exposure_table.append(exposures); settings_table.append(list(settings))
        dark_scale_table.append(dark_scaled_from)
        native_kernels.append(native); cell_kernels.append(cell)
        backgrounds.append(background); negative_native.append(native_negative); negative_cell.append(cell_negative)
        below_background.append(float(np.mean(corrected < 0)))
        all_saturated.append(merged.all_saturated_fraction)
        source_counts.append(merged.counts_by_exposure)
        seam_table.append(merged.seam_ratios)
        print(json.dumps({"wavelength_nm": band["wavelength_nm"], "background_dn": background,
                          "native_total_dn": native_total, "native_negative_mass": native_negative,
                          "cell_total_dn": cell_total, "cell_negative_mass": cell_negative,
                          "all_saturated_fraction": merged.all_saturated_fraction}, sort_keys=True))

    metadata = {
        "schema_version": 1, "session_id": manifest.get("session_id"),
        "coordinate_frame": "acquired_frame", "sensor_origin_verified": False,
        "cfa_image_origin_verified": False, "roi_xywh": [roi.x, roi.y, roi.width, roi.height],
        "cell_bin": args.cell_bin,
        "normalization": "signed unit total; zero/nonfinite totals rejected; negative residuals preserved",
        "background": "dark-subtracted median of unsaturated far-exposure outer ring; no smooth model",
        "ring_width": args.ring_width, "cell_average": "unweighted arithmetic mean, even acquired-frame origin",
        "hdr_merge": "headroom-and-exposure weighted; saturated samples excluded; all-saturated pixels are lower bounds",
        "matched_dark_applied": True, "matched_dark_sets": "start/end averaged when both exist",
        "dark_exposure_scaling": "linear black-level-anchored scaling when the nearest dark exposure differs",
        "nonnegative_physical_kernel_verified": False, "source_spectral_purity_verified": False,
        "optical_diffraction_verified": False,
        "exposures_ns": exposure_table, "request_settings": settings_table,
        "source_run_ids": run_ids, "seam_ratios": seam_table,
        "dark_scaled_from_ns": dark_scale_table,
        "dark_set_drift_mean_dn": float(np.mean(dark_drift_dn)),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("xb") as stream:
        np.savez_compressed(
            stream, wavelength_nm=np.asarray(wavelengths, dtype=np.float64), iso=np.asarray(iso_values, dtype=np.int32),
            base_exposure_us=np.asarray(base_us, dtype=np.float64),
            psf_unit_sum=np.asarray(cell_kernels), psf_unit_sum_native=np.asarray(native_kernels),
            roi_xywh=np.asarray([roi.x, roi.y, roi.width, roi.height], dtype=np.int32),
            background_dn=np.asarray(backgrounds), negative_mass=np.asarray(negative_cell),
            negative_mass_native=np.asarray(negative_native), below_background_fraction=np.asarray(below_background),
            all_saturated_fraction=np.asarray(all_saturated), source_counts=np.asarray(source_counts, dtype=np.int64),
            metadata_json=np.asarray(json.dumps(metadata, sort_keys=True)))
    report = {"schema_version": 1, "output": output.name, "bands": len(bands), "roi_xywh": metadata["roi_xywh"],
              "shapes": {"psf_unit_sum": list(np.asarray(cell_kernels).shape),
                         "psf_unit_sum_native": list(np.asarray(native_kernels).shape)},
              "negative_mass_range": [float(np.min(negative_cell)), float(np.max(negative_cell))],
              "background_dn_range": [float(np.min(backgrounds)), float(np.max(backgrounds))],
              "metadata": metadata}
    if diagnostics is not None:
        diagnostics.parent.mkdir(parents=True, exist_ok=True)
        with diagnostics.open("x", encoding="utf-8") as stream:
            json.dump(report, stream, indent=2, allow_nan=False)
    print(json.dumps({"output": str(output), "bands": len(bands), "shape": report["shapes"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
