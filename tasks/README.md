# Tasks directory

## Important notice

The files under `tasks/` originate from early experimental directions and **must not be assumed to define the current architecture.**

All legacy empty stubs have been removed. Active tasks are listed below.

Do not silently revive legacy task logic. Do not reuse old task scripts without explicit audit.

## Task status classification

Each task file should carry one of the following status labels:

| Status | Meaning |
|--------|---------|
| **active** | Currently in use as part of the mainline architecture. Uses `control -> devices` boundaries. Compatible with current LCD/TLS/camera conventions. |
| **planned** | Will be implemented in a future phase. Module name and purpose are known but implementation is deferred. |
| **experimental** | Research or prototype code that may contain useful patterns but has not been audited against current architecture rules. |
| **legacy** | Code from earlier project phases that is no longer maintained. May bypass `SessionController`, use old conventions, or lack metadata preservation. |
| **deprecated** | Should never be used. Depends on removed or unsupported paths (e.g. pywinauto TLS automation). |

### Audit criteria

Before classifying a task:

1. Does it use `control -> devices` boundaries?
2. Does it bypass `SessionController`?
3. Does it depend on old pywinauto TLS logic?
4. Does it preserve raw capture metadata?
5. Is it compatible with the current LCD physical mask convention `[H, 3W]`?

Tasks that bypass `SessionController` should be marked `legacy` or `needs audit`.

Tasks that use pywinauto TLS automation should be marked `deprecated`.

Tasks that do not preserve raw metadata should be marked `needs redesign`.

## Removed legacy stubs

The following empty placeholders were removed — their historical intent is preserved here:

| Removed file | Original intent |
|------|-------|
| `aperture_search_task.py` | Aperture parameter search. Never implemented. |
| `calibration_sequence_task.py` | Calibration sequence automation. Never implemented. Full calibration workflow is Phase 4 scope. |
| `capture_average_task.py` | Simple frame averaging. Never implemented. Superseded by `capture_forward_dataset.py`. |
| `wavelength_sweep_task.py` | Wavelength sweep orchestration. Never implemented. Full wavelength sweep is outside current phase scope. |

## Planned future tasks (Phase 2)

The following modules have been implemented in Phase 2 as the new minimal capture task layer:

| File | Status | Purpose |
|------|--------|---------|
| `capture_plan.py` | **active** | Capture plan data structures (dataclasses, JSON/YAML loading, validation) |
| `raw_capture_h5.py` | **active** | Raw capture HDF5 writer (incremental, resizable datasets, metadata preservation) |
| `capture_forward_dataset.py` | **active** | Minimal capture orchestration with CaptureDeviceBundle protocol + fake/real adapters |

Corresponding tests:

| File | Status |
|------|--------|
| `tests/test_capture_plan.py` | **active** |
| `tests/test_raw_capture_h5.py` | **active** |
| `tests/test_capture_forward_dataset_dry_run.py` | **active** |

The CLI entry point is `scripts/capture_forward_dataset.py`.

These have been implemented cleanly using `control -> devices` boundaries (via the narrow `CaptureDeviceBundle` protocol) and supersede the legacy stubs currently in this directory.

## Planned future tasks (Phase 3A/3B/3C)

Phase 3 absorbs reusable bachelor-thesis experimental outputs into mainline
abstractions. It is branch-result abstraction, not branch-workflow promotion.
The stable Phase 2 task files remain in their current locations unless a
separate compatibility-preserving move is explicitly planned.

| File / package | Status | Purpose |
|---|---|---|
| `tasks/profiles/pupil_profile.py` | **planned** | Effective LCD pupil profile artifact |
| `tasks/profiles/camera_profile.py` | **planned** | Camera safety profile artifact |
| `tasks/profiles/calibrate_broadband_camera_profile.py` | **planned** | Broadband pass-through exposure calibration |
| `tasks/profiles/scan_pupil_broadband.py` | **planned** | Broadband mixed-light pupil scan |
| `tasks/profiles/calibrate_per_band_pupil_open_camera_profile.py` | **planned** | Per-band exposure calibration under selected-pupil-open LCD state |
| `tasks/psf/capture_psf_dictionary.py` | **planned** | Profile-dependent PSF dictionary capture |
| `tasks/psf/capture_dotf_dataset.py` | **planned** | Profile-dependent dOTF diagnostic capture |
| `tasks/psf/capture_mask_family_psf.py` | **planned** | Profile-dependent mask-family PSF capture |
| `tasks/diagnostics/compute_h_matrix_diagnostic.py` | **planned** | H-matrix diagnostic export for measured PSF dictionaries |
| `tasks/conversion/extract_psf_roi.py` | **planned** | Explicit PSF ROI extraction from preserved raw HDF5 |
| `tasks/conversion/convert_raw_to_lcd_forward.py` | **planned** | Raw-to-LCD_forward conversion with profile metadata transfer |

## Policy

- Old tasks may be referenced for design patterns but must not be assumed correct.
- New tasks must use control-layer semantics unless a bypass is explicitly justified and documented.
- New tasks must preserve raw capture metadata.
- All new task code must be compatible with the current architecture rules in `AGENTS.md`.
