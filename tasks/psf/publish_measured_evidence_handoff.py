from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import h5py
import numpy as np

from tasks.artifacts.json_io import read_scalar_string
from .build_peak_patch_psf_dictionary import (
    PeakPatchPSFDictionaryError,
    PeakPatchPSFDictionaryManifest,
)
from .compact_dense_export import render_peak_patch_dense_view


class MeasuredEvidenceHandoffError(ValueError):
    pass


def publish_measured_evidence_handoff(
    *,
    dictionary_h5: str | Path,
    output_h5: str | Path,
    include_dense_diagnostic: bool = False,
) -> None:
    dictionary_path = Path(dictionary_h5)
    output_path = Path(output_h5)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with h5py.File(dictionary_path, "r") as src:
        _require_paths(
            src,
            [
                "peak_patch_dictionary/patches",
                "peak_patch_dictionary/entry_wavelength_nm",
                "peak_patch_dictionary/entry_wavelength_index",
                "peak_patch_dictionary/entry_mask_index",
                "peak_patch_dictionary/entry_capture_indices",
                "peak_patch_dictionary/peak_id",
                "peak_patch_dictionary/peak_center_xy",
                "peak_patch_dictionary/patch_origin_xy",
                "peak_patch_dictionary/patch_shape_hw",
                "peak_patch_dictionary/frame_shape",
                "peak_patch_dictionary/coordinate_frame",
                "peak_patch_dictionary/manifest_json",
            ],
        )
        manifest = _read_manifest(src)
        patches = src["peak_patch_dictionary/patches"]
        frame_shape = tuple(int(v) for v in src["peak_patch_dictionary/frame_shape"][()])
        patch_origin_xy = np.asarray(src["peak_patch_dictionary/patch_origin_xy"], dtype=np.int64)

        with h5py.File(output_path, "w") as dst:
            dst.attrs["export_type"] = "measured_evidence_peak_patch_psf_dictionary"
            dst.attrs["source_dictionary_h5"] = str(dictionary_path)
            out_patches = dst.create_dataset(
                "psf_peak_patches",
                shape=patches.shape,
                dtype=patches.dtype,
                compression="gzip",
                compression_opts=4,
                chunks=patches.chunks or (1, patches.shape[1], patches.shape[2], patches.shape[3]),
            )
            for i in range(patches.shape[0]):
                out_patches[i] = patches[i]

            entries = dst.require_group("entries")
            entries.create_dataset("wavelength_nm", data=src["peak_patch_dictionary/entry_wavelength_nm"][()])
            entries.create_dataset("wavelength_index", data=src["peak_patch_dictionary/entry_wavelength_index"][()])
            entries.create_dataset("mask_index", data=src["peak_patch_dictionary/entry_mask_index"][()])
            _copy_dataset(src, dst, "peak_patch_dictionary/entry_mask_ids", "entries/mask_ids")
            _copy_dataset(src, dst, "peak_patch_dictionary/entry_capture_indices", "entries/capture_indices")

            peaks = dst.require_group("peak_table")
            _copy_dataset(src, dst, "peak_patch_dictionary/peak_id", "peak_table/peak_id")
            peaks.create_dataset("center_xy", data=src["peak_patch_dictionary/peak_center_xy"][()])
            peaks.create_dataset("patch_origin_xy", data=patch_origin_xy)
            peaks.create_dataset("patch_shape_hw", data=src["peak_patch_dictionary/patch_shape_hw"][()])
            peaks.create_dataset("frame_shape", data=np.asarray(frame_shape, dtype=np.int64))
            peaks.create_dataset(
                "coordinate_frame",
                data=read_scalar_string(src["peak_patch_dictionary/coordinate_frame"]),
            )

            if "mask_table/masks_physical" in src:
                _copy_dataset(src, dst, "mask_table/masks_physical", "mask_table/masks_physical")
                _copy_dataset(src, dst, "mask_table/mask_ids", "mask_table/mask_ids")

            profiles = dst.require_group("profiles")
            _copy_scalar_if_present(src, profiles, "profiles/pupil_profile_id", "pupil_profile_id")
            _copy_scalar_if_present(src, profiles, "profiles/camera_profile_id", "camera_profile_id")

            source = dst.require_group("source")
            source.create_dataset("dictionary_h5", data=str(dictionary_path))
            _copy_scalar_if_present(
                src, source, "source/raw_capture_artifact_id", "raw_capture_artifact_id"
            )
            _copy_scalar_if_present(
                src, source, "source/peak_layout_artifact_id", "peak_layout_artifact_id"
            )

            metadata: dict[str, Any] = {
                "source_dictionary_h5": str(dictionary_path),
                "manifest": manifest.to_dict(),
                "exports": {
                    "dense_diagnostic_view": {
                        "enabled": bool(include_dense_diagnostic),
                        "role": "diagnostic_sparse_canvas_render",
                    }
                },
            }
            if include_dense_diagnostic:
                dense = render_peak_patch_dense_view(
                    np.asarray(patches),
                    patch_origin_xy=patch_origin_xy,
                    frame_shape=frame_shape,
                )
                dense_grp = dst.require_group("exports").require_group("dense_diagnostic")
                dense_grp.create_dataset(
                    "psf_dense",
                    data=dense,
                    compression="gzip",
                    compression_opts=4,
                    chunks=(1, dense.shape[1], dense.shape[2]),
                )
                metadata["exports"]["dense_diagnostic_view"]["frame_shape"] = list(frame_shape)
            dst.create_dataset("metadata_json", data=json.dumps(metadata, indent=2, sort_keys=True))


def _read_manifest(src: h5py.File) -> PeakPatchPSFDictionaryManifest:
    text = read_scalar_string(src["peak_patch_dictionary/manifest_json"])
    try:
        data = json.loads(text)
        if not isinstance(data, dict):
            raise MeasuredEvidenceHandoffError("manifest_json must decode to a mapping")
        return PeakPatchPSFDictionaryManifest.from_dict(data)
    except (json.JSONDecodeError, PeakPatchPSFDictionaryError) as exc:
        raise MeasuredEvidenceHandoffError(str(exc)) from exc


def _require_paths(src: h5py.File, paths: list[str]) -> None:
    for path in paths:
        if path not in src:
            raise MeasuredEvidenceHandoffError(f"dictionary missing {path}")


def _copy_dataset(src: h5py.File, dst: h5py.File, src_path: str, dst_path: str) -> None:
    if src_path not in src:
        return
    parent_path, name = dst_path.rsplit("/", 1)
    parent = dst.require_group(parent_path)
    src.copy(src[src_path], parent, name=name)


def _copy_scalar_if_present(
    src: h5py.File,
    dst_group: h5py.Group,
    src_path: str,
    dst_name: str,
) -> None:
    if src_path in src:
        dst_group.create_dataset(dst_name, data=read_scalar_string(src[src_path]))
