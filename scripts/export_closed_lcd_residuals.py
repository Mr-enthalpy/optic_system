#!/usr/bin/env python3
"""Export Phase 3.4 closed-LCD averaged-frame ROI residuals."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import h5py
import numpy as np


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _ensure_sys_path() -> None:
    root = str(_repo_root())
    if root not in sys.path:
        sys.path.insert(0, root)


_ensure_sys_path()

from tasks.psf_phase3 import json_dumps  # noqa: E402


RELEASE_TYPE = "closed_lcd_avg10_roi_residual"
DEFAULT_RELEASE_NAME = "optic_system_phase3_closed_lcd_residual_release_20260523"
FORBIDDEN_CLAIMS = [
    "real camera noise",
    "read noise",
    "sensor-only dark",
    "complete noise model",
]


def export_closed_lcd_residual_release(
    *,
    source: str | Path,
    release_dir: str | Path,
    release_name: str = DEFAULT_RELEASE_NAME,
    mask_id: str = "all_closed_window",
    roi: str = "roi_512",
    residual_crop_size: int = 256,
) -> dict[str, Any]:
    source_path = _resolve_path(source)
    release_path = _resolve_path(release_dir)
    release_path.mkdir(parents=True, exist_ok=True)

    extracted = _extract_closed_lcd(source_path=source_path, mask_id=mask_id, roi=roi)
    h5_path = release_path / "closed_lcd_roi512_avg10_residuals.h5"
    csv_path = release_path / "closed_lcd_roi512_avg10_stats.csv"
    figures_dir = release_path / "figures"
    provenance_dir = release_path / "provenance"
    scripts_dir = release_path / "scripts"
    figures_dir.mkdir(exist_ok=True)
    provenance_dir.mkdir(exist_ok=True)
    scripts_dir.mkdir(exist_ok=True)

    residuals = extracted["crops"] - extracted["mean"][:, np.newaxis, :, :]
    residuals_256 = _center_crop_stack(residuals, residual_crop_size)
    stats = _stats_rows(extracted=extracted, residuals=residuals)

    _write_residual_h5(
        h5_path,
        extracted=extracted,
        residuals=residuals,
        residuals_256=residuals_256,
        release_name=release_name,
        source_path=source_path,
        mask_id=mask_id,
        roi=roi,
    )
    _write_stats_csv(csv_path, stats)
    _write_figures(figures_dir, extracted=extracted, residuals=residuals)
    _write_docs(
        release_path,
        release_name=release_name,
        source_path=source_path,
        manifest=_manifest_summary(
            release_name=release_name,
            source_path=source_path,
            extracted=extracted,
            stats=stats,
            mask_id=mask_id,
            roi=roi,
        ),
    )
    _write_provenance(
        provenance_dir,
        release_name=release_name,
        source_path=source_path,
        extracted=extracted,
        mask_id=mask_id,
        roi=roi,
    )
    _copy_release_scripts(scripts_dir)
    manifest = _manifest_summary(
        release_name=release_name,
        source_path=source_path,
        extracted=extracted,
        stats=stats,
        mask_id=mask_id,
        roi=roi,
    )
    manifest["files"] = _file_records(release_path, exclude_names={"manifest.json"})
    (release_path / "manifest.json").write_text(json_dumps(manifest), encoding="utf-8")
    checksum = {
        "schema_version": 1,
        "release_name": release_name,
        "files": _file_records(release_path, exclude_names={"manifest.json", "checksum_manifest.json"}),
    }
    (provenance_dir / "checksum_manifest.json").write_text(json_dumps(checksum), encoding="utf-8")
    manifest["files"] = _file_records(release_path, exclude_names={"manifest.json"})
    (release_path / "manifest.json").write_text(json_dumps(manifest), encoding="utf-8")
    return manifest


def _extract_closed_lcd(*, source_path: Path, mask_id: str, roi: str) -> dict[str, Any]:
    with h5py.File(str(source_path), "r") as f:
        required = [
            "raw/crops",
            "raw/mask_id",
            "raw/wavelength_nm",
            "raw/wavelength_index",
            "raw/repeat_index",
            "raw/exposure_us",
            "raw/gain_db",
            "capture/plan_json",
            "provenance/psf_roi_source_json",
        ]
        for path in required:
            if path not in f:
                raise ValueError(f"source HDF5 missing required dataset: {path}")
        mask_ids = np.asarray([_decode(x) for x in f["raw/mask_id"][()]], dtype=object)
        wavelength_nm = np.asarray(f["raw/wavelength_nm"][()], dtype=np.float64)
        wavelength_index = np.asarray(f["raw/wavelength_index"][()], dtype=np.int64)
        repeat_index = np.asarray(f["raw/repeat_index"][()], dtype=np.int64)
        exposure_us = np.asarray(f["raw/exposure_us"][()], dtype=np.float64)
        gain_db = np.asarray(f["raw/gain_db"][()], dtype=np.float64)
        plan = _require_json_dataset(f, "capture/plan_json")
        psf_roi = _require_json_dataset(f, "provenance/psf_roi_source_json")
        camera_metadata = _optional_json_dataset(f, "camera/metadata_json")
        lcd_metadata = _optional_json_dataset(f, "lcd/metadata_json")
        tls_metadata = _optional_json_dataset(f, "tls/metadata_json")

        if str(plan.get("phase")) != "3.4":
            raise ValueError("source HDF5 must be a Phase 3.4 PSF dictionary capture")
        if str(plan.get("psf_roi_key")) != roi:
            raise ValueError(f"source plan psf_roi_key must be {roi!r}, got {plan.get('psf_roi_key')!r}")
        n_avg_frames = int(plan.get("capture", {}).get("frames_per_capture", 0))
        if n_avg_frames <= 0:
            raise ValueError("capture.frames_per_capture must be present and > 0")

        selected_mask = mask_ids == mask_id
        if not bool(np.any(selected_mask)):
            raise ValueError(f"mask_id not found in source: {mask_id}")
        ordered_wl_idx = list(dict.fromkeys(int(x) for x in wavelength_index[selected_mask].tolist()))
        crops_by_wl = []
        repeats_by_wl = []
        wavelengths = []
        exposures = []
        gains = []
        for wl_idx in ordered_wl_idx:
            idx = np.where(selected_mask & (wavelength_index == wl_idx))[0]
            if idx.size == 0:
                raise ValueError(f"missing closed-LCD captures for wavelength_index={wl_idx}")
            order = np.argsort(repeat_index[idx])
            idx = idx[order]
            reps = repeat_index[idx]
            if len(set(int(x) for x in reps.tolist())) != idx.size:
                raise ValueError(f"duplicate repeat_index for wavelength_index={wl_idx}")
            wl_unique = np.unique(wavelength_nm[idx])
            exp_unique = np.unique(exposure_us[idx])
            gain_unique = np.unique(gain_db[idx])
            if wl_unique.size != 1 or exp_unique.size != 1 or gain_unique.size != 1:
                raise ValueError(f"wavelength/exposure/gain is not constant for wavelength_index={wl_idx}")
            crops_by_wl.append(np.asarray(f["raw/crops"][idx], dtype=np.float64))
            repeats_by_wl.append([int(x) for x in reps.tolist()])
            wavelengths.append(float(wl_unique[0]))
            exposures.append(float(exp_unique[0]))
            gains.append(float(gain_unique[0]))

    crops = np.stack(crops_by_wl, axis=0)
    if crops.ndim != 4:
        raise ValueError(f"closed-LCD crops must be [L,R,H,W], got {crops.shape}")
    if len(set(tuple(x) for x in repeats_by_wl)) != 1:
        raise ValueError("repeat indices must match across wavelengths")
    mean = np.mean(crops, axis=1)
    std = np.std(crops, axis=1)
    return {
        "crops": crops,
        "mean": mean,
        "std": std,
        "wavelengths_nm": wavelengths,
        "exposure_us": exposures,
        "gain_db": gains,
        "repeat_indices": repeats_by_wl[0],
        "n_repeats": int(crops.shape[1]),
        "n_avg_frames": n_avg_frames,
        "roi_shape": [int(crops.shape[-2]), int(crops.shape[-1])],
        "plan": plan,
        "psf_roi": psf_roi,
        "camera_metadata": camera_metadata,
        "lcd_metadata": lcd_metadata,
        "tls_metadata": tls_metadata,
    }


def _write_residual_h5(
    path: Path,
    *,
    extracted: dict[str, Any],
    residuals: np.ndarray,
    residuals_256: np.ndarray | None,
    release_name: str,
    source_path: Path,
    mask_id: str,
    roi: str,
) -> None:
    string_dtype = h5py.string_dtype(encoding="utf-8")
    with h5py.File(str(path), "w") as f:
        f.attrs["release_name"] = release_name
        f.attrs["release_type"] = RELEASE_TYPE
        f.attrs["created_at_unix"] = time.time()
        closed = f.require_group("closed_lcd")
        closed.create_dataset("crops_avg10", data=np.asarray(extracted["crops"], dtype=np.float32), compression="gzip", compression_opts=4)
        closed.create_dataset("mean_avg10", data=np.asarray(extracted["mean"], dtype=np.float32), compression="gzip", compression_opts=4)
        closed.create_dataset("residuals_avg10", data=np.asarray(residuals, dtype=np.float32), compression="gzip", compression_opts=4)
        closed.create_dataset("std_across_repeats", data=np.asarray(extracted["std"], dtype=np.float32), compression="gzip", compression_opts=4)
        if residuals_256 is not None:
            closed.create_dataset("residuals_256", data=np.asarray(residuals_256, dtype=np.float32), compression="gzip", compression_opts=4)

        metadata = f.require_group("metadata")
        metadata.create_dataset("wavelengths_nm", data=np.asarray(extracted["wavelengths_nm"], dtype=np.float64))
        metadata.create_dataset("exposure_us", data=np.asarray(extracted["exposure_us"], dtype=np.float64))
        metadata.create_dataset("gain_db", data=np.asarray(extracted["gain_db"], dtype=np.float64))
        metadata.create_dataset("n_repeats", data=int(extracted["n_repeats"]))
        metadata.create_dataset("n_avg_frames", data=int(extracted["n_avg_frames"]))
        metadata.create_dataset("roi_name", data=roi, dtype=string_dtype)
        metadata.create_dataset("roi_shape", data=np.asarray(extracted["roi_shape"], dtype=np.int64))
        metadata.create_dataset("source_mask_id", data=mask_id, dtype=string_dtype)
        metadata.create_dataset("is_sensor_dark", data=False)
        metadata.create_dataset("is_single_frame_burst", data=False)
        metadata.create_dataset("is_closed_lcd_residual", data=True)
        metadata.create_dataset(
            "recommended_use",
            data="averaged-frame additive residual for measured-PSF simulation",
            dtype=string_dtype,
        )
        metadata.create_dataset("forbidden_claims", data=np.asarray(FORBIDDEN_CLAIMS, dtype=object), dtype=string_dtype)
        metadata.create_dataset("source_psf_dictionary", data=str(source_path), dtype=string_dtype)
        metadata.create_dataset("git_commit", data=str(_git_commit()), dtype=string_dtype)

        provenance = f.require_group("provenance")
        provenance.create_dataset("source_plan_json", data=json_dumps(extracted["plan"]), dtype=string_dtype)
        provenance.create_dataset("source_psf_roi_json", data=json_dumps(extracted["psf_roi"]), dtype=string_dtype)
        provenance.create_dataset("source_camera_metadata_json", data=json_dumps(extracted["camera_metadata"]), dtype=string_dtype)
        provenance.create_dataset("source_lcd_metadata_json", data=json_dumps(extracted["lcd_metadata"]), dtype=string_dtype)
        provenance.create_dataset("source_tls_metadata_json", data=json_dumps(extracted["tls_metadata"]), dtype=string_dtype)


def _write_stats_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = [
        "wavelength_nm",
        "exposure_us",
        "gain_db",
        "repeats",
        "avg_frames",
        "mean_count",
        "residual_std_mean",
        "residual_std_p95",
        "residual_abs_p95",
        "residual_min",
        "residual_max",
    ]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row[key] for key in fields})


def _write_figures(figures_dir: Path, *, extracted: dict[str, Any], residuals: np.ndarray) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    wavelengths = extracted["wavelengths_nm"]
    mean = extracted["mean"]
    std = extracted["std"]
    for i, wl in enumerate(wavelengths):
        wl_key = _wl_key(wl)
        _save_image(figures_dir / f"closed_lcd_mean_{wl_key}.png", mean[i], cmap="gray", title=f"{wl:.0f} nm mean")
        _save_image(figures_dir / f"closed_lcd_residual_std_{wl_key}.png", std[i], cmap="magma", title=f"{wl:.0f} nm residual std")

    fig, ax = plt.subplots(figsize=(7, 4), dpi=160)
    for i, wl in enumerate(wavelengths):
        values = residuals[i].reshape(-1)
        ax.hist(values, bins=120, histtype="step", linewidth=1.2, label=f"{wl:.0f} nm")
    ax.set_xlabel("Residual count")
    ax.set_ylabel("Pixels")
    ax.legend(frameon=False)
    fig.tight_layout()
    fig.savefig(figures_dir / "residual_histograms.png")
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(6, 4.5), dpi=160)
    for i, wl in enumerate(wavelengths):
        spectrum = _mean_power_spectrum(residuals[i])
        radial = _radial_profile(spectrum)
        ax.plot(radial, linewidth=1.2, label=f"{wl:.0f} nm")
    ax.set_xlabel("Spatial frequency radius (px)")
    ax.set_ylabel("Mean log power")
    ax.legend(frameon=False)
    fig.tight_layout()
    fig.savefig(figures_dir / "residual_power_spectrum.png")
    plt.close(fig)


def _save_image(path: Path, image: np.ndarray, *, cmap: str, title: str) -> None:
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(4.2, 4.0), dpi=160)
    im = ax.imshow(image, cmap=cmap)
    ax.set_title(title)
    ax.set_xticks([])
    ax.set_yticks([])
    fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def _write_docs(release_path: Path, *, release_name: str, source_path: Path, manifest: dict[str, Any]) -> None:
    stats_lines = [
        "wavelength_nm | exposure_us | repeats | avg_frames | mean_count | residual_std_mean | residual_std_p95",
        "--- | ---: | ---: | ---: | ---: | ---: | ---:",
    ]
    for row in manifest["statistics"]:
        stats_lines.append(
            f"{row['wavelength_nm']:.0f} | {row['exposure_us']:.4f} | {row['repeats']} | {row['avg_frames']} | "
            f"{row['mean_count']:.6f} | {row['residual_std_mean']:.6f} | {row['residual_std_p95']:.6f}"
        )
    dataset_table = "\n".join(stats_lines)
    (release_path / "README.md").write_text(
        "\n".join(
            [
                f"# {release_name}",
                "",
                "This release provides exposure-matched closed-LCD ROI residuals extracted from the Phase 3.4 measured PSF dictionary acquisition pipeline. Each residual sample corresponds to a 10-frame averaged roi_512 crop under the all_closed_window mask.",
                "",
                "The release is intended for averaged-frame residual injection in measured-PSF-driven simulation. It should not be interpreted as a complete real sensor noise model.",
                "",
                "The release does not provide single-frame burst noise, read noise decomposition, shot noise estimation, PRNU, flat-field calibration, or sensor-only dark characterization.",
                "",
                "本发布包提供与 Phase 3.4 三波长 measured PSF dictionary 曝光条件匹配的闭合 LCD ROI 残差。每个样本为 all_closed_window 条件下 10 帧平均后的 roi_512 crop。该数据适合用于当前平均采集流程下的经验有效背景扰动注入，不应解释为完整真实传感器噪声模型。",
                "",
                "Core file: `closed_lcd_roi512_avg10_residuals.h5`.",
                "",
                "The all_closed_window sample appears in one split file as a normal mask entry due to the original dictionary split procedure. In this residual release it is reclassified as a closed-LCD residual source and should be excluded from forward-validation and reconstruction mask sequences.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (release_path / "DATASET.md").write_text(
        "\n".join(
            [
                "# Dataset",
                "",
                f"Source PSF dictionary: `{source_path}`",
                "",
                "HDF5 contract:",
                "",
                "- `/closed_lcd/crops_avg10`: `[L, R, H, W]`, camera counts.",
                "- `/closed_lcd/mean_avg10`: `[L, H, W]`, per-wavelength closed-LCD mean.",
                "- `/closed_lcd/residuals_avg10`: `[L, R, H, W]`, `crops_avg10 - mean_avg10`.",
                "- `/closed_lcd/std_across_repeats`: `[L, H, W]`, pixelwise repeat std.",
                "- `/closed_lcd/residuals_256`: optional centered `[L, R, 256, 256]` residual crop.",
                "",
                dataset_table,
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (release_path / "LIMITATIONS.md").write_text(
        "\n".join(
            [
                "# Limitations",
                "",
                "1. No burst single frames are available, so this release cannot estimate single-frame read noise.",
                "2. No exposure-matched full-frame dark is available, so this release does not provide three-wavelength full-frame sensor dark data.",
                "3. `all_closed_window` is closed-LCD / optical-path dark-bottom data, not lens-cap or shutter-closed sensor-only dark.",
                "4. The release does not contain bright-field shot noise.",
                "5. The release does not contain PRNU or flat-field multiplicative noise.",
                "6. The release does not represent mask-dependent leakage under arbitrary masks.",
                "7. The release only corresponds to the current 10-frame averaged acquisition pipeline.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (release_path / "REPRODUCE.md").write_text(
        "\n".join(
            [
                "# Reproduce",
                "",
                "Export:",
                "",
                "```bash",
                "python scripts/export_closed_lcd_residuals.py \\",
                f"  --source {source_path.as_posix()} \\",
                "  --mask-id all_closed_window \\",
                "  --roi roi_512 \\",
                f"  --release-dir {release_name}",
                "```",
                "",
                "Validate:",
                "",
                "```bash",
                "python scripts/validate_closed_lcd_residual_release.py \\",
                f"  --release-dir {release_name}",
                "```",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (release_path / "CHANGELOG.md").write_text(
        f"# Changelog\n\n- 2026-05-23: Initial closed-LCD averaged-frame residual release.\n",
        encoding="utf-8",
    )


def _write_provenance(
    provenance_dir: Path,
    *,
    release_name: str,
    source_path: Path,
    extracted: dict[str, Any],
    mask_id: str,
    roi: str,
) -> None:
    source_sha = _sha256(source_path)
    source_files = {
        "release_name": release_name,
        "source_psf_dictionary": str(source_path),
        "source_psf_dictionary_sha256": source_sha,
        "source_mask": mask_id,
        "roi": roi,
    }
    (provenance_dir / "source_files.json").write_text(json_dumps(source_files), encoding="utf-8")
    extraction_log = {
        "script": "scripts/export_closed_lcd_residuals.py",
        "git_commit": _git_commit(),
        "created_at_unix": time.time(),
        "wavelengths_nm": extracted["wavelengths_nm"],
        "repeat_indices": extracted["repeat_indices"],
        "n_avg_frames": extracted["n_avg_frames"],
    }
    (provenance_dir / "extraction_log.json").write_text(json_dumps(extraction_log), encoding="utf-8")
    (provenance_dir / "raw_h5_paths.txt").write_text(str(source_path) + "\n", encoding="utf-8")


def _copy_release_scripts(scripts_dir: Path) -> None:
    for name in [
        "export_closed_lcd_residuals.py",
        "inspect_closed_lcd_residuals.py",
        "validate_closed_lcd_residual_release.py",
    ]:
        src = _repo_root() / "scripts" / name
        if src.exists():
            shutil.copy2(src, scripts_dir / name)


def _manifest_summary(
    *,
    release_name: str,
    source_path: Path,
    extracted: dict[str, Any],
    stats: list[dict[str, Any]],
    mask_id: str,
    roi: str,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "release_name": release_name,
        "release_type": RELEASE_TYPE,
        "source_project": "optic_system",
        "source_phase": "phase3_release_20260520",
        "source_psf_dictionary": source_path.name,
        "source_mask": mask_id,
        "roi": roi,
        "wavelengths_nm": extracted["wavelengths_nm"],
        "exposure_us": extracted["exposure_us"],
        "gain_db": extracted["gain_db"],
        "n_repeats": extracted["n_repeats"],
        "n_avg_frames": extracted["n_avg_frames"],
        "is_sensor_dark": False,
        "is_single_frame_burst": False,
        "is_closed_lcd_residual": True,
        "recommended_use": [
            "averaged-frame residual injection",
            "closed-LCD background residual stress test",
        ],
        "forbidden_claims": [
            "real sensor noise model",
            "read noise estimate",
            "shot noise estimate",
            "sensor-only dark",
            "full radiometric calibration",
        ],
        "statistics": stats,
    }


def _stats_rows(*, extracted: dict[str, Any], residuals: np.ndarray) -> list[dict[str, Any]]:
    rows = []
    for i, wl in enumerate(extracted["wavelengths_nm"]):
        std_map = np.std(residuals[i], axis=0)
        values = residuals[i].reshape(-1)
        rows.append(
            {
                "wavelength_nm": float(wl),
                "exposure_us": float(extracted["exposure_us"][i]),
                "gain_db": float(extracted["gain_db"][i]),
                "repeats": int(extracted["n_repeats"]),
                "avg_frames": int(extracted["n_avg_frames"]),
                "mean_count": float(np.mean(extracted["crops"][i])),
                "residual_std_mean": float(np.mean(std_map)),
                "residual_std_p95": float(np.percentile(std_map, 95.0)),
                "residual_abs_p95": float(np.percentile(np.abs(values), 95.0)),
                "residual_min": float(np.min(values)),
                "residual_max": float(np.max(values)),
            }
        )
    return rows


def _center_crop_stack(arr: np.ndarray, size: int) -> np.ndarray | None:
    if int(size) <= 0:
        return None
    h, w = int(arr.shape[-2]), int(arr.shape[-1])
    if h < size or w < size:
        return None
    y0 = (h - size) // 2
    x0 = (w - size) // 2
    return np.asarray(arr[..., y0 : y0 + size, x0 : x0 + size])


def _mean_power_spectrum(residuals_for_wavelength: np.ndarray) -> np.ndarray:
    spectra = []
    for item in np.asarray(residuals_for_wavelength):
        centered = item - float(np.mean(item))
        fft = np.fft.fftshift(np.fft.fft2(centered))
        spectra.append(np.log1p(np.abs(fft) ** 2))
    return np.mean(np.stack(spectra, axis=0), axis=0)


def _radial_profile(image: np.ndarray) -> np.ndarray:
    y, x = np.indices(image.shape)
    cy = (image.shape[0] - 1) / 2.0
    cx = (image.shape[1] - 1) / 2.0
    r = np.sqrt((x - cx) ** 2 + (y - cy) ** 2).astype(np.int32)
    sums = np.bincount(r.ravel(), weights=image.ravel())
    counts = np.bincount(r.ravel())
    return sums / np.maximum(counts, 1)


def _file_records(root: Path, *, exclude_names: set[str] | None = None) -> list[dict[str, Any]]:
    exclude_names = exclude_names or set()
    records = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.name in exclude_names:
            continue
        rel = str(path.relative_to(root)).replace("\\", "/")
        records.append({"path": rel, "bytes": path.stat().st_size, "sha256": _sha256(path)})
    return records


def _require_json_dataset(f: h5py.File, path: str) -> dict[str, Any]:
    value = f[path][()]
    text = _decode(value)
    if not text:
        raise ValueError(f"required provenance dataset is empty: {path}")
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError(f"required provenance dataset must decode to a JSON object: {path}")
    return data


def _optional_json_dataset(f: h5py.File, path: str) -> dict[str, Any]:
    if path not in f:
        return {}
    value = f[path][()]
    text = _decode(value)
    if not text:
        return {}
    data = json.loads(text)
    return data if isinstance(data, dict) else {}


def _decode(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return str(value)


def _resolve_path(path: str | Path) -> Path:
    p = Path(path)
    return p if p.is_absolute() else _repo_root() / p


def _wl_key(value: float) -> str:
    return format(float(value), ".0f")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _git_commit() -> str | None:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=_repo_root(), text=True).strip()
    except Exception:
        return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Export closed-LCD averaged-frame residual release")
    parser.add_argument("--source", required=True, help="Phase 3.4 PSF dictionary raw HDF5")
    parser.add_argument("--release-dir", required=True, help="Output release directory")
    parser.add_argument("--release-name", default=DEFAULT_RELEASE_NAME)
    parser.add_argument("--mask-id", default="all_closed_window")
    parser.add_argument("--roi", default="roi_512")
    args = parser.parse_args()
    manifest = export_closed_lcd_residual_release(
        source=args.source,
        release_dir=args.release_dir,
        release_name=args.release_name,
        mask_id=args.mask_id,
        roi=args.roi,
    )
    print(json_dumps({"release_name": manifest["release_name"], "release_type": manifest["release_type"]}))


if __name__ == "__main__":
    main()
