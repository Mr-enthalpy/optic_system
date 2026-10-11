from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import h5py
import numpy as np

from tasks.measurement.coordinate_frame import (
    camera_frame_extent_from_dict,
    camera_frame_extent_from_hdf5,
    require_full_sensor_extent,
)
from tasks.measurement.h5_arrays import read_mask_arrays
from tasks.measurement.json_io import (
    h5_string_dtype,
    index_string,
    read_optional_dataset_string,
    read_scalar_string,
    read_string_array,
    unique_preserve_order,
)
from .profile_requirements import (
    PSFArtifactError,
    require_paths,
    validate_policy_none,
    validate_profile_manifests,
)
from tasks.raw_capture_h5 import RAW_CAPTURE_SCHEMA_VERSION, _capture_schedule_is_complete


class FullFramePSFSurveyError(PSFArtifactError):
    pass


@dataclass
class FullFramePSFSurveyManifest:
    survey_id: str
    source_raw_capture_h5: str
    pupil_profile_id: str | None
    camera_profile_id: str | None
    illumination_mode: str
    entry_wavelengths_nm: list[float]
    entry_illumination_json: list[str]
    entry_mask_ids: list[str]
    unique_wavelengths_nm: list[float]
    unique_mask_ids: list[str]
    frame_shape: tuple[int, int]
    camera_frame_extent: dict[str, Any]
    survey_policy: dict[str, Any]
    full_frame_role: str = "scout"
    notes: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FullFramePSFSurveyManifest:
        if data.get("artifact_type") != "full_frame_psf_survey" or data.get("schema_version") != 1:
            raise FullFramePSFSurveyError("unsupported survey manifest format")
        frame_shape = data.get("frame_shape")
        if not isinstance(frame_shape, (list, tuple)) or len(frame_shape) != 2:
            raise FullFramePSFSurveyError("frame_shape must contain [H, W]")
        return cls(
            survey_id=_require_str(data, "survey_id"),
            source_raw_capture_h5=_require_str(data, "source_raw_capture_h5"),
            pupil_profile_id=_optional_str(data.get("pupil_profile_id")),
            camera_profile_id=_optional_str(data.get("camera_profile_id")),
            illumination_mode=_require_str(data, "illumination_mode"),
            entry_wavelengths_nm=[
                float(v) for v in _require_list(data, "entry_wavelengths_nm")
            ],
            entry_illumination_json=[str(v) for v in _require_list(data, "entry_illumination_json")],
            entry_mask_ids=[str(v) for v in _require_list(data, "entry_mask_ids")],
            unique_wavelengths_nm=[
                float(v) for v in _require_list(data, "unique_wavelengths_nm")
            ],
            unique_mask_ids=[str(v) for v in _require_list(data, "unique_mask_ids")],
            frame_shape=(int(frame_shape[0]), int(frame_shape[1])),
            camera_frame_extent=_require_dict(data, "camera_frame_extent"),
            survey_policy=_require_dict(data, "survey_policy"),
            full_frame_role=_require_str(data, "full_frame_role"),
            notes=_optional_str(data.get("notes")),
        )

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["artifact_type"] = "full_frame_psf_survey"
        data["schema_version"] = 1
        data["frame_shape"] = list(self.frame_shape)
        return data

    def to_json(self, path: str | Path | None = None) -> str:
        text = json.dumps(self.to_dict(), indent=2, sort_keys=True)
        if path is not None:
            Path(path).write_text(text + "\n", encoding="utf-8")
        return text

    @classmethod
    def load_json(cls, path: str | Path) -> FullFramePSFSurveyManifest:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise FullFramePSFSurveyError("manifest JSON root must be a mapping")
        return cls.from_dict(data)


def build_full_frame_psf_survey(
    *,
    source_raw_capture_h5: str | Path,
    output_h5: str | Path,
    survey_id: str | None = None,
    manifest_path: str | Path | None = None,
    pupil_profile_manifest: str | Path | None = None,
    camera_profile_manifest: str | Path | None = None,
    background_policy: str = "none",
    normalization_policy: str = "none",
    max_entries: int = 100,
    max_total_pixels: int = 1_000_000,
    allow_large_survey: bool = False,
    notes: str | None = None,
) -> FullFramePSFSurveyManifest:
    """Build a small full-frame scout artifact from raw capture HDF5.

    By default rejects surveys larger than 100 entries or 1M total pixels.
    Pass allow_large_survey=True to override.
    """

    try:
        validate_policy_none(background_policy, "background_policy")
        validate_policy_none(normalization_policy, "normalization_policy")
    except PSFArtifactError as exc:
        raise FullFramePSFSurveyError(str(exc)) from exc
    except ValueError as exc:
        raise FullFramePSFSurveyError(str(exc)) from exc

    source_path = Path(source_raw_capture_h5).resolve()
    output_path = Path(output_h5)
    if source_path == output_path.resolve():
        raise FullFramePSFSurveyError("survey output must not overwrite raw capture")
    if output_path.exists():
        raise FullFramePSFSurveyError("survey output already exists")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if survey_id is None:
        survey_id = output_path.stem
    if manifest_path is None:
        manifest_path = output_path.with_suffix(".manifest.json")
    manifest_path = Path(manifest_path)
    if manifest_path.exists() or manifest_path.resolve() in (source_path, output_path.resolve()):
        raise FullFramePSFSurveyError("manifest must be a distinct new output path")

    with h5py.File(source_path, "r") as src:
        try:
            if src.attrs.get("raw_capture_schema_version") != RAW_CAPTURE_SCHEMA_VERSION:
                raise FullFramePSFSurveyError("unsupported raw capture schema version; explicit conversion required")
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
            flags = json.loads(read_scalar_string(src["capture/processing_flags_json"]))
            plan = json.loads(read_scalar_string(src["capture/plan_json"]))
            n_wavelengths, n_masks = len(plan["wavelengths"]), len(plan["masks"])
            expected_shape = (n_wavelengths * n_masks,)
            if any(a.shape != expected_shape for a in (completed, wavelength_indices, mask_indices, capture_indices)):
                raise FullFramePSFSurveyError("capture index dimensions do not match plan")
            if not flags.get("run_succeeded") or not flags.get("capture_complete") or not _capture_schedule_is_complete(
                completed, capture_indices, wavelength_indices, mask_indices,
                n_wavelengths=n_wavelengths, n_masks=n_masks,
            ):
                raise FullFramePSFSurveyError("survey requires a complete successful raw capture")
            valid_rows = list(range(len(completed)))
            if src["raw/frames_avg"].ndim != 3 or src["raw/frames_avg"].shape[0] != len(completed):
                raise FullFramePSFSurveyError("raw frame dimensions do not match capture rows")
            if not valid_rows:
                raise FullFramePSFSurveyError("raw capture contains no completed captures")

            first_frame = np.asarray(src["raw/frames_avg"][valid_rows[0]])
            if first_frame.ndim != 2:
                raise FullFramePSFSurveyError(
                    f"raw frames_avg entries must be 2D, got shape {first_frame.shape}"
                )
            frame_shape = (int(first_frame.shape[0]), int(first_frame.shape[1]))
            _check_survey_size(
                n_entries=len(valid_rows),
                frame_shape=frame_shape,
                max_entries=max_entries,
                max_total_pixels=max_total_pixels,
                allow_large_survey=allow_large_survey,
            )
            extent = camera_frame_extent_from_hdf5(src, frame_shape=frame_shape)
            require_full_sensor_extent(
                extent,
                artifact_name="full-frame PSF survey",
            )
            for row in valid_rows:
                row_extent = camera_frame_extent_from_dict(json.loads(read_scalar_string(src["camera/frame_extent_json"][row:row+1])))
                if row_extent.shape_hw != frame_shape or row_extent.origin_xy != (0, 0) or row_extent.mode != "full_sensor" or row_extent.sensor_shape_hw != frame_shape:
                    raise FullFramePSFSurveyError("all survey entries require the same full sensor frame extent")


            illumination_by_index = read_string_array(src["illumination/illumination_json"])
            if len(illumination_by_index) != n_wavelengths:
                raise FullFramePSFSurveyError("illumination table length does not match capture plan")
            mask_ids_by_index = read_string_array(src["masks/mask_id"])
            if len(mask_ids_by_index) != n_masks:
                raise FullFramePSFSurveyError("display table length does not match capture plan")
            entry_mask_ids = [
                index_string(mask_ids_by_index, int(mask_indices[row])) for row in valid_rows
            ]
            source_plan_json = read_scalar_string(src["capture/plan_json"])
            entry_illumination_json = [
                index_string(illumination_by_index, int(wavelength_indices[row]))
                for row in valid_rows
            ]
            entry_wavelengths = [
                _wavelength_from_illumination_json(
                    index_string(illumination_by_index, int(wavelength_indices[row]))
                )
                for row in valid_rows
            ]
            pupil_profile_id = read_optional_dataset_string(src, "profiles/pupil_profile_id")
            camera_profile_id = read_optional_dataset_string(src, "profiles/camera_profile_id")
            modes = {json.loads(item)["mode"] for item in entry_illumination_json}
            if modes != {"monochromatic"} or not np.all(np.isfinite(entry_wavelengths)) or any(w <= 0 for w in entry_wavelengths):
                raise FullFramePSFSurveyError("PSF survey requires explicit positive monochromatic wavelengths")
            illum_mode = "monochromatic"
            validate_profile_manifests(
                pupil_profile_id=pupil_profile_id,
                camera_profile_id=camera_profile_id,
                illumination_mode_value=illum_mode,
                wavelengths_nm=unique_preserve_order(entry_wavelengths),
                pupil_profile_manifest=pupil_profile_manifest,
                camera_profile_manifest=camera_profile_manifest,
            )

            manifest = FullFramePSFSurveyManifest(
                survey_id=str(survey_id),
                source_raw_capture_h5=str(source_path),
                pupil_profile_id=pupil_profile_id,
                camera_profile_id=camera_profile_id,
                illumination_mode=illum_mode,
                entry_wavelengths_nm=entry_wavelengths,
                entry_illumination_json=entry_illumination_json,
                entry_mask_ids=entry_mask_ids,
                unique_wavelengths_nm=unique_preserve_order(entry_wavelengths),
                unique_mask_ids=unique_preserve_order(entry_mask_ids),
                frame_shape=frame_shape,
                camera_frame_extent=extent,
                survey_policy={
                    "role": "full_frame_scout_capture",
                    "applied_background_policy": background_policy,
                    "applied_normalization_policy": normalization_policy,
                    "max_entries": max_entries,
                    "max_total_pixels": max_total_pixels,
                    "allow_large_survey": allow_large_survey,
                },
                notes=notes,
            )
        except PSFArtifactError as exc:
            raise FullFramePSFSurveyError(str(exc)) from exc
        except ValueError as exc:
            raise FullFramePSFSurveyError(str(exc)) from exc

        _write_survey_h5(
            output_path=output_path,
            src=src,
            manifest=manifest,
            valid_rows=valid_rows,
            capture_indices=capture_indices[valid_rows],
            wavelength_indices=wavelength_indices[valid_rows],
            mask_indices=mask_indices[valid_rows],
            source_plan_json=source_plan_json,
        )

    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    with manifest_path.open("x", encoding="utf-8") as stream:
        stream.write(manifest.to_json() + "\n")
    return manifest


def _write_survey_h5(
    *,
    output_path: Path,
    src: h5py.File,
    manifest: FullFramePSFSurveyManifest,
    valid_rows: list[int],
    capture_indices: np.ndarray,
    wavelength_indices: np.ndarray,
    mask_indices: np.ndarray,
    source_plan_json: str,
) -> None:
    string_dtype = h5_string_dtype()
    with h5py.File(output_path, "x") as dst:
        dst.attrs["artifact_type"] = "full_frame_psf_survey"
        dst.attrs["schema_version"] = 1
        dst.attrs["complete"] = False
        dst.attrs["survey_id"] = manifest.survey_id
        grp = dst.require_group("full_frame_survey")
        frames = grp.create_dataset(
            "frames_avg",
            shape=(len(valid_rows), manifest.frame_shape[0], manifest.frame_shape[1]),
            dtype=src["raw/frames_avg"].dtype,
            compression="gzip",
            compression_opts=4,
            chunks=(1, manifest.frame_shape[0], manifest.frame_shape[1]),
        )
        for out_idx, row in enumerate(valid_rows):
            frame = np.asarray(src["raw/frames_avg"][row])
            if frame.shape != manifest.frame_shape:
                raise FullFramePSFSurveyError("all survey frames must share shape")
            if not np.all(np.isfinite(frame)):
                raise FullFramePSFSurveyError("survey frames must have finite intensities")
            frames[out_idx] = frame
        settings = []
        for row in valid_rows:
            settings.append(json.dumps({name: float(src[f"camera/{name}"][row]) for name in (
                "requested_exposure_us", "requested_gain_db", "readback_exposure_us", "readback_gain_db"
            )}, sort_keys=True))
        grp.create_dataset("camera_settings_json", data=np.asarray(settings, dtype=object), dtype=string_dtype)
        for name in ("camera", "lcd", "tls", "illumination"):
            src.copy(name, dst)
        grp.create_dataset("entry_mask_ids", data=np.asarray(manifest.entry_mask_ids, dtype=object), dtype=string_dtype)
        grp.create_dataset("entry_wavelength_nm", data=np.asarray(manifest.entry_wavelengths_nm, dtype=np.float64))
        grp.create_dataset(
            "entry_illumination_json",
            data=np.asarray(manifest.entry_illumination_json, dtype=object),
            dtype=string_dtype,
        )
        grp.create_dataset("unique_mask_ids", data=np.asarray(manifest.unique_mask_ids, dtype=object), dtype=string_dtype)
        grp.create_dataset("unique_wavelength_nm", data=np.asarray(manifest.unique_wavelengths_nm, dtype=np.float64))
        grp.create_dataset("mask_index", data=mask_indices.astype(np.int64))
        grp.create_dataset("wavelength_index", data=wavelength_indices.astype(np.int64))
        grp.create_dataset("capture_indices", data=capture_indices.astype(np.int64))
        grp.create_dataset("frame_shape", data=np.asarray(manifest.frame_shape, dtype=np.int64))
        grp.create_dataset("camera_frame_extent_json", data=json.dumps(manifest.camera_frame_extent, sort_keys=True))
        grp.create_dataset("survey_policy_json", data=json.dumps(manifest.survey_policy, sort_keys=True))
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
        source.create_dataset("plan_json", data=source_plan_json)
        dst.attrs["complete"] = True


def _require_str(data: dict[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise FullFramePSFSurveyError(f"{key} must be a non-empty string")
    return value.strip()


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise FullFramePSFSurveyError(f"expected string or null, got {type(value).__name__}")
    stripped = value.strip()
    return stripped or None


def _require_list(data: dict[str, Any], key: str) -> list[Any]:
    value = data.get(key)
    if not isinstance(value, list):
        raise FullFramePSFSurveyError(f"{key} must be a list")
    return value


def _require_dict(data: dict[str, Any], key: str) -> dict[str, Any]:
    value = data.get(key)
    if not isinstance(value, dict):
        raise FullFramePSFSurveyError(f"{key} must be a mapping")
    return value


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


def _check_survey_size(
    *,
    n_entries: int,
    frame_shape: tuple[int, int],
    max_entries: int,
    max_total_pixels: int,
    allow_large_survey: bool,
) -> None:
    if allow_large_survey:
        return
    total_pixels = n_entries * frame_shape[0] * frame_shape[1]
    if n_entries > max_entries:
        raise FullFramePSFSurveyError(
            f"survey has {n_entries} entries (max {max_entries}); "
            "pass allow_large_survey=True to override"
        )
    if total_pixels > max_total_pixels:
        raise FullFramePSFSurveyError(
            f"survey total pixels {total_pixels} exceeds max {max_total_pixels}; "
            "pass allow_large_survey=True to override"
        )
