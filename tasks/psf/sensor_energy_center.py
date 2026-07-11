from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import h5py
import numpy as np

from tasks.artifacts.coordinate_frame import (
    CoordinateFrameDescriptor,
    camera_frame_extent_from_dict,
    validate_coordinate_frame_descriptor,
)
from tasks.artifacts.errors import ArtifactIOError
from tasks.artifacts.frame_source import open_full_frame_survey_source
from tasks.valid_pixel_domain import (
    ValidPixelDomainError,
    describe_valid_pixel_domain,
    resolve_valid_pixel_mask,
)


class SensorEnergyCenterError(ValueError):
    pass


@dataclass
class SensorEnergyCenterProfile:
    center_profile_id: str
    source_survey_h5: str
    coordinate_frame: str
    camera_frame_extent: dict[str, Any]
    center_xy: tuple[float, float]
    estimator_name: str
    bg_policy: dict[str, Any]
    corr_policy: dict[str, Any]
    aggregation_policy: dict[str, Any]
    per_entry_center_xy: list[tuple[float, float]]
    per_entry_mask_ids: list[str]
    per_entry_wavelengths_nm: list[float]
    per_entry_background_value: list[float]
    per_entry_total_corr_energy: list[float]
    per_entry_fallback_used: list[bool]
    per_wavelength_mean_center_xy: dict[str, tuple[float, float]]
    per_wavelength_center_std_xy: dict[str, tuple[float, float]]
    global_center_std_xy: tuple[float, float]
    max_center_deviation_px: float
    camera_frame_shape: tuple[int, int] | None = None
    notes: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SensorEnergyCenterProfile":
        return cls(
            center_profile_id=_require_str(data, "center_profile_id"),
            source_survey_h5=_require_str(data, "source_survey_h5"),
            coordinate_frame=_require_str(data, "coordinate_frame"),
            camera_frame_extent=_require_dict(data, "camera_frame_extent"),
            center_xy=_float_pair(data.get("center_xy"), "center_xy"),
            estimator_name=_require_str(data, "estimator_name"),
            bg_policy=_require_dict(data, "bg_policy"),
            corr_policy=_require_dict(data, "corr_policy"),
            aggregation_policy=_require_dict(data, "aggregation_policy"),
            per_entry_center_xy=[
                _float_pair(item, "per_entry_center_xy[]")
                for item in _require_list(data, "per_entry_center_xy")
            ],
            per_entry_mask_ids=[str(x) for x in _require_list(data, "per_entry_mask_ids")],
            per_entry_wavelengths_nm=[
                float(x) for x in _require_list(data, "per_entry_wavelengths_nm")
            ],
            per_entry_background_value=[
                float(x)
                for x in data.get(
                    "per_entry_background_value",
                    [float("nan")] * len(data.get("per_entry_center_xy", [])),
                )
            ],
            per_entry_total_corr_energy=[
                float(x)
                for x in data.get(
                    "per_entry_total_corr_energy",
                    [float("nan")] * len(data.get("per_entry_center_xy", [])),
                )
            ],
            per_entry_fallback_used=[
                bool(x)
                for x in data.get(
                    "per_entry_fallback_used",
                    [False] * len(data.get("per_entry_center_xy", [])),
                )
            ],
            per_wavelength_mean_center_xy={
                str(k): _float_pair(v, f"per_wavelength_mean_center_xy[{k!r}]")
                for k, v in _require_dict(data, "per_wavelength_mean_center_xy").items()
            },
            per_wavelength_center_std_xy={
                str(k): _float_pair(v, f"per_wavelength_center_std_xy[{k!r}]")
                for k, v in _require_dict(data, "per_wavelength_center_std_xy").items()
            },
            global_center_std_xy=_float_pair(
                data.get("global_center_std_xy"),
                "global_center_std_xy",
            ),
            max_center_deviation_px=float(data["max_center_deviation_px"]),
            camera_frame_shape=(
                _int_pair(data.get("camera_frame_shape"), "camera_frame_shape")
                if data.get("camera_frame_shape") is not None else None
            ),
            notes=_optional_str(data.get("notes")),
        )

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["artifact_type"] = "sensor_energy_center_profile"
        data["schema_version"] = 1
        data["center_xy"] = [float(self.center_xy[0]), float(self.center_xy[1])]
        data["per_entry_center_xy"] = [
            [float(x), float(y)] for x, y in self.per_entry_center_xy
        ]
        data["per_entry_background_value"] = [
            float(x) for x in self.per_entry_background_value
        ]
        data["per_entry_total_corr_energy"] = [
            float(x) for x in self.per_entry_total_corr_energy
        ]
        data["per_entry_fallback_used"] = [
            bool(x) for x in self.per_entry_fallback_used
        ]
        data["per_wavelength_mean_center_xy"] = {
            str(k): [float(v[0]), float(v[1])]
            for k, v in sorted(self.per_wavelength_mean_center_xy.items())
        }
        data["per_wavelength_center_std_xy"] = {
            str(k): [float(v[0]), float(v[1])]
            for k, v in sorted(self.per_wavelength_center_std_xy.items())
        }
        data["global_center_std_xy"] = [
            float(self.global_center_std_xy[0]),
            float(self.global_center_std_xy[1]),
        ]
        if self.camera_frame_shape is not None:
            data["camera_frame_shape"] = [
                int(self.camera_frame_shape[0]),
                int(self.camera_frame_shape[1]),
            ]
        return data

    def to_json(self, path: str | Path | None = None) -> str:
        text = json.dumps(self.to_dict(), indent=2, sort_keys=True)
        if path is not None:
            Path(path).write_text(text + "\n", encoding="utf-8")
        return text

    @classmethod
    def load_json(cls, path: str | Path) -> "SensorEnergyCenterProfile":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise SensorEnergyCenterError("center profile JSON root must be a mapping")
        artifact_type = data.get("artifact_type")
        if artifact_type not in {None, "sensor_energy_center_profile"}:
            raise SensorEnergyCenterError(
                f"expected sensor_energy_center_profile, got {artifact_type!r}"
            )
        return cls.from_dict(data)


@dataclass(frozen=True)
class EnergyCenterEstimate:
    center_xy: tuple[float, float]
    background_value: float
    total_corr_energy: float
    fallback_used: bool
    peak_xy: tuple[int, int]
    peak_value: float


def estimate_frame_energy_center(
    frame: np.ndarray,
    *,
    valid_pixel_mask: np.ndarray | None = None,
    bg_percentile: float = 5.0,
) -> EnergyCenterEstimate:
    """Estimate the energy-weighted center of a single frame.

    ``valid_pixel_mask`` must already be a resolved, validated domain (e.g. from
    :func:`tasks.valid_pixel_domain.resolve_valid_pixel_domain`).  This low-level
    helper intentionally does not re-apply the exclusion-fraction cap, so callers
    passing a mask directly are responsible for having enforced the cap / audited
    override upstream.  ``derive_sensor_energy_center_profile`` resolves and caps
    the mask before calling this.
    """
    arr = np.asarray(frame, dtype=np.float64)
    if arr.ndim != 2:
        raise SensorEnergyCenterError(f"frame must be 2D, got {arr.shape}")
    mask = _valid_mask(arr.shape, valid_pixel_mask)
    if not (0.0 <= float(bg_percentile) <= 100.0):
        raise SensorEnergyCenterError("bg_percentile must be in [0, 100]")
    bg = float(np.percentile(arr[mask], float(bg_percentile)))
    corr = np.maximum(arr - bg, 0.0)
    corr_valid = np.where(mask, corr, 0.0)
    peak_eval = np.where(mask, corr, -np.inf)
    peak_y, peak_x = np.unravel_index(int(np.argmax(peak_eval)), arr.shape)
    total = float(np.sum(corr_valid))
    fallback_used = bool(total <= 0.0)
    if total <= 0.0:
        center = (float(peak_x), float(peak_y))
    else:
        yy, xx = np.mgrid[: arr.shape[0], : arr.shape[1]]
        center = (
            float(np.sum(xx * corr_valid) / total),
            float(np.sum(yy * corr_valid) / total),
        )
    return EnergyCenterEstimate(
        center_xy=center,
        background_value=bg,
        total_corr_energy=total,
        fallback_used=fallback_used,
        peak_xy=(int(peak_x), int(peak_y)),
        peak_value=float(arr[peak_y, peak_x]),
    )


def derive_sensor_energy_center_profile(
    survey_h5: str | Path,
    output_json: str | Path,
    *,
    center_profile_id: str | None = None,
    bg_percentile: float = 5.0,
    valid_pixel_domain: dict[str, Any] | None = None,
    valid_pixel_mask: np.ndarray | None = None,
    explicit_mask_large_exclusion_override: bool = False,
    explicit_mask_large_exclusion_reason: str | None = None,
    notes: str | None = None,
) -> SensorEnergyCenterProfile:
    source_path = Path(survey_h5)
    output_path = Path(output_json)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if center_profile_id is None:
        center_profile_id = output_path.stem

    with h5py.File(str(source_path), "r") as f:
        try:
            source = open_full_frame_survey_source(f, source_path)
        except ArtifactIOError as exc:
            raise SensorEnergyCenterError(
                "SensorEnergyCenterProfile requires FullFramePSFSurvey; "
                "convert raw capture to survey first"
            ) from exc
        descriptor = source.descriptor
        resolved_valid_mask = _valid_mask_from_domain(
            descriptor.frame_shape,
            valid_pixel_domain,
            valid_pixel_mask,
            explicit_mask_large_exclusion_override=explicit_mask_large_exclusion_override,
            explicit_mask_large_exclusion_reason=explicit_mask_large_exclusion_reason,
        )
        centers: list[tuple[float, float]] = []
        background_values: list[float] = []
        total_energy: list[float] = []
        fallback_used: list[bool] = []
        for entry_index in range(descriptor.frame_count):
            frame = source.read_frame(entry_index)
            estimate = estimate_frame_energy_center(
                frame,
                valid_pixel_mask=resolved_valid_mask,
                bg_percentile=bg_percentile,
            )
            centers.append(estimate.center_xy)
            background_values.append(float(estimate.background_value))
            total_energy.append(float(estimate.total_corr_energy))
            fallback_used.append(bool(estimate.fallback_used))

    center_arr = np.asarray(centers, dtype=np.float64)
    if center_arr.ndim != 2 or center_arr.shape[0] < 1:
        raise SensorEnergyCenterError("no entry centers were estimated")
    global_center = tuple(float(v) for v in np.mean(center_arr, axis=0))
    std_xy = tuple(float(v) for v in np.std(center_arr, axis=0))
    deviations = np.linalg.norm(center_arr - np.asarray(global_center)[None, :], axis=1)
    per_wavelength_mean: dict[str, tuple[float, float]] = {}
    per_wavelength_std: dict[str, tuple[float, float]] = {}
    for wavelength in _unique_preserve_order(list(descriptor.entry_wavelengths_nm)):
        key = _wavelength_key(wavelength)
        idx = [i for i, value in enumerate(descriptor.entry_wavelengths_nm) if float(value) == float(wavelength)]
        values = center_arr[idx, :]
        per_wavelength_mean[key] = tuple(float(v) for v in np.mean(values, axis=0))
        per_wavelength_std[key] = tuple(float(v) for v in np.std(values, axis=0))

    profile = SensorEnergyCenterProfile(
        center_profile_id=str(center_profile_id),
        source_survey_h5=str(source_path),
        coordinate_frame=descriptor.coordinate_frame,
        camera_frame_extent=descriptor.camera_frame_extent_dict(),
        camera_frame_shape=descriptor.frame_shape,
        center_xy=(float(global_center[0]), float(global_center[1])),
        estimator_name="full_frame_energy_weighted_centroid",
        bg_policy={
            "method": "percentile",
            "percentile": float(bg_percentile),
            "domain": "valid_pixels",
            "valid_pixel_domain": _valid_pixel_domain_record(
                descriptor.frame_shape,
                valid_pixel_domain,
                valid_pixel_mask,
                explicit_mask_large_exclusion_override=explicit_mask_large_exclusion_override,
                explicit_mask_large_exclusion_reason=explicit_mask_large_exclusion_reason,
            ),
            "thesis_algorithm_source": "audited_thesis_energy_center_algorithm",
        },
        corr_policy={
            "formula": "corr = max(frame - bg, 0)",
            "negative_values": "clipped_to_zero",
            "display_tail_normalization_used": False,
            "crop_window_used": False,
        },
        aggregation_policy={
            "method": "arithmetic_mean",
            "domain": "per_entry_centers",
            "single_global_origin": True,
            "per_wavelength_origins": False,
            "outlier_rejection": None,
        },
        notes=notes,
        per_entry_center_xy=[(float(x), float(y)) for x, y in centers],
        per_entry_mask_ids=list(descriptor.mask_ids),
        per_entry_wavelengths_nm=[float(v) for v in descriptor.entry_wavelengths_nm],
        per_entry_background_value=[float(v) for v in background_values],
        per_entry_total_corr_energy=[float(v) for v in total_energy],
        per_entry_fallback_used=[bool(v) for v in fallback_used],
        per_wavelength_mean_center_xy=per_wavelength_mean,
        per_wavelength_center_std_xy=per_wavelength_std,
        global_center_std_xy=(float(std_xy[0]), float(std_xy[1])),
        max_center_deviation_px=float(np.max(deviations)) if deviations.size else 0.0,
    )
    profile.to_json(output_path)
    return profile


def validate_center_profile_for_frame_source(
    profile: SensorEnergyCenterProfile,
    *,
    coordinate_frame: str,
    camera_frame_extent: dict[str, Any],
    frame_shape: tuple[int, int] | None = None,
) -> None:
    actual_shape = (
        tuple(int(v) for v in frame_shape)
        if frame_shape is not None else tuple(int(v) for v in (profile.camera_frame_shape or (0, 0)))
    )
    expected_shape = (
        tuple(int(v) for v in profile.camera_frame_shape)
        if profile.camera_frame_shape is not None else actual_shape
    )
    try:
        actual = CoordinateFrameDescriptor(
            coordinate_frame=str(coordinate_frame),
            camera_frame_extent=camera_frame_extent_from_dict(
                camera_frame_extent,
                fallback_shape=actual_shape,
            ),
            frame_shape=actual_shape,
        )
        expected = CoordinateFrameDescriptor(
            coordinate_frame=str(profile.coordinate_frame),
            camera_frame_extent=camera_frame_extent_from_dict(
                profile.camera_frame_extent,
                fallback_shape=expected_shape,
            ),
            frame_shape=expected_shape,
        )
        validate_coordinate_frame_descriptor(
            actual,
            expected,
            require_same_frame_shape=frame_shape is not None
            and profile.camera_frame_shape is not None,
        )
    except ValueError as exc:
        raise SensorEnergyCenterError(
            f"center profile {str(exc)}"
        ) from exc


def _valid_mask(shape: tuple[int, int], valid_pixel_mask: np.ndarray | None) -> np.ndarray:
    try:
        # The mask is already a validated/resolved domain; do not re-apply the
        # exclusion-fraction cap here (it was enforced upstream, possibly with an
        # explicit large-exclusion override).
        return resolve_valid_pixel_mask(
            shape, valid_pixel_mask=valid_pixel_mask, max_excluded_fraction=1.0
        )
    except ValidPixelDomainError as exc:
        raise SensorEnergyCenterError(str(exc)) from exc


def _valid_mask_from_domain(
    shape: tuple[int, int],
    valid_pixel_domain: dict[str, Any] | None,
    valid_pixel_mask: np.ndarray | None,
    *,
    explicit_mask_large_exclusion_override: bool = False,
    explicit_mask_large_exclusion_reason: str | None = None,
) -> np.ndarray:
    try:
        return resolve_valid_pixel_mask(
            shape,
            valid_pixel_domain,
            valid_pixel_mask,
            explicit_mask_large_exclusion_override=explicit_mask_large_exclusion_override,
            explicit_mask_large_exclusion_reason=explicit_mask_large_exclusion_reason,
        )
    except ValidPixelDomainError as exc:
        raise SensorEnergyCenterError(str(exc)) from exc


def _valid_pixel_domain_record(
    frame_shape: tuple[int, int],
    valid_pixel_domain: dict[str, Any] | None,
    valid_pixel_mask: np.ndarray | None,
    *,
    explicit_mask_large_exclusion_override: bool = False,
    explicit_mask_large_exclusion_reason: str | None = None,
) -> dict[str, Any]:
    return describe_valid_pixel_domain(
        frame_shape=frame_shape,
        valid_pixel_domain=valid_pixel_domain,
        valid_pixel_mask=valid_pixel_mask,
        explicit_mask_large_exclusion_override=explicit_mask_large_exclusion_override,
        explicit_mask_large_exclusion_reason=explicit_mask_large_exclusion_reason,
    )


def _wavelength_key(value: float) -> str:
    v = float(value)
    return str(int(v)) if v.is_integer() else str(v)


def _unique_preserve_order(values: list[float]) -> list[float]:
    out: list[float] = []
    for value in values:
        if not any(float(value) == float(existing) for existing in out):
            out.append(float(value))
    return out


def _require_str(data: dict[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise SensorEnergyCenterError(f"{key} must be a non-empty string")
    return value.strip()


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise SensorEnergyCenterError(f"expected string or null, got {type(value).__name__}")
    stripped = value.strip()
    return stripped or None


def _require_dict(data: dict[str, Any], key: str) -> dict[str, Any]:
    value = data.get(key)
    if not isinstance(value, dict):
        raise SensorEnergyCenterError(f"{key} must be a mapping")
    return value


def _require_list(data: dict[str, Any], key: str) -> list[Any]:
    value = data.get(key)
    if not isinstance(value, list):
        raise SensorEnergyCenterError(f"{key} must be a list")
    return value


def _float_pair(value: Any, name: str) -> tuple[float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise SensorEnergyCenterError(f"{name} must contain two numbers")
    return (float(value[0]), float(value[1]))


def _int_pair(value: Any, name: str) -> tuple[int, int]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise SensorEnergyCenterError(f"{name} must contain two integers")
    return (int(value[0]), int(value[1]))
