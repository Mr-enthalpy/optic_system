# Tasks Directory

## Purpose

Some historical task files came from early experiments; active task files are
listed below. Do not revive removed or unaudited legacy task logic without
explicit audit.

This document is an index of implemented task modules. Roadmap phase structure
lives in [`../docs/roadmap.md`](../docs/roadmap.md). Profile-chain operational
rules live in
[`../docs/profile_task_chain.md`](../docs/profile_task_chain.md).

## Task Status Classification

Each task file should carry one of the following status labels:

| Status | Meaning |
|--------|---------|
| **active** | Currently in use as part of the mainline architecture. Uses current camera/LCD/TLS conventions and preserves metadata. |
| **planned** | Future direction recorded in the roadmap. Not an active module unless implemented and listed here. |
| **experimental** | Prototype code that may contain useful patterns but has not been audited against current architecture rules. |
| **legacy** | Code from earlier project phases that is no longer maintained. May bypass current boundaries or lack metadata preservation. |
| **deprecated** | Should never be used. Depends on removed or unsupported paths such as pywinauto TLS automation. |

## Audit Criteria

Before classifying or reusing a task, check:

1. Does it use the current `control -> devices` boundary, or document a narrow task adapter boundary?
2. Does it bypass `SessionController`?
3. Does it depend on old pywinauto TLS logic?
4. Does it preserve raw capture metadata and profile identifiers?
5. Is it compatible with the current LCD physical mono mask convention?

Tasks that bypass the current architecture should be marked `legacy` or
`needs audit`. Tasks that use pywinauto TLS automation should be marked
`deprecated`.

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
| `tasks/psf/export_peak_patch_dictionary_to_lcd_forward.py` | **active** | Peak-patch dictionary export with LCD_forward-readable metadata. |
| `tasks/psf/compact_dense_export.py` | **active** | Diagnostic dense canvas rendering from peak patches and recorded coordinates. |

## Active Tests

| File | Status |
|------|--------|
| `tests/test_capture_plan.py` | **active** |
| `tests/test_raw_capture_h5.py` | **active** |
| `tests/test_capture_forward_dataset_dry_run.py` | **active** |

Additional task-specific tests live with the relevant profile, PSF, support,
and export modules.

## Removed Legacy Stubs

The following empty placeholders were removed. Their historical intent is
preserved here only to prevent accidental resurrection:

| Removed file | Original intent |
|--------------|-----------------|
| `aperture_search_task.py` | Aperture parameter search. Never implemented. |
| `calibration_sequence_task.py` | Calibration sequence automation. Never implemented. Full calibration workflow is outside the current task layer. |
| `capture_average_task.py` | Simple frame averaging. Never implemented. Superseded by `capture_forward_dataset.py`. |
| `wavelength_sweep_task.py` | Wavelength sweep orchestration. Never implemented. Full sweep orchestration is not an active task. |

## Planned Directions

Planned task names are roadmap items, not active files. See
[`../docs/roadmap.md`](../docs/roadmap.md) for future profile-dependent
capture tasks, diagnostics, and conversion directions.

## Policy

- Old tasks may be referenced for design patterns but must not be assumed correct.
- New task code must preserve raw capture metadata and profile identifiers.
- Hardware tests must remain opt-in.
- All new task code must be compatible with the architecture rules in `AGENTS.md`.
