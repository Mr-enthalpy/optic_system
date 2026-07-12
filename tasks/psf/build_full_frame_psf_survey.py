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
from tasks.artifacts.h5_arrays import read_mask_arrays
from tasks.artifacts.json_io import (
    h5_string_dtype,
    index_string,
    loads_json_object,
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
        from tasks.artifact_versioning import read_schema_version

        read_schema_version(data, "full_frame_psf_survey", legacy_mode=True)
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
            entry_illumination_json=[
                str(v) for v in data.get("entry_illumination_json", [])
            ],
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
        from tasks.artifact_versioning import emit_schema_version

        data = asdict(self)
        data["artifact_type"] = "full_frame_psf_survey"
        data["frame_shape"] = list(self.frame_shape)
        emit_schema_version(data, "full_frame_psf_survey")
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

    source_path = Path(source_raw_capture_h5)
    output_path = Path(output_h5)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if survey_id is None:
        survey_id = output_path.stem
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


            illumination_by_index = _read_raw_illumination_json_by_index(src, plan=None)
            mask_ids_by_index = read_string_array(src["masks/mask_id"])
            entry_mask_ids = [
                index_string(mask_ids_by_index, int(mask_indices[row])) for row in valid_rows
            ]
            source_plan_json = read_scalar_string(src["capture/plan_json"])
            plan = loads_json_object(source_plan_json)
            if not illumination_by_index:
                illumination_by_index = _read_raw_illumination_json_by_index(src, plan=plan)
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
            illum_mode = illumination_mode(plan)
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

    manifest.to_json(manifest_path)
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
    with h5py.File(output_path, "w") as dst:
        dst.attrs["artifact_type"] = "full_frame_psf_survey"
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
            frames[out_idx] = frame
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
