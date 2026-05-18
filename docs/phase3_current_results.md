# Phase 3 Current Frozen Results

This document records the current Phase 3 results that have already been
reacquired and should be treated as the active baseline when the thread
context is no longer available.

It is not a roadmap. It is the current result freeze.

## Phase 3.0.5b

Canonical output:

- [camera_params_psf_safe.json](/C:/Users/Dell/PycharmProjects/optic_system/outputs/exposure_calibration/camera_params_psf_safe.json)
- [bishe_psf_safe_exposure.h5](/C:/Users/Dell/PycharmProjects/optic_system/data/raw/bishe_psf_safe_exposure.h5)

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

- [effective_pupil_window.json](/C:/Users/Dell/PycharmProjects/optic_system/outputs/pupil_geometry/effective_pupil_window.json)
- [bishe_pupil_geometry.h5](/C:/Users/Dell/PycharmProjects/optic_system/data/raw/bishe_pupil_geometry.h5)

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
- The cleaned result is the active baseline; the contaminated raw run was
  preserved, not deleted.

Preserved contaminated file:

- [bishe_pupil_geometry_rscan_contaminated_20260519_025516.h5](/C:/Users/Dell/PycharmProjects/optic_system/data/raw/bishe_pupil_geometry_rscan_contaminated_20260519_025516.h5)

Cleaning report:

- [radius_scan_cleaning_report.json](/C:/Users/Dell/PycharmProjects/optic_system/outputs/pupil_geometry/radius_scan_cleaning_report.json)
- [radius_scan_cleaning_report.md](/C:/Users/Dell/PycharmProjects/optic_system/outputs/pupil_geometry/radius_scan_cleaning_report.md)

Cleaning policy used:

- single-point spike at `idx=19` removed by linear interpolation
- post-step tail at `idx=156` shifted downward by the short-plateau median gap

Operational conclusion:

- Downstream Phase 3.2+ mask generation must use this cleaned
  `effective_pupil_window.json`.

## Phase 3.2a

Canonical output:

- [psf_roi.json](/C:/Users/Dell/PycharmProjects/optic_system/outputs/psf_roi/psf_roi.json)
- [bishe_psf_roi.h5](/C:/Users/Dell/PycharmProjects/optic_system/data/raw/bishe_psf_roi.h5)

Current camera-frame PSF ROI in camera sensor coordinates:

- center:
  - `x = 1148.9956423978238`
  - `y = 934.199642494447`
- ROI:
  - `x = [1021, 1277)`
  - `y = [806, 1062)`
  - `width = 256`
  - `height = 256`

Current quality summary:

- `peak_pixel = 238.82`
- `background_level = 24.98`
- `roi_energy_fraction = 0.448829988729193`
- `full_scale_in_avg_valid_domain = false`

Important display caveat:

- `psf_roi_preview.png` is a contrast-stretched preview, not a raw linear
  exposure judgment image.
- It is valid for checking where the ROI box lands.
- It must not be used by itself to decide whether the PSF is overexposed or
  whether only the main lobe is present.

Operational conclusion:

- All downstream PSF crops must use this `psf_roi.json`.
- Do not re-estimate ROI in later Phase 3 scripts.

## Phase 3.2b

Canonical output:

- [bishe_psf_repeatability.h5](/C:/Users/Dell/PycharmProjects/optic_system/data/raw/bishe_psf_repeatability.h5)
- [repeatability_metrics.json](/C:/Users/Dell/PycharmProjects/optic_system/outputs/psf_repeatability/repeatability_metrics.json)
- [diversity_metrics.json](/C:/Users/Dell/PycharmProjects/optic_system/outputs/psf_repeatability/diversity_metrics.json)
- [repeatability_report.md](/C:/Users/Dell/PycharmProjects/optic_system/outputs/psf_repeatability/repeatability_report.md)
- [mask_mean_psfs.png](/C:/Users/Dell/PycharmProjects/optic_system/outputs/psf_repeatability/mask_mean_psfs.png)

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

- `mean_intra_mask_mse = 0.020824489169650618`
- `mean_inter_mask_mse = 15.698048369816375`
- `inter_mask_distance_over_intra_noise = 753.8263359993741`
- `mask_induced_differences_larger_than_repeat_noise = true`

Observed repeatability quality:

- same-mask normalized correlation is about `0.9994` to `0.9999`
- center drift is about `0.02 px` to `0.12 px`
- total-energy CV is on the order of `1e-4` to `6e-4`

Operational conclusion:

- Phase 3.2b is complete for the current baseline.
- The current data supports:
  - same-mask PSFs are repeatable
  - mask-induced PSF differences are much larger than repeatability noise
- This is an experimental prerequisite result only. It must not be described
  as forward-model success.

## Phase 3.3

Canonical output:

- [bishe_dotf_diagnostic.h5](/C:/Users/Dell/PycharmProjects/optic_system/data/raw/bishe_dotf_diagnostic.h5)
- [dotf_metrics.json](/C:/Users/Dell/PycharmProjects/optic_system/outputs/dotf/dotf_metrics.json)
- [dotf_report.md](/C:/Users/Dell/PycharmProjects/optic_system/outputs/dotf/dotf_report.md)
- [psf_reference.png](/C:/Users/Dell/PycharmProjects/optic_system/outputs/dotf/psf_reference.png)

Current quantitative conclusion:

- `dotf_computed = true`
- `pupil_stitching_performed = false`
- `dotf_peak_abs(edge_block_left) = 0.0004062839982924802`
- `dotf_peak_abs(edge_block_right) = 0.0004193781484524579`
- `dotf_peak_abs(edge_block_top) = 0.0004046599696057576`
- `dotf_peak_abs(edge_block_bottom) = 0.0005967033596909086`

Perturbation outputs present:

- [edge_block_left](/C:/Users/Dell/PycharmProjects/optic_system/outputs/dotf/edge_block_left)
- [edge_block_right](/C:/Users/Dell/PycharmProjects/optic_system/outputs/dotf/edge_block_right)
- [edge_block_top](/C:/Users/Dell/PycharmProjects/optic_system/outputs/dotf/edge_block_top)
- [edge_block_bottom](/C:/Users/Dell/PycharmProjects/optic_system/outputs/dotf/edge_block_bottom)

Operational conclusion:

- Phase 3.3 is complete for the current baseline.
- The current milestone is diagnostic only: dOTF was computed and visualized
  for four edge perturbations.
- No pupil stitching was performed and no full complex-pupil reconstruction is
  claimed.

## Phase 3.4

Current attempted raw file:

- [bishe_psf_dictionary.h5](/C:/Users/Dell/PycharmProjects/optic_system/data/raw/bishe_psf_dictionary.h5)

Current attempted-run status:

- hardware run attempted under the current `global_safe_camera`
- capture failed before any useful acquisition
- `processing_flags_json.completed = false`
- `processing_flags_json.n_captures_written = 0`

Current blocker:

- capture path TLS API mismatch:
  - `'TLSService' object has no attribute 'set_target_wavelength_nm'`

Operational conclusion:

- Phase 3.4 is not complete.
- There is no usable measured PSF dictionary output yet.
- The current 3.4 state is "attempted and blocked", not "pending but untried".

## Active downstream defaults

When starting a new Phase 3.2+ hardware task from a fresh context, the active
defaults are:

- camera params:
  - [camera_params_psf_safe.json](/C:/Users/Dell/PycharmProjects/optic_system/outputs/exposure_calibration/camera_params_psf_safe.json)
- pupil window:
  - [effective_pupil_window.json](/C:/Users/Dell/PycharmProjects/optic_system/outputs/pupil_geometry/effective_pupil_window.json)
- PSF ROI:
  - [psf_roi.json](/C:/Users/Dell/PycharmProjects/optic_system/outputs/psf_roi/psf_roi.json)

If later reruns replace any of these three files, this document must be
updated in the same change.
