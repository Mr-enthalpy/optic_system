# Phase 3 technical workflow

## Provenance rule

No thesis-usable result is valid unless it records:

- source `raw_capture.h5` path
- capture plan ID
- mask IDs or mask generation recipe
- wavelength metadata
- camera parameter source
- preprocessing parameters
- script name
- git commit if available

## Phase 3.0.5b dependency

All downstream capture tasks use:

```text
outputs/exposure_calibration/camera_params_psf_safe.json
```

Current frozen Phase 3.0.5b baseline:

- `global_safe_camera.exposure_us = 487.3046875`
- `global_safe_camera.gain_db = 0.0`
- `550 nm` is the limiting wavelength

Interpretation boundary:

- `camera_params_psf_safe.json` is a per-wavelength safe camera parameter
  catalog.
- `global_safe_camera` is a derived shared baseline only.
- Phases that explicitly declare `camera_profile_policy:
  wavelength_recommended` must use the per-wavelength recommended profiles
  rather than forcing `global_safe_camera`.

See `docs/phase3_current_results.md`.

## Data flow overview

```text
capture plan (YAML)
  -> capture_forward_dataset.py
  -> raw_capture.h5
  -> preprocessing / ROI / alignment
  -> experiment-specific analysis
  -> LCD_forward-compatible HDF5 (if needed)
  -> thesis figures
```

All processing steps between `raw_capture.h5` and final figures are
reproducible scripts that read from HDF5 and write results to `outputs/`.
No intermediate step re-acquires hardware data directly.

## Run-status monitor integration

Long-running capture and calibration tasks support an optional `--status-dir`
argument.  When provided, the task publishes runtime diagnostics:

- `state.json` - task identity, phase, progress, completion
- `current_mask_preview.png` or `.npy` - downsample preview of active LCD mask
- `latest_frame_preview_fast.npy` - optional fast camera preview side channel
- `latest_frame_preview.png` or `.npy` - latest task-published camera frame
  preview fallback
- `frame_stats.json` - strict peak-pixel and signal diagnostics
- `log.jsonl` - structured event log

A read-only monitor reads these files:

```bash
python scripts/monitor_run_status.py --status-dir outputs/run_status/latest
```

The monitor never connects to hardware and never controls the task.  See
`docs/readonly_monitor_gui.md`.

The status directory is transient runtime diagnostics, not thesis evidence.
`raw_capture.h5` remains the sole experimental record.

For camera preview status, tasks publish raw frame arrays by default and leave
Bayer display encoding to the read-only monitor. This keeps Bayer-pattern
assumptions out of capture tasks when camera metadata is unavailable.

## Data preservation rules

Raw capture HDF5 files are the sole auditable record of every hardware run.
They must not be casually deleted.

1. Every HDF5 produced by a hardware task is experimental evidence.
2. Before deleting any HDF5, confirm it is a duplicate, corrupted beyond
   recovery, or explicitly superseded by a documented re-run whose output
   has been validated.
3. Corrupted or contaminated HDF5 must be **renamed** with a descriptive
   suffix (e.g. ``_lamp_drift``, ``_y_contaminated``) rather than deleted.
4. Each raw HDF5 whose bar scan fit fails still contains valid bar profile
   data — it must be kept.  Use ``--resume-from-h5`` to load bar data and
   re-run the radius scan without re-acquiring hardware.
5. The provenance rule (``source_raw_capture_h5``) depends on these files
   existing.  Downstream outputs referencing a deleted HDF5 become
   unauditable.

A ``dataset_manifest.json`` under each output directory records every run,
its status (raw / cleaned / contaminated / complete), and cleaning history.

## Phase 3.0.5b full-scale rule

Canonical hardware PSF-safe exposure calibration must resolve
`frame_dtype_full_scale` from camera frame metadata, currently the frame stream
pixel-format metadata. Hardware runs fail if this metadata is unavailable.
They must not infer the strict PSF safety limit from observed pixel values or
from an ndarray dtype fallback.

The calibration output JSON and raw sweep HDF5 record
`frame_dtype_full_scale_source` so failure artifacts can be audited without
replaying the run log.

## Phase 3.0.5b valid-pixel domain

The strict peak-pixel safety rule applies to an explicit valid camera pixel
domain. Full-frame mode is equivalent only when every camera pixel is treated
as valid. Known invalid pixels must be excluded through a recorded
`valid_pixel_domain` policy, not through silent hardcoded row or pixel drops.

Inside the valid domain, the rule remains zero-tolerance: any non-finite pixel
or any pixel greater than or equal to `frame_dtype_full_scale` fails PSF-safe
calibration. Pixels outside the valid domain do not change `psf_safe`, but
full-scale or non-finite artifacts there are recorded as diagnostics in both
the raw sweep HDF5 and `camera_params_psf_safe.json`.

Signal metrics used for exposure/gain selection are computed over the same
valid camera pixel domain used for PSF safety. Invalid-domain artifacts cannot
raise `p_signal`, `dynamic_range`, or change `low_signal`; they only appear in
invalid-domain diagnostics.

For the canonical `exclude_top_rows` policy, run
`scripts/diagnose_valid_pixel_domain.py` before hardware Phase 3.0.5b and
update the plan's `valid_pixel_domain.source_artifact` to the generated JSON.
The committed plan intentionally keeps a placeholder artifact path so a fresh
checkout does not look silently configured with local lab evidence. If the
artifact is missing, hardware calibration fails fast by design.

Phase 3.1 capture scripts require `camera_params_psf_safe.json` to carry
`psf_safety_policy.evaluated_domain == "valid_camera_pixel_domain"` and a
`psf_safety_policy.valid_pixel_domain` provenance block. This prevents
downstream pupil geometry, repeatability, and dictionary captures from consuming
unscoped camera safety parameters.

## Valid-pixel domain diagnostic (standalone, dark-field)

**Phase:** 3.0.5b prerequisite.  Must run **before** `calibrate_psf_safe_exposure.py`.

**This is a manual physical procedure and cannot be chained automatically.**

The valid-pixel domain probe must run in true dark-field:
no intentional light reaches the camera sensor.  Any detected full-scale
pixel at short exposure under dark-field is a sensor defect, not a light
signal.

Procedure:

1. Physically turn off the light source (Xe lamp off).
2. Ensure the LCD displays all-opaque: `lcd.show_all_opaque()`.
3. Run `scripts/diagnose_valid_pixel_domain.py --hardware ...`.
4. This produces a JSON file under
   `outputs/diagnostics/shutter_gain_peak_probe/`.
5. Physically turn on the light source (Xe lamp on).
6. Wait for the lamp to reach stable output (warm-up time depends on
   the lamp model; consult the lamp manual).
7. Update `plans/bishe_psf_safe_exposure.yaml` to point
   `valid_pixel_domain.source_artifact` at the generated JSON.

The Xe lamp start-up and shut-down are slow physical operations.
Automation must not attempt to toggle the lamp between these steps.
The script itself does **not** control the light source.

If the probe is run with the light source on, light-responsive pixels
may be misclassified as sensor defects, especially at higher exposures.
The resulting `valid_pixel_domain` would exclude valid pixels from the
PSF safety check, defeating its purpose.

## Effective Pupil Geometry Calibration

**Purpose:** Calibrate an effective pupil window in LCD physical coordinates.

**Old-project references:** `old/calibrating.py`, `old/circle.py`, and
`old/ellipse.py`.

Phase 3.1 calibrates an effective pupil window using energy-based bar
profiles and circular-radius scans. The first pass records X/Y total-energy
profiles from dark bars on a bright background and fits a circle center and
initial radius. The second pass records energy versus transparent circular
window radius on a dark background and fits the circle/ellipse overlap model.
Downstream captures should encode masks inside the resulting effective pupil
window instead of using an all-transmissive LCD.

The current frozen hardware result uses `global_safe_camera` directly.
The active plan no longer requests `fast_pupil_scan`.

### Capture plan

- `plans/bishe_pupil_geometry.yaml`
- Camera parameters: `outputs/exposure_calibration/camera_params_psf_safe.json`
  is mandatory; `camera_profile: fast_pupil_scan` is preferred.
- **TLS requirement:** The TLS must filter the light source to the planned
  wavelength.  Without monochromatic filtering the broadband white light will
  overexpose the camera with PSF-safe parameters.  Hardware runs require
  `--tls-serial` or `TLS_C1_SERIAL`.  The dangerous
  `--allow-wavelength-labels-without-tls` override is reserved for explicit
  manual external wavelength control.
- Masks: bright/dark references, X/Y dark bars, and circular apertures.
- Wavelength: single wavelength, fixed.
- Camera: averaged frames per reference, bar position, and aperture radius.

### Analysis

1. Load `data/raw/bishe_pupil_geometry.h5`.
2. Fit the circle center and initial radii from X/Y bar energy profiles.
3. Fit ellipse semi-axes and scale from radius-scan energy using the
   circle/ellipse overlap model.
4. Set the effective circular window radius to `radius_factor_of_b * b`.
5. Write `outputs/pupil_geometry/effective_pupil_window.json`.

### Output

```text
outputs/pupil_geometry/effective_pupil_window.json
  - phase: "3.1"
  - camera_profile_requested / camera_profile_used
  - center: LCD physical-coordinate pupil center
  - ellipse: fitted a, b, k and fit quality
  - radius: effective circular pupil window radius
  - validity flags: not scientific calibration valid and not training-ready
```

**Coordinate system note:** `effective_pupil_window.json` is in **LCD physical
coordinates**.  The camera-frame PSF crop is a separate calibration in
Phase 3.2a (`outputs/psf_roi/psf_roi.json`) and uses **camera sensor
coordinates**.  These are different coordinate systems and must not be
conflated.

Current frozen Phase 3.1 result:

- `center ≈ (1065.2462, 1871.5352)` in LCD physical coordinates
- `radius ≈ 52.7972 px`
- this result comes from a cleaned `r scan`

## Camera-frame PSF ROI calibration

**Phase:** 3.2a — Camera-frame PSF ROI calibration

**Purpose:** Determine a fixed crop window in camera sensor coordinates for
the point-source PSF.  This is the single source of truth for all subsequent
PSF crops (repeatability, dOTF, PSF dictionary).

**Dependency:** `outputs/pupil_geometry/effective_pupil_window.json`
(Phase 3.1, LCD domain).

Phase 3.2 is data-first. It adds task-specific scripts and lightweight local
helpers only; it does not introduce a reusable PSF framework, ROI subsystem,
or workflow engine.

### Capture plan

- `plans/bishe_psf_roi.yaml`
- Camera parameters: `outputs/exposure_calibration/camera_params_psf_safe.json`.
- LCD: display the effective pupil window (inside = all-open, outside = all-closed).
- Wavelength: single wavelength, fixed.
- Camera: burst of N frames per capture, K repeats.
- Hardware validation rejects `lcd.settle_ms < 100`; the default is 200 ms.
- Hardware capture refuses to overwrite an existing `output.raw_h5`; rename
  the existing raw HDF5 or choose a new output path before rerunning.

### Analysis

1. Load `data/raw/bishe_psf_roi.h5`.
2. Apply dark subtraction or baseline correction on averaged frame.
3. Locate PSF center via peak detection followed by local center-of-mass.
4. Choose the configured fixed crop size, currently 256 x 256.
5. Write `outputs/psf_roi/psf_roi.json`.

The frozen baseline remains `roi_256`. Follow-up analysis may generate larger
ROI candidates around the same PSF center for dOTF support/leakage inspection.
This does not re-estimate the PSF center and does not automatically select a
final modelling ROI.
The current manual modelling choice after that comparison is `roi_512`.

### Output

```text
outputs/psf_roi/psf_roi.json
  - phase: "3.2a"
  - task: "camera_frame_psf_roi_calibration"
  - roi: {x_min, x_max, y_min, y_max, width, height}
  - center: {x, y, method}
  - quality: {peak_pixel, mean_pixel, background_level, roi_energy_fraction,
    full_scale_in_avg_valid_domain}
  - validity: not scientific calibration valid, not training-ready
```

`full_scale_in_avg_valid_domain` is an averaged-frame quality diagnostic. It
is not equivalent to the Phase 3.0.5b raw-burst strict PSF-safety rule.

Current frozen Phase 3.2a ROI:

- center `≈ (1148.996, 934.200)` in camera sensor coordinates
- ROI `x=[1021,1277), y=[806,1062), 256 x 256`
- `roi_energy_fraction ≈ 0.44883`

Preview caveat:

- `psf_roi_preview.png` is contrast-stretched. Use it to verify ROI placement,
  not to decide whether the raw PSF is overexposed or whether only the main
  lobe is present.

## PSF repeatability and mask-induced diversity

**Phase:** 3.2b — PSF repeatability and mask-induced diversity

**Purpose:** Reacquire and quantify the old finding that PSF differences
exceed repeat noise.

**Old-project reference:** `old/base.py:on_capture_clicked` (multi-frame
averaging), `old/roi.py:find_max_energy_roi` (ROI selection)

**Dependency:** `outputs/psf_roi/psf_roi.json` (Phase 3.2a, camera domain).

### Capture plan

- `plans/bishe_psf_repeatability.yaml`
- Camera parameters: `outputs/exposure_calibration/camera_params_psf_safe.json`.
- LCD: all masks are restricted by
  `outputs/pupil_geometry/effective_pupil_window.json`; outside the window is
  opaque.
- Masks: representative mask set, each repeated K times (K >= 10).
- Wavelengths: one or more thesis wavelengths. The active plan uses
  `wavelength_recommended` profiles for `450/550/650 nm`.
- Camera: burst of N frames per capture.
- Hardware validation rejects `lcd.settle_ms < 100`; the default is 200 ms.
- Hardware capture refuses to overwrite an existing `output.raw_h5`; rename
  the existing raw HDF5 or choose a new output path before rerunning.

### Analysis

1. Load `raw_capture.h5` - extract frames for each mask x repetition.
2. Apply `psf_roi.json` crop to every frame.
3. Group captures by wavelength.
4. For each wavelength: intra-mask repeatability via mean PSF, std PSF,
   coefficient of variation, normalized correlation, PSNR, and SSIM among
   repeats.
5. For each wavelength: inter-mask diversity via pairwise MSE, PSNR, SSIM,
   cross-correlation, and Fourier magnitude difference.
6. For each mask: compare repeat-averaged PSFs across wavelengths to measure
   same-mask spectral diversity.
7. Re-run the same comparisons after background subtraction and unit-energy
   normalization so cross-wavelength shape claims are not driven mainly by
   residual photometric scaling.
8. Confirm that both between-mask and cross-wavelength PSF differences exceed
   within-condition repeat noise where applicable.
9. Write `repeatability_metrics.json`, `diversity_metrics.json`,
   `psf_diversity_metrics.json`, wavelength-grouped outputs,
   `spectral_diversity_metrics.json`, and normalized companion metrics when
   multiple wavelengths are present.

### Output

```text
outputs/psf_repeatability/repeatability_metrics.json
  - per-mask repeatability (mean, std, PSNR, SSIM, correlation)
  - between-mask pairwise distances (MSE, PSNR, SSIM)
  - same-mask cross-wavelength PSF difference summary when multiple
    wavelengths are present
  - inter_mask_distance / intra_mask_repeat_noise
  - psf_roi provenance recorded
outputs/psf_repeatability/repeatability_metrics_normalized.json
  - background-subtracted + unit-energy normalized companion analysis
outputs/psf_repeatability/diversity_metrics_normalized.json
  - stricter mask-diversity analysis with reduced global energy-scale influence
outputs/psf_repeatability/spectral_diversity_metrics_normalized.json
  - stricter same-mask cross-wavelength shape-difference analysis
```

The conclusion is limited to whether mask-induced and wavelength-induced PSF
differences are larger than repeatability noise. For cross-wavelength claims,
the normalized companion metrics are the stricter basis because they suppress
global photometric scaling. It must not be described as forward-model success.

Current frozen Phase 3.2b result:

- raw averaged crops:
  - `mean_intra_mask_mse ≈ 0.0151267`
  - `mean_inter_mask_mse ≈ 4.39363`
  - `inter_mask_distance_over_intra_noise ≈ 290.455`
- background-subtracted + unit-energy normalized crops:
  - `mean_intra_mask_mse ≈ 2.53772e-11`
  - `mean_inter_mask_mse ≈ 7.55959e-09`
  - `inter_mask_distance_over_intra_noise ≈ 297.889`
  - `mean_cross_wavelength_same_mask_mse ≈ 3.30682e-08`
  - `cross_wavelength_same_mask_over_intra_noise ≈ 1303.07`
  - `wavelength_induced_differences_larger_than_repeat_noise = true`
- `mask_induced_differences_larger_than_repeat_noise = true`

This is the current audited basis for entering Phase 3.3. PSF-difference
claims belong to Phase 3.2b; Phase 3.3 is a dOTF visualization milestone.

## dOTF diagnostic visualization

**Phase:** 3.3 — dOTF diagnostic visualization

**Purpose:** Use dOTF to reveal low-dimensional / sparse pupil or
LCD-induced structure.  Directly migrate old-project `old/perturbation.py`
dOTF computation and visualization logic. This phase is diagnostic
visualization, not the main PSF-difference milestone.

**Old-project reference:** `old/perturbation.py` (mask perturbation),
`old/roi.py:compute_dotf`, `old/roi.py:show_complex_2d`

**Dependency:** `outputs/psf_roi/psf_roi.json` (Phase 3.2a, camera domain).

**Explicit warning:** Full pupil stitching is not the minimum success
criterion.  Observing clear, reproducible structure in the dOTF magnitude
and/or phase is sufficient for this milestone.

### Capture plan

- `plans/bishe_dotf_diagnostic.yaml`
- Camera parameters: `outputs/exposure_calibration/camera_params_psf_safe.json`.
- LCD: all masks inside `effective_pupil_window`.
- Masks: base window mask + base window with edge-local perturbations at
  various positions.
- Wavelengths: one or more thesis wavelengths. Current active plan uses the
  configured wavelength list and analyzes each wavelength separately. Any
  cross-wavelength comparison here is secondary diagnostic context, not the
  Phase 3.2b PSF diversity conclusion.

### Analysis

1. Load `raw_capture.h5` and group captures by wavelength.
2. For each wavelength, extract base PSF and perturbed PSF.
3. Apply `psf_roi.json` crop to both reference and perturbed PSF.
4. Align and optionally normalize energy.
5. Compute dOTF:
   - `PSF -> OTF` (FFT2 with shift)
   - `dOTF = OTF_perturbed - OTF_reference`
6. Visualize dOTF abs, log_abs, phase, real, imag.
7. Compare dOTF behavior across ROI candidates and wavelengths.
8. Interpret observed structure as a diagnostic only; do not stitch or claim
   a final complex pupil reconstruction.

### Output

```text
outputs/dotf/
  dotf_metrics.json
  dotf_report.md
  dotf_roi_comparison_manifest.json
  dotf_roi_comparison_report.md
  wl_450p0/<roi_key>/<perturbation_id>/...
  wl_550p0/<roi_key>/<perturbation_id>/...
  wl_650p0/<roi_key>/<perturbation_id>/...

Single-wavelength runs keep the legacy top-level baseline outputs:

```text
outputs/dotf/
  psf_reference.npy
  psf_reference.png
  <perturbation_id>/psf_perturbed.npy
  <perturbation_id>/otf_reference.npy
  <perturbation_id>/otf_perturbed.npy
  <perturbation_id>/dotf_complex.npy
  <perturbation_id>/dotf_abs.png
  <perturbation_id>/dotf_log_abs.png
  <perturbation_id>/dotf_phase.png
  <perturbation_id>/dotf_real.png
  <perturbation_id>/dotf_imag.png
  <roi_key>/<perturbation_id>/...
```
```

Current frozen Phase 3.3 result:

- `dotf_computed = true`
- `pupil_stitching_performed = false`
- analyzed wavelengths:
  - `450 nm`
  - `550 nm`
  - `650 nm`
- perturbations completed:
  - `edge_block_left`
  - `edge_block_right`
  - `edge_block_top`
  - `edge_block_bottom`
- current audited raw:
  - `data/raw/bishe_dotf_diagnostic_20260520_004205.h5`
- current audited analysis:
  - `outputs/dotf_20260520_004205/`

This is the current audited basis for entering Phase 3.4. It is a diagnostic
visualization result only, not a stitched pupil result.

Phase 3.3 dOTF can be recomputed for multiple ROI candidates without repeating
hardware capture, as long as full-frame raw frames are available.

The purpose is to visually compare dOTF behavior under different PSF support
windows. No automatic ROI selection is performed.
The current manual outcome of that comparison is `roi_512` for Phase 3.4.

## PSF dictionary

**Purpose:** Build a data-first measured mask-to-PSF dictionary and export a
derived LCD_forward-compatible dataset without training a forward model.

### Capture plans

- `plans/bishe_psf_dictionary.yaml` - representative single-wavelength measured
  measured PSF dictionary across the planned wavelength list.
- Camera parameters: `outputs/exposure_calibration/camera_params_psf_safe.json`.
- Inputs: `effective_pupil_window.json` and `psf_roi.json`.
- Masks: deterministic representative masks plus seeded random low/mid
  frequency masks and task-related patterns. Every physical mask is limited by
  the effective pupil window, and the lowres control mask is preserved.
- Wavelengths: all wavelengths listed in `plans/bishe_psf_dictionary.yaml`.

### Analysis

1. Load `raw_capture.h5` - extract per-repeat PSF ROI crops, lowres masks,
   wavelength labels, mask IDs, and complete provenance.
2. Group by `mask_id` and wavelength, compute repeat-averaged PSF crops, and
   summarize repeat noise / center drift / energy variation.
3. Write preview contact sheets and `.npy` stacks for reproducible analysis.
4. Export repeat-averaged pairs as `LCD_forward`-compatible HDF5:
   ```text
   masks: [N, 1, 1, 64, 64]
   psfs:  [N, 1, L, Hp, Wp]
   ```
5. Keep the raw HDF5 as the source of truth.

### Output

```text
outputs/psf_dictionary/
  psf_dictionary_summary.json
  psf_dictionary_manifest.json
  mask_preview_contact_sheet.png
  psf_preview_contact_sheet.png
  psf_mean_stack.npy
  psf_crop_stack.npy
  mask_lowres_stack.npy
  export_lcd_forward/train.h5
  export_lcd_forward/val.h5
  export_lcd_forward/test.h5
  psf_dictionary_report.md
```

Current Phase 3.4 state:

- current audited raw:
  - `data/raw/bishe_psf_dictionary_20260520_010603.h5`
- current audited analysis:
  - `outputs/psf_dictionary_20260520_010603/`
- `psf_dictionary_acquired = true`
- `psf_roi_key_used = roi_512`
- `wavelengths_nm = [450.0, 550.0, 650.0]`
- `n_masks = 170`
- `repeats_per_mask = 5`
- `export_lcd_forward.enabled = true`
- the historical failed raw file is preserved for audit only and is not part of
  the current baseline
- the current audited 3.4 result is timestamped and has not yet been promoted
  to the canonical directory name `outputs/psf_dictionary/`

Operational implication:

- Phase 3.4 is complete for the current baseline.
- There is now a usable measured PSF dictionary export for `LCD_forward`.
- Phase 3.4 uses the manually selected ROI `roi_512` after the Phase 3.3
  multi-ROI dOTF comparison.
- `roi_256` remains the frozen baseline, but it is not the current modelling
  ROI.
- Phase 3.4 now captures the selected ROI crop only. Full-frame preservation
  for ROI diagnostics belongs to Phase 3.2a / 3.3.
- Until the 3.4 result is promoted to `outputs/psf_dictionary/`, downstream
  consumers must point explicitly to
  `outputs/psf_dictionary_20260520_010603/export_lcd_forward/`.

## Backend boundary from Phase 3.5 onward

From Phase 3.5 onward, `optic_system` is no longer the modelling backend.
`optic_system` remains responsible only for hardware-side data acquisition and
export. `LCD_forward` consumes exported HDF5 files and performs forward
validation, rendering, reconstruction, and thesis figure generation.

## Phase 3.5 in optic_system

**Status:** skipped in `optic_system`

Measured-PSF forward validation belongs to `LCD_forward`. `optic_system` does
not implement forward-model fitting, held-out forward validation, or
prediction-vs-measurement figure generation.

## Phase 3.6 target capture and export

**Purpose:** Capture real target observations under the same lowres mask
sequence used by the measured PSF dictionary, preserve full-frame raw data,
and export a reconstruction-ready HDF5 for `LCD_forward`.

### Capture plan

- `plans/bishe_target_capture.yaml`
- Inputs:
  - `outputs/pupil_geometry/effective_pupil_window.json`
  - `outputs/psf_roi/psf_roi.json`
  - `outputs/exposure_calibration/camera_params_psf_safe.json`
  - current audited 3.4 export, currently:
    - `outputs/psf_dictionary_20260520_010603/export_lcd_forward/train.h5`
    - `outputs/psf_dictionary_20260520_010603/export_lcd_forward/val.h5`
    - `outputs/psf_dictionary_20260520_010603/export_lcd_forward/test.h5`
- Masks: selected lowres masks from the Phase 3.4 measured dictionary export.
- Wavelengths: one or more wavelengths, recorded explicitly in raw HDF5.
- Target: real static target, documented by `target_id`, description, and notes.

### optic_system capture/export responsibilities

1. Load the Phase 3.4 exported lowres masks and their `mask_id`s.
2. Map lowres masks back to pupil-window-limited physical LCD masks.
3. Capture full-frame averaged observations and PSF-ROI crops for each
   wavelength x mask x repeat condition.
4. Preserve lowres masks, wavelength labels, mask IDs, target metadata, and
   complete provenance in `data/raw/bishe_target_capture.h5`.
5. Export `outputs/target_capture/export_lcd_forward/target_frames.h5` for
   downstream reconstruction.

Phase 3.6 boundary:

- Phase 3.6 may preserve full-frame averaged target observations together with
  ROI crops.
- This is intentionally different from Phase 3.4, which now stores the
  selected PSF ROI crop only.
- Phase 3.6 capture should use the current audited 3.4 export source unless
  and until a canonical `outputs/psf_dictionary/` promotion is performed.

### LCD_forward responsibilities

1. Load measured PSF dictionary exports and target-frame exports.
2. Build the forward operator.
3. Run reconstruction.
4. Produce quantitative and qualitative result figures.

### Output

```text
data/raw/bishe_target_capture.h5
outputs/target_capture/
  export_lcd_forward/target_frames.h5
  README.md
```

## Phase 3.7 in optic_system

**Status:** skipped in `optic_system`

Thesis figure aggregation, report freeze, and final result packaging belong to
`LCD_forward` outputs or a separate thesis-writing workspace.

## Implementation order

Phases should be implemented sequentially:

1. **Phase 3.1** - LCD-domain effective pupil geometry calibration
2. **Phase 3.2a** - Camera-frame PSF ROI calibration
3. **Phase 3.2b** - PSF repeatability and mask-induced diversity
4. **Phase 3.3** - dOTF diagnostic visualization
5. **Phase 3.4** - PSF dictionary and export
6. **Phase 3.5** - Forward validation in `LCD_forward`
7. **Phase 3.6** - Target capture/export in `optic_system`, reconstruction in `LCD_forward`
8. **Phase 3.7** - Thesis figure freeze outside `optic_system`

Each phase depends on the outputs of the previous phases but not on
implementation details.  Phase 3.2a needs the effective pupil window from 3.1
(LCD domain); Phase 3.2b and 3.3 need the PSF ROI from 3.2a (camera domain);
Phase 3.4 needs both; etc.
