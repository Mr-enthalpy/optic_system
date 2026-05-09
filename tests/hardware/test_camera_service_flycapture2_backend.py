from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.hardware

if os.environ.get("OPTIC_SYSTEM_HARDWARE_TEST") != "1":
    pytest.skip("Set OPTIC_SYSTEM_HARDWARE_TEST=1 to run FlyCapture2 camera service hardware tests.", allow_module_level=True)

from devices.camera_service import CameraServiceClient
from devices.frame_stream import FrameStreamClient


def test_camera_service_flycapture2_sidecar_hardware_smoke() -> None:
    camera_index = int(os.environ.get("OPTIC_SYSTEM_CAMERA_INDEX", "0"))
    frame_count = int(os.environ.get("OPTIC_SYSTEM_FRAME_COUNT", "30"))

    service = CameraServiceClient(timeout_ms=5000, auto_ensure=True)
    stream = FrameStreamClient(recv_timeout_ms=5000)
    shutdown_sent = False
    try:
        open_reply = service.open_camera(index=camera_index, disable_trigger=True)
        assert open_reply["backend"] == "flycapture2_c"
        assert open_reply["width"] > 0
        assert open_reply["height"] > 0

        service.start_stream()
        packets = [stream.recv_frame() for _ in range(frame_count)]

        assert len(packets) == frame_count
        assert packets[-1].meta["protocol_version"] == 2
        assert packets[-1].meta["backend"] == "flycapture2_c"
        assert packets[-1].raw.nbytes == packets[-1].meta["frame_nbytes"]
        assert packets[-1].raw.size > 0

        status = service.get_stream_status()
        assert status["running"] is True
        assert status["seq"] >= frame_count

        service.stop_stream()
        service.close_camera()
        service.shutdown_sidecar()
        shutdown_sent = True
    finally:
        stream.close()
        if not shutdown_sent:
            try:
                service.stop_stream()
            except Exception:
                pass
            try:
                service.close_camera()
            except Exception:
                pass
        service.close()
