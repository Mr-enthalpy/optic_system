# optic_system → thesis handoff: UPSTREAM U1/U2/U2b appendix figures

## Release location

```
D:\datasets\optic_system\phase3_release_20260520\thesis\figures\appendix\
```

The canonical release root is `D:\datasets\optic_system\phase3_release_20260520`.
MANIFEST.json, SHA256SUMS.txt, and RELEASE.json have been updated.

## New artifacts (10 files)

| File | Role | Thesis appendix |
|------|------|-----------------|
| `appendix_lcd_effective_pupil_annotated.pdf/.png` | U1 — LCD encoding region with boundary overlay, center, radius, scale | A.1 |
| `appendix_psf_roi_comparison.pdf/.png` | U2 — PSF ROI candidate overlay, linear [bg,peak] display + log inset | A.2 |
| `appendix_roi_energy_decomposition.pdf/.png` | U2b — support-domain enclosed energy vs ROI size; far-field threshold decomposition (noise-floor vs diffraction peaks) | A.2 |
| `appendix_calibration_summary.csv` | LCD pupil params, PSF centre, per-ROI energy coverage, per-wavelength exposure/gain | appendix table |
| `appendix_roi_energy_decomposition.csv` | support-domain enclosed fractions, far-field energy by threshold, metadata | appendix table |
| `thesis_optic_system_figures_manifest.json` | output manifest with coordinates, provenance, data source refs | reference |
| `README.md` | description, key conclusions, data provenance, regeneration command | reference |

## Key numbers for thesis text

| Item | Value | Source |
|------|-------|--------|
| LCD effective centre | (1065.25, 1871.54) px | appendix_calibration_summary.csv |
| LCD effective radius | 52.80 px | ↑ |
| Ellipse fit R² | 0.9992 | ↑ |
| PSF centre (camera) | (1149.13, 934.51) px | ↑ |
| Final modelling ROI | roi_512 | ↑ |
| roi_256 enclosed (full-frame denom) | 44.85% | ↑ |
| roi_512 enclosed (full-frame denom) | 50.02% | ↑ |
| roi_512 enclosed (r < 300 domain) | 97.37% | appendix_roi_energy_decomposition.csv |
| Noise-floor artifact in full-frame denom | 46.82 pp | ↑ |
| Genuine far-field diffraction (corr >= 0.5) | 5.73% of total | ↑ |
| Exposure 450 / 550 / 650 nm | 779.7 / 487.3 / 2241.6 µs | appendix_calibration_summary.csv |

## Data provenance

- PSF frame: `common/provenance/raw_h5/bishe_psf_roi.h5` (550 nm, 5 burst averages)
- Dark frame: `common/provenance/raw_h5/bishe_pupil_geometry.h5` / `references/dark_frame_avg`
- Background policy: 5th percentile of valid pixel domain (exclude top 1 row)
- ROI definitions: `common/roi_context/psf_roi/psf_roi.json`

## Important caveats for thesis text

1. U2 main panel uses **linear intensity** [bg, peak] — physically interpretable. The log inset is explicitly labeled "tail-enhanced, not energy-proportional".
2. The full-frame `roi_energy_fraction` denominator is dominated by noise-floor integration (4.8M px × ~0.02 counts). **Do not cite the 55% raw leakage without the noise-floor decomposition context.** The support-domain bounded values (r < 300: 97.4% enclosed) are the physically meaningful numbers.
3. roi_512 was selected as a **finite modelling support**, not as complete physical PSF support. The PSF has infinite diffraction wings but the modelling ROI does not need to capture them.
4. These are calibration-support appendix figures, not main scientific results. Chapter 3 main figures remain the responsibility of LCD_forward or the thesis workspace.

## Regenerate (no hardware required)

```bash
cd optic_system
python scripts/export_thesis_calibration_figures.py \
  --phase3-release D:/datasets/optic_system/phase3_release_20260520 \
  --out-dir outputs/thesis_figures \
  --format both --dpi 300 --force
```

## Copy PDFs to thesis assets

```bash
python scripts/export_thesis_calibration_figures.py \
  --copy-to-thesis-assets ../thesis_mono_lcd_nju/assets/figures
```

## Reference

- [GitHub issue #58](https://github.com/Mr-enthalpy/optic_system/issues/58) — full energy leakage diagnosis and far-field diffraction analysis
- `outputs/thesis_figures/README.md` — same content as in the appendix directory on D: drive
