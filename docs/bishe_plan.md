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
- LCD-domain effective pupil geometry calibration
- camera-frame PSF ROI calibration
- PSF repeatability and mask-induced diversity verification
- dOTF diagnostic evidence for low-dimensional / sparse pupil or
  LCD-induced structure
- measured PSF dictionary
- simple forward-model validation
- three-wavelength multiframe linear reconstruction demo
- thesis figures and report-ready results

## Phase 3 thesis roadmap

### Phase 3.0 - Branch bootstrap and old-project knowledge migration

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

### Phase 3.0.5b - PSF-safe exposure/gain calibration

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

### Phase 3.1 - Effective pupil geometry calibration

**Status: implementation re-aligned with old/calibrating.py and old/ellipse.py
physical model; canonical hardware rerun pending**

Purpose:
- calibrate an effective pupil window in LCD physical coordinates using
  energy-based bar profiles and circular-radius scans
- produce `effective_pupil_window.json` as the window baseline for all
  subsequent mask-encoded experiments
- use PSF-safe camera parameters from Phase 3.0.5b through
  `camera_profile: fast_pupil_scan` when available

**Hardware constraint:** The TLS (monochromator) must filter the light
source to the planned wavelength.  Without wavelength filtering the
broadband white light will overexpose the camera with PSF-safe parameters
that assume filtered monochromatic light.  Phase 3.1 therefore requires
TLS participation by default (`--tls-serial` or `TLS_C1_SERIAL`).
For explicit manual external wavelength control, use the dangerous
`--allow-wavelength-labels-without-tls` override.

Scripts:
- `tasks/pupil_geometry_masks.py` - physical mono reference/bar/aperture masks
- `tasks/pupil_geometry_model.py` - circle profile and ellipse-overlap model
- `tasks/pupil_geometry_h5.py` - raw geometry calibration HDF5
- `scripts/capture_pupil_geometry.py` - acquisition
- `scripts/analyze_pupil_geometry.py` - geometry fit and effective window export
- `plans/bishe_pupil_geometry.yaml`

Flow:
```
bright reference + dark reference
  -> X/Y dark-bar energy profiles
  -> circle center and initial radius fit
  -> circular aperture radius scan
  -> ellipse-overlap fit
  -> effective circular pupil window
  -> capture raw_capture.h5
  -> output effective_pupil_window.json
```

Outputs:
```
data/raw/bishe_pupil_geometry.h5
outputs/pupil_geometry/
  x_profile.csv
  y_profile.csv
  radius_scan.csv
  bar_profile_fit.png
  radius_overlap_fit.png
  effective_pupil_window.npy
  effective_pupil_window.png
  effective_pupil_window.json
  pupil_geometry_report.md
```

Acceptance criteria:
1. Bar profiles produce a usable center and initial radius estimate.
2. Radius scan fits ellipse semi-axes `a`, `b` and scale `k`.
3. Effective circular pupil window radius is slightly below `b`.
4. Raw HDF5 preserves bar/radius scan energies and mask metadata.
5. All downstream experiments reference this window by default.

Fallback:
If the profile or radius fit is unclear, adjust optics, exposure, point
source, LCD position, or scan ranges before entering complex dOTF work. This
phase is the coordinate/window baseline for all later experiments - it cannot
be skipped.

**Phase 3.1 does not include:**
- camera-frame PSF ROI detection, cropping, or alignment
- PSF repeatability metrics
- dOTF computation
- PSF dictionary construction

These are the responsibilities of Phase 3.2a, 3.2b, 3.3, and 3.4 respectively.
The output `effective_pupil_window.json` is in **LCD physical coordinates**.
The camera-frame PSF ROI (`psf_roi.json`) is a separate calibration in
Phase 3.2a and uses **camera sensor coordinates**.

---

### Phase 3.2 - Camera-frame PSF ROI + PSF repeatability/diversity

**Status: data-first scripts implemented; hardware acquisition pending**

This phase sets the camera-sensor coordinate baseline (PSF ROI) and then
reacquires the two key old-project findings: same-mask PSF stability and
between-mask PSF diversity.  It is split into two sub-phases because the
PSF ROI calibration is a strict prerequisite for all downstream analysis.

#### Phase 3.2a — Camera-frame PSF ROI calibration

Purpose:
- determine a fixed crop window in **camera sensor coordinates** for the
  point-source PSF
- produce `psf_roi.json` as the single source of truth for all subsequent
  PSF crops (3.2b, 3.3, 3.4+)

Phase 3.1 gives an **LCD-domain** effective pupil window
(`effective_pupil_window.json`).  Phase 3.2a gives a **camera-frame**
PSF crop window (`psf_roi.json`).  These are different coordinate systems
and must not be conflated.

Input:
- `outputs/pupil_geometry/effective_pupil_window.json`
- `outputs/exposure_calibration/camera_params_psf_safe.json`
- point source setup with the effective pupil window displayed on LCD

Minimum algorithm:
1. Display the effective circular pupil window on LCD (inside = all-open,
   outside = all-closed).
2. Acquire point-source PSF with K-frame burst averaging.
3. Apply simple baseline correction.
4. Locate PSF center by peak pixel followed by local center-of-mass.
5. Choose the configured fixed crop size, currently 256 x 256.
6. Write `outputs/psf_roi/psf_roi.json`.

Scripts:
- `scripts/capture_psf_roi.py` — acquisition
- `scripts/analyze_psf_roi.py` — analysis and ROI export
- `plans/bishe_psf_roi.yaml`

Hardware capture rejects `lcd.settle_ms < 100`; the default is 200 ms
because the LCD frame period is about 20 ms.

Outputs:
```
data/raw/bishe_psf_roi.h5
outputs/psf_roi/
  psf_roi.json
  psf_roi_preview.png
  psf_roi_report.md
```

psf_roi.json schema:
```json
{
  "schema_version": 1,
  "phase": "3.2a",
  "task": "camera_frame_psf_roi_calibration",
  "source_raw_h5": "data/raw/bishe_psf_roi.h5",
  "pupil_window_source": "outputs/pupil_geometry/effective_pupil_window.json",
  "camera_params_source": "outputs/exposure_calibration/camera_params_psf_safe.json",
  "camera_profile_used": "per_gain_safe_params:10.0",
  "wavelength_nm": 550.0,
  "frame_shape": [2048, 2448],
  "roi": {
    "x_min": 1000,
    "x_max": 1256,
    "y_min": 800,
    "y_max": 1056,
    "width": 256,
    "height": 256
  },
  "center": {
    "x": 1128.0,
    "y": 928.0,
    "method": "peak_then_center_of_mass"
  },
  "quality": {
    "peak_pixel": 0.0,
    "mean_pixel": 0.0,
    "background_level": 0.0,
    "roi_energy_fraction": 0.0,
    "full_scale_in_avg_valid_domain": false
  },
  "validity": {
    "psf_roi_estimated": true,
    "scientific_calibration_valid": false,
    "training_ready": false
  }
}
```

**psf_roi.json is camera-frame coordinates.**
**effective_pupil_window.json is LCD physical coordinates.**
Both are recorded in every downstream raw HDF5 as provenance.

#### Phase 3.2b — PSF repeatability and mask-induced diversity

Purpose:
- reacquire the two key old-project findings with new data:
  1. Same-mask repeat captures yield stable PSFs (intra-mask repeatability).
  2. Between-mask PSF differences exceed repeat noise (inter-mask diversity).

Input:
- `outputs/psf_roi/psf_roi.json` (camera-frame crop)
- `outputs/pupil_geometry/effective_pupil_window.json` (LCD mask window)
- `outputs/exposure_calibration/camera_params_psf_safe.json`
- representative mask set

Scripts:
- `scripts/capture_psf_repeatability.py` — acquisition
- `scripts/analyze_psf_repeatability.py` — analysis
- `plans/bishe_psf_repeatability.yaml`

Flow:
```
effective pupil window on LCD
  -> representative masks (inside window)
  -> K repeats per mask, N-frame burst per capture
  -> raw capture HDF5
  -> psf_roi crop every frame
  -> intra-mask: mean / std / coefficient-of-variation / PSNR / SSIM
  -> inter-mask: pairwise MSE / PSNR / SSIM / Fourier difference
```

Default masks (all inside the effective pupil window):
```
all_open_window
vertical_stripes_lowfreq
horizontal_stripes_lowfreq
checkerboard_lowfreq
central_block
edge_block
random_lowfreq_1
random_lowfreq_2
```

Outputs:
```
data/raw/bishe_psf_repeatability.h5
outputs/psf_repeatability/
  psfs_aligned.npy
  psfs_mean.npy
  psfs_std.npy
  repeatability_metrics.json
  diversity_metrics.json
  psf_diversity_metrics.json
  pairwise_distance_matrix.npy
  ssim_matrix.npy
  psnr_matrix.npy
  repeatability_report.md
  report.md
```

Core metrics (intra-mask):
```
mean PSF
std PSF
coefficient of variation
normalized correlation among repeats
PSNR among repeats
SSIM among repeats
```

Core metrics (inter-mask diversity):
```
pairwise MSE / RMSE
pairwise PSNR
pairwise SSIM
normalized cross-correlation
Fourier magnitude difference
```

Core conclusion: report `inter_mask_distance / intra_mask_repeat_noise`.
If the ratio is clearly greater than 1, the report may state that
mask-induced PSF differences are larger than repeatability noise.

Exit criteria:
1. Within-mask PSF variance smaller than between-mask difference.
2. PSF ROI crop is used for all frames; ROI metadata recorded.
3. Representative mask PSF comparison figure ready for thesis.
4. The conclusion is limited to the Phase 3.2 data prerequisite and does not
   claim a successful forward model.
5. This is the first critical milestone.  If it fails, dOTF, forward
   model, and reconstruction should not proceed.

Phase 3.2b does not include:
- forward surrogate training
- mask learning
- PSF dictionary modeling
- complex field basis
- deep reconstruction

These belong to LCD_forward or later Phase 3.4+.

---

### Phase 3.3 - dOTF diagnostic visualization

**Status: scripts implemented; hardware acquisition pending**

Purpose:
- use dOTF to provide direct visible evidence for low-dimensional / sparse
  pupil or LCD-induced structure
- full pupil stitching is NOT the minimum success criterion
- migrate the old-project `old/perturbation.py` dOTF computation and
  visualization logic into active scripts
- keep the result as diagnostic visualization only; no pupil stitching or
  full complex pupil reconstruction

Input:
- `outputs/pupil_geometry/effective_pupil_window.json`
- `outputs/psf_roi/psf_roi.json`
- `outputs/exposure_calibration/camera_params_psf_safe.json`
- edge perturbation masks

Scripts:
- `scripts/capture_dotf.py` — acquisition
- `scripts/analyze_dotf.py` — dOTF computation and visualization
- `plans/bishe_dotf_diagnostic.yaml`

Flow:
```
effective pupil window on LCD
  -> display base (reference) mask
  -> capture reference PSF (N-frame burst average)
  -> display edge-perturbed mask
  -> capture perturbed PSF (N-frame burst average)
  -> psf_roi crop both reference & perturbed PSF
  -> align, normalize energy
  -> OTF_ref = FFT2(PSF_ref)
  -> OTF_pert = FFT2(PSF_pert)
  -> dOTF = OTF_pert - OTF_ref
  -> output abs / log_abs / phase / real / imag visualizations
```

Outputs:
```
data/raw/bishe_dotf_diagnostic.h5
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

Acceptance:

- LCD sub-pixel / stripe / array structure stably visible in dOTF amplitude
  and phase across perturbations.
- dOTF results directly show structured / sparse / low-dimensional
  pupil-plane features without requiring full stitching.
- The result remains a diagnostic visualization and is not promoted to a full
  stitched pupil estimate.

Note: the raw dOTF result contains two conjugate pupils combined in the
complex plane.  Even without de-convolution or stitching, structured pupil
pixel / stripe / low-dimensional features are directly visible in the
dOTF abs, log_abs, and phase outputs.

Phase 3.3 does not attempt:
- full complex-pupil stitching
- de-convolution of conjugate pupil planes
- complete pupil reconstruction as a success criterion

---

### Phase 3.4 - Measured PSF dictionary and LCD_forward export

**Status: planned**

Purpose:
- build a measured mask-to-PSF dictionary as the foundation for forward
  modelling
- export selected data into `LCD_forward`-compatible HDF5
- all PSF crops use `outputs/psf_roi/psf_roi.json`

Scripts:
- `scripts/make_psf_dictionary_masks.py`
- `scripts/build_psf_dictionary.py`
- `scripts/convert_to_lcd_forward_h5.py`
- `plans/bishe_psf_dict_single_lambda.yaml`
- `plans/bishe_psf_dict_three_lambda.yaml`

Flow:
```
representative mask families
  -> single / three-wavelength PSF acquisition
  -> ROI / align / average
  -> PSF dictionary
  -> train / val / test split
  -> LCD_forward-compatible HDF5
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

After this phase, the thesis has the "hardware acquisition -> data format
-> forward modelling" main chain.

---

### Phase 3.5 - Simple forward model validation

**Status: planned**

Purpose:
- validate that measured PSFs explain typical mask-dependent PSF differences
- do not require a complex neural model

Three-tier model complexity:
```
baseline 0: measured PSF lookup / dictionary
baseline 1: low-rank PSF basis
baseline 2: mask statistics / low-dimensional descriptor -> PSF basis coefficients
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

### Phase 3.6 - Three-wavelength multiframe linear reconstruction

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
x_hat = argmin ||A x - y||^2 + lambda * R(x)
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

### Phase 3.7 - Thesis figures and report freeze

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

1. PSF difference exceeds repeat noise - re-verified under current framework.
2. PSF stability across masks - re-verified under current framework.
3. LCD-domain effective pupil window calibration complete.
4. Camera-frame PSF ROI calibration complete.
5. dOTF diagnostic complete - at minimum proving low-dimensional sparse
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
