# Phase 3 Current Frozen Results

This document records the current Phase 3 results that have already been
reacquired and should be treated as the active baseline when the thread
context is no longer available.

It is not a roadmap. It is the current result freeze.

## Phase 3.0.5b

Canonical output:

- `outputs/exposure_calibration/camera_params_psf_safe.json`
- `data/raw/bishe_psf_safe_exposure.h5`

Current default camera parameters:

- `global_safe_camera.exposure_us = 487.3046875`
- `global_safe_camera.gain_db = 0.0`
- `frame_dtype_full_scale = 255`
- valid-pixel domain: `exclude_top_rows(top_rows=1)`

Current per-wavelength burst-peak margins:

- `450 nm`: `peak_pixel_burst = 231`, margin `24`
- `550 nm`: `peak_pixel_burst = 213`, margin `42`
- `650 nm`: `peak_pixel_burst = 237`, margin `18`

Operational conclusion:

- `camera_params_psf_safe.json` is a per-wavelength safe camera catalog.
- `global_safe_camera` is a derived shared baseline only.
- Later Phase 3 tasks may still use that shared baseline when a phase is
  intentionally wavelength-independent, but Phase 3.3 / 3.4 / 3.6 should use
  `camera_profile_policy: wavelength_recommended`.
- The previous `10 dB` per-gain selection is not the default baseline anymore.
- `550 nm` is still the limiting wavelength.

## Phase 3.1

Canonical output:

- `outputs/pupil_geometry/effective_pupil_window.json`
- `data/raw/bishe_pupil_geometry.h5`

Current effective pupil window in LCD physical coordinates:

- `center.x = 1065.2462265532397`
- `center.y = 1871.5352061814938`
- `radius = 52.79722598558055`
- `radius_factor_of_b = 0.9`
- ellipse fit:
  - `a = 115.51445902470007`
  - `b = 58.66358442842284`
  - `r_squared = 0.999178591231608`

Important result history:

- The current Phase 3.1 result comes from a rerun under the new
  schema v2 safe camera catalog.
- The current derived shared baseline is `0 dB / 487.30 us`, but later
  multi-wavelength phases may subscribe to per-wavelength recommended profiles.
- The final `r scan` required documented cleaning before the result was frozen.
- The cleaned result is the active baseline.

Cleaning policy used:

- single-point spike at `idx=19` removed by linear interpolation
- post-step tail at `idx=156` shifted downward by the short-plateau median gap

Operational conclusion:

- Downstream Phase 3.2+ mask generation must use this cleaned
  `effective_pupil_window.json`.

## Phase 3.2a

Canonical output:

- `outputs/psf_roi/psf_roi.json`
- `data/raw/bishe_psf_roi.h5`

Current camera-frame PSF ROI in camera sensor coordinates:

- center:
  - `x = 1149.1284484551213`
  - `y = 934.5088420118792`
- ROI:
  - `x = [1021, 1277)`
  - `y = [807, 1063)`
  - `width = 256`
  - `height = 256`

Current quality summary:

- `peak_pixel = 238.21999999999997`
- `background_level = 24.98`
- `roi_energy_fraction = 0.44846083647854107`
- `full_scale_in_avg_valid_domain = false`

Important display caveat:

- `psf_roi_preview.png` is a contrast-stretched preview, not a raw linear
  exposure judgment image.
- It is valid for checking where the ROI box lands.
- It must not be used by itself to decide whether the PSF is overexposed or
  whether only the main lobe is present.

Operational conclusion:

- The current frozen baseline ROI is `roi_256`.
- This remains the audited Phase 3.2a baseline.
- A follow-up multi-ROI diagnostic may generate additional ROI candidates
  around the same PSF center for dOTF support/leakage inspection.
- This is not a re-estimation of the PSF center and does not automatically
  select the Phase 3.4 ROI.
- The `256 x 256` baseline has `roi_energy_fraction ≈ 0.44846`.
- Therefore larger ROI candidates are useful for diagnosing PSF support
  truncation and windowed-dOTF leakage.
- After the multi-ROI dOTF comparison, the current manually selected
  Phase 3.4 modelling ROI is `roi_512`.
- This selection keeps `roi_256` as the frozen Phase 3.2a baseline while
  moving Phase 3.4 to a more moderate support window.

Energy decomposition diagnostic:

The apparent 55% leakage for roi_256 under the full-frame denominator is
dominated by a noise-floor integration artifact:

  Full-frame total (background-subtracted):       212,212 counts
  Far field r >= 200 px:                          111,518 counts (52.55%)
    Genuine diffraction peaks (corr >= 0.5):       12,151 counts ( 5.73%)
    Noise-floor integration artifact (corr < 0.5): 99,368 counts (46.82%)

Dark frame (Phase 3.1): mean 24.90, std 0.20.
Scene background (5th percentile): 24.98.
Dark frame is consistent with scene floor (offset 0.08 counts).

Within physically motivated support domains:
  roi_256 enclosed r<200:  94.5%
  roi_256 enclosed r<300:  87.3%
  roi_512 enclosed r<300:  97.4%

Both ROIs achieve >90% enclosed energy within compact support regions.
The full-frame denominator value is preserved as the audited Phase 3.2a
baseline; the noise-floor decomposition is a supplementary diagnostic.
See GitHub issue #58, scripts/_diffraction_wing_analysis.py, and
scripts/export_thesis_calibration_figures.py.
Appendix figure U2c (appendix_psf_tail_enhanced.pdf) provides a full-frame
tail-enhanced visualization with p=0.99 percentile normalization and
magma colormap for diagnosing far-field diffraction peak distribution.

## Phase 3.2b

Canonical output:

- canonical raw:
  - `data/raw/bishe_psf_repeatability_20260519_222907.h5`
- canonical analysis:
  - `outputs/psf_repeatability/repeatability_metrics.json`
  - `outputs/psf_repeatability/diversity_metrics.json`
  - `outputs/psf_repeatability/repeatability_metrics_normalized.json`
  - `outputs/psf_repeatability/diversity_metrics_normalized.json`
  - `outputs/psf_repeatability/spectral_diversity_metrics_normalized.json`
  - `outputs/psf_repeatability/repeatability_report.md`
  - `outputs/psf_repeatability/multi_wavelength_mask_mean_psfs.png`
  - `outputs/psf_repeatability/wl_450p0/mask_mean_psfs.png`
  - `outputs/psf_repeatability/wl_550p0/mask_mean_psfs.png`
  - `outputs/psf_repeatability/wl_650p0/mask_mean_psfs.png`

Canonical-path note:

- `outputs/psf_repeatability/` has been promoted to the latest audited
  rerun analysis.
- The canonical analysis still points back to the audited raw source
  `data/raw/bishe_psf_repeatability_20260519_222907.h5`.

Mask set reacquired:

- `all_open_window`
- `vertical_stripes_lowfreq`
- `horizontal_stripes_lowfreq`
- `checkerboard_lowfreq`
- `central_block`
- `edge_block`
- `random_lowfreq_1`
- `random_lowfreq_2`

Current quantitative conclusion from raw averaged crops:

- `mean_intra_mask_mse = 0.015126694714581536`
- `mean_inter_mask_mse = 4.393628748339698`
- `inter_mask_distance_over_intra_noise = 290.4553064130007`
- `mask_induced_differences_larger_than_repeat_noise = true`

Current stricter quantitative conclusion from background-subtracted + unit-energy normalized crops:

- `mean_intra_mask_mse = 2.5377195578181706e-11`
- `mean_inter_mask_mse = 7.559587679437541e-09`
- `inter_mask_distance_over_intra_noise = 297.88901047588456`
- `mask_induced_differences_larger_than_repeat_noise = true`
- `mean_cross_wavelength_same_mask_mse = 3.306822975612114e-08`
- `cross_wavelength_same_mask_over_intra_noise = 1303.0687198766705`
- `wavelength_induced_differences_larger_than_repeat_noise = true`

Observed repeatability quality:

- same-mask normalized correlation remains close to `1.0`
- center drift remains sub-pixel
- total-energy CV remains small relative to inter-mask separation

Operational conclusion:

- Phase 3.2b is complete for the current baseline.
- The current data supports:
  - same-mask PSFs are repeatable
  - mask-induced PSF differences are much larger than repeatability noise
  - same-mask cross-wavelength PSF shape differences remain much larger than
    repeatability noise after background subtraction and unit-energy normalization
- Fig 3 (fig3_wavelength_psf_scale.pdf) visualizes the same-mask
  cross-wavelength PSF scale dependence via side-by-side log-scale
  comparison and cumulative enclosed energy vs square window
  half-size with r₅₀% vertical markers.
- Interpretation boundary:
  - raw metrics still mix shape differences with residual photometric scaling
  - normalized metrics are the stricter basis for cross-wavelength shape claims
- This is an experimental prerequisite result only. It must not be described
  as forward-model success.

Recommended thesis caption for the Phase 3.2b mean-PSF montage:
"不同掩膜和不同波长下的平均 PSF 形态差异". The combined montage
`multi_wavelength_mask_mean_psfs` has mask patterns as columns and
wavelengths as rows; each tile is the repeat-averaged PSF crop shown with
log intensity. The per-wavelength `wl_*/mask_mean_psfs` figures show the
same eight masks for one wavelength at a time.

## Phase 3.3

Current audited run:

- raw:
  - `data/raw/bishe_dotf_diagnostic_20260520_004205.h5`
- analysis:
  - `outputs/dotf_20260520_004205/dotf_metrics.json`
  - `outputs/dotf_20260520_004205/dotf_report.md`
  - `outputs/dotf_20260520_004205/dotf_roi_comparison_manifest.json`
  - `outputs/dotf_20260520_004205/dotf_roi_comparison_report.md`

Current quantitative conclusion:

- `dotf_computed = true`
- `pupil_stitching_performed = false`
- baseline ROI for compatibility outputs: `roi_256`
- analyzed wavelengths: `450 / 550 / 650 nm`

Representative `dotf_peak_abs` values from the current audited run:

- `450 nm`
  - left `= 0.0001381822738005245`
  - right `= 0.00010904350225226366`
  - top `= 0.0001494530008194772`
  - bottom `= 8.744030696634999e-05`
- `550 nm`
  - left `= 0.00022161369097285598`
  - right `= 0.00024960049813936477`
  - top `= 0.00014791418702452707`
  - bottom `= 0.0002556389159228659`
- `650 nm`
  - left `= 0.00018102115335255704`
  - right `= 0.00013182050162237468`
  - top `= 0.00011399750908913154`
  - bottom `= 0.00013354328734823217`

Multi-ROI perturbation outputs are present under:

- `outputs/dotf_20260520_004205/wl_450p0/<roi_key>/<perturbation_id>/`
- `outputs/dotf_20260520_004205/wl_550p0/<roi_key>/<perturbation_id>/`
- `outputs/dotf_20260520_004205/wl_650p0/<roi_key>/<perturbation_id>/`

Thesis-facing visibility-enhanced dOTF outputs:

- `dotf_log_abs_annotated.png`: log-amplitude panel with a white dashed
  effective pupil-domain / pupil-like copy boundary.
- `dotf_phase_annotated.png`: phase panel with a white arrow indicating the
  stripe direction.
- `dotf_structure_annotated.png`: side-by-side log-amplitude and phase panels.

Recommended thesis Fig. 3-5 caption note:

- 白色虚线/箭头标出本文关注的结构化响应区域。

Historical note:

- `data/raw/bishe_dotf_diagnostic_20260519_234914.h5` was a
  failed/contaminated run because it contained only `perturbed` captures and no
  valid reference pair.
- It was deleted from the workspace after the current audited 3.3 run and
  release package were verified. It is not part of the current baseline.

Operational conclusion:

- Phase 3.3 is complete for the current baseline.
- The current milestone is diagnostic only: dOTF was computed and visualized
  for four edge perturbations.
- No pupil stitching was performed and no full complex-pupil reconstruction is
  claimed.
- Phase 3.3 dOTF can be recomputed for multiple ROI candidates without
  repeating hardware capture, as long as full-frame raw frames are available.
- The purpose is to visually compare dOTF behavior under different PSF support
  windows. No automatic ROI selection is performed.
- The current manual outcome of that comparison is to use `roi_512` for
  Phase 3.4 modelling work.

## Phase 3.4

Current audited run:

- raw:
  - `data/raw/bishe_psf_dictionary_20260520_010603.h5`
- analysis:
  - `outputs/psf_dictionary_20260520_010603/psf_dictionary_summary.json`
  - `outputs/psf_dictionary_20260520_010603/psf_dictionary_manifest.json`
  - `outputs/psf_dictionary_20260520_010603/psf_dictionary_report.md`
- export:
  - `outputs/psf_dictionary_20260520_010603/export_lcd_forward/train.h5`
  - `outputs/psf_dictionary_20260520_010603/export_lcd_forward/val.h5`
  - `outputs/psf_dictionary_20260520_010603/export_lcd_forward/test.h5`

Current audited capture summary:

- `psf_dictionary_acquired = true`
- `psf_roi_key_used = roi_512`
- `wavelengths_nm = [450.0, 550.0, 650.0]`
- `n_masks = 170`
- `repeats_per_mask = 5`
- `export_lcd_forward.enabled = true`

Historical note:

- `data/raw/bishe_psf_dictionary_aborted_before_phase305_rerun_20260519_064654.h5`
  was a historical failed run and is not the current baseline.
- It was deleted from the workspace after the current audited 3.4 run and
  `LCD_forward` release package were verified.
- The current audited 3.4 output has not yet been promoted to the canonical
  directory name `outputs/psf_dictionary/`. Until that promotion happens,
  downstream plans must point explicitly to
  `outputs/psf_dictionary_20260520_010603/export_lcd_forward/`.

Operational conclusion:

- Phase 3.4 is complete for the current baseline.
- There is now a usable measured PSF dictionary export for `LCD_forward`.
- Phase 3.4 uses the manually selected ROI `roi_512` after reviewing the
  Phase 3.3 multi-ROI dOTF comparison.
- `roi_256` remains the frozen Phase 3.2a baseline, but it is not the current
  modelling ROI.
- Phase 3.4 dictionary capture is no longer a full-frame diagnostic task.
  It stores the selected PSF ROI crop only.
- Full-frame preservation for ROI diagnostics and dOTF support inspection
  belongs to Phase 3.2a / 3.3, not to Phase 3.4.
- The `LCD_forward` export is a data handoff only. Forward validation,
  reconstruction, and figure generation remain outside `optic_system`.

## Phase 3.4 closed-LCD residual release

Current independent data release:

- `D:/datasets/optic_system/optic_system_phase3_closed_lcd_residual_release_20260523/`
- core HDF5:
  - `closed_lcd_roi512_avg10_residuals.h5`
- statistics:
  - `closed_lcd_roi512_avg10_stats.csv`

Boundary:

- Release type: `closed-LCD averaged-frame residual release`.
- Source: `all_closed_window` entries from the Phase 3.4 measured PSF
  dictionary raw HDF5.
- Shape: `closed_lcd/crops_avg10 = [3, 5, 512, 512]`.
- Wavelengths: `[450.0, 550.0, 650.0]`.
- Exposure (us): `[779.6875, 487.3046875, 2241.6015625]`.
- Gain (dB): `[0.0, 0.0, 0.0]`.
- Repeats: `5`.
- Averaging: `10 frames per capture`.
- Intended use: averaged-frame additive residual injection for
  measured-PSF-driven simulation in `LCD_forward`.

Forbidden interpretation:

- It is not a real sensor noise release.
- It is not a sensor-only dark release.
- It is not a read-noise, shot-noise, PRNU, or full radiometric calibration
  release.
- It does not replace real target capture.

Current measured summary from the release inspect script:

- `450 nm`: mean_count `24.998287`, residual_std `0.016032`
- `550 nm`: mean_count `24.998260`, residual_std `0.015910`
- `650 nm`: mean_count `24.998308`, residual_std `0.015883`

Validation command:

```powershell
python scripts/validate_closed_lcd_residual_release.py --release-dir D:\datasets\optic_system\optic_system_phase3_closed_lcd_residual_release_20260523
```

Validation result:

- `ok = true`
- `errors = []`

## External handoff

External handoff is now one canonical release with two consumer views:

- artifact root:
  - `D:/datasets/optic_system/phase3_release_20260520/`
- Git-tracked descriptor:
  - `handoff/optic_system_phase3_release_20260520/`

The release contains:

- `common/`: shared docs, provenance, raw HDF5 sources, plans, ROI context
- `lcd_forward/`: Phase 3.4 measured PSF dictionary data contract and HDF5
  export
- `thesis/`: Phase 3.0.5b to 3.3 figures, metrics, reports, and evidence
  summary

This keeps `LCD_forward` and thesis consumers on the same camera parameters,
ROI choice, wavelength list, and source raw files while still presenting
different views for different tasks.

## Active downstream defaults

When starting a new Phase 3.2+ hardware task from a fresh context, the active
defaults are:

- camera params:
  - `outputs/exposure_calibration/camera_params_psf_safe.json`
- pupil window:
  - `outputs/pupil_geometry/effective_pupil_window.json`
- PSF ROI:
  - `outputs/psf_roi/psf_roi.json`
- current measured PSF dictionary export for downstream modelling:
  - `outputs/psf_dictionary_20260520_010603/export_lcd_forward/`

If later reruns replace any of these baseline artifacts, this document must be
updated in the same change.
