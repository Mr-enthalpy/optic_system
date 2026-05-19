# optic_system Phase 3 Release Data Contract

Canonical external artifact root:

- `D:/datasets/optic_system/phase3_release_20260520/`

## Views

This release has one canonical artifact root with two consumer views:

- `lcd_forward/`
- `thesis/`

Shared inputs and provenance live under:

- `common/`

## LCD_forward View

Path inside artifact:

- `lcd_forward/psf_dictionary/`

Files:

- `train.h5`
- `val.h5`
- `test.h5`
- `README.md`
- `psf_dictionary_summary.json`
- `psf_dictionary_manifest.json`
- `psf_dictionary_report.md`

Dataset contract:

- `masks`: `[N, 1, 1, 64, 64]`, `uint8`
- `psfs`: `[N, 1, 3, 512, 512]`, `float64`
- `wavelengths_nm`: `[3]`, `float64`
- `mask_id`: `[N]`
- `mask_family`: `[N]`
- `metadata_json`: scalar JSON string

Split sizes:

- train: `N = 136`
- val: `N = 17`
- test: `N = 17`

Wavelength order:

- `450.0`
- `550.0`
- `650.0`

Normalization:

- `background_subtract_then_sum_normalize`

ROI:

- `psf_roi_key_used = roi_512`
- Phase 3.4 stores ROI crops only.

Boundary:

- This view is for downstream data ingestion.
- It does not carry the Phase 3.0.5b to 3.3 thesis narrative.

## Thesis View

Path inside artifact:

- `thesis/`

Contents:

- `figures/`
- `metrics/`
- `reports/`
- `thesis_evidence_summary.md`

Boundary:

- This view supports thesis claims and figure assembly for Phase 3.0.5b to
  Phase 3.3.
- It excludes Phase 3.4 train / val / test exports.

## Common

Path inside artifact:

- `common/`

Contents:

- `docs/`: frozen docs and workflow narrative
- `provenance/raw_h5/`: audited raw HDF5 sources
- `provenance/plans/`: generated hardware plans used for audited runs
- `roi_context/`: PSF ROI candidates and dOTF ROI comparison context

## Verification

Run from the repository root:

```powershell
.venv\Scripts\python.exe scripts\verify_phase3_release.py D:\datasets\optic_system\phase3_release_20260520
```
