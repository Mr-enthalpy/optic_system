# Outputs directory

## Role

`outputs/` stores all processed analysis results from the Phase 3 thesis
workflow.  Raw capture HDF5 files may also be placed here, at the user's
discretion, but the canonical location for raw captures is the path specified
by the capture plan.

**Every output must trace back to a source `raw_capture.h5` file.**  See
provenance rule below.

## Output subdirectories

### `outputs/pupil_scan/`

- **Phase:** 3.1 — Effective LCD pupil scan
- **Depends on:** `outputs/pupil_scan/pupil_scan_raw.h5` (or user-specified
  raw capture path)
- **Produces:**
  - `effective_lcd_roi.json` — pupil center, radii, optionally ellipse
    parameters
  - `x_profile.csv` / `y_profile.csv` — raw energy-difference profiles
    (optional)
  - `pupil_scan_figure.png` — visualization (optional)
- **Script:** pupil scan analysis script (to be created in Phase 3.1)
- **Status:** intermediate — consumed by later phases, not thesis-final

### `outputs/psf_repeatability/`

- **Phase:** 3.2 — PSF repeatability and ROI alignment
- **Depends on:** `outputs/psf_repeatability/repeatability_raw.h5`
- **Produces:**
  - `repeatability_metrics.json` — within-mask RMS, correlation, between-mask
    differences
- **Script:** repeatability analysis script (to be created in Phase 3.2)
- **Status:** intermediate — validates determinism, not thesis-final

### `outputs/dotf/`

- **Phase:** 3.3 — dOTF diagnostic
- **Depends on:** `outputs/dotf/dotf_raw.h5`
- **Produces:**
  - `dotf_<id>.npy` — complex dOTF arrays (N pairs)
  - `dotf_magnitude_<id>.png` — magnitude visualizations
  - `dotf_phase_<id>.png` — phase visualizations
  - `dotf_summary.json` — parameters and metadata
- **Script:** dOTF analysis script (to be created in Phase 3.3)
- **Status:** thesis-figure-ready (after review)

### `outputs/psf_dictionary/`

- **Phase:** 3.4 — PSF dictionary and LCD_forward export
- **Depends on:**
  - `outputs/psf_dictionary/psf_dict_single_lambda_raw.h5`
  - `outputs/psf_dictionary/psf_dict_three_lambda_raw.h5`
- **Produces:**
  - `psf_dict_lambda_<wl>nm.h5` — LCD_forward-compatible HDF5
  - `psf_dict_three_lambda.h5` — LCD_forward-compatible HDF5 (3 wavelengths)
  - `psf_dictionary_metadata.json` — mask list, wavelengths, ROI parameters
- **Script:** dictionary export script (to be created in Phase 3.4)
- **Status:** thesis-figure-ready (consumed by Phase 3.5 and 3.6)

### `outputs/linear_recon/`

- **Phase:** 3.5 (forward model), 3.6 (multiframe reconstruction)
- **Depends on:**
  - `outputs/psf_dictionary/` — PSF dictionary
  - `outputs/linear_recon/multiframe_target_raw.h5` — target captures
- **Produces:**
  - `forward_model_validation.json` — simple convolution validation metrics
  - `multiframe_recon_results/` — reconstructed scenes as .npy
  - `reconstruction_metrics.json` — reconstruction quality metrics
- **Script:** reconstruction script (to be created in Phase 3.5/3.6)
- **Status:** thesis-figure-ready (after review)

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
