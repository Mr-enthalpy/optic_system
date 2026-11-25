"""Synthetic HDR fusion directory tree; no phone data or optical claim."""
import json
from pathlib import Path

import numpy as np

from scripts.fuse_udc_hdr_psf import main


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def add_batch(session, index, *, condition, exposure_ns, mean, roi_xywh, verified=True):
    directory = session / f"batch_{index:03d}"
    decoded = directory / "decoded"
    decoded.mkdir(parents=True)
    np.save(decoded / "temporal_mean_dn.npy", mean.astype(np.float64), allow_pickle=False)
    run_id = f"run_{index}_00000000"
    write(decoded / "summary.json", {"capture_complete": True, "run_succeeded": True, "run_id": run_id})
    run_dir = directory / "export/device_files/captures" / run_id
    write(run_dir / "run.json", {
        "run_id": run_id, "condition": condition, "condition_verified_by_operator": verified,
        "requested_exposure_ns": exposure_ns, "requested_iso": 100,
        "requested_shading_mode": "off", "requested_tonemap_mode": "high_quality",
        "requested_display_panel": "black", "requested_companion_size": "preview"})
    write(run_dir / "characteristics.json", {"values": {
        "android.sensor.info.whiteLevel": 1023, "android.sensor.blackLevelPattern": [64, 64, 64, 64]}})


def test_fusion_subtracts_far_ring_background_and_normalizes(tmp_path):
    roi_xywh = [0, 0, 8, 8]
    exposures = [1000, 25000, 100000]
    background = np.ones((8, 8))
    background[4, 4] = 101.0
    session = tmp_path / "session"
    for band_index, wavelength in enumerate((400, 500)):
        for role, exposure in enumerate(exposures):
            raw = 64.0 + background * (exposure / exposures[0])
            add_batch(session, band_index * 3 + role, condition="point", exposure_ns=exposure,
                      mean=raw, roi_xywh=roi_xywh)
    for role, exposure in enumerate(exposures):
        add_batch(session, 6 + role, condition="dark", exposure_ns=exposure,
                  mean=np.full((8, 8), 64.0), roi_xywh=roi_xywh)
    manifest = {"schema_version": 1, "session_id": "synthetic", "roi_xywh": roi_xywh,
                "darks": [{"exposure_ns": e, "batches": [6 + i]} for i, e in enumerate(exposures)],
                "bands": [{"wavelength_nm": w, "batches": [
                    {"batch_index": b * 3 + i, "exposure_ns": e} for i, e in enumerate(exposures)]}
                    for b, w in enumerate((400, 500))]}
    write(tmp_path / "manifest.json", manifest)
    output = tmp_path / "psf_hdr.npz"
    diagnostics = tmp_path / "diagnostics.json"
    assert main(["--session", str(session), "--manifest", str(tmp_path / "manifest.json"),
                 "--output", str(output), "--diagnostics", str(diagnostics), "--ring-width", "2"]) == 0
    with np.load(output, allow_pickle=False) as data:
        kernels = data["psf_unit_sum"]
        native = data["psf_unit_sum_native"]
        assert kernels.shape == (2, 4, 4) and native.shape == (2, 8, 8)
        np.testing.assert_allclose(kernels.sum(axis=(1, 2)), 1.0, rtol=1e-12)
        np.testing.assert_allclose(native.sum(axis=(1, 2)), 1.0, rtol=1e-12)
        np.testing.assert_allclose(kernels[:, 2, 2], 1.0, rtol=1e-10, atol=1e-12)
        np.testing.assert_allclose(kernels[:, 0, 0], 0.0, atol=1e-12)
        np.testing.assert_allclose(native[:, 4, 4], 1.0, rtol=1e-10)
        np.testing.assert_array_equal(data["roi_xywh"], roi_xywh)
        assert data["background_dn"].tolist() == [1.0, 1.0]
    report = json.loads(diagnostics.read_text())
    assert report["metadata"]["matched_dark_applied"] is True
    assert report["metadata"]["nonnegative_physical_kernel_verified"] is False
