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

Current phase: Phase 0 / Phase 1 boundary.

Meaning:

- Camera base path exists.
- LCD base path exists.
- TLS service / command / event / state support may partially exist.
- TLS application / GUI integration may still be incomplete.
- Minimal capture task layer is not yet stable.
- Raw capture HDF5 export is not yet stable.
- Conversion into `LCD_forward` format is not yet stable.

Do not assume the repository is already a full calibration system.

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

### Phase 2  --  Minimal capture task layer

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

Canonical physical mask convention:

```text
mono mask: [H, 3W]
display RGB buffer: [H, W, 3]

rgb[y, x, c] = mono[y, 3*x + c]
```

Rules:

* Physical mask logic must operate on `[H, 3W]`.
* RGB packing should occur only at the LCD device boundary.
* Do not move physical LCD mask reasoning into GUI widgets.
* Do not introduce RGB LCD assumptions unless explicitly requested.
* Do not silently reinterpret mono masks as ordinary grayscale `[H, W]` if physical subpixel semantics matter.

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

1. Finish documentation and boundary reset.
2. Complete TLS SDK integration closure.
3. Add minimal capture task layer.
4. Add raw capture HDF5 export.
5. Add conversion to `LCD_forward` HDF5.
6. Add family-aware GenerMask capture support.
7. Consider larger closed-loop experiment automation only after the above are stable.
