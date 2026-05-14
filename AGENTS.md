# AGENTS.md

This repository is `optic_system`.

It is the hardware-control and synchronized-capture frontend for the mono-LCD programmable diffractive imaging project.

Its downstream learning repository is `LCD_forward`.

## Project role

`optic_system` is responsible for:

- camera hardware access through sidecar / shared memory frame streaming
- camera preview and capture infrastructure
- mono LCD physical mask display
- TLS wavelength control through `tls_c1`
- control-layer command / event / state management
- minimal synchronized capture tasks
- raw capture HDF5 export
- conversion boundary toward `LCD_forward`

`optic_system` is not responsible for:

- neural-network training
- forward surrogate implementation
- reconstruction network implementation
- differentiable mask optimization
- model evaluation inside the learning stack
- large general experiment orchestration

Those belong to `LCD_forward` or later experiment-specific layers.

## Current phase

Current mainline phase: Phase 2C -- GUI / diagnostics / architecture consolidation.

Meaning:

- Phase 2A minimal capture task layer is complete.
- Phase 2B hardware smoke capture validation is complete.
- The mainline is now consolidating GUI roles, diagnostics boundaries,
  file-only monitoring, and stale entry points before the long-term
  LCD_forward conversion phase.
- ``app/main_gui.py`` is the manual/debug control GUI and may control hardware.
- ``scripts/monitor_run_status.py`` is the only supported read-only monitor.
  It reads task-published run-status files and must remain hardware-free.
- The bachelor-thesis experimental branch is separate and owns its own
  Phase 3.0--3.7 task workflow.

Do not assume the repository is already a full calibration system.
Do not assume Phase 2B output data is training-ready or scientifically valid.

## Roadmap

### Phase 0  --  Documentation and boundary reset

Primary goal:

- rewrite repository-level constraints
- replace obsolete early-GUI-prototype constraints
- clarify that hardware capture is now in scope
- clarify that training is out of scope
- clarify TLS backend policy
- mark old task scripts as legacy unless audited

Allowed work:

- README updates
- AGENTS updates
- architecture docs
- roadmap docs
- task status docs
- comments that clarify active vs legacy paths

Do not implement large functionality in Phase 0 unless explicitly requested.

### Phase 1  --  TLS SDK integration closure

Primary goal:

- make `tls_c1` the only active TLS backend
- remove the active pywinauto TLS automation path
- expose TLS through normal application/control path

Expected work:

- `devices/tls_service.py`
- TLS commands
- TLS events
- TLS state fields
- `SessionController` TLS handling
- optional GUI TLS panel
- app assembly support for optional TLS
- no-hardware TLS tests
- opt-in hardware TLS tests

Non-goals:

- full wavelength sweep workflow
- full calibration workflow
- capture automation
- training integration
- direct GUI-to-TLS calls

### Phase 2A  --  Minimal capture task layer

Primary goal:

Create a clean minimal capture path.

Expected new modules may include:

- `tasks/capture_plan.py`
- `tasks/raw_capture_h5.py`
- `tasks/capture_forward_dataset.py`

First task should support:

- load capture plan
- initialize camera / LCD / optional TLS
- set TLS wavelength if enabled
- move TLS and wait until idle
- show physical mono LCD mask
- wait `settle_ms`
- acquire K frames
- average or store burst
- write raw capture HDF5 with metadata

Non-goals:

- full scheduler
- full calibration engine
- GenerMask optimization loop
- training
- direct `LCD_forward` training invocation

### Phase 2B  --  Hardware smoke capture validation

Primary goal:

Validate that the Phase 2A capture task can control real camera, mono LCD,
and optional TLS in a deterministic sequence and produce ``raw_capture.h5``
with valid structure and metadata.

Expected work:

- hardware smoke test plans (camera + LCD, camera + LCD + TLS)
- ``scripts/make_smoke_masks.py`` for ``[H, 3W]`` / ``[3H, W]`` mask generation
- ``scripts/inspect_raw_capture.py`` for HDF5 structure inspection
- opt-in hardware smoke tests under ``tests/``
- axis-aware ``LCDService`` with user-configured ``subpixel_axis``
- LCD metadata audit in raw HDF5

Non-goals:

- optical scientific correctness validation
- PSF measurement or calibration
- light-source integration
- spectral response characterization
- claiming captured data is training-ready
- forward surrogate training
- ``LCD_forward`` import or invocation

Phase 2B must keep ``processing_flags_json`` as::

  scientific_calibration_valid: false
  optical_alignment_validated:   false
  training_ready:                false

Passing hardware smoke tests does **not** imply the optical system is
calibrated or scientifically valid.

### Phase 2C -- GUI / diagnostics / architecture consolidation

Primary goal:

Stabilize the GUI and diagnostics architecture after hardware smoke validation
and before long-term raw-capture conversion work.

Allowed work:

- remove duplicated or unsafe GUI/monitor entry points
- clarify that ``app/main_gui.py`` is the control GUI
- clarify that ``scripts/monitor_run_status.py`` is the file-only read-only monitor
- improve ``RunStatusPublisher`` / ``RunStatusReader``
- improve documentation around diagnostics boundaries
- clean empty legacy stubs and stale documentation
- keep default tests hardware-free

Non-goals:

- pupil scan
- dOTF
- PSF dictionary
- thesis-specific experiment scripts
- LCD_forward conversion
- neural training
- GenerMask optimization

### Phase 3  --  Raw capture to LCD_forward conversion

Primary goal:

Convert raw experimental captures into training-ready HDF5 for `LCD_forward`.

Expected conversion target:

```text
Forward calibration:
masks: [N, T, 1, Hm, Wm]
psfs:  [N, T, L, Hp, Wp]

Reconstruction:
objects: [N, L, H, W]
frames:  [N, T, 1, H, W]
masks:   [N, T, 1, Hm, Wm]
````

The raw capture format must preserve enough metadata to allow reprocessing.

### Phase 4  --  Family-aware GenerMask calibration and closed-loop experiments

Primary goal:

Support structured mask-family calibration and later closed-loop experiments.

Possible work:

* GenerMask family registry
* family-aware capture plans
* held-out family validation
* perturbation robustness audit
* repeated forward-surrogate retraining through `LCD_forward`
* controlled mask-design experiments

Do not start Phase 4 before Phase 1-3 are stable unless explicitly requested.

## Mainline / thesis branch relationship

The bachelor-thesis branch starts from the post-Phase-2B baseline and uses a
distilled experimental workflow for thesis delivery.

Mainline and thesis branch have different responsibilities:

Mainline provides reusable infrastructure:

- camera / LCD / TLS services
- capture task foundations
- raw HDF5 conventions
- run-status diagnostics
- read-only monitor
- GUI and architecture cleanup

The thesis branch consumes this infrastructure and implements thesis-specific
task workflows:

- exposure safety sweep
- effective LCD pupil scan
- PSF repeatability and ROI alignment
- dOTF diagnostic
- measured PSF dictionary
- minimal linear reconstruction demo
- thesis figures

Synchronization rule:

- mainline architecture fixes may be periodically merged or rebased into the
  thesis branch.
- thesis-specific task code should not be moved into mainline unless explicitly
  promoted as reusable infrastructure.
- mainline documentation should not replace its long-term Phase 3/4 roadmap
  with the thesis Phase 3.0--3.7 workflow.

## Architecture rules

The dependency direction is:

```text
GUI / CLI / task
  -> control command
  -> SessionController
  -> devices / capture
  -> hardware or stream
```

Rules:

* GUI must not directly own hardware lifecycle.
* GUI must not call hardware SDKs directly.
* GUI should send user intent as control commands.
* Task code should use control-layer semantics unless a bypass is explicitly justified.
* Device-facing wrappers belong in `devices/`.
* Frame-stream and capture handling belongs in `capture/` or explicit task capture modules.
* Hardware state should be reflected in control state and events.
* Avoid hidden global hardware state.

## Camera rules

Camera access should go through the existing camera service / sidecar boundary.

Do not directly import or use legacy camera SDKs in GUI, tasks, or control code unless explicitly working inside the camera sidecar implementation.

Expected path:

```text
CameraServiceClient
  -> camera sidecar RPC
  -> shared memory frame stream
  -> FrameStreamClient
  -> PreviewWorker or capture task
```

Rules:

* Preserve sidecar isolation.
* Preserve shared-memory frame-stream semantics.
* Keep no-hardware import possible.
* Hardware tests must be opt-in.
* Do not silently change camera startup semantics.
* If changing `PreConfigGUI`, trigger mode, or open/start behavior, update README and tests together.

## LCD rules

The LCD is a physical mono subpixel device.

Do not treat the LCD as a semantic RGB image display.

### LCD identity and physical mono mask convention

Do not assume the target mono LCD can be inferred automatically from
display resolution.

The user or configuration is responsible for selecting:

* LCD display index
* LCD subpixel axis

The software is responsible for:

* checking internal consistency between ``logical_shape``, ``subpixel_axis``,
  and ``physical_shape``
* recording LCD metadata into raw capture HDF5
* failing clearly when mask shape does not match ``LCDService.physical_shape``

Supported physical mono conventions:

* ``subpixel_axis = 0``: physical mono mask is ``[3H, W]``
* ``subpixel_axis = 1``: physical mono mask is ``[H, 3W]``

Rules:

* Do not hardcode ``1080×5760`` as an LCD physical mask shape.
* Do not assume width is always the subpixel-expanded axis.
* Do not silently select a desktop monitor as the target LCD.
* Physical mask logic must operate on the axis-aware physical mono representation.
* RGB packing should occur only at the LCD device boundary.
* Do not move physical LCD mask reasoning into GUI widgets.
* Do not introduce RGB LCD assumptions unless explicitly requested.
* Do not silently reinterpret mono masks as ordinary grayscale ``[H, W]`` if
  physical subpixel semantics matter.

## TLS rules

The active TLS backend is `tls_c1`.

The old pywinauto-based TLS GUI automation path is deprecated.

Rules:

* Do not write new pywinauto TLS control code.
* Do not control vendor GUI windows for TLS.
* Do not scatter direct SDK calls across the repository.
* Wrap TLS access through `devices/tls_service.py`.
* Use `tls_c1` high-level API by default.
* Do not call low-level `SpectrometerAPI` from random modules.
* Expose TLS operations through control commands and events.
* Keep hardware tests opt-in.
* Keep default imports hardware-free.
* Do not commit vendor DLLs into this repository.
* Do not make `tls_c1` a hard import at top level unless explicitly inside optional TLS code.

Recommended command semantics:

```text
SetTLSWavelength:
    set target wavelength only

MoveTLS:
    perform physical movement

RefreshTLSStatus:
    query device status
```

Preserve this separation. Setting a target wavelength and moving the hardware are not the same operation.

## Task-layer rules

Historical files under `tasks/` must not be assumed to define the current architecture.

Before reusing any old task:

1. audit whether it uses `control -> devices`
2. check whether it bypasses `SessionController`
3. check whether it depends on old pywinauto TLS logic
4. check whether it preserves metadata
5. check whether it is compatible with current LCD physical mask convention
6. mark it as active, legacy, experimental, or deprecated

New minimal capture tasks should be implemented cleanly and separately.

Do not silently revive legacy task logic.

## Run-status diagnostics rules

`RunStatusPublisher` / `RunStatusReader` provide a file-only diagnostics
boundary for read-only monitoring.  They are infrastructure; task code must
explicitly publish diagnostics if the monitor should display them.

Task integrations that want richer monitor output should call, as applicable:

```text
status.write_frame_preview(...)
status.write_frame_stats(...)
status.append_log(...)
```

If a task only writes `state.json` and `current_mask_preview`, the monitor must
continue to work and show only those available fields.  Missing frame previews,
frame stats, or logs are not monitor failures.

Reusable monitor/diagnostics improvements discovered on the thesis branch
may be selectively backported to master when they remain hardware-free
and task-agnostic.


## Capture data rules

Raw capture data must be preserved before conversion.

A raw capture HDF5 should preserve, as applicable:

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

Raw capture HDF5 must preserve LCD metadata when available:

* display_index
* reported_shape
* logical_shape
* subpixel_axis
* physical_shape
* mapping policy (axis-aware)

Raw capture HDF5 produced by Phase 2B must keep ``processing_flags_json`` as::

  scientific_calibration_valid: false
  optical_alignment_validated:   false
  training_ready:                false

Passing hardware smoke tests does **not** imply the optical system is
calibrated or scientifically valid.

Training-ready data for `LCD_forward` should be generated by a separate conversion step.

Do not discard metadata during first acquisition.

Do not directly train neural models from raw capture files inside `optic_system`.

## LCD_forward boundary

`LCD_forward` consumes training-ready HDF5.

Expected downstream format:

```text
Forward calibration:
masks: [N, T, 1, Hm, Wm]
psfs:  [N, T, L, Hp, Wp]

Reconstruction:
objects: [N, L, H, W]
frames:  [N, T, 1, H, W]
masks:   [N, T, 1, Hm, Wm]
```

Rules:

* `optic_system` may export or convert data into this format.
* `optic_system` must not implement `LCD_forward` models.
* `optic_system` must not train forward surrogates.
* `optic_system` must not train reconstruction networks.
* If invoking `LCD_forward` scripts is ever added, it must be explicit and external, not hidden inside hardware capture code.

## GenerMask rules

GenerMask work is Phase 4 or later unless explicitly requested.

When GenerMask appears in `optic_system`, its role is to produce physical LCD masks or capture plans, not to train neural networks.

Rules:

* Keep `GenerMask -> physical mask -> LCDService` explicit.
* Preserve physical mask visibility and metadata.
* Record family name and parameters in capture metadata.
* Do not skip the physical mask representation.
* Do not collapse directly into network latent variables inside `optic_system`.

## Testing rules

Default tests must run without:

* camera hardware
* LCD hardware
* TLS hardware
* vendor DLLs

Hardware tests must be explicit opt-in.

Use fake or mock backends for default tests.

Suggested environment variables:

```text
RUN_CAMERA_HARDWARE_TESTS=1
RUN_LCD_HARDWARE_TESTS=1
TLS_C1_RUN_HARDWARE_TESTS=1
```

Phase 2B hardware tests may additionally use:

```text
OPTIC_SYSTEM_RUN_PHASE2_HARDWARE_TESTS=1
OPTIC_SYSTEM_RUN_TLS_HARDWARE_TESTS=1
OPTIC_SYSTEM_LCD_DISPLAY_INDEX
OPTIC_SYSTEM_LCD_SUBPIXEL_AXIS
OPTIC_SYSTEM_EXPECT_LCD_LOGICAL_SHAPE
```

Rules:

* Do not make hardware tests run by default.
* Do not make top-level import require hardware DLLs.
* Do not make GUI import fail when optional TLS support is unavailable.
* Prefer no-hardware tests for command/event/state behavior.
* Use opt-in tests for real motion or real device access.

## Documentation rules

When changing hardware semantics, update documentation in the same change.

This includes:

* camera startup behavior
* trigger behavior
* LCD mask format
* TLS command semantics
* task status
* raw capture HDF5 schema
* hardware test requirements

README and AGENTS must not contradict code behavior.

If code and docs disagree, treat it as a bug.

## Style rules

* Keep modules small.
* Prefer explicit dataclasses for plans and state.
* Prefer explicit tensor/array shape comments.
* Avoid hidden global state.
* Avoid broad orchestration abstractions before they are needed.
* Keep hardware-facing code isolated.
* Keep training logic out of this repository.
* Use clear error types for hardware failures.
* Prefer narrow device wrappers over large generic hardware frameworks.

## Prohibited changes unless explicitly requested

Do not:

* resurrect pywinauto TLS automation
* add a full general experiment scheduler
* add neural-network training code
* add forward surrogate code
* add reconstruction models
* commit vendor DLLs
* bypass `SessionController` from GUI
* make `tls_c1` a mandatory import for normal startup
* silently reuse legacy `tasks/` as active architecture
* remove raw metadata during export
* conflate raw capture HDF5 with training-ready HDF5

## Preferred next implementation order

Completed:

- TLS SDK integration closure
- Phase 2A minimal capture task layer
- raw capture HDF5 export
- Phase 2B hardware smoke capture validation
- file-only read-only monitor
- removal of legacy monitor GUI path and empty task stubs

Current mainline priority:

1. Continue Phase 2C GUI / diagnostics / architecture consolidation as needed.
2. Keep diagnostics file-only and hardware-free.
3. Keep ``app/main_gui.py`` and ``scripts/monitor_run_status.py`` roles distinct.
4. After Phase 2C stabilizes, resume long-term Phase 3 raw-capture to
   LCD_forward conversion.
