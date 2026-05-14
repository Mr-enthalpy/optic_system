from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

_repo_root = Path(__file__).resolve().parents[1]
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from tasks.capture_forward_dataset import (
    CameraCaptureAdapter,
    FakeCamera,
    CaptureFrames,
)


class FakeCaptureHelper:
    def __init__(self, height=240, width=320):
        self._h = height
        self._w = width

    def capture_one(self):
        raw = (np.ones((self._h, self._w), dtype=np.float64) * 128).astype(np.uint8)
        rgb = np.zeros((self._h, self._w, 3), dtype=np.uint8)
        return raw, rgb

    def capture_one_packet(self):
        raw, rgb = self.capture_one()
        return SimpleNamespace(raw=raw, preview_bgr=rgb, meta={"format": "raw8"})


class TestCameraCaptureAdapterWithService:
    def test_apply_exposure_calls_set_value(self):
        mock_svc = MagicMock()
        helper = FakeCaptureHelper()
        adapter = CameraCaptureAdapter(helper, camera_service=mock_svc)

        adapter.apply_camera_params(exposure_us=5000.0)
        mock_svc.set_value.assert_called_once_with("SHUTTER", 5.0)

    def test_apply_gain_calls_set_value(self):
        mock_svc = MagicMock()
        helper = FakeCaptureHelper()
        adapter = CameraCaptureAdapter(helper, camera_service=mock_svc)

        adapter.apply_camera_params(gain_db=3.0)
        mock_svc.set_value.assert_called_once_with("GAIN", 3.0)

    def test_apply_both(self):
        mock_svc = MagicMock()
        helper = FakeCaptureHelper()
        adapter = CameraCaptureAdapter(helper, camera_service=mock_svc)

        adapter.apply_camera_params(exposure_us=2500.0, gain_db=6.0)
        assert mock_svc.set_value.call_count == 2
        mock_svc.set_value.assert_any_call("SHUTTER", 2.5)
        mock_svc.set_value.assert_any_call("GAIN", 6.0)

    def test_read_params_returns_values(self):
        mock_svc = MagicMock()
        mock_svc.get_value.side_effect = lambda name: (
            5.0 if name == "SHUTTER" else 3.0
        )
        helper = FakeCaptureHelper()
        adapter = CameraCaptureAdapter(helper, camera_service=mock_svc)

        params = adapter.read_camera_params()
        assert params["exposure_us"] == 5000.0
        assert params["gain_db"] == 3.0

    def test_read_params_error_returns_none(self):
        mock_svc = MagicMock()
        mock_svc.get_value.side_effect = RuntimeError("not connected")
        helper = FakeCaptureHelper()
        adapter = CameraCaptureAdapter(helper, camera_service=mock_svc)

        params = adapter.read_camera_params()
        assert params["exposure_us"] is None
        assert params["gain_db"] is None

    def test_metadata_includes_camera_params(self):
        mock_svc = MagicMock()
        mock_svc.get_value.side_effect = lambda name: (
            5.0 if name == "SHUTTER" else 3.0
        )
        helper = FakeCaptureHelper()
        adapter = CameraCaptureAdapter(helper, camera_service=mock_svc)

        capture = adapter.acquire_burst(3)
        assert capture.metadata["exposure_us"] == 5000.0
        assert capture.metadata["gain_db"] == 3.0

    def test_without_service_backward_compatible(self):
        helper = FakeCaptureHelper()
        adapter = CameraCaptureAdapter(helper)

        adapter.apply_camera_params(exposure_us=5000.0)
        capture = adapter.acquire_burst(2)
        assert capture.metadata["exposure_us"] is None
        assert capture.metadata["gain_db"] is None

    def test_apply_camera_params_noop_without_service(self):
        helper = FakeCaptureHelper()
        adapter = CameraCaptureAdapter(helper)
        adapter.apply_camera_params(exposure_us=100.0, gain_db=12.0)


class TestFakeCameraWithParams:
    def test_default_params_none(self):
        cam = FakeCamera()
        capture = cam.acquire_burst(2)
        assert capture.metadata["exposure_us"] is None
        assert capture.metadata["gain_db"] is None

    def test_custom_params(self):
        cam = FakeCamera(exposure_us=5000.0, gain_db=3.0)
        capture = cam.acquire_burst(2)
        assert capture.metadata["exposure_us"] == 5000.0
        assert capture.metadata["gain_db"] == 3.0

    def test_partial_params(self):
        cam = FakeCamera(exposure_us=2500.0, gain_db=None)
        capture = cam.acquire_burst(1)
        assert capture.metadata["exposure_us"] == 2500.0
        assert capture.metadata["gain_db"] is None

    def test_acquire_burst_shape(self):
        cam = FakeCamera(height=240, width=320, exposure_us=1000.0)
        capture = cam.acquire_burst(4)
        assert capture.burst.shape == (4, 240, 320)
        assert capture.frames_avg.shape == (240, 320)
        assert capture.metadata["n"] == 4
        assert capture.metadata["acquisition"] == "burst"

    def test_fake_camera_seed_deterministic(self):
        cam1 = FakeCamera(seed=42)
        cam2 = FakeCamera(seed=42)
        c1 = cam1.acquire_burst(1)
        c2 = cam2.acquire_burst(1)
        assert np.array_equal(c1.frames_avg, c2.frames_avg)


class TestCameraCaptureAdapterReadbackWithAbsValue:
    def test_readback_uses_abs_value_ms_converted_to_us(self):
        mock_svc = MagicMock()
        mock_svc.get_value.side_effect = lambda name: (
            5.0 if name == "SHUTTER" else 3.0
        )
        helper = FakeCaptureHelper()
        adapter = CameraCaptureAdapter(helper, camera_service=mock_svc)

        params = adapter.read_camera_params()
        assert params["exposure_us"] == 5000.0
        assert params["gain_db"] == 3.0
        mock_svc.get_value.assert_any_call("SHUTTER")
        mock_svc.get_value.assert_any_call("GAIN")

    def test_readback_without_service_returns_none(self):
        helper = FakeCaptureHelper()
        adapter = CameraCaptureAdapter(helper)

        params = adapter.read_camera_params()
        assert params["exposure_us"] is None
        assert params["gain_db"] is None

    def test_apply_then_readback_roundtrip(self):
        mock_svc = MagicMock()
        shutter_values = [5.0]

        def _get_value(name):
            if name == "SHUTTER":
                return shutter_values[0]
            return 3.0

        mock_svc.get_value.side_effect = _get_value
        helper = FakeCaptureHelper()
        adapter = CameraCaptureAdapter(helper, camera_service=mock_svc)

        adapter.apply_camera_params(exposure_us=5000.0, gain_db=6.0)
        params = adapter.read_camera_params()
        assert params["exposure_us"] == 5000.0
        assert params["gain_db"] == 3.0


class TestCaptureMetadataRawDtype:
    def test_adapter_metadata_includes_raw_dtype_and_full_scale(self):
        mock_svc = MagicMock()
        mock_svc.get_value.side_effect = lambda name: (
            5.0 if name == "SHUTTER" else 3.0
        )
        helper = FakeCaptureHelper()
        adapter = CameraCaptureAdapter(helper, camera_service=mock_svc)

        capture = adapter.acquire_burst(2)
        assert capture.metadata["raw_dtype"] == "uint8"
        assert capture.metadata["pixel_format"] == "raw8"
        assert capture.metadata["frame_dtype_full_scale"] == 255
        assert capture.metadata["frame_dtype_full_scale_source"] == "frame_metadata.format"

    def test_fake_camera_metadata_has_full_scale(self):
        cam = FakeCamera(height=240, width=320)
        capture = cam.acquire_burst(1)
        assert capture.metadata["raw_dtype"] == "float64"
        assert capture.metadata["frame_dtype_full_scale"] == 255
