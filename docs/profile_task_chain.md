# Profile-Driven Calibration Task Chain

## Purpose

The profile chain creates hardware-calibration artifacts used by downstream
PSF, dOTF, and mask-family-driven capture tasks using externally-defined masks.

It is a profile-artifact chain, not a monolithic acquisition workflow. Each
stage should persist its result so downstream stages can restart from saved
artifacts instead of rerunning previous scans.

## Chain

```text
broadband pass-through camera calibration
  -> broadband LCD pupil scan
  -> PupilProfile
  -> selected-pupil-open per-band camera calibration
  -> downstream PSF / dOTF / mask-family-driven captures
```

## Artifact Boundaries

- The broadband `CameraProfile` is an input to broadband LCD pupil scanning.
- The `PupilProfile` is the selected LCD pupil geometry artifact.
- The per-band pupil-open `CameraProfile` is the exposure profile for
  PSF-producing tasks.
- Downstream tasks should load persisted profiles and should not require the
  earlier calibration stages to be rerun.

## Pass-Through Semantics

TLS zero-order broadband pass-through must be represented explicitly in capture
plans:

```yaml
wavelengths:
  - illumination:
      mode: broadband_passthrough
      tls_setpoint_nm: 0.0
      effective_wavelength_nm: null
    grating: 1
    settle_ms: 2000
```

Monochromatic entries must also use explicit illumination objects:

```yaml
wavelengths:
  - illumination:
      mode: monochromatic
      tls_setpoint_nm: 550.0
      effective_wavelength_nm: 550.0
    grating: 1
    settle_ms: 2000
```

Do not call `set_wavelength(0)` or `set_wavelength_nm(0)`. The `tls_c1`
high-level API exposes pass-through as a separate operation, and task code must
call `TLSService.set_pass_through()` / `tls_c1.set_pass_through()`.

Pass-through is a device-control mode, not a physical wavelength. Wavelength
labels without TLS are not equivalent to pass-through.

Real hardware tasks default to hardware runtime mode. Fake devices, missing
required hardware, diagnostic-only shortcuts, and test-settle overrides must be
explicit non-hardware/diagnostic choices. No-TLS positive wavelength labels are
allowed only in non-hardware contexts. TLS zero-order pass-through requires a
real TLS adapter in hardware mode.

## Exposure Search Policy

- Camera exposure calibration is gain-outer and exposure-binary-search inner.
- Configured gains are recorded, but execution sorts gain values ascending.
- For each completed gain, publish the maximum verified safe exposure.
- Exposure bounds combine camera capability and plan constraints. Hardware
  adapters should read camera-settable `SHUTTER` bounds through the camera API
  and convert them to `exposure_us`; the effective binary-search interval is
  the intersection with the plan/config interval. Plan/config bounds are a
  no-hardware fallback when the API is unavailable, and a deliberate search
  window constraint when the API is available.
- If the minimum exposure is unsafe at a higher gain, later higher gains may be
  skipped and the stop condition must be recorded.
- Only explicit lower-bound-unsafe failures may become a high-gain stop
  condition. Configuration errors, frame-shape errors, and backend exceptions
  must fail the task.
- Bad-pixel exclusion is represented by the valid-pixel mask. Saturation
  reports must still record full-burst all-pixel saturation diagnostics so
  excluded saturated pixels are auditable without changing the safety decision.
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
