#!/usr/bin/env python3
"""Export thesis appendix calibration diagnostic figures from Phase 3 handoff.

Generates:
  - appendix_lcd_effective_pupil_annotated.pdf/png   (U1)
  - appendix_psf_roi_comparison.pdf/png              (U2)
  - appendix_roi_energy_decomposition.pdf/png        (U2b)
  - appendix_psf_tail_enhanced.pdf/png               (U2c)
  - fig3_wavelength_psf_scale.pdf/png                (Fig 3)
  - appendix_calibration_summary.csv
  - appendix_roi_energy_decomposition.csv
  - thesis_optic_system_figures_manifest.json
"""

from __future__ import annotations

import argparse
import csv
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import h5py
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

DEFAULT_RELEASE_ROOT = Path("D:/datasets/optic_system/phase3_release_20260520")
DEFAULT_OUT_DIR = Path("outputs/thesis_figures")

MATPLOTLIB_RCPARAMS = {
    "font.size": 8,
    "axes.titlesize": 9,
    "axes.labelsize": 8,
    "xtick.labelsize": 7,
    "ytick.labelsize": 7,
    "legend.fontsize": 7,
    "figure.dpi": 150,
    "savefig.dpi": 300,
    "savefig.bbox": "tight",
    "savefig.pad_inches": 0.1,
    "font.family": "sans-serif",
    "font.sans-serif": ["DejaVu Sans", "Arial", "Helvetica"],
    "mathtext.default": "regular",
}


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _resolve_path(path: str | Path) -> Path:
    p = Path(path)
    return _repo_root() / p if not p.is_absolute() else p


def export_thesis_calibration_figures(
    phase3_release: str | Path,
    out_dir: str | Path,
    *,
    copy_to_thesis_assets: str | Path | None = None,
    fmt: str = "both",
    dpi: int = 300,
    force: bool = False,
) -> dict[str, Any]:
    release_root = _resolve_path(phase3_release) if not Path(phase3_release).is_absolute() else Path(phase3_release)
    out = _resolve_path(out_dir) if not Path(out_dir).is_absolute() else Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    artifacts = _locate_artifacts(release_root)
    _check_required_files(artifacts)

    lcd_data = _load_lcd_pupil_data(release_root, artifacts)
    psf_data = _load_psf_roi_data(release_root, artifacts)
    exposure_data = _load_exposure_data(release_root, artifacts)

    energy_decomp = _compute_energy_decomposition(psf_data, release_root)

    with plt.rc_context(MATPLOTLIB_RCPARAMS):
        lcd_pdf = _build_figure_u1(lcd_data, out, dpi, fmt)
        psf_pdf = _build_figure_u2(psf_data, out, dpi, fmt)
        decomp_pdf = _build_figure_u2b(psf_data, energy_decomp, out, dpi, fmt)
        tail_pdf = _build_figure_u2c_tail_enhanced(psf_data, out, dpi, fmt)
        fig3_pdf = _build_figure_wavelength_psf_scale(out, dpi, fmt)

    pdf_paths = [p for p in [lcd_pdf, psf_pdf, decomp_pdf, tail_pdf, fig3_pdf] if p is not None]

    csv_path = _write_calibration_csv(lcd_data, psf_data, exposure_data, out)
    _write_energy_decomposition_csv(energy_decomp, out)
    manifest = _write_manifest(lcd_data, psf_data, energy_decomp, out, str(release_root))

    _copy_to_thesis_assets(
        pdf_paths=pdf_paths,
        thesis_assets_dir=Path(copy_to_thesis_assets) if copy_to_thesis_assets else None,
        force=force,
    )

    return manifest


# ---------------------------------------------------------------------------
# artifact location
# ---------------------------------------------------------------------------


def _locate_artifacts(release_root: Path) -> dict[str, Path]:
    common_raw = release_root / "common" / "provenance" / "raw_h5"
    thesis_metrics = release_root / "thesis" / "metrics"
    return {
        "pupil_h5": common_raw / "bishe_pupil_geometry.h5",
        "pupil_window_json": thesis_metrics / "pupil_geometry" / "effective_pupil_window.json",
        "psf_roi_h5": common_raw / "bishe_psf_roi.h5",
        "psf_roi_json": thesis_metrics / "psf_roi" / "psf_roi.json",
        "camera_params_json": thesis_metrics / "exposure_calibration" / "camera_params_psf_safe.json",
    }


def _check_required_files(artifacts: dict[str, Path]) -> None:
    missing = [k for k, p in artifacts.items() if not p.exists()]
    if missing:
        paths = "\n".join(f"  {k}: {artifacts[k]}" for k in missing)
        raise FileNotFoundError(
            f"Required Phase 3 handoff artifacts not found:\n{paths}\n"
            f"Set --phase3-release to the release root directory."
        )


# ---------------------------------------------------------------------------
# data loading
# ---------------------------------------------------------------------------


def _load_json(path: Path) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _read_h5_scalar(f: h5py.File, key: str) -> str:
    val = f[key][()]
    if isinstance(val, bytes):
        return val.decode("utf-8")
    return str(val)


def _read_h5_json(f: h5py.File, key: str) -> dict[str, Any]:
    text = _read_h5_scalar(f, key)
    return json.loads(text) if text else {}


def _load_lcd_pupil_data(release_root: Path, artifacts: dict[str, Path]) -> dict[str, Any]:
    window_json = _load_json(artifacts["pupil_window_json"])
    with h5py.File(str(artifacts["pupil_h5"]), "r") as f:
        plan = _read_h5_json(f, "capture/plan_json")
        tls_meta = _read_h5_json(f, "tls/metadata_json")
        bar_x_pos = np.asarray(f["bar_scan/x/positions"], dtype=np.float64)
        bar_x_energy = np.asarray(f["bar_scan/x/energies"], dtype=np.float64)
        bar_y_pos = np.asarray(f["bar_scan/y/positions"], dtype=np.float64)
        bar_y_energy = np.asarray(f["bar_scan/y/energies"], dtype=np.float64)
        radii = np.asarray(f["radius_scan/radii"], dtype=np.float64)
        radius_energy = np.asarray(f["radius_scan/energies"], dtype=np.float64)

    center = (float(window_json["center"]["x"]), float(window_json["center"]["y"]))
    effective_radius = float(window_json["radius"])
    ellipse = window_json.get("ellipse", {})
    physical_shape = tuple(window_json.get("physical_shape", [2560, 1620]))

    return {
        "center": center,
        "effective_radius": effective_radius,
        "ellipse": ellipse,
        "physical_shape": physical_shape,
        "wavelength_nm": float(
            tls_meta.get("target_wavelength_nm") or tls_meta.get("current_wavelength_nm") or window_json.get("wavelength_nm", 550.0)
        ),
        "bar_x_positions": bar_x_pos,
        "bar_x_energies": bar_x_energy,
        "bar_y_positions": bar_y_pos,
        "bar_y_energies": bar_y_energy,
        "radii": radii,
        "radius_energies": radius_energy,
        "window_json": window_json,
    }


def _load_psf_roi_data(release_root: Path, artifacts: dict[str, Path]) -> dict[str, Any]:
    roi_json = _load_json(artifacts["psf_roi_json"])
    with h5py.File(str(artifacts["psf_roi_h5"]), "r") as f:
        frames_avg = np.asarray(f["raw/frames_avg"], dtype=np.float64)
        mean_frame = np.mean(frames_avg, axis=0) if frames_avg.ndim == 3 else frames_avg

    center = (float(roi_json["center"]["x"]), float(roi_json["center"]["y"]))
    selected_roi_key = roi_json.get("final_selected_roi_key") or "roi_512"
    rois = roi_json.get("rois", {})
    candidate_keys = [k for k in rois if k != selected_roi_key]
    sorted_candidates = sorted(candidate_keys, key=lambda k: rois[k].get("width", 0))
    selected_roi = rois.get(selected_roi_key, {})

    energy_coverage = _compute_roi_energy_coverage(mean_frame, rois)
    stored_energy_frac = roi_json.get("quality", {}).get("roi_energy_fraction")
    roi_256_frac = energy_coverage.get("roi_256") or (float(stored_energy_frac) if stored_energy_frac is not None else None)

    valid_mask = np.ones(mean_frame.shape, dtype=bool)
    valid_mask[:1, :] = False
    background = float(np.percentile(mean_frame[valid_mask], 5.0))
    peak_pixel = float(np.max(mean_frame[valid_mask]))

    return {
        "center": center,
        "mean_frame": mean_frame,
        "frame_shape": tuple(roi_json.get("frame_shape", [2048, 2448])),
        "selected_roi_key": selected_roi_key,
        "selected_roi": selected_roi,
        "candidate_rois": {k: rois[k] for k in sorted_candidates},
        "energy_coverage": energy_coverage,
        "roi_256_energy_frac": roi_256_frac,
        "background": background,
        "peak_pixel": peak_pixel,
        "roi_json": roi_json,
        "wavelength_nm": float(roi_json.get("wavelength_nm", 550.0)),
    }


def _compute_roi_energy_coverage(mean_frame: np.ndarray, rois: dict[str, Any]) -> dict[str, float]:
    valid_mask = np.ones(mean_frame.shape, dtype=bool)
    valid_mask[:1, :] = False
    background = float(np.percentile(mean_frame[valid_mask], 5.0))
    corrected = np.maximum(mean_frame - background, 0.0)
    total = float(np.sum(corrected[valid_mask]))
    if total <= 0 or not np.isfinite(total):
        return {}
    result: dict[str, float] = {}
    for rk, rr in rois.items():
        if not rr or not rr.get("fits_frame"):
            continue
        x0 = max(0, min(mean_frame.shape[1], int(rr.get("x_min", 0))))
        x1 = max(0, min(mean_frame.shape[1], int(rr.get("x_max", 0))))
        y0 = max(0, min(mean_frame.shape[0], int(rr.get("y_min", 0))))
        y1 = max(0, min(mean_frame.shape[0], int(rr.get("y_max", 0))))
        if x1 <= x0 or y1 <= y0:
            continue
        roi_sum = float(np.sum(corrected[y0:y1, x0:x1]))
        result[rk] = roi_sum / total
    return result


def _compute_energy_decomposition(psf_data: dict[str, Any], release_root: Path) -> dict[str, Any]:
    mean_frame = psf_data["mean_frame"]
    center = psf_data["center"]
    energy_cov_full = psf_data["energy_coverage"]

    valid_mask = np.ones(mean_frame.shape, dtype=bool)
    valid_mask[:1, :] = False
    bg = float(np.percentile(mean_frame[valid_mask], 5.0))
    corrected = np.maximum(mean_frame - bg, 0.0)
    cy, cx = center[1], center[0]

    yg, xg = np.ogrid[: mean_frame.shape[0], : mean_frame.shape[1]]
    r = np.sqrt((xg - cx) ** 2 + (yg - cy) ** 2)

    support_radii = [200, 300, 500]
    support_totals: dict[str, float] = {}
    for sr in support_radii:
        supp_mask = (r < sr) & valid_mask
        support_totals[f"r<{sr}"] = float(np.sum(corrected[supp_mask]))
    support_totals["full"] = float(np.sum(corrected[valid_mask]))

    rois_def = {
        "roi_256": (1021, 1277, 807, 1063),
        "roi_512": (893, 1405, 679, 1191),
        "roi_768": (765, 1533, 551, 1319),
        "roi_1024": (637, 1661, 423, 1447),
    }
    roi_enclosed: dict[str, dict[str, float]] = {}
    for rk, (x0, x1, y0, y1) in rois_def.items():
        roi_sum = float(np.sum(corrected[max(0, y0): min(mean_frame.shape[0], y1),
                                          max(0, x0): min(mean_frame.shape[1], x1)]))
        entry: dict[str, float] = {}
        for domain, total in support_totals.items():
            entry[domain] = roi_sum / total if total > 0 else float("nan")
        roi_enclosed[rk] = entry

    thresholds = [0.0, 0.1, 0.5, 1.0, 5.0, 10.0]
    far_mask = (r >= 200) & valid_mask
    full_total = support_totals["full"]
    far_thresholds: list[dict[str, float]] = []
    for t in thresholds:
        t_mask = far_mask & (corrected >= t)
        energy = float(np.sum(corrected[t_mask]))
        far_thresholds.append({
            "threshold": t,
            "pixels": int(t_mask.sum()),
            "energy": energy,
            "pct_total": energy / full_total * 100 if full_total > 0 else 0.0,
        })

    dark_mean = None
    dark_std = None
    dark_h5 = release_root / "common" / "provenance" / "raw_h5" / "bishe_pupil_geometry.h5"
    if dark_h5.exists():
        with h5py.File(str(dark_h5), "r") as f:
            dark = np.asarray(f["references/dark_frame_avg"], dtype=np.float64)
            dark_mean = float(np.mean(dark[valid_mask]))
            dark_std = float(np.std(dark[valid_mask]))

    return {
        "roi_enclosed": roi_enclosed,
        "far_field_thresholds": far_thresholds,
        "support_totals": support_totals,
        "metadata": {
            "bg": bg,
            "peak": psf_data["peak_pixel"],
            "dark_mean": dark_mean,
            "dark_std": dark_std,
            "full_frame_denominator": full_total,
            "noise_floor_artifact_pp": float(far_thresholds[0]["pct_total"] - far_thresholds[2]["pct_total"]),
            "genuine_diffraction_pp": float(far_thresholds[2]["pct_total"]),
        },
    }


def _load_exposure_data(release_root: Path, artifacts: dict[str, Path]) -> dict[str, Any]:
    params = _load_json(artifacts["camera_params_json"])
    catalog = params.get("camera_param_catalog", {})
    exposure: dict[str, dict[str, float]] = {}
    for wl_key in ("450.0", "550.0", "650.0"):
        entry = catalog.get(wl_key, {})
        rec = entry.get("recommended", {}) if isinstance(entry, dict) else {}
        exposure[wl_key] = {
            "exposure_us": float(rec.get("exposure_us", float("nan"))),
            "gain_db": float(rec.get("gain_db", float("nan"))),
        }
    return {"exposure": exposure, "params_json": params}


# ---------------------------------------------------------------------------
# U1: LCD effective pupil annotated figure
# ---------------------------------------------------------------------------


def _build_figure_u1(data: dict[str, Any], out_dir: Path, dpi: int, fmt: str) -> Path:
    center = data["center"]
    radius = data["effective_radius"]
    ellipse = data["ellipse"]
    w = data["physical_shape"]
    wl = data["wavelength_nm"]

    fig = plt.figure(figsize=(8.0, 5.0))

    # ---- panel (a): LCD physical coordinate map with circle overlay ----
    ax_map = fig.add_axes([0.06, 0.12, 0.48, 0.80])
    _draw_lcd_pupil_map(ax_map, data)

    # ---- panel (b): bar profile sub-panels ----
    ax_bar_x = fig.add_axes([0.63, 0.58, 0.33, 0.33])
    ax_bar_y = fig.add_axes([0.63, 0.12, 0.33, 0.33])

    _draw_bar_profile(ax_bar_x, data, axis="x")
    _draw_bar_profile(ax_bar_y, data, axis="y")

    fig.text(0.03, 0.97, "(a)", fontsize=9, fontweight="bold", va="top")
    fig.text(0.59, 0.97, "(b)", fontsize=9, fontweight="bold", va="top")
    fig.text(0.59, 0.55, "(c)", fontsize=9, fontweight="bold", va="top")

    fig.text(
        0.06, 1.01,
        f"LCD Effective Encoding Region ({wl:.0f} nm)",
        fontsize=9, fontweight="bold", va="bottom",
    )
    fig.text(
        0.06, 0.97,
        "LCD physical coordinates",
        fontsize=7.5, va="top",
    )

    return _save_figure(fig, out_dir / "appendix_lcd_effective_pupil_annotated", dpi, fmt)


def _draw_lcd_pupil_map(ax: plt.Axes, data: dict[str, Any]) -> None:
    center = data["center"]
    radius = data["effective_radius"]
    ellipse = data["ellipse"]
    physical_shape = data["physical_shape"]

    pad_x = physical_shape[1] * 0.06
    pad_y = physical_shape[0] * 0.06
    margin = max(pad_x, pad_y, 4 * radius)
    x_lim = (center[0] - 4 * radius, center[0] + 4 * radius)
    y_lim = (center[1] - 4 * radius, center[1] + 4 * radius)

    ax.set_xlim(x_lim)
    ax.set_ylim(y_lim)
    ax.set_aspect("equal")
    ax.set_xlabel("LCD physical X [px]")
    ax.set_ylabel("LCD physical Y [px]")
    ax.invert_yaxis()

    theta = np.linspace(0, 2 * np.pi, 200)
    circle_x = center[0] + radius * np.cos(theta)
    circle_y = center[1] + radius * np.sin(theta)
    ax.fill(circle_x, circle_y, facecolor="#FFDD55", edgecolor="#CC6600", linewidth=1.2, alpha=0.35, zorder=1)
    ax.plot(circle_x, circle_y, color="#CC6600", linewidth=1.2, zorder=2)

    ax.plot(center[0], center[1], "r+", markersize=8, markeredgewidth=1.5, zorder=3)

    if ellipse:
        e_a = float(ellipse.get("a", 0))
        e_b = float(ellipse.get("b", 0))
        r2 = float(ellipse.get("r_squared", 0))
        if e_a > 0 and e_b > 0:
            e_theta = np.linspace(0, 2 * np.pi, 200)
            e_x = center[0] + e_a * np.cos(e_theta)
            e_y = center[1] + e_b * np.sin(e_theta)
            ax.plot(e_x, e_y, "--", color="#336699", linewidth=0.8, alpha=0.7, zorder=1, label="ellipse fit")

    props = dict(boxstyle="round,pad=0.3", facecolor="white", edgecolor="gray", alpha=0.85)
    info_lines = [
        f"center  = ({center[0]:.1f}, {center[1]:.1f}) px",
        f"$r_{{\\rm eff}}$ = {radius:.1f} px",
    ]
    if ellipse:
        info_lines.append(f"$R^2$ = {ellipse.get('r_squared', 0):.4f}")
    ax.text(
        0.98,
        0.03,
        "\n".join(info_lines),
        transform=ax.transAxes,
        fontsize=7,
        verticalalignment="bottom",
        horizontalalignment="right",
        bbox=props,
        zorder=5,
    )

    scale_len = max(20, round(2 * radius / 6) * 10)
    scale_y = y_lim[0] + (y_lim[1] - y_lim[0]) * 0.08
    scale_x0 = x_lim[1] - scale_len * 1.5
    scale_x1 = x_lim[1] - scale_len * 0.5
    ax.plot([scale_x0, scale_x1], [scale_y, scale_y], "k-", linewidth=1.5)
    ax.text((scale_x0 + scale_x1) / 2, scale_y + 15, f"{scale_len} px", ha="center", fontsize=6, va="bottom")


def _draw_bar_profile(ax: plt.Axes, data: dict[str, Any], axis: str) -> None:
    center = data["center"]
    radius = data["effective_radius"]

    if axis == "x":
        positions = np.asarray(data["bar_x_positions"], dtype=np.float64)
        energies = np.asarray(data["bar_x_energies"], dtype=np.float64)
        center_val = center[0]
        xlabel = "LCD physical X [px]"
    else:
        positions = np.asarray(data["bar_y_positions"], dtype=np.float64)
        energies = np.asarray(data["bar_y_energies"], dtype=np.float64)
        center_val = center[1]
        xlabel = "LCD physical Y [px]"

    if positions.size < 2:
        ax.text(0.5, 0.5, "no data", transform=ax.transAxes, ha="center", va="center", fontsize=7)
        return

    ax.plot(positions, energies, ".", color="#336699", markersize=1.5, alpha=0.7)
    ax.axvline(center_val, color="#CC6600", linestyle="--", linewidth=0.8, alpha=0.8, label=f"center={center_val:.1f}")
    ax.axvline(center_val - radius, color="gray", linestyle=":", linewidth=0.6, alpha=0.5)
    ax.axvline(center_val + radius, color="gray", linestyle=":", linewidth=0.6, alpha=0.5)

    ax.set_xlabel(xlabel)
    ax.set_ylabel("Energy [AU]")
    ax.legend(loc="upper left", fontsize=6, framealpha=0.7)
    ax.ticklabel_format(axis="y", style="scientific", scilimits=(-2, 3))


# ---------------------------------------------------------------------------
# U2: PSF ROI comparison figure
# ---------------------------------------------------------------------------


def _build_figure_u2(data: dict[str, Any], out_dir: Path, dpi: int, fmt: str) -> Path:
    center = data["center"]
    mean_frame = data["mean_frame"]
    selected_key = data["selected_roi_key"]
    selected_roi = data["selected_roi"]
    candidates = data["candidate_rois"]
    wl = data["wavelength_nm"]
    bg = data["background"]
    peak = data["peak_pixel"]

    fig = plt.figure(figsize=(7.2, 3.5))

    # ---- panel (a): PSF with ROI overlays ----
    ax_frame = fig.add_axes([0.08, 0.12, 0.88, 0.80])
    _draw_psf_roi_overlay(ax_frame, data, vmin=bg, vmax=peak)

    # ---- inset: log tail-enhanced view ----
    inset = ax_frame.inset_axes([0.60, 0.60, 0.37, 0.37])
    _draw_log_inset(inset, data, bg=bg)

    fig.suptitle(
        f"PSF ROI Candidate Overlay  |  {wl:.0f} nm  |  camera sensor coordinates",
        fontsize=9,
        fontweight="bold",
        y=0.98,
    )

    return _save_figure(fig, out_dir / "appendix_psf_roi_comparison", dpi, fmt)


def _draw_psf_roi_overlay(ax: plt.Axes, data: dict[str, Any], *, vmin: float, vmax: float) -> None:
    center = data["center"]
    img = np.asarray(data["mean_frame"], dtype=np.float64)
    candidates = data["candidate_rois"]
    selected_key = data["selected_roi_key"]
    selected_roi = data["selected_roi"]

    if vmax <= vmin:
        vmax = vmin + 1.0
    ax.imshow(img, cmap="gray", vmin=vmin, vmax=vmax, aspect="equal", origin="upper")

    all_rois = [("roi_256", candidates.get("roi_256")), ("roi_512", selected_roi),
                ("roi_768", candidates.get("roi_768")), ("roi_1024", candidates.get("roi_1024"))]

    for rk, rr in all_rois:
        if rr is None:
            continue
        x0, y0 = int(rr["x_min"]), int(rr["y_min"])
        w, h = int(rr["width"]), int(rr["height"])
        if rk == selected_key:
            rect = plt.Rectangle((x0, y0), w, h, fill=False, edgecolor="#FFDD22", linewidth=1.5, linestyle="-", zorder=5)
            ax.text(x0 + 4, y0 + 10, rk, color="#FFDD22", fontsize=7, fontweight="bold", va="top", zorder=6)
        else:
            rect = plt.Rectangle((x0, y0), w, h, fill=False, edgecolor="white", linewidth=0.7, linestyle="--", alpha=0.6, zorder=4)
            ax.text(x0 + 4, y0 + 10, rk, color="white", fontsize=6, alpha=0.7, va="top", zorder=5)
        ax.add_patch(rect)

    ax.plot(center[0], center[1], "r+", markersize=8, markeredgewidth=1.5, zorder=7)

    fh, fw = data["frame_shape"]
    view_margin = 300
    vx_min = max(0, center[0] - view_margin)
    vx_max = min(fw, center[0] + view_margin)
    vy_min = max(0, center[1] - view_margin)
    vy_max = min(fh, center[1] + view_margin)
    ax.set_xlim(vx_min, vx_max)
    ax.set_ylim(vy_max, vy_min)
    ax.set_xlabel(f"Camera X [px]  |  linear intensity [{vmin:.0f}, {vmax:.0f}]")
    ax.set_ylabel("Camera Y [px]")


def _draw_log_inset(ax: plt.Axes, data: dict[str, Any], *, bg: float) -> None:
    img = np.asarray(data["mean_frame"], dtype=np.float64)
    corrected = np.maximum(img - bg, 0.0)
    log_img = np.log10(corrected + 1.0)

    ax.imshow(log_img, cmap="gray", aspect="equal", origin="upper")

    center = data["center"]
    fh, fw = data["frame_shape"]
    view_margin = 300
    vx_min = max(0, center[0] - view_margin)
    vx_max = min(fw, center[0] + view_margin)
    vy_min = max(0, center[1] - view_margin)
    vy_max = min(fh, center[1] + view_margin)
    ax.set_xlim(vx_min, vx_max)
    ax.set_ylim(vy_max, vy_min)
    ax.set_xticks([])
    ax.set_yticks([])

    ax.text(
        0.98, 0.03,
        "log₁₀(bg-subtracted + 1)\ntail-enhanced, not\nenergy-proportional",
        transform=ax.transAxes, fontsize=5.5, va="bottom", ha="right",
        color="yellow", alpha=0.85,
    )


# ---------------------------------------------------------------------------
# U2b: ROI energy decomposition figure
# ---------------------------------------------------------------------------


def _build_figure_u2b(psf_data: dict, energy_decomp: dict, out_dir: Path, dpi: int, fmt: str) -> Path:
    roi_enc = energy_decomp["roi_enclosed"]
    far_thresh = energy_decomp["far_field_thresholds"]
    meta = energy_decomp["metadata"]

    fig = plt.figure(figsize=(7.2, 3.8))

    # ---- panel (a): ROI enclosed energy vs support domain (group bar chart) ----
    ax_bar = fig.add_axes([0.08, 0.15, 0.43, 0.78])
    _draw_roi_support_domain_bars(ax_bar, roi_enc)

    # ---- panel (b): far-field cumulative energy by threshold ----
    ax_thresh = fig.add_axes([0.57, 0.15, 0.38, 0.78])
    _draw_far_field_threshold_plot(ax_thresh, far_thresh)

    fig.text(0.04, 0.96, "(a)", fontsize=9, fontweight="bold", va="top")
    fig.text(0.54, 0.96, "(b)", fontsize=9, fontweight="bold", va="top")

    fig.suptitle(
        "PSF ROI Energy Decomposition  |  noise-floor vs diffraction wing separation",
        fontsize=9,
        fontweight="bold",
        y=1.005,
        va="bottom",
    )

    return _save_figure(fig, out_dir / "appendix_roi_energy_decomposition", dpi, fmt)


# ---------------------------------------------------------------------------
# U2c: full-frame PSF tail-enhanced view (p=0.99 percentile normalization)
# ---------------------------------------------------------------------------


def _build_figure_u2c_tail_enhanced(
    psf_data: dict[str, Any],
    out_dir: Path,
    dpi: int,
    fmt: str,
) -> Path:
    mean_frame = np.asarray(psf_data["mean_frame"], dtype=np.float64)
    center = psf_data["center"]
    bg = psf_data["background"]
    fh, fw = psf_data["frame_shape"]
    wl = psf_data["wavelength_nm"]
    selected_roi = psf_data["selected_roi"]
    candidates = psf_data["candidate_rois"]

    corrected = np.maximum(mean_frame - bg, 0.0)
    nonzero = corrected[corrected > 0]
    p99 = float(np.percentile(nonzero, 99.0)) if nonzero.size else 1.0

    cy, cx = center[1], center[0]
    yg, xg = np.ogrid[: fh, : fw]
    r = np.sqrt((xg - cx) ** 2 + (yg - cy) ** 2)
    far_mask = (r >= 200) & (mean_frame > bg)
    if far_mask.any():
        far_flat_idx = int(np.argmax(corrected[far_mask]))
        far_indices = np.argwhere(far_mask)
        spot_y, spot_x = far_indices[far_flat_idx]
        spot_r = float(r[spot_y, spot_x])
    else:
        spot_y, spot_x = 0, 0
        spot_r = 0.0

    fig = plt.figure(figsize=(8.0, 6.0))

    ax_main = fig.add_axes([0.06, 0.10, 0.72, 0.85])
    im = ax_main.imshow(corrected, cmap="magma", vmin=0, vmax=p99, aspect="equal", origin="upper")

    all_rois = [("roi_256", candidates.get("roi_256")), ("roi_512", selected_roi),
                ("roi_768", candidates.get("roi_768")), ("roi_1024", candidates.get("roi_1024"))]
    for rk, rr in all_rois:
        if rr is None:
            continue
        x0, y0 = int(rr["x_min"]), int(rr["y_min"])
        w, h = int(rr["width"]), int(rr["height"])
        if rk == "roi_512":
            rect = plt.Rectangle((x0, y0), w, h, fill=False, edgecolor="#FFDD22", linewidth=1.2, linestyle="-", zorder=5)
        else:
            rect = plt.Rectangle((x0, y0), w, h, fill=False, edgecolor="white", linewidth=0.6, linestyle="--", alpha=0.4, zorder=4)
        ax_main.add_patch(rect)

    ax_main.plot(cx, cy, "r+", markersize=8, markeredgewidth=1.5, zorder=7)

    hs = 32
    if spot_y > 0:
        zoom_rect = plt.Rectangle(
            (spot_x - hs, spot_y - hs), 2 * hs, 2 * hs,
            fill=False, edgecolor="#FFDD22", linewidth=1.0, linestyle="--", zorder=6, alpha=0.8,
        )
        ax_main.add_patch(zoom_rect)

    ax_main.text(
        0.02, 0.98, "saturated main lobe\n(p=0.99 clip)",
        transform=ax_main.transAxes, fontsize=7, va="top", color="yellow",
    )
    ax_main.text(
        0.98, 0.02,
        f"p99 threshold = {p99:.1f} counts above bg\n"
        f"roi_512 box = {selected_roi.get('width', '?')}x{selected_roi.get('height', '?')} px",
        transform=ax_main.transAxes, fontsize=6, va="bottom", ha="right",
        color="white", bbox=dict(facecolor="black", alpha=0.5),
    )

    ax_main.set_xlim(0, fw)
    ax_main.set_ylim(fh, 0)
    ax_main.set_xlabel("Camera X [px]")
    ax_main.set_ylabel("Camera Y [px]")

    cax = fig.add_axes([0.80, 0.15, 0.02, 0.75])
    cbar = fig.colorbar(im, cax=cax)
    cbar.set_label("clipped at p=0.99", fontsize=7)
    cbar.ax.tick_params(labelsize=6)

    if spot_y > 0:
        inset = ax_main.inset_axes([0.55, 0.55, 0.42, 0.42])
        y0_ins = max(0, spot_y - hs)
        y1_ins = min(fh, spot_y + hs)
        x0_ins = max(0, spot_x - hs)
        x1_ins = min(fw, spot_x + hs)
        roi_ins = corrected[y0_ins:y1_ins, x0_ins:x1_ins]
        inset.imshow(roi_ins, cmap="magma", vmin=0, vmax=p99, aspect="equal", origin="upper")
        inset.set_xticks([])
        inset.set_yticks([])
        inset.text(
            0.02, 0.98,
            f"diffraction peak (r={spot_r:.0f} px, zoom 64x64)",
            transform=inset.transAxes, fontsize=5.5, va="top", color="yellow",
        )

    fig.text(0.03, 0.97, "(a)", fontsize=9, fontweight="bold", va="top")
    if spot_y > 0:
        fig.text(0.53, 0.97, "(b)", fontsize=9, fontweight="bold", va="top")

    fig.suptitle(
        f"Full-Frame PSF Tail-Enhanced View (p=0.99)  |  {wl:.0f} nm",
        fontsize=9,
        fontweight="bold",
        y=1.005,
        va="bottom",
    )

    return _save_figure(fig, out_dir / "appendix_psf_tail_enhanced", dpi, fmt)


# ---------------------------------------------------------------------------
# Fig 3: same-mask cross-wavelength PSF scale comparison
# ---------------------------------------------------------------------------


def _build_figure_wavelength_psf_scale(out_dir: Path, dpi: int, fmt: str) -> Path:
    release_root = Path("D:/datasets/optic_system/phase3_release_20260520")
    h5_path = release_root / "common" / "provenance" / "raw_h5" / "bishe_psf_repeatability_20260519_222907.h5"

    if not h5_path.exists():
        print(f"warning: repeatability HDF5 not found ({h5_path}), skipping Fig 3")
        return None

    with h5py.File(str(h5_path), "r") as f:
        all_crops = np.asarray(f["raw/crops"], dtype=np.float64)
        all_masks = f["raw/mask_id"][()]

    wl_crops: dict[float, np.ndarray] = {}
    for wl_base_idx, wl_val in [(0, 450.0), (80, 550.0), (160, 650.0)]:
        indices = [
            i for i in range(wl_base_idx, wl_base_idx + 80)
            if all_masks[i].decode() == "all_open_window"
        ]
        wl_crops[wl_val] = np.mean(all_crops[indices], axis=0)

    wl_list = [450.0, 550.0, 650.0]
    crops = {wl: wl_crops[wl] for wl in wl_list}
    logs: dict[float, np.ndarray] = {}
    for wl in wl_list:
        crop = crops[wl]
        bg = float(np.percentile(crop, 5.0))
        corrected = np.maximum(crop - bg, 0.0)
        logs[wl] = np.log10(corrected + 1.0)

    global_min = float(min(logs[wl][logs[wl] > 0].min() for wl in wl_list))
    global_max = float(max(logs[wl].max() for wl in wl_list))

    fig = plt.figure(figsize=(8.5, 5.0))

    # ---- panel (a): 3 PSF crops side-by-side ----
    gs = fig.add_gridspec(1, 4, top=0.92, bottom=0.42, left=0.06, right=0.92,
                          wspace=0.08, width_ratios=[1, 1, 1, 0.06])
    wl_labels = [("450 nm", "#3355AA"), ("550 nm", "#55AA33"), ("650 nm", "#CC4400")]
    im = None
    for j, (wl, (label, edge_color)) in enumerate(zip(wl_list, wl_labels)):
        ax = fig.add_subplot(gs[0, j])
        img = logs[wl]
        im = ax.imshow(img, cmap="magma", vmin=global_min, vmax=global_max, aspect="equal", origin="upper")
        ax.set_title(label, fontsize=8, color=edge_color)
        ax.set_xticks([])
        ax.set_yticks([])
        rect = plt.Rectangle((96, 96), 64, 64, fill=False, edgecolor="white", linewidth=0.6, linestyle="--", alpha=0.5)
        ax.add_patch(rect)
        if j == 0:
            scale_y = 245
            scale_x0 = 10
            scale_x1 = 60
            ax.plot([scale_x0, scale_x1], [scale_y, scale_y], "w-", linewidth=1.5)
            ax.text((scale_x0 + scale_x1) / 2, scale_y - 6, "50 px", ha="center", fontsize=5.5, color="white")

    cax = fig.add_subplot(gs[0, 3])
    cbar = fig.colorbar(im, cax=cax)
    cbar.set_label(r"log$_{10}$(bg-sub + 1)", fontsize=7)
    cbar.ax.tick_params(labelsize=6)

    fig.text(0.03, 0.955, "(a) all_open_window PSF, same log scale", fontsize=8, fontweight="bold", va="top")

    # ---- panel (b): cumulative enclosed energy vs window half-size ----
    ax_c = fig.add_axes([0.12, 0.08, 0.78, 0.24])
    colors = ["#3355AA", "#55AA33", "#CC4400"]
    half_range = 120
    half_sizes = np.arange(0, half_range + 1)
    encl: dict[float, np.ndarray] = {}
    for wl, c in zip(wl_list, colors):
        crop = crops[wl]
        bg = float(np.percentile(crop, 5.0))
        corrected = np.maximum(crop - bg, 0.0)
        total = float(np.sum(corrected))
        cumulative = np.empty(len(half_sizes), dtype=np.float64)
        for i, hs in enumerate(half_sizes):
            y0 = max(0, 128 - hs)
            y1 = min(256, 128 + hs)
            x0 = max(0, 128 - hs)
            x1 = min(256, 128 + hs)
            cumulative[i] = float(np.sum(corrected[y0:y1, x0:x1])) / total if total > 0 else 0.0
        encl[wl] = cumulative
        ax_c.plot(half_sizes, cumulative, color=c, linewidth=1.2, label=f"{wl:.0f} nm")

    ax_c.axhline(0.5, color="gray", linestyle="--", linewidth=0.6, alpha=0.5)
    ax_c.text(2, 0.52, "0.5", fontsize=6, color="gray", va="bottom")
    ax_c.set_xlabel("Enclosing square half-size [px]")
    ax_c.set_ylabel("Enclosed energy fraction")
    ax_c.legend(fontsize=7, loc="lower right", framealpha=0.7)
    ax_c.set_xlim(0, half_range)
    ax_c.set_ylim(0, 1.05)

    label_ys: dict[float, float] = {450.0: 0.08, 550.0: 0.14, 650.0: 0.20}
    for wl, color in [(450.0, "#3355AA"), (550.0, "#55AA33"), (650.0, "#CC4400")]:
        cum = encl.get(wl)
        if cum is None or len(cum) < 2:
            continue
        r50_idx = int(np.searchsorted(cum, 0.5))
        trans_hs = int(half_sizes[r50_idx])
        ly = label_ys.get(wl, 0.1)
        ax_c.axvline(trans_hs, color=color, linestyle="--", linewidth=0.8, alpha=0.7)
        ax_c.text(
            trans_hs + 2, ly,
            f"r$_{{{wl:.0f}}}$={trans_hs}",
            fontsize=6.5, color=color, va="bottom",
        )

    fig.text(0.03, 0.40, "(b) cumulative enclosed energy vs square window size", fontsize=8, fontweight="bold", va="top")

    fig.text(
        0.06, 1.01,
        "Same-Mask Cross-Wavelength PSF Scale  |  all_open_window  |  roi_256 crop",
        fontsize=9, fontweight="bold", va="bottom",
    )

    return _save_figure(fig, out_dir / "fig3_wavelength_psf_scale", dpi, fmt)


def _draw_roi_support_domain_bars(ax: plt.Axes, roi_enc: dict[str, dict[str, float]]) -> None:
    domains = ["r<200", "r<300", "r<500", "full"]
    rois = ["roi_256", "roi_512", "roi_768", "roi_1024"]
    colors = ["#4477AA", "#CC6600", "#88AA33", "#CC88AA"]
    x = np.arange(len(domains))
    n_bars = len(rois)
    width = 0.8 / n_bars

    for i, (rk, c) in enumerate(zip(rois, colors)):
        vals = [roi_enc[rk].get(d, float("nan")) * 100 for d in domains]
        bx = x + (i - (n_bars - 1) / 2) * width
        bars = ax.bar(bx, vals, width, label=rk, color=c, edgecolor="white", linewidth=0.4)

    ax.axhline(100, color="gray", linestyle="--", linewidth=0.6, alpha=0.5)
    ax.set_xticks(x)
    ax.set_xticklabels(domains)
    ax.set_ylabel("Enclosed energy [%]")
    ax.set_ylim(0, 130)
    ax.legend(fontsize=6, loc="upper right", ncol=2, framealpha=0.7)


def _draw_far_field_threshold_plot(ax: plt.Axes, far_thresh: list[dict[str, float]]) -> None:
    thresholds = [ft["threshold"] for ft in far_thresh]
    pct_vals = [ft["pct_total"] for ft in far_thresh]
    px_vals = [ft["pixels"] for ft in far_thresh]

    ax.semilogx(thresholds, pct_vals, "ko-", markersize=4, linewidth=1.2, label="energy % of total")
    ax.set_xlabel("Threshold (corr >= x) [counts]")
    ax.set_ylabel("Far-field energy [% of total]", color="C0")
    ax.tick_params(axis="y", labelcolor="C0")

    ax2 = ax.twinx()
    ax2.semilogx(thresholds, px_vals, "s--", color="gray", markersize=3, linewidth=0.8, alpha=0.7, label="pixel count")
    ax2.set_ylabel("Pixels", color="gray")
    ax2.tick_params(axis="y", labelcolor="gray")
    ax2.set_yscale("log")

    ax.axvline(0.5, color="C1", linestyle=":", linewidth=0.8, alpha=0.8, label="diffraction threshold")

    lines1, labels1 = ax.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax.legend(lines1 + lines2 + [ax.lines[-1]],
              labels1 + labels2 + ["corr=0.5 thr"], fontsize=6, loc="upper right", framealpha=0.7)

    ax.set_xlim(0.02, 20)
    ax.set_ylim(bottom=-1)


# ---------------------------------------------------------------------------
# CSV and manifest
# ---------------------------------------------------------------------------


def _write_calibration_csv(lcd_data: dict, psf_data: dict, exposure_data: dict, out_dir: Path) -> Path:
    path = out_dir / "appendix_calibration_summary.csv"
    lcd_center = lcd_data["center"]
    ellipse = lcd_data["ellipse"]
    psf_center = psf_data["center"]
    energy_cov = psf_data.get("energy_coverage", {})

    rows = [
        {"item": "lcd_center_x", "value": f"{lcd_center[0]:.4f}", "unit": "px",
         "source_artifact": "effective_pupil_window.json", "note": "LCD physical coordinate X"},
        {"item": "lcd_center_y", "value": f"{lcd_center[1]:.4f}", "unit": "px",
         "source_artifact": "effective_pupil_window.json", "note": "LCD physical coordinate Y"},
        {"item": "lcd_effective_radius", "value": f"{lcd_data['effective_radius']:.4f}", "unit": "px",
         "source_artifact": "effective_pupil_window.json", "note": "effective circular pupil radius"},
        {"item": "lcd_fit_r2", "value": f"{ellipse.get('r_squared', float('nan')):.6f}", "unit": "",
         "source_artifact": "effective_pupil_window.json", "note": "ellipse/radius fit R²"},
        {"item": "psf_center_x", "value": f"{psf_center[0]:.4f}", "unit": "px",
         "source_artifact": "psf_roi.json", "note": "camera sensor coordinate X"},
        {"item": "psf_center_y", "value": f"{psf_center[1]:.4f}", "unit": "px",
         "source_artifact": "psf_roi.json", "note": "camera sensor coordinate Y"},
        {"item": "selected_roi", "value": psf_data["selected_roi_key"], "unit": "",
         "source_artifact": "psf_roi.json", "note": "final modelling ROI key"},
    ]

    for rk in ["roi_256", "roi_512", "roi_768", "roi_1024"]:
        val = energy_cov.get(rk)
        rows.append({
            "item": f"{rk}_energy_coverage",
            "value": f"{val:.4f}" if val is not None else "N/A",
            "unit": "",
            "source_artifact": "computed from bishe_psf_roi.h5 frames_avg",
            "note": f"enclosed energy fraction in {rk}",
        })

    for label, wl_key in [("450", "450.0"), ("550", "550.0"), ("650", "650.0")]:
        exp = exposure_data["exposure"].get(wl_key, {})
        rows.append({
            "item": f"exposure_{label}nm",
            "value": f"{exp.get('exposure_us', float('nan')):.4f}" if exp else "N/A",
            "unit": "us",
            "source_artifact": "camera_params_psf_safe.json",
            "note": f"PSF-safe exposure at {label} nm",
        })
        rows.append({
            "item": f"gain_{label}nm",
            "value": f"{exp.get('gain_db', float('nan')):.4f}" if exp else "N/A",
            "unit": "dB",
            "source_artifact": "camera_params_psf_safe.json",
            "note": f"PSF-safe gain at {label} nm",
        })

    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["item", "value", "unit", "source_artifact", "note"])
        writer.writeheader()
        writer.writerows(rows)
    return path


def _write_energy_decomposition_csv(energy_decomp: dict[str, Any], out_dir: Path) -> Path:
    path = out_dir / "appendix_roi_energy_decomposition.csv"
    roi_enc = energy_decomp["roi_enclosed"]
    support = energy_decomp["support_totals"]
    far_thresh = energy_decomp["far_field_thresholds"]
    meta = energy_decomp["metadata"]

    rows = []

    rows.append({"section": "support_domain", "item": "r<200_total_signal", "value": f"{support['r<200']:.1f}",
                  "unit": "counts", "note": "background-subtracted total in r<200"})
    rows.append({"section": "support_domain", "item": "r<300_total_signal", "value": f"{support['r<300']:.1f}",
                  "unit": "counts", "note": "background-subtracted total in r<300"})
    rows.append({"section": "support_domain", "item": "r<500_total_signal", "value": f"{support['r<500']:.1f}",
                  "unit": "counts", "note": "background-subtracted total in r<500"})
    rows.append({"section": "support_domain", "item": "full_frame_total_signal", "value": f"{support['full']:.1f}",
                  "unit": "counts", "note": "full-frame denominator, dominated by noise-floor integration"})

    for rk in ["roi_256", "roi_512", "roi_768", "roi_1024"]:
        for domain in ["r<200", "r<300", "r<500", "full"]:
            val = roi_enc[rk].get(domain, float("nan"))
            rows.append({
                "section": "support_domain",
                "item": f"{rk}_enclosed_{domain}",
                "value": f"{val * 100:.2f}" if val == val else "N/A",
                "unit": "%",
                "note": f"{rk} enclosed energy fraction within {domain.replace('r<','r < ')} support domain",
            })

    for ft in far_thresh:
        t = ft["threshold"]
        rows.append({"section": "far_field", "item": f"threshold_{t:.1f}_pixels",
                      "value": str(ft["pixels"]), "unit": "px",
                      "note": f"pixels at r>=200 with corr>={t}"})
        rows.append({"section": "far_field", "item": f"threshold_{t:.1f}_energy",
                      "value": f"{ft['energy']:.1f}", "unit": "counts",
                      "note": f"energy at r>=200 with corr>={t}"})
        rows.append({"section": "far_field", "item": f"threshold_{t:.1f}_pct_total",
                      "value": f"{ft['pct_total']:.2f}", "unit": "%",
                      "note": f"% of full-frame total at r>=200 with corr>={t}"})

    rows.append({"section": "metadata", "item": "noise_floor_artifact_pp",
                  "value": f"{meta['noise_floor_artifact_pp']:.2f}", "unit": "pp",
                  "note": "apparent leakage from noise-floor integration (corr<0.5)"})
    rows.append({"section": "metadata", "item": "genuine_diffraction_pp",
                  "value": f"{meta['genuine_diffraction_pp']:.2f}", "unit": "pp",
                  "note": "genuine far-field diffraction peak contribution (corr>=0.5)"})

    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["section", "item", "value", "unit", "note"])
        writer.writeheader()
        writer.writerows(rows)
    return path


def _write_manifest(lcd_data: dict, psf_data: dict, energy_decomp: dict | None, out_dir: Path, phase3_release: str) -> dict[str, Any]:
    lcd_center = lcd_data["center"]
    psf_center = psf_data["center"]
    candidates = psf_data["candidate_rois"]
    energy_cov = psf_data.get("energy_coverage", {})

    roi_candidate_sizes = []
    for rk in ["roi_256", "roi_768", "roi_1024"]:
        r = candidates.get(rk, {})
        if r:
            roi_candidate_sizes.append(r.get("width", 0))

    known_energy: dict[str, float | None] = {}
    for rk in ["roi_256", "roi_512", "roi_768", "roi_1024"]:
        v = energy_cov.get(rk)
        if v is not None:
            known_energy[rk] = v

    manifest = {
        "task": "thesis_optic_system_figures",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "phase3_release": str(phase3_release),
        "hardware_required": False,
        "figures": {
            "lcd_effective_pupil": {
                "output_pdf": str(out_dir / "appendix_lcd_effective_pupil_annotated.pdf"),
                "center_xy": [lcd_center[0], lcd_center[1]],
                "effective_radius_px": lcd_data["effective_radius"],
                "fit_r2": lcd_data["ellipse"].get("r_squared", None),
                "lcd_coordinate_system": "physical_mono",
            },
            "psf_roi_comparison": {
                "output_pdf": str(out_dir / "appendix_psf_roi_comparison.pdf"),
                "psf_center_xy": [psf_center[0], psf_center[1]],
                "selected_roi": int(psf_data["selected_roi"].get("width", 0)) if psf_data["selected_roi"] else 512,
                "roi_candidates": sorted(set([256, 512] + roi_candidate_sizes)),
                "known_energy_coverage": known_energy,
                "camera_coordinate_system": "sensor_pixels",
                "roi_energy_decomposition": {
                    "output_pdf": str(out_dir / "appendix_roi_energy_decomposition.pdf"),
                    "output_csv": str(out_dir / "appendix_roi_energy_decomposition.csv"),
                },
            },
        },
        "calibration_summary_csv": str(out_dir / "appendix_calibration_summary.csv"),
        "energy_decomposition_csv": str(out_dir / "appendix_roi_energy_decomposition.csv"),
    }

    if energy_decomp is not None:
        decomp_meta = energy_decomp["metadata"]
        manifest["figures"]["psf_roi_comparison"]["roi_energy_decomposition"].update({
            "full_frame_denominator": decomp_meta["full_frame_denominator"],
            "noise_floor_artifact_pp": decomp_meta["noise_floor_artifact_pp"],
            "genuine_diffraction_pp": decomp_meta["genuine_diffraction_pp"],
            "support_domains": ["r<200", "r<300", "r<500", "full"],
            "data_provenance": {
                "psf_source": "bishe_psf_roi.h5/raw/frames_avg",
                "dark_frame": "bishe_pupil_geometry.h5/references/dark_frame_avg",
                "background_policy": "5th percentile valid pixel domain",
                "diagnostic_script": "scripts/_diffraction_wing_analysis.py",
            },
        })
    path = out_dir / "thesis_optic_system_figures_manifest.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, default=str, ensure_ascii=False)
    return manifest


def _copy_to_thesis_assets(pdf_paths: list[Path], thesis_assets_dir: Path | None, force: bool) -> None:
    if thesis_assets_dir is None:
        return
    assets = Path(thesis_assets_dir)
    assets.mkdir(parents=True, exist_ok=True)
    for src in pdf_paths:
        dst = assets / src.name
        if dst.exists() and not force:
            print(f"skip (exists, use --force to overwrite): {dst}")
            continue
        import shutil
        shutil.copy2(str(src), str(dst))
        print(f"copied: {src} -> {dst}")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _save_figure(fig: plt.Figure, base_path: Path, dpi: int, fmt: str) -> Path:
    pdf_path = base_path.with_suffix(".pdf")
    png_path = base_path.with_suffix(".png")
    if fmt in ("pdf", "both"):
        fig.savefig(str(pdf_path), dpi=dpi, format="pdf")
    if fmt in ("png", "both"):
        fig.savefig(str(png_path), dpi=dpi, format="png")
    plt.close(fig)
    return pdf_path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="Export thesis appendix calibration diagnostic figures from Phase 3 handoff.")
    parser.add_argument(
        "--phase3-release",
        default=str(DEFAULT_RELEASE_ROOT),
        help=f"Path to Phase 3 handoff release root (default: {DEFAULT_RELEASE_ROOT})",
    )
    parser.add_argument(
        "--out-dir",
        default=str(DEFAULT_OUT_DIR),
        help=f"Output directory for thesis figures (default: {DEFAULT_OUT_DIR})",
    )
    parser.add_argument("--copy-to-thesis-assets", default=None, help="Optional thesis assets directory for PDF copies")
    parser.add_argument("--format", choices=["pdf", "png", "both"], default="both")
    parser.add_argument("--dpi", type=int, default=300, help="Output DPI (default: 300)")
    parser.add_argument("--force", action="store_true", help="Overwrite existing output files")
    args = parser.parse_args()

    release_root = Path(args.phase3_release)
    if not release_root.exists():
        print(f"Phase 3 release root not found: {release_root}")
        print("Use --phase3-release to specify the correct path.")
        raise SystemExit(1)

    manifest = export_thesis_calibration_figures(
        phase3_release=release_root,
        out_dir=args.out_dir,
        copy_to_thesis_assets=args.copy_to_thesis_assets,
        fmt=args.format,
        dpi=args.dpi,
        force=args.force,
    )

    print(f"Manifest: {manifest.get('calibration_summary_csv', '')}")
    print("Done.")


if __name__ == "__main__":
    main()
