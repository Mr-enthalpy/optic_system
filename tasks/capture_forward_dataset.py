"""
Capture forward dataset — Phase 2 minimal synchronous capture orchestration.

This task intentionally uses a narrow synchronous CaptureDeviceBundle
instead of SessionController.dispatch().  Reasons:

* capture needs blocking TLS move / wait_until_idle semantics;
* capture needs a private FrameStreamClient (cannot share the
  PreviewWorker PUB/SUB socket);
* no CaptureBurst command exists on SessionController yet;
* SessionController remains the GUI / control path — this bundle is a
  minimal capture-task synchronous execution adapter, not a replacement.

Do not add neural network code, LCD_forward imports, or pywinauto
automation here.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol as _Protocol

import numpy as np

from .capture_plan import CapturePlan, CapturePlanError
from .raw_capture_h5 import RawCaptureWriter, RawCaptureWriteError


# ---------------------------------------------------------------------------
# Error types
# ---------------------------------------------------------------------------


class DeviceNotReadyError(RuntimeError):
    pass


class TLSUnavailableError(RuntimeError):
    def __init__(self):
        super().__init__(
            "TLS is requested but no TLS device is available. "
            "Install tls_c1 and enable TLS, or disable --enable-tls."
        )


class CameraUnavailableError(RuntimeError):
    pass


class LCDDisplayError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Capture result
# ---------------------------------------------------------------------------


@dataclass
class CaptureFrames:
    burst: np.ndarray          # [K, H, W]
    frames_avg: np.ndarray     # [H, W]
    metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Capture-device protocols — narrow synchronous adapter boundary
# ---------------------------------------------------------------------------


class CameraCaptureProtocol(_Protocol):
    def acquire_burst(self, k: int) -> CaptureFrames:
        ...


class LCDDisplayProtocol(_Protocol):
    def show_physical_mask(self, mask: np.ndarray) -> None:
        ...

    def metadata(self) -> dict[str, Any]:
        ...

    def physical_shape(self) -> tuple[int, int]:
        ...

    def subpixel_axis(self) -> int:
        ...


class TLSControlProtocol(_Protocol):
    def set_grating(self, grating: int) -> None:
        ...

    def set_wavelength(self, wavelength_nm: float) -> None:
        ...

    def move_and_wait(self, timeout_s: float) -> None:
        ...

    def status(self) -> dict[str, Any]:
        ...


class CaptureDeviceBundle(_Protocol):
    camera: CameraCaptureProtocol
    lcd: LCDDisplayProtocol
    tls: TLSControlProtocol | None


# ---------------------------------------------------------------------------
# Fake devices  (also useful for CLI --dry-run)
# ---------------------------------------------------------------------------


class FakeCamera:
    def __init__(self, *, seed: int = 42, height: int = 480, width: int = 640):
        self._rng = np.random.default_rng(seed)
        self._h = height
        self._w = width

    def acquire_burst(self, k: int) -> CaptureFrames:
        burst = self._rng.normal(128, 40, (k, self._h, self._w)).astype(np.float64)
        avg = burst.mean(axis=0, dtype=np.float64)
        return CaptureFrames(
            burst=burst,
            frames_avg=avg,
            metadata={
                "exposure_us": None,
                "gain_db": None,
                "roi": None,
                "timestamp_ns": time.monotonic_ns(),
                "status": {"source": "fake"},
                "frame_shape": [self._h, self._w],
                "acquisition": "burst",
                "n": k,
            },
        )


class FakeLCD:
    def __init__(self, *, height: int = 60, width_phys: int = 180, subpixel_axis: int = 1):
        self._h = height
        self._w = width_phys
        self._subpixel_axis = subpixel_axis
        self.last_mask: np.ndarray | None = None

    def show_physical_mask(self, mask: np.ndarray) -> None:
        mask = np.asarray(mask)
        if mask.ndim != 2:
            raise LCDDisplayError(f"mask must be 2D [H, 3W], got shape {mask.shape}")
        self.last_mask = mask.copy()

    def metadata(self) -> dict[str, Any]:
        return {
            "display_index": 0,
            "physical_shape": [self._h, self._w],
            "logical_shape": (self._h // 3, self._w) if self._subpixel_axis == 0 else (self._h, self._w // 3),
            "subpixel_axis": self._subpixel_axis,
            "transmissive_code": 255,
            "opaque_code": 0,
        }

    def physical_shape(self) -> tuple[int, int]:
        return (self._h, self._w)

    def subpixel_axis(self) -> int:
        return self._subpixel_axis


class FakeTLS:
    def __init__(self):
        self._current_nm: float | None = None
        self._target_nm: float | None = None
        self._grating: int | None = None
        self._connected = False

    def connect(self) -> None:
        self._connected = True

    def set_grating(self, grating: int) -> None:
        self._grating = int(grating)

    def set_wavelength(self, wavelength_nm: float) -> None:
        self._target_nm = float(wavelength_nm)

    def move_and_wait(self, timeout_s: float) -> None:
        self._current_nm = self._target_nm

    def status(self) -> dict[str, Any]:
        return {
            "connected": self._connected,
            "current_wavelength_nm": self._current_nm,
            "target_wavelength_nm": self._target_nm,
            "grating": self._grating,
            "moving": False,
            "timestamp_ns": time.monotonic_ns(),
        }


class FakeDeviceBundle:
    def __init__(
        self,
        camera: CameraCaptureProtocol | None = None,
        lcd: LCDDisplayProtocol | None = None,
        tls: TLSControlProtocol | None = None,
    ):
        self.camera = camera or FakeCamera()
        self.lcd = lcd or FakeLCD()
        self.tls = tls


# ---------------------------------------------------------------------------
# Real-device adapters  (wrap existing device services)
# ---------------------------------------------------------------------------


class CameraCaptureAdapter:
    """
    Synchronous frame-capture adapter.

    *capture_helper* must be a ``FrameCaptureHelper`` with its own
    ``FrameStreamClient`` (not shared with PreviewWorker).
    """

    def __init__(self, capture_helper):
        self._helper = capture_helper

    def acquire_burst(self, k: int) -> CaptureFrames:
        frames: list[np.ndarray] = []
        for _ in range(k):
            raw, _rgb = self._helper.capture_one()
            frames.append(raw.astype(np.float64, copy=False))
        burst = np.stack(frames, axis=0)
        avg = burst.mean(axis=0, dtype=np.float64)
        return CaptureFrames(
            burst=burst,
            frames_avg=avg,
            metadata={
                "acquisition": "burst",
                "n": k,
                "timestamp_ns": time.monotonic_ns(),
            },
        )


class LCDAdapter:
    def __init__(self, lcd_service):
        self._service = lcd_service

    def show_physical_mask(self, mask: np.ndarray) -> None:
        self._service.show_mono_mask(mask, mask_id=None)

    def metadata(self) -> dict[str, Any]:
        return self._service.get_metadata()

    def physical_shape(self) -> tuple[int, int]:
        meta = self._service.get_metadata()
        h, w = meta["physical_shape"]
        return (int(h), int(w))

    def subpixel_axis(self) -> int:
        return int(self._service.subpixel_axis)


class TLSAdapter:
    def __init__(self, tls_service):
        self._service = tls_service

    def set_grating(self, grating: int) -> None:
        self._service.set_grating(int(grating))

    def set_wavelength(self, wavelength_nm: float) -> None:
        self._service.set_wavelength_nm(wavelength_nm)

    def move_and_wait(self, timeout_s: float) -> None:
        self._service.move(timeout_s=timeout_s)
        self._service.wait_until_idle(timeout_s=timeout_s)

    def status(self) -> dict[str, Any]:
        st = self._service.get_status()
        return {
            "connected": st.connected,
            "current_wavelength_nm": st.current_wavelength_nm,
            "target_wavelength_nm": st.target_wavelength_nm,
            "grating": st.grating,
            "moving": st.moving,
            "timestamp_ns": time.monotonic_ns(),
        }


# ---------------------------------------------------------------------------
# Main capture orchestration
# ---------------------------------------------------------------------------


def run_capture_forward_dataset(
    plan: CapturePlan,
    devices: CaptureDeviceBundle,
    output_path: Path,
    *,
    enable_tls: bool = False,
    dry_run: bool = False,
) -> Path:
    plan.validate()

    if enable_tls and devices.tls is None:
        raise TLSUnavailableError()

    physical_masks = _materialize_masks(plan, allow_placeholder=dry_run, placeholder_shape=devices.lcd.physical_shape())

    writer = RawCaptureWriter(output_path, plan)
    writer.open()

    try:
        writer.write_physical_masks(physical_masks)

        capture_idx = 0
        for wi, wl_entry in enumerate(plan.wavelengths):
            if enable_tls and devices.tls is not None:
                tls = devices.tls
                if wl_entry.grating is not None:
                    tls.set_grating(wl_entry.grating)
                tls.set_wavelength(wl_entry.wavelength_nm)
                tls.move_and_wait(timeout_s=60.0)
                if wl_entry.settle_ms > 0 and not dry_run:
                    time.sleep(wl_entry.settle_ms / 1000.0)
                tls_status = tls.status()
            else:
                tls_status = {
                    "connected": False,
                    "current_wavelength_nm": wl_entry.wavelength_nm,
                    "target_wavelength_nm": wl_entry.wavelength_nm,
                    "grating": wl_entry.grating,
                    "moving": False,
                    "timestamp_ns": time.monotonic_ns(),
                }

            for mi, mask_entry in enumerate(plan.masks):
                lcd = devices.lcd
                mask_array = physical_masks[mi]

                _validate_mask_shape(mask_array, mask_entry.mask_id, lcd)

                lcd.show_physical_mask(mask_array)
                lcd_display_ts = time.monotonic_ns()

                if plan.lcd_settle_ms > 0 and not dry_run:
                    time.sleep(plan.lcd_settle_ms / 1000.0)

                k = plan.camera.frames_per_capture
                capture = devices.camera.acquire_burst(k)
                frames_to_store = capture.burst if plan.store_burst else None

                writer.append_capture(
                    capture_index=capture_idx,
                    wavelength_index=wi,
                    mask_index=mi,
                    frames=frames_to_store,
                    frames_avg=capture.frames_avg,
                    camera_meta=capture.metadata,
                    tls_status=tls_status,
                    lcd_display_timestamp_ns=lcd_display_ts,
                )

                capture_idx += 1

        writer.finalize(completed=True)
    except Exception:
        writer.finalize(completed=False, error=str(
            _last_exc_info()
        ))
        raise

    return output_path


def _validate_mask_shape(
    mask_array: np.ndarray,
    mask_id: str,
    lcd: LCDDisplayProtocol,
) -> None:
    expected_h, expected_w = lcd.physical_shape()
    if mask_array.shape != (expected_h, expected_w):
        raise CapturePlanError(
            f"mask {mask_id!r} shape {mask_array.shape} "
            f"does not match LCD physical shape ({expected_h}, {expected_w})"
        )
    axis = lcd.subpixel_axis()
    divisor = mask_array.shape[1] if axis == 1 else mask_array.shape[0]
    if divisor % 3 != 0:
        raise CapturePlanError(
            f"mask {mask_id!r}: physical mono mask shape {mask_array.shape} "
            f"incompatible with subpixel_axis={axis}; "
            f"the expanded axis length must be divisible by 3"
        )


def _materialize_masks(
    plan: CapturePlan,
    *,
    allow_placeholder: bool = False,
    placeholder_shape: tuple[int, int] = (60, 180),
) -> list[np.ndarray]:
    masks: list[np.ndarray] = []
    for entry in plan.masks:
        if entry.array is not None:
            mask = np.asarray(entry.array, dtype=np.uint8)
        elif entry.path is not None:
            mask = _load_mask_from_path(entry.path)
        elif allow_placeholder:
            mask = _generate_placeholder_mask(entry, placeholder_shape)
        else:
            raise CapturePlanError(
                f"mask {entry.mask_id!r}: either 'array' or 'path' "
                f"must be provided; placeholder masks are allowed "
                f"only in dry-run mode"
            )
        if mask.ndim != 2:
            raise CapturePlanError(
                f"mask {entry.mask_id!r}: expected 2D array [H, 3W], "
                f"got shape {mask.shape}"
            )
        masks.append(mask.astype(np.uint8, copy=False))
    return masks


def _generate_placeholder_mask(entry, shape: tuple[int, int]) -> np.ndarray:
    digest = hashlib.sha256(entry.mask_id.encode("utf-8")).hexdigest()
    seed = int(digest[:16], 16) & 0xFFFFFFFF
    rng = np.random.default_rng(seed)
    h, w = shape
    return rng.integers(0, 256, (h, w), dtype=np.uint8)


def _load_mask_from_path(path: str) -> np.ndarray:
    p = Path(path)
    if p.suffix in (".npy",):
        return np.load(str(p))
    if p.suffix in (".npz",):
        data = np.load(str(p))
        arrays = [v for v in data.values() if isinstance(v, np.ndarray)]
        if len(arrays) != 1:
            raise CapturePlanError(
                f"expected exactly one array in {path}, got {len(arrays)}"
            )
        return arrays[0]
    if p.suffix in (".png", ".tiff", ".tif", ".bmp", ".jpg", ".jpeg"):
        try:
            import cv2
            img = cv2.imread(str(p), cv2.IMREAD_GRAYSCALE)
            if img is None:
                raise CapturePlanError(f"failed to read image: {path}")
            return np.asarray(img, dtype=np.uint8)
        except ImportError:
            raise CapturePlanError(
                "opencv-python is required to load image masks. "
                "Install it with: pip install opencv-python"
            )
    raise CapturePlanError(f"unsupported mask file format: {p.suffix}")


def _last_exc_info() -> str:
    import sys
    exc = sys.exc_info()[1]
    return str(exc) if exc is not None else "unknown error"
