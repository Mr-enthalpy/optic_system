from __future__ import annotations

import math
import time
from pathlib import Path
from typing import Any

import h5py
import numpy as np

from tasks.psf_phase3 import center_of_mass, crop_frame, json_dumps, now_ns, processing_flags, pupil_window_mask


def dotf_reference_mask(
    physical_shape: tuple[int, int],
    pupil_window: dict[str, Any],
    *,
    subpixel_axis: int,
    bg_code: int = 0,
    open_code: int = 255,
) -> tuple[np.ndarray, dict[str, Any]]:
    mask, meta = pupil_window_mask(
        physical_shape,
        pupil_window,
        subpixel_axis=subpixel_axis,
        mask_id="dotf_reference",
        bg_code=bg_code,
        aperture_code=open_code,
    )
    meta.update(
        {
            "mask_type": "dotf_reference",
            "capture_role": "reference",
            "perturbation_id": "none",
            "inside_effective_pupil": "all_open",
            "outside_effective_pupil": "opaque",
        }
    )
    return mask, meta


def dotf_edge_perturbation_mask(
    physical_shape: tuple[int, int],
    pupil_window: dict[str, Any],
    *,
    subpixel_axis: int,
    side: str,
    block_size_px: int,
    offset_from_effective_radius_px: int = 0,
    bg_code: int = 0,
    open_code: int = 255,
    perturb_code: int = 0,
) -> tuple[np.ndarray, dict[str, Any]]:
    mask, base_meta = dotf_reference_mask(
        physical_shape,
        pupil_window,
        subpixel_axis=subpixel_axis,
        bg_code=bg_code,
        open_code=open_code,
    )
    if int(block_size_px) <= 0:
        raise ValueError("block_size_px must be > 0")
    side_key = _normalize_perturbation_side(side)
    cx = float(pupil_window["center"]["x"])
    cy = float(pupil_window["center"]["y"])
    radius = float(pupil_window["radius"])
    offset = int(offset_from_effective_radius_px)
    block = int(block_size_px)

    x_min, x_max, y_min, y_max = _edge_block_bounds(
        physical_shape,
        center=(cx, cy),
        radius=radius,
        side=side_key,
        block_size_px=block,
        offset_from_effective_radius_px=offset,
    )
    region = np.zeros(mask.shape, dtype=bool)
    region[y_min:y_max, x_min:x_max] = True
    inside = mask == int(open_code)
    applied = region & inside
    mask[applied] = int(perturb_code)
    meta = dict(base_meta)
    meta.update(
        {
            "mask_id": f"dotf_{side_key}",
            "mask_type": "dotf_edge_perturbation",
            "capture_role": "perturbed",
            "perturbation_id": f"edge_block_{side_key}",
            "perturbation": {
                "type": "local_edge_occlusion",
                "side": side_key,
                "block_size_px": block,
                "offset_from_effective_radius_px": offset,
                "perturb_code": int(perturb_code),
                "open_code": int(open_code),
                "background_code": int(bg_code),
            },
            "inside_effective_pupil": "all_open_except_local_edge_occlusion",
            "outside_effective_pupil": "opaque",
            "perturbation_bounds": {
                "x_min": int(x_min),
                "x_max": int(x_max),
                "y_min": int(y_min),
                "y_max": int(y_max),
                "applied_pixel_count": int(np.count_nonzero(applied)),
            },
        }
    )
    return mask, meta


class DotfRawWriter:
    def __init__(self, output_path: str | Path, *, plan_id: str, phase: str = "3.3"):
        self.path = Path(output_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.plan_id = str(plan_id)
        self.phase = str(phase)
        self._file: h5py.File | None = None
        self._n = 0

    def open(self) -> "DotfRawWriter":
        self._file = h5py.File(str(self.path), "w")
        string_dtype = h5py.string_dtype(encoding="utf-8")
        f = self._file
        f.attrs["plan_id"] = self.plan_id
        f.attrs["phase"] = self.phase
        f.attrs["created_at_ns"] = now_ns()
        raw = f.require_group("raw")
        raw.create_dataset(
            "frames_avg",
            shape=(0, 1, 1),
            maxshape=(None, None, None),
            dtype=np.float64,
            chunks=(1, 128, 128),
            compression="gzip",
            compression_opts=4,
        )
        raw.create_dataset(
            "crops",
            shape=(0, 1, 1),
            maxshape=(None, None, None),
            dtype=np.float64,
            chunks=(1, 64, 64),
            compression="gzip",
            compression_opts=4,
        )
        raw.create_dataset("mask_id", shape=(0,), maxshape=(None,), dtype=string_dtype)
        raw.create_dataset("repeat_index", shape=(0,), maxshape=(None,), dtype=np.int64)
        raw.create_dataset("capture_role", shape=(0,), maxshape=(None,), dtype=string_dtype)
        raw.create_dataset("perturbation_id", shape=(0,), maxshape=(None,), dtype=string_dtype)
        raw.create_dataset("timestamp_ns", shape=(0,), maxshape=(None,), dtype=np.int64)
        raw.create_dataset("mask_metadata_json", shape=(0,), maxshape=(None,), dtype=string_dtype)
        capture = f.require_group("capture")
        capture.create_dataset("plan_json", data="", dtype=string_dtype)
        capture.create_dataset("processing_flags_json", data=json_dumps(processing_flags(self.phase, False)), dtype=string_dtype)
        f.require_group("camera").create_dataset("metadata_json", data="", dtype=string_dtype)
        f.require_group("lcd").create_dataset("metadata_json", data="", dtype=string_dtype)
        f.require_group("tls").create_dataset("metadata_json", data="", dtype=string_dtype)
        provenance = f.require_group("provenance")
        provenance.create_dataset("pupil_window_source_json", data="", dtype=string_dtype)
        provenance.create_dataset("psf_roi_source_json", data="", dtype=string_dtype)
        provenance.create_dataset("camera_params_source_json", data="", dtype=string_dtype)
        return self

    def write_json_sections(
        self,
        *,
        plan: dict[str, Any],
        camera_metadata: dict[str, Any],
        lcd_metadata: dict[str, Any],
        tls_metadata: dict[str, Any],
        pupil_window_source: dict[str, Any],
        psf_roi_source: dict[str, Any],
        camera_params_source: dict[str, Any],
    ) -> None:
        f = self._ensure_open()
        f["capture/plan_json"][()] = json_dumps(plan)
        f["camera/metadata_json"][()] = json_dumps(camera_metadata)
        f["lcd/metadata_json"][()] = json_dumps(lcd_metadata)
        f["tls/metadata_json"][()] = json_dumps(tls_metadata)
        f["provenance/pupil_window_source_json"][()] = json_dumps(pupil_window_source)
        f["provenance/psf_roi_source_json"][()] = json_dumps(psf_roi_source)
        f["provenance/camera_params_source_json"][()] = json_dumps(camera_params_source)

    def append_capture(
        self,
        *,
        frame_avg: np.ndarray,
        crop: np.ndarray,
        mask_id: str,
        repeat_index: int,
        capture_role: str,
        perturbation_id: str,
        mask_metadata: dict[str, Any],
    ) -> int:
        f = self._ensure_open()
        row = self._n
        _append_frame(f["raw/frames_avg"], row, np.asarray(frame_avg, dtype=np.float64))
        _append_frame(f["raw/crops"], row, np.asarray(crop, dtype=np.float64))
        _append_scalar(f["raw/mask_id"], str(mask_id))
        _append_scalar(f["raw/repeat_index"], int(repeat_index))
        _append_scalar(f["raw/capture_role"], str(capture_role))
        _append_scalar(f["raw/perturbation_id"], str(perturbation_id))
        _append_scalar(f["raw/timestamp_ns"], int(time.time_ns()))
        _append_scalar(f["raw/mask_metadata_json"], json_dumps(mask_metadata))
        self._n += 1
        return row

    def finalize(self, *, completed: bool, error: str | None = None, analysis_valid: bool | None = None) -> None:
        if self._file is None:
            return
        flags = processing_flags(self.phase, completed)
        flags["error"] = error
        flags["n_captures_written"] = self._n
        if analysis_valid is not None:
            flags["analysis_valid"] = bool(analysis_valid)
        self._file["capture/processing_flags_json"][()] = json_dumps(flags)
        self._file.close()
        self._file = None

    def _ensure_open(self) -> h5py.File:
        if self._file is None:
            raise RuntimeError("HDF5 writer is not open")
        return self._file


def psf_to_otf(psf: np.ndarray) -> np.ndarray:
    arr = np.asarray(psf, dtype=np.float64)
    if arr.ndim != 2:
        raise ValueError(f"psf must be 2D, got {arr.shape}")
    return np.fft.fftshift(np.fft.fft2(np.fft.ifftshift(arr)))


def align_frame_to_reference(frame: np.ndarray, reference: np.ndarray) -> tuple[np.ndarray, dict[str, int]]:
    ref_x, ref_y = center_of_mass(reference)
    x, y = center_of_mass(frame)
    shift_x = int(round(ref_x - x))
    shift_y = int(round(ref_y - y))
    aligned = np.roll(np.asarray(frame, dtype=np.float64), shift=(shift_y, shift_x), axis=(0, 1))
    return aligned, {"shift_x": shift_x, "shift_y": shift_y}


def normalize_psf_energy(psf: np.ndarray) -> np.ndarray:
    arr = np.asarray(psf, dtype=np.float64)
    total = float(np.sum(np.maximum(arr, 0.0)))
    if total <= 0.0:
        total = float(np.sum(np.abs(arr)))
    if total <= 0.0:
        return arr.copy()
    return arr / total


def compute_dotf(
    psf_reference: np.ndarray,
    psf_perturbed: np.ndarray,
    *,
    normalize_energy: bool,
    align_before_fft: bool,
) -> dict[str, Any]:
    ref = np.asarray(psf_reference, dtype=np.float64)
    pert = np.asarray(psf_perturbed, dtype=np.float64)
    shift = {"shift_x": 0, "shift_y": 0}
    if align_before_fft:
        pert, shift = align_frame_to_reference(pert, ref)
    if normalize_energy:
        ref = normalize_psf_energy(ref)
        pert = normalize_psf_energy(pert)
    otf_ref = psf_to_otf(ref)
    otf_pert = psf_to_otf(pert)
    dotf = otf_pert - otf_ref
    return {
        "psf_reference": ref,
        "psf_perturbed": pert,
        "otf_reference": otf_ref,
        "otf_perturbed": otf_pert,
        "dotf": dotf,
        "alignment_shift": shift,
    }


def recompute_crops_from_frames(frames: np.ndarray, psf_roi: dict[str, Any]) -> np.ndarray:
    return np.stack([crop_frame(frame, psf_roi["roi"]) for frame in np.asarray(frames)], axis=0)


def save_grayscale_preview(path: str | Path, image: np.ndarray, *, mode: str = "linear", percentile: float = 99.5) -> None:
    arr = np.asarray(image, dtype=np.float64)
    if mode == "linear":
        scaled = _scale_linear(arr, upper_percentile=percentile)
    elif mode == "log":
        scaled = _scale_linear(np.log1p(np.abs(arr)), upper_percentile=percentile)
    elif mode == "signed":
        scaled = _scale_signed(arr, percentile=percentile)
    else:
        raise ValueError(f"unknown preview mode: {mode}")
    _save_uint8_image(path, scaled)


def save_phase_preview(path: str | Path, complex_image: np.ndarray) -> None:
    phase = np.angle(np.asarray(complex_image))
    rgb = _phase_to_rgb(phase)
    _save_uint8_image(path, rgb)


def _edge_block_bounds(
    physical_shape: tuple[int, int],
    *,
    center: tuple[float, float],
    radius: float,
    side: str,
    block_size_px: int,
    offset_from_effective_radius_px: int,
) -> tuple[int, int, int, int]:
    h, w = int(physical_shape[0]), int(physical_shape[1])
    cx, cy = float(center[0]), float(center[1])
    r = float(radius)
    block = int(block_size_px)
    offset = int(offset_from_effective_radius_px)
    half = max(1, block // 2)
    if side == "left":
        x_min = int(math.floor(cx - r + offset))
        x_max = x_min + block
        y_min = int(round(cy)) - half
        y_max = y_min + block
    elif side == "right":
        x_max = int(math.ceil(cx + r + 1 - offset))
        x_min = x_max - block
        y_min = int(round(cy)) - half
        y_max = y_min + block
    elif side == "top":
        y_min = int(math.floor(cy - r + offset))
        y_max = y_min + block
        x_min = int(round(cx)) - half
        x_max = x_min + block
    elif side == "bottom":
        y_max = int(math.ceil(cy + r + 1 - offset))
        y_min = y_max - block
        x_min = int(round(cx)) - half
        x_max = x_min + block
    else:
        raise ValueError(f"unknown side: {side}")
    x_min = min(max(0, x_min), max(0, w - 1))
    x_max = min(max(x_min + 1, x_max), w)
    y_min = min(max(0, y_min), max(0, h - 1))
    y_max = min(max(y_min + 1, y_max), h)
    return x_min, x_max, y_min, y_max


def _normalize_perturbation_side(side: str) -> str:
    side_key = str(side).strip().lower()
    if side_key.startswith("edge_block_"):
        side_key = side_key[len("edge_block_") :]
    if side_key not in {"left", "right", "top", "bottom"}:
        raise ValueError(f"unsupported perturbation side: {side}")
    return side_key


def _append_frame(dset: h5py.Dataset, row: int, frame: np.ndarray) -> None:
    if frame.ndim != 2:
        raise ValueError(f"frame must be 2D, got {frame.shape}")
    if row == 0:
        dset.resize((1, frame.shape[0], frame.shape[1]))
    else:
        if dset.shape[1:] != frame.shape:
            raise ValueError(f"all frames must share shape {dset.shape[1:]}, got {frame.shape}")
        dset.resize((row + 1, frame.shape[0], frame.shape[1]))
    dset[row] = frame


def _append_scalar(dset: h5py.Dataset, value: Any) -> None:
    n = dset.shape[0]
    dset.resize((n + 1,))
    dset[n] = value


def _scale_linear(array: np.ndarray, *, lower_percentile: float = 1.0, upper_percentile: float = 99.5) -> np.ndarray:
    finite = np.asarray(array, dtype=np.float64)
    finite = finite[np.isfinite(finite)]
    if finite.size == 0:
        return np.zeros(np.asarray(array).shape, dtype=np.uint8)
    lo = float(np.percentile(finite, lower_percentile))
    hi = float(np.percentile(finite, upper_percentile))
    if hi <= lo:
        return np.zeros(np.asarray(array).shape, dtype=np.uint8)
    scaled = (np.asarray(array, dtype=np.float64) - lo) * (255.0 / (hi - lo))
    return np.clip(scaled, 0, 255).astype(np.uint8)


def _scale_signed(array: np.ndarray, *, percentile: float = 99.5) -> np.ndarray:
    arr = np.asarray(array, dtype=np.float64)
    finite = np.abs(arr[np.isfinite(arr)])
    if finite.size == 0:
        return np.zeros(arr.shape, dtype=np.uint8)
    bound = float(np.percentile(finite, percentile))
    if bound <= 0.0:
        return np.full(arr.shape, 127, dtype=np.uint8)
    scaled = (arr + bound) * (255.0 / (2.0 * bound))
    return np.clip(scaled, 0, 255).astype(np.uint8)


def _phase_to_rgb(phase: np.ndarray) -> np.ndarray:
    h = np.mod((np.asarray(phase, dtype=np.float64) + np.pi) / (2.0 * np.pi), 1.0)
    s = np.ones_like(h)
    v = np.ones_like(h)
    return _hsv_to_rgb(h, s, v)


def _hsv_to_rgb(h: np.ndarray, s: np.ndarray, v: np.ndarray) -> np.ndarray:
    hh = np.mod(h, 1.0) * 6.0
    i = np.floor(hh).astype(np.int32)
    f = hh - i
    p = v * (1.0 - s)
    q = v * (1.0 - s * f)
    t = v * (1.0 - s * (1.0 - f))
    r = np.zeros_like(hh)
    g = np.zeros_like(hh)
    b = np.zeros_like(hh)

    idx = i % 6
    masks = [idx == k for k in range(6)]
    r[masks[0]], g[masks[0]], b[masks[0]] = v[masks[0]], t[masks[0]], p[masks[0]]
    r[masks[1]], g[masks[1]], b[masks[1]] = q[masks[1]], v[masks[1]], p[masks[1]]
    r[masks[2]], g[masks[2]], b[masks[2]] = p[masks[2]], v[masks[2]], t[masks[2]]
    r[masks[3]], g[masks[3]], b[masks[3]] = p[masks[3]], q[masks[3]], v[masks[3]]
    r[masks[4]], g[masks[4]], b[masks[4]] = t[masks[4]], p[masks[4]], v[masks[4]]
    r[masks[5]], g[masks[5]], b[masks[5]] = v[masks[5]], p[masks[5]], q[masks[5]]
    rgb = np.stack([r, g, b], axis=-1)
    return np.clip(np.round(rgb * 255.0), 0, 255).astype(np.uint8)


def _save_uint8_image(path: str | Path, image: np.ndarray) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    arr = np.asarray(image, dtype=np.uint8)
    try:
        import cv2

        if arr.ndim == 3 and arr.shape[2] == 3:
            cv2.imwrite(str(path), cv2.cvtColor(arr, cv2.COLOR_RGB2BGR))
        else:
            cv2.imwrite(str(path), arr)
        return
    except ImportError:
        pass

    try:
        from PIL import Image

        Image.fromarray(arr).save(path)
        return
    except ImportError:
        pass

    np.save(str(path.with_suffix(".npy")), arr)
