# Pupil Scan Report

- source_raw_capture_h5: `data\raw\bishe_pupil_scan.h5`
- capture_plan_id: `bishe_pupil_scan`
- method: `robust_support_consensus`
- confidence: `medium`
- roi_physical: `{'x_min': 1045, 'x_max': 1181, 'y_min': 1782, 'y_max': 1994}`

## Warnings
- PR #24 review observed clipping/local saturation in the Phase 3.1 hardware
  images. This result is first-pass coarse active-region localization only.
- Do not describe this output as final pupil geometry, final effective pupil,
  calibrated active pupil, or a PSF-safe scan.
- Final fine strip scan, dOTF, PSF dictionary, and PSF repeatability must use
  `outputs/exposure_calibration/camera_params_psf_safe.json` after rerunning
  Phase 3.0.5b PSF-safe exposure refinement.
- The old `outputs/exposure_calibration/camera_params.json` has been revoked as
  a run input. Historical parameters for this report are preserved in raw HDF5
  camera provenance.

## Inputs
- x_profile_available: True
- y_profile_available: True
- response_map_shape: [20, 20]
