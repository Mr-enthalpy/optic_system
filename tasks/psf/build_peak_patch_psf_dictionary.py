from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import h5py
import numpy as np

from tasks.artifacts.coordinate_frame import (
    camera_frame_extent_from_hdf5,
    require_full_sensor_extent,
)
from tasks.artifacts.json_io import (
    h5_string_dtype,
    index_string,
    loads_json_object,
    read_mask_arrays,
    read_optional_dataset_string,
    read_scalar_string,
    read_string_array,
    unique_preserve_order,
)
from .profile_requirements import (
    PSFArtifactError,
    illumination_mode,
    require_paths,
    validate_policy_none,
    validate_profile_manifests,
)
from .derive_peak_layout_profile import PeakLayoutProfileManifest


class PeakPatchPSFDictionaryError(PSFArtifactError):
    pass


@dataclass
class PeakPatchPSFDictionaryManifest:
    dictionary_id: str
    source_raw_capture_h5: str
    peak_layout_profile: str
    pupil_profile_id: str | None
    camera_profile_id: str | None
    illumination_mode: str
    entry_wavelengths_nm: list[float]
    entry_mask_ids: list[str]
    unique_wavelengths_nm: list[float]
    unique_mask_ids: list[str]
    frame_shape: tuple[int, int]
    camera_frame_extent: dict[str, Any]
    peak_layout_coordinate_frame: str
    peak_layout_camera_frame_extent: dict[str, Any]
    peak_ids: list[str]
    patch_shape_hw: list[list[int]]
    patch_origin_xy: list[list[int]]
    applied_background_policy: str
    applied_normalization_policy: str
    notes: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PeakPatchPSFDictionaryManifest:
        frame_shape = data.get("frame_shape")
        if not isinstance(frame_shape, (list, tuple)) or len(frame_shape) != 2:
            raise PeakPatchPSFDictionaryError("frame_shape must contain [H, W]")
        return cls(
            dictionary_id=_require_str(data, "dictionary_id"),
            source_raw_capture_h5=_require_str(data, "source_raw_capture_h5"),
            peak_layout_profile=_require_str(data, "peak_layout_profile"),
            pupil_profile_id=_optional_str(data.get("pupil_profile_id")),
            camera_profile_id=_optional_str(data.get("camera_profile_id")),
            illumination_mode=_require_str(data, "illumination_mode"),
            entry_wavelengths_nm=[
                float(v) for v in _require_list(data, "entry_wavelengths_nm")
            ],
            entry_mask_ids=[str(v) for v in _require_list(data, "entry_mask_ids")],
            unique_wavelengths_nm=[
                float(v) for v in _require_list(data, "unique_wavelengths_nm")
            ],
            unique_mask_ids=[str(v) for v in _require_list(data, "unique_mask_ids")],
            frame_shape=(int(frame_shape[0]), int(frame_shape[1])),
            camera_frame_extent=_require_dict(data, "camera_frame_extent"),
            peak_layout_coordinate_frame=_require_str(data, "peak_layout_coordinate_frame"),
            peak_layout_camera_frame_extent=_require_dict(data, "peak_layout_camera_frame_extent"),
            peak_ids=[str(v) for v in _require_list(data, "peak_ids")],
            patch_shape_hw=_int_pairs(data, "patch_shape_hw"),
            patch_origin_xy=_int_pairs(data, "patch_origin_xy"),
            applied_background_policy=_require_str(data, "applied_background_policy"),
            applied_normalization_policy=_require_str(data, "applied_normalization_policy"),
            notes=_optional_str(data.get("notes")),
        )

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["artifact_type"] = "peak_patch_psf_dictionary"
        data["frame_shape"] = list(self.frame_shape)
        return data

    def to_json(self, path: str | Path | None = None) -> str:
        text = json.dumps(self.to_dict(), indent=2, sort_keys=True)
        if path is not None:
            Path(path).write_text(text + "\n", encoding="utf-8")
        return text

    @classmethod
    def load_json(cls, path: str | Path) -> PeakPatchPSFDictionaryManifest:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise PeakPatchPSFDictionaryError("manifest JSON root must be a mapping")
        return cls.from_dict(data)


def build_peak_patch_psf_dictionary(
    *,
    source_raw_capture_h5: str | Path,
    peak_layout_profile: str | Path,
    output_h5: str | Path,
    dictionary_id: str | None = None,
    manifest_path: str | Path | None = None,
    pupil_profile_manifest: str | Path | None = None,
    camera_profile_manifest: str | Path | None = None,
    background_policy: str = "none",
    normalization_policy: str = "none",
    output_dtype: str = "float32",
    allow_acquired_frame_only: bool = False,
    allow_camera_frame_extent_mismatch: bool = False,
    notes: str | None = None,
) -> PeakPatchPSFDictionaryManifest:
    try:
        validate_policy_none(background_policy, "background_policy")
        validate_policy_none(normalization_policy, "normalization_policy")
    except PSFArtifactError as exc:
        raise PeakPatchPSFDictionaryError(str(exc)) from exc
    except ValueError as exc:
        raise PeakPatchPSFDictionaryError(str(exc)) from exc
    dtype = _output_dtype(output_dtype)

    source_path = Path(source_raw_capture_h5)
    layout_path = Path(peak_layout_profile)
    layout = PeakLayoutProfileManifest.load_json(layout_path)
    output_path = Path(output_h5)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if dictionary_id is None:
        dictionary_id = output_path.stem
    if manifest_path is None:
        manifest_path = output_path.with_suffix(".manifest.json")

    with h5py.File(source_path, "r") as src:
        try:
            require_paths(
                src,
                [
                    "raw/frames_avg",
                    "capture/completed",
                    "capture/wavelength_index",
                    "capture/mask_index",
                    "capture/capture_index",
                    "capture/plan_json",
                    "illumination/illumination_json",
                    "masks/mask_id",
                ],
            )
            completed = np.asarray(src["capture/completed"], dtype=bool)
            wavelength_indices = np.asarray(src["capture/wavelength_index"], dtype=np.int64)
            mask_indices = np.asarray(src["capture/mask_index"], dtype=np.int64)
            capture_indices = np.asarray(src["capture/capture_index"], dtype=np.int64)
            valid_rows = [
                int(i)
                for i, done in enumerate(completed)
                if bool(done) and wavelength_indices[i] >= 0 and mask_indices[i] >= 0
            ]
            if not valid_rows:
                raise PeakPatchPSFDictionaryError("raw capture contains no completed captures")
            first_frame = np.asarray(src["raw/frames_avg"][valid_rows[0]])
            if first_frame.ndim != 2:
                raise PeakPatchPSFDictionaryError(
                    f"raw frames_avg entries must be 2D, got shape {first_frame.shape}"
                )
            frame_shape = (int(first_frame.shape[0]), int(first_frame.shape[1]))
            if frame_shape != layout.frame_shape:
                raise PeakPatchPSFDictionaryError(
                    f"raw frame_shape {frame_shape} does not match PeakLayoutProfile {layout.frame_shape}"
                )
            _validate_fixed_patch_layout(layout, frame_shape=frame_shape)
            extent = camera_frame_extent_from_hdf5(src, frame_shape=frame_shape)
            require_full_sensor_extent(
                extent,
                allow_acquired_frame_only=allow_acquired_frame_only,
                artifact_name="peak-patch PSF dictionary",
            )
            _validate_layout_extent(
                raw_extent=extent,
                layout_extent=layout.camera_frame_extent,
                allow_mismatch=allow_camera_frame_extent_mismatch,
            )

            groups: dict[tuple[int, int], list[int]] = {}
            for row in valid_rows:
                key = (int(mask_indices[row]), int(wavelength_indices[row]))
                groups.setdefault(key, []).append(row)
            sorted_keys = sorted(groups)
            illumination_by_index = _read_raw_illumination_json_by_index(src, plan=None)
            mask_ids_by_index = read_string_array(src["masks/mask_id"])
            entry_mask_ids = [
                index_string(mask_ids_by_index, mask_idx) for mask_idx, _ in sorted_keys
            ]
            source_plan_json = read_scalar_string(src["capture/plan_json"])
            plan = loads_json_object(source_plan_json)
            if not illumination_by_index:
                illumination_by_index = _read_raw_illumination_json_by_index(src, plan=plan)
            entry_wavelengths = [
                _wavelength_from_illumination_json(
                    index_string(illumination_by_index, wl_idx)
                )
                for _, wl_idx in sorted_keys
            ]
            pupil_profile_id = read_optional_dataset_string(src, "profiles/pupil_profile_id")
            camera_profile_id = read_optional_dataset_string(src, "profiles/camera_profile_id")
            illum_mode = illumination_mode(plan)
            validate_profile_manifests(
                pupil_profile_id=pupil_profile_id,
                camera_profile_id=camera_profile_id,
                illumination_mode_value=illum_mode,
                wavelengths_nm=unique_preserve_order(entry_wavelengths),
                pupil_profile_manifest=pupil_profile_manifest,
                camera_profile_manifest=camera_profile_manifest,
            )

            manifest = PeakPatchPSFDictionaryManifest(
                dictionary_id=str(dictionary_id),
                source_raw_capture_h5=str(source_path),
                peak_layout_profile=str(layout_path),
                pupil_profile_id=pupil_profile_id,
                camera_profile_id=camera_profile_id,
                illumination_mode=illum_mode,
                entry_wavelengths_nm=entry_wavelengths,
                entry_mask_ids=entry_mask_ids,
                unique_wavelengths_nm=unique_preserve_order(entry_wavelengths),
                unique_mask_ids=unique_preserve_order(entry_mask_ids),
                frame_shape=frame_shape,
                camera_frame_extent=extent,
                peak_layout_coordinate_frame=layout.coordinate_frame,
                peak_layout_camera_frame_extent=layout.camera_frame_extent,
                peak_ids=layout.peak_ids,
                patch_shape_hw=layout.patch_shape_hw,
                patch_origin_xy=layout.patch_origin_xy,
                applied_background_policy=background_policy,
                applied_normalization_policy=normalization_policy,
                notes=notes,
            )
        except PSFArtifactError as exc:
            raise PeakPatchPSFDictionaryError(str(exc)) from exc
        except ValueError as exc:
            raise PeakPatchPSFDictionaryError(str(exc)) from exc

        _write_dictionary_h5(
            output_path=output_path,
            src=src,
            layout=layout,
            manifest=manifest,
            sorted_keys=sorted_keys,
            groups=groups,
            capture_indices=capture_indices,
            entry_mask_indices=np.asarray([key[0] for key in sorted_keys], dtype=np.int64),
            dtype=dtype,
            source_plan_json=source_plan_json,
        )

    manifest.to_json(manifest_path)
    return manifest


def _write_dictionary_h5(
    *,
    output_path: Path,
    src: h5py.File,
    layout: PeakLayoutProfileManifest,
    manifest: PeakPatchPSFDictionaryManifest,
    sorted_keys: list[tuple[int, int]],
    groups: dict[tuple[int, int], list[int]],
    capture_indices: np.ndarray,
    entry_mask_indices: np.ndarray,
    dtype: np.dtype,
    source_plan_json: str,
) -> None:
    string_dtype = h5_string_dtype()
    vlen_i64 = h5py.vlen_dtype(np.dtype("int64"))
    n_entry = len(sorted_keys)
    n_peak = len(layout.peak_ids)
    patch_shape = tuple(layout.patch_shape_hw[0])
    with h5py.File(output_path, "w") as dst:
        dst.attrs["artifact_type"] = "peak_patch_psf_dictionary"
        dst.attrs["dictionary_id"] = manifest.dictionary_id
        grp = dst.require_group("peak_patch_dictionary")
        patches = grp.create_dataset(
            "patches",
            shape=(n_entry, n_peak, patch_shape[0], patch_shape[1]),
            dtype=dtype,
            compression="gzip",
            compression_opts=4,
            chunks=(1, max(1, n_peak), patch_shape[0], patch_shape[1]),
        )
        capture_dset = grp.create_dataset("entry_capture_indices", shape=(n_entry,), dtype=vlen_i64)
        for entry_idx, key in enumerate(sorted_keys):
            rows = groups[key]
            avg = _average_rows(src["raw/frames_avg"], rows)
            patches[entry_idx] = _extract_peak_patches(avg, layout).astype(dtype, copy=False)
            capture_dset[entry_idx] = capture_indices[rows].astype(np.int64)

        grp.create_dataset("entry_mask_ids", data=np.asarray(manifest.entry_mask_ids, dtype=object), dtype=string_dtype)
        grp.create_dataset("entry_mask_index", data=entry_mask_indices)
        grp.create_dataset("entry_wavelength_nm", data=np.asarray(manifest.entry_wavelengths_nm, dtype=np.float64))
        grp.create_dataset("unique_mask_ids", data=np.asarray(manifest.unique_mask_ids, dtype=object), dtype=string_dtype)
        grp.create_dataset("unique_wavelength_nm", data=np.asarray(manifest.unique_wavelengths_nm, dtype=np.float64))
        grp.create_dataset("peak_id", data=np.asarray(layout.peak_ids, dtype=object), dtype=string_dtype)
        grp.create_dataset("peak_center_xy", data=np.asarray(layout.center_xy, dtype=np.float64))
        grp.create_dataset("patch_origin_xy", data=np.asarray(layout.patch_origin_xy, dtype=np.int64))
        grp.create_dataset("patch_shape_hw", data=np.asarray(layout.patch_shape_hw, dtype=np.int64))
        grp.create_dataset("frame_shape", data=np.asarray(manifest.frame_shape, dtype=np.int64))
        grp.create_dataset("coordinate_frame", data=manifest.peak_layout_coordinate_frame)
        grp.create_dataset(
            "camera_frame_extent_json",
            data=json.dumps(manifest.camera_frame_extent, sort_keys=True),
        )
        grp.create_dataset(
            "peak_layout_camera_frame_extent_json",
            data=json.dumps(manifest.peak_layout_camera_frame_extent, sort_keys=True),
        )
        grp.create_dataset("normalization_policy_json", data=json.dumps({"applied": manifest.applied_normalization_policy}))
        grp.create_dataset("background_policy_json", data=json.dumps({"applied": manifest.applied_background_policy}))
        grp.create_dataset("manifest_json", data=manifest.to_json())

        masks = read_mask_arrays(src)
        if masks is not None:
            mask_table = dst.require_group("mask_table")
            mask_table.create_dataset(
                "masks_physical",
                data=masks,
                compression="gzip",
                compression_opts=4,
                chunks=(1, masks.shape[1], masks.shape[2]),
            )
            mask_table.create_dataset(
                "mask_ids",
                data=np.asarray(read_string_array(src["masks/mask_id"]), dtype=object),
                dtype=string_dtype,
            )

        profiles = dst.require_group("profiles")
        profiles.create_dataset("pupil_profile_id", data=manifest.pupil_profile_id or "")
        profiles.create_dataset("camera_profile_id", data=manifest.camera_profile_id or "")
        source = dst.require_group("source")
        source.create_dataset("raw_capture_h5", data=manifest.source_raw_capture_h5)
        source.create_dataset("peak_layout_profile", data=manifest.peak_layout_profile)
        source.create_dataset("plan_json", data=source_plan_json)


def _average_rows(frames_dset: h5py.Dataset, rows: list[int]) -> np.ndarray:
    avg = np.zeros(frames_dset.shape[1:], dtype=np.float64)
    for row in rows:
        frame = np.asarray(frames_dset[row], dtype=np.float64)
        if frame.shape != avg.shape:
            raise PeakPatchPSFDictionaryError("all dictionary frames must share shape")
        avg += frame
    avg /= float(len(rows))
    return avg


def _extract_peak_patches(
    frame: np.ndarray,
    layout: PeakLayoutProfileManifest,
) -> np.ndarray:
    patch_shape = tuple(layout.patch_shape_hw[0])
    result = np.empty((layout.n_peaks, patch_shape[0], patch_shape[1]), dtype=np.float64)
    for peak_idx, origin_xy in enumerate(layout.patch_origin_xy):
        x0, y0 = int(origin_xy[0]), int(origin_xy[1])
        ph, pw = layout.patch_shape_hw[peak_idx]
        result[peak_idx] = frame[y0 : y0 + ph, x0 : x0 + pw]
    return result


def _validate_fixed_patch_layout(
    layout: PeakLayoutProfileManifest,
    *,
    frame_shape: tuple[int, int],
) -> None:
    if not layout.peak_ids:
        raise PeakPatchPSFDictionaryError("PeakLayoutProfile contains no peaks")
    first_shape = tuple(layout.patch_shape_hw[0])
    h, w = frame_shape
    for i, shape in enumerate(layout.patch_shape_hw):
        if tuple(shape) != first_shape:
            raise PeakPatchPSFDictionaryError(
                "first peak-patch dictionary version requires uniform patch_shape_hw"
            )
        ph, pw = int(shape[0]), int(shape[1])
        x0, y0 = [int(v) for v in layout.patch_origin_xy[i]]
        if ph <= 0 or pw <= 0:
            raise PeakPatchPSFDictionaryError("patch_shape_hw must be positive")
        if x0 < 0 or y0 < 0 or x0 + pw > w or y0 + ph > h:
            raise PeakPatchPSFDictionaryError("peak patch extends outside frame_shape")


def _validate_layout_extent(
    *,
    raw_extent: dict[str, Any],
    layout_extent: dict[str, Any],
    allow_mismatch: bool,
) -> None:
    if _canonical_extent(raw_extent) == _canonical_extent(layout_extent):
        return
    if allow_mismatch:
        return
    raise PeakPatchPSFDictionaryError(
        "raw capture camera_frame_extent does not match PeakLayoutProfile "
        "camera_frame_extent; pass allow_camera_frame_extent_mismatch only when "
        "the coordinate transform has been audited"
    )


def _canonical_extent(extent: dict[str, Any]) -> dict[str, Any]:
    return {
        "mode": extent.get("mode"),
        "origin_xy": _int_list_or_none(extent.get("origin_xy")),
        "shape_hw": _int_list_or_none(extent.get("shape_hw")),
        "sensor_shape_hw": _int_list_or_none(extent.get("sensor_shape_hw")),
    }


def _int_list_or_none(value: Any) -> list[int] | None:
    if value is None:
        return None
    if not isinstance(value, (list, tuple)):
        return None
    return [int(v) for v in value]


def _output_dtype(output_dtype: str) -> np.dtype:
    normalized = output_dtype.lower()
    if normalized not in {"float32", "float64"}:
        raise PeakPatchPSFDictionaryError("output_dtype must be float32 or float64")
    return np.dtype(normalized)


def _require_str(data: dict[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise PeakPatchPSFDictionaryError(f"{key} must be a non-empty string")
    return value.strip()


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise PeakPatchPSFDictionaryError(f"expected string or null, got {type(value).__name__}")
    stripped = value.strip()
    return stripped or None


def _require_list(data: dict[str, Any], key: str) -> list[Any]:
    value = data.get(key)
    if not isinstance(value, list):
        raise PeakPatchPSFDictionaryError(f"{key} must be a list")
    return value


def _require_dict(data: dict[str, Any], key: str) -> dict[str, Any]:
    value = data.get(key)
    if not isinstance(value, dict):
        raise PeakPatchPSFDictionaryError(f"{key} must be a mapping")
    return value


def _int_pairs(data: dict[str, Any], key: str) -> list[list[int]]:
    return [[int(pair[0]), int(pair[1])] for pair in _require_list(data, key)]


def _wavelength_from_illumination_json(illumination_str: str) -> float:
    try:
        data = json.loads(illumination_str)
    except (json.JSONDecodeError, TypeError):
        return float("nan")
    mode = data.get("mode") or ""
    if mode == "broadband_passthrough":
        return float("nan")
    effective = data.get("effective_wavelength_nm")
    if effective is not None:
        return float(effective)
    label = data.get("wavelength_label_nm")
    if label is not None:
        return float(label)
    return float("nan")


def _read_raw_illumination_json_by_index(
    src: h5py.File,
    *,
    plan: dict[str, Any] | None,
) -> list[str]:
    if "illumination/illumination_json" in src:
        return [
            value.decode("utf-8") if isinstance(value, bytes) else str(value)
            for value in src["illumination/illumination_json"][()]
        ]
    if isinstance(plan, dict) and isinstance(plan.get("wavelengths"), list):
        result: list[str] = []
        for item in plan["wavelengths"]:
            if isinstance(item, dict) and isinstance(item.get("illumination"), dict):
                result.append(json.dumps(item["illumination"], sort_keys=True))
            else:
                result.append(json.dumps({"mode": "unknown"}, sort_keys=True))
        return result
    return []
