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
| `tasks/profiles/pupil_profile.py` | **active** | Effective LCD pupil profile artifact |
| `tasks/profiles/camera_profile.py` | **active** | Camera safety profile artifact |
| `tasks/profiles/calibrate_broadband_camera_profile.py` | **active** | Broadband pass-through exposure calibration for LCD pupil scanning |
| `tasks/profiles/scan_pupil_broadband.py` | **active** | Broadband pass-through LCD pupil scan and `PupilProfile` generation |
| `tasks/profiles/calibrate_per_band_pupil_open_camera_profile.py` | **active** | Per-band exposure calibration under selected-pupil-open LCD state |
| `tasks/psf/profile_requirements.py` | **active** | Explicit profile dependency validation for broadband pupil scan and PSF-producing tasks |
| `tasks/psf/build_full_frame_psf_survey.py` | **active** | Small full-frame scout artifact for peak layout discovery |
| `tasks/psf/derive_peak_layout_profile.py` | **active** | Peak layout profile derivation from scout survey data |
| `tasks/psf/build_peak_patch_psf_dictionary.py` | **active** | Profile-dependent peak-patch PSF dictionary builder |
| `tasks/psf/export_peak_patch_dictionary_to_lcd_forward.py` | **active** | Peak-patch dictionary export with LCD_forward-readable metadata |
| `tasks/psf/compact_dense_export.py` | **active** | Diagnostic dense canvas rendering from peak patches and recorded coordinates |
| `tasks/psf/capture_psf_dictionary.py` | **planned** | Profile-dependent PSF dictionary capture |
| `tasks/psf/capture_dotf_dataset.py` | **planned** | Profile-dependent dOTF diagnostic capture |
| `tasks/psf/capture_mask_family_psf.py` | **planned** | Profile-dependent mask-family PSF capture |
| `tasks/diagnostics/compute_h_matrix_diagnostic.py` | **planned** | H-matrix diagnostic export for measured PSF dictionaries |
| `tasks/conversion/convert_raw_to_lcd_forward.py` | **planned** | Raw-to-LCD_forward conversion with profile metadata transfer |

Mainline profile dependency chain:

```text
calibrate_broadband_camera_profile
  -> CameraProfile(profile_family=broadband_passthrough,
                   valid_for=pupil_scan_broadband)
  -> scan_pupil_broadband
  -> PupilProfile
  -> calibrate_per_band_pupil_open_camera_profile
  -> CameraProfile(profile_family=per_band_pupil_open,
                   depends_on_pupil_profile_id=...)
  -> downstream PSF / dOTF / mask-family capture tasks
```

This deliberately differs from the bachelor-thesis branch workflow. Mainline
does not use full-LCD-open per-band exposure profiles for downstream
PSF-producing tasks. Pupil scanning uses TLS zero-order pass-through
(`wavelength_nm: 0.0` in capture plans, implemented through
`TLSService.set_pass_through()` / `tls_c1.set_pass_through()`), not a selected
monochromatic wavelength.

## Policy

- Old tasks may be referenced for design patterns but must not be assumed correct.
- New tasks must use control-layer semantics unless a bypass is explicitly justified and documented.
- New tasks must preserve raw capture metadata.
- All new task code must be compatible with the current architecture rules in `AGENTS.md`.
