from __future__ import annotations

import numpy as np
import pytest

from devices.lcd_service import LCDService


class _FakeBackend:
    def __init__(self, shape: tuple[int, int, int]):
        self._shape = shape
        self._last_rgb: np.ndarray | None = None

    def get_metadata(self) -> dict:
        return {
            "display_index": 0,
            "display_size": (self._shape[1], self._shape[0]),
            "reported_shape": self._shape,
        }

    def show(self, rgb: np.ndarray) -> None:
        self._last_rgb = rgb.copy()

    def close(self) -> None:
        pass


def _make_fake_backend(h: int, w: int) -> _FakeBackend:
    return _FakeBackend((h, w, 3))


class TestLCDServiceRoundtrip:
    def test_axis0_roundtrip(self) -> None:
        svc = LCDService(
            backend=_make_fake_backend(540, 2560),
            subpixel_axis=0,
        )
        mono = np.arange(1620 * 2560, dtype=np.uint8).reshape(1620, 2560)
        rgb = svc.mono_to_rgb(mono)
        assert rgb.shape == (540, 2560, 3)
        recovered = svc.rgb_to_mono(rgb)
        assert np.array_equal(recovered, mono)

    def test_axis1_roundtrip(self) -> None:
        svc = LCDService(
            backend=_make_fake_backend(540, 2560),
            subpixel_axis=1,
        )
        mono = np.arange(540 * 7680, dtype=np.uint8).reshape(540, 7680)
        rgb = svc.mono_to_rgb(mono)
        assert rgb.shape == (540, 2560, 3)
        recovered = svc.rgb_to_mono(rgb)
        assert np.array_equal(recovered, mono)

    def test_axis0_roundtrip_pattern(self) -> None:
        svc = LCDService(
            backend=_make_fake_backend(100, 200),
            subpixel_axis=0,
        )
        mono = np.zeros((300, 200), dtype=np.uint8)
        mono[::10, :] = 255
        rgb = svc.mono_to_rgb(mono)
        recovered = svc.rgb_to_mono(rgb)
        assert np.array_equal(recovered, mono)


class TestLCDServiceAxisPacking:
    """Lightweight roundtrip contracts — small dimensions, fast."""

    def test_axis0_pack_roundtrip(self) -> None:
        backend = _FakeBackend((2, 5, 3))
        svc = LCDService(backend=backend, subpixel_axis=0)
        mono = np.arange(2 * 3 * 5, dtype=np.uint8).reshape(6, 5)
        rgb = svc.mono_to_rgb(mono)
        assert rgb.shape == (2, 5, 3)
        assert np.array_equal(svc.rgb_to_mono(rgb), mono)

    def test_axis1_pack_roundtrip(self) -> None:
        backend = _FakeBackend((2, 5, 3))
        svc = LCDService(backend=backend, subpixel_axis=1)
        mono = np.arange(2 * 5 * 3, dtype=np.uint8).reshape(2, 15)
        rgb = svc.mono_to_rgb(mono)
        assert rgb.shape == (2, 5, 3)
        assert np.array_equal(svc.rgb_to_mono(rgb), mono)
