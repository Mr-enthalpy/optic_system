# Camera Service FlyCapture2 C Migration

This migration replaces the old `pyflycap2 + GUI` sidecar backend with the
headless `flycapture2_c` package while keeping the existing process and frame
transport architecture:

- camera SDK calls stay inside the sidecar process, primarily through
  `devices/camera_backend_flycapture2.py`;
- the main process still controls the sidecar through ZMQ REQ/REP;
- frames are still written by the sidecar into a shared-memory ring buffer;
- PUB messages still carry frame metadata, not image payloads.

## What Changed

The old backend imported:

```python
from pyflycap2.interface import Camera, GUI
```

The new backend imports:

```python
from flycapture2_c import Camera
```

`PreConfigGUI` is now deprecated. It no longer opens a GUI and returns:

```json
{
  "ok": false,
  "err": "PreConfigGUI is deprecated. Use explicit camera configuration commands instead.",
  "replacement_ops": [
    "DisableTrigger",
    "SetPixelFormat",
    "SetROI",
    "SetProperty",
    "SetPropertyAuto",
    "SnapshotProperties"
  ]
}
```

`PreConfigGUI` is not part of the normal startup path. The main GUI constructs
`SessionController` with `preconfigure=False` and opens the camera through
explicit protocol fields instead of launching the vendor GUI first.

The old two-step workflow:

1. open FlyCapture GUI;
2. manually configure camera;
3. return to `optic_system` and open the camera;

has been removed from default GUI startup. Headless startup now uses
scriptable commands such as `OpenCamera` with `disable_trigger=true`,
`SetPixelFormat`, `SetROI`, `SetProperty`, and `SetPropertyAuto`.

## Dependency Notes

`flycapture2_c` is an external camera SDK wrapper, not part of this repository.
The target repository is `Mr-enthalpy/flycapture2_c`, and the Python import name
is `flycapture2_c`. Do not use `flycapture_c`.

The package/distribution name may appear as `flycapture2-c`, but code should
import:

```python
from flycapture2_c import Camera
```

The wrapper does not bundle the vendor FlyCapture2 SDK, runtime DLLs, drivers,
headers, libraries, or sample binaries. Hardware machines must install the
FlyCapture2 SDK separately and make the C runtime discoverable. Typical
environment variables are:

```powershell
$env:FLYCAPTURE2_SDK_DIR = "C:\Program Files\Point Grey Research\FlyCapture2"
$env:FLYCAPTURE2_DLL_DIR = "C:\Program Files\Point Grey Research\FlyCapture2\bin64"
```

The sidecar defaults to the same Python environment as the main process
(`sys.executable`). `flycapture2_c` supports Python 3.12, so the sidecar no
longer needs a dedicated Python 3.8 runtime.

Install `flycapture2_c` into the same Python environment that runs
`optic_system`:

```powershell
python -m pip install -e "C:\Users\teacher H\PycharmProjects\flycapture2_c"
```

Verify the import from that same environment:

```powershell
python -c "import flycapture2_c; print(flycapture2_c.__file__)"
```

If a deployment needs a generic sidecar interpreter override, set
`OPTIC_SYSTEM_SIDECAR_PYTHON`. The old `PY38_BIN` variable belonged only to the
historical `pyflycap2` backend path and is ignored by the `flycapture2_c`
launcher.

Importing `flycapture2_c` is designed to be lightweight, but real camera
operations still require the vendor SDK/runtime. Default tests in this
repository must therefore use fake backends and must not require FlyCapture2
DLLs or hardware.

If the sidecar cannot import `flycapture2_c`, it should report a structured,
readable error that names package `flycapture2_c` and repository
`Mr-enthalpy/flycapture2_c`, along with `sys.executable`, `sys.version`,
`sys.path`, `PYTHONPATH`, `OPTIC_SYSTEM_SIDECAR_PYTHON`,
`FLYCAPTURE2_SDK_DIR`, `FLYCAPTURE2_DLL_DIR`, and the original
`flycapture2_c` import error.

## OpenCamera Flow

The new `OpenCamera` flow is:

1. `Camera.open(index)`
2. read camera info and basic capabilities
3. disable trigger only when `disable_trigger` is explicitly `true`
4. apply optional configuration
5. `start()`
6. read the first frame to determine shared-memory layout

Supported request fields:

```json
{
  "op": "OpenCamera",
  "index": 0,
  "context_type": "IIDC",
  "disable_trigger": true,
  "grab_timeout_ms": 1000,
  "pixel_format": "RAW8",
  "roi": {
    "offset_x": 0,
    "offset_y": 0,
    "width": 1024,
    "height": 768
  },
  "properties": [
    {"name": "SHUTTER", "value": 5.0, "auto": false},
    {"name": "GAIN", "value": 0.0, "auto": false}
  ]
}
```

`context_type` remains accepted for compatibility but is not required by
`flycapture2_c`. If `disable_trigger` is omitted or false, `OpenCamera` does not
modify trigger state. The reply includes `configuration_applied`, including a
trigger summary such as:

```json
{
  "disable_trigger": {
    "requested": false,
    "applied": false
  }
}
```

## Protocol Ops

Existing compatibility ops:

- `Ping`
- `OpenCamera`
- `GetCameraInfo`
- `StartStream`
- `StopStream`
- `SetProperty`
- `GetRange`
- `GetValue`
- `CloseCamera`
- `PreConfigGUI` deprecated, returns a structured error

New or expanded ops:

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

`CloseCamera` closes the camera and shared memory but keeps the sidecar service
running. `Shutdown` closes the camera and exits the sidecar.

## Trigger Control

Trigger is not changed implicitly during `OpenCamera`. To replace the old GUI
pre-configuration workflow, set `"disable_trigger": true` in `OpenCamera` or
send `DisableTrigger` explicitly after opening the camera.

Open with trigger disable:

```json
{"op": "OpenCamera", "index": 0, "disable_trigger": true}
```

Explicit disable:

```json
{"op": "DisableTrigger"}
```

Explicit trigger configuration:

```json
{
  "op": "SetTriggerMode",
  "on_off": false,
  "source": 0,
  "mode": 0,
  "polarity": 1,
  "parameter": 0
}
```

Read current trigger state:

```json
{"op": "GetTriggerMode"}
```

## Properties

Snapshot all known properties:

```json
{"op": "SnapshotProperties"}
```

Read one property capability:

```json
{"op": "GetPropertyInfo", "name": "SHUTTER"}
```

Set manual absolute value:

```json
{"op": "SetProperty", "name": "SHUTTER", "value": 5.0}
```

`SetProperty` uses the absolute-value property path and sets manual mode by
default. If the property does not support absolute values, the sidecar returns a
structured unsupported error instead of reporting success.

Set auto/manual:

```json
{"op": "SetPropertyAuto", "name": "SHUTTER", "auto": false}
```

`GetRange` remains compatible with old callers and returns `range`. It now also
returns `units`, `integer_range`, and `abs_supported` when available.

## Pixel Format And ROI

`flycapture2_c` now exposes a machine-readable
`flycapture2_c.pixel_format.PIXEL_FORMAT_SUPPORT` matrix. `optic_system` should
use that matrix when it is available instead of carrying local guesses about
which SDK pixel formats are safe.

The important distinction is:

- camera-configurable: the camera/SDK may accept the format in Format7 or GigE
  configuration;
- `read_frame()` decodable: `flycapture2_c` can convert the SDK image buffer
  into an owned structured NumPy array.

Only `read_frame()` decodable formats are safe for this sidecar's shared-memory
stream. Known-but-undecoded formats should return a structured unsupported
error before changing camera configuration.

Current `flycapture2_c` decoded formats are:

- `MONO8`
- `MONO16`
- `RAW8`
- `RAW16`
- `RGB8` / `RGB`

Earlier `optic_system` integration made several conservative compromises while
pixel-format support was still unsettled. These should not be preserved as
requirements:

- do not infer `BGR8` from a generic three-channel frame;
- do not treat `RGBU`, `BGR`, `BGRU`, or 16-bit color variants as decoded just
  because the SDK enum is known;
- do not assume Format7 bitfield membership means the frame can be read by
  `read_frame()`;
- do not silently reinterpret unknown SDK pixel-format integers as raw mono
  data.

RGB8 frames from `flycapture2_c` are RGB arrays with shape
`[height, width, 3]`, dtype `uint8`, and stream metadata `format: "rgb8"`.
Downstream preview code may convert RGB to BGR for OpenCV display, but the
shared-memory metadata should preserve the SDK/wrapper layout.

Set pixel format:

```json
{"op": "SetPixelFormat", "pixel_format": "RAW8", "mode": 0}
```

Set ROI through Format7:

```json
{
  "op": "SetROI",
  "offset_x": 0,
  "offset_y": 0,
  "width": 1024,
  "height": 768,
  "mode": 0
}
```

`ReconfigureCamera` can apply `disable_trigger`, `grab_timeout_ms`,
`pixel_format`, `roi`, and `properties` while running. Pixel format and ROI are
validated before configuration changes are applied. Unsupported or
known-but-undecoded pixel formats return a structured error and leave the camera
configuration unchanged.

Successful replies include:

```json
{
  "ok": true,
  "old_layout": {"format": "raw8"},
  "new_layout": {"format": "raw16"},
  "layout_changed": true,
  "shm_recreated": true
}
```

When the layout changes, the sidecar recreates shared memory and publishes a
status event. Subscribers can also recover from the new frame metadata because
every frame includes the shared-memory name, ring size, shape, dtype, row bytes,
and total frame byte count.

## Bayer Preview Policy

Raw frame metadata may include `bayer_pattern` when the camera configuration or
backend can state it explicitly. Valid values are `BG`, `GB`, `RG`, and `GR`.

The preview consumer no longer assumes `GR` as a universal default. If a
`raw8` or `raw16` frame has no valid `bayer_pattern`, preview uses a mono
fallback and records a `preview_warning` in packet metadata. Debayering is used
only when metadata or `CAM_BAYER_PATTERN` explicitly provides a supported Bayer
pattern.

## Frame Metadata

Frame PUB messages still use topic `frame`. The JSON metadata now includes the
old fields plus enough layout information for downstream shared-memory readers:

```json
{
  "protocol_version": 2,
  "backend": "flycapture2_c",
  "shm": "flycap2_ring_A",
  "ring_size": 8,
  "index": 0,
  "seq": 123,
  "width": 1024,
  "height": 768,
  "stride": 1024,
  "row_bytes": 1024,
  "frame_nbytes": 786432,
  "dtype": "uint8",
  "shape": [768, 1024],
  "pixel_format": "RAW8",
  "format": "raw8",
  "ts_ns": 123456789,
  "dropped_frames": 0
}
```

For 16-bit frames, `dtype` is `uint16`, `row_bytes` is `width * 2`, and
`frame_nbytes` is `width * height * 2`. `RGB8` frames use
`shape: [height, width, 3]` and `format: "rgb8"`. `bgr8` remains a protocol
value for a future backend that explicitly reports BGR, but it is not produced
by current `flycapture2_c` RGB decode.

## Logging

The sidecar launcher no longer only supports silent startup. Set:

```powershell
$env:CAMERA_SERVICE_LOG = "camera_service.log"
```

to capture sidecar stdout/stderr. Set:

```powershell
$env:CAMERA_SERVICE_DEBUG = "1"
```

to inherit stdout/stderr in the launching console.

## Windows / PyCharm SDK Configuration

If the FlyCapture2 SDK is not placed under `flycapture2_c/third_party/FlyCapture2/`,
you **must** set `FLYCAPTURE2_SDK_DIR` and `FLYCAPTURE2_DLL_DIR` explicitly before
starting any Python process that will touch the SDK (including the sidecar).

### Why explicit settings may be needed

`flycapture2_c` searches for the SDK in this order:

1. `FLYCAPTURE2_SDK_DIR` if set (env var);
2. the project-local `<flycapture2_c repo>/third_party/` directory;
3. `<FLYCAPTURE2_SDK_DIR>/FlyCapture2` (if the configured path is a parent container).

If the SDK was installed by the vendor to a system location such as
`D:\Program Files\Point Grey Research\FlyCapture2` and no copy exists under
`third_party/`, the auto-discovery will fail and you will see
`SDKNotFoundError`.

### Required environment variables

```powershell
$env:FLYCAPTURE2_SDK_DIR = "D:\Program Files\Point Grey Research\FlyCapture2"
$env:FLYCAPTURE2_DLL_DIR = "D:\Program Files\Point Grey Research\FlyCapture2\bin64\vs2015"
```

Set these in the same shell before running `optic_system` or the hardware tests.
Both the sidecar and the main process must be able to see them.

### PyCharm Run Configuration

PyCharm run configurations have their own environment variable section.
Configuring the system or shell environment is **not enough** -- you must also:

1. Open **Run → Edit Configurations**.
2. Select the run configuration (e.g. the hardware test, `app.main_gui`, or
   the camera service impl).
3. In **Environment variables**, add (one per line):

   ```text
   FLYCAPTURE2_SDK_DIR=D:\Program Files\Point Grey Research\FlyCapture2
   FLYCAPTURE2_DLL_DIR=D:\Program Files\Point Grey Research\FlyCapture2\bin64\vs2015
   ```

4. Click **Apply** and re-run.

If you use a **Python** or **Python tests** run configuration template, set the
variables in the template so every new configuration inherits them.

### Verifying SDK visibility

From the **same Python environment** that runs `optic_system`:

```powershell
python -c "from flycapture2_c.api import get_api; v = get_api().get_library_version(); print(f'Library {v[0]}.{v[1]}.{v[2]}.{v[3]}')"
```

If this prints a version number, the SDK is discoverable. If it fails with
`SDKNotFoundError` or `DLLLoadError`, check:

- the current values of `FLYCAPTURE2_SDK_DIR` and `FLYCAPTURE2_DLL_DIR` in the
  shell;
- that the SDK directory contains `include/C/FlyCapture2_C.h` (and the other
  required headers);
- that the DLL directory contains `FlyCapture2_C_v140.dll` (or a similar match);
- that the PyCharm run configuration has the same variables set.

### Sidecar environment inheritance

The sidecar is launched as a subprocess by `camera_service.py` using the **same
Python interpreter** (`sys.executable`). The sidecar inherits the **current
process environment**, so environment variables set before `optic_system` starts
are visible to both processes.

If you set environment variables inside a PyCharm run configuration, the sidecar
inherits them automatically. If you use a **terminal** outside PyCharm, set
them in that terminal's session before launching.

### Error diagnostics

When the sidecar fails to find the SDK, the error reply now includes a
structured `sdk_diagnostics` field:

```json
{
  "ok": false,
  "err": "FlyCapture2 SDK headers were not found. Current FLYCAPTURE2_SDK_DIR=...",
  "error_type": "SDKNotFoundError",
  "op": "OpenCamera",
  "recoverable": true,
  "sdk_diagnostics": {
    "FLYCAPTURE2_SDK_DIR": null,
    "FLYCAPTURE2_DLL_DIR": null,
    "suggested_sdk_dir_examples": [
      "D:\\Program Files\\Point Grey Research\\FlyCapture2",
      "C:\\Program Files\\Point Grey Research\\FlyCapture2"
    ],
    "suggested_dll_dir_examples": [
      "D:\\Program Files\\Point Grey Research\\FlyCapture2\\bin64\\vs2015",
      "C:\\Program Files\\Point Grey Research\\FlyCapture2\\bin64\\vs2015"
    ]
  }
}
```

Use `sdk_diagnostics` to quickly see which environment variables (if any) are
currently set and what examples are appropriate for your machine.

## Testing Status

This round adds no-hardware tests for protocol payloads, JSON serialization,
property snapshots, frame metadata, import-error handling, OpenCamera trigger
semantics, ReconfigureCamera layout-change replies, unsupported pixel-format
validation, safe raw Bayer preview fallback, stream idempotency, shared-memory
release on `CloseCamera`, and `CloseCamera` versus `Shutdown` semantics.

Hardware smoke was run with `OPTIC_SYSTEM_HARDWARE_TEST=1`; see
`Hardware Smoke Summary` below for the local result.

Next hardware validation command:

```powershell
$env:OPTIC_SYSTEM_HARDWARE_TEST = "1"
$env:OPTIC_SYSTEM_CAMERA_INDEX = "0"
$env:OPTIC_SYSTEM_FRAME_COUNT = "30"
py -3.12 -m pytest tests/hardware/test_camera_service_flycapture2_backend.py -q
```

## Hardware Smoke Summary

Local hardware smoke was run on 2026-05-08.

First run:

```powershell
$env:OPTIC_SYSTEM_HARDWARE_TEST = "1"
$env:OPTIC_SYSTEM_CAMERA_INDEX = "0"
$env:OPTIC_SYSTEM_FRAME_COUNT = "30"
py -3.12 -m pytest tests/hardware/test_camera_service_flycapture2_backend.py -q
```

Result: failed before hardware access because the launched sidecar environment
could not import package `flycapture2_c`.

Second run installed the sibling `flycapture2_c` checkout into the same Python
3.12 environment used by `optic_system` and pointed the sidecar at the installed
FlyCapture2 SDK/runtime under `D:\Program Files\Point Grey Research`:

```powershell
python -m pip install -e "C:\Users\teacher H\PycharmProjects\flycapture2_c"
python -c "import flycapture2_c; print(flycapture2_c.__file__)"
$env:FLYCAPTURE2_SDK_DIR = "D:\Program Files\Point Grey Research\FlyCapture2"
$env:FLYCAPTURE2_DLL_DIR = "D:\Program Files\Point Grey Research\FlyCapture2\bin64\vs2015"
$env:OPTIC_SYSTEM_HARDWARE_TEST = "1"
$env:OPTIC_SYSTEM_CAMERA_INDEX = "0"
$env:OPTIC_SYSTEM_FRAME_COUNT = "30"
py -3.12 -m pytest tests/hardware/test_camera_service_flycapture2_backend.py -q
```

Result:

```text
.                                                                        [100%]
1 passed in 3.31s
```

This validates the sidecar can import `flycapture2_c`, open camera index 0 with
explicit `disable_trigger=true`, start the shared-memory stream, receive 30
frame metadata events, read frame bytes through shared memory, stop streaming,
close the camera, and shut down the sidecar on this hardware machine.

The older `PY38_BIN` override was only for the retired `pyflycap2` backend. The
`flycapture2_c` backend works with Python 3.12 and the sidecar should normally
share the main process environment.

## Lifecycle Invariants

These invariants were established during the `INVALID_GENERATION` cleanup
hardening (May 2026) and must be preserved by any future changes to the camera
open/close/reconfigure paths.

### Cleanup path

- **`MyCamLite.close()` must call only `Camera.close()`.** It must not call
  `stop_capture()` or `Camera.stop()`.
- **`Camera.close()` is the sole cleanup API.** It performs best-effort stop,
  disconnect, and resource teardown. Failures are collected in
  `cleanup_errors`, not raised.
- **`_close_camera_locked()` must never raise** during pre-open cleanup. If
  cleanup produces errors, they are returned as a list of strings and appear in
  the OpenCamera response as `cleanup_warnings` or `cleanup_errors` -- never as
  `primary_error`.

### Explicit stop

- **`Camera.stop()` is an explicit business operation** and may propagate real
  SDK errors (including `INVALID_GENERATION`).
- **`stop_capture()` is guarded by `is_capturing`** -- it is a no-op when the
  camera is not capturing.
- Explicit stop is used only by `StopStream`, `StopCapture` (implicit in
  `ReconfigureCamera`), and the stream loop error path.

### Error reporting

- **OpenCamera failures report `stage` and `primary_error` separately.**
  `cleanup_errors` (from pre-open cleanup or post-failure close) are a distinct
  field.
- **`cleanup_errors` are diagnostic warnings**, not primary failures. They must
  not cause `ok: false` on `CloseCamera`/`Shutdown` if the close workflow
  completed.
- **Ping returns backend diagnostic fields** (`flycapture2_c_file`,
  `camera_class_file`, `has_cleanup_errors`, `python_executable`,
  `service_file`). These fields must remain available to distinguish the
  actually-running sidecar from stale processes or stale package installs.

### Reconfigure

- **`_reconfigure_locked()` must not restart capture after a failed
  configuration.** The camera is left stopped (`state.running = False`); the
  caller must explicitly `StartStream` or retry.

### INVALID_GENERATION classification

If `fc2StopCapture failed: INVALID_GENERATION (20)` appears again, classify by
context:

| Location | Classification |
|---|---|
| `primary_error` of an explicit `StopStream` / `StopCapture` | Real business stop failure |
| `cleanup_errors` of `CloseCamera` / `Shutdown` / `OpenCamera` | Cleanup warning -- acceptable |
| `primary_error` of `OpenCamera` with `stage == "close_existing_camera"` | Regression -- cleanup is leaking into primary |
| `primary_error` of any op, `backend != "flycapture2_c"` | Old sidecar / old `pyflycap2` path |
