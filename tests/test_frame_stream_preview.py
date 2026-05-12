from __future__ import annotations

import numpy as np

from devices.frame_stream import FrameStreamClient


class FakeSharedMemory:
    def __init__(self, data: bytes) -> None:
        self.buf = bytearray(data)


def test_raw8_preview_with_unknown_bayer_metadata_uses_default_bayer() -> None:
    meta = {
        "index": 0,
        "width": 2,
        "height": 2,
        "stride": 2,
        "format": "raw8",
        "bayer_pattern": "UNKNOWN",
    }

    raw, preview = FrameStreamClient._decode_from_meta(
        FakeSharedMemory(bytes([1, 2, 3, 4])),
        meta,
        default_bayer_pattern=None,
    )

    assert raw.tolist() == [[1, 2], [3, 4]]
    assert preview.shape == (2, 2, 3)
    assert preview.dtype == np.uint8
    assert "mono fallback" not in meta.get("preview_warning", "")
    assert "default BayerGB" in meta.get("preview_warning", "")


def test_raw16_preview_without_bayer_metadata_uses_default_bayer() -> None:
    raw16 = np.array([[0, 256], [512, 1024]], dtype=np.uint16)
    meta = {
        "index": 0,
        "width": 2,
        "height": 2,
        "stride": 4,
        "format": "raw16",
    }

    raw, preview = FrameStreamClient._decode_from_meta(
        FakeSharedMemory(raw16.tobytes()),
        meta,
        default_bayer_pattern=None,
    )

    assert np.array_equal(raw, raw16)
    assert preview.shape == (2, 2, 3)
    assert preview.dtype == np.uint8
    assert "default BayerGB" in meta["preview_warning"]
