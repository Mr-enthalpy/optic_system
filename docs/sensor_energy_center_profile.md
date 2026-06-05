# Sensor Energy Center Profile

`SensorEnergyCenterProfile` defines one global camera-sensor coordinate origin
for PSF support analysis and future peak-cluster modelling.

It is not a crop-window artifact. It does not define crop extraction, crop-size
selection, or training-ready PSF crops.

## Pipeline Position

```text
FullFramePSFSurvey
  -> SensorEnergyCenterProfile
  -> PeakSupportAnalysisReport
  -> SupportCandidateStabilityReport
  -> AdaptivePeakLayoutProfile
  -> AdaptivePeakClusterPSFDictionary
```

RawCapture HDF5 must be explicitly converted into `FullFramePSFSurvey` before
deriving this profile. Pre-mainline raw files must be migrated explicitly
before current measured-artifact analysis.

The profile records:

- `center_xy`: the single global sensor energy center used downstream.
- `coordinate_frame`: `sensor_full_frame` or `acquired_frame`.
- `camera_frame_extent`: the camera extent where the center is valid.
- per-entry center diagnostics.
- per-entry `background_value`, `total_corr_energy`, and fallback markers.
- per-wavelength diagnostic mean/std centers.

Per-wavelength centers are diagnostic only. They must not become separate
coordinate origins.

## Estimator

The mainline estimator is a pure full-frame energy-center function:

```text
bg = 5th percentile over valid pixels
corr = max(frame - bg, 0)
center_xy = weighted centroid of corr
```

If `total_corr_energy <= 0`, the estimator falls back to the valid-domain peak
pixel and records `per_entry_fallback_used: true` for that entry.

The implementation was audited from the bachelor-thesis energy-center logic,
but mainline absorbs only the center-location algorithm. It does not absorb the
thesis crop workflow or task naming.

Display-tail normalization, such as `p=0.99`, is not used for center
estimation.

## Valid Pixel Domain

Derivation may receive an explicit valid pixel domain or explicit valid pixel
mask. This prevents known bad pixels, obscured rows, or invalid camera regions
from biasing the global center.

The CLI accepts a JSON policy:

```bash
python scripts/derive_sensor_energy_center_profile.py survey.h5 center.json \
  --valid-pixel-domain-json '{"type":"exclude_top_rows","top_rows":16}'
```

The selected policy is recorded in `bg_policy.valid_pixel_domain`.

## Downstream Validation

Downstream tasks must reject a center profile when either field differs from
the data being analyzed:

- `coordinate_frame`
- `camera_frame_extent`

When `PeakSupportAnalysisReport` uses a center profile, its manifest records:

```json
{
  "radial_policy": {
    "center_policy": "sensor_energy_center_profile",
    "center_profile_id": "...",
    "center_xy": [0.0, 0.0]
  }
}
```

Component tables store absolute and center-relative coordinates:

```text
centroid_xy_abs
centroid_xy_rel
max_radius_from_energy_center
```

`PeakLayoutProfile` derivation may also load this profile and record:

```text
center_profile_id
energy_center_xy
center_xy_rel
```

The current PR treats the profile as optional but preferred for first-pass peak
layout derivation. Later adaptive peak-cluster tasks should make it required.
