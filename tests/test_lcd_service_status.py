from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from diagnostics.run_status import read_lcd_state, read_mask_preview, write_lcd_state
from devices.lcd_backend import LCDBackend


class FakeLCDBackend:
    """A fake LCD backend that records calls but does not open a real display."""
    def __init__(self, *, reported_shape=(3, 20, 3)):
        self._reported_shape = tuple(reported_shape)
        self.shown: list[np.ndarray] = []
        self.closed = False

    def get_metadata(self) -> dict:
        return {
            "display_index": 99,
            "reported_shape": self._reported_shape,
        }

    def show(self, rgb: np.ndarray) -> None:
        self.shown.append(np.asarray(rgb).copy())

    def close(self) -> None:
        self.closed = True
        self.shown.clear()


@pytest.fixture
def fake_backend():
    return FakeLCDBackend()


@pytest.fixture
def status_dir(tmp_path):
    return tmp_path / "status"


def test_lcd_service_with_status_dir_writes_on_show_mono_mask(fake_backend, status_dir, monkeypatch):
    from devices.lcd_service import LCDService

    monkeypatch.setattr(LCDService, "initialize", lambda self: None)
    svc = LCDService(
        backend=fake_backend,
        display_index=1,
        subpixel_axis=1,
        status_dir=status_dir,
    )
    svc._backend = fake_backend  # already initialized
    mask = np.arange(180, dtype=np.uint8).reshape(3, 60)

    svc.show_mono_mask(mask, mask_id="bars_test")

    assert (status_dir / "lcd_state.json").exists()
    state = read_lcd_state(status_dir)
    assert state is not None
    assert state["current_mask_id"] == "bars_test"
    assert state["current_mode"] == "mono_mask"
    assert state["connected"] is True

    assert state["mask_preview"] is not None
    preview = read_mask_preview(status_dir)
    assert preview is not None


def test_lcd_service_write_failure_does_not_block_show(fake_backend, status_dir, monkeypatch):
    from devices.lcd_service import LCDService

    monkeypatch.setattr(LCDService, "initialize", lambda self: None)
    svc = LCDService(
        backend=fake_backend,
        display_index=1,
        subpixel_axis=1,
        status_dir=status_dir,
    )
    svc._backend = fake_backend
    status_dir.mkdir(parents=True, exist_ok=True)
    readme = status_dir / "README.txt"
    readme.write_text("block the directory name")
    status_dir_str = str(status_dir)
    lcd_state_path = status_dir / "lcd_state.json"
    lcd_state_path.unlink(missing_ok=True)
    try:
        lcd_state_path.mkdir()
    except Exception:
        pass

    original = svc._publish_lcd_state_and_mask_preview
    def _noop(*args, **kwargs):
        return
    svc._publish_lcd_state_and_mask_preview = _noop

    mask = np.zeros((3, 60), dtype=np.uint8)
    svc.show_mono_mask(mask, mask_id="should_not_crash")

    assert len(fake_backend.shown) == 1


def test_lcd_service_without_status_dir_does_not_write(fake_backend, monkeypatch, tmp_path):
    from devices.lcd_service import LCDService

    monkeypatch.setattr(LCDService, "initialize", lambda self: None)
    svc = LCDService(
        backend=fake_backend,
        display_index=1,
        subpixel_axis=1,
    )
    svc._backend = fake_backend
    mask = np.zeros((3, 60), dtype=np.uint8)

    svc.show_mono_mask(mask, mask_id="test")

    assert not (tmp_path / "lcd_state.json").exists()


def test_lcd_service_close_writes_state(fake_backend, status_dir, monkeypatch):
    from devices.lcd_service import LCDService

    monkeypatch.setattr(LCDService, "initialize", lambda self: None)
    svc = LCDService(
        backend=fake_backend,
        display_index=1,
        subpixel_axis=1,
        status_dir=status_dir,
    )
    svc._backend = fake_backend
    svc.show_all_transmissive()
    svc.close()

    state = read_lcd_state(status_dir)
    assert state is not None
    assert state["connected"] is False


def test_lcd_service_set_status_dir(fake_backend, monkeypatch, tmp_path):
    from devices.lcd_service import LCDService

    monkeypatch.setattr(LCDService, "initialize", lambda self: None)
    sd = tmp_path / "later_status"
    svc = LCDService(
        backend=fake_backend,
        display_index=1,
        subpixel_axis=1,
    )
    svc._backend = fake_backend

    mask = np.zeros((3, 60), dtype=np.uint8)
    svc.show_mono_mask(mask, mask_id="pre")
    assert not sd.exists()

    svc.set_status_dir(sd)
    svc.show_mono_mask(mask, mask_id="post")
    assert (sd / "lcd_state.json").exists()

    state = read_lcd_state(sd)
    assert state["current_mask_id"] == "post"


def test_lcd_status_publish_does_not_recurse(fake_backend, status_dir, monkeypatch):
    from devices.lcd_service import LCDService

    monkeypatch.setattr(LCDService, "initialize", lambda self: None)
    svc = LCDService(
        backend=fake_backend,
        display_index=1,
        subpixel_axis=1,
        status_dir=status_dir,
    )
    svc._backend = fake_backend
    mask = np.zeros((3, 60), dtype=np.uint8)

    svc.show_mono_mask(mask, mask_id="no_recurse")

    assert (status_dir / "lcd_state.json").exists()
    state = read_lcd_state(status_dir)
    assert state is not None
    assert state["current_mask_id"] == "no_recurse"


def test_lcd_close_does_not_reinitialize_backend(fake_backend, status_dir, monkeypatch):
    from devices.lcd_service import LCDService, LCDBackend

    monkeypatch.setattr(LCDService, "initialize", lambda self: None)
    svc = LCDService(
        backend=fake_backend,
        display_index=1,
        subpixel_axis=1,
        status_dir=status_dir,
    )
    svc._backend = fake_backend
    svc.show_all_transmissive()

    reinit_calls = []
    original_init = svc.initialize

    def _tracked_init():
        if svc._backend is None:
            reinit_calls.append(1)
        original_init()

    monkeypatch.setattr(svc, "initialize", _tracked_init)
    svc.close()

    assert reinit_calls == []
    assert fake_backend.closed is True


def test_lcd_close_writes_connected_false(fake_backend, status_dir, monkeypatch):
    from devices.lcd_service import LCDService

    monkeypatch.setattr(LCDService, "initialize", lambda self: None)
    svc = LCDService(
        backend=fake_backend,
        display_index=1,
        subpixel_axis=1,
        status_dir=status_dir,
    )
    svc._backend = fake_backend
    svc.show_all_transmissive()
    svc.close()

    state = read_lcd_state(status_dir)
    assert state is not None
    assert state["connected"] is False
    assert state["current_mode"] is None
    assert state["current_mask_id"] is None
