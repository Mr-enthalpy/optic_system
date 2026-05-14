# Capture plans

## Role

Capture plans define parameters for one capture or calibration task invocation.
Some plans are consumed by task-specific scripts (e.g. `calibrate_psf_safe_exposure.py`,
`capture_pupil_scan.py`) rather than `capture_forward_dataset.py`.
Each plan specifies masks, wavelengths, camera settings, and settle timing.
The capture script reads a plan, initializes devices, executes the acquisition
sequence, and writes a `raw_capture.h5` file.

**All plans produce raw capture HDF5 first.**  Downstream analysis scripts
consume the raw HDF5 to produce processed results.

## Active plans

| File | Purpose |
|---|---|
| `hardware_smoke_no_tls.yaml` | Hardware smoke test: camera + LCD, no TLS |
| `hardware_smoke_with_tls.yaml` | Hardware smoke test: camera + LCD + TLS |
| `bishe_psf_safe_exposure.yaml` | Phase 3.0.5b PSF-safe exposure/gain refinement |
| `bishe_pupil_scan.yaml` | Phase 3.1 procedural effective LCD pupil scan |

### `plans/bishe_psf_safe_exposure.yaml`
- **Phase:** 3.0.5b - PSF-safe exposure/gain refinement.
- **Purpose:** Find camera parameters whose raw burst frames are strictly below
  dtype full scale for every tested wavelength.
- **TLS sequencing:** Wavelength/grating movement is the outer hardware loop.
  The script sets a wavelength once, waits for the slow TLS motion to finish,
  then evaluates the plan-derived `(exposure_us, gain_db)` candidates at that
  wavelength.
- **TLS requirement:** Hardware runs require `--tls-serial` or `TLS_C1_SERIAL`.
  Without TLS participation, plan wavelengths are only labels and the output
  cannot prove cross-wavelength safety. The dangerous
  `--allow-wavelength-labels-without-tls` override is reserved for explicit
  manual external wavelength control or fixed single-wavelength tests.
- **Hardware scheduling invariant:** TLS / monochromator motion is slow,
  expensive, and motion-state-sensitive. LCD updates are cheap and camera
  parameter changes plus repeated burst acquisition are cheap enough for this
  calibration. Therefore, Phase 3.0.5b keeps TLS wavelength movement as the
  outer hardware loop: each planned wavelength should trigger at most one
  TLS move/wait cycle per search pass, and exposure/gain candidates are
  evaluated under that fixed wavelength. If future coarse/fine passes are
  added, TLS move count must scale with
  `N_wavelengths * N_search_passes`, not with
  `N_wavelengths * N_candidates`.
- **Selection policy:** Current thesis-branch 3.0.5b uses an auditable
  discrete grid and lexicographic selection, not continuous joint optimization
  of `(gain_db, exposure_us)`. The objective is strict PSF safety across all
  planned wavelengths, then preference for `gain_db_min`, then the largest
  usable exposure at that gain. Higher gain is penalized and used only when
  `gain_db_min` is PSF-safe but unusably dim. Camera parameters are global
  across wavelengths so Phase 3.1 pupil scan, PSF repeatability, and PSF
  dictionary captures remain comparable.
- **Output raw HDF5:** `data/raw/bishe_psf_safe_exposure.h5`.
- **Downstream output:** `outputs/exposure_calibration/camera_params_psf_safe.json`
  is written only when a globally PSF-safe setting is found.

### `plans/bishe_pupil_scan.yaml`
- **Phase:** 3.1 - Effective LCD pupil / active modulation region scan
- **Purpose:** Locate the physical LCD region where procedural mask changes
  measurably affect camera captures.
- **Input:** `camera_params_source` from Phase 3.0.5b by default
  (`outputs/exposure_calibration/camera_params_psf_safe.json`).
- **Masks:** Generated procedurally at runtime by
  `tasks/pupil_scan_masks.py`; the plan does not list hundreds of mask files.
- **Output raw HDF5:** `data/raw/bishe_pupil_scan.h5`.
- **Analysis:** `scripts/analyze_pupil_scan.py` writes
  `outputs/pupil_scan/effective_lcd_roi.json` and diagnostics.

## Planned plans (Phase 3 thesis)

The following capture plans are defined for the Phase 3 thesis workflow.
They will be implemented in their respective milestones.

### `plans/bishe_psf_repeatability.yaml`
- **Phase:** 3.2 — PSF repeatability and ROI alignment
- **Purpose:** Capture multiple repeats of 2-3 distinct masks to quantify
  within-mask repeat noise and between-mask differences.
- **Masks:** 2-3 distinct mask patterns (e.g., full-white, full-dark,
  grating).  Each repeated K times (K >= 3).
- **Wavelengths:** Single wavelength.
- **Camera:** Burst of frames per capture (`frames_per_capture` >= 10).
- **Camera params:** `outputs/exposure_calibration/camera_params_psf_safe.json`.
- **Expected output raw HDF5:** `outputs/psf_repeatability/repeatability_raw.h5`
- **Downstream analysis:** Repeatability analysis -> `repeatability_metrics.json`

### `plans/bishe_dotf_edge_perturb.yaml`
- **Phase:** 3.3 — dOTF diagnostic
- **Purpose:** Capture base mask + perturbation mask PSF pairs for dOTF
  computation.  Perturbations are placed at the edge of the effective pupil
  to probe pupil-plane structure.
- **Masks:** Base circular window mask + several perturbation masks
  (base with small disk/gaussian perturbation at pupil edge).  Generated by
  a perturbation mask script.
- **Wavelengths:** Single wavelength.
- **Camera:** Averaged frames per mask.
- **Camera params:** `outputs/exposure_calibration/camera_params_psf_safe.json`.
- **Expected output raw HDF5:** `outputs/dotf/dotf_raw.h5`
- **Downstream analysis:** dOTF computation -> dOTF complex arrays,
  magnitude/phase visualizations.

### `plans/bishe_psf_dict_single_lambda.yaml`
- **Phase:** 3.4 — PSF dictionary (1 wavelength)
- **Purpose:** Build a mask-to-PSF dictionary at a single wavelength.
- **Masks:** Gratings at various periods/orientations, checkerboards, radial
  patterns.  ~10-20 distinct masks.
- **Wavelengths:** Single wavelength.
- **Camera:** Averaged frames per mask.
- **Camera params:** `outputs/exposure_calibration/camera_params_psf_safe.json`.
- **Expected output raw HDF5:** `outputs/psf_dictionary/psf_dict_single_lambda_raw.h5`
- **Downstream analysis:** PSF extraction, normalization, LCD_forward export.

### `plans/bishe_psf_dict_three_lambda.yaml`
- **Phase:** 3.4 / 3.6 — PSF dictionary (3 wavelengths)
- **Purpose:** Build a mask-to-PSF dictionary at 3 wavelengths for
  multispectral reconstruction.
- **Masks:** Same mask set as single-lambda dictionary.
- **Wavelengths:** 3 wavelengths (e.g., 480 nm, 555 nm, 630 nm).
- **Camera:** Averaged frames per mask.
- **Camera params:** `outputs/exposure_calibration/camera_params_psf_safe.json`.
- **Expected output raw HDF5:** `outputs/psf_dictionary/psf_dict_three_lambda_raw.h5`
- **Downstream analysis:** PSF extraction per wavelength, LCD_forward export.

### `plans/bishe_multiframe_target.yaml`
- **Phase:** 3.6 — Multiframe reconstruction
- **Purpose:** Capture target scene frames for reconstruction validation.
- **Masks:** 1-2 target scenes (e.g., combined grating patterns, simple
  geometric shapes).
- **Wavelengths:** 3 wavelengths (matching the PSF dictionary).
- **Camera:** Averaged frames per mask per wavelength.
- **Expected output raw HDF5:** `outputs/linear_recon/multiframe_target_raw.h5`
- **Downstream analysis:** Linear inverse reconstruction -> reconstructed
  scenes, metrics.

## Plan format

Plans are task-specific YAML files.  Common fields include `plan_id`,
`wavelength` or `wavelengths`, camera settings, and output paths.
Task-specific fields (e.g. `scan.scan_modes`, `camera_params_source`,
`signal`) vary by plan.

See individual plan files under `plans/` for current schemas.

