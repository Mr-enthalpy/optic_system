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
- Bad-pixel exclusion is driven by the plan-level ``valid_pixel_domain``
  policy.  This is *valid-pixel-domain-aware calibration/analysis* — bad pixels
  are excluded from measurement decisions and provenance is recorded; it is not
  end-to-end bad-pixel correction of scientific PSF data (that is a later,
  dictionary-level concern).
- The policy vocabulary (``tasks/valid_pixel_domain.py``) supports ``full_frame``,
  ``exclude_top_rows`` (requires a positive ``top_rows``), and ``exclude_xyxy``
  (requires ``x1 > x0`` and ``y1 > y0``).  ``coerce_valid_pixel_domain`` performs
  strict frame-independent validation at plan-parse time: unknown ``type`` values,
  unknown fields, missing/zero ``top_rows``, and inverted/negative rectangles are
  rejected (fail-fast).
- The calibration entry points run a fail-fast preflight BEFORE any hardware
  state change (LCD mask display or TLS move): they re-canonicalize the policy
  (so directly-constructed plans are checked too), enforce policy/mask mutual
  exclusion, freeze the explicit mask, and pre-validate the explicit-mask domain
  (override channel, exclusion cap, zero-valid-pixel).  Only the ``exclude_xyxy``
  vs actual-frame bounds check is deferred until the first frame shape is known.
- At capture time the policy is resolved to a boolean mask; out-of-bounds
  rectangles are rejected (never silently clipped), the mask must leave at least
  one valid pixel, and the excluded fraction must not exceed
  ``MAX_EXCLUDED_FRACTION`` (default ``0.01``).  Excluding more requires an
  explicit ``large_exclusion_override: true`` together with a non-empty
  ``large_exclusion_reason``.  The override lifts only the fraction cap; it never
  relaxes coordinate validity, field completeness, or the "at least one valid
  pixel" rule.  When an explicit boolean ``valid_pixel_mask`` is supplied instead
  of a policy, it must be a 2D array with boolean dtype (values are not silently
  coerced from numeric/NaN data), the same cap applies, and over-cap exclusion
  requires ``explicit_mask_large_exclusion_override`` plus
  ``explicit_mask_large_exclusion_reason``.  These two parameters are threaded
  through the calibration and analysis entry points
  (``calibrate_broadband_camera_profile``,
  ``calibrate_per_band_pupil_open_camera_profile``,
  ``derive_sensor_energy_center_profile`` and the exposure-search helpers) so an
  explicit mask can still cover a large documented defect through an audited
  override.
- The calibration persists a **resolved-domain provenance record** in
  ``CameraProfile.extra["valid_pixel_domain"]`` (via ``describe_valid_pixel_domain``,
  which requires the frame shape).  The record includes ``resolved_policy``,
  ``frame_shape_hw``, ``valid_pixel_count``, ``excluded_pixel_count``,
  ``excluded_fraction``, ``max_excluded_fraction`` (the cap actually applied),
  ``mask_digest`` (a versioned sha256 of the resolved mask), and the override
  provenance.  The override is recorded as two distinct flags:
  ``large_exclusion_override_requested`` (the caller asked for it) and
  ``large_exclusion_override_applied`` (it was actually needed to pass the cap),
  so a defensive override on an in-cap policy never claims it was used.
  The resolved-domain object exposes ``resolved_policy`` / ``requested_policy``
  as read-only copies and freezes its mask, so provenance cannot drift after
  resolution.  The mask SHA-256 digest is computed only when a provenance record
  is produced (``resolve_valid_pixel_domain`` / ``describe_valid_pixel_domain``);
  the per-probe / per-frame ``resolve_valid_pixel_mask`` path skips it to avoid
  hashing native-sensor-sized masks repeatedly.
  ``analyze_diffraction_support`` records the same
  resolved record in ``PeakSupportAnalysisManifest.valid_pixel_domain`` so reports
  using different exclusions are distinguishable.
- Saturation reports must still record full-burst all-pixel saturation
  diagnostics so excluded saturated pixels are auditable without changing the
  safety decision.
- Peak provenance is disambiguated: ``peak_pixel`` remains the exposure-safety
  decision peak (valid domain) and new profiles set
  ``peak_pixel_domain="valid_pixel_domain"``.  ``full_frame_peak_pixel`` and
  ``full_frame_saturated_pixel_count`` record the unmasked full-burst statistics.
  These fields are optional (backward compatible) and appear on both the broadband
  ``CameraProfile`` and each ``PerWavelengthCameraSettings``.  They are parsed
  strictly on load: ``full_frame_peak_pixel`` must be a finite number (bool and
  string values are rejected, not coerced) and ``full_frame_saturated_pixel_count``
  must be a non-negative integer.  Backup safe-exposure
  candidates published in ``safe_profiles_by_gain`` /
  ``safe_profiles_by_wavelength`` carry the same ``peak_pixel_domain`` /
  ``full_frame_peak_pixel`` / ``full_frame_saturated_pixel_count`` provenance so
  every candidate in a profile is semantically unambiguous.  Every exposure
  search (broadband and each per-band wavelength) verifies that all of its probes
  share one camera frame shape via ``require_single_probe_frame_shape``; a
  mid-search shape change (unexpected ROI / pixel-format / stream reconfiguration)
  fails the calibration rather than mixing sensor domains.  Per-band calibration
  additionally verifies that the frame shape is identical across all wavelengths
  before recording the shared valid-domain provenance record.
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
