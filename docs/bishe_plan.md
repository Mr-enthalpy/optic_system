# Bachelor thesis experimental plan

## Thesis goal

Build a minimal mono-LCD programmable diffractive imaging prototype:

1. **Stable hardware capture** — reproducible mask-to-PSF acquisition using
   the Phase 2 capture stack (camera + mono LCD + optional TLS wavelength
   control).
2. **PSF repeatability verification** — reacquire and quantify the old finding
   that PSF differences across masks exceed frame-to-frame repeat noise.
3. **dOTF diagnostic analysis** — use differential optical transfer function
   to reveal low-dimensional / sparse pupil or LCD-induced structure.
4. **Measured PSF dictionary** — build a mask-to-PSF lookup table and export
   to `LCD_forward`-compatible HDF5.
5. **Simple forward model** — validate that measured PSFs and mask
   convolution yield plausible blurred estimates.
6. **Three-wavelength multiframe linear reconstruction** — demonstrate
   multispectral recovery using measured PSFs and simple linear inverse
   reconstruction.

## Relation to long-term research

The thesis is a **reduced experimental loop** of the long-term research
program (LCD_forward / GenerMask / forward-surrogate route).

This thesis does **not** require:

- neural-network training
- differentiable mask optimization
- forward surrogate learning
- full calibration against a known ground truth

It focuses on establishing the hardware capture pipeline, verifying basic
optical determinism, and demonstrating a simple reconstruction proof-of-concept.

## Phase 3 milestones

### M0 — Branch bootstrap and old-project migration
**Status: current**

- Create `phase3-bishe-experimental-loop` branch from post-Phase 2B master.
- Audit old project knowledge (`docs/old_experiment_audit.md`).
- Define thesis experimental plan (this document).
- Define technical workflows (`docs/phase3_workflow.md`).
- Define planned capture plans (`plans/README.md`).
- Define output directory structure (`outputs/README.md`).
- Establish provenance rule: every processed result must trace back to
  `raw_capture.h5`.

### M1 — Effective LCD pupil scan
**Status: planned**

- Implement bar-scan capture plan: sweep a narrow vertical/horizontal bar
  across the LCD, capture camera response.
- Fit effective pupil center and radius from energy-difference profiles.
- Optionally fit ellipse parameters to account for off-axis effects.
- Output: `outputs/pupil_scan/effective_lcd_roi.json`

### M2 — PSF repeatability and ROI alignment
**Status: planned**

- Capture multiple repeats of the same mask at a single wavelength.
- Quantify frame-to-frame and capture-to-capture variation.
- Establish energy-based ROI selection for downstream PSF extraction.
- Output: `outputs/psf_repeatability/repeatability_metrics.json`

### M3 — dOTF diagnostic
**Status: planned**

- Generate base mask + perturbation mask pairs.
- Capture PSF pairs.
- Compute dOTF (complex OTF difference with least-squares flux scaling).
- Visualize dOTF magnitude and phase.
- Interpret observed structure as low-dimensional / sparse pupil or LCD-induced
  features.

Explicit success criterion: Observe clear, reproducible structure in dOTF.
**Full pupil stitching is not required** for this milestone to pass.

### M4 — PSF dictionary and LCD_forward export
**Status: planned**

- Select a set of representative masks (e.g., gratings at various orientations
  and periods, checkerboards, radial patterns).
- Capture PSF for each mask at a single wavelength.
- Export mask-to-PSF pairs in `LCD_forward`-compatible HDF5 format.
- Output: `outputs/psf_dictionary/psf_dict_lambda_<wl>nm.h5`

### M5 — Simple forward model
**Status: planned**

- For a held-out mask, convolve mask with measured PSF to produce predicted
  camera frame.
- Compare predicted frame against measured frame (qualitative + simple
  metrics).
- Output: `outputs/linear_recon/forward_model_validation.json`

### M6 — Three-wavelength multiframe linear reconstruction
**Status: planned**

- Select 3 wavelengths.
- Capture PSF dictionary at each wavelength (same mask set).
- Capture one or more "unknown" target scenes.
- Apply simple linear inverse reconstruction using the measured PSF set.
- Compare reconstructed scene against known mask.
- Output: `outputs/linear_recon/multiframe_recon_results/`

### M7 — Thesis figures and report freeze
**Status: planned**

- Produce publication-quality figures from all preceding milestones.
- Write thesis report.
- Freeze analysis scripts and commit hashes for reproducibility.
- Output: `outputs/bishe_figures/`

## Non-goals

- No neural-network training (forward surrogate, reconstruction network, etc.).
- No full physical first-principles model of the optical system.
- No claim of complete complex pupil recovery unless data supports it.
- No old data reuse — all evidence must come from new Phase 2 captures.
- No GenerMask optimization — mask design is manual or script-driven for this
  thesis.
- No automated calibration scheduler.
- No full wavelength sweep above 3 wavelengths unless explicitly scoped in.

## Provenance rule

Every processed analysis output must record:

- source `raw_capture.h5` path
- capture plan ID
- mask IDs and wavelengths used
- preprocessing parameters (ROI size, cropping, background subtraction)
- analysis script name and version / commit hash

This rule is non-negotiable.  Old data was lost; new results must be fully
auditable.
