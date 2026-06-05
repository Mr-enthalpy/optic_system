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
- profile artifacts
- `FullFramePSFSurvey`
- `SensorEnergyCenterProfile`
- `PeakSupportAnalysisReport`
- `SupportCandidateStabilityReport`
- `PeakLayoutProfile` and future `AdaptivePeakLayoutProfile`
- `PeakPatchPSFDictionary` and future `AdaptivePeakClusterPSFDictionary`
- measured-artifact diagnostics and metadata-rich exports
- optional export boundary toward `LCD_forward`

`optic_system` is not responsible for:

- neural-network training
- forward surrogate implementation
- reconstruction network implementation
- differentiable mask optimization
- model evaluation inside the learning stack
- large general experiment orchestration

Those belong to `LCD_forward` or later experiment-specific layers.

## Current phase

Current active tracks:

```text
Phase 3A-H -- hardware validation of the profile-driven calibration chain.
Phase 3.5C -- real-data operationalization and support stability audit.
```

Profile-chain hardware validation is about camera/LCD/TLS tasks and persisted
profile artifacts. Support-stability work is about measured PSF
survey/support/peak-cluster artifacts. Do not mix the two concerns in one PR
unless explicitly requested.

Completed:

- Phase 2 minimal hardware capture layer.
- Phase 3.5A full-frame scout -> first-pass `PeakLayoutProfile` -> fixed-size
  `PeakPatchPSFDictionary`.
- Phase 3.5B `PeakSupportAnalysisReport` baseline.

Active:

- Phase 3A-H validation of broadband pass-through `CameraProfile`, broadband
  LCD pupil scan, `PupilProfile`, and selected-pupil-open per-band
  `CameraProfile`.
- scipy / streaming / energy-only support analysis.
- real-data presets for 2048 x 2448 full-frame data.
- `SupportCandidateStabilityReport`.
- adaptive peak-cluster support preparation.

Current meaning:

- ``app/main_gui.py`` is the manual/debug control GUI and may control hardware.
- ``scripts/monitor_run_status.py`` is the only supported read-only monitor.
  It reads task-published run-status files and must remain hardware-free.
- The bachelor-thesis experimental branch is historical reference, not a
  source of mainline workflow structure.
- The active mainline path is measured-artifact construction and diagnostics,
  not learning-side modelling.

Do not assume connected components are stable physical peak IDs.
Do not promote support components directly into `PeakLayoutProfile`.
Do not assume Phase 2B output data is training-ready or scientifically valid.

## Roadmap

The active roadmap lives in `docs/roadmap.md`. AGENTS.md intentionally keeps
only the execution constraints that agents must obey.

Mainline structure:

```text
Phase 3   -- stable capture and profile-aware artifacts
Phase 3.5 -- support-aware peak-cluster preparation
Phase 3.6 -- adaptive peak-cluster PSF dictionary
Phase 4+  -- LCD_forward-side modelling / reconstruction / optimization
```

Current `optic_system` work is limited to measured artifact construction,
diagnostics, and metadata-rich exports.

`optic_system` may build:

- profile artifacts;
- full-frame scout surveys;
- support analysis reports;
- support-candidate stability reports;
- peak layout and adaptive peak layout artifacts;
- fixed-size peak-patch and future adaptive peak-cluster dictionaries;
- dense-kernel compatibility exports and peak-cluster exports.

`optic_system` must not build:

- forward surrogate training code;
- reconstruction models;
- differentiable mask optimization loops;
- hidden `LCD_forward` training or validation invocations.

## Mainline / thesis branch relationship

The bachelor-thesis branch is historical reference.

Mainline may absorb:

- audited algorithms;
- reusable artifact abstractions;
- hardware-free diagnostics.

Mainline must not absorb:

- thesis phase numbering;
- thesis workflow ordering;
- ROI-centered data contracts;
- thesis-specific reconstruction or figure scripts.

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

### TLS zero-order pass-through mode

Setting the TLS wavelength to ``0 nm`` places the monochromator grating in
zero-order position, where it passes broadband light through the optical path.

* Use ``TLSService.set_pass_through()`` / ``tls_c1.set_pass_through()`` for
  zero-order mode.
* Do not call ``set_wavelength_nm(0)``. The `tls_c1` high-level parser rejects
  non-positive wavelengths and exposes pass-through as a separate API.
* Capture plans must use explicit illumination objects. Numeric wavelength
  sentinels are not supported pass-through encodings.
* Task internals must consume `IlluminationSpec`.
* Pass-through is a device-control mode, not a physical wavelength.
* Wavelength labels without TLS are not equivalent to pass-through mode; they
  skip TLS movement, while pass-through explicitly moves the grating to
  zero-order.

## Task-layer rules

Historical files under `tasks/` must not be assumed to define the current architecture.

`tasks/artifacts/` contains hardware-free shared artifact IO, frame-source, and
coordinate-frame helpers. New measured-artifact modules should use this layer
instead of reimplementing frame-source parsing, manifest JSON handling, or
coordinate validation.

Runtime mode is explicit for capture, profile, and diagnostic task entry
points. Real hardware tasks default to hardware runtime mode. Fake devices,
missing required hardware, raw fallback, and test-settle overrides must be
explicit non-hardware/diagnostic choices. No-TLS positive wavelength labels are
allowed only in non-hardware contexts. TLS zero-order pass-through requires a
real TLS adapter in hardware mode.

Before reusing any old task:

1. audit whether it uses `control -> devices`
2. check whether it bypasses `SessionController`
3. check whether it depends on old pywinauto TLS logic
4. check whether it preserves metadata
5. check whether it is compatible with current LCD physical mask convention
6. mark it as active, legacy, experimental, or deprecated

New minimal capture tasks should be implemented cleanly and separately.

Do not silently revive legacy task logic.

### Profile task chain

Detailed operational rules for the profile-driven calibration chain live in
`docs/profile_task_chain.md`.

Hard rules:

* Pass-through uses `TLSService.set_pass_through()` /
  `tls_c1.set_pass_through()`, not `set_wavelength(0)`.
* Capture plans must use explicit `tasks.illumination.IlluminationSpec`
  objects; raw numeric pass-through sentinel checks are invalid.
* PSF-producing tasks need a `PupilProfile` and a per-band pupil-open
  `CameraProfile`.
* Broadband `CameraProfile` artifacts are only for broadband pupil scan.
* Full-LCD-open per-band profiles must not be used for PSF-producing tasks.
* Hardware tests must remain opt-in.
* Do not introduce hidden `LCD_forward` training or validation.
* `SensorEnergyCenterProfile` is a coordinate-origin artifact, not a crop
  artifact.

## Peak-cluster artifact rules

The fixed-size `PeakPatchPSFDictionary` is a v1 baseline and compatibility
artifact only. The medium-term production target is
`AdaptivePeakClusterPSFDictionary`.

Rules:

* `PeakSupportAnalysisReport` is diagnostic evidence, not a layout.
* Do not promote connected components directly into `PeakLayoutProfile`.
* `SupportCandidateStabilityReport` is required before adaptive layout
  promotion.
* Component IDs are local to one report and are not stable physical peak IDs.
* Support candidates must be traceable to `(entry_index, tau, component_id)`
  or equivalent source component keys.
* Adaptive clusters must record the original coordinate frame.
* Adaptive clusters must record center, support type, radius or bbox, local raw
  patch, background, energy metrics, and peak value.
* Non-rectangular supports must preserve an explicit support mask.
* Fixed-size dense or peak-patch exports may remain available as baselines, but
  they must not be described as the final peak-cluster representation.

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
camera frame extent / acquired-frame extent metadata
profile ids
support / peak-cluster coordinate metadata when available
processing flags
```

Raw capture metadata should use camera frame extent terminology.
`/camera/frame_extent_json` is the raw HDF5 field. Capture plans must use
`camera.frame_extent`. Pre-mainline thesis/development data are outside the
current schema and require explicit migration if needed.

Measured artifacts have distinct roles:

```text
FullFramePSFSurvey:
  scout data

PeakSupportAnalysisReport:
  diagnostic evidence

PeakPatchPSFDictionary:
  v1 fixed-size compatibility baseline

AdaptivePeakClusterPSFDictionary:
  medium-term production target
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

`LCD_forward` consumes measured-artifact exports.

Dense-kernel export may exist as a baseline:

```text
Forward calibration:
masks: [N, T, 1, Hm, Wm]
psfs:  [N, T, L, Hp, Wp]

Reconstruction:
objects: [N, L, H, W]
frames:  [N, T, 1, H, W]
masks:   [N, T, 1, Hm, Wm]
```

The mainline direction is peak-patch and adaptive peak-cluster export:

```text
FullFramePSFSurvey
  -> PeakPatchPSFDictionary baseline export

FullFramePSFSurvey
  -> SensorEnergyCenterProfile
  -> PeakSupportAnalysisReport
  -> SupportCandidateStabilityReport
  -> AdaptivePeakLayoutProfile
  -> AdaptivePeakClusterPSFDictionary export
```

Rules:

* `optic_system` may export dense-kernel baselines when useful.
* `optic_system` may export peak-patch and adaptive peak-cluster artifacts.
* `SensorEnergyCenterProfile` defines one global sensor energy center as the
  camera-coordinate origin for support and peak-cluster analysis.
* `SensorEnergyCenterProfile` is not a crop-window artifact. Do not introduce
  crop extraction, crop-size selection, or crop-centered exports through this
  profile.
* `SensorEnergyCenterProfile` should record per-entry background, corrected
  energy, and fallback diagnostics. If a valid pixel domain is known, derive
  the profile with that domain rather than allowing invalid sensor regions to
  bias the center.
* Downstream center-relative coordinates must be traceable to
  `SensorEnergyCenterProfile.center_xy`, and downstream tasks must reject the
  profile if its coordinate frame or camera frame extent does not match the
  analyzed data.
* `optic_system` must not implement `LCD_forward` models.
* `optic_system` must not train forward surrogates.
* `optic_system` must not train reconstruction networks.
* If invoking `LCD_forward` scripts is ever added, it must be explicit and external, not hidden inside hardware capture code.

## GenerMask rules

GenerMask modelling and optimization belong to `LCD_forward` unless explicitly
requested for physical mask generation or capture-plan construction.

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
- Phase 3.5A full-frame scout -> first-pass `PeakLayoutProfile` -> fixed-size
  `PeakPatchPSFDictionary`
- Phase 3.5B `PeakSupportAnalysisReport` baseline

Current mainline priority:

1. Validate the Phase 3A-H profile-driven calibration chain on real hardware.
2. Accelerate `PeakSupportAnalysisReport` on real full-frame data.
3. Add streaming / energy-only support analysis for large surveys.
4. Define real-data presets for support analysis.
5. Build `SupportCandidateStabilityReport`.
6. Promote only stable support candidates into `AdaptivePeakLayoutProfile`.
7. Build `AdaptivePeakClusterPSFDictionary`.
