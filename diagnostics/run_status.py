from __future__ import annotations

import json
import os
import tempfile
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np


@dataclass
class RunStatus:
    run_id: str
    plan_id: str | None = None
    phase: str | None = None
    capture_index: int | None = None
    n_captures: int | None = None
    current_mask_id: str | None = None
    current_wavelength_nm: float | None = None
    target_wavelength_nm: float | None = None
    tls_grating: int | None = None
    tls_moving: bool | None = None
    camera_frame_seq: int | None = None
    camera_max_pixel: float | None = None
    last_update_ns: int = field(default_factory=time.monotonic_ns)
    current_mask_preview: str | None = None
    latest_frame_preview: str | None = None
    frame_stats: str | None = None
    log_file: str | None = None
    lcd_display_index: int | None = None
    lcd_physical_shape: list[int] | None = None
    lcd_logical_shape: list[int] | None = None
    lcd_subpixel_axis: int | None = None
    camera_exposure_us: float | None = None
    camera_gain_db: float | None = None
    camera_roi: list[int] | None = None
    camera_frame_dtype_full_scale: int | None = None
    completed: bool | None = None
    error: str | None = None


class RunStatusPublisher:
    def __init__(self, status_dir: Path, run_id: str):
        self.status_dir = Path(status_dir)
        self.status_dir.mkdir(parents=True, exist_ok=True)
        self._state = RunStatus(run_id=run_id)

    def update(self, **kwargs: Any) -> None:
        data = asdict(self._state)
        data.update(kwargs)
        data["run_id"] = str(data.get("run_id") or self._state.run_id)
        data["last_update_ns"] = time.monotonic_ns()
        self._state = _status_from_dict(data)
        _atomic_write_text(
            self.status_dir / "state.json",
            json.dumps(asdict(self._state), indent=2, sort_keys=True),
        )

    def write_mask_preview(
        self,
        mask: np.ndarray,
        filename: str = "current_mask_preview.png",
    ) -> Path:
        self.status_dir.mkdir(parents=True, exist_ok=True)
        requested = self.status_dir / filename
        array = np.asarray(mask)
        array = _downsample_preview(array)
        if requested.suffix.lower() == ".npy":
            path = requested
            _atomic_save_npy(path, array)
        else:
            try:
                path = requested
                _atomic_write_image(path, _as_uint8_preview(array))
            except Exception:
                path = requested.with_suffix(".npy")
                _atomic_save_npy(path, array)

        rel = _relative_to_status_dir(path, self.status_dir)
        self.update(current_mask_preview=rel)
        return path

    def write_frame_preview(
        self,
        frame: np.ndarray,
        filename: str = "latest_frame_preview.png",
    ) -> Path:
        self.status_dir.mkdir(parents=True, exist_ok=True)
        requested = self.status_dir / filename
        array = np.asarray(frame)
        array = _downsample_preview(array)
        if requested.suffix.lower() == ".npy":
            path = requested
            _atomic_save_npy(path, array)
        else:
            try:
                path = requested
                _atomic_write_image(path, _as_uint8_preview(array))
            except Exception:
                path = requested.with_suffix(".npy")
                _atomic_save_npy(path, array)

        rel = _relative_to_status_dir(path, self.status_dir)
        self.update(latest_frame_preview=rel)
        return path

    def write_frame_stats(
        self,
        stats: dict[str, Any],
        filename: str = "frame_stats.json",
    ) -> Path:
        self.status_dir.mkdir(parents=True, exist_ok=True)
        path = self.status_dir / filename
        _atomic_write_text(
            path,
            json.dumps(stats, indent=2, sort_keys=True, default=_json_default),
        )
        rel = _relative_to_status_dir(path, self.status_dir)
        self.update(frame_stats=rel)
        return path

    def append_log(
        self,
        level: str,
        message: str,
        source: str | None = None,
        **fields: Any,
    ) -> Path:
        self.status_dir.mkdir(parents=True, exist_ok=True)
        path = self.status_dir / "log.jsonl"
        row: dict[str, Any] = {
            "time_ns": time.time_ns(),
            "level": str(level).upper(),
            "message": str(message),
        }
        if source is not None:
            row["source"] = source
        row.update(fields)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, sort_keys=True, default=_json_default))
            f.write("\n")

        rel = _relative_to_status_dir(path, self.status_dir)
        self.update(log_file=rel)
        return path


class RunStatusReader:
    def __init__(self, status_dir: Path):
        self.status_dir = Path(status_dir)

    def read(self) -> RunStatus | None:
        path = self.status_dir / "state.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError):
            return None
        if not isinstance(data, dict):
            return None
        try:
            return _status_from_dict(data)
        except Exception:
            return None

    def read_mask_preview(self) -> np.ndarray | None:
        status = self.read()
        if status is None or not status.current_mask_preview:
            return None

        return self._read_preview_file(status.current_mask_preview)

    def read_frame_preview(self) -> np.ndarray | None:
        status = self.read()
        if status is None or not status.latest_frame_preview:
            return None

        return self._read_preview_file(status.latest_frame_preview)

    def read_frame_stats(self) -> dict[str, Any] | None:
        status = self.read()
        if status is None or not status.frame_stats:
            return None

        path = Path(status.frame_stats)
        if not path.is_absolute():
            path = self.status_dir / path
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError):
            return None
        return data if isinstance(data, dict) else None

    def tail_log(self, max_lines: int = 200) -> list[dict[str, Any]]:
        status = self.read()
        if status is None or not status.log_file:
            return []

        path = Path(status.log_file)
        if not path.is_absolute():
            path = self.status_dir / path
        try:
            with path.open("r", encoding="utf-8") as f:
                lines = deque(f, maxlen=max(0, int(max_lines)))
        except (OSError, ValueError):
            return []

        rows: list[dict[str, Any]] = []
        for line in lines:
            try:
                data = json.loads(line)
            except (json.JSONDecodeError, TypeError):
                continue
            if isinstance(data, dict):
                rows.append(data)
        return rows

    def _read_preview_file(self, preview_path: str) -> np.ndarray | None:
        path = Path(preview_path)
        if not path.is_absolute():
            path = self.status_dir / path
        try:
            if path.suffix.lower() == ".npy":
                return np.load(str(path))
            return _read_image(path)
        except Exception:
            return None


def _status_from_dict(data: dict[str, Any]) -> RunStatus:
    names = set(RunStatus.__dataclass_fields__.keys())
    filtered = {name: data.get(name) for name in names if name in data}
    if not filtered.get("run_id"):
        filtered["run_id"] = "unknown"
    return RunStatus(**filtered)


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
        text=True,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.write("\n")
        os.replace(tmp_name, path)
    finally:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass


def _atomic_save_npy(path: Path, array: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".npy.tmp",
        dir=str(path.parent),
    )
    os.close(fd)
    try:
        with open(tmp_name, "wb") as f:
            np.save(f, array)
        os.replace(tmp_name, path)
    finally:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass


def _atomic_write_image(path: Path, image: np.ndarray) -> None:
    try:
        import cv2
    except ImportError as exc:
        raise RuntimeError("cv2 is required for PNG mask previews") from exc

    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=path.suffix or ".png",
        dir=str(path.parent),
    )
    os.close(fd)
    try:
        if not cv2.imwrite(tmp_name, image):
            raise RuntimeError(f"failed to write image preview: {path}")
        os.replace(tmp_name, path)
    finally:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass


def _read_image(path: Path) -> np.ndarray | None:
    try:
        import cv2
    except ImportError:
        return None
    image = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
    return None if image is None else image


def _downsample_preview(arr: np.ndarray, max_side: int = 768) -> np.ndarray:
    a = np.asarray(arr)
    h, w = a.shape[:2]
    if max(h, w) <= max_side:
        return a
    scale = max_side / max(h, w)
    new_h, new_w = max(1, int(h * scale)), max(1, int(w * scale))
    try:
        import cv2
        return cv2.resize(a, (new_w, new_h), interpolation=cv2.INTER_AREA)
    except ImportError:
        ry, rx = max(1, h // new_h), max(1, w // new_w)
        return a[::ry, ::rx]


def _as_uint8_preview(array: np.ndarray) -> np.ndarray:
    arr = np.asarray(array)
    if arr.dtype == np.uint8:
        return arr
    if arr.size == 0:
        return arr.astype(np.uint8)
    finite = arr[np.isfinite(arr)] if np.issubdtype(arr.dtype, np.floating) else arr
    if finite.size == 0:
        return np.zeros(arr.shape, dtype=np.uint8)
    mn = float(np.min(finite))
    mx = float(np.max(finite))
    if mx <= mn:
        return np.zeros(arr.shape, dtype=np.uint8)
    scaled = (arr.astype(np.float64) - mn) * (255.0 / (mx - mn))
    return np.clip(scaled, 0, 255).astype(np.uint8)


def _relative_to_status_dir(path: Path, status_dir: Path) -> str:
    try:
        return str(path.relative_to(status_dir))
    except ValueError:
        return str(path)


def _json_default(value: Any) -> Any:
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, np.ndarray):
        return value.tolist()
    raise TypeError(f"object of type {type(value).__name__} is not JSON serializable")
