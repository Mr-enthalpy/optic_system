# optic_system

`optic_system` is an in-progress refactor of an optical experiment control
system. The current work focuses on stable device boundaries, explicit control
semantics, camera preview, and minimal LCD control. It is not yet a complete
experiment automation system.

## Canonical Instructions

Project instructions live in [AGENTS.md](AGENTS.md).

That file is now the single source of truth for architecture boundaries, staged
goals, camera sidecar rules, `flycapture2_c` dependency notes, LCD conventions,
and success criteria.

The previous staged documents under `AGENTS/` have been retired to avoid
conflicting instructions.

## Current Scope

Implemented or active work:

- camera sidecar connection and launch;
- headless camera backend using `flycapture2_c`;
- ZMQ REQ/REP camera control protocol;
- shared-memory ring buffer frame transport;
- ZMQ PUB frame metadata;
- GUI live preview;
- camera parameter display and update through the control layer;
- minimal LCD service and debug-pattern control;
- LCD default all-transmissive startup behavior;
- no-hardware tests and opt-in hardware test skeletons.

Out of scope for now:

- full aperture search;
- full calibration workflow;
- wavelength sweep workflow;
- synchronized LCD-camera acquisition scheduling;
- dataset export pipeline;
- GenerMask optimization;
- large workflow/scheduler framework.

## Directory Layout

```text
app/        application assembly and entry points
capture/    frame consumption helpers and preview worker
control/    commands, events, state, and controller semantics
devices/    camera sidecar client/implementation, frame stream, LCD service
docs/       migration notes and operational documentation
gui/        preview, camera panel, status panel, LCD debug panel
old/        legacy reference code; do not modify
patterns/   pattern generation helpers
tasks/      future task-layer placeholders
tests/      no-hardware tests and opt-in hardware tests
```

Dependency direction must remain:

```text
gui -> control -> devices / capture
```

`devices` and `capture` must not depend on `gui`.

## Camera Architecture

The camera SDK stays inside the sidecar process:

- `devices/camera_service.py` is the main-process client.
- `devices/camera_service_impl.py` is the hardware-facing sidecar.
- Main process control uses ZMQ REQ/REP.
- Frame bytes are written by the sidecar into a shared-memory ring buffer.
- Frame metadata is published with ZMQ PUB.
- Main process consumers read frame bytes from shared memory using metadata.

Do not move the camera SDK into the main process, and do not send full image
payloads through ZMQ.

## Camera Backend Dependency

The active backend is `flycapture2_c`, replacing the old `pyflycap2 + GUI`
path.

Important details:

- target repository: `Mr-enthalpy/flycapture2_c`;
- Python import package: `flycapture2_c`;
- package/distribution name may appear as `flycapture2-c`;
- do not use `flycapture_c`;
- the FlyCapture2 vendor SDK and runtime DLLs are not bundled;
- hardware machines must install the FlyCapture2 SDK/runtime separately;
- default tests must not require the SDK, DLLs, or a real camera.

Local development can use the sibling checkout if present:

```powershell
$env:PYTHONPATH = "C:\Users\teacher H\PycharmProjects\flycapture2_c"
```

For installed environments, install the wrapper into the sidecar Python
environment instead:

```powershell
python -m pip install C:\Users\teacher H\PycharmProjects\flycapture2_c
```

When the FlyCapture2 SDK is not in the default location, set one or both:

```powershell
$env:FLYCAPTURE2_SDK_DIR = "C:\Program Files\Point Grey Research\FlyCapture2"
$env:FLYCAPTURE2_DLL_DIR = "C:\Program Files\Point Grey Research\FlyCapture2\bin64"
```

## Python Environment

Main GUI/runtime dependencies are listed in [requirements.txt](requirements.txt):

```powershell
python -m pip install -r requirements.txt
```

The sidecar launcher still supports selecting a specific Python command:

```powershell
$env:PY38_BIN = "C:\Path\To\Python38\python.exe"
```

Use this only when the camera SDK wrapper is installed in a dedicated sidecar
environment.

## Startup

Default GUI startup:

```powershell
python -m app.main_gui
```

Current intended startup behavior:

1. connect to or launch the camera sidecar;
2. open the camera through `OpenCamera` with explicit startup configuration;
3. main GUI startup requests `disable_trigger=true` through `OpenCamera`;
4. sidecar applies explicit scriptable configuration if supplied;
5. sidecar starts capture and reads a first frame to determine layout;
6. main process starts the stream;
7. preview worker consumes PUB metadata plus shared memory frames;
8. LCD initializes and defaults to all-transmissive when configured;
9. GUI opens as the frontend.

`PreConfigGUI` is deprecated. It no longer opens FlyCapture GUI and should only
be treated as a compatibility RPC that returns a structured error pointing to
explicit replacement operations such as `DisableTrigger`, `SetPixelFormat`,
`SetROI`, `SetProperty`, `SetPropertyAuto`, and `SnapshotProperties`.

Common options:

```powershell
python -m app.main_gui --disable-lcd
python -m app.main_gui --no-auto-sidecar
python -m app.main_gui --lcd-display-index 1
python -m app.main_gui --lcd-transmissive-code 255 --lcd-opaque-code 0
```

## Useful Environment Variables

### `SIDECAR`

Override the sidecar script path. Default:

```text
devices/camera_service_impl.py
```

### `CAMERA_SERVICE_LOG`

Capture sidecar stdout/stderr to a log file:

```powershell
$env:CAMERA_SERVICE_LOG = "camera_service.log"
```

### `CAMERA_SERVICE_DEBUG`

Inherit sidecar stdout/stderr in the launching console:

```powershell
$env:CAMERA_SERVICE_DEBUG = "1"
```

### `CAM_BAYER_PATTERN`

Explicitly enable raw Bayer preview conversion. Supported values:

- `BG`
- `GB`
- `RG`
- `GR`

Example:

```powershell
$env:CAM_BAYER_PATTERN = "GR"
```

If this variable is unset and frame metadata does not include `bayer_pattern`,
raw8/raw16 preview uses a mono fallback instead of assuming a universal Bayer
layout.

## LCD Representation

The LCD physical mask is mono subpixel data:

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

All physical mask reasoning should use `[H, 3W]`. Only `LCDService` should pack
physical mono masks into RGB display buffers.

## Tests

Default no-hardware tests:

```powershell
py -3.12 -m pytest -q
```

Hardware tests are opt-in:

```powershell
$env:OPTIC_SYSTEM_HARDWARE_TEST = "1"
$env:OPTIC_SYSTEM_CAMERA_INDEX = "0"
$env:OPTIC_SYSTEM_FRAME_COUNT = "30"
py -3.12 -m pytest tests/hardware/test_camera_service_flycapture2_backend.py -q
```

No default test should require a real camera, FlyCapture2 DLLs, or installed
vendor SDK.

## More Documentation

- [Camera service FlyCapture2 C migration](docs/camera_service_flycapture2_migration.md)
