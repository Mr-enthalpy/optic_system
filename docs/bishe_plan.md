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
- target-capture export for downstream reconstruction
- thesis figures and report-ready results are produced outside `optic_system`

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

**Status: hardware rerun complete**

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

Current frozen result:
- `global_safe_camera = 0.0 dB / 487.3046875 us`
- per-wavelength recommended profiles:
  - `450 nm`: `0.0 dB / 779.6875 us`
  - `550 nm`: `0.0 dB / 487.3046875 us`
  - `650 nm`: `0.0 dB / 2241.6015625 us`
- `550 nm` is still the limiting wavelength in the derived shared baseline

Downstream default:
All Phase 3 captures use `outputs/exposure_calibration/camera_params_psf_safe.json`,
but the file now represents a per-wavelength safe camera catalog rather than a
single global setting. `global_safe_camera` is a derived shared baseline only.
Phases that declare `camera_profile_policy: wavelength_recommended` should use
the per-wavelength recommended profiles.

---

### Phase 3.1 - Effective pupil geometry calibration

**Status: hardware rerun complete; cleaned result frozen**

Purpose:
- calibrate an effective pupil window in LCD physical coordinates using
  energy-based bar profiles and circular-radius scans
- produce `effective_pupil_window.json` as the window baseline for all
  subsequent mask-encoded experiments
- use PSF-safe camera parameters from Phase 3.0.5b; the current frozen result
  was acquired after the schema v2 catalog update

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

Current frozen result:
- center in LCD physical coordinates:
  - `x ≈ 1065.2462`
  - `y ≈ 1871.5352`
- effective radius:
  - `radius ≈ 52.7972 px`
- ellipse fit:
  - `a ≈ 115.5145`
  - `b ≈ 58.6636`
  - `R^2 ≈ 0.99918`

Result note:
- The final accepted Phase 3.1 result required documented `r scan` cleaning.
- The active output `data/raw/bishe_pupil_geometry.h5` is the cleaned
  self-contained HDF5 used to generate the current
  `outputs/pupil_geometry/effective_pupil_window.json`.
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

**Status: hardware acquisition and analysis complete for the current baseline**

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
- keep `roi_256` as the frozen audited baseline while allowing larger
  centered ROI candidates for later dOTF diagnostics

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
5. Choose the configured frozen baseline crop size, currently 256 x 256.
6. Generate additional centered ROI candidates for later analysis-only use.
7. Write `outputs/psf_roi/psf_roi.json`.

Scripts:
- `scripts/capture_psf_roi.py` — acquisition
- `scripts/analyze_psf_roi.py` — analysis and ROI export
- `plans/bishe_psf_roi.yaml`

Hardware capture rejects `lcd.settle_ms < 100`; the default is 200 ms
because the LCD frame period is about 20 ms.

Outputs:
```

Current frozen result:
- center in camera sensor coordinates:
  - `x ≈ 1148.9956`
  - `y ≈ 934.1996`
- ROI:
  - `x = [1021, 1277)`
  - `y = [806, 1062)`
  - `256 x 256`

Display note:
- `psf_roi_preview.png` is contrast-stretched for visibility.
- It is valid for ROI placement checks, not for raw exposure judgment.
- `roi_256` remains the current audited baseline.
- Larger ROI candidates are diagnostic only and do not automatically change
  the current baseline.
- The current manual Phase 3.4 modelling choice is `roi_512`.
data/raw/bishe_psf_roi.h5
outputs/psf_roi/
  psf_roi.json
  psf_roi_preview.png
  psf_roi_report.md
```

psf_roi.json schema:
```json
{
  "schema_version": 2,
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
  "rois": {
    "roi_256": {},
    "roi_512": {},
    "roi_768": {},
    "roi_1024": {}
  },
  "current_baseline_roi_key": "roi_256",
  "default_roi_key": "roi_256",
  "final_selected_roi_key": null,
  "validity": {
    "psf_roi_candidates_estimated": true,
    "final_roi_selected": false,
    "scientific_calibration_valid": false,
    "training_ready": false
  }
}
```

**psf_roi.json is camera-frame coordinates.**
**effective_pupil_window.json is LCD physical coordinates.**
Both are recorded in every downstream raw HDF5 as provenance.

The 256 x 256 baseline has `roi_energy_fraction ≈ 0.44883`. Larger centered
ROI candidates are therefore useful for diagnosing PSF support truncation and
windowed-dOTF leakage. No automatic final ROI selection is performed here.

#### Phase 3.2b — PSF repeatability and mask-induced diversity

Purpose:
- reacquire the two key old-project findings with new data:
  1. Same-mask repeat captures yield stable PSFs (intra-mask repeatability).
  2. Between-mask PSF differences exceed repeat noise (inter-mask diversity).
  3. Same-mask PSFs may also differ systematically across wavelengths
     (cross-wavelength spectral diversity).

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
  -> one or more thesis wavelengths with wavelength-specific camera profiles
  -> K repeats per mask, N-frame burst per capture
  -> raw capture HDF5
  -> psf_roi crop every frame
  -> group by wavelength
  -> intra-mask: mean / std / coefficient-of-variation / PSNR / SSIM
  -> inter-mask: pairwise MSE / PSNR / SSIM / Fourier difference
  -> same-mask cross-wavelength: pairwise PSF difference after repeat averaging
  -> rerun cross-wavelength comparison after background subtraction and
     unit-energy normalization
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

Current frozen conclusion:
- raw averaged crops:
  - `mean_intra_mask_mse ≈ 0.0151267`
  - `mean_inter_mask_mse ≈ 4.39363`
  - `inter_mask_distance_over_intra_noise ≈ 290.455`
- `mask_induced_differences_larger_than_repeat_noise = true`
- background-subtracted + unit-energy normalized crops:
  - `mean_intra_mask_mse ≈ 2.53772e-11`
  - `mean_inter_mask_mse ≈ 7.55959e-09`
  - `inter_mask_distance_over_intra_noise ≈ 297.889`
  - `mean_cross_wavelength_same_mask_mse ≈ 3.30682e-08`
  - `cross_wavelength_same_mask_over_intra_noise ≈ 1303.07`
  - `wavelength_induced_differences_larger_than_repeat_noise = true`

Therefore the current data supports:
1. same-mask PSFs are repeatable
2. mask-induced PSF differences are much larger than repeatability noise
3. same-mask cross-wavelength PSF shape differences remain much larger than
   repeatability noise after background subtraction and unit-energy normalization
4. cross-wavelength PSF difference, when analyzed, belongs to this phase
   rather than Phase 3.3

This is the Phase 3.2 completion criterion. It is not a forward-model
success claim.

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
For multi-wavelength runs also report the same-mask
`cross_wavelength_distance / intra_mask_repeat_noise` ratio.
For cross-wavelength shape claims, prefer the background-subtracted +
unit-energy normalized companion analysis over the raw-crop metrics.

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

**Status: hardware acquisition and analysis complete for the current baseline**

Purpose:
- use dOTF to provide direct visible evidence for low-dimensional / sparse
  pupil or LCD-induced structure
- full pupil stitching is NOT the minimum success criterion
- migrate the old-project `old/perturbation.py` dOTF computation and
  visualization logic into active scripts
- keep the result as diagnostic visualization only; no pupil stitching or
  full complex pupil reconstruction
- do not treat this phase as the main PSF diversity or spectral-difference
  milestone; that belongs to Phase 3.2b

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
  -> for each configured wavelength:
       -> display base (reference) mask
       -> capture reference PSF (N-frame burst average)
       -> display edge-perturbed mask
       -> capture perturbed PSF (N-frame burst average)
  -> store full-frame raw averages once
  -> recompute crops for one or more ROI candidates from psf_roi.json
  -> group by wavelength and compare dOTF per wavelength as a secondary
     diagnostic
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
  dotf_report.md
  dotf_roi_comparison_manifest.json
  dotf_roi_comparison_report.md
  wl_450p0/
  wl_550p0/
  wl_650p0/
```

Current frozen result:

- `dotf_computed = true`
- `pupil_stitching_performed = false`
- perturbation outputs are present for:
  - `edge_block_left`
  - `edge_block_right`
  - `edge_block_top`
  - `edge_block_bottom`
- `dotf_peak_abs`:
  - left `= 0.0004062839982924802`
  - right `= 0.0004193781484524579`
  - top `= 0.0004046599696057576`
  - bottom `= 0.0005967033596909086`

Acceptance:

- LCD sub-pixel / stripe / array structure stably visible in dOTF amplitude
  and phase across perturbations.
- dOTF results directly show structured / sparse / low-dimensional
  pupil-plane features without requiring full stitching.
- The result remains a diagnostic visualization and is not promoted to a full
  stitched pupil estimate.
- Multiple ROI candidates may be compared without repeating hardware capture,
  as long as full-frame raw frames are available.
- No automatic ROI selection is performed; later modelling ROI choice remains
  manual.
- The current manual outcome of that comparison is `roi_512` for Phase 3.4.

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

**Status: hardware capture and analysis complete for the current baseline; historical failed runs preserved for audit**

Purpose:
- build a measured mask-to-PSF dictionary as the foundation for forward
  modelling
- export selected data into `LCD_forward`-compatible HDF5
- all PSF crops use `outputs/psf_roi/psf_roi.json`
- keep the work data-first; no forward training or mask optimization

Scripts:
- `tasks/psf_dictionary_masks.py`
- `tasks/psf_dictionary_phase3.py`
- `scripts/capture_psf_dictionary.py`
- `scripts/analyze_psf_dictionary.py`
- `plans/bishe_psf_dictionary.yaml`

Flow:
```
representative mask families
  -> multi-wavelength PSF acquisition
  -> ROI crop preservation
  -> repeat-averaged measured PSF dictionary per wavelength
  -> train / val / test split
  -> LCD_forward-compatible HDF5 export
```

Current audited `optic_system` outputs:
```
data/raw/bishe_psf_dictionary_20260520_010603.h5
outputs/psf_dictionary_20260520_010603/
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

`LCD_forward` side format:
```
data/bishe_forward/train.h5
data/bishe_forward/val.h5
data/bishe_forward/test.h5

masks: [N, T, 1, Hm, Wm]
psfs:  [N, T, L, Hp, Wp]
```

Current audited result:

- `data/raw/bishe_psf_dictionary_20260520_010603.h5` is the active audited raw
  source
- `outputs/psf_dictionary_20260520_010603/` is the active audited analysis and
  export directory
- `psf_dictionary_acquired = true`
- `psf_roi_key_used = roi_512`
- `wavelengths_nm = [450.0, 550.0, 650.0]`
- `n_masks = 170`
- `repeats_per_mask = 5`
- `export_lcd_forward.enabled = true`
- historical failed runs remain preserved for audit and are not part of the
  current baseline

Manual ROI selection rule:

- Phase 3.4 uses the manually selected ROI `roi_512` after reviewing the
  Phase 3.3 multi-ROI dOTF comparison.
- `roi_256` remains the frozen baseline, but it is not the current modelling
  ROI.
- Phase 3.4 dictionary capture stores the selected ROI crop only. Full-frame
  preservation for ROI diagnostics and dOTF support inspection belongs to
  Phase 3.2a / 3.3.
- The current audited export is still timestamped. Until an explicit canonical
  promotion is performed, downstream consumers should point to
  `outputs/psf_dictionary_20260520_010603/export_lcd_forward/`.

Exit criteria:
1. Measured PSF dictionary acquired for every planned wavelength with complete provenance.
2. Data readable by LCD_forward-compatible HDF5 readers.
3. Raw per-repeat ROI crops, mask metadata, wavelength metadata, and export
   provenance preserved.
4. No training or optimization code introduced in `optic_system`.

After this phase, the thesis has the "hardware acquisition -> data format
-> forward modelling" main chain.

---

### Phase 3.5 - skipped in optic_system

**Status: intentionally skipped in `optic_system`**

Implemented in `LCD_forward`:
- measured-PSF forward validation
- forward error reporting
- predicted-vs-measured figures

`optic_system` does not implement forward validation, PSF basis fitting,
or held-out forward-model evaluation from Phase 3.5 onward.

---

### Phase 3.6 - partial in optic_system

**Status: target capture/export implemented in `optic_system`; reconstruction
belongs to `LCD_forward`**

`optic_system` responsibilities:
- real target multiframe / multi-wavelength capture
- full-frame raw HDF5 preservation
- PSF-ROI crop preservation
- lowres mask preservation
- LCD_forward-compatible target export

Active scripts and plan:
- `plans/bishe_target_capture.yaml`
- `scripts/capture_target_multiframe.py`
- `scripts/export_target_lcd_forward.py`

`LCD_forward` responsibilities:
- forward operator assembly
- linear reconstruction
- result visualization
- reconstruction figures and metrics

`optic_system` Phase 3.6 outputs:
```
data/raw/bishe_target_capture.h5
outputs/target_capture/
  export_lcd_forward/target_frames.h5
  README.md
```

Exit criteria on the `optic_system` side:
1. At least one target capture run can be stored with complete provenance.
2. Full-frame data, crops, lowres masks, wavelength labels, and mask IDs are preserved.
3. LCD_forward can read the exported HDF5 without requiring `optic_system`
   reconstruction code.

---

### Phase 3.7 - skipped in optic_system

**Status: intentionally skipped in `optic_system`**

Thesis appendix calibration figures (U1: LCD effective pupil, U2: PSF ROI
comparison, U2b: ROI energy decomposition) are exported by `optic_system`
from Phase 3 handoff artifacts. See `scripts/export_thesis_calibration_figures.py`
and `outputs/thesis_figures/`. These are calibration-support figures for the
thesis appendix, not main scientific results.

Thesis figure aggregation for Chapters 2-3, main-results figures, report
freeze, defense-slide preparation, and final narrative assembly belong to
`LCD_forward` or a separate thesis-writing workspace.

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
7. Phase 3.5 forward validation handled downstream in `LCD_forward`.
8. Phase 3.6 target-capture export handled in `optic_system`, with
   reconstruction handled downstream in `LCD_forward`.
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
