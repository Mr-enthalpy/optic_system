# Tasks directory

## Important notice

The files under `tasks/` originate from early experimental directions and **must not be assumed to define the current architecture.**

This directory currently contains placeholder stubs with no implementation. New minimal capture tasks will be implemented separately in **Phase 2.**

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

## Current task files

| File | Status | Notes |
|------|--------|-------|
| `aperture_search_task.py` | **legacy** | Empty stub. Original intent was aperture parameter search. Has not been audited against current architecture. Bypasses `SessionController` by design assumption. |
| `calibration_sequence_task.py` | **legacy** | Empty stub. Original intent was calibration sequence automation. Has not been audited. Full calibration workflow is Phase 4 scope. |
| `capture_average_task.py` | **legacy** | Empty stub. Original intent was simple frame averaging. Has not been audited. Will be superseded by Phase 2 minimal capture task. |
| `wavelength_sweep_task.py` | **legacy** | Empty stub. Original intent was wavelength sweep orchestration. Has not been audited. Full wavelength sweep is outside current phase scope. |

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

## Policy

- Old tasks should not be deleted without explicit authorization.
- Old tasks may be referenced for design patterns but must not be assumed correct.
- New tasks must use control-layer semantics unless a bypass is explicitly justified and documented.
- New tasks must preserve raw capture metadata.
- All new task code must be compatible with the current architecture rules in `AGENTS.md`.
