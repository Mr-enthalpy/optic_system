# Outputs directory

## Role

`outputs/` stores all processed analysis results from the Phase 3 thesis
workflow.  Raw capture HDF5 files may also be placed here, at the user's
discretion, but the canonical location for raw captures is the path specified
by the capture plan.

**Every output must trace back to a source `raw_capture.h5` file.**  See
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
  - `effective_pupil_window.json` - effective circular pupil window,
    circle estimate, ellipse-overlap fit, and provenance
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

### `outputs/psf_repeatability/`

- **Phase:** 3.2 — PSF repeatability and ROI alignment
- **Depends on:** `outputs/psf_repeatability/repeatability_raw.h5`
- **Produces:**
  - `psfs_aligned.npy` — aligned PSF stack
  - `psfs_mean.npy` — per-mask mean PSF
  - `psfs_std.npy` — per-mask PSF standard deviation
  - `mask_difference_matrix.npy` — between-mask distance matrix
  - `mask_difference_matrix.png` — visualisation
  - `repeatability_metrics.json` — within-mask variance, between-mask distance, SNR-like ratio
  - `psf_repeatability_report.md`
- **Script:** repeatability analysis scripts (to be created in Phase 3.2)
- **Status:** planned

### `outputs/dotf/`

- **Phase:** 3.3 — dOTF diagnostic
- **Depends on:** `outputs/dotf/dotf_raw.h5`
- **Produces:**
  - `psf_ref.npy` — reference PSF
  - `psf_perturbed.npy` — perturbed PSF
  - `otf_ref.npy` — reference OTF
  - `otf_perturbed.npy` — perturbed OTF
  - `dotf_complex.npy` — complex dOTF
  - `dotf_amp.png` — dOTF amplitude
  - `dotf_phase.png` — dOTF phase
  - `dotf_structure_overlay.png` — structure overlay
  - `dotf_sparsity_metrics.json` — sparsity / structure diagnostics
  - `dotf_report.md`
- **Script:** dOTF analysis scripts (to be created in Phase 3.3)
- **Status:** planned

### `outputs/psf_dictionary/`

- **Phase:** 3.4 — PSF dictionary and LCD_forward export
- **Depends on:**
  - `data/raw/bishe_psf_dict_*.h5`
- **Produces:**
  - `masks_physical.npy` — physical mask arrays
  - `masks_downsampled.npy` — downsampled masks
  - `psfs_mean.npy` — per-mask mean PSF
  - `psfs_std.npy` — per-mask PSF standard deviation
  - `wavelengths.npy` — wavelength list
  - `dictionary_metadata.json`
  - `psf_dict_lambda_<wl>nm.h5` — LCD_forward-compatible HDF5 (single lambda)
  - `psf_dict_three_lambda.h5` — LCD_forward-compatible HDF5 (3 wavelengths)
- **Script:** dictionary export scripts (to be created in Phase 3.4)
- **Status:** planned

### `outputs/linear_recon/`

- **Phase:** 3.5 (forward model), 3.6 (multiframe reconstruction)
- **Depends on:**
  - `outputs/psf_dictionary/` — PSF dictionary
  - `outputs/linear_recon/multiframe_target_raw.h5` — target captures
- **Produces:**
  - `A_matrix_info.json` — forward matrix metadata
  - `condition_number_report.json` — channel condition analysis
  - `recon_single_frame.npy` — single-frame reconstruction
  - `recon_multiframe.npy` — multi-frame reconstruction
  - `recon_error.npy` — reconstruction error map
  - `recon_comparison.png` — single vs multi-frame comparison
  - `linear_recon_report.md`
- **Script:** reconstruction scripts (to be created in Phase 3.5/3.6)
- **Status:** planned

### `outputs/bishe_figures/`

- **Phase:** 3.7 — Thesis figures and report freeze
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

This rule exists because old experimental data was lost.  New results must be
fully auditable and reproducible.

Scripts should embed this metadata automatically.  Manual entry is acceptable
for early exploration but must be replaced by automated recording before
thesis freeze.

## Directory creation convention

Subdirectories under `outputs/` may be created by analysis scripts as needed.
This `README.md` defines the canonical directory names.  Scripts should create
directories with `os.makedirs(..., exist_ok=True)`.

If raw capture HDF5 files are written to `outputs/`, they should be placed in
the same subdirectory as their downstream analysis outputs for co-location.
