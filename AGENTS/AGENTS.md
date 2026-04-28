# AGENTS.md

## Purpose

This document is the consolidated project constraint set derived from:

- `AGENTS/AGENTS0.md`
- `AGENTS/AGENTS1.md`
- `AGENTS/AGENTS2.md`

It combines the architecture-first refactor rules, the hardware-backed camera prototype rules, the LCD integration rules, and the later GUI startup correction clarified during implementation review.

The stage files remain useful as historical context, but this file should be treated as the unified working constraint document for the active project.

---

## Project context

This repository is a refactor and reconstruction of an optical experiment control system.

The legacy implementation lives under `old/`. It includes:

- a Python 3.8 `pyflycap` / `pyflycap2` camera sidecar process;
- shared-memory + pub-sub video transport;
- camera control logic;
- GUI scripts for live preview and parameter control;
- mono LCD related code;
- aperture / pupil search utilities;
- one-off experiment scripts and task code.

These legacy files are reference material for refactoring. They are not the target structure of the new project.

---

## Global non-negotiable constraints

### 1. Do not modify `old/`

`old/` is reference-only.

Allowed:

- read it;
- compare behavior;
- map responsibilities from it;
- migrate ideas into the new structure.

Not allowed:

- editing files under `old/`;
- renaming, deleting, moving, or reformatting files under `old/`.

### 2. Preserve the current outer project skeleton

The active project structure outside `old/` must remain the working scaffold.

Do not:

- flatten the project back into script piles;
- replace the project with a new unrelated architecture;
- move core logic back into monolithic GUI scripts.

### 3. Keep clear dependency direction

The dependency direction should remain:

- `gui -> control`
- `control -> devices / capture`
- `devices` must not depend on `gui`
- `capture` must not depend on `gui`

### 4. Small modules, explicit roles

Prefer:

- small modules;
- small classes;
- explicit responsibilities;
- readable state and event flow.

Do not recreate new god objects.

### 5. Avoid premature overbuilding

No heavy workflow framework is required yet.

Do not prematurely implement:

- a full experiment scheduler;
- a calibration engine;
- a mask optimization workflow engine;
- a generic orchestration DSL;
- a large fake-hardware simulation stack.

Leave extension points, but only build the current stage requirements.

---

## Architecture baseline

The intended structure is:

```text
devices/
capture/
control/
gui/
app/
```

### `devices/`

Responsibilities:

- hardware-facing device wrappers;
- camera sidecar RPC client;
- frame-stream shared-memory consumer;
- LCD service and LCD packing boundary;
- compatibility with legacy device-side protocols.

Must not contain:

- GUI logic;
- experiment orchestration;
- calibration workflow logic.

Key files:

- `camera_service.py`
- `camera_service_impl.py`
- `frame_stream.py`
- `lcd_service.py`
- optional `lcd_backend.py`
- optional `lcd_debug_patterns.py`

### `capture/`

Responsibilities:

- frame-consumption helpers separated from raw device communication;
- preview worker;
- later capture helpers if needed.

Key file:

- `preview_worker.py`

### `control/`

This is the semantic middle layer and is one of the most important refactor goals.

Responsibilities:

- command semantics;
- event semantics;
- shared session state;
- coordination between GUI and device/capture layers;
- safe startup and shutdown sequencing.

Key files:

- `commands.py`
- `events.py`
- `state.py`
- `bus.py`
- `session_controller.py`

### `gui/`

The GUI is required, but it is not the control plane.

Responsibilities:

- live visualization;
- camera parameter display and editing;
- device and hardware-debug status visibility;
- minimal LCD debug interaction when needed;
- sending user intent to the controller;
- updating widgets from events and shared state.

Key files:

- `main_window.py`
- `camera_panel.py`
- `preview_panel.py`
- `status_panel.py`
- optional `lcd_panel.py`
- `bindings.py`

### `app/`

Responsibilities:

- assemble the active system;
- instantiate services, controller, and GUI;
- run startup order;
- enter GUI main loop;
- perform safe shutdown.

Key file:

- `main_gui.py`

---

## Legacy mapping rules

The most important legacy mappings are:

- `old/cam.py`
- `old/cam_impl.py`
- `old/base.py`

Specific mapping:

- `old/cam_impl.py -> devices/camera_service_impl.py`

`devices/camera_service_impl.py` is the hardware-facing Python 3.8 sidecar implementation. Its core behavior should remain close to the legacy source unless an explicit rewrite is requested.

Current refactor focus is primarily around:

- service client structure;
- frame-stream client structure;
- preview worker;
- control/event/state layer;
- GUI frontend behavior.

---

## Camera subsystem rules

### Current camera goal

The camera subsystem must support the minimal hardware-backed prototype:

> start or connect to the camera sidecar, open the camera, stream frames, show live preview in GUI, expose camera parameters, allow camera parameter adjustment, and support safe shutdown.

### GUI pre-configuration rule

This is a strict rule derived from legacy behavior and later implementation correction.

In `old/cam.py`, the camera startup order in `Video` is:

1. ensure sidecar;
2. `PreConfigGUI`;
3. `OpenCamera`;
4. `StartStream`.

This order is authoritative for the refactored camera GUI path.

Reason:

- some cameras must be fully configured through the FlyCapture GUI before connection succeeds;
- the `pyflycap2` programmatic API exposes fewer controls than the GUI;
- therefore the pre-configuration GUI is not optional by default in the normal hardware path.

Required behavior:

- `main_gui` startup should default to opening the pre-configuration GUI first;
- only an explicit override may skip it;
- `gui.show_selection()` should be allowed to block until the user closes the GUI;
- do not add auto-close timers;
- do not add forced short timeouts for the `PreConfigGUI` RPC call;
- the client must not treat a long-open pre-configuration window as a startup failure.

### Hardware observability rules

Do not rely only on API success return values.

Hardware-facing behavior should expose enough structured information for a human to judge:

- whether preview is updating;
- whether the image is visually valid;
- whether exposure/gain changes physically affect the image;
- whether frames appear frozen, flipped, cropped, saturated, or incorrectly converted.

Useful camera debug fields include:

- camera serial;
- width;
- height;
- stride;
- pixel format;
- frame sequence number;
- timestamp;
- max pixel value;
- stream running status;
- sidecar connection status;
- latest error;
- setting name;
- requested setting value;
- actual value after write.

### Camera responsibilities by layer

`devices/` should handle:

- start/connect sidecar;
- camera RPC commands;
- camera open/close;
- stream start/stop;
- camera info;
- parameter read/write;
- frame metadata subscription;
- shared-memory frame decode.

`capture/` should handle:

- continuous frame consumption;
- latest preview packet;
- lightweight statistics;
- callback delivery to the controller.

`control/` should handle:

- command dispatch;
- state updates;
- event publication;
- safe startup and shutdown;
- camera parameter apply and refresh semantics.

`gui/` should handle:

- display preview;
- display camera parameters;
- accept camera parameter edits;
- display status/debug information;
- send commands through the controller.

### Minimal camera command / event / state semantics

Minimum camera commands:

- `ApplyCameraSettings`
- `Shutdown`

Optional and acceptable when implemented cleanly:

- `RefreshCameraSettings`

Minimum useful camera events:

- `PreviewFrameUpdated`
- `PreviewStatsUpdated`
- `CameraSettingsApplied`
- `StatusMessage`
- `CameraError`

Minimum useful camera state:

- `camera_open`
- `stream_running`
- `latest_preview_bgr`
- `latest_max_pixel`
- `latest_frame_seq`
- `latest_frame_timestamp_ns`
- `pixel_format`
- `frame_width`
- `frame_height`
- `last_error`

---

## LCD subsystem rules

### Current LCD goal

The next integrated stage adds minimal LCD control while preserving the same architecture rule:

> GUI remains a visualization and interaction frontend, not the device control plane.

Current LCD scope:

- initialize LCD device/service;
- default LCD to an all-transmissive state;
- support displaying mono physical masks;
- support a small set of LCD debug patterns;
- expose enough LCD state and metadata to support physical debugging.

Do not yet implement:

- full aperture search;
- calibration sequence automation;
- wavelength sweep workflow;
- synchronized LCD-camera acquisition workflows;
- dataset export pipeline;
- GenerMask optimization;
- a full mask sequence scheduler.

### Critical LCD representation rule

The LCD display API may report an RGB display buffer:

```text
[H, W, 3]
```

But the physical LCD must be treated as a mono subpixel array:

```text
[H, 3W]
```

The RGB channels are not semantic color channels. They are packed neighboring mono subpixel columns.

Canonical project convention:

- physical mono mask: `[H, 3W]`
- display RGB buffer: `[H, W, 3]`

Mapping:

```text
rgb[y, x, c] = mono[y, 3*x + c]
```

Rules:

- all physical mask reasoning must use `[H, 3W]`;
- only the LCD device boundary may pack `[H, 3W]` into `[H, W, 3]`;
- do not write LCD logic that treats RGB as real color meaning.

### LCD service rules

`devices/lcd_service.py` should be the main LCD abstraction.

Responsibilities:

- wrap LCD backend behavior;
- expose reported display metadata;
- expose physical mono shape `[H, 3W]`;
- convert mono masks to packed RGB buffers;
- show all-transmissive mask;
- show all-opaque mask;
- show arbitrary mono masks;
- expose LCD metadata.

If a backend adapter is needed, keep it small and avoid placing physical mask semantics there.

### LCD startup rule

After LCD service initialization, the LCD should default to an all-transmissive mask.

Use centralized configurable codes such as:

- `transmissive_code`
- `opaque_code`

Do not hard-code the assumption that `255` is always correct forever.

### LCD commands / events / state

Recommended minimal LCD commands:

- `SetLCDAllTransmissive`
- `SetLCDAllOpaque`
- `ShowLCDMonoMask`
- `ShowLCDDebugPattern`

Recommended minimal LCD events:

- `LCDAllTransmissiveShown`
- `LCDAllOpaqueShown`
- `LCDMaskShown`
- `LCDDebugPatternShown`
- `LCDStatusChanged`
- `LCDError`

Recommended LCD state fields:

- `lcd_connected`
- `lcd_reported_shape`
- `lcd_physical_shape`
- `lcd_current_mode`
- `lcd_current_mask_id`
- `lcd_last_error`

The state must clearly distinguish:

- reported display shape `[H, W, 3]`;
- physical mono shape `[H, 3W]`.

### LCD GUI rules

GUI support for LCD should remain minimal.

Acceptable approaches:

- add a small `lcd_panel.py`, or
- extend `status_panel.py` with LCD metadata and state.

If buttons are added, they must send commands through the controller.

They must not call LCD service directly.

Useful LCD debug actions include:

- full transmissive;
- full opaque;
- center cross;
- vertical bars;
- horizontal bars;
- corner markers;
- checkerboard with safe feature size.

---

## Startup and shutdown sequencing

### Camera-oriented startup sequence

The preferred camera startup sequence is:

1. connect or start camera sidecar;
2. open pre-configuration GUI with `PreConfigGUI`;
3. wait until the pre-configuration GUI is closed by the user;
4. open camera;
5. start camera stream;
6. start preview worker.

### Integrated camera + LCD startup sequence

When LCD is part of the assembled app, the preferred overall startup sequence is:

1. connect or start camera sidecar;
2. run camera `PreConfigGUI`;
3. open camera;
4. start camera stream;
5. initialize LCD backend/service;
6. set LCD to all-transmissive state;
7. initialize controller and GUI;
8. start preview worker if not already started by the controller design;
9. enter GUI main loop.

### Shutdown sequence

Preferred shutdown order:

1. stop preview worker;
2. stop camera stream;
3. close camera;
4. close LCD service;
5. release owned sidecar if applicable;
6. close GUI.

---

## What the GUI must and must not do

GUI should do:

- display live preview;
- display max-pixel and lightweight frame status;
- display camera parameter values;
- accept camera parameter edits;
- display camera and LCD metadata/status;
- expose minimal LCD debug actions when required;
- send commands to the controller;
- refresh widgets from events and shared state.

GUI must not do:

- directly own the device lifecycle;
- directly call low-level camera operations except through controller semantics;
- directly call LCD device service except through controller semantics;
- implement experiment workflows;
- become the scheduler or automation layer;
- hide hardware problems behind silent retries or purely optimistic status.

---

## Out-of-scope items for the current consolidated stage

Unless explicitly requested, do not casually add:

- full task scheduler;
- full calibration workflow;
- LCD/TLS integrated experiment workflow;
- wavelength sweep workflow;
- aperture search workflow;
- experiment mask generation pipeline;
- data export system;
- calibration dataset capture pipeline;
- full mock hardware infrastructure;
- automatic reconstruction / optimization workflows.

---

## Success criteria

This consolidated stage is successful if all of the following are true:

1. The previous camera live preview GUI works.
2. The GUI shows camera preview and camera parameter state.
3. Camera parameter changes flow through the control layer.
4. The GUI does not act as the control plane.
5. The camera startup path preserves the required `PreConfigGUI -> OpenCamera` behavior.
6. The pre-configuration GUI is not auto-closed or prematurely timed out by the client.
7. Hardware-observable camera debug information is available.
8. LCD service, when assembled, is controlled through the controller rather than directly from GUI.
9. LCD defaults to all-transmissive state on startup.
10. LCD metadata clearly distinguishes display RGB shape and physical mono shape.
11. At least one LCD debug pattern can be shown through the control path.
12. The overall system can shut down safely without re-entangling responsibilities.

---

## Development priorities

When making changes, prefer this order:

1. preserve architecture boundaries;
2. keep the camera preview path stable;
3. keep `PreConfigGUI` startup semantics correct;
4. make camera parameter display and update behavior correct;
5. improve hardware observability and status reporting;
6. integrate LCD control conservatively;
7. only then consider higher-level experiment features.
