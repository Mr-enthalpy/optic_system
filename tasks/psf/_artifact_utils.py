from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import h5py
import numpy as np

from tasks.artifacts.coordinate_frame import (
    camera_frame_extent_to_dict,
    read_camera_frame_extent_from_group,
)
from tasks.profiles import CameraProfile, PupilProfile
from .profile_requirements import validate_psf_profile_dependencies


class PSFArtifactError(ValueError):
    pass


def read_scalar_string(dataset: h5py.Dataset) -> str:
    value = dataset[()]
    if isinstance(value, bytes):
        return value.decode("utf-8")
    if isinstance(value, np.ndarray):
        value = value.flat[0]
        if isinstance(value, bytes):
            return value.decode("utf-8")
    return str(value)


def read_optional_dataset_string(src: h5py.File, path: str) -> str | None:
    if path not in src:
        return None
    value = read_scalar_string(src[path]).strip()
    return value or None


def read_string_array(dataset: h5py.Dataset) -> list[str]:
    values = dataset[()]
    result: list[str] = []
    for value in values:
        result.append(value.decode("utf-8") if isinstance(value, bytes) else str(value))
    return result


def loads_json_object(text: str) -> dict[str, Any]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def read_mask_arrays(src: h5py.File) -> np.ndarray | None:
    if "masks/masks_physical" not in src:
        return None
    masks = np.asarray(src["masks/masks_physical"])
    if masks.ndim == 3 and masks.shape[1] > 1 and masks.shape[2] > 1:
        return masks
    return None


def unique_preserve_order(values: list[Any]) -> list[Any]:
    result: list[Any] = []
    for value in values:
        if value not in result:
            result.append(value)
    return result


def index_string(values: list[str], index: int) -> str:
    if index < 0 or index >= len(values):
        raise PSFArtifactError(f"index {index} out of range")
    return values[index]


def validate_policy_none(policy: str, name: str) -> None:
    if policy != "none":
        raise PSFArtifactError(
            f"{name}={policy!r} is not implemented; only 'none' is currently allowed"
        )


def illumination_mode(plan: dict[str, Any]) -> str:
    for key_path in (
        ("illumination", "mode"),
        ("extra", "illumination", "mode"),
    ):
        node: Any = plan
        for key in key_path:
            if not isinstance(node, dict) or key not in node:
                node = None
                break
            node = node[key]
        if isinstance(node, str) and node.strip():
            return node.strip()
    return "monochromatic"


def camera_frame_extent(
    src: h5py.File,
    *,
    frame_shape: tuple[int, int] | None,
) -> dict[str, Any]:
    if "camera" in src:
        extent = read_camera_frame_extent_from_group(
            src["camera"],
            fallback_shape=frame_shape,
        )
        return camera_frame_extent_to_dict(extent)
    if frame_shape is None:
        shape_hw = None
    else:
        shape_hw = [int(frame_shape[0]), int(frame_shape[1])]
    return {
        "mode": "unknown",
        "origin_xy": [0, 0],
        "shape_hw": shape_hw,
        "sensor_shape_hw": None,
        "source": "fallback_from_frame_shape",
    }


def validate_profile_manifests(
    *,
    pupil_profile_id: str | None,
    camera_profile_id: str | None,
    illumination_mode_value: str,
    wavelengths_nm: list[float],
    pupil_profile_manifest: str | Path | None,
    camera_profile_manifest: str | Path | None,
    allow_profile_id_only: bool,
) -> None:
    if not pupil_profile_id or not camera_profile_id:
        raise PSFArtifactError("PSF artifacts require pupil_profile_id and camera_profile_id")
    if pupil_profile_manifest is None or camera_profile_manifest is None:
        if allow_profile_id_only:
            return
        raise PSFArtifactError(
            "pupil_profile_manifest and camera_profile_manifest are required; "
            "pass allow_profile_id_only only for legacy metadata"
        )
    try:
        pupil = _load_profile_manifest(PupilProfile, pupil_profile_manifest)
        camera = _load_profile_manifest(CameraProfile, camera_profile_manifest)
        validate_psf_profile_dependencies(
            {
                "requires": {
                    "pupil_profile_id": pupil_profile_id,
                    "camera_profile_id": camera_profile_id,
                },
                "illumination": {
                    "mode": illumination_mode_value,
                    "wavelengths_nm": wavelengths_nm,
                },
            },
            pupil_profile=pupil,
            camera_profile=camera,
        )
    except ValueError as exc:
        raise PSFArtifactError(str(exc)) from exc


def require_full_sensor_extent(
    extent: dict[str, Any],
    *,
    allow_acquired_frame_only: bool,
    artifact_name: str,
) -> None:
    if extent.get("mode") == "full_sensor":
        return
    if allow_acquired_frame_only:
        return
    raise PSFArtifactError(
        f"cannot confirm full-sensor acquisition for {artifact_name}; pass "
        "allow_acquired_frame_only to record acquired-frame coordinates explicitly"
    )


def require_paths(src: h5py.File, paths: list[str]) -> None:
    for path in paths:
        if path not in src:
            raise PSFArtifactError(f"raw capture missing {path}")


def h5_string_dtype() -> h5py.Datatype:
    return h5py.string_dtype(encoding="utf-8")


def _load_profile_manifest(cls: Any, path: str | Path) -> Any:
    profile_path = Path(path)
    if profile_path.suffix.lower() in {".yaml", ".yml"}:
        return cls.load_yaml(profile_path)
    return cls.load_json(profile_path)


