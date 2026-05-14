from __future__ import annotations

import json
from pathlib import Path

import h5py
import numpy as np

from scripts.analyze_pupil_scan import analyze_pupil_scan
from tasks.pupil_scan_h5 import PupilScanWriter
from tasks.pupil_scan_masks import ScanMaskSpec, iter_pupil_scan_masks


PHYSICAL_SHAPE = (100, 300)
KNOWN_ROI = {"x_min": 90, "x_max": 210, "y_min": 30, "y_max": 70}


def _write_synthetic_scan(
    path: Path,
    *,
    modes: list[str],
    roi: dict[str, int] = KNOWN_ROI,
    low_snr: bool = False,
    outlier: bool = False,
    block_roi: dict[str, int] | None = None,
) -> None:
    spec = ScanMaskSpec(
        physical_shape=PHYSICAL_SHAPE,
        subpixel_axis=1,
        scan_modes=modes,
        bar_count=20,
        block_rows=10,
        block_cols=10,
        include_baselines=True,
    )
    with PupilScanWriter(path, plan_id="synthetic_pupil") as writer:
        writer.write_plan_json(
            {"plan_id": "synthetic_pupil", "camera_params_source": "camera_params.json"}
        )
        writer.write_lcd_metadata({"physical_shape": list(PHYSICAL_SHAPE), "subpixel_axis": 1})
        writer.write_camera_metadata(
            exposure_us=50000.0,
            gain_db=0.0,
            frame_dtype_full_scale=255,
            camera_params_source={"source": "camera_params.json", "overridden": False},
        )
        writer.write_tls_metadata(wavelength_nm=550.0, grating=1, status={})

        outlier_written = False
        for mask_id, _mask, meta in iter_pupil_scan_masks(spec):
            target = block_roi if block_roi is not None and meta["mode"] == "blocks" else roi
            response = 0.0 if low_snr else _response_for_meta(meta, target)
            frame = np.ones((32, 32), dtype=np.float64) * response * 100.0
            if (
                outlier
                and not outlier_written
                and meta["mode"] == "blocks"
                and response == 0.0
            ):
                frame[0, 0] = 1_000_000.0
                outlier_written = True
            writer.append_capture(
                mask_id=mask_id,
                mask_metadata=meta,
                frames_avg=frame,
            )


def _response_for_meta(meta: dict, roi: dict[str, int]) -> float:
    if meta.get("baseline") == "baseline_all_open":
        return 1.0
    if meta.get("baseline") == "baseline_all_closed":
        return 0.0
    x0, x1 = int(meta.get("x_min", 0)), int(meta.get("x_max", 0))
    y0, y1 = int(meta.get("y_min", 0)), int(meta.get("y_max", 0))
    ix0, ix1 = max(x0, roi["x_min"]), min(x1, roi["x_max"])
    iy0, iy1 = max(y0, roi["y_min"]), min(y1, roi["y_max"])
    inter = max(0, ix1 - ix0) * max(0, iy1 - iy0)
    area = max(1, (x1 - x0) * (y1 - y0))
    roi_area = max(1, (roi["x_max"] - roi["x_min"]) * (roi["y_max"] - roi["y_min"]))
    if meta["mode"] in ("bars_x", "bars_y"):
        return inter / area
    return inter / min(area, roi_area)


def _assert_roi_close(result: dict, roi: dict[str, int], tol: int = 35) -> None:
    got = result["roi_physical"]
    for key in ("x_min", "x_max", "y_min", "y_max"):
        assert abs(got[key] - roi[key]) <= tol, (key, got, roi)


def test_known_rectangular_roi_recovered_within_tolerance(tmp_path: Path) -> None:
    h5_path = tmp_path / "scan.h5"
    out_dir = tmp_path / "out"
    _write_synthetic_scan(h5_path, modes=["bars_x", "bars_y", "blocks"])

    result = analyze_pupil_scan(
        h5_path,
        out_dir,
        threshold_fraction=0.4,
        margin_fraction=0.0,
        smooth_window=3,
        min_component_size=2,
    )

    _assert_roi_close(result, KNOWN_ROI)
    assert (out_dir / "effective_lcd_roi.json").exists()
    assert (out_dir / "response_map.npy").exists()
    assert (out_dir / "response_map.png").exists()
    assert (out_dir / "x_profile.csv").exists()
    assert (out_dir / "y_profile.csv").exists()
    assert (out_dir / "pupil_scan_report.md").exists()


def test_single_outlier_bright_point_does_not_shift_roi(tmp_path: Path) -> None:
    h5_path = tmp_path / "outlier.h5"
    out_dir = tmp_path / "out"
    _write_synthetic_scan(
        h5_path,
        modes=["bars_x", "bars_y", "blocks"],
        outlier=True,
    )

    result = analyze_pupil_scan(
        h5_path,
        out_dir,
        threshold_fraction=0.4,
        margin_fraction=0.0,
        smooth_window=3,
        min_component_size=2,
    )

    _assert_roi_close(result, KNOWN_ROI)


def test_low_snr_returns_warning_or_low_confidence(tmp_path: Path) -> None:
    h5_path = tmp_path / "low.h5"
    out_dir = tmp_path / "out"
    _write_synthetic_scan(h5_path, modes=["bars_x", "bars_y", "blocks"], low_snr=True)

    result = analyze_pupil_scan(h5_path, out_dir, smooth_window=3, min_component_size=2)

    assert result["confidence"]["level"] in {"low", "failed"}
    assert result["confidence"]["warnings"]


def test_bars_only_roi(tmp_path: Path) -> None:
    h5_path = tmp_path / "bars.h5"
    out_dir = tmp_path / "out"
    _write_synthetic_scan(h5_path, modes=["bars_x", "bars_y"])

    result = analyze_pupil_scan(
        h5_path,
        out_dir,
        threshold_fraction=0.4,
        margin_fraction=0.0,
        smooth_window=3,
        min_component_size=2,
    )

    _assert_roi_close(result, KNOWN_ROI)


def test_blocks_only_roi(tmp_path: Path) -> None:
    h5_path = tmp_path / "blocks.h5"
    out_dir = tmp_path / "out"
    _write_synthetic_scan(h5_path, modes=["blocks"])

    result = analyze_pupil_scan(
        h5_path,
        out_dir,
        threshold_fraction=0.4,
        margin_fraction=0.0,
        smooth_window=3,
        min_component_size=2,
    )

    _assert_roi_close(result, KNOWN_ROI, tol=40)


def test_bars_and_blocks_disagreement_warning(tmp_path: Path) -> None:
    h5_path = tmp_path / "disagree.h5"
    out_dir = tmp_path / "out"
    _write_synthetic_scan(
        h5_path,
        modes=["bars_x", "bars_y", "blocks"],
        block_roi={"x_min": 0, "x_max": 80, "y_min": 0, "y_max": 40},
    )

    result = analyze_pupil_scan(
        h5_path,
        out_dir,
        threshold_fraction=0.4,
        margin_fraction=0.0,
        smooth_window=3,
        min_component_size=2,
    )

    assert any("disagree" in w for w in result["confidence"]["warnings"])
    assert result["confidence"]["level"] == "low"


def test_effective_lcd_roi_json_provenance_fields(tmp_path: Path) -> None:
    h5_path = tmp_path / "prov.h5"
    out_dir = tmp_path / "out"
    _write_synthetic_scan(h5_path, modes=["blocks"])

    result = analyze_pupil_scan(h5_path, out_dir, smooth_window=3, min_component_size=2)

    with open(out_dir / "effective_lcd_roi.json", "r", encoding="utf-8") as f:
        saved = json.load(f)
    assert saved["source_raw_capture_h5"] == str(h5_path)
    assert saved["capture_plan_id"] == "synthetic_pupil"
    assert saved["camera_params_source"] == "camera_params.json"
    assert saved["wavelength_nm"] == 550.0
    assert saved["validity"]["scientific_calibration_valid"] is False
    assert saved["validity"]["training_ready"] is False
    assert result["roi_physical"] == saved["roi_physical"]


def test_analysis_traces_to_raw_h5(tmp_path: Path) -> None:
    h5_path = tmp_path / "trace.h5"
    out_dir = tmp_path / "out"
    _write_synthetic_scan(h5_path, modes=["blocks"])

    analyze_pupil_scan(h5_path, out_dir, smooth_window=3, min_component_size=2)

    with h5py.File(h5_path, "r") as f:
        assert f["raw/frames_avg"].shape[0] > 0
        assert f["scan/mask_recipe_json"].shape[0] == f["raw/frames_avg"].shape[0]
