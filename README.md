# optic_system

`optic_system` is an in-progress refactor of an optical experiment control
system. The current work focuses on stable device boundaries, explicit control
semantics, camera preview, and minimal LCD control. It is not yet a complete
experiment automation system.

## Canonical Instructions

Project instructions live in [AGENTS.md](AGENTS.md).

That file is now the single source of truth for architecture boundaries, staged
goals, camera sidecar rules, `flycapture2_c` dependency notes, LCD conventions,
TLS architecture rules, and success criteria.

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
- TLS service wrapper via `tls_c1` / `TLSC1` SDK facade;
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
devices/    camera sidecar client/implementation, frame stream, LCD service, TLS service
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

Install the wrapper into the same Python environment that runs `optic_system`:

```powershell
python -m pip install -e "C:\Users\teacher H\PycharmProjects\flycapture2_c"
```

Verify the import from that same environment:

```powershell
python -c "import flycapture2_c; print(flycapture2_c.__file__)"
```

When the FlyCapture2 SDK is not in the default location, set one or both:

```powershell
$env:FLYCAPTURE2_SDK_DIR = "C:\Program Files\Point Grey Research\FlyCapture2"
$env:FLYCAPTURE2_DLL_DIR = "C:\Program Files\Point Grey Research\FlyCapture2\bin64"
```

## TLS Architecture

TLS control has been migrated from `pywinauto` GUI automation to an SDK-based
wrapper architecture. The new path replaces the legacy GUI automation approach
with explicit device boundaries.

### TLS Dependency

`optic_system` treats [Mr-enthalpy/tls_c1](https://github.com/Mr-enthalpy/tls_c1)
as an optional dependency. It is not required for basic GUI startup.

Rules:

- Without TLS hardware, vendor DLLs, or `tls_c1` installed, the project base
  imports and no-hardware tests must still run.
- `tls_c1` is only needed when you enable a TLS backend or run future TLS
  hardware smoke tests.
- Only `devices/tls_service.py` may import `tls_c1`, and only through a lazy
  import using the high-level `tls_c1` / `TLSC1` facade.
- Do not copy `tls_c1` source into `optic_system`, and do not commit vendor DLLs
  into this repository.

### TLS Modules

- `devices/tls_service.py`
  - Thin wrapper around `tls_c1` / `TLSC1` SDK facade
  - Lazy import: does not break base imports when `tls_c1` is not installed
  - Unified boundary for TLS connect, target wavelength, grating, move, and
    status queries
- `control/session_controller.py`
  - Handles TLS commands
  - Publishes TLS events and updates shared state

Do not call `TLSService` directly from GUI widgets. GUI sends intent through
`control`, which owns TLS semantics.

### Installing tls_c1

Option A: install directly from GitHub

```powershell
python -m pip install git+https://github.com/Mr-enthalpy/tls_c1.git
```

Option B: clone locally, then install editable

```powershell
git clone https://github.com/Mr-enthalpy/tls_c1.git .\third_party\tls_c1
python -m pip install -e .\third_party\tls_c1
```

If the upstream `tls_c1` requires a local SDK/DLL path, set `TLS_C1_SDK_DIR` on
your machine as documented by `tls_c1`. Do not commit DLLs into this repository.

## Python Environment

Main GUI/runtime dependencies are listed in [requirements.txt](requirements.txt):

```powershell
python -m pip install -r requirements.txt
```

The camera sidecar defaults to the same Python interpreter running
`optic_system` (`sys.executable`). If a deployment needs an explicit interpreter
override, use:

```powershell
$env:OPTIC_SYSTEM_SIDECAR_PYTHON = "C:\Path\To\Python\python.exe"
```

This is a generic override and has no Python 3.8 meaning. The old `PY38_BIN`
variable belonged to the retired `pyflycap2` backend path and is ignored by the
`flycapture2_c` sidecar launcher.

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

### `OPTIC_SYSTEM_SIDECAR_PYTHON`

Optional generic Python command override for launching the sidecar. By default,
the launcher uses `sys.executable`.

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

### `TLS_C1_SERIAL`

Device serial number for TLS hardware smoke and future integration smoke tests.
Default unit tests do not read or require this variable.

### `TLS_C1_SDK_DIR`

TLS vendor SDK / DLL directory, per `tls_c1` upstream convention. This is a
local-machine environment variable. Do not substitute it by committing DLLs
into `optic_system`.

### `TLS_C1_SAFE_GRATING`

Safe grating number used by future hardware smoke tests. Should be provided
explicitly via environment variable.

### `TLS_C1_SAFE_WAVELENGTH_NM`

Safe target wavelength (nm) used by future hardware smoke tests. Should be
provided explicitly via environment variable.

### `TLS_C1_RUN_HARDWARE_TESTS`

Only when set to `1` will `tests/test_tls_hardware_smoke.py` run. Default tests
are always no-hardware.

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

TLS no-hardware tests (require no hardware, no vendor DLLs):

```powershell
python -m pytest tests/test_tls_service.py tests/test_tls_controller.py
```

If you are developing the TLS control layer without real hardware, you typically
only need:

1. install the base project dependencies;
2. do not install `tls_c1`, or install `tls_c1` without setting
   `TLS_C1_SDK_DIR`;
3. run no-hardware tests to validate wrapper and controller semantics.

Camera hardware tests are opt-in:

```powershell
$env:OPTIC_SYSTEM_HARDWARE_TEST = "1"
$env:OPTIC_SYSTEM_CAMERA_INDEX = "0"
$env:OPTIC_SYSTEM_FRAME_COUNT = "30"
py -3.12 -m pytest tests/hardware/test_camera_service_flycapture2_backend.py -q
```

TLS hardware smoke test entry point (future):

```powershell
$env:TLS_C1_SDK_DIR = "C:\Path\To\VendorSDK"
$env:TLS_C1_RUN_HARDWARE_TESTS = "1"
$env:TLS_C1_SERIAL = "YOUR_SERIAL"
$env:TLS_C1_SAFE_GRATING = "1"
$env:TLS_C1_SAFE_WAVELENGTH_NM = "550.0"
python -m pytest tests/test_tls_hardware_smoke.py
```

No default test should require a real camera, TLS hardware, FlyCapture2 DLLs,
or installed vendor SDKs.

## Development Notes

- Preserve the `gui -> control -> devices / capture` dependency direction.
- Do not push control logic back into GUI.
- TLS must only access `tls_c1` through `devices/tls_service.py`.
- GUI must not call `TLSService` directly.
- Do not modify `old/`.
- Reference [AGENTS.md](AGENTS.md) for the authoritative constraint set.

## More Documentation

- [Camera service FlyCapture2 C migration](docs/camera_service_flycapture2_migration.md)
