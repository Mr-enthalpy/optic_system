from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Iterator

import numpy as np


SUPPORTED_SCAN_MODES = {"bars_x", "bars_y", "blocks", "apertures"}


class PupilScanMaskError(ValueError):
    pass


@dataclass(frozen=True)
class ScanMaskSpec:
    physical_shape: tuple[int, int]
    subpixel_axis: int
    scan_modes: list[str]
    active_code: int = 255
    background_code: int = 0
    bar_count: int = 40
    bar_width: int | None = None
    block_rows: int = 20
    block_cols: int = 20
    aperture_grid_rows: int = 10
    aperture_grid_cols: int = 10
    aperture_radius: int | None = None
    include_baselines: bool = True
    _validated_modes: tuple[str, ...] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        h, w = _validate_shape(self.physical_shape)
        object.__setattr__(self, "physical_shape", (h, w))

        if self.subpixel_axis not in (0, 1):
            raise PupilScanMaskError(
                f"subpixel_axis must be 0 or 1, got {self.subpixel_axis}"
            )
        if self.subpixel_axis == 0 and h % 3 != 0:
            raise PupilScanMaskError(
                "subpixel_axis=0 requires physical_shape[0] divisible by 3"
            )
        if self.subpixel_axis == 1 and w % 3 != 0:
            raise PupilScanMaskError(
                "subpixel_axis=1 requires physical_shape[1] divisible by 3"
            )

        modes = tuple(self.scan_modes)
        if not modes:
            raise PupilScanMaskError("scan_modes must be non-empty")
        unknown = [m for m in modes if m not in SUPPORTED_SCAN_MODES]
        if unknown:
            raise PupilScanMaskError(f"unsupported scan mode(s): {unknown}")
        object.__setattr__(self, "_validated_modes", modes)

        for name in ("active_code", "background_code"):
            value = int(getattr(self, name))
            if value < 0 or value > 255:
                raise PupilScanMaskError(f"{name} must be in [0, 255], got {value}")

        for name in (
            "bar_count",
            "block_rows",
            "block_cols",
            "aperture_grid_rows",
            "aperture_grid_cols",
        ):
            value = int(getattr(self, name))
            if value <= 0:
                raise PupilScanMaskError(f"{name} must be positive, got {value}")

        if self.bar_width is not None and int(self.bar_width) <= 0:
            raise PupilScanMaskError("bar_width must be positive when set")
        if self.aperture_radius is not None and int(self.aperture_radius) <= 0:
            raise PupilScanMaskError("aperture_radius must be positive when set")


def iter_pupil_scan_masks(
    spec: ScanMaskSpec,
) -> Iterator[tuple[str, np.ndarray, dict[str, Any]]]:
    """
    Yield procedural pupil-scan masks.

    Each mask is a physical mono LCD array with shape [H_phys, W_phys].
    The metadata is sufficient to reproduce the same mask and includes a
    SHA-256 hash of the generated array bytes.
    """

    if spec.include_baselines:
        yield _baseline_mask(spec, "baseline_all_open", spec.active_code)
        yield _baseline_mask(spec, "baseline_all_closed", spec.background_code)

    for mode in spec._validated_modes:
        if mode == "bars_x":
            yield from _iter_bars_x(spec)
        elif mode == "bars_y":
            yield from _iter_bars_y(spec)
        elif mode == "blocks":
            yield from _iter_blocks(spec)
        elif mode == "apertures":
            yield from _iter_apertures(spec)
        else:  # pragma: no cover - guarded by ScanMaskSpec validation.
            raise PupilScanMaskError(f"unsupported scan mode: {mode}")


def _baseline_mask(
    spec: ScanMaskSpec,
    mask_id: str,
    fill_value: int,
) -> tuple[str, np.ndarray, dict[str, Any]]:
    mask = np.full(spec.physical_shape, int(fill_value), dtype=np.uint8)
    meta = _base_metadata(spec, mode="baseline")
    meta.update({"baseline": mask_id, "fill_value": int(fill_value)})
    return _finish(mask_id, mask, meta)


def _iter_bars_x(spec: ScanMaskSpec) -> Iterator[tuple[str, np.ndarray, dict[str, Any]]]:
    h, w = spec.physical_shape
    width = _bar_width(spec, axis="x")
    for idx, (x_min, x_max) in enumerate(_scan_intervals(w, spec.bar_count, width, align=3 if spec.subpixel_axis == 1 else 1)):
        mask = _blank(spec)
        mask[:, x_min:x_max] = spec.active_code
        meta = _base_metadata(spec, mode="bars_x")
        meta.update(
            {
                "bar_index": idx,
                "x_min": int(x_min),
                "x_max": int(x_max),
                "y_min": 0,
                "y_max": h,
                "center_x": (x_min + x_max - 1) / 2.0,
                "center_y": (h - 1) / 2.0,
            }
        )
        yield _finish(f"bars_x_{idx:04d}", mask, meta)


def _iter_bars_y(spec: ScanMaskSpec) -> Iterator[tuple[str, np.ndarray, dict[str, Any]]]:
    h, w = spec.physical_shape
    width = _bar_width(spec, axis="y")
    for idx, (y_min, y_max) in enumerate(_scan_intervals(h, spec.bar_count, width, align=3 if spec.subpixel_axis == 0 else 1)):
        mask = _blank(spec)
        mask[y_min:y_max, :] = spec.active_code
        meta = _base_metadata(spec, mode="bars_y")
        meta.update(
            {
                "bar_index": idx,
                "x_min": 0,
                "x_max": w,
                "y_min": int(y_min),
                "y_max": int(y_max),
                "center_x": (w - 1) / 2.0,
                "center_y": (y_min + y_max - 1) / 2.0,
            }
        )
        yield _finish(f"bars_y_{idx:04d}", mask, meta)


def _iter_blocks(spec: ScanMaskSpec) -> Iterator[tuple[str, np.ndarray, dict[str, Any]]]:
    h, w = spec.physical_shape
    y_edges = _grid_edges(h, spec.block_rows, align=3 if spec.subpixel_axis == 0 else 1)
    x_edges = _grid_edges(w, spec.block_cols, align=3 if spec.subpixel_axis == 1 else 1)
    for row in range(len(y_edges) - 1):
        y_min, y_max = y_edges[row], y_edges[row + 1]
        for col in range(len(x_edges) - 1):
            x_min, x_max = x_edges[col], x_edges[col + 1]
            if y_max <= y_min or x_max <= x_min:
                continue
            mask = _blank(spec)
            mask[y_min:y_max, x_min:x_max] = spec.active_code
            meta = _base_metadata(spec, mode="blocks")
            meta.update(
                {
                    "row": int(row),
                    "col": int(col),
                    "x_min": int(x_min),
                    "x_max": int(x_max),
                    "y_min": int(y_min),
                    "y_max": int(y_max),
                    "center_x": (x_min + x_max - 1) / 2.0,
                    "center_y": (y_min + y_max - 1) / 2.0,
                }
            )
            yield _finish(f"block_r{row:03d}_c{col:03d}", mask, meta)


def _iter_apertures(spec: ScanMaskSpec) -> Iterator[tuple[str, np.ndarray, dict[str, Any]]]:
    h, w = spec.physical_shape
    radius = spec.aperture_radius
    if radius is None:
        radius = max(1, min(h // max(spec.aperture_grid_rows, 1), w // max(spec.aperture_grid_cols, 1)) // 4)
    y_centers = _cell_centers(h, spec.aperture_grid_rows, align=3 if spec.subpixel_axis == 0 else 1)
    x_centers = _cell_centers(w, spec.aperture_grid_cols, align=3 if spec.subpixel_axis == 1 else 1)

    yy, xx = np.ogrid[:h, :w]
    for row, cy in enumerate(y_centers):
        for col, cx in enumerate(x_centers):
            mask = _blank(spec)
            disk = (yy - cy) ** 2 + (xx - cx) ** 2 <= float(radius) ** 2
            mask[disk] = spec.active_code
            meta = _base_metadata(spec, mode="apertures")
            meta.update(
                {
                    "row": int(row),
                    "col": int(col),
                    "center_x": float(cx),
                    "center_y": float(cy),
                    "radius": int(radius),
                    "x_min": int(max(0, np.floor(cx - radius))),
                    "x_max": int(min(w, np.ceil(cx + radius + 1))),
                    "y_min": int(max(0, np.floor(cy - radius))),
                    "y_max": int(min(h, np.ceil(cy + radius + 1))),
                }
            )
            yield _finish(f"aperture_r{row:03d}_c{col:03d}", mask, meta)


def _base_metadata(spec: ScanMaskSpec, *, mode: str) -> dict[str, Any]:
    return {
        "mode": mode,
        "physical_shape": [int(spec.physical_shape[0]), int(spec.physical_shape[1])],
        "subpixel_axis": int(spec.subpixel_axis),
        "active_code": int(spec.active_code),
        "background_code": int(spec.background_code),
        "scan_modes": list(spec._validated_modes),
        "bar_count": int(spec.bar_count),
        "bar_width": None if spec.bar_width is None else int(spec.bar_width),
        "block_rows": int(spec.block_rows),
        "block_cols": int(spec.block_cols),
        "aperture_grid_rows": int(spec.aperture_grid_rows),
        "aperture_grid_cols": int(spec.aperture_grid_cols),
        "aperture_radius": None if spec.aperture_radius is None else int(spec.aperture_radius),
        "generator": "tasks.pupil_scan_masks.iter_pupil_scan_masks",
        "recipe_version": "1.0",
    }


def _finish(
    mask_id: str,
    mask: np.ndarray,
    metadata: dict[str, Any],
) -> tuple[str, np.ndarray, dict[str, Any]]:
    digest = mask_hash(mask)
    metadata = dict(metadata)
    metadata["mask_id"] = mask_id
    metadata["mask_hash"] = digest
    metadata["mask_recipe_json"] = json.dumps(
        {k: v for k, v in metadata.items() if k != "mask_hash"},
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return mask_id, mask, metadata


def mask_hash(mask: np.ndarray) -> str:
    arr = np.asarray(mask, dtype=np.uint8)
    h = hashlib.sha256()
    h.update(str(arr.shape).encode("ascii"))
    h.update(str(arr.dtype).encode("ascii"))
    h.update(arr.tobytes(order="C"))
    return h.hexdigest()


def _blank(spec: ScanMaskSpec) -> np.ndarray:
    return np.full(spec.physical_shape, int(spec.background_code), dtype=np.uint8)


def _validate_shape(shape: tuple[int, int]) -> tuple[int, int]:
    if len(shape) != 2:
        raise PupilScanMaskError(f"physical_shape must be length 2, got {shape}")
    h, w = int(shape[0]), int(shape[1])
    if h <= 0 or w <= 0:
        raise PupilScanMaskError(f"physical_shape must be positive, got {shape}")
    return h, w


def _bar_width(spec: ScanMaskSpec, *, axis: str) -> int:
    h, w = spec.physical_shape
    length = w if axis == "x" else h
    align = 3 if (axis == "x" and spec.subpixel_axis == 1) or (axis == "y" and spec.subpixel_axis == 0) else 1
    raw = int(spec.bar_width) if spec.bar_width is not None else max(1, int(np.ceil(length / spec.bar_count)))
    return min(length, _ceil_to_multiple(raw, align))


def _scan_intervals(
    length: int,
    count: int,
    width: int,
    *,
    align: int = 1,
) -> list[tuple[int, int]]:
    if count == 1 or width >= length:
        return [(0, length)]
    max_start = max(0, length - width)
    starts = np.linspace(0, max_start, count)
    intervals: list[tuple[int, int]] = []
    for start_f in starts:
        start = _floor_to_multiple(int(round(float(start_f))), align)
        start = min(max(0, start), max_start)
        end = min(length, start + width)
        if end <= start:
            end = min(length, start + 1)
        intervals.append((int(start), int(end)))
    return intervals


def _grid_edges(length: int, count: int, *, align: int = 1) -> list[int]:
    if count <= 0:
        raise PupilScanMaskError("grid count must be positive")
    if align == 3:
        logical_length = length // 3
        raw = np.linspace(0, logical_length, count + 1)
        edges = [int(round(float(v))) * 3 for v in raw]
    else:
        raw = np.linspace(0, length, count + 1)
        edges = [int(round(float(v))) for v in raw]
    edges[0] = 0
    edges[-1] = length
    for i in range(1, len(edges)):
        if edges[i] < edges[i - 1]:
            edges[i] = edges[i - 1]
    return edges


def _cell_centers(length: int, count: int, *, align: int = 1) -> list[float]:
    edges = _grid_edges(length, count, align=align)
    return [(edges[i] + edges[i + 1] - 1) / 2.0 for i in range(len(edges) - 1)]


def _ceil_to_multiple(value: int, multiple: int) -> int:
    if multiple <= 1:
        return int(value)
    return int(np.ceil(value / multiple) * multiple)


def _floor_to_multiple(value: int, multiple: int) -> int:
    if multiple <= 1:
        return int(value)
    return int(np.floor(value / multiple) * multiple)
