"""Offline CLI and file-handoff acceptance using the existing synthetic fixture."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

import h5py
import numpy as np
import pytest


ROOT = Path(__file__).resolve().parents[1]


def run_cli(script, *args, cwd, blocked=False):
    # Fail even an attempted device/SDK import, independently of installed SDKs.
    code = """
import importlib.abc, runpy, sys
class NoHardware(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'devices', 'capture', 'gui', 'control', 'flycapture2_c', 'tls_c1', 'zmq', 'pygame', 'cv2'}:
            raise RuntimeError('Offline command attempted hardware import: ' + fullname)
sys.meta_path.insert(0, NoHardware())
sys.argv = sys.argv[1:]
runpy.run_path(sys.argv[0], run_name='__main__')
"""
    command = [sys.executable, "-X", "utf8"]
    if blocked:
        command += ["-c", code]
    command += [str(ROOT / "scripts" / script), *map(str, args)]
    return subprocess.run(command, cwd=cwd, text=True, encoding="utf-8", capture_output=True, timeout=60)


def convert(run, output, cwd, blocked=True):
    return run_cli(
        "build_psf_survey.py", "--raw", run / "raw.h5",
        "--pupil-profile", run / "profiles/pupil.json",
        "--camera-profile", run / "profiles/camera.json",
        "--output", output, cwd=cwd, blocked=blocked,
    )


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


@pytest.fixture
def delivered(measured, tmp_path):
    raw, survey, base, kwargs, plan, *_ = measured
    run = tmp_path / "received measurements" / "run_fixture"
    (run / "profiles").mkdir(parents=True)
    shutil.copy2(raw, run / "raw.h5")
    for name in ("pupil", "camera"):
        shutil.copy2(tmp_path / f"{name}.json", run / "profiles" / f"{name}.json")
    plan.to_json(run / "plan.json")
    return run, raw, survey, base


def test_offline_clis_process_delivered_data_without_any_device_import(delivered, tmp_path):
    run, raw, original_survey, base = delivered
    # Paths with spaces, unrelated CWD, and no original source dependency.
    work = tmp_path / "unrelated working directory"
    work.mkdir()
    original_hash, delivered_hash = digest(raw), digest(run / "raw.h5")
    inspect = run_cli("inspect_raw_capture.py", run / "raw.h5", "--require-success", cwd=work, blocked=True)
    assert inspect.returncode == 0, inspect.stderr
    summary = json.loads(inspect.stdout)
    assert summary["capture_succeeded"] and summary["completed_rows"] == summary["expected_rows"] == 4
    output = run / "reports/survey.h5"
    result = convert(run, output, work)
    assert result.returncode == 0, result.stderr
    with h5py.File(output) as copied, h5py.File(original_survey) as original:
        np.testing.assert_array_equal(copied["full_frame_survey/frames_avg"], original["full_frame_survey/frames_avg"])
        assert copied.attrs["schema_version"] == 1
    center = run / "reports/center.json"
    result = run_cli("estimate_sensor_center.py", "--survey", output, "--output", center, cwd=work, blocked=True)
    assert result.returncode == 0, result.stderr
    profile = json.loads(center.read_text())
    assert profile["per_entry_total_corr_energy"] == [45, 90, 135, 180]
    # Center-origin selection and explicit sensor selection both use unmodified amplitude.
    centered = run / "reports/centered.npz"
    result = run_cli("extract_roi_psf.py", "--survey", output, "--wavelength-index", 1,
        "--display-index", 0, "--center-profile", center, "--roi-shape", 3, 3,
        "--output", centered, cwd=work, blocked=True)
    assert result.returncode == 0, result.stderr
    explicit = run / "reports/explicit.npz"
    result = run_cli("extract_roi_psf.py", "--survey", output, "--wavelength-index", 1,
        "--display-index", 0, "--roi", 4, 3, 3, 3, "--output", explicit, cwd=work, blocked=True)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["total_intensity"] == 135
    with np.load(explicit, allow_pickle=False) as data, np.load(centered, allow_pickle=False) as other:
        np.testing.assert_array_equal(data["psf"], base[3:6, 4:7] * 3)
        np.testing.assert_array_equal(data["psf"], other["psf"])
        meta = json.loads(str(data["metadata_json"]))
        assert meta["normalization"] == "none" and meta["roi_xywh"] == [4, 3, 3, 3]
        assert meta["wavelength_nm"] == 650 and meta["display_state_id"] == "fixed"
        assert meta["camera_settings"]["readback_exposure_us"] == 200
        assert data["psf"].sum() == 135
    assert digest(raw) == original_hash == delivered_hash == digest(run / "raw.h5")
    again = convert(run, output, work)
    assert again.returncode == 2 and "already exists" in again.stderr
    assert run_cli("estimate_sensor_center.py", "--survey", output, "--output", center, cwd=work).returncode == 2
    assert run_cli("extract_roi_psf.py", "--survey", output, "--wavelength-index", 0,
        "--display-index", 0, "--roi", 0, 0, 1, 1, "--output", explicit, cwd=work).returncode == 2


@pytest.mark.parametrize("damage", ["failed", "incomplete", "dimensions", "wrong_profile"])
def test_invalid_deliveries_cannot_become_formal_surveys(delivered, tmp_path, damage):
    run, *_ = delivered
    if damage == "wrong_profile":
        path = run / "profiles/pupil.json"
        data = json.loads(path.read_text())
        data["pupil_profile_id"] = "different-pupil"
        path.write_text(json.dumps(data))
    else:
        with h5py.File(run / "raw.h5", "r+") as f:
            if damage == "incomplete":
                # Even stale success flags cannot make an uncommitted row valid.
                f["capture/completed"][0] = False
            elif damage == "dimensions":
                del f["capture/mask_index"]
                f["capture"].create_dataset("mask_index", data=[0])
            else:
                flags = json.loads(f["capture/processing_flags_json"][()].decode())
                flags["run_succeeded"] = False
                f["capture/processing_flags_json"][()] = json.dumps(flags)
        inspect = run_cli("inspect_raw_capture.py", run / "raw.h5", "--require-success", cwd=tmp_path, blocked=True)
        assert inspect.returncode == 2 and not json.loads(inspect.stdout)["capture_succeeded"]
    output = run / "reports/rejected.h5"
    result = convert(run, output, tmp_path)
    assert result.returncode == 2 and "Survey conversion failed" in result.stderr
    assert not output.exists()
    assert (run / "raw.h5").exists()


def test_offline_cli_invalid_paths_and_geometry(delivered, tmp_path):
    run, *_ = delivered
    output = run / "reports/new.h5"
    (run / "profiles/camera.json").unlink()  # Synthetic fixture only.
    result = convert(run, output, tmp_path)
    assert result.returncode == 2 and not output.exists()
    result = run_cli("inspect_raw_capture.py", run / "missing.h5", cwd=tmp_path)
    assert result.returncode == 2
    result = run_cli("extract_roi_psf.py", "--survey", tmp_path / "survey.h5",
        "--wavelength-index", 0, "--display-index", 0, "--roi", 0, 0, 3, 3,
        "--roi-shape", 3, 3, "--output", tmp_path / "bad.npz", cwd=tmp_path)
    assert result.returncode == 2 and "--roi-shape" in result.stderr


@pytest.fixture
def powershell():
    if sys.platform != "win32":
        pytest.skip("Windows/SMB copy helper")
    executable = shutil.which("pwsh") or shutil.which("powershell")
    if not executable:
        pytest.skip("PowerShell is unavailable")
    return executable


def copy_file(powershell, source, destination, expected):
    return subprocess.run([powershell, "-NoProfile", "-File", str(ROOT / "scripts/copy_measurement_file.ps1"),
        "-Source", str(source), "-Destination", str(destination), "-ExpectedSha256", expected,
        "-Attempts", "2", "-RetrySeconds", "0"], text=True, encoding="utf-8", errors="replace", capture_output=True, timeout=30)


def test_verified_copy_repeat_and_corruption_protection(measured, tmp_path, powershell):
    source = measured[0]
    expected = digest(source)
    original_mtime = source.stat().st_mtime_ns
    target = tmp_path / "receiver with spaces/raw.h5"
    result = copy_file(powershell, source, target, expected)
    assert result.returncode == 0, result.stderr
    assert digest(target) == expected
    target_mtime = target.stat().st_mtime_ns
    result = copy_file(powershell, source, target, expected)
    assert result.returncode == 0 and "Verified existing" in result.stdout
    assert target.stat().st_mtime_ns == target_mtime
    target.write_bytes(b"corrupt delivered file")
    result = copy_file(powershell, source, target, expected)
    assert result.returncode != 0 and "refusing to overwrite" in result.stderr
    assert target.read_bytes() == b"corrupt delivered file"
    bad = tmp_path / "receiver with spaces/bad.h5"
    result = copy_file(powershell, source, bad, "0" * 64)
    assert result.returncode != 0 and "Source SHA-256" in result.stderr
    assert "Copy attempt 1 failed" in result.stdout and not bad.exists()
    assert digest(source) == expected and source.stat().st_mtime_ns == original_mtime
    assert not list(target.parent.glob("*.partial"))


def test_copy_refuses_a_source_open_for_writing(measured, tmp_path, powershell):
    source = measured[0]
    target = tmp_path / "must-not-exist.h5"
    check = tmp_path / "held-writer.ps1"
    check.write_text("""
param($Source, $Destination, $Hash, $Helper)
$held = [IO.File]::Open($Source, [IO.FileMode]::Open, [IO.FileAccess]::Write, [IO.FileShare]::Read)
try {
    try {
        & $Helper -Source $Source -Destination $Destination -ExpectedSha256 $Hash -Attempts 1
        exit 1
    } catch { exit 0 }
} finally { $held.Dispose() }
""", encoding="utf-8")
    result = subprocess.run([powershell, "-NoProfile", "-File", str(check), "-Source", str(source),
        "-Destination", str(target), "-Hash", digest(source), "-Helper", str(ROOT / "scripts/copy_measurement_file.ps1")],
        text=True, encoding="utf-8", errors="replace", capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert not target.exists()
