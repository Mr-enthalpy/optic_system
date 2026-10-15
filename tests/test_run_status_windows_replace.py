import errno
import json

import numpy as np
import pytest

from diagnostics import run_status as status


@pytest.mark.parametrize('kind', ['json', 'npy', 'image'])
def test_brief_windows_reader_lock_does_not_abort_status_publish(tmp_path, monkeypatch, kind):
    path = tmp_path / {'json': 'state.json', 'npy': 'preview.npy', 'image': 'preview.png'}[kind]
    path.write_bytes(b'previous complete file')
    replace = status.os.replace
    calls = []
    waits = []

    def briefly_locked(source, destination):
        calls.append(destination)
        if len(calls) == 1:
            assert path.read_bytes() == b'previous complete file'
            error = PermissionError('reader holds destination')
            error.winerror = 32
            raise error
        replace(source, destination)

    monkeypatch.setattr(status.os, 'replace', briefly_locked)
    monkeypatch.setattr(status.time, 'sleep', waits.append)
    array = np.arange(9, dtype=np.uint8).reshape(3, 3)
    if kind == 'json':
        status._atomic_write_text(path, '{"phase":"live"}')
        assert json.loads(path.read_text()) == {'phase': 'live'}
    elif kind == 'npy':
        status._atomic_save_npy(path, array)
        np.testing.assert_array_equal(np.load(path), array)
    else:
        status._atomic_write_image(path, array)
        np.testing.assert_array_equal(status._read_image(path), array)
    assert len(calls) == 2
    assert waits
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize('winerror', [5, None])
def test_persistent_or_unrelated_write_errors_remain_visible(tmp_path, monkeypatch, winerror):
    path = tmp_path / 'state.json'
    path.write_text('previous complete file')
    calls = []
    waits = []

    def always_fails(*args):
        calls.append(True)
        error = OSError(errno.ENOSPC, 'persistent write failure')
        if winerror is not None:
            error.winerror = winerror
        raise error

    monkeypatch.setattr(status.os, 'replace', always_fails)
    monkeypatch.setattr(status.time, 'sleep', waits.append)
    with pytest.raises(OSError, match='persistent write failure'):
        status._atomic_write_text(path, 'new content')
    assert path.read_text() == 'previous complete file'
    assert list(tmp_path.iterdir()) == [path]
    if winerror is None:
        assert len(calls) == 1
        assert not waits
    else:
        assert 1 < len(calls) <= 5
        assert sum(waits) <= .2
