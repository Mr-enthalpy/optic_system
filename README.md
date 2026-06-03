# optic_system

`optic_system` is the hardware-control and synchronized-capture frontend for the mono-LCD programmable diffractive imaging project.

It is not the neural-network training repository.
Its downstream learning / reconstruction repository is `LCD_forward`.

The intended system boundary is:

```text
optic_system
  -> controls camera / LCD / TLS
  -> captures raw experimental observations
  -> writes raw capture HDF5 with metadata
  -> optionally converts raw capture data into LCD_forward-compatible HDF5

LCD_forward
  -> loads training HDF5
  -> trains mask -> PSF forward surrogate
  -> renders frames from object + PSF
  -> trains reconstruction baseline
  -> evaluates forward / reconstruction performance
````

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
* future conversion boundary toward `LCD_forward`

The repository should stay focused on hardware control and data acquisition.
Training, reconstruction, and differentiable mask optimization belong to `LCD_forward`.
The long-term scientific route is summarized in
[`docs/research_idea.md`](docs/research_idea.md); in this repository, that route
stops at measured artifacts, profile-aware raw captures, metadata, and
diagnostics.

## System components

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

## Relationship with LCD_forward

`optic_system` should produce experimental data.

`LCD_forward` should consume training-ready HDF5 data.

The long-term connection is:

```text
optic_system raw capture HDF5
  -> conversion script
  -> LCD_forward train/val/test HDF5
  -> forward surrogate training
  -> reconstruction training
  -> evaluation
```

The `LCD_forward` training format is expected to include tensors such as:

```text
Forward calibration:
masks: [N, T, 1, Hm, Wm]
psfs:  [N, T, L, Hp, Wp]

Reconstruction:
objects: [N, L, H, W]
frames:  [N, T, 1, H, W]
masks:   [N, T, 1, Hm, Wm]
```

`optic_system` should not directly train these models.

## Development roadmap

The active roadmap is maintained in [`docs/roadmap.md`](docs/roadmap.md).
Completed Phase 0/1/2 implementation details and hardware-smoke evidence are
preserved in [`docs/completed_phases.md`](docs/completed_phases.md).

Current stage:

```text
Phase 3.5C -- real-data operationalization and support stability audit.
```

Completed baseline:

```text
Phase 2 -- minimal hardware capture layer.
Phase 3.5A -- full-frame scout -> peak layout -> fixed-size peak-patch dictionary data contract.
Phase 3.5B -- first-pass diffraction support analysis report.
```

The current mainline data-artifact paths are:

```text
FullFramePSFSurvey
  -> first-pass PeakLayoutProfile
  -> fixed-size PeakPatchPSFDictionary

FullFramePSFSurvey
  -> PeakSupportAnalysisReport
  -> future SupportCandidateStabilityReport
  -> future AdaptivePeakLayoutProfile
  -> future AdaptivePeakClusterPSFDictionary
```

The fixed-size `PeakPatchPSFDictionary` is a v1 compatibility baseline. The
medium-term representation target is an adaptive peak-cluster dictionary where
each real diffraction peak cluster has its own support, coordinates, and local
raw data.

Training the peak-cluster forward model, multi-frame reconstruction, and
differentiable mask or `GenerMask` optimization are deferred to `LCD_forward`.

### Raw capture HDF5 schema

```
/                            attrs: plan_id, created_at_ns, software_version
/raw/frames_avg              [N_capture, H, W]  float64  (always written)
/raw/frames                  [N_capture, K, H, W] float64  (only if store_burst=True)
/masks/masks_physical        [N_mask, Hlcd, Wlcd_phys] uint8
/masks/mask_id               [N_mask] str
/masks/family_id             [N_mask] str
/masks/family_params_json    [N_mask] str
/masks/has_mask_array        [N_mask] bool
/tls/wavelength_nm           [N_wavelengths] float64
/tls/grating                 [N_wavelengths] int64
/tls/settle_ms               [N_wavelengths] int64
/tls/timestamp_ns            [N_wavelengths] int64
/tls/status_json             [N_wavelengths] str
/camera/exposure_us          [N_capture] float64
/camera/gain_db              [N_capture] float64
/camera/roi_json             [N_capture] str
/camera/timestamp_ns         [N_capture] int64
/camera/status_json          [N_capture] str
/lcd/settle_ms               [N_capture] int64
/lcd/display_timestamp_ns    [N_capture] int64
/lcd/mapping_policy_json     scalar str  (axis-aware: subpixel_axis, physical_mono)
/lcd/metadata_json           scalar str  (display_index, reported_shape,
                                          logical_shape, subpixel_axis, physical_shape)
/profiles/requirements_json  scalar str  (plan requires block)
/profiles/pupil_profile_id   scalar str
/profiles/camera_profile_id  scalar str
/capture/capture_index       [N_capture] int64
/capture/wavelength_index    [N_capture] int64
/capture/mask_index          [N_capture] int64
/capture/burst_count         [N_capture] int64
/capture/completed           [N_capture] bool
/capture/plan_json           scalar str
/capture/plan_id             scalar str
/capture/processing_flags_json scalar str
```

### Mainline artifact path

The mainline no longer treats Phase 3 as one broad bucket containing profile
calibration, PSF dictionaries, support diagnostics, conversion, and learning
experiments. The active staged plan is documented in
[`docs/roadmap.md`](docs/roadmap.md).

In short:

```text
Phase 3   -- stable capture and profile-aware experimental artifacts
Phase 3.5 -- support-aware peak-cluster preparation
Phase 3.6 -- adaptive peak-cluster PSF dictionary
Phase 4+  -- LCD_forward-side modelling, reconstruction, and optimization
```

Historical Phase 0/1/2 details are preserved in
[`docs/completed_phases.md`](docs/completed_phases.md). Thesis-branch phase
numbers are experimental history and do not define the mainline roadmap.

## Active scope

Allowed in core development:

* camera hardware wrapper
* frame stream client
* preview worker
* LCD physical mono mask display
* TLS SDK service wrapper
* control-layer command / event / state definitions
* minimal synchronized capture task
* raw HDF5 export
* conversion boundary toward `LCD_forward`
* hardware-free tests
* opt-in hardware smoke tests

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

## Legacy task policy

Some files under `tasks/` may come from earlier experimental directions.

They should not be assumed to define the current architecture.

Before reusing any old task:

1. audit its dependency path
2. confirm it uses `control -> devices` boundaries
3. confirm it does not bypass `SessionController`
4. confirm it does not depend on pywinauto TLS automation
5. confirm it emits sufficient metadata
6. document whether it is active, legacy, or deprecated

New minimal capture tasks should be implemented cleanly and separately.

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
ROI metadata
processing flags
```

Training-ready files for `LCD_forward` should be generated by a separate conversion step.

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

The active work is Phase 3.5C: real-data support-analysis operationalization and
support stability audit. Immediate `optic_system` work is limited to measured
artifact construction and diagnostics. Learning-side forward modelling,
reconstruction, and mask optimization remain `LCD_forward` responsibilities.
