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
**Status: implemented / in progress**

- The original Phase 3.0.5 exposure sweep was sensor-level coarse safety only.
  It is not sufficient for PSF, dOTF, or pupil-fit experiments because
  point-source PSF energy may occupy a very small fraction of the full sensor.
  A small global saturated fraction can still mean the PSF core is saturated.
  Phase 3.0.5b introduces PSF-safe exposure selection using max-pixel headroom
  as the primary constraint.
- Phase 3.0.5b evaluates PSF-safe max-pixel headroom over the raw burst frames,
  not only the averaged frame. The averaged frame remains useful for p99.9 and
  signal diagnostics, while burst max-pixel and burst saturated-pixel count
  decide PSF safety.
- Current Phase 3.0.5b has no bad-pixel mask, so any full-scale burst pixel is
  unsafe. Future bad-pixel mask support may exempt only explicitly marked known
  bad pixels; there is no implicit hot-pixel exemption.
- Any new Phase 3 capture must use `camera_params_psf_safe.json`.
- The Phase 3.1 data currently reviewed in PR #24 is first-pass coarse
  active-region localization only. It must not be described as final pupil
  geometry, final effective pupil, calibrated active pupil, or a PSF-safe scan.
  The review observed clipping/local saturation, so final fine scans must use
  lower exposure or the Phase 3.0.5b PSF-safe criterion before pupil
  characterization.
- Depends by default on Phase 3.0.5b
  `outputs/exposure_calibration/camera_params_psf_safe.json` for exposure,
  gain, frames-per-capture defaults, and raw frame full scale.
- Implement procedural bar/block scan capture: generate masks at runtime,
  display each physical LCD mask, capture camera response, and write raw HDF5
  first.
- Estimate the effective LCD physical-coordinate ROI from robust response
  support using smoothed bar profiles and/or the largest 2D block-map
  component.
- This phase locates the LCD region where mask changes measurably affect the
  camera image. It is not a scientific calibration validity claim.
- Output: `outputs/pupil_scan/effective_lcd_roi.json`

### M2 — PSF repeatability and ROI alignment
**Status: planned**

- Capture multiple repeats of the same mask at a single wavelength.
- Use `outputs/exposure_calibration/camera_params_psf_safe.json` by default.
- Quantify frame-to-frame and capture-to-capture variation.
- Establish energy-based ROI selection for downstream PSF extraction.
- Output: `outputs/psf_repeatability/repeatability_metrics.json`

### M3 — dOTF diagnostic
**Status: planned**

- Generate base mask + perturbation mask pairs.
- Use `outputs/exposure_calibration/camera_params_psf_safe.json` by default.
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
- Use `outputs/exposure_calibration/camera_params_psf_safe.json` by default.
- Capture PSF for each mask at a single wavelength.
- Export mask-to-PSF pairs in `LCD_forward`-compatible HDF5 format.
- Output: `outputs/psf_dictionary/psf_dict_lambda_<wl>nm.h5`

### M5 — Simple forward model
**Status: planned**

- For a held-out mask, use its measured PSF (from the dictionary) to render
  a target/object frame via convolution.
- Compare the rendered frame against the measured camera frame (qualitative +
  simple metrics).
- Also validate mask-to-PSF prediction consistency across the dictionary
  (held-out mask PSF vs. interpolated/predicted PSF).
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
