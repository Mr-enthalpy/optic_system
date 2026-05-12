# Phase 3 technical workflow

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

## Effective LCD pupil scan

**Purpose:** Locate the LCD region that actually affects the optical system.

**Old-project reference:** `old/calibrating.py:locate_aperture_and_build_roi`

### Capture plan

- `plans/bishe_pupil_scan.yaml`
- Masks: a narrow vertical bar swept across X, a narrow horizontal bar swept
  across Y.
- Wavelength: single wavelength, fixed.
- Camera: averaged frames per bar position.

### Analysis

1. Load `raw_capture.h5` — extract averaged frames for each bar position.
2. For each frame, compute energy (sum of pixel intensities).
3. Fit circle parameters (center, radius) from X and Y energy-difference
   profiles (`old/circle.py:_fit_circle_from_profile` provides the algorithm).
4. Optionally refine with ellipse fit (`old/ellipse.py`).
5. Write `outputs/pupil_scan/effective_lcd_roi.json`.

### Output

```text
outputs/pupil_scan/effective_lcd_roi.json
  - xc, yc: effective pupil center (LCD pixel coordinates)
  - r_avg: average effective radius
  - r_x, r_y: per-axis radii
  - optionally: a, b (ellipse semi-axes)
```

## PSF repeatability

**Purpose:** Reacquire and quantify the old finding that PSF differences
exceed repeat noise.

**Old-project reference:** `old/base.py:on_capture_clicked` (multi-frame
averaging), `old/roi.py:find_max_energy_roi` (ROI selection)

### Capture plan

- `plans/bishe_psf_repeatability.yaml`
- Masks: 2-3 distinct masks, each repeated K times.
- Wavelength: single wavelength, fixed.
- Camera: burst of N frames per capture.

### Analysis

1. Load `raw_capture.h5` — extract frames for each mask × repetition.
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
- Masks: base circular window + base circular window with perturbations at
  various edge positions.
- Wavelength: single wavelength, fixed.

### Analysis

1. Load `raw_capture.h5` — extract base PSF and perturbed PSF.
2. Optionally subtract dark frame.
3. Crop ROI from PSF pair (energy-based or using pupil scan results).
4. Pad ROI for fine frequency resolution.
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

- `plans/bishe_psf_dict_single_lambda.yaml` — dictionary at one wavelength.
- `plans/bishe_psf_dict_three_lambda.yaml` — dictionary at three wavelengths.
- Masks: gratings at various periods and orientations, checkerboards, radial
  patterns.
- Wavelengths: 1 or 3 wavelengths.

### Analysis

1. Load `raw_capture.h5` — extract averaged PSF for each mask.
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

1. **Phase 3.1** — Effective LCD pupil scan
2. **Phase 3.2** — PSF repeatability and ROI alignment
3. **Phase 3.3** — dOTF diagnostic
4. **Phase 3.4** — PSF dictionary and export
5. **Phase 3.5** — Simple forward model validation
6. **Phase 3.6** — Three-wavelength multiframe linear reconstruction
7. **Phase 3.7** — Thesis figures and report freeze

Each phase depends on the outputs of the previous phases but not on
implementation details.  Phase 3.2 needs the pupil ROI from 3.1; Phase 3.3
needs the ROI convention and PSF extraction from 3.2; etc.
