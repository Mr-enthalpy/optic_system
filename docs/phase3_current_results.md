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

- `global_safe_camera.exposure_us = 736.541748046875`
- `global_safe_camera.gain_db = 0.0`
- `frame_dtype_full_scale = 255`
- valid-pixel domain: `exclude_top_rows(top_rows=1)`

Current per-wavelength burst-peak margins:

- `450 nm`: `peak_pixel_burst = 172`, margin `83`
- `550 nm`: `peak_pixel_burst = 243`, margin `12`
- `650 nm`: `peak_pixel_burst = 79`, margin `176`

Operational conclusion:

- All downstream Phase 3 tasks should default to `global_safe_camera`.
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
  `0 dB / 736.54 us` safe camera parameters.
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

## Phase 3.2b

Canonical output:

- `data/raw/bishe_psf_repeatability.h5`
- `outputs/psf_repeatability/repeatability_metrics.json`
- `outputs/psf_repeatability/diversity_metrics.json`
- `outputs/psf_repeatability/repeatability_report.md`
- `outputs/psf_repeatability/mask_mean_psfs.png`

Mask set reacquired:

- `all_open_window`
- `vertical_stripes_lowfreq`
- `horizontal_stripes_lowfreq`
- `checkerboard_lowfreq`
- `central_block`
- `edge_block`
- `random_lowfreq_1`
- `random_lowfreq_2`

Current quantitative conclusion:

- `mean_intra_mask_mse = 0.033240701887342676`
- `mean_inter_mask_mse = 16.028353418023247`
- `inter_mask_distance_over_intra_noise = 482.19058286872365`
- `mask_induced_differences_larger_than_repeat_noise = true`

Observed repeatability quality:

- same-mask normalized correlation remains close to `1.0`
- center drift remains sub-pixel
- total-energy CV remains small relative to inter-mask separation

Operational conclusion:

- Phase 3.2b is complete for the current baseline.
- The current data supports:
  - same-mask PSFs are repeatable
  - mask-induced PSF differences are much larger than repeatability noise
- This is an experimental prerequisite result only. It must not be described
  as forward-model success.

## Phase 3.3

Canonical output:

- `data/raw/bishe_dotf_diagnostic.h5`
- `outputs/dotf/dotf_metrics.json`
- `outputs/dotf/dotf_report.md`
- `outputs/dotf/dotf_roi_comparison_manifest.json`
- `outputs/dotf/dotf_roi_comparison_report.md`

Current quantitative conclusion for the audited baseline ROI `roi_256`:

- `dotf_computed = true`
- `pupil_stitching_performed = false`
- `dotf_peak_abs(edge_block_left) = 0.0003390569974598773`
- `dotf_peak_abs(edge_block_right) = 0.00044685164266314943`
- `dotf_peak_abs(edge_block_top) = 0.00021784801577389982`
- `dotf_peak_abs(edge_block_bottom) = 0.0003818186234985599`

Perturbation outputs present:

- `outputs/dotf/roi_256/`
- `outputs/dotf/roi_512/`
- `outputs/dotf/roi_768/`
- `outputs/dotf/roi_1024/`

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

Current raw target:

- `data/raw/bishe_psf_dictionary.h5`

Current status:

- The previous or partially prepared Phase 3.4 run is superseded by the
  Phase 3.0.5 camera catalog policy change.
- Phase 3.4 must be rerun after the current `camera_params_psf_safe.json`
  schema v2 per-wavelength catalog is produced and adopted.
- No current Phase 3.4 raw file is valid for LCD_forward export.

Operational conclusion:

- Phase 3.4 is not complete.
- There is no usable measured PSF dictionary output yet.
- Phase 3.4 uses the manually selected ROI `roi_512` after reviewing the
  Phase 3.3 multi-ROI dOTF comparison.
- `roi_256` remains the frozen Phase 3.2a baseline, but it is not the current
  modelling ROI.
- Phase 3.4 dictionary capture is no longer a full-frame diagnostic task.
  It stores the selected PSF ROI crop only.
- Full-frame preservation for ROI diagnostics and dOTF support inspection
  belongs to Phase 3.2a / 3.3, not to Phase 3.4.
- External handoff packages must leave Phase 3.4 data empty until the current
  hardware run finishes and is analyzed.

## External handoff

The current external handoff should be assembled as a local export package,
for example under:

- `handoff/phase3_external_release_20260519/`

That package is intended for the thesis project and for `LCD_forward`.

It contains current mainline data plus the ROI decision context.
It does not carry backup / contaminated history as part of the mainline
handoff narrative.

## Active downstream defaults

When starting a new Phase 3.2+ hardware task from a fresh context, the active
defaults are:

- camera params:
  - `outputs/exposure_calibration/camera_params_psf_safe.json`
- pupil window:
  - `outputs/pupil_geometry/effective_pupil_window.json`
- PSF ROI:
  - `outputs/psf_roi/psf_roi.json`

If later reruns replace any of these three files, this document must be
updated in the same change.
