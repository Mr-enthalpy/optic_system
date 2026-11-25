"""Shared normalization must retain signed residuals and avoid source mutation."""
from pathlib import Path
import subprocess
import sys
import numpy as np
import pytest

from tasks.measurement.intensity import relative_response


def test_signed_normalization_preserves_source_and_linear_scaling():
    observed = np.array([[-2., 4.], [1., 5.]])
    original = observed.copy()
    result = relative_response(observed, exposure_us=20, normalize_sum=True)
    np.testing.assert_allclose(result, observed / 8)
    np.testing.assert_array_equal(observed, original)
    assert result[0, 0] < 0
    np.testing.assert_array_equal(relative_response(observed, exposure_us=20), observed / 20)
    assert not np.shares_memory(relative_response(observed), observed)


@pytest.mark.parametrize('values', [np.zeros((2, 2)), np.full((2, 2), -1.), [[np.nan]], [[np.inf]]])
def test_rejects_undefined_unit_sum(values):
    with pytest.raises(ValueError, match='positive finite'):
        relative_response(values, normalize_sum=True)


def test_normalization_import_has_no_hardware_or_hdf5_dependency():
    code = "import sys; sys.modules['h5py']=None; from tasks.measurement.intensity import relative_response; assert 'devices' not in sys.modules; assert 'tasks.psf' not in sys.modules"
    result = subprocess.run([sys.executable, '-c', code], cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
