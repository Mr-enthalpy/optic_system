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

Until `fast_pupil_scan` profile export is available,
`global_safe_camera` fallback is allowed only when the plan explicitly enables
it and the report records the fallback.

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

## Camera-frame PSF ROI calibration

**Phase:** 3.2a — Camera-frame PSF ROI calibration

**Purpose:** Determine a fixed crop window in camera sensor coordinates for
the point-source PSF.  This is the single source of truth for all subsequent
PSF crops (repeatability, dOTF, PSF dictionary).

**Dependency:** `outputs/pupil_geometry/effective_pupil_window.json`
(Phase 3.1, LCD domain).

### Capture plan

- `plans/bishe_psf_roi.yaml`
- Camera parameters: `outputs/exposure_calibration/camera_params_psf_safe.json`.
- LCD: display the effective pupil window (inside = all-open, outside = all-closed).
- Wavelength: single wavelength, fixed.
- Camera: burst of N frames per capture, K repeats.

### Analysis

1. Load `data/raw/bishe_psf_roi.h5`.
2. Apply dark subtraction or baseline correction on averaged frame.
3. Locate PSF center via peak detection, center-of-mass, or energy connected-component.
4. Choose fixed crop size (by energy envelope fraction or pre-configured size).
5. Write `outputs/psf_roi/psf_roi.json`.

### Output

```text
outputs/psf_roi/psf_roi.json
  - phase: "3.2a"
  - task: "camera_frame_psf_roi_calibration"
  - roi: {x_min, x_max, y_min, y_max, width, height}
  - center: {x, y, method}
  - crop_policy: {type, size, margin_policy, energy_fraction}
  - validity: not scientific calibration valid, not training-ready
```

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
- LCD: all masks inside `effective_pupil_window`.
- Masks: representative mask set, each repeated K times (K >= 10).
- Wavelength: single wavelength, fixed.
- Camera: burst of N frames per capture.

### Analysis

1. Load `raw_capture.h5` - extract frames for each mask x repetition.
2. Apply `psf_roi.json` crop to every frame.
3. Intra-mask repeatability: mean PSF, std PSF, coefficient of variation,
   normalized correlation, PSNR, SSIM among repeats.
4. Inter-mask diversity: pairwise MSE, PSNR, SSIM, cross-correlation,
   Fourier magnitude difference.
5. Confirm that between-mask differences exceed within-mask repeat noise.
6. Write `outputs/psf_repeatability/repeatability_metrics.json`.

### Output

```text
outputs/psf_repeatability/repeatability_metrics.json
  - per-mask repeatability (mean, std, PSNR, SSIM, correlation)
  - between-mask pairwise distances (MSE, PSNR, SSIM)
  - psf_roi provenance recorded
```

## dOTF diagnostic visualization

**Phase:** 3.3 — dOTF diagnostic visualization

**Purpose:** Use dOTF to reveal low-dimensional / sparse pupil or
LCD-induced structure.  Directly migrate old-project `old/perturbation.py`
dOTF computation and visualization logic.

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
- Wavelength: single wavelength, fixed.

### Analysis

1. Load `raw_capture.h5` - extract base PSF and perturbed PSF.
2. Apply `psf_roi.json` crop to both reference and perturbed PSF.
3. Align and optionally normalize energy.
4. Compute dOTF:
   - `PSF -> OTF` (FFT2 with shift)
   - `dOTF = OTF_perturbed - OTF_reference`
5. Visualize dOTF abs, log_abs, phase, real, imag.
6. Interpret observed structure.

### Output

```text
outputs/dotf/
  psf_reference.npy
  psf_perturbed.npy
  otf_reference.npy
  otf_perturbed.npy
  dotf_complex.npy
  dotf_abs.png
  dotf_log_abs.png
  dotf_phase.png
  dotf_real.png
  dotf_imag.png
  dotf_report.md
```

## PSF dictionary

**Purpose:** Build measured mask-to-PSF dictionary and export to
`LCD_forward`-compatible format.

### Capture plans

- `plans/bishe_psf_dict_single_lambda.yaml` - dictionary at one wavelength.
- `plans/bishe_psf_dict_three_lambda.yaml` - dictionary at three wavelengths.
- Camera parameters: `outputs/exposure_calibration/camera_params_psf_safe.json`.
- Masks: gratings at various periods and orientations, checkerboards, radial
  patterns.
- Wavelengths: 1 or 3 wavelengths.

### Analysis

1. Load `raw_capture.h5` - extract averaged PSF for each mask.
2. Apply ROI cropping and possible dark-frame subtraction.
3. Normalize if needed.
4. Package as `LCD_forward`-compatible HDF5:
   ```text
   masks: [N, 1, Hm, Wm]
   psfs:  [N, L, Hp, Wp]
   ```
5. Write `outputs/psf_dictionary/psf_dict_<config>.h5`.

### Output

```text
outputs/psf_dictionary/
  psf_dict_lambda_<wl>nm.h5
  psf_dict_three_lambda.h5
  psf_dictionary_metadata.json
```

## Three-wavelength multiframe linear reconstruction

**Purpose:** Demonstrate multispectral recovery using measured PSFs and simple
linear inverse reconstruction.

### Capture plan

- `plans/bishe_multiframe_target.yaml`
- Masks: target scene(s) (possibly a combination of simple patterns).
- Wavelengths: 3 wavelengths.
- PSF dictionary: same masks acquired at each of the 3 wavelengths.

### Analysis

1. Load PSF dictionary (from M4).
2. Load target scene frames.
3. Apply simple linear reconstruction (e.g., Tikhonov-regularized least squares
   per wavelength, or joint multiframe formulation).
4. Compare reconstruction against known target mask.
5. Write results and metrics.

### Output

```text
outputs/linear_recon/
  multiframe_recon_results/
    recon_<scene_id>_<wl>nm.npy
    recon_<scene_id>_combined.npy
  forward_model_validation.json
  reconstruction_metrics.json
```

## Implementation order

Phases should be implemented sequentially:

1. **Phase 3.1** - LCD-domain effective pupil geometry calibration
2. **Phase 3.2a** - Camera-frame PSF ROI calibration
3. **Phase 3.2b** - PSF repeatability and mask-induced diversity
4. **Phase 3.3** - dOTF diagnostic visualization
5. **Phase 3.4** - PSF dictionary and export
6. **Phase 3.5** - Simple forward model validation
7. **Phase 3.6** - Three-wavelength multiframe linear reconstruction
8. **Phase 3.7** - Thesis figures and report freeze

Each phase depends on the outputs of the previous phases but not on
implementation details.  Phase 3.2a needs the effective pupil window from 3.1
(LCD domain); Phase 3.2b and 3.3 need the PSF ROI from 3.2a (camera domain);
Phase 3.4 needs both; etc.
