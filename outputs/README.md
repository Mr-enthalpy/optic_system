# Outputs directory

## Role

`outputs/` stores all processed analysis results from the Phase 3 thesis
workflow. Raw capture HDF5 files may also be placed here, at the user's
discretion, but the canonical location for raw captures is the path specified
by the capture plan.

**Every output must trace back to a source `raw_capture.h5` file.** See the
provenance rule below.

## Output subdirectories

### `outputs/exposure_calibration/`

- **Phase:** 3.0.5b - PSF-safe exposure refinement
- **Depends on:** `data/raw/bishe_psf_safe_exposure.h5` produced by
  `scripts/calibrate_psf_safe_exposure.py`.
- **Produces:**
  - `camera_params_psf_safe.json` - exposure, gain, frame scale, per-wavelength
    valid-domain peak-pixel diagnostics, invalid-domain artifact diagnostics,
    and PSF-safe validity flags.
- **Downstream default:** All Phase 3 captures and analyses must use
  `camera_params_psf_safe.json`.
- **Validity boundary:** PSF-safe exposure only means every raw burst pixel
  inside the recorded valid camera pixel domain was strictly below
  `frame_dtype_full_scale` for the planned wavelengths, and that usable-signal
  checks were computed over that same domain. It does not imply scientific
  calibration validity, optical alignment validity, or training-ready data.
- **Required provenance:** `camera_params_psf_safe.json` must record
  `psf_safety_policy.evaluated_domain == "valid_camera_pixel_domain"` and a
  `valid_pixel_domain` block. The raw sweep HDF5 records the same policy under
  `/valid_pixel_domain`.

### `outputs/pupil_geometry/`

- **Phase:** 3.1 - Effective pupil geometry calibration
- **Depends on:** `data/raw/bishe_pupil_geometry.h5` produced by
  `scripts/capture_pupil_geometry.py`.
- **Produces:**
  - `effective_pupil_window.json` - effective circular pupil window, circle
    estimate, ellipse-overlap fit, and provenance
  - `effective_pupil_window.npy` - physical mono circular window mask
  - `effective_pupil_window.png` - quick-look window visualization
  - `x_profile.csv` - dark-bar energy profile along physical x
  - `y_profile.csv` - dark-bar energy profile along physical y
  - `radius_scan.csv` - circular aperture radius energy profile and fit
  - `bar_profile_fit.png` - quick-look bar profile plot
  - `radius_overlap_fit.png` - quick-look overlap model fit
  - `pupil_geometry_report.md` - human-readable diagnostics and warnings
- **Script:** `scripts/analyze_pupil_geometry.py`
- **Status:** intermediate; estimates the effective pupil window, not final
  scientific calibration validity or training-ready data.

### `outputs/psf_roi/`

- **Phase:** 3.2a - Camera-frame PSF ROI calibration
- **Depends on:**
  - `data/raw/bishe_psf_roi.h5` produced by `scripts/capture_psf_roi.py`
  - `outputs/pupil_geometry/effective_pupil_window.json` (Phase 3.1, LCD domain)
  - `outputs/exposure_calibration/camera_params_psf_safe.json` (Phase 3.0.5b)
- **Produces:**
  - `psf_roi.json` - camera-frame crop window, center, compatibility baseline,
    and multi-ROI candidate metadata
  - `psf_roi_preview.png` - legacy baseline overlay for `roi_256`
  - `psf_roi_preview_roi_256.png` / `roi_512` / `roi_768` / `roi_1024` -
    candidate overlays on the averaged frame
  - `psf_roi_candidates_report.md` - human-readable candidate summary
  - `psf_roi_report.md` - compatibility alias of the candidate summary
- **Script:** `scripts/analyze_psf_roi.py`
- **Status:** hardware capture and analysis complete for current baseline
- **Coordinate system:** This directory contains the camera sensor crop used by
  PSF repeatability, dOTF, and PSF dictionary captures. It is distinct from
  `outputs/pupil_geometry/`, which is in LCD physical coordinates.
- **Current frozen ROI:** center `≈ (1148.996, 934.200)`, ROI
  `x=[1021,1277), y=[806,1062), 256 x 256`.
- **Frozen baseline rule:** `roi_256` remains the current audited baseline.
- **Multi-ROI rule:** larger centered ROI candidates may be added for
  diagnostic dOTF support/leakage inspection. They do not automatically select
  the final Phase 3.4 modelling ROI.
- **Display caveat:** `psf_roi_preview.png` is contrast-stretched and is for
  ROI placement checks, not raw exposure judgment.

### `outputs/psf_repeatability/`

- **Phase:** 3.2b - PSF repeatability and mask-induced diversity
- **Depends on:**
  - `data/raw/bishe_psf_repeatability.h5`
  - `outputs/psf_roi/psf_roi.json` (Phase 3.2a, camera domain)
  - `outputs/pupil_geometry/effective_pupil_window.json` (Phase 3.1, LCD domain)
- **Produces:**
  - `psfs_aligned.npy` - aligned PSF stack (all masks all repeats)
  - `psfs_mean.npy` - per-mask mean PSF
  - `psfs_std.npy` - per-mask PSF standard deviation
  - `repeatability_metrics.json` - intra-mask repeatability (PSNR, SSIM,
    coefficient of variation) and inter-mask pairwise distances
  - `repeatability_metrics_normalized.json` - background-subtracted +
    unit-energy normalized companion analysis
  - `pairwise_distance_matrix.npy` - between-mask pairwise distance matrix
  - `ssim_matrix.npy` - between-mask pairwise SSIM matrix
  - `psnr_matrix.npy` - between-mask pairwise PSNR matrix
  - `diversity_metrics.json` - inter-mask diversity summary and
    `inter_mask_distance / intra_mask_repeat_noise`
  - `diversity_metrics_normalized.json` - stricter diversity summary with
    reduced global energy-scale influence
  - `psf_diversity_metrics.json` - alias for the diversity summary
  - `spectral_diversity_metrics_normalized.json` - stricter same-mask
    cross-wavelength shape-difference summary
  - `repeatability_report.md`
  - `report.md` - alias for the repeatability report
- **Script:** `scripts/analyze_psf_repeatability.py`
- **Status:** hardware capture and analysis complete for current baseline
- **Current frozen conclusion:**
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
  - Interpretation: normalized companion metrics are the stricter basis for
    cross-wavelength shape claims.
  - `mask_induced_differences_larger_than_repeat_noise = true`

### `outputs/dotf/`

- **Phase:** 3.3 - dOTF diagnostic visualization
- **Depends on:**
  - current audited raw: `data/raw/bishe_dotf_diagnostic_20260520_004205.h5`
  - `outputs/psf_roi/psf_roi.json` (Phase 3.2a, camera domain)
  - `outputs/pupil_geometry/effective_pupil_window.json` (Phase 3.1, LCD domain)
- **Produces:**
  - `psf_reference.npy` - mean reference PSF
  - `psf_reference.png` - quick-look reference PSF
  - `<perturbation_id>/psf_perturbed.npy` - mean perturbed PSF
  - `<perturbation_id>/otf_reference.npy` - reference OTF used for that
    comparison
  - `<perturbation_id>/otf_perturbed.npy` - perturbed OTF
  - `<perturbation_id>/dotf_complex.npy` - complex dOTF
  - `<perturbation_id>/dotf_abs.png` - dOTF amplitude
  - `<perturbation_id>/dotf_log_abs.png` - dOTF log amplitude
  - `<perturbation_id>/dotf_phase.png` - dOTF phase
  - `<perturbation_id>/dotf_real.png` - dOTF real part
  - `<perturbation_id>/dotf_imag.png` - dOTF imaginary part
  - `dotf_metrics.json`
  - `dotf_report.md`
  - `dotf_roi_comparison_manifest.json` - per-ROI comparison summary
  - `dotf_roi_comparison_report.md` - human-readable multi-ROI comparison
  - `<roi_key>/<perturbation_id>/dotf_metrics.json` - per-ROI per-perturbation metrics
- **Script:** `scripts/analyze_dotf.py`
- **Status:** hardware capture and analysis complete for current baseline
- **Current frozen conclusion:**
  - `dotf_computed = true`
  - `pupil_stitching_performed = false`
  - `dotf_peak_abs(edge_block_left) ~= 4.06284e-4`
  - `dotf_peak_abs(edge_block_right) ~= 4.19378e-4`
  - `dotf_peak_abs(edge_block_top) ~= 4.04660e-4`
  - `dotf_peak_abs(edge_block_bottom) ~= 5.96703e-4`
- **Multi-ROI rule:** dOTF may be recomputed for multiple ROI candidates from
  existing full-frame raw data. No automatic ROI selection is performed.
- **Boundary:** Diagnostic visualization only. No pupil stitching or final
  complex pupil reconstruction is performed here.
- **Current audited run:** the latest audited multi-wavelength dOTF result is
  currently stored under `outputs/dotf_20260520_004205/` with raw source
  `data/raw/bishe_dotf_diagnostic_20260520_004205.h5`. The legacy
  `outputs/dotf/` directory should not be assumed to be the active baseline
  unless a deliberate promotion is performed.

### `outputs/psf_dictionary/`

- **Phase:** 3.4 - PSF dictionary and LCD_forward export
- **Depends on:**
  - current audited raw: `data/raw/bishe_psf_dictionary_20260520_010603.h5`
  - `outputs/psf_roi/psf_roi.json` (Phase 3.2a, camera domain)
  - `outputs/pupil_geometry/effective_pupil_window.json` (Phase 3.1, LCD domain)
- **Produces:**
  - `psf_dictionary_summary.json` - measured-PSF dictionary metadata and quality summary
  - `psf_dictionary_manifest.json` - per-mask manifest with family and repeat count
  - `mask_preview_contact_sheet.png` - tiled lowres mask preview
  - `psf_preview_contact_sheet.png` - tiled repeat-averaged PSF preview
  - `psf_mean_stack.npy` - repeat-averaged PSF crop per mask and wavelength
  - `psf_crop_stack.npy` - raw per-repeat PSF crops
  - `mask_lowres_stack.npy` - lowres control masks `[N,1,64,64]`
  - `export_lcd_forward/train.h5` - LCD_forward-compatible training split
  - `export_lcd_forward/val.h5` - LCD_forward-compatible validation split
  - `export_lcd_forward/test.h5` - LCD_forward-compatible test split
  - `psf_dictionary_report.md`
- **Script:** `scripts/analyze_psf_dictionary.py`
- **Status:** hardware capture and analysis complete for current baseline
- **Current audited run:** the active audited result is
  `outputs/psf_dictionary_20260520_010603/` with raw source
  `data/raw/bishe_psf_dictionary_20260520_010603.h5`
- **Current audited summary:**
  - `psf_dictionary_acquired = true`
  - `psf_roi_key_used = roi_512`
  - `wavelengths_nm = [450.0, 550.0, 650.0]`
  - `n_masks = 170`
  - `repeats_per_mask = 5`
  - `export_lcd_forward.enabled = true`
- **Historical failed run:** the older failed raw files remain preserved for
  audit and should not be treated as the current baseline
- **ROI rule:** Phase 3.4 must use a manually selected ROI after reviewing the
  Phase 3.3 multi-ROI dOTF comparison. The current selected modelling ROI is
  `roi_512`. `roi_256` remains the frozen baseline, not the current Phase 3.4
  crop target.
- **Storage rule:** Phase 3.4 raw dictionary capture stores PSF ROI crops only,
  not full-frame raw averages. Full-frame preservation for ROI diagnostics and
  dOTF support inspection belongs to Phase 3.2a / 3.3.
- **Boundary:** Data-first acquisition and export only. No forward-model
  training is performed here.
- **Canonical-path note:** the latest audited Phase 3.4 result has not yet
  been promoted to the canonical directory name `outputs/psf_dictionary/`.
  Downstream consumers should point explicitly to
  `outputs/psf_dictionary_20260520_010603/export_lcd_forward/` until that
  promotion is performed.
- **Wavelength rule:** Phase 3.6 target capture must not request wavelengths
  that are missing from these exports. The export records `wavelengths_nm`
  explicitly so `optic_system` can fail fast before reconstruction.

### `outputs/target_capture/`

- **Phase:** 3.6 - target multiframe / multi-wavelength capture export
- **Depends on:**
  - `data/raw/bishe_target_capture.h5`
  - `outputs/psf_roi/psf_roi.json`
  - `outputs/pupil_geometry/effective_pupil_window.json`
  - current audited Phase 3.4 export:
    - `outputs/psf_dictionary_20260520_010603/export_lcd_forward/train.h5`
    - `outputs/psf_dictionary_20260520_010603/export_lcd_forward/val.h5`
    - `outputs/psf_dictionary_20260520_010603/export_lcd_forward/test.h5`
- **Produces:**
  - `export_lcd_forward/target_frames.h5` - LCD_forward-compatible target-frame dataset
  - `export_lcd_forward/README.md` - dataset note and shape summary
- **Script:** `scripts/export_target_lcd_forward.py`
- **Status:** scripts implemented; hardware data pending
- **Boundary:** No reconstruction is performed in `optic_system`. This
  directory contains only derived export data for `LCD_forward`.
- **Raw storage note:** Phase 3.6 target capture may still preserve full-frame
  averaged observations together with ROI crops. That is distinct from Phase
  3.4 dictionary capture, which now stores the selected ROI crop only.

### Phase 3.5 / 3.7 note

- Phase 3.5 forward validation is skipped in `optic_system` and belongs to
  `LCD_forward`.
- Phase 3.7 thesis figure aggregation and report freeze are skipped in
  `optic_system` and belong to `LCD_forward` or a thesis-writing workspace.
  optic_system does export thesis-APPENDIX calibration diagnostic figures
  (U1: LCD effective pupil, U2: PSF ROI comparison, U2b: energy decomposition)
  from Phase 3 handoff artifacts; see `outputs/thesis_figures/`.

### `outputs/thesis_figures/`

- **Phase:** UPSTREAM U1/U2/U2b - Thesis appendix calibration figures
- **Depends on:** Phase 3 handoff release (`D:/datasets/optic_system/phase3_release_20260520`)
- **Script:** `scripts/export_thesis_calibration_figures.py`
- **Hardware required:** no
- **Produces:**
  - `appendix_lcd_effective_pupil_annotated.pdf/png` — U1: LCD encoding region with boundary overlay
  - `appendix_psf_roi_comparison.pdf/png` — U2: PSF ROI candidate overlay, linear [bg,peak] display + log inset
  - `appendix_roi_energy_decomposition.pdf/png` — U2b: support-domain enclosed energy and far-field threshold decomposition
  - `appendix_psf_tail_enhanced.pdf/png` — U2c: full-frame PSF tail-enhanced view (p=0.99 percentile, magma colormap, 64x64 diffraction peak zoom)
  - `appendix_calibration_summary.csv` — LCD/PSF/exposure calibration summary table
  - `appendix_roi_energy_decomposition.csv` — per-ROI energy coverage, support-domain and far-field sections
  - `thesis_optic_system_figures_manifest.json` — output manifest with provenance
  - `README.md` — description, conclusions, regeneration instructions
- **Status:** complete (thesis-ready appendix figures)

### `outputs/bishe_figures/`

- **Phase:** 3.7 - Thesis figures and report freeze
- **Depends on:** All preceding output directories
- **Produces:**
  - Figures in publication-quality format (PNG, PDF)
  - Figure generation scripts (frozen)
  - Commit hash record for reproducibility
- **Status:** final (thesis-ready)

## Provenance rule

Every analysis output file that may be used in the thesis must record, in its
accompanying metadata (JSON sidecar or HDF5 attribute):

| Field | Description |
|---|---|
| `source_raw_capture_h5` | Absolute or repository-relative path to the source raw capture file |
| `capture_plan_id` | Plan ID from the capture plan YAML |
| `mask_ids` | List of mask identifiers used |
| `wavelength_ids` | List of wavelengths (nm) used |
| `preprocessing` | Dict of preprocessing parameters (ROI size, crop coordinates, background subtraction method, etc.) |
| `analysis_script` | Script name that produced this output |
| `analysis_commit` | Git commit hash of the repository at analysis time |

This rule exists because old experimental data was lost. New results must be
fully auditable and reproducible.

Scripts should embed this metadata automatically. Manual entry is acceptable
for early exploration but must be replaced by automated recording before
thesis freeze.

## Current frozen baseline

The current audited Phase 3 baseline is summarized in:

- `docs/phase3_current_results.md`

If any later rerun changes the active:

- `outputs/exposure_calibration/camera_params_psf_safe.json`
- `outputs/pupil_geometry/effective_pupil_window.json`
- `outputs/psf_roi/psf_roi.json`

then that document must be updated in the same change.

## Directory creation convention

Subdirectories under `outputs/` may be created by analysis scripts as needed.
This `README.md` defines the canonical directory names. Scripts should create
directories with `os.makedirs(..., exist_ok=True)`.

If raw capture HDF5 files are written to `outputs/`, they should be placed in
the same subdirectory as their downstream analysis outputs for co-location.
