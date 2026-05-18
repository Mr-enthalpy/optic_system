# Capture plans

## Role

Capture plans define parameters for one capture or calibration task invocation.
Some plans are consumed by task-specific scripts (e.g. `calibrate_psf_safe_exposure.py`,
`capture_pupil_geometry.py`) rather than `capture_forward_dataset.py`.
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
| `bishe_pupil_geometry.yaml` | Phase 3.1 effective pupil geometry calibration |
| `bishe_psf_roi.yaml` | Phase 3.2a camera-frame PSF ROI calibration |
| `bishe_psf_repeatability.yaml` | Phase 3.2b PSF repeatability and mask-induced diversity |
| `bishe_dotf_diagnostic.yaml` | Phase 3.3 dOTF diagnostic visualization |

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
  evaluated under that fixed wavelength. TLS move count must scale with the
  wavelength list, not with the exposure/gain candidate list.
- **Selection policy:** Current thesis-branch 3.0.5b uses an auditable
  discrete grid and lexicographic selection, not continuous joint optimization
  of `(gain_db, exposure_us)`. The objective is strict PSF safety across all
  planned wavelengths, then preference for `gain_db_min`, then the largest
  usable exposure at that gain. Higher gain is penalized and used only when
  `gain_db_min` is PSF-safe but unusably dim. Camera parameters are global
  across wavelengths so Phase 3.1 pupil geometry, PSF repeatability, and PSF
  dictionary captures remain comparable.
- **Valid-pixel domain:** The strict full-scale rule is evaluated over the
  plan's explicit `valid_pixel_domain`. The default is full frame. Known
  invalid pixels may be excluded only through a recorded policy such as
  `exclude_top_rows`; invalid-domain full-scale artifacts are diagnostics and
  do not relax the zero-tolerance rule inside the valid domain. Signal metrics
  used for selection are computed over the same valid domain.
- **Output raw HDF5:** `data/raw/bishe_psf_safe_exposure.h5`.
- **Downstream output:** `outputs/exposure_calibration/camera_params_psf_safe.json`
  is written only when a globally PSF-safe setting is found.

### `plans/bishe_pupil_geometry.yaml`
- **Phase:** 3.1 - Effective pupil geometry calibration.
- **Purpose:** Calibrate an effective circular pupil window in LCD physical
  coordinates using X/Y dark-bar energy profiles and a circular-aperture
  radius scan.
- **Input:** `camera_params_source` from Phase 3.0.5b by default
  (`outputs/exposure_calibration/camera_params_psf_safe.json`).
- **Camera profile:** `camera_profile: fast_pupil_scan` is preferred.
  `global_safe_camera` fallback is allowed only when the plan explicitly sets
  `allow_global_safe_camera_fallback: true`, and that provenance is recorded
  in HDF5 and analysis outputs.
- **Masks:** Generated procedurally at runtime by
  `tasks/pupil_geometry_masks.py`; the plan records bar width/step and radius
  scan range rather than listing mask files.
- **TLS requirement:** Hardware runs require `--tls-serial` or `TLS_C1_SERIAL`.
  Without TLS wavelength filtering the light source outputs broadband white
  light, which will overexpose the camera with PSF-safe parameters.
  The dangerous `--allow-wavelength-labels-without-tls` override is
  reserved for explicit manual external wavelength control.
- **Output raw HDF5:** `data/raw/bishe_pupil_geometry.h5`.
- **Analysis:** `scripts/analyze_pupil_geometry.py` writes
  `outputs/pupil_geometry/effective_pupil_window.json` and diagnostics.

### `plans/bishe_psf_roi.yaml`
- **Phase:** 3.2a - Camera-frame PSF ROI calibration
- **Purpose:** Determine a fixed crop window in camera sensor coordinates for
  the point-source PSF.  Produces `psf_roi.json` as the single source of truth
  for all subsequent PSF crops.
- **Inputs:**
  - `outputs/pupil_geometry/effective_pupil_window.json` (LCD mask window)
  - `outputs/exposure_calibration/camera_params_psf_safe.json`
- **LCD:** Displays the effective circular pupil window (inside = all-open,
  outside = all-closed).
- **Masks:** Single mask (effective pupil window).  Not a mask list.
- **Wavelengths:** Single wavelength.
- **Camera:** Burst of N frames per capture, K repeats.
- **LCD settle:** Hardware validation rejects values below 100 ms; default is
  200 ms.
- **Output raw HDF5:** `data/raw/bishe_psf_roi.h5`
- **Downstream analysis:** `scripts/analyze_psf_roi.py` writes
  `outputs/psf_roi/psf_roi.json` and diagnostics.

### `plans/bishe_psf_repeatability.yaml`
- **Phase:** 3.2b - PSF repeatability and mask-induced diversity
- **Purpose:** Capture multiple repeats of a representative mask set to quantify
  intra-mask repeat noise and inter-mask PSF diversity.
- **Inputs:**
  - `outputs/psf_roi/psf_roi.json` (camera-frame crop)
  - `outputs/pupil_geometry/effective_pupil_window.json` (LCD mask window)
  - `outputs/exposure_calibration/camera_params_psf_safe.json`
- **Masks:** Representative low-frequency mask set, each repeated K times.
  Outside the effective pupil window is opaque for every mask.
- **Wavelengths:** Single wavelength.
- **Camera:** Burst of N frames per capture.
- **LCD settle:** Hardware validation rejects values below 100 ms; default is
  200 ms.
- **Output raw HDF5:** `data/raw/bishe_psf_repeatability.h5`
- **Downstream analysis:** `scripts/analyze_psf_repeatability.py` writes
  repeatability/diversity JSON, matrix `.npy` files, and diagnostics.

## Planned plans (later Phase 3 thesis)

### `plans/bishe_dotf_diagnostic.yaml`
- **Phase:** 3.3 - dOTF diagnostic visualization
- **Purpose:** Capture base mask + perturbation mask PSF pairs for dOTF
  computation.  Perturbations are edge-local blocks at the effective pupil
  boundary to probe pupil-plane structure.
- **Inputs:**
  - `outputs/psf_roi/psf_roi.json` (camera-frame crop)
  - `outputs/pupil_geometry/effective_pupil_window.json` (LCD mask window)
  - `outputs/exposure_calibration/camera_params_psf_safe.json`
- **Masks:** Base effective pupil window mask + perturbation masks
  (base with edge-local opaque blocks at various positions).
- **Wavelengths:** Single wavelength.
- **Camera:** One reference burst plus one burst per perturbation for each
  repeat. Reference and perturbed captures are interleaved by repeat to limit
  source drift.
- **Output raw HDF5:** `data/raw/bishe_dotf_diagnostic.h5`
- **Downstream analysis:** `scripts/analyze_dotf.py` writes per-perturbation
  dOTF complex arrays and abs/log_abs/phase/real/imag visualizations.
- **Boundary:** Diagnostic visualization only. No pupil stitching or full
  complex pupil reconstruction.

### `plans/bishe_psf_dict_single_lambda.yaml`
- **Phase:** 3.4 - PSF dictionary (1 wavelength)
- **Purpose:** Build a mask-to-PSF dictionary at a single wavelength.
- **Masks:** Gratings at various periods/orientations, checkerboards, radial
  patterns.  ~10-20 distinct masks.
- **Wavelengths:** Single wavelength.
- **Camera:** Averaged frames per mask.
- **Camera params:** `outputs/exposure_calibration/camera_params_psf_safe.json`.
- **Expected output raw HDF5:** `outputs/psf_dictionary/psf_dict_single_lambda_raw.h5`
- **Downstream analysis:** PSF extraction, normalization, LCD_forward export.

### `plans/bishe_psf_dict_three_lambda.yaml`
- **Phase:** 3.4 / 3.6 - PSF dictionary (3 wavelengths)
- **Purpose:** Build a mask-to-PSF dictionary at 3 wavelengths for
  multispectral reconstruction.
- **Masks:** Same mask set as single-lambda dictionary.
- **Wavelengths:** 3 wavelengths (e.g., 480 nm, 555 nm, 630 nm).
- **Camera:** Averaged frames per mask.
- **Camera params:** `outputs/exposure_calibration/camera_params_psf_safe.json`.
- **Expected output raw HDF5:** `outputs/psf_dictionary/psf_dict_three_lambda_raw.h5`
- **Downstream analysis:** PSF extraction per wavelength, LCD_forward export.

### `plans/bishe_multiframe_target.yaml`
- **Phase:** 3.6 - Multiframe reconstruction
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
