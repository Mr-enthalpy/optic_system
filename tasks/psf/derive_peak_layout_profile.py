from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import h5py
import numpy as np

from tasks.artifacts.coordinate_frame import (
    camera_frame_extent_from_dict,
    resolve_coordinate_frame,
    validate_coordinate_frame_extent,
)
from tasks.artifacts.json_io import read_json_dataset_or_attr

from .profile_requirements import PSFArtifactError
from .sensor_energy_center import (
    SensorEnergyCenterError,
    SensorEnergyCenterProfile,
    validate_center_profile_for_frame_source,
)


class PeakLayoutProfileError(PSFArtifactError):
    pass


@dataclass
class PeakLayoutProfileManifest:
    peak_layout_id: str
    source_survey_h5: str
    frame_shape: tuple[int, int]
    coordinate_frame: str
    camera_frame_extent: dict[str, Any]
    peak_ids: list[str]
    center_xy: list[list[float]]
    patch_shape_hw: list[list[int]]
    patch_origin_xy: list[list[int]]
    stability_score: list[float]
    amplitude_range: list[list[float]]
    local_background_stats: list[dict[str, float]]
    survey_wavelengths_nm: list[float]
    survey_mask_ids: list[str]
    valid_wavelengths_nm: list[float]
    valid_mask_ids: list[str]
    validity_scope: dict[str, str]
    detection_policy: dict[str, Any]
    notes: str | None = None
    center_profile_id: str | None = None
    energy_center_xy: list[float] | None = None
    center_xy_rel: list[list[float]] | None = None

    @classmethod
    def from_dict(
        cls,
        data: dict[str, Any],
        *,
        legacy_mode: bool = True,
    ) -> PeakLayoutProfileManifest:
        from tasks.artifact_versioning import read_schema_version

        read_schema_version(data, "peak_layout_profile", legacy_mode=legacy_mode)
        frame_shape = data.get("frame_shape")
        if not isinstance(frame_shape, (list, tuple)) or len(frame_shape) != 2:
            raise PeakLayoutProfileError("frame_shape must contain [H, W]")
        return cls(
            peak_layout_id=_require_str(data, "peak_layout_id"),
            source_survey_h5=_require_str(data, "source_survey_h5"),
            frame_shape=(int(frame_shape[0]), int(frame_shape[1])),
            coordinate_frame=_require_str(data, "coordinate_frame"),
            camera_frame_extent=_require_dict(data, "camera_frame_extent"),
            peak_ids=[str(v) for v in _require_list(data, "peak_ids")],
            center_xy=_float_pairs(data, "center_xy"),
            patch_shape_hw=_int_pairs(data, "patch_shape_hw"),
            patch_origin_xy=_int_pairs(data, "patch_origin_xy"),
            stability_score=[float(v) for v in _require_list(data, "stability_score")],
            amplitude_range=_float_pairs(data, "amplitude_range"),
            local_background_stats=[
                dict(v) for v in _require_list(data, "local_background_stats")
            ],
            survey_wavelengths_nm=[
                float(v)
                for v in _require_list(
                    data,
                    "survey_wavelengths_nm",
                    fallback_key="valid_wavelengths_nm",
                )
            ],
            survey_mask_ids=[
                str(v)
                for v in _require_list(
                    data,
                    "survey_mask_ids",
                    fallback_key="valid_mask_ids",
                )
            ],
            valid_wavelengths_nm=[
                float(v) for v in _require_list(data, "valid_wavelengths_nm")
            ],
            valid_mask_ids=[str(v) for v in _require_list(data, "valid_mask_ids")],
            validity_scope=_require_dict(data, "validity_scope"),
            detection_policy=_require_dict(data, "detection_policy"),
            notes=_optional_str(data.get("notes")),
            center_profile_id=_optional_str(data.get("center_profile_id")),
            energy_center_xy=(
                [float(v) for v in _float_pair(data["energy_center_xy"], "energy_center_xy")]
                if data.get("energy_center_xy") is not None else None
            ),
            center_xy_rel=(
                _float_pairs(data, "center_xy_rel")
                if data.get("center_xy_rel") is not None else None
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        from tasks.artifact_versioning import emit_schema_version

        data = asdict(self)
        data["artifact_type"] = "peak_layout_profile"
        data["frame_shape"] = list(self.frame_shape)
        emit_schema_version(data, "peak_layout_profile")
        return data

    def validate(self) -> None:
        """Validate measured peak-layout structure without scientific judgement."""
        try:
            validate_coordinate_frame_extent(
                self.coordinate_frame,
                self.camera_frame_extent,
                self.frame_shape,
            )
        except ValueError as exc:
            raise PeakLayoutProfileError(str(exc)) from exc

        if not self.peak_ids or any(
            not isinstance(peak_id, str) or not peak_id.strip()
            for peak_id in self.peak_ids
        ):
            raise PeakLayoutProfileError("peak_ids must be non-empty strings")
        if len(set(self.peak_ids)) != len(self.peak_ids):
            raise PeakLayoutProfileError("peak_ids must be unique")

        peak_count = len(self.peak_ids)
        aligned = {
            "center_xy": self.center_xy,
            "patch_shape_hw": self.patch_shape_hw,
            "patch_origin_xy": self.patch_origin_xy,
            "stability_score": self.stability_score,
            "amplitude_range": self.amplitude_range,
            "local_background_stats": self.local_background_stats,
        }
        for name, values in aligned.items():
            if len(values) != peak_count:
                raise PeakLayoutProfileError(
                    f"{name} length must match peak_ids length"
                )
        if self.center_xy_rel is not None and len(self.center_xy_rel) != peak_count:
            raise PeakLayoutProfileError(
                "center_xy_rel length must match peak_ids length"
            )

        h, w = self.frame_shape
        for index in range(peak_count):
            _finite_pair(self.center_xy[index], f"center_xy[{index}]")
            if self.center_xy_rel is not None:
                _finite_pair(self.center_xy_rel[index], f"center_xy_rel[{index}]")
            ph, pw = _positive_int_pair(
                self.patch_shape_hw[index],
                f"patch_shape_hw[{index}]",
            )
            x0, y0 = _nonnegative_int_pair(
                self.patch_origin_xy[index],
                f"patch_origin_xy[{index}]",
            )
            if x0 + pw > w or y0 + ph > h:
                raise PeakLayoutProfileError(
                    f"patch {index} extends outside frame_shape"
                )
            _finite_number(self.stability_score[index], f"stability_score[{index}]")
            low, high = _finite_pair(
                self.amplitude_range[index],
                f"amplitude_range[{index}]",
            )
            if low > high:
                raise PeakLayoutProfileError(
                    f"amplitude_range[{index}] must be ordered"
                )

        if not _subset_values(self.valid_wavelengths_nm, self.survey_wavelengths_nm):
            raise PeakLayoutProfileError(
                "valid_wavelengths_nm must be contained in survey_wavelengths_nm"
            )
        if not set(self.valid_mask_ids).issubset(set(self.survey_mask_ids)):
            raise PeakLayoutProfileError(
                "valid_mask_ids must be contained in survey_mask_ids"
            )

    def to_json(self, path: str | Path | None = None) -> str:
        text = json.dumps(self.to_dict(), indent=2, sort_keys=True)
        if path is not None:
            Path(path).write_text(text + "\n", encoding="utf-8")
        return text

    @classmethod
    def load_json(cls, path: str | Path) -> PeakLayoutProfileManifest:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise PeakLayoutProfileError("profile JSON root must be a mapping")
        return cls.from_dict(data)

    @property
    def n_peaks(self) -> int:
        return len(self.peak_ids)


def derive_peak_layout_profile(
    *,
    survey_h5: str | Path,
    output_json: str | Path,
    peak_layout_id: str | None = None,
    patch_shape_hw: tuple[int, int] = (9, 9),
    threshold_sigma: float = 3.0,
    min_area: int = 1,
    max_peaks: int | None = None,
    center_profile: str | Path | SensorEnergyCenterProfile,
    notes: str | None = None,
) -> PeakLayoutProfileManifest:
    """Derive a replaceable first-pass peak layout from a full-frame scout survey.

    Requires a SensorEnergyCenterProfile to compute center-relative peak coordinates.
    """

    survey_path = Path(survey_h5)
    output_path = Path(output_json)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if peak_layout_id is None:
        peak_layout_id = output_path.stem
    if patch_shape_hw[0] <= 0 or patch_shape_hw[1] <= 0:
        raise PeakLayoutProfileError("patch_shape_hw must be positive")
    center_profile_obj = _load_center_profile(center_profile)

    with h5py.File(survey_path, "r") as src:
        if "full_frame_survey/frames_avg" not in src:
            raise PeakLayoutProfileError("survey missing full_frame_survey/frames_avg")
        frames_dset = src["full_frame_survey/frames_avg"]
        if frames_dset.ndim != 3:
            raise PeakLayoutProfileError("survey frames must have shape [N, H, W]")
        n, h, w = frames_dset.shape
        if n < 1:
            raise PeakLayoutProfileError("survey contains no frames")

        mean_map = np.zeros((h, w), dtype=np.float64)
        for i in range(n):
            mean_map += np.asarray(frames_dset[i], dtype=np.float64)
        mean_map /= float(n)

        background_median = float(np.median(mean_map))
        background_std = float(np.std(mean_map))
        threshold = background_median + float(threshold_sigma) * background_std
        components = _connected_components(mean_map > threshold)
        peaks = []
        for pixels in components:
            if len(pixels) < min_area:
                continue
            ys = np.asarray([p[0] for p in pixels], dtype=np.int64)
            xs = np.asarray([p[1] for p in pixels], dtype=np.int64)
            weights = mean_map[ys, xs]
            total = float(weights.sum())
            if total <= 0:
                center_x = float(xs.mean())
                center_y = float(ys.mean())
            else:
                center_x = float((xs * weights).sum() / total)
                center_y = float((ys * weights).sum() / total)
            y0, x0 = _patch_origin(
                center_xy=(center_x, center_y),
                frame_shape=(h, w),
                patch_shape_hw=patch_shape_hw,
            )
            local = mean_map[y0 : y0 + patch_shape_hw[0], x0 : x0 + patch_shape_hw[1]]
            peaks.append({
                "center_xy": [center_x, center_y],
                "origin_xy": [int(x0), int(y0)],
                "max_value": float(local.max()) if local.size else 0.0,
                "energy": float(weights.sum()),
                "local_median": float(np.median(local)) if local.size else 0.0,
                "local_std": float(np.std(local)) if local.size else 0.0,
            })
        peaks.sort(key=lambda item: item["energy"], reverse=True)
        if max_peaks is not None:
            peaks = peaks[: int(max_peaks)]
        peaks.sort(key=lambda item: (item["origin_xy"][1], item["origin_xy"][0]))
        if not peaks:
            raise PeakLayoutProfileError("no stable peaks detected")

        peak_ids = [f"peak_{i:04d}" for i in range(len(peaks))]
        stability = [
            _stability_score(frames_dset, p["origin_xy"], patch_shape_hw, threshold)
            for p in peaks
        ]
        valid_wavelengths = _read_float_dataset(src, "full_frame_survey/unique_wavelength_nm")
        valid_mask_ids = _read_string_dataset(src, "full_frame_survey/unique_mask_ids")
        camera_extent = _read_camera_frame_extent(src)
        coordinate_frame = _coordinate_frame(camera_extent)
        try:
            validate_center_profile_for_frame_source(
                center_profile_obj,
                coordinate_frame=coordinate_frame,
                camera_frame_extent=camera_extent,
                frame_shape=(int(h), int(w)),
            )
        except SensorEnergyCenterError as exc:
            raise PeakLayoutProfileError(str(exc)) from exc
        energy_center_xy = [
            float(center_profile_obj.center_xy[0]),
            float(center_profile_obj.center_xy[1]),
        ]
        center_xy_rel = [
            [
                float(p["center_xy"][0]) - float(energy_center_xy[0]),
                float(p["center_xy"][1]) - float(energy_center_xy[1]),
            ]
            for p in peaks
        ]
        manifest = PeakLayoutProfileManifest(
            peak_layout_id=str(peak_layout_id),
            source_survey_h5=str(survey_path),
            frame_shape=(int(h), int(w)),
            coordinate_frame=coordinate_frame,
            camera_frame_extent=camera_extent,
            peak_ids=peak_ids,
            center_xy=[p["center_xy"] for p in peaks],
            patch_shape_hw=[
                [int(patch_shape_hw[0]), int(patch_shape_hw[1])] for _ in peaks
            ],
            patch_origin_xy=[p["origin_xy"] for p in peaks],
            stability_score=stability,
            amplitude_range=[[0.0, p["max_value"]] for p in peaks],
            local_background_stats=[
                {"median": p["local_median"], "std": p["local_std"]} for p in peaks
            ],
            survey_wavelengths_nm=valid_wavelengths,
            survey_mask_ids=valid_mask_ids,
            valid_wavelengths_nm=valid_wavelengths,
            valid_mask_ids=valid_mask_ids,
            validity_scope={
                "mask_scope": "survey_only",
                "wavelength_scope": "survey_only",
            },
            detection_policy={
                "algorithm": "mean_energy_threshold_connected_components",
                "algorithm_role": "first_pass_high_energy_layout_baseline",
                "known_limitation": "may miss low-energy stable far-field peaks",
                "threshold_sigma": float(threshold_sigma),
                "threshold_value": float(threshold),
                "min_area": int(min_area),
                "max_peaks": max_peaks,
                "patch_shape_hw": [int(patch_shape_hw[0]), int(patch_shape_hw[1])],
                "center_profile_role": "required",
            },
            center_profile_id=center_profile_obj.center_profile_id,
            energy_center_xy=energy_center_xy,
            center_xy_rel=center_xy_rel,
            notes=notes,
        )

    manifest.to_json(output_path)
    return manifest


def _connected_components(mask: np.ndarray) -> list[list[tuple[int, int]]]:
    visited = np.zeros(mask.shape, dtype=bool)
    components: list[list[tuple[int, int]]] = []
    h, w = mask.shape
    for y in range(h):
        for x in range(w):
            if not mask[y, x] or visited[y, x]:
                continue
            stack = [(y, x)]
            visited[y, x] = True
            pixels: list[tuple[int, int]] = []
            while stack:
                cy, cx = stack.pop()
                pixels.append((cy, cx))
                for ny in range(max(0, cy - 1), min(h, cy + 2)):
                    for nx in range(max(0, cx - 1), min(w, cx + 2)):
                        if mask[ny, nx] and not visited[ny, nx]:
                            visited[ny, nx] = True
                            stack.append((ny, nx))
            components.append(pixels)
    return components


def _patch_origin(
    *,
    center_xy: tuple[float, float],
    frame_shape: tuple[int, int],
    patch_shape_hw: tuple[int, int],
) -> tuple[int, int]:
    h, w = frame_shape
    ph, pw = patch_shape_hw
    x0 = int(round(center_xy[0] - pw / 2.0))
    y0 = int(round(center_xy[1] - ph / 2.0))
    x0 = max(0, min(w - pw, x0))
    y0 = max(0, min(h - ph, y0))
    return y0, x0


def _stability_score(
    frames_dset: h5py.Dataset,
    origin_xy: list[int],
    patch_shape_hw: tuple[int, int],
    threshold: float,
) -> float:
    x0, y0 = origin_xy
    ph, pw = patch_shape_hw
    hits = 0
    for i in range(frames_dset.shape[0]):
        patch = np.asarray(frames_dset[i, y0 : y0 + ph, x0 : x0 + pw])
        if patch.size and float(patch.max()) > threshold:
            hits += 1
    return float(hits / frames_dset.shape[0])


def _read_float_dataset(src: h5py.File, path: str) -> list[float]:
    if path not in src:
        return []
    return [float(v) for v in np.asarray(src[path])]


def _read_string_dataset(src: h5py.File, path: str) -> list[str]:
    if path not in src:
        return []
    return [
        value.decode("utf-8") if isinstance(value, bytes) else str(value)
        for value in src[path][()]
    ]


def _read_camera_frame_extent(src: h5py.File) -> dict[str, Any]:
    if "full_frame_survey/camera_frame_extent_json" not in src:
        raise PeakLayoutProfileError(
            "survey missing full_frame_survey/camera_frame_extent_json"
        )
    try:
        extent = read_json_dataset_or_attr(src["full_frame_survey"], "camera_frame_extent_json")
    except ValueError as exc:
        raise PeakLayoutProfileError(str(exc)) from exc
    return extent


def _coordinate_frame(camera_frame_extent: dict[str, Any]) -> str:
    try:
        extent = camera_frame_extent_from_dict(camera_frame_extent)
        return resolve_coordinate_frame(extent)
    except ValueError:
        return "acquired_frame"


def _load_center_profile(
    center_profile: str | Path | SensorEnergyCenterProfile,
) -> SensorEnergyCenterProfile:
    if isinstance(center_profile, SensorEnergyCenterProfile):
        return center_profile
    try:
        return SensorEnergyCenterProfile.load_json(center_profile)
    except SensorEnergyCenterError as exc:
        raise PeakLayoutProfileError(str(exc)) from exc


def _require_str(data: dict[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise PeakLayoutProfileError(f"{key} must be a non-empty string")
    return value.strip()


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise PeakLayoutProfileError(f"expected string or null, got {type(value).__name__}")
    stripped = value.strip()
    return stripped or None


def _require_list(
    data: dict[str, Any],
    key: str,
    *,
    fallback_key: str | None = None,
) -> list[Any]:
    value = data.get(key)
    if value is None and fallback_key is not None:
        value = data.get(fallback_key)
    if not isinstance(value, list):
        raise PeakLayoutProfileError(f"{key} must be a list")
    return value


def _require_dict(data: dict[str, Any], key: str) -> dict[str, Any]:
    value = data.get(key)
    if not isinstance(value, dict):
        raise PeakLayoutProfileError(f"{key} must be a mapping")
    return value


def _float_pairs(data: dict[str, Any], key: str) -> list[list[float]]:
    return [[float(pair[0]), float(pair[1])] for pair in _require_list(data, key)]


def _float_pair(value: Any, name: str) -> tuple[float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise PeakLayoutProfileError(f"{name} must be a pair")
    return (float(value[0]), float(value[1]))


def _int_pairs(data: dict[str, Any], key: str) -> list[list[int]]:
    return [[int(pair[0]), int(pair[1])] for pair in _require_list(data, key)]


def _finite_number(value: Any, name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise PeakLayoutProfileError(f"{name} must be a finite number") from exc
    if not math.isfinite(number):
        raise PeakLayoutProfileError(f"{name} must be finite")
    return number


def _finite_pair(value: Any, name: str) -> tuple[float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise PeakLayoutProfileError(f"{name} must contain two numbers")
    return (
        _finite_number(value[0], f"{name}[0]"),
        _finite_number(value[1], f"{name}[1]"),
    )


def _positive_int_pair(value: Any, name: str) -> tuple[int, int]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise PeakLayoutProfileError(f"{name} must contain two integers")
    pair = (int(value[0]), int(value[1]))
    if pair[0] <= 0 or pair[1] <= 0:
        raise PeakLayoutProfileError(f"{name} dimensions must be positive")
    return pair


def _nonnegative_int_pair(value: Any, name: str) -> tuple[int, int]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise PeakLayoutProfileError(f"{name} must contain two integers")
    pair = (int(value[0]), int(value[1]))
    if pair[0] < 0 or pair[1] < 0:
        raise PeakLayoutProfileError(f"{name} must be non-negative")
    return pair


def _subset_values(values: list[float], candidates: list[float]) -> bool:
    return all(any(float(value) == float(item) for item in candidates) for value in values)
