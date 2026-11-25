# Task pipelines: LCD prototype and real UDC

The 2026-11-25 user request authorizes a real-UDC task branch alongside the existing LCD-camera prototype. Physical acquisition belongs here; reconstruction and task learning remain in the reconstruction repository. Share code only where the operations have the same actual meaning.

```text
LCD laboratory
  broadband CameraProfile -> LCD pupil scan -> PupilProfile
  -> per-band pupil-open CameraProfile -> synchronized LCD/camera/TLS capture
  -> RawCapture v3 -> FullFramePSFSurvey v1 -> sensor center / dense PSF ROI

Real UDC / current Camera2 phone
  arrange ONE physical condition + record source/geometry/display description
  -> UDCCapturePlan + current operator confirmation
  -> Camera2 RAW batches -> selected native run export -> integrity/control checks
  -> original-DN decoding -> acquired-frame mean / explicit ROI / diagnostics

Shared: array ROI geometry, energy-center estimator, file-only run-status monitor
```

## Retain, replace, reuse

| Module / concept | LCD laboratory | Real UDC |
|---|---|---|
| Task plan / runner | Existing mask/wavelength CapturePlan and device bundle retained | `tasks/udc/plan.py`, `capture.py`; no dummy LCD device |
| Camera transport | FlyCapture2 sidecar, ZMQ/shared memory, private stream | Existing Android Camera2 app + ADB control in `tools/android_raw_probe/` |
| Exposure calibration | CameraProfile, exposure/gain-dB dependencies | Requested/readback ns and ISO; do not borrow gain decomposition or calibration values |
| Pupil scan / pupil-open profile | Required physical LCD calibration | Not applicable to the integral phone assembly; no synthetic PupilProfile |
| Display | Physical display identity, mono subpixel masks, RGB boundary packing | Fixed probe-app viewport and brightness/vendor-state limitations; no LCD packing or dynamic coding |
| Illumination | Existing TLS movement/pass-through services retained | Current task records externally arranged source; no automatic TLS commands |
| Native observations | RawCapture v3 with physical LCD/TLS records | Camera2 JSON, planes, request/result, DNG/companions; no relabelling as RawCapture v3 |
| Survey / center profile | Existing full-sensor/Profile checks retained | Not reused as a file format without the required physical evidence |
| ROI / centroid algorithms | Shared `tasks/measurement/roi.py`, `energy_center.py` | Same code, explicitly labelled acquired-image coordinates |
| Completion / monitor | Existing RunStatusPublisher/Reader | Same file-only publisher/reader; LCD/TLS files are optional |

Historical `tasks.psf` imports expose the same ROI/center operations, whose implementations now live in the shared measurement layer. No historical schema/file migration is required. The phone task imports without HDF5, vendor SDKs, pygame or LCD initialization.

## Phone plan and physical conditions

`UDCCapturePlan` is a small local contract for the existing Camera2 tool, not a universal backend registry. A JSON plan contains `schema_version=1`, `plan_id`, one `condition` (`unspecified`, `dark`, `flat`, `point`), `source_description`, `display_state`, ISO/RAW/companion selection, and an ordered `batches` list of exposure-us/frame-count pairs. Repeated references are retained in order.

Source and display descriptions are annotations, not commands. Actual app display/brightness records remain authoritative. Geometry uses `source_distance_mm` and `aperture_diameter_um`; `bandwidth_nm` can remain null when unknown. These are operator observations, not independent certification.

Point plans require positive finite wavelength, distance and aperture diameter. Reused `IlluminationSpec(mode="monochromatic", effective_wavelength_nm=..., tls_setpoint_nm=None)` describes an external source. Dark observations have no incident wavelength. A broadband/unknown flat source can set `illumination=null` and describe its source; it must not be disguised as TLS zero-order pass-through.

Every physical hardware invocation requires `--condition-verified`. Confirmation is not a reusable permission stored in the plan. Changing dark/flat/point conditions, source position or wavelength requires a new arrangement/plan and a fresh confirmation. The runner cannot switch an optical scene by changing a JSON label.

## Run and inspect

Use a member-local environment with `requirements-udc.txt`; add pytest for development tests. Keep machine paths in private configuration.

```powershell
.\.venv\Scripts\python.exe scripts/capture_udc.py `
  --plan plans/udc/dark_example.json --validate-only

# Only after physically arranging and confirming the actual condition.
.\.venv\Scripts\python.exe scripts/capture_udc.py --hardware `
  --plan local_config/actual_udc_plan.json --adb $adb `
  --output $new_output_directory --condition-verified
```

Validation-only opens no devices and creates no observation files; it is not simulated hardware success. The committed dark plan is an example, not evidence of occlusion. The LCD entry remains `scripts/capture_dataset.py --hardware|--dry-run` with existing profile requirements.

Each UDC batch uses the existing application, waits for a terminal record and checks requested format/controls/condition/counts. It exports only that native run, checks/decodes native plane layout and manual readbacks. RAW_SENSOR DNGs declared saved must exist and be nonempty; this is not pixelwise DNG equivalence validation.

Outputs are `plan.json`, `batch_NNN/export/`, `batch_NNN/decoded/`, and file-only monitor state under `status/`. `status/pipeline.json` distinguishes capture completeness, phone run success, export completeness and decode completeness. An export/decode error does not erase completed phone frames. A DNG error may be capture-complete but unsuccessful. Partial archives/failed native runs remain; diagnostic export is attempted for known failed phone run IDs. Acquisition is not automatically retried, and existing outputs are rejected.

Pipeline success means scheduled captures, files and requested control checks completed. It does not certify unsaturated illumination, flatness, radiometric linearity or diffraction. Saturation fractions and metadata warnings remain visible without selecting away frames.

## Shared algorithms, separate coordinates and evidence

```python
from tasks.udc.measurement import load_udc_measurement
from tasks.measurement.roi import ROI

measurement = load_udc_measurement(exported_native_run, roi=ROI(100, 80, 256, 256))
mean_dn = measurement.mean_dn  # arithmetic mean of every validated native frame
diagnostic = measurement.energy_center(bg_percentile=5)
```

Native observations remain untouched. Half-open ROI coordinates belong to `acquired_frame`; metadata retain full image shape, ROI `(x,y,width,height)`, parity origin, declared CFA/black/white levels and lens-shading status. Physical sensor origin/CFA alignment remain unverified; parity tiles are not calibrated RGB. The center estimator reports background, corrected energy, fallback and acquired-frame coordinates; it does not create the LCD's SensorEnergyCenterProfile.

A point-source label alone does not establish a PSF, wavelength dependence or useful spectral discrimination. Independent source/geometry/repeat evidence is still required. Historical physical/reconstruction negative results and separately frozen reconstruction protocols are unaffected.

The [2026-11-25 dark acceptance](reports/2026-11-25-udc-pipeline-dark-acceptance.md) records the first physical capture/export/decode on this phone task. The subsequent [400–700 nm camera-parameter experiment](reports/2026-11-25-udc-band-camera-parameters.md) records the separately authorized finite TLS sweep, all-pixel peak safety, a 14 µs search start for the resumed bands and independent native-file checks. These dated observations retain their actual acquisition commits and setup limits; they do not establish radiometric calibration or wavelength mechanisms.
