from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import h5py
import numpy as np

from tasks.artifacts.errors import ArtifactIOError
from tasks.artifacts.frame_source import (
    frame_dataset_count_and_shape as _shared_frame_dataset_count_and_shape,
    open_full_frame_survey_source,
    read_frame_entry as _shared_read_frame_entry,
)
from tasks.artifacts.json_io import (
    decode_h5_string,
)
from tasks.runtime_mode import RuntimePolicy

from .sensor_energy_center import (
    SensorEnergyCenterError,
    SensorEnergyCenterProfile,
    validate_center_profile_for_frame_source,
)

try:  # scipy is optional; keep the pure-Python fallback for minimal environments.
    from scipy import ndimage as _scipy_ndimage
except Exception:  # pragma: no cover - exercised only when scipy is unavailable.
    _scipy_ndimage = None


DEFAULT_TAU_VALUES = [0.1, 0.5, 1.0, 2.0, 5.0]
DEFAULT_SUPPORT_RADII = [200.0, 300.0, 500.0]
DEFAULT_FAR_FIELD_RADIUS = 200.0
DEFAULT_BG_PERCENTILE = 5.0
DEFAULT_MIN_COMPONENT_AREA = 1
DEFAULT_CONNECTIVITY = 8
SUPPORT_ANALYSIS_PRESETS: dict[str, dict[str, Any]] = {
    "measured_full_frame_2048": {
        "min_component_area": 8,
        "description": "Measured 2048 x 2448 full-frame Phase 3 support analysis.",
    },
}


class DiffractionSupportAnalysisError(ValueError):
    pass


@dataclass
class PeakSupportAnalysisManifest:
    report_id: str
    source_survey_h5: str
    frame_shape: tuple[int, int]
    coordinate_frame: str
    camera_frame_extent: dict[str, Any]
    tau_values: list[float]
    support_radii: list[float]
    bg_policy: dict[str, Any]
    corr_policy: dict[str, Any]
    radial_policy: dict[str, Any]
    component_policy: dict[str, Any]
    entry_mask_ids: list[str]
    entry_wavelengths_nm: list[float]
    notes: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PeakSupportAnalysisManifest":
        return cls(
            report_id=str(data["report_id"]),
            source_survey_h5=str(data["source_survey_h5"]),
            frame_shape=_int_pair(data["frame_shape"], "frame_shape"),
            coordinate_frame=str(data["coordinate_frame"]),
            camera_frame_extent=dict(data.get("camera_frame_extent") or {}),
            tau_values=[float(x) for x in data["tau_values"]],
            support_radii=[float(x) for x in data["support_radii"]],
            bg_policy=dict(data["bg_policy"]),
            corr_policy=dict(data["corr_policy"]),
            radial_policy=dict(data["radial_policy"]),
            component_policy=dict(data["component_policy"]),
            entry_mask_ids=[str(x) for x in data["entry_mask_ids"]],
            entry_wavelengths_nm=[float(x) for x in data["entry_wavelengths_nm"]],
            notes=data.get("notes"),
        )

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["artifact_type"] = "peak_support_analysis_report"
        out["schema_version"] = 1
        out["frame_shape"] = [int(self.frame_shape[0]), int(self.frame_shape[1])]
        return out

    def to_json_text(self) -> str:
        return json.dumps(self.to_dict(), indent=2, sort_keys=True)


@dataclass
class SurveyMetadata:
    mask_ids: list[str]
    wavelengths_nm: list[float]
    frame_shape: tuple[int, int]
    coordinate_frame: str
    camera_frame_extent: dict[str, Any]
    frame_count: int
    frame_dataset_path: str


def analyze_diffraction_support(
    survey_h5: str | Path,
    output_h5: str | Path,
    *,
    report_id: str | None = None,
    tau_values: list[float] | tuple[float, ...] | None = None,
    support_radii: list[float] | tuple[float, ...] | None = None,
    far_field_radius: float | None = None,
    bg_percentile: float | None = None,
    min_component_area: int | None = None,
    connectivity: int | None = None,
    center_policy: str = "frame_center",
    manual_center_xy: tuple[float, float] | None = None,
    center_profile: str | Path | SensorEnergyCenterProfile | None = None,
    valid_pixel_domain: dict[str, Any] | None = None,
    energy_only: bool = False,
    preset_name: str | None = None,
    notes: str | None = None,
    runtime_policy: RuntimePolicy | str | None = None,
) -> PeakSupportAnalysisManifest:
    source_path = Path(survey_h5)
    out_path = Path(output_h5)
    preset = _support_analysis_preset(preset_name)
    center_profile_obj = _load_center_profile(center_profile)
    if center_profile_obj is not None:
        if center_policy not in {"frame_center", "sensor_energy_center_profile"}:
            raise DiffractionSupportAnalysisError(
                "center_profile requires center_policy='sensor_energy_center_profile'"
            )
        center_policy = "sensor_energy_center_profile"
    tau = [float(x) for x in (tau_values or DEFAULT_TAU_VALUES)]
    radii = [float(x) for x in (support_radii or DEFAULT_SUPPORT_RADII)]
    resolved_far_field_radius = (
        float(far_field_radius)
        if far_field_radius is not None
        else float(preset.get("far_field_radius", DEFAULT_FAR_FIELD_RADIUS))
    )
    resolved_bg_percentile = (
        float(bg_percentile)
        if bg_percentile is not None
        else float(preset.get("bg_percentile", DEFAULT_BG_PERCENTILE))
    )
    resolved_min_component_area = (
        int(min_component_area)
        if min_component_area is not None
        else int(preset.get("min_component_area", DEFAULT_MIN_COMPONENT_AREA))
    )
    resolved_connectivity = (
        int(connectivity)
        if connectivity is not None
        else int(preset.get("connectivity", DEFAULT_CONNECTIVITY))
    )
    _validate_parameters(
        tau_values=tau,
        support_radii=radii,
        far_field_radius=resolved_far_field_radius,
        bg_percentile=resolved_bg_percentile,
        min_component_area=resolved_min_component_area,
        connectivity=resolved_connectivity,
        center_policy=center_policy,
    )

    component_rows: list[dict[str, Any]] = []
    with h5py.File(str(source_path), "r") as source_file:
        frames, survey = _open_survey_frame_source(source_file)
        if center_profile_obj is not None:
            try:
                validate_center_profile_for_frame_source(
                    center_profile_obj,
                    coordinate_frame=survey.coordinate_frame,
                    camera_frame_extent=survey.camera_frame_extent,
                    frame_shape=survey.frame_shape,
                )
            except SensorEnergyCenterError as exc:
                raise DiffractionSupportAnalysisError(str(exc)) from exc
        valid_mask = _valid_pixel_mask(survey.frame_shape, valid_pixel_domain)
        if not np.any(valid_mask):
            raise DiffractionSupportAnalysisError("valid pixel mask is empty")

        n = survey.frame_count
        r_count = len(radii)
        t_count = len(tau)
        background = np.zeros((n,), dtype=np.float64)
        compact_energy = np.zeros((n, r_count), dtype=np.float64)
        compact_fraction = np.zeros((n, r_count), dtype=np.float64)
        far_noise_energy = np.zeros((n, t_count), dtype=np.float64)
        far_sig_energy = np.zeros((n, t_count), dtype=np.float64)
        far_noise_count = np.zeros((n, t_count), dtype=np.int64)
        far_sig_count = np.zeros((n, t_count), dtype=np.int64)
        center_xy = np.zeros((n, 2), dtype=np.float64)
        total_corr_energy = np.zeros((n,), dtype=np.float64)

        for entry_index in range(n):
            psf = _read_frame_entry(frames, entry_index)
            bg = float(np.percentile(psf[valid_mask], resolved_bg_percentile))
            corr = np.maximum(psf - bg, 0.0)
            corr_valid = np.where(valid_mask, corr, 0.0)
            center = _resolve_center_xy(
                corr_valid,
                valid_mask,
                center_policy,
                manual_center_xy,
                center_profile_obj,
            )
            radius_map = _radius_map(survey.frame_shape, center)
            total = float(np.sum(corr_valid))
            far_mask = (radius_map >= float(resolved_far_field_radius)) & valid_mask

            background[entry_index] = bg
            center_xy[entry_index] = center
            total_corr_energy[entry_index] = total
            for r_idx, radius in enumerate(radii):
                mask = (radius_map < float(radius)) & valid_mask
                energy = float(np.sum(corr_valid[mask]))
                compact_energy[entry_index, r_idx] = energy
                compact_fraction[entry_index, r_idx] = energy / total if total > 0 else 0.0
            for t_idx, threshold in enumerate(tau):
                significant = corr_valid >= float(threshold)
                far_significant = far_mask & significant
                far_noise = far_mask & ~significant
                far_sig_energy[entry_index, t_idx] = float(np.sum(corr_valid[far_significant]))
                far_noise_energy[entry_index, t_idx] = float(np.sum(corr_valid[far_noise]))
                far_sig_count[entry_index, t_idx] = int(np.count_nonzero(far_significant))
                far_noise_count[entry_index, t_idx] = int(np.count_nonzero(far_noise))

                if not energy_only:
                    component_rows.extend(
                        _component_rows_for_entry(
                            corr=corr_valid,
                            significant_mask=significant & valid_mask,
                            radius_map=radius_map,
                            entry_index=entry_index,
                            tau=float(threshold),
                            far_field_radius=float(resolved_far_field_radius),
                            min_component_area=int(resolved_min_component_area),
                            connectivity=int(resolved_connectivity),
                            mask_id=survey.mask_ids[entry_index],
                            wavelength_nm=survey.wavelengths_nm[entry_index],
                            energy_center_xy=center,
                        )
                )

    manifest = PeakSupportAnalysisManifest(
        report_id=report_id or out_path.with_suffix("").name,
        source_survey_h5=str(source_path),
        frame_shape=survey.frame_shape,
        coordinate_frame=survey.coordinate_frame,
        camera_frame_extent=survey.camera_frame_extent,
        tau_values=tau,
        support_radii=radii,
        bg_policy={
            "method": "percentile",
            "percentile": float(resolved_bg_percentile),
            "domain": "valid_pixels",
        },
        corr_policy={
            "formula": "corr = max(psf - bg, 0)",
            "negative_values": "clipped_to_zero",
            "full_frame_apparent_cumulative_energy_is_not_support_selection": True,
            "p99_display_tail_normalization_is_visualization_only": True,
        },
        radial_policy={
            "center_policy": center_policy,
            "far_field_radius": float(resolved_far_field_radius),
            "support_radii": radii,
            **_center_profile_radial_policy(center_profile_obj),
        },
        component_policy={
            "analysis_mode": "energy_only" if energy_only else "component_table",
            "component_table_written": not bool(energy_only),
            "threshold_source": "corr >= tau",
            "min_component_area": int(resolved_min_component_area),
            "connectivity": int(resolved_connectivity),
            "preset_name": preset_name,
            "frame_read_policy": "hdf5_entry_streaming",
        },
        entry_mask_ids=survey.mask_ids,
        entry_wavelengths_nm=survey.wavelengths_nm,
        notes=notes,
    )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    _write_report_h5(
        out_path,
        manifest=manifest,
        source_survey_h5=str(source_path),
        tau_values=tau,
        support_radii=radii,
        background=background,
        center_xy=center_xy,
        total_corr_energy=total_corr_energy,
        compact_energy=compact_energy,
        compact_fraction=compact_fraction,
        far_noise_energy=far_noise_energy,
        far_sig_energy=far_sig_energy,
        far_noise_count=far_noise_count,
        far_sig_count=far_sig_count,
        component_rows=component_rows,
        write_components=not bool(energy_only),
    )
    return manifest


def _open_survey_frame_source(
    f: h5py.File,
) -> tuple[h5py.Dataset, SurveyMetadata]:
    try:
        source = open_full_frame_survey_source(f, getattr(f, "filename", ""))
    except ArtifactIOError as exc:
        raise DiffractionSupportAnalysisError(
            "PeakSupportAnalysisReport requires FullFramePSFSurvey; "
            "convert raw capture to survey first"
        ) from exc
    descriptor = source.descriptor
    return source.dataset, SurveyMetadata(
        mask_ids=list(descriptor.mask_ids),
        wavelengths_nm=[float(v) for v in descriptor.wavelengths_nm],
        frame_shape=descriptor.frame_shape,
        coordinate_frame=descriptor.coordinate_frame,
        camera_frame_extent=descriptor.camera_frame_extent_dict(),
        frame_count=descriptor.frame_count,
        frame_dataset_path=descriptor.dataset_path,
    )


def _frame_dataset_count_and_shape(frames: h5py.Dataset) -> tuple[int, tuple[int, int]]:
    try:
        return _shared_frame_dataset_count_and_shape(frames)
    except ArtifactIOError as exc:
        raise DiffractionSupportAnalysisError(str(exc)) from exc


def _read_frame_entry(frames: h5py.Dataset, entry_index: int) -> np.ndarray:
    try:
        return _shared_read_frame_entry(frames, entry_index)
    except ArtifactIOError as exc:
        raise DiffractionSupportAnalysisError(str(exc)) from exc


def propose_peak_supports_from_report(
    report_h5: str | Path,
    *,
    tau: float,
    padding: int = 8,
    snap_sizes: tuple[int, ...] = (64, 128, 256),
    merge_overlapping: bool = True,
    far_field_only: bool = False,
) -> list[dict[str, Any]]:
    """
    Diagnostic support-candidate proposal helper.

    This is not a stability report and not a layout promotion path. A future
    SupportCandidateStabilityReport task should own cross-entry candidate
    aggregation and promotion semantics.
    """

    rows = _read_component_rows(report_h5)
    frame_h, frame_w = _read_report_frame_shape(report_h5)
    selected = [
        row for row in rows
        if np.isclose(float(row["tau"]), float(tau), rtol=0.0, atol=1e-12)
        and (not far_field_only or bool(row["is_far_field"]))
    ]
    boxes: list[dict[str, Any]] = []
    for row in selected:
        x0, y0, x1, y1 = [int(v) for v in row["bbox_xyxy"]]
        boxes.append(
            {
                "source_component_ids": [int(row["component_id"])],
                "source_component_keys": [_component_key(row)],
                "entry_indices": [int(row["entry_index"])],
                "bbox_xyxy": _clip_box_to_frame(
                    [x0 - padding, y0 - padding, x1 + padding, y1 + padding],
                    frame_shape_hw=(frame_h, frame_w),
                ),
                "energy_score": float(row["energy"]),
                "area_cost": 0.0,
                "support_tau": float(tau),
            }
        )
    if merge_overlapping:
        boxes = _merge_overlapping_boxes(boxes)
    candidates: list[dict[str, Any]] = []
    for idx, box in enumerate(boxes):
        x0, y0, x1, y1 = box["bbox_xyxy"]
        width = max(1, int(x1) - int(x0))
        height = max(1, int(y1) - int(y0))
        raw_patch_h = _snap_size(height, snap_sizes)
        raw_patch_w = _snap_size(width, snap_sizes)
        patch_h = min(int(raw_patch_h), int(frame_h))
        patch_w = min(int(raw_patch_w), int(frame_w))
        cx = (float(x0) + float(x1)) / 2.0
        cy = (float(y0) + float(y1)) / 2.0
        origin_x = _clamp_int(int(round(cx - patch_w / 2.0)), 0, max(0, frame_w - patch_w))
        origin_y = _clamp_int(int(round(cy - patch_h / 2.0)), 0, max(0, frame_h - patch_h))
        candidates.append(
            {
                "candidate_id": f"support_{idx:04d}",
                "artifact_type": "candidate_support",
                "not_a_peak_layout_profile": True,
                "source_component_ids": box["source_component_ids"],
                "source_component_keys": [_component_key_to_dict(key) for key in box["source_component_keys"]],
                "bbox_xyxy": [int(x0), int(y0), int(x1), int(y1)],
                "patch_origin_xy": [origin_x, origin_y],
                "patch_shape_hw": [int(patch_h), int(patch_w)],
                "patch_clipped_to_frame": bool(raw_patch_h > frame_h or raw_patch_w > frame_w),
                "support_tau": float(tau),
                "support_radius_class": "far_field" if far_field_only else "all_components",
                "stability_score": None,
                "energy_score": float(box["energy_score"]),
                "area_cost": float(patch_h * patch_w),
            }
        )
    return candidates


def _validate_parameters(
    *,
    tau_values: list[float],
    support_radii: list[float],
    far_field_radius: float,
    bg_percentile: float,
    min_component_area: int,
    connectivity: int,
    center_policy: str,
) -> None:
    if not tau_values or any(float(x) < 0.0 for x in tau_values):
        raise DiffractionSupportAnalysisError("tau_values must be non-empty and non-negative")
    if not support_radii or any(float(x) <= 0.0 for x in support_radii):
        raise DiffractionSupportAnalysisError("support_radii must be non-empty and positive")
    if float(far_field_radius) <= 0.0:
        raise DiffractionSupportAnalysisError("far_field_radius must be positive")
    if not (0.0 <= float(bg_percentile) <= 100.0):
        raise DiffractionSupportAnalysisError("bg_percentile must be in [0, 100]")
    if int(min_component_area) <= 0:
        raise DiffractionSupportAnalysisError("min_component_area must be > 0")
    if int(connectivity) not in (4, 8):
        raise DiffractionSupportAnalysisError("connectivity must be 4 or 8")
    if center_policy not in {
        "frame_center",
        "manual_xy",
        "brightest_component",
        "sensor_energy_center_profile",
    }:
        raise DiffractionSupportAnalysisError("unsupported center_policy")


def _load_center_profile(
    center_profile: str | Path | SensorEnergyCenterProfile | None,
) -> SensorEnergyCenterProfile | None:
    if center_profile is None:
        return None
    if isinstance(center_profile, SensorEnergyCenterProfile):
        return center_profile
    try:
        return SensorEnergyCenterProfile.load_json(center_profile)
    except SensorEnergyCenterError as exc:
        raise DiffractionSupportAnalysisError(str(exc)) from exc


def _center_profile_radial_policy(
    center_profile: SensorEnergyCenterProfile | None,
) -> dict[str, Any]:
    if center_profile is None:
        return {}
    return {
        "center_profile_id": center_profile.center_profile_id,
        "center_xy": [float(center_profile.center_xy[0]), float(center_profile.center_xy[1])],
        "center_profile_coordinate_frame": center_profile.coordinate_frame,
        "global_center_std_xy": [
            float(center_profile.global_center_std_xy[0]),
            float(center_profile.global_center_std_xy[1]),
        ],
        "max_center_deviation_px": float(center_profile.max_center_deviation_px),
    }


def _support_analysis_preset(preset_name: str | None) -> dict[str, Any]:
    if preset_name is None:
        return {}
    if preset_name not in SUPPORT_ANALYSIS_PRESETS:
        names = ", ".join(sorted(SUPPORT_ANALYSIS_PRESETS))
        raise DiffractionSupportAnalysisError(f"unknown support analysis preset '{preset_name}'; available: {names}")
    return dict(SUPPORT_ANALYSIS_PRESETS[preset_name])


def _component_rows_for_entry(
    *,
    corr: np.ndarray,
    significant_mask: np.ndarray,
    radius_map: np.ndarray,
    entry_index: int,
    tau: float,
    far_field_radius: float,
    min_component_area: int,
    connectivity: int,
    mask_id: str,
    wavelength_nm: float,
    energy_center_xy: tuple[float, float],
) -> list[dict[str, Any]]:
    labels = _connected_components(significant_mask, connectivity=connectivity)
    rows: list[dict[str, Any]] = []
    for component_id, coords in labels:
        if coords.shape[0] < min_component_area:
            continue
        yy = coords[:, 0]
        xx = coords[:, 1]
        values = corr[yy, xx]
        radii = radius_map[yy, xx]
        energy = float(np.sum(values))
        area = int(coords.shape[0])
        centroid = [
            float(np.sum(xx * values) / energy) if energy > 0 else float(np.mean(xx)),
            float(np.sum(yy * values) / energy) if energy > 0 else float(np.mean(yy)),
        ]
        rows.append(
            {
                "entry_index": int(entry_index),
                "tau": float(tau),
                "component_id": int(component_id),
                "bbox_xyxy": [
                    int(np.min(xx)),
                    int(np.min(yy)),
                    int(np.max(xx)) + 1,
                    int(np.max(yy)) + 1,
                ],
                "centroid_xy": centroid,
                "centroid_xy_abs": centroid,
                "centroid_xy_rel": [
                    float(centroid[0]) - float(energy_center_xy[0]),
                    float(centroid[1]) - float(energy_center_xy[1]),
                ],
                "area": area,
                "energy": energy,
                "peak_value": float(np.max(values)) if values.size else 0.0,
                "mean_value": float(np.mean(values)) if values.size else 0.0,
                "max_radius": float(np.max(radii)) if radii.size else 0.0,
                "max_radius_from_energy_center": float(np.max(radii)) if radii.size else 0.0,
                "is_far_field": bool(np.max(radii) >= far_field_radius) if radii.size else False,
                "mask_id": str(mask_id),
                "wavelength_nm": float(wavelength_nm),
            }
        )
    return rows


def _connected_components(mask: np.ndarray, *, connectivity: int) -> list[tuple[int, np.ndarray]]:
    if _scipy_ndimage is not None:
        return _connected_components_scipy(mask, connectivity=connectivity)
    return _connected_components_python(mask, connectivity=connectivity)


def _connected_components_scipy(mask: np.ndarray, *, connectivity: int) -> list[tuple[int, np.ndarray]]:
    arr = np.asarray(mask, dtype=bool)
    if connectivity == 4:
        structure = np.array([[0, 1, 0], [1, 1, 1], [0, 1, 0]], dtype=bool)
    else:
        structure = np.ones((3, 3), dtype=bool)
    labeled, count = _scipy_ndimage.label(arr, structure=structure)
    objects = _scipy_ndimage.find_objects(labeled)
    components: list[tuple[int, np.ndarray]] = []
    for label_id in range(1, int(count) + 1):
        obj = objects[label_id - 1]
        if obj is None:
            continue
        local = labeled[obj] == label_id
        local_coords = np.argwhere(local)
        y0 = int(obj[0].start)
        x0 = int(obj[1].start)
        local_coords[:, 0] += y0
        local_coords[:, 1] += x0
        components.append((label_id - 1, local_coords.astype(np.int64, copy=False)))
    return components


def _connected_components_python(mask: np.ndarray, *, connectivity: int) -> list[tuple[int, np.ndarray]]:
    arr = np.asarray(mask, dtype=bool)
    visited = np.zeros(arr.shape, dtype=bool)
    h, w = arr.shape
    if connectivity == 4:
        neighbors = [(-1, 0), (0, -1), (0, 1), (1, 0)]
    else:
        neighbors = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]
    components: list[tuple[int, np.ndarray]] = []
    starts = np.argwhere(arr & ~visited)
    component_id = 0
    for start_y, start_x in starts:
        if visited[start_y, start_x] or not arr[start_y, start_x]:
            continue
        q: list[tuple[int, int]] = [(int(start_y), int(start_x))]
        visited[start_y, start_x] = True
        coords: list[tuple[int, int]] = []
        while q:
            y, x = q.pop()
            coords.append((y, x))
            for dy, dx in neighbors:
                ny = y + dy
                nx = x + dx
                if 0 <= ny < h and 0 <= nx < w and arr[ny, nx] and not visited[ny, nx]:
                    visited[ny, nx] = True
                    q.append((ny, nx))
        components.append((component_id, np.asarray(coords, dtype=np.int64)))
        component_id += 1
    return components


def _write_report_h5(
    path: Path,
    *,
    manifest: PeakSupportAnalysisManifest,
    source_survey_h5: str,
    tau_values: list[float],
    support_radii: list[float],
    background: np.ndarray,
    center_xy: np.ndarray,
    total_corr_energy: np.ndarray,
    compact_energy: np.ndarray,
    compact_fraction: np.ndarray,
    far_noise_energy: np.ndarray,
    far_sig_energy: np.ndarray,
    far_noise_count: np.ndarray,
    far_sig_count: np.ndarray,
    component_rows: list[dict[str, Any]],
    write_components: bool = True,
) -> None:
    string_dtype = h5py.string_dtype(encoding="utf-8")
    with h5py.File(str(path), "w") as f:
        support = f.require_group("support_analysis")
        support.create_dataset("tau_values", data=np.asarray(tau_values, dtype=np.float64))
        support.create_dataset("support_radii", data=np.asarray(support_radii, dtype=np.float64))
        support.create_dataset("frame_shape", data=np.asarray(manifest.frame_shape, dtype=np.int64))
        support.create_dataset("background_value", data=background)
        support.create_dataset("center_xy", data=center_xy)
        support.create_dataset("total_corr_energy", data=total_corr_energy)
        support.create_dataset("compact_support_energy", data=compact_energy)
        support.create_dataset("compact_support_fraction", data=compact_fraction)
        support.create_dataset("far_field_noise_energy", data=far_noise_energy)
        support.create_dataset("far_field_significant_energy", data=far_sig_energy)
        support.create_dataset("far_field_noise_pixel_count", data=far_noise_count)
        support.create_dataset("far_field_significant_pixel_count", data=far_sig_count)

        if write_components:
            comp = f.require_group("components")
            n_comp = len(component_rows)
            comp.create_dataset(
                "entry_index",
                data=np.asarray([r["entry_index"] for r in component_rows], dtype=np.int64),
            )
            comp.create_dataset("tau", data=np.asarray([r["tau"] for r in component_rows], dtype=np.float64))
            comp.create_dataset(
                "component_id",
                data=np.asarray([r["component_id"] for r in component_rows], dtype=np.int64),
            )
            comp.create_dataset(
                "bbox_xyxy",
                data=np.asarray([r["bbox_xyxy"] for r in component_rows], dtype=np.int64).reshape((n_comp, 4)),
            )
            comp.create_dataset(
                "centroid_xy",
                data=np.asarray([r["centroid_xy"] for r in component_rows], dtype=np.float64).reshape((n_comp, 2)),
            )
            comp.create_dataset(
                "centroid_xy_abs",
                data=np.asarray([r["centroid_xy_abs"] for r in component_rows], dtype=np.float64).reshape((n_comp, 2)),
            )
            comp.create_dataset(
                "centroid_xy_rel",
                data=np.asarray([r["centroid_xy_rel"] for r in component_rows], dtype=np.float64).reshape((n_comp, 2)),
            )
            comp.create_dataset("area", data=np.asarray([r["area"] for r in component_rows], dtype=np.int64))
            comp.create_dataset("energy", data=np.asarray([r["energy"] for r in component_rows], dtype=np.float64))
            comp.create_dataset(
                "peak_value",
                data=np.asarray([r["peak_value"] for r in component_rows], dtype=np.float64),
            )
            comp.create_dataset(
                "mean_value",
                data=np.asarray([r["mean_value"] for r in component_rows], dtype=np.float64),
            )
            comp.create_dataset(
                "max_radius",
                data=np.asarray([r["max_radius"] for r in component_rows], dtype=np.float64),
            )
            comp.create_dataset(
                "max_radius_from_energy_center",
                data=np.asarray([r["max_radius_from_energy_center"] for r in component_rows], dtype=np.float64),
            )
            comp.create_dataset(
                "is_far_field",
                data=np.asarray([r["is_far_field"] for r in component_rows], dtype=np.bool_),
            )
            comp.create_dataset(
                "mask_id",
                data=np.asarray([r["mask_id"] for r in component_rows], dtype=object),
                dtype=string_dtype,
            )
            comp.create_dataset(
                "wavelength_nm",
                data=np.asarray([r["wavelength_nm"] for r in component_rows], dtype=np.float64),
            )

        metadata = f.require_group("metadata")
        metadata.create_dataset("manifest_json", data=manifest.to_json_text(), dtype=string_dtype)
        source = f.require_group("source")
        source.create_dataset("survey_h5", data=str(source_survey_h5), dtype=string_dtype)


def _read_component_rows(report_h5: str | Path) -> list[dict[str, Any]]:
    with h5py.File(str(report_h5), "r") as f:
        if "components" not in f:
            raise DiffractionSupportAnalysisError(
                "support report has no component table; energy-only reports cannot propose peak supports"
            )
        comp = f["components"]
        n = int(comp["entry_index"].shape[0])
        rows: list[dict[str, Any]] = []
        for i in range(n):
            centroid_xy = [float(v) for v in comp["centroid_xy"][i]]
            rows.append(
                {
                    "entry_index": int(comp["entry_index"][i]),
                    "tau": float(comp["tau"][i]),
                    "component_id": int(comp["component_id"][i]),
                    "bbox_xyxy": [int(v) for v in comp["bbox_xyxy"][i]],
                    "centroid_xy": centroid_xy,
                    "centroid_xy_abs": (
                        [float(v) for v in comp["centroid_xy_abs"][i]]
                        if "centroid_xy_abs" in comp else centroid_xy
                    ),
                    "centroid_xy_rel": (
                        [float(v) for v in comp["centroid_xy_rel"][i]]
                        if "centroid_xy_rel" in comp else [0.0, 0.0]
                    ),
                    "area": int(comp["area"][i]),
                    "energy": float(comp["energy"][i]),
                    "peak_value": float(comp["peak_value"][i]),
                    "mean_value": float(comp["mean_value"][i]),
                    "max_radius": float(comp["max_radius"][i]),
                    "max_radius_from_energy_center": (
                        float(comp["max_radius_from_energy_center"][i])
                        if "max_radius_from_energy_center" in comp
                        else float(comp["max_radius"][i])
                    ),
                    "is_far_field": bool(comp["is_far_field"][i]),
                    "mask_id": _decode(comp["mask_id"][i]),
                    "wavelength_nm": float(comp["wavelength_nm"][i]),
                }
            )
    return rows


def _read_report_frame_shape(report_h5: str | Path) -> tuple[int, int]:
    with h5py.File(str(report_h5), "r") as f:
        if "support_analysis/frame_shape" in f:
            return _int_pair(f["support_analysis/frame_shape"][()].tolist(), "support_analysis/frame_shape")
        if "metadata/manifest_json" in f:
            manifest = json.loads(_decode(f["metadata/manifest_json"][()]))
            return _int_pair(manifest["frame_shape"], "metadata/manifest_json.frame_shape")
    raise DiffractionSupportAnalysisError("support report is missing frame_shape")


def _normalize_frames(frames: np.ndarray) -> np.ndarray:
    arr = np.asarray(frames, dtype=np.float64)
    if arr.ndim == 2:
        return arr[np.newaxis, :, :]
    if arr.ndim == 3:
        return arr
    raise DiffractionSupportAnalysisError(f"survey frames must be 2D or 3D, got {arr.shape}")


def _valid_pixel_mask(shape: tuple[int, int], valid_pixel_domain: dict[str, Any] | None) -> np.ndarray:
    mask = np.ones((int(shape[0]), int(shape[1])), dtype=bool)
    if not valid_pixel_domain:
        return mask
    policy_type = str(valid_pixel_domain.get("type") or "full_frame")
    if policy_type == "full_frame":
        return mask
    if policy_type == "exclude_top_rows":
        top_rows = int(valid_pixel_domain.get("top_rows", 0))
        if top_rows > 0:
            mask[:top_rows, :] = False
        return mask
    raise DiffractionSupportAnalysisError(f"unsupported valid_pixel_domain.type: {policy_type}")


def _resolve_center_xy(
    corr: np.ndarray,
    valid_mask: np.ndarray,
    center_policy: str,
    manual_center_xy: tuple[float, float] | None,
    center_profile: SensorEnergyCenterProfile | None = None,
) -> tuple[float, float]:
    h, w = corr.shape
    if center_policy == "frame_center":
        return ((float(w) - 1.0) / 2.0, (float(h) - 1.0) / 2.0)
    if center_policy == "manual_xy":
        if manual_center_xy is None:
            raise DiffractionSupportAnalysisError("manual_center_xy is required for center_policy='manual_xy'")
        return (float(manual_center_xy[0]), float(manual_center_xy[1]))
    if center_policy == "brightest_component":
        peak_eval = np.where(valid_mask, corr, -np.inf)
        y, x = np.unravel_index(int(np.argmax(peak_eval)), peak_eval.shape)
        return (float(x), float(y))
    if center_policy == "sensor_energy_center_profile":
        if center_profile is None:
            raise DiffractionSupportAnalysisError(
                "center_profile is required for center_policy='sensor_energy_center_profile'"
            )
        return (float(center_profile.center_xy[0]), float(center_profile.center_xy[1]))
    raise DiffractionSupportAnalysisError(f"unsupported center_policy: {center_policy}")


def _radius_map(shape: tuple[int, int], center_xy: tuple[float, float]) -> np.ndarray:
    h, w = int(shape[0]), int(shape[1])
    cx, cy = float(center_xy[0]), float(center_xy[1])
    yy, xx = np.ogrid[:h, :w]
    return np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2)


def _merge_overlapping_boxes(boxes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    for box in boxes:
        current = dict(box)
        changed = True
        while changed:
            changed = False
            keep: list[dict[str, Any]] = []
            for other in merged:
                if _boxes_overlap(current["bbox_xyxy"], other["bbox_xyxy"]):
                    current["bbox_xyxy"] = _union_box(current["bbox_xyxy"], other["bbox_xyxy"])
                    current["source_component_ids"] = sorted(
                        set(current["source_component_ids"] + other["source_component_ids"])
                    )
                    current["source_component_keys"] = sorted(
                        set(current["source_component_keys"] + other["source_component_keys"])
                    )
                    current["entry_indices"] = sorted(set(current["entry_indices"] + other["entry_indices"]))
                    current["energy_score"] = float(current["energy_score"]) + float(other["energy_score"])
                    changed = True
                else:
                    keep.append(other)
            merged = keep
        merged.append(current)
    return merged


def _boxes_overlap(a: list[int], b: list[int]) -> bool:
    return not (a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1])


def _union_box(a: list[int], b: list[int]) -> list[int]:
    return [min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3])]


def _clip_box_to_frame(box: list[int], *, frame_shape_hw: tuple[int, int]) -> list[int]:
    h, w = int(frame_shape_hw[0]), int(frame_shape_hw[1])
    x0, y0, x1, y1 = [int(v) for v in box]
    clipped = [
        _clamp_int(x0, 0, w),
        _clamp_int(y0, 0, h),
        _clamp_int(x1, 0, w),
        _clamp_int(y1, 0, h),
    ]
    if clipped[2] <= clipped[0]:
        clipped[2] = min(w, clipped[0] + 1)
    if clipped[3] <= clipped[1]:
        clipped[3] = min(h, clipped[1] + 1)
    return clipped


def _clamp_int(value: int, low: int, high: int) -> int:
    return max(int(low), min(int(high), int(value)))


def _component_key(row: dict[str, Any]) -> tuple[int, float, int]:
    return (int(row["entry_index"]), float(row["tau"]), int(row["component_id"]))


def _component_key_to_dict(key: tuple[int, float, int]) -> dict[str, Any]:
    return {
        "entry_index": int(key[0]),
        "tau": float(key[1]),
        "component_id": int(key[2]),
    }


def _snap_size(value: int, snap_sizes: tuple[int, ...]) -> int:
    for size in sorted(int(x) for x in snap_sizes):
        if value <= size:
            return size
    return int(max(snap_sizes))


def _decode(value: Any) -> str:
    return decode_h5_string(value)


def _int_pair(value: Any, name: str) -> tuple[int, int]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise DiffractionSupportAnalysisError(f"{name} must be a pair")
    return (int(value[0]), int(value[1]))
