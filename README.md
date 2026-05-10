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

The physical mono mask convention is:

```text
mono mask: [H, 3W]
display RGB buffer: [H, W, 3]

rgb[y, x, c] = mono[y, 3*x + c]
```

All physical reasoning about LCD masks should use the mono representation `[H, 3W]`.

Only the LCD device boundary should pack this representation into RGB display buffers.

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

### Phase 0  --  Documentation and boundary reset

**Complete.**  (See `docs/architecture.md`, `docs/roadmap.md`, `tasks/README.md`, `AGENTS.md`.)

Goals:

* redefine `optic_system` as the hardware-control and synchronized-capture frontend
* replace old prototype-stage constraints
* clarify that neural training belongs to `LCD_forward`
* mark old experimental tasks as legacy unless audited
* document TLS SDK backend replacement
* prepare Codex / AGENT for staged implementation

Expected outputs:

* updated `README.md`
* updated `AGENTS.md`
* optional `docs/architecture.md`
* optional `docs/roadmap.md`
* optional `tasks/README.md`

### Phase 1  --  TLS SDK integration closure

**Complete.**  `tls_c1` is the sole active TLS backend.  Use `--enable-tls` to
assemble `TLSService` and inject it into `SessionController`.  An optional
`TLSPanel` provides GUI control (connect / disconnect / set grating / set
wavelength / move / refresh).  Pywinauto TLS automation is fully removed from
active code paths.  All default tests are hardware-free.

### Phase 2  --  Minimal capture task layer

**Implemented.**  Modules `tasks/capture_plan.py`, `tasks/raw_capture_h5.py`,
`tasks/capture_forward_dataset.py` provide the minimal capture path.  The CLI
entry point is `scripts/capture_forward_dataset.py`.

Goal:

Create a clean acquisition task path instead of reviving historical task scripts.

The capture task supports:

```text
load capture plan
initialize camera / LCD / optional TLS
for each wavelength:
    set TLS wavelength
    move and wait until idle
    for each mask:
        show LCD mono mask
        wait settle_ms
        acquire K frames
        average or store burst
        save frame data and metadata
write raw capture HDF5
```

Modules:

```text
tasks/capture_plan.py              capture plan dataclasses + JSON/YAML loading
tasks/raw_capture_h5.py            incremental raw HDF5 writer (context manager)
tasks/capture_forward_dataset.py   orchestration + CaptureDeviceBundle protocol
scripts/capture_forward_dataset.py thin CLI entry point
```

Tests: `tests/test_capture_plan.py`, `tests/test_raw_capture_h5.py`,
`tests/test_capture_forward_dataset_dry_run.py`.

#### CLI usage

Dry-run (no hardware required)::

```bash
python scripts/capture_forward_dataset.py --plan plan.yaml --output out.h5 --dry-run
```

Hardware mode (explicit opt-in)::

```bash
python scripts/capture_forward_dataset.py --plan plan.yaml --output out.h5 --hardware --enable-tls
```

#### Raw capture HDF5 schema

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
/lcd/mapping_policy_json     scalar str
/capture/capture_index       [N_capture] int64
/capture/wavelength_index    [N_capture] int64
/capture/mask_index          [N_capture] int64
/capture/burst_count         [N_capture] int64
/capture/completed           [N_capture] bool
/capture/plan_json           scalar str
/capture/plan_id             scalar str
/capture/processing_flags_json scalar str
```

Processing flags record::

```json
{
  "scientific_calibration_valid": false,
  "optical_alignment_validated": false,
  "training_ready": false,
  "phase": "phase2_minimal_capture",
  "completed": true,
  "error": null,
  "last_completed_capture_index": ...
}
```

### Phase 3  --  Raw capture to LCD_forward conversion

Goal:

Convert raw experimental captures into `LCD_forward` training data.

Expected outputs:

```text
raw capture HDF5
  -> converted forward training HDF5
  -> train.h5 / val.h5 / test.h5
```

The conversion layer should handle:

* mask downsampling or encoding into `[N, T, 1, Hm, Wm]`
* PSF ROI extraction
* frame averaging
* dark / flat correction if available
* wavelength metadata
* camera metadata
* TLS metadata
* train / val / test split

The raw capture format should preserve enough metadata to allow future reprocessing.

### Phase 4  --  Family-aware GenerMask calibration and closed-loop experiments

Goal:

Support structured mask-family calibration and controlled optimization loops.

This phase may include:

* GenerMask family registry
* family-aware calibration plans
* held-out family generalization checks
* perturbation robustness audits
* repeated forward-surrogate retraining
* controlled mask design experiments

This phase should only begin after Phase 1-3 are stable.

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

Basic GUI launch:

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

## Current status

The repository currently has:

* camera sidecar / frame stream infrastructure
* GUI preview path
* mono LCD display abstraction
* control-layer architecture (commands / events / state)
* **completed TLS SDK integration** — tls_c1 via TLSService, SessionController TLS handling, TLSPanel GUI, opt-in TLS hardware smoke tests
* hardware-free test infrastructure for all layers

The repository still needs:

* minimal capture task layer (Phase 2)
* raw capture HDF5 writer (Phase 2)
* conversion path toward `LCD_forward` (Phase 3)
* family-aware GenerMask calibration (Phase 4)
