# AGENTS.md

## Project context

This project is a refactor and reconstruction of an existing optical experiment control system.

The legacy implementation is stored under `old/`. It contains:

- a Python 3.8 `pyflycap` camera sidecar process;
- shared-memory inter-process communication for real-time video frames;
- camera control logic;
- GUI scripts for real-time preview and camera parameter adjustment;
- mono LCD related scripts;
- aperture / pupil search utilities;
- several one-off experimental scripts.

The `old/` directory is a reference source only.

Do not modify any file under `old/`.

The current project outside `old/` is the active refactored system.

---

## Current stage

Hardware camera access is now available.

The current stage is no longer purely architectural. The system must support a minimal hardware-backed prototype:

> Open the camera, stream live video, show the live preview in GUI, expose camera parameters, and allow the user to adjust them.

This is the first minimal working form of the refactored system.

The priority is still architectural correctness, but implementation must now respect real hardware behavior.

---

## Hard constraints

### 1. Do not modify `old/`

`old/` is only for reference.

Do not edit, rename, delete, reformat, or move anything under `old/`.

### 2. Preserve the current project skeleton

The external project structure already exists.

Do not replace it with a new architecture. Fill and refine the current structure.

### 3. Treat `old/cam_impl.py` as the sidecar reference

`old/cam_impl.py` corresponds to the current sidecar implementation:

- `devices/camera_service_impl.py`

This file is the hardware-facing Python 3.8 camera service.

Do not rewrite its core logic unless explicitly requested.

The current refactor should focus on:

- camera service client;
- frame stream client;
- preview worker;
- control layer;
- GUI frontend.

### 4. GUI is required but must not be the control plane

The GUI is a required first-class component.

It must provide:

- live video preview;
- camera parameter display;
- camera parameter editing;
- basic device status visibility;
- hardware debugging visibility.

However, the GUI must not directly become the system control plane.

The GUI should send commands to the control layer and subscribe to events or state updates.

It should not directly implement experiment workflows, device lifecycle logic, calibration sequences, or task orchestration.

### 5. Introduce and preserve a control / event layer

The old system already had:

- device code;
- GUI code;
- task scripts.

The missing layer was a unified control / event semantic layer.

Therefore, the current project must keep a clear middle layer that defines:

- commands;
- events;
- shared session state;
- session controller behavior.

The GUI and future automated workers should both act as clients of this layer.

### 6. Keep the first prototype minimal

The current target is only:

- camera sidecar connection;
- live frame stream;
- GUI preview;
- camera parameter display;
- camera parameter update;
- safe shutdown.

Do not prematurely implement:

- LCD control;
- TLS control;
- wavelength sweep;
- aperture search;
- full calibration sequence;
- mask generation;
- frame averaging workflow;
- dataset export;
- automated experiment scheduler.

Leave extension points, but do not overbuild.

---

## Hardware-aware development rules

Because the hardware camera is now connected, some failures are only observable through physical behavior.

Examples:

- API calls succeed but the displayed frame is wrong;
- video stream is active but visually frozen;
- exposure / gain changes are accepted but have no physical effect;
- preview frame is flipped, cropped, saturated, or color-converted incorrectly;
- shared-memory decoding succeeds but produces visually invalid frames.

Therefore:

### 1. Do not rely only on program return values

A call returning success does not prove the physical state is correct.

Hardware-facing changes should make the system more observable.

### 2. Add debug visibility where appropriate

Prefer adding lightweight diagnostics such as:

- current frame shape;
- pixel format;
- max pixel value;
- frame sequence number;
- timestamp;
- camera setting values before and after update;
- stream running status;
- sidecar connection status;
- latest error message.

### 3. Keep human observation in the loop

For hardware-facing behavior, human visual confirmation may be necessary.

Do not try to fake hardware validation with purely synthetic tests.

When a physical effect must be judged visually, expose enough information for a human to report:

- whether preview updates;
- whether brightness changes after parameter updates;
- whether frames look saturated;
- whether frame orientation appears correct;
- whether the stream freezes;
- whether the GUI state matches the visible behavior.

### 4. Prefer structured debug outputs

When adding hardware debug functions, prefer structured outputs over print-only behavior.

Useful debug fields include:

- camera serial;
- width;
- height;
- stride;
- pixel format;
- setting name;
- requested value;
- actual value after write;
- frame sequence number;
- timestamp;
- max pixel value.

---

## Recommended current architecture

### `devices/`

Hardware-facing device clients and sidecar-facing code.

Expected files:

- `camera_service.py`
- `camera_service_impl.py`
- `frame_stream.py`

Responsibilities:

- start or connect to the camera sidecar;
- send camera control RPC commands;
- open / close camera;
- start / stop stream;
- read camera metadata;
- read / update camera parameters;
- subscribe to frame metadata;
- decode shared-memory frames.

The `devices/` layer must not depend on GUI.

---

### `capture/`

Frame-consumption helpers.

Expected file for this stage:

- `preview_worker.py`

Responsibilities:

- continuously consume frames from `FrameStreamClient`;
- keep latest preview frame;
- compute lightweight frame statistics;
- send frame packets or stats to the controller via callback.

It should not own experiment logic.

---

### `control/`

The central semantic layer.

Expected files:

- `commands.py`
- `events.py`
- `state.py`
- `bus.py`
- `session_controller.py`

Responsibilities:

- define command objects;
- define event objects;
- maintain session state;
- receive GUI commands;
- call device and capture layers;
- publish events back to GUI;
- support safe shutdown.

At the current stage, this layer should remain small.

---

### `gui/`

GUI frontend.

Expected files:

- `main_window.py`
- `camera_panel.py`
- `preview_panel.py`
- `status_panel.py`
- `bindings.py`

Responsibilities:

- display live preview;
- display camera parameters;
- allow parameter editing;
- display basic runtime status;
- send user actions to controller as commands;
- update itself from events and session state.

The GUI must not directly call low-level device methods except through the controller.

---

### `app/`

Application entry points.

Expected file:

- `main_gui.py`

Responsibilities:

- create device clients;
- open camera;
- start stream;
- create controller;
- create GUI;
- start preview worker;
- enter GUI main loop;
- perform safe shutdown.

---

## Current minimal command / event model

### Commands

At this stage, only minimal commands are required:

- `ApplyCameraSettings`
- `Shutdown`

Optional if already implemented cleanly:

- `RefreshCameraSettings`

Do not add full experiment commands yet unless explicitly requested.

### Events

At this stage, useful events include:

- `PreviewFrameUpdated`
- `PreviewStatsUpdated`
- `CameraSettingsApplied`
- `StatusMessage`
- `CameraError`

### Session state

At minimum, track:

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

## Success criteria for this stage

This stage is successful if:

1. The sidecar camera service can be started or connected.
2. The camera can be opened.
3. The frame stream can be started.
4. The GUI shows a live preview from the camera.
5. The GUI displays camera settings.
6. The user can adjust camera settings from the GUI.
7. The GUI does not directly act as the control plane.
8. Control logic goes through the controller layer.
9. The system can shut down safely.
10. Hardware-observable debug information is available.

No full calibration workflow is required in this stage.

---

## Development priorities

When changing code, prefer the following order:

1. Make the minimal live preview path stable.
2. Make camera settings display correct.
3. Make camera setting updates observable and verifiable.
4. Improve status / error reporting.
5. Only then add additional experiment actions.

Do not expand into full optical experiment automation before the minimal hardware preview prototype is stable.