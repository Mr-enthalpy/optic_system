# AGENTS.md

This is the single canonical project instruction document for `optic_system`.

The old staged files under `AGENTS/` have been retired. Stage history and current
scope are now recorded here as sections of one document, so new work does not
need to reconcile multiple instruction files.

## Project Context

This repository is a refactor and reconstruction of an optical experiment
control system.

The legacy implementation lives under `old/`. It contains old camera sidecar
code, shared-memory video transport, GUI scripts, LCD code, aperture/pupil
utilities, and one-off experiment scripts. Treat `old/` as reference material
only.

Do not modify `old/`.

## Global Constraints

- Preserve the active project skeleton: `devices/`, `capture/`, `control/`,
  `gui/`, `app/`, `patterns/`, and `tasks/`.
- Keep dependency direction clear: `gui -> control -> devices / capture`.
- `devices` and `capture` must not depend on `gui`.
- Keep device SDK calls out of the main process unless a stage explicitly
  changes that architecture.
- Prefer small modules with explicit responsibilities.
- Do not recreate monolithic legacy scripts or GUI-owned control logic.
- Do not overbuild schedulers, calibration engines, workflow DSLs, or fake
  hardware systems before they are needed.

## Architecture Boundaries

### `devices/`

Owns hardware-facing boundaries and transport clients:

- camera sidecar RPC client;
- camera sidecar implementation;
- frame-stream shared-memory consumer;
- LCD service and LCD packing boundary;
- TLS service wrapper (lazy-import `tls_c1`);
- small backend adapters when needed.

It must not contain GUI logic or experiment orchestration.

### `capture/`

Owns frame-consumption helpers:

- preview worker;
- latest-frame handling;
- lightweight frame statistics.

### `control/`

Owns semantic coordination:

- commands;
- events;
- shared state;
- startup/shutdown sequencing;
- applying camera/LCD settings.

The GUI and later automation code should both act as clients of this layer.

### `gui/`

Owns visualization and user interaction:

- live preview;
- camera parameter display/editing;
- camera/LCD status display;
- minimal LCD debug actions.

The GUI must send intent through `control`, not call device services directly.

### `app/`

Owns composition:

- instantiate services;
- wire controller and GUI;
- run startup;
- perform safe shutdown.

## Camera Architecture

The current camera architecture is process-isolated:

1. camera SDK access runs inside the sidecar process through
   `devices/camera_backend_flycapture2.py`;
2. the main process controls the sidecar through ZMQ REQ/REP;
3. the sidecar writes frame bytes to a shared-memory ring buffer;
4. the sidecar publishes frame metadata through ZMQ PUB;
5. downstream consumers read shared memory using the metadata;
6. large image payloads must not be sent through ZMQ.

`devices/camera_service_impl.py` should stay focused on RPC handling, state, and
shared-memory write scheduling. Do not import the camera SDK directly into GUI,
control, capture, or main process code.

## Camera Backend

The active camera backend is `flycapture2_c`, replacing the old
`pyflycap2 + FlyCapture GUI` path.

Important dependency facts:

- The target repository is `Mr-enthalpy/flycapture2_c`.
- The import package is `flycapture2_c`.
- The distribution/project name may appear as `flycapture2-c`.
- Do not write `flycapture_c`.
- `flycapture2_c` does not bundle the vendor FlyCapture2 SDK, DLLs, drivers,
  headers, or libraries.
- The FlyCapture2 SDK/runtime must be installed separately on hardware machines.
- The sidecar defaults to the same Python interpreter as the main process and
  that environment must be able to import `flycapture2_c` and load the
  FlyCapture2 C runtime when hardware operations begin.
- Import failure must produce a readable error that mentions
  `Mr-enthalpy/flycapture2_c` and package `flycapture2_c`.
- No-hardware tests must not require the FlyCapture2 SDK or a connected camera.

Useful environment variables:

- `OPTIC_SYSTEM_SIDECAR_PYTHON`: optional generic Python command override for
  launching the sidecar.
- `SIDECAR`: optional path override for the sidecar script.
- `FLYCAPTURE2_SDK_DIR`: FlyCapture2 SDK install path for `flycapture2_c`.
- `FLYCAPTURE2_DLL_DIR`: directory containing the FlyCapture2 C runtime DLL.
- `CAMERA_SERVICE_LOG`: file path for sidecar stdout/stderr capture.
- `CAMERA_SERVICE_DEBUG=1`: inherit sidecar stdout/stderr in the console.
- `CAM_BAYER_PATTERN`: optional Bayer preview conversion override, such as `GR`.

Local development may use the sibling checkout:

```text
C:\Users\teacher H\PycharmProjects\flycapture2_c
```

Do not assume that checkout exists on every machine. Document it as a local
development convenience, not as a portable dependency.

Install `flycapture2_c` into the same Python environment that runs
`optic_system`, for example:

```text
python -m pip install -e "C:\Users\teacher H\PycharmProjects\flycapture2_c"
python -c "import flycapture2_c; print(flycapture2_c.__file__)"
```

The old `PY38_BIN` variable belongs only to the historical `pyflycap2` backend
path and must not affect the `flycapture2_c` sidecar launcher.

## Camera Startup

The current headless startup path is:

1. connect to or launch the camera sidecar;
2. `OpenCamera`;
3. sidecar calls `Camera.open(index)`;
4. sidecar reads camera info/capabilities;
5. sidecar disables trigger only when `disable_trigger=true` is explicitly requested;
6. sidecar applies explicit scriptable configuration;
7. sidecar starts capture;
8. sidecar reads a first frame to determine frame layout;
9. sidecar creates shared memory;
10. main process sends `StartStream`;
11. preview/capture consumers read PUB metadata and shared memory.

`PreConfigGUI` is deprecated. It must not open a GUI. It may remain as a
compatibility RPC that returns a structured error with replacement operations.

The project no longer requires FlyCapture GUI pre-configuration as a normal
startup step. Explicit protocol operations replace that workflow.

## Camera Protocol Requirements

Keep compatibility for these existing operations:

- `Ping`
- `OpenCamera`
- `GetCameraInfo`
- `StartStream`
- `StopStream`
- `SetProperty`
- `GetRange`
- `GetValue`
- `CloseCamera`
- `PreConfigGUI` as deprecated compatibility only

Supported expanded operations include:

- `GetBackendInfo`
- `SnapshotProperties`
- `GetPropertyInfo`
- `SetPropertyAuto`
- `GetTriggerMode`
- `DisableTrigger`
- `SetTriggerMode`
- `GetStreamStatus`
- `GetFormat7Info`
- `GetFormat7Configuration`
- `ValidateFormat7`
- `SetPixelFormat`
- `SetROI`
- `SetGrabTimeout`
- `ReconfigureCamera`
- `Shutdown`

`CloseCamera` closes the camera while keeping the service alive.
`Shutdown` closes the camera and exits the sidecar.

RPC errors should be structured:

```json
{
  "ok": false,
  "err": "message",
  "error_type": "CameraStateError",
  "op": "SetProperty",
  "recoverable": true
}
```

## Shared Memory Frame Metadata

Frame metadata should include enough information to interpret the shared-memory
slot without relying on legacy `format` alone:

- `protocol_version`
- `backend`
- `shm`
- `ring_size`
- `index`
- `seq`
- `width`
- `height`
- `stride`
- `row_bytes`
- `frame_nbytes`
- `dtype`
- `shape`
- `pixel_format`
- `format`
- `timestamp_sdk` when available
- `ts_ns`
- `dropped_frames`

For 16-bit frames, `dtype` must be `uint16` and byte counts must reflect two
bytes per pixel. `flycapture2_c` currently decodes `RGB8` / `RGB` into
`(height, width, 3)` `uint8` RGB arrays; publish those as `format: "rgb8"`.
Do not infer BGR from a three-channel array unless a backend explicitly reports
a BGR pixel format.

Use `flycapture2_c.pixel_format.PIXEL_FORMAT_SUPPORT` when it is available.
Configuration support and `read_frame()` decode support are separate. Do not
treat a camera-configurable pixel format as stream-safe unless it is also
`read_frame_decodable`.

## LCD Architecture

LCD control remains device/control-layer owned. GUI actions must flow through
commands.

The LCD physical representation is mono subpixel data:

```text
[H, 3W]
```

The display buffer may be RGB-shaped:

```text
[H, W, 3]
```

Mapping:

```text
rgb[y, x, c] = mono[y, 3*x + c]
```

Rules:

- physical mask reasoning uses `[H, 3W]`;
- only the LCD device boundary packs mono masks to RGB display buffers;
- RGB channels are packed neighboring mono subpixels, not semantic colors;
- LCD service initialization should default to an all-transmissive mask.

## TLS Architecture

TLS control replaces the legacy `pywinauto` GUI automation path with a device
wrapper around the `tls_c1` SDK while preserving the same architecture rule:

> GUI sends intent, `control/` owns semantics, and `devices/` owns SDK access.

### Current TLS scope

- connect and disconnect the TLS device through `devices/tls_service.py`;
- set grating and target wavelength through controller commands;
- trigger a move through the controller;
- expose TLS status, errors, and movement state through `control/events.py` and
  `control/state.py`;
- keep future hardware smoke tests opt-in via environment variables.
- treat `Mr-enthalpy/tls_c1` as an optional external dependency rather than a
  vendored subtree.

Do not:

- reintroduce `pywinauto` GUI automation for TLS;
- call `tls_c1` low-level `SpectrometerAPI` directly outside the wrapper;
- let GUI widgets call `TLSService` directly;
- make `tls_c1` a hard startup dependency for the base GUI path;
- build the full wavelength sweep workflow here;
- build the full calibration workflow here.

### TLS service rules

`devices/tls_service.py` is the only TLS SDK boundary.

Responsibilities:

- lazy-import `tls_c1` so the base project can import without the SDK installed;
- use the high-level `tls_c1` / `TLSC1` facade only;
- normalize SDK status into project-level state;
- convert SDK exceptions into reportable project errors.
- document installation as an external dependency from
  `https://github.com/Mr-enthalpy/tls_c1`;
- keep vendor DLL handling outside this repository and use `TLS_C1_SDK_DIR` on
  the local machine when needed.

### TLS controller rules

`control/session_controller.py` should:

- accept an optional TLS service;
- translate GUI intent into explicit TLS commands;
- publish TLS status changes through the event bus;
- avoid blocking the GUI thread on potentially long TLS moves.

### TLS test rules

Default automated tests must not require:

- TLS hardware;
- vendor DLLs;
- `TLS_C1_SDK_DIR`.

Real hardware tests must be opt-in and explicitly gated by environment variables.

Recommended documentation guidance:

- base `requirements.txt` may stay independent from `tls_c1` when the project
  must remain importable without the SDK;
- README should explain both `pip install
  git+https://github.com/Mr-enthalpy/tls_c1.git` and local editable install from
  a checkout;
- README should state that `TLS_C1_SDK_DIR` is local-machine configuration, not
  a committed asset path.

## Stage History And Goals

### Stage 0: Architecture Extraction

Goal: establish the project skeleton and prevent legacy control flow from being
rebuilt as monolithic GUI scripts.

Success criteria:

- GUI is a frontend, not the control plane;
- device access is isolated in `devices`;
- frame consumption is separated into `capture`;
- command/event/state semantics exist in `control`.

### Stage 1: Hardware Camera Prototype

Goal: support a minimal hardware-backed camera preview path.

Success criteria:

- sidecar can be launched or connected;
- camera can be opened and closed safely;
- stream can be started and stopped;
- frames reach GUI preview through shared memory and preview worker;
- camera settings can be listed, displayed, and applied through control.

Historical note: early work used `pyflycap2` and FlyCapture GUI
pre-configuration. That is no longer the active requirement.

### Stage 2: Minimal LCD Integration

Goal: add LCD service/control integration while preserving GUI boundaries.

Success criteria:

- LCD service exposes reported shape and physical mono shape;
- LCD defaults to all-transmissive on startup;
- at least one debug pattern can be shown through controller commands;
- GUI does not call LCD service directly.

### Stage 3: Headless FlyCapture2 C Camera Backend

Goal: replace the old `pyflycap2 + GUI` camera backend with scriptable
`flycapture2_c` control while preserving the sidecar and shared-memory
architecture.

Success criteria:

- no runtime dependency on `pyflycap2.interface.Camera` or `GUI`;
- `devices/camera_service_impl.py` uses `flycapture2_c`;
- `PreConfigGUI` is deprecated and does not open GUI;
- trigger, properties, pixel format, ROI, camera info, and stream status are
  available through RPC;
- `OpenCamera` reports whether trigger configuration was requested/applied and
  does not change trigger state implicitly;
- no-hardware tests cover protocol behavior;
- hardware tests are opt-in only.

## Out Of Scope Until Explicitly Requested

- full experiment scheduler;
- calibration workflow engine;
- wavelength sweep workflow;
- synchronized LCD-camera acquisition workflow;
- dataset export pipeline;
- GenerMask optimization workflow;
- full aperture search;
- broad fake-hardware simulation framework;
- moving camera SDK calls into the main process;
- replacing shared memory with ZMQ image payloads.

## Development Priorities

When making changes, prefer this order:

1. preserve architecture boundaries;
2. keep camera sidecar and shared-memory transport stable;
3. keep explicit headless camera configuration scriptable and testable;
4. expose hardware-observable status rather than hiding errors;
5. keep GUI/control/device responsibilities separate;
6. add tests that do not require hardware by default;
7. only then add larger experiment workflows.
