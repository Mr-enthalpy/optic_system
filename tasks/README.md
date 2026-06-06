# Tasks Directory

## Purpose

This document is an index of implemented task modules. Roadmap phase structure
lives in [`../docs/roadmap.md`](../docs/roadmap.md). Profile-chain operational
rules live in
[`../docs/profile_task_chain.md`](../docs/profile_task_chain.md).

## Active Capture Layer

| File | Status | Purpose |
|------|--------|---------|
| `capture_plan.py` | **active** | Capture plan dataclasses, JSON/YAML loading, and validation. |
| `raw_capture_h5.py` | **active** | Raw capture HDF5 writer with resizable datasets and metadata preservation. |
| `capture_forward_dataset.py` | **active** | Minimal capture orchestration with a narrow `CaptureDeviceBundle` protocol and fake/real adapters. |

CLI entry point:

```text
scripts/capture_forward_dataset.py
```

## Active Runtime Mode Helpers

| File | Status | Purpose |
|------|--------|---------|
| `tasks/runtime_mode.py` | **active** | Explicit `hardware`, `no_hardware`, `synthetic`, and `diagnostic` runtime validation policies. |

Real hardware tasks default to hardware runtime mode.
Fake devices, missing required hardware, diagnostic-only shortcuts, and
test-settle overrides must be explicit non-hardware/diagnostic choices.
No-TLS positive wavelength labels are allowed only in non-hardware contexts.
TLS zero-order pass-through requires a real TLS adapter in hardware mode.

## Active Profile Modules

| File | Status | Purpose |
|------|--------|---------|
| `tasks/profiles/pupil_profile.py` | **active** | Effective LCD pupil profile artifact. |
| `tasks/profiles/camera_profile.py` | **active** | Camera exposure profile artifact. |
| `tasks/profiles/calibrate_broadband_camera_profile.py` | **active** | Broadband pass-through camera calibration for LCD pupil scanning. |
| `tasks/profiles/scan_pupil_broadband.py` | **active** | Broadband pass-through LCD pupil scan and `PupilProfile` generation. |
| `tasks/profiles/calibrate_per_band_pupil_open_camera_profile.py` | **active** | Per-band exposure calibration under selected-pupil-open LCD state. |

Profile-chain hardware rules are centralized in
[`../docs/profile_task_chain.md`](../docs/profile_task_chain.md).

## Active PSF / Peak-Cluster Modules

| File | Status | Purpose |
|------|--------|---------|
| `tasks/psf/profile_requirements.py` | **active** | Explicit profile dependency validation for broadband pupil scan and PSF-producing tasks. |
| `tasks/psf/build_full_frame_psf_survey.py` | **active** | Full-frame scout artifact for peak layout discovery. |
| `tasks/psf/sensor_energy_center.py` | **active** | Sensor energy center profile derivation for center-relative PSF / peak-cluster coordinates. |
| `tasks/psf/derive_peak_layout_profile.py` | **active** | Peak layout profile derivation from scout survey data. |
| `tasks/psf/build_peak_patch_psf_dictionary.py` | **active** | Profile-dependent peak-patch PSF dictionary builder. |
| `tasks/psf/export_peak_patch_dictionary_to_lcd_forward.py` | **active** | Existing peak-patch measured-evidence export for downstream operator modelling. |
| `tasks/psf/compact_dense_export.py` | **active** | Diagnostic dense canvas rendering from peak patches and recorded coordinates. |

## Active Shared Artifact Helpers

| File | Status | Purpose |
|------|--------|---------|
| `tasks/artifacts/json_io.py` | **active** | Hardware-free JSON and HDF5 string helpers for measured artifacts. |
| `tasks/artifacts/coordinate_frame.py` | **active** | Shared camera frame extent and coordinate-frame descriptors / validation. |
| `tasks/artifacts/frame_source.py` | **active** | Shared HDF5 frame-source descriptors for `FullFramePSFSurvey` inputs. |
| `tasks/artifacts/manifest.py` | **active** | Minimal manifest reference dataclasses and JSON helpers. |

New measured-artifact modules should use `tasks/artifacts/` instead of
reimplementing frame-source parsing or coordinate validation.

Measured-artifact analysis tasks consume `FullFramePSFSurvey`. RawCapture HDF5
must be explicitly converted into `FullFramePSFSurvey` before sensor-center,
support, or layout analysis. Pre-mainline raw files must be migrated explicitly
before current measured-artifact analysis.

Raw capture metadata should use camera frame extent terminology.
`/camera/frame_extent_json` is the raw HDF5 field. Capture plans must use
`camera.frame_extent`. Pre-mainline thesis/development data are outside the
current schema and require explicit migration if needed.

## Active Illumination Helpers

| File | Status | Purpose |
|------|--------|---------|
| `tasks/illumination.py` | **active** | Typed `IlluminationSpec` normalization for monochromatic, broadband pass-through, and label-only illumination semantics. |

Capture plans must use explicit `illumination` objects. TLS zero-order
broadband pass-through is represented by
`illumination.mode=broadband_passthrough`; numeric wavelength sentinels are not
supported capture-plan inputs. Wavelength labels without TLS are not equivalent
to pass-through.

## Active Tests

| File | Status |
|------|--------|
| `tests/test_capture_plan.py` | **active** |
| `tests/test_raw_capture_h5.py` | **active** |
| `tests/test_capture_forward_dataset_dry_run.py` | **active** |

Additional task-specific tests live with the relevant profile, PSF, support,
and export modules.

## Planned Directions

Planned task names are roadmap items, not active files. See
[`../docs/roadmap.md`](../docs/roadmap.md) for future profile-dependent
capture tasks, diagnostics, and conversion directions.

## Policy

No historical task revival is planned. The active module list above defines
the implementation surface. All new task code must preserve raw capture
metadata and profile identifiers. Hardware tests must remain opt-in.
