# Phase 3 External Handoff

This document defines the canonical external release layout.

There is one release artifact with two consumer views. Do not maintain
separate independent handoffs for `LCD_forward` and the thesis project.

## Canonical Release

Artifact root:

- `D:/datasets/optic_system/phase3_release_20260520/`

Git-tracked descriptor:

- `handoff/optic_system_phase3_release_20260520/`

Required top-level files:

- `RELEASE.json`
- `MANIFEST.json`
- `SHA256SUMS.txt`
- `common/`
- `lcd_forward/`
- `thesis/`

## Layout

```text
optic_system_phase3_release_YYYYMMDD/
├── RELEASE.json
├── MANIFEST.json
├── SHA256SUMS.txt
├── common/
│   ├── docs/
│   ├── provenance/
│   └── roi_context/
├── lcd_forward/
│   ├── psf_dictionary/
│   │   ├── train.h5
│   │   ├── val.h5
│   │   ├── test.h5
│   │   └── README.md
│   └── data_contract.md
└── thesis/
    ├── figures/
    ├── metrics/
    ├── reports/
    └── thesis_evidence_summary.md
```

## Common View

`common/` contains shared provenance used by both views:

- frozen docs and workflow notes
- audited raw HDF5 sources
- generated hardware plans
- ROI candidates and dOTF ROI comparison context

This prevents the thesis narrative and `LCD_forward` ingestion from silently
diverging on camera parameters, ROI choice, wavelength list, or source raw
files.

## LCD_forward View

`lcd_forward/` is data-interface-first.

It contains:

- Phase 3.4 measured PSF dictionary export
- compact metadata / provenance
- data contract

It excludes:

- Phase 3.0.5b to 3.3 thesis narrative
- ROI-choice debate beyond minimal provenance
- thesis figure discussion

## Thesis View

`thesis/` is narrative-first.

It contains:

- figures
- metrics
- reports
- evidence summary for Phase 3.0.5b through Phase 3.3

It excludes:

- Phase 3.4 `train.h5`, `val.h5`, `test.h5`
- Phase 3.4 PSF dictionary arrays intended for downstream modelling
- target-capture export data for reconstruction

## Git Boundary

Large data must not be committed to git.

Git should track only:

- release structure documentation
- `MANIFEST.json`
- `SHA256SUMS.txt`
- data contract docs
- small summary JSON / Markdown
- ingest / verify scripts

Actual `.h5`, large `.npy`, and bulk `.png` payloads belong in external
artifact storage:

- local lab storage: `D:/datasets/optic_system/phase3_release_YYYYMMDD/`
- compressed archive plus `SHA256SUMS.txt`
- GitHub Release asset / cloud drive / NAS / object storage / DVC remote

## Verification

Run:

```powershell
.venv\Scripts\python.exe scripts\verify_phase3_release.py D:\datasets\optic_system\phase3_release_20260520
```
