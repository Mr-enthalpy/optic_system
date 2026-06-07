# optic_system

`optic_system` is the hardware-control and synchronized-capture frontend for the mono-LCD programmable diffractive imaging project.

It is not a neural-network training repository, mask-family design repository,
or reconstruction repository.

The intended long-term system is split across four repositories:

```text
lcd_mask_families
  -> mask family definitions
  -> mask instance and sequence specs
  -> physical mask generation, quantization, and projection rules
  -> shared mask identity and versioning

optic_system
  -> hardware control, visualization, and synchronized acquisition
  -> raw capture preservation
  -> profile-aware measured artifacts
  -> full-frame surveys, support/stability/layout diagnostics
  -> measured evidence handoff publication

LCD_forward
  -> LCD mask-to-peak-cluster/operator modelling
  -> LCD-to-measured-response surrogate learning
  -> mask-family evaluation, parameter selection, and operator-aware mask-sequence design from measured evidence
  -> peak-cluster operator package generation
  -> H-matrix/operator diagnostics

reconstruction
  -> inverse-problem solving
  -> forward/adjoint consumption
  -> reconstruction pipelines and learned reconstruction
  -> task-level evaluation
  -> reconstruction-driven capture-plan proposals
```

See [`docs/cross_repository_boundary.md`](docs/cross_repository_boundary.md)
for the normative handoff boundary. The handoff categories and directories in
this repository are placeholders, not implemented cross-repository APIs.

## Current project role

`optic_system` is being reorganized from an early camera/LCD GUI prototype into a hardware-facing experimental frontend.

The active responsibilities are:

* camera service management
* camera frame streaming through sidecar / shared memory
* live preview
* mono LCD physical mask display
* TLS wavelength-control backend through `tls_c1`
* control-layer command / event / state management
* minimal synchronized acquisition tasks
* raw capture HDF5 export
* profile artifacts
* `FullFramePSFSurvey`
* `SensorEnergyCenterProfile`
* `PeakSupportAnalysisReport`
* `PeakPatchPSFDictionary`
* future `AdaptivePeakClusterPSFDictionary`
* metadata-rich diagnostic exports
* measured evidence and measured response handoff publication

The repository should stay focused on hardware control and data acquisition.
Mask-family design belongs to `lcd_mask_families`; LCD response/operator
modelling belongs to `LCD_forward`; reconstruction pipelines belong to
`reconstruction`.
The long-term scientific route is summarized in
[`docs/research_idea.md`](docs/research_idea.md); in this repository, that route
stops at measured artifacts, profile-aware raw captures, metadata, and
diagnostics.

## System components

### Entry-point architecture

There are three different user-facing entry points. They are intentionally not
the same process.

```text
Manual/debug control:
  python -m app.main_gui
  -> SessionController
  -> device services
  -> camera sidecar / LCD / TLS

Experimental capture:
  python scripts/capture_forward_dataset.py ...
  -> capture task
  -> camera sidecar frame stream + LCDService + optional TLSService
  -> raw HDF5 + run-status files

Read-only monitoring:
  python scripts/monitor_run_status.py --status-dir ...
  -> polls task-published files
  -> displays task progress, LCD/TLS state, latest frame preview, frame stats, logs
```

The key distinction is the camera image path. For experimental image data,
opening the camera for acquisition means running a capture task. The capture
task consumes the camera frame stream and publishes preview/stat/log files into
the run-status directory. The monitor reads those files; it does not open the
camera, subscribe to the shared-memory stream, or control exposure/gain.

Richer live monitor output should therefore be added at the publishing task:
call `RunStatusPublisher.write_frame_preview(...)`,
`RunStatusPublisher.write_frame_stats(...)`, and
`RunStatusPublisher.append_log(...)`. The monitor remains hardware-free and
task-agnostic.

### Camera

The camera path is based on a sidecar process wrapping the legacy camera SDK environment.

The intended camera path is:

```text
CameraServiceClient
  -> camera sidecar RPC
  -> camera SDK / pyflycap runtime
  -> shared memory frame stream
  -> FrameStreamClient
  -> PreviewWorker
  -> GUI / capture task
```

The sidecar boundary exists because the camera SDK stack may require a different Python/runtime environment than the main application.

### LCD

The LCD is treated as a physical mono subpixel array, not as a semantic RGB display.

The physical mono mask convention depends on the configured ``subpixel_axis``:

```text
subpixel_axis=0:  mono mask [3H, W]  →  display RGB buffer [H, W, 3]
subpixel_axis=1:  mono mask [H, 3W]  →  display RGB buffer [H, W, 3]
```

The software does **not** infer which display is the target mono LCD.
The user must select the display index and subpixel axis.
The software only checks internal consistency and records metadata.

Configure via CLI or environment:

```bash
--lcd-display-index <index>  --lcd-subpixel-axis 0|1
```

Environment fallback: ``OPTIC_SYSTEM_LCD_DISPLAY_INDEX``, ``OPTIC_SYSTEM_LCD_SUBPIXEL_AXIS``.

Only the LCD device boundary should pack the physical mono representation into RGB display buffers.

### TLS

TLS wavelength control uses the `tls_c1` SDK wrapper.

The old pywinauto-based GUI automation path is deprecated and should not be used for new code.

The intended TLS path is:

```text
GUI / task intent
  -> control command
  -> SessionController
  -> TLSService
  -> tls_c1 high-level API
  -> vendor SDK
```

`tls_c1` is the only active TLS backend.

Hardware tests for TLS must remain opt-in.
The default test suite should run without TLS hardware and without vendor DLLs.

Capture plans must represent illumination explicitly. TLS zero-order broadband
pass-through uses `illumination.mode: broadband_passthrough`; `wavelength_nm:
0.0` is not a supported capture-plan input. Task internals consume
`IlluminationSpec`. Pass-through is a device-control mode, not a physical
wavelength, and task code must call `TLSService.set_pass_through()`.

Runtime mode is explicit for capture, profile, and diagnostic task entry
points. Real hardware tasks default to hardware runtime mode. Fake devices,
missing required hardware, diagnostic-only shortcuts, and test-settle overrides
must be explicit non-hardware/diagnostic choices. No-TLS positive wavelength
labels are allowed only in non-hardware contexts. TLS zero-order pass-through
requires a real TLS adapter in hardware mode.

## Cross-Repository Boundary

`optic_system` is the hardware-control, visualization, synchronized-capture,
and measured-artifact frontend.

It does not train forward surrogates, own mask-family design, or own
reconstruction pipelines.

Allowed future inputs:

```text
lcd_mask_families
  -> mask specs, explicit physical masks, or mask sequence specs

LCD_forward or reconstruction
  -> capture-plan handoff requests
```

Allowed future outputs:

```text
optic_system
  -> measured evidence handoffs for LCD_forward
  -> measured response / target-capture handoffs for reconstruction
```

The `LCD_forward` relationship is narrowed to measured-evidence consumption
and capture-plan feedback. `LCD_forward` owns mask-to-operator surrogate
modelling, measured-response learning, mask-family evaluation, parameter
selection, operator-aware mask-sequence design from measured evidence, and
operator package publication.

The `reconstruction` relationship is a future measured-response consumer and
future capture-plan proposer. Reconstruction pipelines, learned reconstruction,
inverse-problem evaluation, and forward/adjoint consumption live there.

The `lcd_mask_families` relationship is a future shared source of mask
family/spec identity. `optic_system` may consume mask specs or explicit masks
from it once that repository exists, but this repository must not implement
that external package or invent final schemas for it.

## Development roadmap

The active roadmap is maintained in [`docs/roadmap.md`](docs/roadmap.md).

Current active tracks:

- hardware validation of the profile-driven calibration chain
- real-data operationalization and support stability audit

Completed baseline:

- minimal hardware capture layer
- full-frame scout -> peak layout -> fixed-size peak-patch dictionary data contract
- first-pass diffraction support analysis report

Completed implementation details and hardware-smoke evidence are preserved in
[`docs/completed_phases.md`](docs/completed_phases.md).

The current mainline data-artifact paths are:

```text
Broadband pass-through CameraProfile
  -> broadband LCD pupil scan
  -> PupilProfile
  -> selected-pupil-open per-band CameraProfile
  -> downstream PSF / dOTF captures, and mask-family-driven captures using externally-defined masks

FullFramePSFSurvey
  -> first-pass PeakLayoutProfile
  -> fixed-size PeakPatchPSFDictionary

FullFramePSFSurvey
  -> SensorEnergyCenterProfile
  -> PeakSupportAnalysisReport
  -> future SupportCandidateStabilityReport
  -> future AdaptivePeakLayoutProfile
  -> future AdaptivePeakClusterPSFDictionary
```

Measured-artifact analysis tasks consume `FullFramePSFSurvey`. RawCapture HDF5
must be explicitly converted into `FullFramePSFSurvey` before sensor-center,
support, or layout analysis. Pre-mainline raw files must be migrated explicitly
before current measured-artifact analysis.

The profile-driven calibration chain is documented in
[`docs/profile_task_chain.md`](docs/profile_task_chain.md). Each stage
persists an artifact and downstream tasks should restart from saved artifacts.

`SensorEnergyCenterProfile` records one global sensor energy center as a camera
coordinate origin for support analysis and future peak-cluster modelling. It is
not a crop-window artifact and must not introduce crop extraction, crop-size
selection, or crop-centered exports. The profile also records per-entry
background, corrected energy, and fallback diagnostics, and may be derived with
an explicit valid pixel domain when known invalid sensor regions must be
excluded.

The fixed-size `PeakPatchPSFDictionary` is a v1 compatibility baseline. The
medium-term representation target is an adaptive peak-cluster dictionary where
each real diffraction peak cluster has its own support, coordinates, and local
raw data.

Training the peak-cluster forward/operator model is deferred to `LCD_forward`.
Multi-frame reconstruction is deferred to `reconstruction`. Differentiable
mask-family or `GenerMask` design belongs to the cross-repository loop between
`lcd_mask_families`, `LCD_forward`, and `reconstruction`, not to
`optic_system`.

### Raw capture HDF5 schema

Canonical raw schema lives in [`docs/raw_capture_schema.md`](docs/raw_capture_schema.md).

Summary:

- `/raw/frames_avg` — `[N_capture, H, W]` per-capture averaged frames
- `/raw/frames` — burst frames (only if `store_burst=True`)
- `/masks/` — physical mask arrays + `mask_id` / family metadata
- `/illumination/` — `illumination_json`, `tls_setpoint_nm`, `effective_wavelength_nm`
- `/camera/` — `requested_*` / `readback_*` exposure and gain, `frame_extent_json`
- `/tls/` — device status (`grating`, `settle_ms`, `timestamp_ns`, `status_json`)
- `/capture/` — index tables, `plan_json`, `runtime_policy_json`, `processing_flags_json`
- `/profiles/` — `requirements_json`, `pupil_profile_id`, `camera_profile_id`

Raw capture metadata should use camera frame extent terminology.
`/camera/frame_extent_json` is the raw HDF5 field. Capture plans must use
`camera.frame_extent` for acquisition extent metadata. Pre-mainline
thesis/development data are outside the current schema and require explicit
migration if needed.

Raw capture files use neutral schema terminology: `software_version` is
`optic_system`, `raw_capture_schema_version` records raw schema evolution, and
`capture_role` records acquisition intent such as `minimal_capture`,
`profile_capture`, `psf_capture`, or `survey_capture`.

Canonical capture-plan examples:

```yaml
camera:
  frame_extent:
    mode: full_sensor
    origin_xy: [0, 0]
    shape_hw: [2048, 2448]
    sensor_shape_hw: [2048, 2448]

wavelengths:
  - illumination:
      mode: broadband_passthrough
      tls_setpoint_nm: 0.0
      effective_wavelength_nm: null
    grating: 1
    settle_ms: 2000
  - illumination:
      mode: monochromatic
      tls_setpoint_nm: 550.0
      effective_wavelength_nm: 550.0
    grating: 1
    settle_ms: 2000
```

Raw frame dataset dtype, compression, and chunking are controlled by
`RawFrameStoragePolicy`, not by ad-hoc writer constants. The policy separates:

* observed raw input dtype;
* averaging accumulator dtype;
* stored `frames_avg` dtype;
* stored burst-frame dtype;
* compression and chunk shape.

The default stores frame averages as `float32` and preserves the first burst
input dtype when `store_burst=True`. This keeps small calibration outputs easy
to inspect while avoiding a default `float64` storage expansion for full-frame
raw data.

### Mainline artifact path

The staged plan is documented in [`docs/roadmap.md`](docs/roadmap.md).

In short:

```text
profile-aware artifacts -> support-aware peak-cluster preparation -> adaptive peak-cluster dictionary
```

Cross-repository modelling (operator, reconstruction, mask-family work) belongs
to `lcd_mask_families`, `LCD_forward`, and `reconstruction`. See
[`docs/cross_repository_boundary.md`](docs/cross_repository_boundary.md).

Completed implementation details are preserved in
[`docs/completed_phases.md`](docs/completed_phases.md).

## Core Infrastructure Scope

The core infrastructure scope is the device/control/capture boundary that
supports the active profile, survey, support, and peak-cluster artifact paths
described above. It includes camera/LCD/TLS service wrappers, frame streaming,
preview, control state, synchronized acquisition, raw HDF5 export, and
hardware-free or opt-in hardware tests.

Do not maintain this section as a second active-responsibilities list. Artifact
semantics and current scientific workflow ordering belong in the roadmap and
artifact-path sections above.

## Out of scope

Do not add to the core path:

* neural-network training
* forward surrogate implementation
* reconstruction model implementation
* differentiable mask optimization loop
* large general experiment scheduler
* notebook-only experiment logic
* pywinauto TLS automation
* direct GUI ownership of hardware lifecycle
* hidden global hardware state
* default hardware-dependent tests

## Control architecture

The intended dependency direction is:

```text
GUI / CLI / task
  -> control command
  -> SessionController
  -> devices / capture
  -> hardware or data stream
```

GUI code should not directly control devices.

Task code should not bypass the control layer unless the bypass is explicitly documented and justified.

Hardware-facing wrappers belong in `devices/`.

Capture-specific frame handling belongs in `capture/` or task-specific capture modules.

System state should be represented through the control state object and updated through events.

## TLS policy

The active TLS backend is `tls_c1`.

Rules:

* do not use pywinauto for new TLS control
* do not control vendor GUI windows
* do not scatter low-level SDK calls across the repository
* wrap TLS access through `devices/tls_service.py`
* expose TLS behavior through control commands and events
* keep hardware tests opt-in
* allow no-hardware import and no-hardware tests
* do not commit vendor DLLs into this repository

Recommended command semantics:

```text
SetTLSWavelength:
    set target wavelength only

MoveTLS:
    perform physical motion

RefreshTLSStatus:
    query current hardware state
```

This separation should be preserved because setting a target wavelength and moving the hardware are different operations.

## Data export policy

Raw acquisition data should be stored before conversion.

A raw capture HDF5 file should preserve:

```text
masks_physical
frames_raw or frames_avg
camera metadata
LCD metadata
TLS metadata
capture timing metadata
camera frame extent / acquired-frame extent metadata
support / peak-cluster coordinate metadata when available
processing flags
```

Handoff-grade exports (measured evidence, measured response) should be
generated by explicit handoff or conversion steps, not by raw capture code.

Do not discard raw metadata during first acquisition.

## Testing policy

Default tests must run without:

* camera hardware
* LCD hardware
* TLS hardware
* vendor DLLs

Hardware tests must be explicit opt-in.

Suggested environment-variable pattern:

```text
RUN_CAMERA_HARDWARE_TESTS=1
RUN_LCD_HARDWARE_TESTS=1
TLS_C1_RUN_HARDWARE_TESTS=1
```

Fake or mock backends should be used for default CI-style tests.

## Installation

Base installation:

```bash
pip install -r requirements.txt
```

If editable installation is configured:

```bash
pip install -e .
```

TLS support requires installing `tls_c1` separately.

Example local editable installation:

```bash
pip install -e path/to/tls_c1
```

Do not assume TLS support is available unless the dependency is installed and explicitly enabled.

## Running the GUI

`app/main_gui.py` is the manual/debug control GUI. It owns a controller
lifecycle and may change camera, LCD, and TLS hardware state.

Basic manual/debug GUI launch:

```bash
python -m app.main_gui
```

TLS support is enabled explicitly via:

```bash
python -m app.main_gui --enable-tls [--tls-serial-number SERIAL] [--tls-mono MONO] [--tls-port-type TYPE] [--tls-safe-grating N]
```

TLS CLI options:

| Flag | Default | Description |
|------|---------|-------------|
| `--enable-tls` | (off) | enable TLS wavelength control via tls_c1 SDK |
| `--tls-serial-number` | `$TLS_C1_SERIAL` | device serial number |
| `--tls-mono` | Omni | monochromator type |
| `--tls-port-type` | USB | port type |
| `--tls-safe-grating` | 1 | grating to set after auto-connect |

When `tls_c1` is not installed, `--enable-tls` exits with a clear error
describing the missing dependency.  Omitting `--enable-tls` starts the
GUI without any TLS requirement.

`scripts/monitor_run_status.py` is the file-only run-status monitor. It reads
only task-published status files and previews, does not open camera/LCD/TLS
hardware, and is safe to open or close while a capture task keeps running.
The latest camera image displayed by the monitor is the most recent preview
file written by the capture task after a burst capture.

Example monitor usage:

```bash
python scripts/capture_forward_dataset.py --plan plan.yaml --output out.h5 --hardware \
  --status-dir outputs/run_status/repeatability_001

python scripts/monitor_run_status.py --status-dir outputs/run_status/repeatability_001
```

Use `--no-gui` for terminal-only polling. See
`docs/readonly_monitor_gui.md` for behavior, limitations, and the safety
boundary.



## Current status

The repository currently has:

* camera sidecar / frame stream infrastructure
* GUI preview path
* mono LCD display abstraction with axis-aware subpixel model
* control-layer architecture with commands, events, and state
* completed minimal hardware capture and raw HDF5 preservation baseline
* completed full-frame scout -> peak layout -> fixed-size peak-patch dictionary data contract
* completed first-pass `PeakSupportAnalysisReport`
* hardware-free default test infrastructure

Current active work: hardware validation of the profile-driven calibration
chain, and real-data support-analysis operationalization with support stability
audit. Learning-side forward modelling, reconstruction, and mask-family design
remain outside `optic_system` and are split across `LCD_forward`,
`reconstruction`, and `lcd_mask_families`.
