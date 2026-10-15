import hashlib
import json
from types import SimpleNamespace

import numpy as np
import pytest
import zmq

from devices import camera_service_impl as impl
from devices.camera_frame_layout import build_frame_metadata, frame_layout_from_array
from devices.frame_stream import FrameStreamClient


class SharedBuffer:
    def __init__(self, size):
        self.buf = bytearray(size)


class Notifications:
    def __init__(self, messages):
        self.messages = list(messages)

    def poll(self, timeout):
        return timeout != 0 and bool(self.messages)

    def recv_multipart(self):
        return b'frame', json.dumps(self.messages.pop(0)).encode()

    def close(self, *args):
        pass


def metadata(raw, seq=0):
    meta = build_frame_metadata(frame_layout_from_array(raw, pixel_format='MONO8'), index=0, seq=seq)
    meta['frame_sha256'] = hashlib.sha256(raw).hexdigest()
    return meta


def reader(monkeypatch, raw, messages):
    client = FrameStreamClient(recv_timeout_ms=1000)
    client._sub = Notifications(messages)
    shm = SharedBuffer(raw.nbytes)
    shm.buf[:] = raw.tobytes()
    client._shm = shm
    monkeypatch.setattr(client, '_ensure_sub', lambda: None)
    monkeypatch.setattr(client, '_ensure_shm', lambda name: None)
    return client


def test_reused_ring_slot_never_pairs_new_pixels_with_old_metadata(monkeypatch):
    old = np.full((4, 4), 1, np.uint8)
    current = np.full((4, 4), 9, np.uint8)
    client = reader(monkeypatch, current, [metadata(old, 0), metadata(current, 8)])
    packet = client.recv_frame()
    assert packet.meta['seq'] == 8
    assert packet.meta['stale_notifications_discarded'] == 1
    np.testing.assert_array_equal(packet.raw, current)


def test_overwrite_during_copy_is_rejected(monkeypatch):
    raw = np.arange(16, dtype=np.uint8).reshape(4, 4)
    client = reader(monkeypatch, raw, [metadata(raw, 1), metadata(raw, 2)])
    decode = client._decode_from_meta
    calls = []

    def interrupted_copy(*args, **kwargs):
        copy, preview = decode(*args, **kwargs)
        if not calls:
            copy[0, 0] = 255
        calls.append(True)
        return copy, preview

    monkeypatch.setattr(client, '_decode_from_meta', interrupted_copy)
    packet = client.recv_frame()
    assert packet.meta['seq'] == 2
    np.testing.assert_array_equal(packet.raw, raw)


def test_continuously_stale_notifications_respect_timeout(monkeypatch):
    raw = np.zeros((4, 4), np.uint8)
    client = reader(monkeypatch, raw, [metadata(raw + 1)])
    ticks = iter([0, 0, 2])
    monkeypatch.setattr('devices.frame_stream.time.monotonic', lambda: next(ticks))
    with pytest.raises(zmq.Again):
        client.recv_frame()
    assert client._last_packet is None


def test_pause_does_not_replay_queued_display_state_notifications(monkeypatch):
    raw = np.zeros((4, 4), np.uint8)
    client = reader(monkeypatch, raw, [metadata(raw, 1), metadata(raw, 2), metadata(raw, 3)])
    monkeypatch.setattr(client._sub, 'poll', lambda timeout: bool(client._sub.messages))
    packet = client.recv_frame()
    assert packet.meta['seq'] == 3
    assert packet.meta['stale_notifications_discarded'] == 2


def test_draining_does_not_hide_sidecar_errors(monkeypatch):
    raw = np.zeros((4, 4), np.uint8)
    client = reader(monkeypatch, raw, [])
    messages = [(b'frame', json.dumps(metadata(raw)).encode()),
                (b'status', json.dumps({'err': 'camera disconnected'}).encode())]
    monkeypatch.setattr(client._sub, 'poll', lambda timeout: bool(messages))
    monkeypatch.setattr(client._sub, 'recv_multipart', lambda: messages.pop(0))
    with pytest.raises(RuntimeError, match='camera disconnected'):
        client.recv_frame()
    assert client._last_packet is None


def test_zero_timeout_can_read_an_available_verified_frame(monkeypatch):
    raw = np.zeros((4, 4), np.uint8)
    client = reader(monkeypatch, raw, [metadata(raw)])
    client.recv_timeout_ms = 0
    monkeypatch.setattr(client._sub, 'poll', lambda timeout: bool(client._sub.messages))
    np.testing.assert_array_equal(client.recv_frame().raw, raw)


@pytest.mark.parametrize('change,message', [
    ({'protocol_version': 2}, 'Unsupported camera frame protocol'),
    ({'frame_sha256': None}, 'frame_sha256'),
])
def test_unverified_protocol_is_rejected(monkeypatch, change, message):
    raw = np.zeros((4, 4), np.uint8)
    meta = metadata(raw);meta.update(change)
    client = reader(monkeypatch, raw, [meta])
    with pytest.raises(RuntimeError, match=message):
        client.recv_frame()


@pytest.mark.parametrize('shape,dtype,fmt', [
    ((4, 4), np.uint8, 'RAW8'),
    ((4, 4), np.uint16, 'RAW16'),
    ((4, 4, 3), np.uint8, 'RGB8'),
])
def test_sidecar_publishes_digest_of_exact_written_bytes(shape, dtype, fmt):
    raw = np.arange(np.prod(shape), dtype=dtype).reshape(shape)
    layout = frame_layout_from_array(raw, pixel_format=fmt)
    state = impl.CameraServiceState()
    state.running = True
    state.layout = layout
    state.shm = SharedBuffer(layout.frame_nbytes * impl.RING)
    state.cam = SimpleNamespace(capture=lambda: (raw, SimpleNamespace(), layout))
    published = []

    class Publisher:
        def send_multipart(self, parts):
            published.append(json.loads(parts[1]))
            state.stop_event.set()

    impl.stream_loop(state, Publisher())
    assert len(published) == 1
    meta = published[0]
    assert meta['protocol_version'] == 3
    assert meta['frame_sha256'] == hashlib.sha256(state.shm.buf[:raw.nbytes]).hexdigest()
