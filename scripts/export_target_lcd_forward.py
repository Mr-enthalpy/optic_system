#!/usr/bin/env python3
"""Export Phase 3.6 target capture raw HDF5 to LCD_forward-compatible HDF5."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import h5py
import numpy as np


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _ensure_sys_path() -> None:
    root = str(_repo_root())
    if root not in sys.path:
        sys.path.insert(0, root)


_ensure_sys_path()

from tasks.psf_phase3 import json_dumps  # noqa: E402


def _resolve_repo_path(path: str | Path) -> Path:
    p = Path(path)
    return p if p.is_absolute() else _repo_root() / p


def export_target_lcd_forward(raw_h5: str | Path, output_dir: str | Path) -> dict[str, Any]:
    raw_path = _resolve_repo_path(raw_h5)
    out_dir = _resolve_repo_path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    with h5py.File(str(raw_path), "r") as f:
        plan = _require_json_dataset(f, "capture/plan_json")
        pupil_window = _require_json_dataset(f, "provenance/pupil_window_source_json")
        psf_roi = _require_json_dataset(f, "provenance/psf_roi_source_json")
        camera_params_source = _require_json_dataset(f, "provenance/camera_params_source_json")
        mask_source_metadata = _require_json_dataset(f, "provenance/mask_source_metadata_json")
        target_metadata = _require_json_dataset(f, "target/target_metadata_json")
        crops = f["raw/crops"][()]
        masks_lowres = f["raw/masks_lowres"][()]
        mask_ids = [_decode(x) for x in f["raw/mask_id"][()]]
        mask_families = [_decode(x) for x in f["raw/mask_family"][()]]
        wavelength_nm = np.asarray(f["raw/wavelength_nm"][()], dtype=np.float64)
        wavelength_index = np.asarray(f["raw/wavelength_index"][()], dtype=np.int64)
        repeat_index = np.asarray(f["raw/repeat_index"][()], dtype=np.int64)
        capture_roles = [_decode(x) for x in f["raw/capture_role"][()]]

    _validate_required_provenance(plan, pupil_window, psf_roi, camera_params_source, mask_source_metadata)
    role_arr = np.asarray(capture_roles)
    encoded_idx = np.where(role_arr == "encoded_target")[0]
    if encoded_idx.size == 0:
        raise ValueError("raw HDF5 does not contain any encoded_target captures")
    encoded_mask_ids = [mask_ids[i] for i in encoded_idx]
    encoded_wavelength_idx = wavelength_index[encoded_idx]
    encoded_wavelength_nm = wavelength_nm[encoded_idx]
    unique_mask_ids = list(dict.fromkeys(encoded_mask_ids))
    unique_wavelength_index = list(dict.fromkeys(encoded_wavelength_idx.tolist()))
    unique_wavelength_nm = [float(encoded_wavelength_nm[np.where(encoded_wavelength_idx == idx)[0][0]]) for idx in unique_wavelength_index]
    T = len(unique_mask_ids)
    L = len(unique_wavelength_index)
    H, W = int(crops.shape[1]), int(crops.shape[2])
    frames = np.zeros((1, T, L, 1, H, W), dtype=np.float64)
    masks = np.zeros((1, T, 1, 64, 64), dtype=np.uint8)
    mask_family_out: list[str] = []
    for t, mask_id in enumerate(unique_mask_ids):
        first_idx = encoded_idx[encoded_mask_ids.index(mask_id)]
        masks[0, t, 0] = np.asarray(masks_lowres[first_idx, 0], dtype=np.uint8)
        mask_family_out.append(mask_families[first_idx])
        for l, wl_idx in enumerate(unique_wavelength_index):
            match = [
                i for i in encoded_idx
                if mask_ids[i] == mask_id and int(wavelength_index[i]) == int(wl_idx)
            ]
            if not match:
                raise ValueError(f"missing encoded_target captures for mask_id={mask_id} wavelength_index={wl_idx}")
            frames[0, t, l, 0] = np.mean(crops[match], axis=0)

    export_path = out_dir / "target_frames.h5"
    with h5py.File(str(export_path), "w") as f:
        string_dtype = h5py.string_dtype(encoding="utf-8")
        f.create_dataset("frames", data=frames, compression="gzip", compression_opts=4)
        f.create_dataset("masks", data=masks, compression="gzip", compression_opts=4)
        f.create_dataset("wavelengths_nm", data=np.asarray(unique_wavelength_nm, dtype=np.float64))
        f.create_dataset("mask_id", data=np.asarray(unique_mask_ids, dtype=object), dtype=string_dtype)
        f.create_dataset("mask_family", data=np.asarray(mask_family_out, dtype=object), dtype=string_dtype)
        metadata = {
            "schema_version": 1,
            "phase": "3.6",
            "source_raw_h5": _repo_relative(raw_path),
            "pupil_window_source": str(plan.get("pupil_window_source")),
            "psf_roi_source": str(plan.get("psf_roi_source")),
            "camera_params_source": str(plan.get("camera_params_source")),
            "mask_source": mask_source_metadata,
            "target_id": target_metadata.get("target_id"),
            "frames_shape": list(frames.shape),
            "masks_shape": list(masks.shape),
            "has_ground_truth_objects": False,
            "normalization": "raw_repeat_averaged_crop",
            "L": int(L),
            "T": int(T),
        }
        f.create_dataset("metadata_json", data=json_dumps(metadata), dtype=string_dtype)
    readme_lines = [
        "# Target Capture LCD_forward Export",
        "",
        "This directory contains Phase 3.6 target observations exported from optic_system.",
        "No reconstruction is performed here.",
        "Shapes:",
        "- frames: [1, T, L, 1, H, W]",
        "- masks:  [1, T, 1, 64, 64]",
    ]
    (out_dir / "README.md").write_text("\n".join(readme_lines) + "\n", encoding="utf-8")
    return {
        "schema_version": 1,
        "phase": "3.6",
        "target_id": target_metadata.get("target_id"),
        "source_raw_h5": _repo_relative(raw_path),
        "output_h5": _repo_relative(export_path),
        "frames_shape": list(frames.shape),
        "masks_shape": list(masks.shape),
        "wavelengths_nm": unique_wavelength_nm,
    }


def _validate_required_provenance(
    plan: dict[str, Any],
    pupil_window: dict[str, Any],
    psf_roi: dict[str, Any],
    camera_params_source: dict[str, Any],
    mask_source_metadata: dict[str, Any],
) -> None:
    if plan.get("phase") != "3.6":
        raise ValueError("capture/plan_json must describe a Phase 3.6 target capture plan")
    if pupil_window.get("phase") != "3.1":
        raise ValueError("provenance/pupil_window_source_json must contain a Phase 3.1 effective pupil window")
    roi = psf_roi.get("roi")
    if psf_roi.get("phase") != "3.2a" or not isinstance(roi, dict):
        raise ValueError("provenance/psf_roi_source_json must contain a Phase 3.2a PSF ROI")
    if not isinstance(camera_params_source, dict) or not camera_params_source:
        raise ValueError("provenance/camera_params_source_json must not be empty")
    if not isinstance(mask_source_metadata, dict) or not mask_source_metadata:
        raise ValueError("provenance/mask_source_metadata_json must not be empty")


def _require_json_dataset(f: h5py.File, path: str) -> dict[str, Any]:
    value = f[path][()]
    text = _decode(value)
    if not text:
        raise ValueError(f"required provenance dataset is empty: {path}")
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError(f"required provenance dataset must decode to a JSON object: {path}")
    return data


def _decode(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return str(value)


def _repo_relative(path: Path) -> str:
    try:
        return str(path.relative_to(_repo_root())).replace("\\", "/")
    except ValueError:
        return str(path)


def main() -> None:
    parser = argparse.ArgumentParser(description="Export Phase 3.6 target capture to LCD_forward HDF5")
    parser.add_argument("--raw-h5", default="data/raw/bishe_target_capture.h5")
    parser.add_argument("--output-dir", default="outputs/target_capture/export_lcd_forward")
    args = parser.parse_args()
    result = export_target_lcd_forward(args.raw_h5, args.output_dir)
    print(json_dumps(result))


if __name__ == "__main__":
    main()
