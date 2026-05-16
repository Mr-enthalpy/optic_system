# Old experiment audit

## Status

The old project under `old/` is legacy. It is **not** an active capture path.
None of its code may be imported, called, or revived in active code paths.

All active hardware access must go through current device/service boundaries.

GUI / interactive control path:

```text
GUI / CLI
  -> control command
  -> SessionController
  -> devices
  -> hardware or stream
```

Batch capture task path (Phase 2/3):

```text
capture plan
  -> capture_forward_dataset.py
  -> CaptureDeviceBundle
  -> devices / capture adapters
  -> hardware or stream
```

Old direct hardware wrappers under `old/` remain forbidden.

## Known old-project findings

These experimental facts were established under the old project and serve as
design priors for the new Phase 3 workflow.  They are **not** thesis evidence
because the old raw data is lost.

1. **PSF differences exceed repeat noise.**
   Different LCD masks produce measurably different PSF structures, and the
   difference between mask-conditioned PSFs is larger than the frame-to-frame
   repeat noise of the camera.  This justifies mask-switching experiments.

2. **Different masks produce stable PSFs.**
   Repeating the same mask exposure yields consistent PSF profiles, confirming
   that the optical system is deterministic enough for dictionary-style
   acquisition.

3. **dOTF shows clear pupil pixel / stripe structure.**
   The differential optical transfer function (dOTF) computed from a pair of
   PSFs (baseline mask vs. perturbed mask) reveals structure attributable to
   the LCD and/or pupil plane.

4. **dOTF full pupil stitching was not reliable.**
   Attempting to reconstruct a high-quality complex pupil by stitching dOTF
   results from multiple perturbation pairs was not reliable under the old
   experimental conditions.  This may reflect alignment drift, PSF size
   limitations, or insufficient SNR.

## Lost data

All old raw captures (`raw_image_*.npy`, `*.npy` profiles, etc.) are
unavailable.  No thesis figure, calibration parameter, or quantitative claim
may be based on old data.  Every result in the thesis must be reacquired using
the new Phase 2 capture stack (`tasks/capture_plan.py`,
`tasks/raw_capture_h5.py`, `tasks/capture_forward_dataset.py`) and traceable
to a specific `raw_capture.h5` file.

## Useful workflow ideas

The following workflow patterns observed in the old project are worth
reconstructing under the new framework:

| Old pattern | Source file | Description |
|---|---|---|
| Mask switching followed by frame averaging | `old/base.py:on_turn_clicked`, `old/base.py:on_capture_clicked` | Iterate over a set of LCD masks, capture N frames per mask, average. |
| Wavelength sweep over multiple mask states | `old/base.py:on_turn_clicked` (range 455-655 nm, step 10 nm) | Outer loop over wavelengths, inner loop over masks. |
| Effective LCD pupil region search | `old/calibrating.py:locate_aperture_and_build_roi` | X/Y bar-scan to locate the LCD region that actually projects onto the camera. Fits circle from energy-difference profiles. |
| Radius / angle scan for pupil ellipse estimation | `old/calibrating.py:scan_overlapping_area`, `old/calibrating.py:scan_rotation_angle` | Vary circular mask radius or rotated ellipse angle, measure camera energy flux to estimate effective pupil geometry. |
| dOTF edge perturbation experiment | `old/perturbation.py`, `old/roi.py:compute_dotf` | Base mask vs. base+perturbation mask -> PSF pair -> dOTF computation to reveal pupil-plane structure. |
| PSF ROI extraction from raw Bayer frames | `old/roi.py:find_max_energy_roi` | Energy-based ROI search on Bayer-demosaiced frames, crop ROI, pad for FFT. |
| dOTF visualization | `old/roi.py:show_complex_2d` | Magnitude/phase side-by-side display of complex dOTF. |

### Specific callouts

**`old/base.py`** is the earliest GUI-based capture loop.  It demonstrates a
useful experimental ordering (mask switch -> settle -> multi-frame capture ->
next mask) but is deeply coupled to legacy hardware paths (direct `Video`,
`LCDDisplay`, pywinauto TLS).  The ordering logic is informative; the code is
not reusable.

**`old/perturbation.py`** contains pure mathematical utilities (tapered
circular window, perturbation disk/gaussian, apply_perturbation) that are
hardware-agnostic.  These patterns may be re-implemented under the new
framework for dOTF mask generation, but the old code must not be imported
directly.

**`old/roi.py`** contains the core dOTF workflow (PSF-OTF, least-squares
scaling, dOTF computation, complex visualization) and ROI utilities.  The
algorithms are transferable but must be re-implemented against Phase 2 raw
capture data formats.

**`old/calibrating.py`** encodes a full effective-pupil-scan workflow:
bar-scan -> circle fit -> ellipse estimation -> angle scan.  The workflow
logic is informative but the implementation depends on old LCD/camera
classes (`LCDDisplay`, `Video`).

## Migration rule

Old code under `old/` may be inspected for experimental ordering and mask/task
ideas, but must not be imported, called, or revived in active code paths.

All thesis evidence must be reacquired under the current framework.  Old data
is lost.

## Forbidden migrations

The following must **never** be revived:

| Prohibited action | Reason |
|---|---|
| Import any module from `old/` | Old code bypasses `SessionController` and uses legacy hardware APIs. |
| Call `old/base.py` `CameraControlGUI` | Old tkinter GUI with direct hardware calls. Superseded by Phase 2 capture tasks. |
| Use `old/tls.py` `LegacyPywinautoTLS` | Pywinauto TLS automation is deprecated. Superseded by `devices/tls_service.py` wrapping `tls_c1` SDK. |
| Use `old/cam.py` `Video` class directly | Old zmq+shared-memory sidecar wrapper. Superseded by current camera service stack. |
| Use `old/lcd.py` `LCDDisplay` directly | Old pygame LCD wrapper without axis-aware subpixel model. Superseded by `devices/lcd_service.py`. |
| Run metadata-free capture loops | Old loops save `.npy` and `.png` with no capture plan, no HDF5 metadata, no provenance. |
| Use old data as thesis evidence | All old raw data is lost. |
| Call `old/aperture.py`, `old/cam_impl.py` | Hardware-level wrapper classes tied to old device model. |

## New-framework replacements

Each old workflow concept maps to a new Phase 3 workflow implemented against
the active capture stack:

| Old concept | New Phase 3 workflow | New implementation target |
|---|---|---|
| Mask switching + frame averaging | Capture plan masks with `camera.frames_per_capture` | `tasks/capture_plan.py` + `tasks/capture_forward_dataset.py` |
| Wavelength loop over masks | CapturePlan `wavelengths` field | `tasks/capture_forward_dataset.py` |
| Effective LCD pupil geometry calibration | Phase 3.1 bar-profile + radius-scan plan and analysis scripts | `plans/bishe_pupil_geometry.yaml`, `scripts/capture_pupil_geometry.py`, `scripts/analyze_pupil_geometry.py` |
| dOTF workflow (PSF-OTF-scaling-dOTF) | Phase 3.3 dOTF diagnostic | New dOTF analysis script consuming `raw_capture.h5` |
| ROI finding on captured frames | Preprocessing step before dOTF/PSF analysis | New preprocessing module |
| Perturbation mask generation | `scripts/make_smoke_masks.py` pattern extended for dOTF | New perturbation mask generator |

## Summary

The old project provides valuable experimental ordering knowledge and algorithm
patterns.  None of its code is reusable as-is.  Every capability must be
reconstructed against the current `SessionController -> devices` architecture,
producing `raw_capture.h5` with full provenance metadata.
