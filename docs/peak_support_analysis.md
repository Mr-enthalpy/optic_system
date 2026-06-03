# Peak Support Analysis

`PeakSupportAnalysisReport` is a diagnostic bridge between
`FullFramePSFSurvey` and later `PeakLayoutProfile` refinement.

The purpose is not to preserve apparent full-frame energy. The purpose is to
identify significant, stable diffraction support that should enter the
peak-patch representation.

The existing production flow remains:

```text
FullFramePSFSurvey
  -> PeakLayoutProfile
  -> PeakPatchPSFDictionary
  -> LCD_forward-readable peak-patch export
```

Support analysis consumes the survey and writes a separate report. It does not
modify `PeakLayoutProfile` and does not run `LCD_forward` validation. The
formal mainline input is `FullFramePSFSurvey`; the CLI and loader expose an
explicit raw-HDF5 fallback only as a legacy/development convenience.

The report preserves the survey coordinate semantics. If the survey
`camera_frame_extent.mode` is `full_sensor`, coordinates are reported as
`sensor_full_frame`. Otherwise they are reported as `acquired_frame`. The
`camera_frame_extent` record is stored with the manifest so downstream layout
refinement can interpret component boxes unambiguously.

## Signal Policy

For every full-frame PSF entry:

```text
bg   = 5th percentile over valid pixels
corr = max(psf - bg, 0)
```

The full-frame cumulative energy is not a support-selection criterion because
large weak backgrounds can dominate the denominator. The analysis separates
far-field content into:

```text
corr < tau:
    noise-floor integral / pseudo leakage

corr >= tau:
    significant diffraction component / candidate peak support
```

Only significant components should drive later peak-support preservation.

`p=0.99` percentile normalization is visualization-only. It is not used by the
support-selection algorithm.

## Default Parameters

```text
tau_values       = [0.1, 0.5, 1.0, 2.0, 5.0]
support_radii    = [200, 300, 500]
far_field_radius = 200
bg_percentile    = 5
connectivity     = 8
center_policy    = frame_center
```

`min_component_area=1` is appropriate for synthetic tests and small diagnostic
images because it preserves every connected component. It is not a good
real-data preset for 2048 x 2448 full-frame camera data. Real full-frame
surveys should use an explicit real-data preset that filters tiny connected
components before writing the candidate table.

## HDF5 Layout

```text
/support_analysis/tau_values
/support_analysis/support_radii
/support_analysis/frame_shape
/support_analysis/background_value
/support_analysis/center_xy
/support_analysis/total_corr_energy
/support_analysis/compact_support_energy
/support_analysis/compact_support_fraction
/support_analysis/far_field_noise_energy
/support_analysis/far_field_significant_energy
/support_analysis/far_field_noise_pixel_count
/support_analysis/far_field_significant_pixel_count

/components/entry_index
/components/tau
/components/component_id
/components/bbox_xyxy
/components/centroid_xy
/components/area
/components/energy
/components/peak_value
/components/mean_value
/components/max_radius
/components/is_far_field
/components/mask_id
/components/wavelength_nm

/metadata/manifest_json
/source/survey_h5
```

`far_field_noise_pixel_count` is the far-field threshold-complement count:
pixels with `radius >= far_field_radius` and `corr < tau`. It includes zero
corrected-intensity pixels and should not be read as the count of nonzero
noise-carrying pixels.

Component IDs are local to one `(entry_index, tau)` pair. Candidate supports
therefore carry `source_component_keys` with `(entry_index, tau, component_id)`
for traceability across entries and threshold levels.

Candidate patch proposals are clipped to the report frame. If a snapped patch
size would exceed the frame, it is reduced to the frame size and its origin is
clamped so `patch_origin_xy + patch_shape_hw` remains in bounds.

Candidate support proposals are named `candidate_supports` conceptually and
are not a final `PeakLayoutProfile`. Helper output records include
`not_a_peak_layout_profile: true`; support candidates must pass cross-mask,
cross-wavelength, and repeat stability audit before any later PR promotes them
into layout refinement.

## CLI

```bash
python scripts/analyze_diffraction_support.py survey.h5 support_analysis.h5
```

The report is no-hardware and read-only with respect to the source survey.
Use `--allow-raw-fallback` only for legacy or development diagnostics that do
not yet have a `FullFramePSFSurvey` artifact.

## Real-Data Lessons

Issue #62 records the first mainline trial on real Phase 3 full-frame PSF data.
The useful conclusions are general rules for the mainline pipeline:

- The formal input remains `FullFramePSFSurvey`. Raw HDF5 fallback is useful for
  legacy diagnostics, but it should not become the production boundary.
- Synthetic defaults should not be treated as real-data defaults. In
  2048 x 2448 full-frame data, `min_component_area=1` can generate very large
  component tables dominated by tiny noise components. Real-data presets should
  set a practical component-area threshold and record it in the manifest.
- Large survey-scale files need streaming support. Energy decomposition can be
  computed entry by entry without loading all frames into memory.
- Large survey-scale files may also need energy-only mode. This mode computes
  background, corrected energy, compact support fractions, and far-field
  noise/significant splits while intentionally skipping the component table.
- A component table is not a layout. Connected components are raw support
  evidence and must pass repeat, wavelength, and mask stability audit before
  they can become layout inputs.
- `p=0.99` display-tail normalization remains visualization-only, even when it
  is useful for inspecting real far-field diffraction patterns.

The next artifact after `PeakSupportAnalysisReport` should be a stability audit
report, not an immediate replacement of `PeakLayoutProfile`.

```text
PeakSupportAnalysisReport
  -> SupportCandidateStabilityReport
  -> AdaptivePeakLayoutProfile
  -> AdaptivePeakClusterPSFDictionary
```

The stability audit should aggregate components across repeat, wavelength, and
mask; estimate centroid stability, energy stability, and hit rate; merge
consistent components into support candidates; and reject noise-floor or
unstable components.
