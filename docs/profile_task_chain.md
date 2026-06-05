# Profile-Driven Calibration Task Chain

## Purpose

The profile chain creates hardware-calibration artifacts used by downstream
PSF, dOTF, and mask-family capture tasks.

It is a profile-artifact chain, not a monolithic acquisition workflow. Each
stage should persist its result so downstream stages can restart from saved
artifacts instead of rerunning previous scans.

## Chain

```text
broadband pass-through camera calibration
  -> broadband LCD pupil scan
  -> PupilProfile
  -> selected-pupil-open per-band camera calibration
  -> downstream PSF / dOTF / mask-family captures
```

## Artifact Boundaries

- The broadband `CameraProfile` is an input to broadband LCD pupil scanning.
- The `PupilProfile` is the selected LCD pupil geometry artifact.
- The per-band pupil-open `CameraProfile` is the exposure profile for
  PSF-producing tasks.
- Downstream tasks should load persisted profiles and should not require the
  earlier calibration stages to be rerun.

## Pass-Through Semantics

`wavelength_nm: 0.0` remains a compatibility encoding for TLS zero-order
broadband pass-through in capture plans. Task internals should normalize it to
`tasks.illumination.IlluminationSpec(mode="broadband_passthrough")`.

Do not call `set_wavelength(0)` or `set_wavelength_nm(0)`. The `tls_c1`
high-level API exposes pass-through as a separate operation, and task code must
call `TLSService.set_pass_through()` / `tls_c1.set_pass_through()`.

Pass-through is a device-control mode, not a physical wavelength. Wavelength
labels without TLS are not equivalent to pass-through.

## Exposure Search Policy

- Camera exposure calibration is gain-outer and exposure-binary-search inner.
- Configured gains are recorded, but execution sorts gain values ascending.
- For each completed gain, publish the maximum verified safe exposure.
- `max_exposure_us` is a hard no-extrapolation bound and should come from the
  camera API's real shutter upper limit in hardware plans.
- If the minimum exposure is unsafe at a higher gain, later higher gains may be
  skipped and the stop condition must be recorded.
- Only explicit lower-bound-unsafe failures may become a high-gain stop
  condition. Configuration errors, frame-shape errors, and backend exceptions
  must fail the task.
- The default selected profile should prefer low gain, then stronger signal,
  then longer exposure.

## Timing Policy

- After an LCD mask update, real hardware must wait at least 20 ms before
  capture or the next hardware-dependent action.
- Below-refresh LCD settle values are allowed only through explicit no-hardware
  test overrides.
- After an exposure or gain change, discard more than 40 frames before using
  frames for measurement. The current conservative default is 80 frames.

## Loop Ordering

For selected-pupil-open per-band calibration, TLS wavelength is the outer loop.
Move TLS once per wavelength, then perform all camera exposure/gain probes for
that wavelength before moving TLS again.

Any later task that uses TLS hardware should order loops to minimize TLS
motion. Spectrometer movement is slow and expensive compared with camera
parameter changes.

## Profile Validity

- The broadband `CameraProfile` is valid only for pupil scanning.
- PSF-producing tasks require a `PupilProfile` and a per-band pupil-open
  `CameraProfile`.
- Full-LCD-open per-band profiles must not be used as PSF-producing capture
  profiles.
- Broadband LCD pupil scanning uses TLS pass-through / broadband light, not a
  selected monochromatic wavelength.
- `PupilScanPlan.scan_range_xyxy` uses conventional physical LCD coordinate
  order: `x0, y0, x1, y1`.
- The broadband pupil scan uses bar profiles, then radius scan, then
  ellipse/circle overlap fitting. The effective circular pupil radius is based
  on the fitted ellipse semi-minor axis.

## Thesis Branch Relation

The bachelor-thesis branch may be audited for algorithms.

Mainline should absorb verified algorithms and reusable abstractions, not
thesis phase numbering, thesis workflow ordering, ROI-centered data contracts,
or thesis-specific reconstruction / figure scripts.
