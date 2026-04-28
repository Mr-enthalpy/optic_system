# AGENTS.md

## Context

The `old/` directory contains an existing legacy project. It already includes:

- a Python 3.8 sidecar process wrapping `pyflycap`;
- shared-memory-based inter-process communication for camera video;
- code that writes camera frames into shared memory and reads them back from a newer Python-side interface;
- several scripts for finding and selecting a valid aperture on a mono LCD setup;
- several GUI scripts/packages for live video preview and controllable camera parameters;
- several miscellaneous task scripts.

These files are **reference material** for refactoring. They are **not** the target structure of the current project.

---

## Main objective

This round is **not** a full-system rewrite.

The goal is to complete the **first-stage minimal working prototype**:

> open live video preview and provide a GUI that displays and allows adjustment of camera parameters.

This is both:

- the original minimal usable form of the system before logic became too entangled, and
- the correct base for later refactoring.

---

## Hard constraints

### 1. Do not modify anything inside `old/`

`old/` is for inspection and reference only.

- Read it
- map responsibilities from it
- migrate ideas from it

But **do not edit any file under `old/`**.

### 2. Keep the current outer project skeleton unchanged

The project already has an external scaffold outside `old/`.  
Do not replace that scaffold.  
Do not flatten the project back into a script pile.  
Implement the refactor **within the current structure**.

### 3. `old/cam_impl.py` maps directly to `devices/camera_service_impl.py`

`old/cam_impl.py` is the direct source for the device-side sidecar implementation.

It should correspond to:

- `devices/camera_service_impl.py`

Its **core logic should not be rewritten in this round**.

The current focus is not the hardware service implementation itself, but the client-side structure around it:
- device client layer,
- control/event layer,
- GUI layer.

### 4. Do not start by redesigning the GUI

The legacy system already has:

- a device layer,
- a presentation layer,
- several task scripts,

but **it lacks a unified control / scheduling / event-semantic layer**.

As a result:

- automation logic leaked into GUI callbacks,
- scripts secretly became another control plane,
- the GUI effectively turned into the system controller.

The next step should therefore be:

> **extract the middle control layer first**, not redesign the GUI first.

GUI and visualization are required and should remain permanent parts of the system.  
However, they must behave like clients of the middle layer, not as the control plane itself.

### 5. Stay within the stage-1 target

Do not expand too far.

This round should **only** achieve the minimal preview + camera-parameter GUI prototype.

You may leave room for future refactoring, but do **not** prematurely implement:
- the full task system,
- the full calibration workflow,
- the complete LCD/TLS integration,
- the complete experiment automation stack.

### 6. No hardware means no real testing is required

Because hardware is not connected in the current environment:

- actual end-to-end runtime validation is not required;
- real hardware tests are not required;
- fake or overbuilt hardware mocks are not required.

The focus is **architectural refactoring**, not runnable deployment.

### 7. Be conservative

This round should be implemented carefully and narrowly.

A good result is not “feature-rich”.  
A good result is:

- structurally correct,
- minimally complete,
- clearly extensible,
- not yet re-entangled.

### 8. The main legacy files to refactor from are:

- `old/cam.py`
- `old/cam_impl.py`
- `old/base.py`

The first-stage target is essentially to decouple and restructure the logic currently mixed across those files.

---

## Stage-1 target architecture

This round should produce the minimal pipeline:

device service client  
→ frame stream reader  
→ preview worker  
→ control layer  
→ GUI

That means:

- camera sidecar can be contacted;
- camera can be opened and stream started;
- frames can be read from shared memory / pub-sub metadata;
- live preview can be displayed in the GUI;
- camera settings can be queried and shown in the GUI;
- camera settings can be modified from the GUI;
- GUI sends commands to a control layer instead of talking directly to the device implementation.

---

## Scope of each layer

### `devices/`

Responsibilities:

- device-side client wrappers;
- camera control RPC client;
- frame stream shared-memory consumer;
- device-facing protocol compatibility.

This layer must **not** contain:
- GUI logic,
- task workflow logic,
- experiment orchestration.

Recommended files:
- `camera_service.py`
- `camera_service_impl.py`
- `frame_stream.py`

### `capture/`

Responsibilities:

- frame-consumption semantics separated from raw device communication;
- preview worker;
- later: capture helpers if needed.

Recommended files:
- `preview_worker.py`

### `control/`

This is the most important new layer for this round.

Responsibilities:
- command semantics,
- event semantics,
- shared session state,
- coordination between GUI and device-side clients.

Recommended files:
- `commands.py`
- `events.py`
- `state.py`
- `bus.py`
- `session_controller.py`

Do **not** build a heavy workflow engine yet.

### `gui/`

GUI should retain only two core responsibilities:

1. live visualization,
2. camera parameter display and adjustment.

GUI must not directly control the device layer.

Recommended files:
- `camera_panel.py`
- `preview_panel.py`
- `status_panel.py`
- `main_window.py`
- `bindings.py`

### `app/`

Responsibilities:
- assemble the minimal system,
- initialize controller + GUI + device clients,
- start the application.

Recommended files:
- `main_gui.py`

---

## GUI responsibility boundary

GUI should be responsible for:

- displaying live preview;
- displaying max-pixel or lightweight live status;
- displaying camera parameter values;
- accepting user edits for camera parameters;
- sending commands to the control layer;
- listening to events and refreshing widgets.

GUI should **not** be responsible for:

- owning device lifecycle;
- running acquisition workflows;
- running scan sequences;
- saving experiment files;
- generating experiment masks;
- managing automation logic.

---

## Control layer responsibility boundary

The control layer should:

- receive commands from GUI or other clients;
- call the device client layer;
- receive preview frames / stats;
- maintain shared state;
- publish events for GUI and future automation clients.

For this round it does **not** need a full task orchestration system.

---

## Minimal command / event / state semantics for stage 1

### Commands

At minimum:
- `ApplyCameraSettings`
- `Shutdown`

Avoid over-expanding the command vocabulary in this round.

### Events

At minimum:
- `PreviewFrameUpdated`
- `PreviewStatsUpdated`
- `CameraSettingsApplied`
- `StatusMessage`

### State

At minimum:
- `camera_open`
- `stream_running`
- `latest_preview_bgr`
- `latest_max_pixel`
- `last_error`

---

## What not to build in this round

To avoid re-entanglement, do **not** casually add:

- the full task scheduler;
- the full calibration workflow;
- LCD workflow integration;
- TLS wavelength workflow;
- aperture-search GUI workflow;
- experiment mask generation logic;
- full data export pipeline;
- full mock hardware infrastructure.

These belong to later stages.

---

## Code-style requirements

### 1. Small modules, small responsibilities

Prefer small classes/modules with explicit roles.

Do not recreate new “god objects”.

### 2. Explicit dependency direction

The dependency direction should remain clear:

- GUI → control
- control → devices / capture
- devices do not depend on GUI
- capture does not depend on GUI

### 3. Avoid premature abstraction

No heavy framework is required here.  
No complex orchestration DSL is required here.  
Keep the control semantics minimal and explicit.

### 4. Leave room for future stages without implementing them now

Leave extension points, but do not pre-build the entire future system.

### 5. Favor clear comments and readable structure

This is an architecture-first refactor.  
Clarity of responsibility is more important than compactness.

---

## Success criteria

This round is successful if all of the following are true:

1. GUI exists as a frontend, not as the control plane.
2. GUI no longer directly manipulates the low-level device implementation.
3. Camera parameter adjustment goes through the control layer.
4. Preview frames flow into GUI through preview/capture + control semantics.
5. The resulting structure can be extended later without re-coupling everything immediately.

That is enough for stage 1.