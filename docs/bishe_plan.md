# Bachelor thesis experimental plan

## Branch role

This branch (`phase3-bishe-experimental-loop`) is the bachelor-thesis
experimental branch of `optic_system`.

It starts from the post-Phase-2B hardware-capture baseline and consumes
mainline infrastructure updates.  It does not redefine the mainline
long-term roadmap.

## Relationship to mainline

Mainline provides:
- camera / LCD / TLS services
- raw HDF5 conventions
- capture task foundations
- run-status diagnostics
- read-only monitor
- GUI / architecture cleanup

This branch implements thesis-specific task workflows.  Generic
infrastructure fixes from mainline may be periodically merged into this
branch.  Thesis-specific task code stays in this branch unless explicitly
promoted.

## Thesis goal

Build and demonstrate a minimal mono-LCD programmable diffractive imaging
prototype:

- stable hardware capture using existing Phase 2 infrastructure
- PSF-safe exposure/gain calibration
- effective LCD active-region scan
- PSF repeatability verification
- dOTF diagnostic evidence for low-dimensional / sparse pupil or
  LCD-induced structure
- measured PSF dictionary
- simple forward-model validation
- three-wavelength multiframe linear reconstruction demo
- thesis figures and report-ready results

## Phase 3 thesis roadmap

### Phase 3.0 — Branch bootstrap and old-project knowledge migration

**Status: complete**

Purpose:
- document old-project findings as design priors
- preserve old project as reference only
- forbid old-code resurrection
- define thesis branch workflow

Old project facts as priors:

1. PSF differences exceed repeat noise.
2. Different masks produce stable PSFs.
3. dOTF reveals visible pupil pixel / stripe structure.
4. dOTF full pupil stitching was not reliable.

These are design priors only.  Old raw data is lost; all thesis evidence
must be reacquired under the current framework.

Old code under `old/` may be inspected for experimental ordering and
mask/task ideas, but must not be imported, called, or revived.

Outputs:
- `docs/old_experiment_audit.md`
- `docs/phase3_workflow.md`
- `plans/README.md`
- `outputs/README.md`

Exit criteria:
1. Old experimental tasks identified for re-implementation.
2. Old code paths marked as forbidden.
3. All new data acquired through Phase 2 raw capture HDF5 pipeline.

---

### Phase 3.0.5b — PSF-safe exposure/gain calibration

**Status: implemented**

Purpose:
- determine PSF-safe camera exposure/gain across thesis wavelengths
- reject settings where any burst pixel reaches full scale
- produce per-wavelength safe camera parameters

Entry points:
- `scripts/calibrate_psf_safe_exposure.py`
- `plans/bishe_psf_safe_exposure.yaml`

Outputs:
- `data/raw/bishe_psf_safe_exposure.h5`
- `outputs/exposure_calibration/camera_params_psf_safe.json`

Downstream default:
All Phase 3 captures use `outputs/exposure_calibration/camera_params_psf_safe.json`.

---

### Phase 3.1 — Effective LCD pupil / active region scan

**Status: in progress**

Purpose:
- locate the LCD physical-coordinate region that measurably affects the
  camera image
- produce `effective_lcd_roi.json` as the coordinate baseline for all
  subsequent experiments
- use PSF-safe camera parameters from Phase 3.0.5b

Scripts:
- `tasks/pupil_scan_masks.py` — procedural mask generation
- `scripts/capture_pupil_scan.py` — acquisition
- `scripts/analyze_pupil_scan.py` — response analysis and ROI extraction
- `plans/bishe_pupil_scan.yaml`

Flow:
```
generate bar / block / aperture scan masks
  → capture raw_capture.h5
  → analyze response strength / PSF variation
  → output effective_lcd_roi.json
```

Outputs:
```
data/raw/bishe_pupil_scan.h5
outputs/pupil_scan/
  response_map.npy
  response_map.png
  effective_lcd_roi.json
  pupil_scan_report.md
```

Acceptance criteria:
1. Effective modulation region identified.
2. ROI returned.
3. Mask changes within ROI produce measurable PSF or brightness changes.
4. Signal outside ROI is significantly weaker or explainable.
5. All downstream experiments reference this ROI by default.

Fallback:
If the response map is unclear, adjust optics, exposure, point source,
LCD position, or ROI size before entering complex dOTF work.  This phase
is the coordinate baseline for all later experiments — it cannot be skipped.

---

### Phase 3.2 — PSF ROI, alignment and repeatability

**Status: planned**

Purpose:
- reacquire the two key old-project findings with new data:
  1. Same-mask repeat captures yield stable PSFs.
  2. Between-mask PSF differences exceed repeat noise.

Scripts:
- `scripts/extract_psf_roi.py`
- `scripts/align_psf_stack.py`
- `scripts/evaluate_psf_repeatability.py`
- `plans/bishe_psf_repeatability.yaml`

Flow:
```
representative mask set
  → K repeats per mask
  → raw_capture.h5
  → automated ROI extraction
  → sub-pixel alignment
  → mean / variance / difference matrix
```

Suggested masks:
```
all_open
all_closed
coarse_vertical_stripes
coarse_horizontal_stripes
stripe_phase_shift_0
stripe_phase_shift_1
coarse_checkerboard
low_freq_random_block
```

Outputs:
```
outputs/psf_repeatability/
  psfs_aligned.npy
  psfs_mean.npy
  psfs_std.npy
  mask_difference_matrix.npy
  mask_difference_matrix.png
  repeatability_metrics.json
  psf_repeatability_report.md
```

Core metrics:
```
within_mask_variance
between_mask_distance
SNR-like ratio = between_mask_distance / within_mask_std
ROI extraction success rate
alignment residual
```

Exit criteria:
1. Within-mask PSF variance smaller than between-mask difference.
2. Automated ROI extraction and alignment are stable.
3. Representative mask PSF comparison figure ready for thesis.

This is the first critical milestone.  If it fails, dOTF, forward
model, and reconstruction should not proceed.

---

### Phase 3.3 — dOTF diagnostic

**Status: planned**

Purpose:
- use dOTF to provide evidence for low-dimensional / sparse pupil or
  LCD-induced structure
- full pupil stitching is NOT the minimum success criterion

Scripts:
- `scripts/make_dotf_masks.py`
- `scripts/compute_dotf.py`
- `scripts/plot_dotf_report.py`
- `plans/bishe_dotf_edge_perturb.yaml`

Flow:
```
base mask + edge perturbation masks
  → capture ref / perturbed PSF pairs
  → ROI / align / average
  → FFT → OTF
  → dOTF = OTF_perturbed - OTF_ref
  → amplitude / phase / sparsity / structure diagnostics
```

Outputs:
```
outputs/dotf/
  psf_ref.npy
  psf_perturbed.npy
  otf_ref.npy
  otf_perturbed.npy
  dotf_complex.npy
  dotf_amp.png
  dotf_phase.png
  dotf_structure_overlay.png
  dotf_sparsity_metrics.json
  dotf_report.md
```

Two-tier acceptance:

**Tier A:**
- Two pupil-plane structures visible in dOTF.
- Non-overlapping regions explainable.
- Multi-perturbation stitching gives a rough pupil estimate.

**Tier B:**
- Full pupil stitching unsatisfactory, BUT:
- LCD sub-pixel / stripe / array structure stably visible in dOTF
  amplitude and phase across perturbations.
- Can support the conclusion that LCD encoding effects are low-dimensional,
  sparse, and calibratable.

The thesis minimum requirement is Tier B.  Do not tie the entire thesis
to Tier A full-pupil stitching.

---

### Phase 3.4 — Measured PSF dictionary and LCD_forward export

**Status: planned**

Purpose:
- build a measured mask-to-PSF dictionary as the foundation for forward
  modelling
- export selected data into `LCD_forward`-compatible HDF5

Scripts:
- `scripts/make_psf_dictionary_masks.py`
- `scripts/build_psf_dictionary.py`
- `scripts/convert_to_lcd_forward_h5.py`
- `plans/bishe_psf_dict_single_lambda.yaml`
- `plans/bishe_psf_dict_three_lambda.yaml`

Flow:
```
representative mask families
  → single / three-wavelength PSF acquisition
  → ROI / align / average
  → PSF dictionary
  → train / val / test split
  → LCD_forward-compatible HDF5
```

`optic_system` side outputs:
```
data/raw/bishe_psf_dict_*.h5
outputs/psf_dictionary/
  masks_physical.npy
  masks_downsampled.npy
  psfs_mean.npy
  psfs_std.npy
  wavelengths.npy
  dictionary_metadata.json
```

`LCD_forward` side format:
```
data/bishe_forward/train.h5
data/bishe_forward/val.h5
data/bishe_forward/test.h5

masks: [N, T, 1, Hm, Wm]
psfs:  [N, T, L, Hp, Wp]
```

Exit criteria:
1. Single-wavelength PSF dictionary usable.
2. Three-wavelength PSF dictionary usable (if TLS stable).
3. Data readable by LCD_forward's `ForwardH5Dataset`.
4. A simple mask-to-PSF forward baseline can be trained or fitted.

After this phase, the thesis has the "hardware acquisition → data format
→ forward modelling" main chain.

---

### Phase 3.5 — Simple forward model validation

**Status: planned**

Purpose:
- validate that measured PSFs explain typical mask-dependent PSF differences
- do not require a complex neural model

Three-tier model complexity:
```
baseline 0: measured PSF lookup / dictionary
baseline 1: low-rank PSF basis
baseline 2: mask statistics / low-dimensional descriptor → PSF basis coefficients
```

Can be implemented in `LCD_forward` or as standalone analysis scripts.

Suggested additions:
```
LCD_forward/configs/forward_bishe_single_lambda.yaml
LCD_forward/configs/forward_bishe_three_lambda.yaml
LCD_forward/scripts/eval_measured_psf_dictionary.py
LCD_forward/scripts/fit_psf_basis_forward.py
LCD_forward/scripts/plot_forward_bishe_report.py
```

Validation metrics:
```
pixel L1 / MSE
frequency-domain error
centroid / main lobe shift
dominant diffraction order position
PSF energy normalization
held-out mask family error
```

Exit criteria:
1. Model explains main PSF differences for typical masks.
2. Forward error smaller than between-mask differences.
3. At least one held-out mask family tested.
4. "Predicted PSF vs measured PSF" thesis figure ready.

Risk control:
If complex field basis models are unsatisfactory, the thesis can
still stand on PSF dictionary / PSF basis baselines.

---

### Phase 3.6 — Three-wavelength multiframe linear reconstruction

**Status: planned**

Purpose:
- demonstrate that multi-mask encoding improves multispectral recovery
- complete the thesis end-to-end demo

Scripts:
- `plans/bishe_multiframe_target.yaml`
- `scripts/build_linear_forward_matrix.py`
- `scripts/solve_linear_multispectral_recon.py`
- `scripts/plot_multiframe_recon_report.py`

Acquisition design:
```
wavelengths: 3 representative wavelengths
masks: 3-5 stable representative masks
targets: colour blocks / filter combinations / simple transmissive targets
frames: multi-mask multi-frame per target
```

Reconstruction model (linear inverse):
```
x_hat = argmin ||A x - y||^2 + λR(x)
```

Priorities:
```
Tikhonov least squares
non-negative clipping
optional TV or Laplacian smoothing
```

Outputs:
```
outputs/linear_recon/
  A_matrix_info.json
  condition_number_report.json
  recon_single_frame.npy
  recon_multiframe.npy
  recon_error.npy
  recon_comparison.png
  linear_recon_report.md
```

Exit criteria:
1. At least three-wavelength data available.
2. Single-frame vs multi-frame comparison produced.
3. Multi-frame recovery quality better than single-frame, or condition
   number / channel separation improved.
4. Final thesis demonstration figure ready.

---

### Phase 3.7 — Thesis figures and report freeze

**Status: planned**

Purpose:
- freeze experiments
- prepare thesis figures
- prepare thesis text and defense slides

Outputs:
```
outputs/bishe_figures/
  system_pipeline.png
  psf_mask_examples.png
  dotf_amp_phase.png
  forward_prediction_vs_measurement.png
  multiframe_recon_comparison.png

docs/bishe_thesis_outline.md
docs/bishe_results_summary.md
docs/bishe_limitations.md
```

Recommended thesis narrative:
1. Mono LCD programmable diffractive imaging system setup.
2. Raw capture HDF5 and automated acquisition workflow.
3. PSF stability and LCD encoding response analysis.
4. dOTF diagnostic and low-dimensional pupil structure evidence.
5. Measured PSF dictionary and simple forward model.
6. Three-wavelength multi-frame linear reconstruction demo.
7. Limitations and future research directions.

---

## Branch acceptance criteria

When this branch closes, the following must hold:

1. PSF difference exceeds repeat noise — re-verified under current framework.
2. PSF stability across masks — re-verified under current framework.
3. Effective LCD pupil / active region scan complete.
4. dOTF diagnostic complete — at minimum proving low-dimensional sparse
   pupil-plane structure (Tier B).
5. Measured PSF dictionary complete.
6. `LCD_forward`-compatible HDF5 export working.
7. Simple forward baseline complete.
8. Three-wavelength multi-frame linear reconstruction demo complete.
9. All thesis figures traceable to `raw_capture.h5`.
10. No old data used as thesis evidence; no old-project acquisition paths
    revived.

## Non-goals

This thesis branch does not implement:

- full GenerMask optimization
- full neural reconstruction
- full closed-loop mask learning
- full physical first-principles LCD model
- complete complex-pupil recovery as a required success criterion
- general experiment scheduler
- mainline Phase 4+ work

## Provenance rule

Every processed analysis output must record:

- source `raw_capture.h5` path
- capture plan ID
- mask IDs and wavelengths used
- preprocessing parameters (ROI size, cropping, background subtraction)
- analysis script name and version / commit hash

This rule is non-negotiable.  Old data was lost; new results must be fully
auditable.
