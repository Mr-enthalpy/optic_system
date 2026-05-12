from __future__ import annotations

import json
import os
import tempfile
import time
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

        path = Path(status.current_mask_preview)
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
