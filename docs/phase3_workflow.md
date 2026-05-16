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

## PSF repeatability

**Purpose:** Reacquire and quantify the old finding that PSF differences
exceed repeat noise.

**Old-project reference:** `old/base.py:on_capture_clicked` (multi-frame
averaging), `old/roi.py:find_max_energy_roi` (ROI selection)

### Capture plan

- `plans/bishe_psf_repeatability.yaml`
- Camera parameters: `outputs/exposure_calibration/camera_params_psf_safe.json`.
- Masks: 2-3 distinct masks, each repeated K times.
- Wavelength: single wavelength, fixed.
- Camera: burst of N frames per capture.

### Analysis

1. Load `raw_capture.h5` - extract frames for each mask x repetition.
2. Apply energy-based ROI cropping.
3. Compute within-mask repeatability metrics (e.g., normalized RMS difference,
   correlation).
4. Compute between-mask difference metrics.
5. Confirm that between-mask differences exceed within-mask repeat noise.
6. Write `outputs/psf_repeatability/repeatability_metrics.json`.

### Output

```text
outputs/psf_repeatability/repeatability_metrics.json
  - per-mask repeatability (RMS, correlation)
  - between-mask differences
  - ROI parameters used
```

## dOTF diagnostic

**Purpose:** Use dOTF to reveal low-dimensional / sparse pupil or
LCD-induced structure.

**Old-project reference:** `old/perturbation.py` (mask perturbation),
`old/roi.py:compute_dotf`, `old/roi.py:show_complex_2d`

**Explicit warning:** Full pupil stitching is not the minimum success
criterion.  Observing clear, reproducible structure in the dOTF magnitude
and/or phase is sufficient for this milestone.

### Capture plan

- `plans/bishe_dotf_edge_perturb.yaml`
- Camera parameters: `outputs/exposure_calibration/camera_params_psf_safe.json`.
- Masks: base circular window + base circular window with perturbations at
  various edge positions.
- Wavelength: single wavelength, fixed.

### Analysis

1. Load `raw_capture.h5` - extract base PSF and perturbed PSF.
2. Optionally subtract dark frame.
3. Crop ROI from PSF pair (energy-based or using the effective pupil window).
4. Pad ROI for higher frequency resolution.
5. Compute dOTF:
   - `PSF -> OTF` (FFT2 with shift)
   - Estimate OTF support mask from magnitude threshold
   - Complex least-squares scale to minimize energy outside support
   - `dOTF = OTF_mod - s * OTF_ref`
6. Visualize dOTF magnitude and phase.
7. Interpret observed structure.

### Output

```text
outputs/dotf/
  dotf_<perturbation_id>.npy         (complex 2D arrays)
  dotf_magnitude_<perturbation_id>.png
  dotf_phase_<perturbation_id>.png
  dotf_summary.json                   (metadata, parameters)
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

1. **Phase 3.1** - Effective pupil geometry calibration
2. **Phase 3.2** - PSF repeatability and ROI alignment
3. **Phase 3.3** - dOTF diagnostic
4. **Phase 3.4** - PSF dictionary and export
5. **Phase 3.5** - Simple forward model validation
6. **Phase 3.6** - Three-wavelength multiframe linear reconstruction
7. **Phase 3.7** - Thesis figures and report freeze

Each phase depends on the outputs of the previous phases but not on
implementation details.  Phase 3.2 needs the effective pupil window from 3.1;
Phase 3.3 needs the ROI convention and PSF extraction from 3.2; etc.
