# Camera Service FlyCapture2 C Migration

This migration replaces the old `pyflycap2 + GUI` sidecar backend with the
headless `flycapture2_c` package while keeping the existing process and frame
transport architecture:

- camera SDK calls stay inside `devices/camera_service_impl.py`;
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

The sidecar Python environment must be able to import `flycapture2_c`. In local
development this may be done from the sibling checkout:

```powershell
$env:PYTHONPATH = "C:\Users\teacher H\PycharmProjects\flycapture2_c"
```

For a real deployment, install the package into the sidecar environment:

```powershell
python -m pip install C:\Users\teacher H\PycharmProjects\flycapture2_c
```

Importing `flycapture2_c` is designed to be lightweight, but real camera
operations still require the vendor SDK/runtime. Default tests in this
repository must therefore use fake backends and must not require FlyCapture2
DLLs or hardware.

If the sidecar cannot import `flycapture2_c`, it should report a structured,
readable error that names package `flycapture2_c` and repository
`Mr-enthalpy/flycapture2_c`.

## OpenCamera Flow

The new `OpenCamera` flow is:

1. `Camera.open(index)`
2. read camera info and basic capabilities
3. `disable_trigger()` by default
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
`flycapture2_c`.

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

Trigger is disabled by default during `OpenCamera`.

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
`pixel_format`, `roi`, and `properties` while running. It stops streaming,
stops capture, applies configuration, restarts capture, reads a fresh frame
layout, and recreates shared memory when the layout changed.

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

## Testing Status

This round adds no-hardware tests for protocol payloads, JSON serialization,
property snapshots, frame metadata, import-error handling, stream idempotency,
and `CloseCamera` versus `Shutdown` semantics.

No real camera hardware was exercised in this round.

Next hardware validation command:

```powershell
$env:OPTIC_SYSTEM_HARDWARE_TEST = "1"
$env:OPTIC_SYSTEM_CAMERA_INDEX = "0"
$env:OPTIC_SYSTEM_FRAME_COUNT = "30"
python -m pytest tests/hardware/test_camera_service_flycapture2_backend.py -q
```
