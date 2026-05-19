# Phase 3 External Handoff

This document defines what should be handed off to the external thesis project
and to `LCD_forward`.

It is not an internal audit log for `optic_system`.

## Purpose

The handoff must include:

- the current mainline data products
- the current mainline interpretation around those data products
- the ROI decision context required for later modelling work

The handoff must not include:

- backup files
- contaminated files
- rollback history
- long narratives tied only to superseded or degraded runs

## Current handoff snapshot

The current external handoff should be assembled as a local export package,
for example under:

- `handoff/phase3_external_release_20260519/`

That package is intended to be copied outside `optic_system` as the current
Phase 3 baseline handoff. The package contents are export artifacts rather
than required repository-tracked source files.

## Included phases

Included now:

- Phase 3.0.5b
- Phase 3.1
- Phase 3.2a
- Phase 3.2b
- Phase 3.3

Not included yet:

- Phase 3.4 data products

Phase 3.4 remains intentionally empty in the package until the current
hardware run finishes and is analyzed.

## ROI decision context

The handoff must preserve both the selected result and the selectable context.

That means the package must carry:

- `outputs/psf_roi/psf_roi.json`
- the ROI preview images for `roi_256`, `roi_512`, `roi_768`, and `roi_1024`
- the multi-ROI dOTF comparison report and manifest
- the per-ROI dOTF output directories under `outputs/dotf/roi_*`

The handoff must not reduce the ROI story to only:

- `final_selected_roi_key = roi_512`

The external consumer must be able to inspect:

- which ROI candidates existed
- what each ROI candidate looked like in dOTF
- why `roi_512` was selected as the current modelling ROI

## Mainline interpretation to preserve

The external handoff should preserve these points:

- `global_safe_camera` is the active Phase 3 camera baseline
- `effective_pupil_window.json` is the active cleaned Phase 3.1 pupil window
- `roi_256` remains the audited Phase 3.2a baseline ROI
- `roi_512` is the current manually selected Phase 3.4 modelling ROI
- Phase 3.2b established that mask-induced PSF differences are much larger
  than repeat noise
- Phase 3.3 established that dOTF can be computed and visually compared across
  multiple ROI candidates from full-frame raw data

## Package boundary

This package is for external use.

It should stand on its own without requiring the reader to reconstruct the
history from chat context or from backup / contaminated files.
