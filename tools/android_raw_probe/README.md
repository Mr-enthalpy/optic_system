# Android Camera2 RAW Probe

This is the separately authorized Axon 20 real-device experiment. It is a standalone Android app and local inspection tool, not an adapter for the laboratory camera controller or its RawCapture/PSF pipeline. It does not alter that pipeline, the LCD prototype's conclusions, or reconstruction evaluation.

No root, bootloader commands, reboot, EDL, HAL changes, network permission or system-partition writes. Only CAMERA permission and the debug application's own files are used. Install/update and `run-as` export require an authorized ADB connection. Do not uninstall or clear app data while measurements remain on the phone.

## Build and install (Windows / PowerShell 7)

Inputs: JDK 8, Android SDK platform 30 and build-tools 30.0.3. Build uses the official SDK's javac/D8/aapt2/zipalign/apksigner directly; Gradle and Android Studio are not required. Install SDK components from Google's official distribution under its license. The package has minSdk 28, targetSdk 30 and a local debug signature. It is research software, not a store release.

From the `optic_system` repository:

```powershell
# Use actual local SDK/JDK/ADB paths. Outputs are intentionally outside Git.
$adb = 'C:\path\to\platform-tools\adb.exe'
$python = '.\.venv\Scripts\python.exe'
& .\tools\android_raw_probe\build.ps1 -SdkRoot 'C:\path\to\sdk' `
    -JdkRoot 'C:\path\to\jdk8' -OutputDirectory '.\outputs\android-probe-build'
# Set $apk to the APK path printed by the successful build.
& $python .\tools\android_raw_probe\device.py --adb $adb status
& $python .\tools\android_raw_probe\device.py --adb $adb install $apk
& $python .\tools\android_raw_probe\device.py --adb $adb enumerate
```

A successful build includes APK signature verification and `build_manifest.json` with APK SHA256, source commit/dirty status and a digest over the manifest and Java sources. A debug signing key is generated once in the supplied output directory and reused. Keep that directory private; changing its key prevents updating an installed copy with `-r`. Do not solve signature mismatch by uninstalling a copy containing unexported data.

The app enumerates **all public CameraManager IDs**, retains all exposed characteristics and lists FRONT IDs. No camera ID is hardcoded. Automatic capture refuses multiple front candidates unless `--camera-id` identifies an enumerated FRONT ID. UI capture uses the operator's selection. HAL-only IDs need not be visible to a normal app.

## Capture

Unlock the phone screen and keep the app foreground. No preview is required. Select RAW_SENSOR first; RAW10 is a separate run. The app uses 3264x2448 when advertised, otherwise explicitly records the selected largest available RAW size and `target_raw_size_available=false`.

```powershell
# Initial availability/control smoke test; does not pretend the scene is dark/flat/point.
& $python .\tools\android_raw_probe\device.py --adb $adb capture `
    --format RAW_SENSOR --exposure-us 10000 --iso 100 --count 10
```

The UI also supports a batch without ADB. Requested values outside reported limits are rejected rather than silently clamped. CONTROL_MODE/AE/AWB are OFF. Available AF, noise reduction, edge, hot-pixel and shading OFF controls are requested, and a lens shading map is requested if advertised. This does not disable corrections embedded in the RAW path. Requested frame duration accounts for advertised stream timing and exposure; readback remains authoritative.

Per-frame exposure/ISO/AE readback is retained, alongside the request. A convenience check uses exposure error <=2%, ISO error <=max(1,1%) and AE OFF. This tolerance is a **control diagnostic**, not a radiometric accuracy or scientific acceptance threshold. The Python tool recomputes it. ISO is not a calibrated analog/digital gain separation; post-RAW sensitivity boost and exposed vendor fields are retained if provided, otherwise the gain decomposition is unknown.

During each batch, app content is fixed black with dim static text at the bottom. It waits 500 ms before opening the camera and records brightness settings without changing them. OS bars, automatic brightness behavior and the vendor-controlled UDC display patch are not verified. For physical tests, use a repeatable brightness setting, record it and do not interact with the UI during capture. This fixed app display state is not dynamic display coding.

Paired attempts use **one CaptureRequest targeting RAW plus JPEG or YUV surfaces**:

```powershell
& $python .\tools\android_raw_probe\device.py --adb $adb capture `
    --format RAW_SENSOR --pair JPEG --exposure-us 10000 --iso 100 --count 10
# Test YUV independently; failure does not trigger a different-exposure fallback.
& $python .\tools\android_raw_probe\device.py --adb $adb capture `
    --format RAW_SENSOR --pair YUV --exposure-us 10000 --iso 100 --count 10
# Separate packed RAW10 attempt.
& $python .\tools\android_raw_probe\device.py --adb $adb capture `
    --format RAW10 --exposure-us 10000 --iso 100 --count 10
```

Companion size prefers advertised 640x480, otherwise the smallest advertised size. Session/stream rejection, capture failure, buffer loss or timestamp mismatch is terminal for that run. No RAW+JPEG/YUV success is claimed until images and the TotalCaptureResult share the exact sensor timestamp. RAW+processed success does not prove identical ISP treatment or RAW optical fidelity.

## Physical sequence (operator required)

Never select `--condition-verified` until the actual optical condition is in place. The app records an operator annotation, not independent verification by software. Record source wavelength/bandwidth if known, source distance/aperture, alignment, display state, ambient illumination and source stabilization. Do not infer a wavelength from an RGB preview.

1. **Front identity and dark:** verify that the chosen physical camera is the screen-side UDC, and record the observation. Use a dark enclosure or external occlusion with no device modification; include possible OLED light leakage in the notes. Capture >=10 frames at fixed exposure/ISO.
2. **Uniform illumination:** hold source, geometry, display state and ISO fixed. Collect >=3 exposures with >=3 repeats each (prefer 10); collect matching dark batches at the same exposure/ISO. Starting candidates are 1/2/4 ms at ISO 100, but actual intensity must determine an unsaturated range. A global linearity fit does not prove every pixel/channel is linear.
3. **Point source:** use an actual narrow-band point source and fixed geometry; capture >=10 unsaturated frames. Inspect each Bayer parity tile and repeatability of spatial structure. An arbitrary bright scene is not a point-source PSF. Different wavelengths and spectral recovery remain separate work.
4. Test RAW10 and RAW+JPEG/YUV only as independent batches with the actual illumination label. Preserve combination errors.

Example after physically arranging a point source:

```powershell
& $python .\tools\android_raw_probe\device.py --adb $adb capture `
    --format RAW_SENSOR --exposure-us 10000 --iso 100 --count 10 `
    --scene point --condition-verified --note 'Actual wavelength/source geometry/display state here'
```

## Export and decode

Python >=3.11, NumPy and Matplotlib are needed. Use this repository's documented analysis environment. Export uses `adb exec-out run-as org.udc.rawprobe tar ...`; **run-as is debug-app access, not root**. Binary bytes are written directly to a file, never through PowerShell text pipelines. A new destination is required; archives, source frames and app data are never deleted. Device serials are used locally for transport selection and omitted from normal output and persisted manifests. Full vendor metadata can contain private identifiers: keep all captures and logs local; review/redact before any sharing.

```powershell
& $python .\tools\android_raw_probe\device.py --adb $adb export .\outputs\phone-export-001
# Set $run to one exported device_files/captures/run_... directory.
& $python .\tools\android_raw_probe\raw_tools.py decode $run .\outputs\phone-decode-001
```

Decoding verifies plane SHA256, byte count, camera provenance and result/image timestamps. RAW_SENSOR uses explicit native-endian uint16 samples, rowStride, pixelStride and data offset. RAW10 uses four high bytes plus the fifth byte's 2-bit fields in Android's documented order; pixelStride must be 0. Neither decoder assumes padding after the last row is mapped.

Outputs include original-DN uint16 `.npy` frames, temporal mean and per-pixel sample standard deviation (when >=2 frames), and a log display of four parity tiles. Preview scaling affects only the plot; no demosaic, black subtraction, normalization, denoising or correction is applied to raw `.npy` data. Temporal statistics include source drift and motion, not only sensor noise. Results retain declared CFA, white/black levels, actual exposure/ISO and lens-shading status. Color names for parity tiles remain unverified until image-to-sensor crop origin is established; BGGR metadata alone does not settle that mapping. RAW_SENSOR's 16-bit container does not establish a 16-bit ADC.

For an exposure series:

```powershell
& $python .\tools\android_raw_probe\raw_tools.py series $dark1 $flat1 $dark2 $flat2 $dark3 $flat3 `
    --output .\outputs\phone-linearity-001
```

Series comparison rejects mixed camera, firmware, format, ISO or dimensions. Only completed, operator-confirmed batches with valid control readback, >=3 frames and matched dark/flat exposures contribute to a fit. >=3 exposure levels and <1% saturated samples are diagnostic prerequisites. The report gives dark-subtracted mean vs measured exposure and residuals; it does not assign a scientific pass threshold or prove source stability/diffraction.

## On-device files and completion semantics

All files are in the application's private `files/` directory, exported without conversion:

- `inventories/inventory_*.json`: all publicly enumerated camera IDs and all exposed characteristic keys.
- `captures/run_*/run.json`: request, physical annotation, source identity, events, committed frames and final state.
- `inventory.json`, `characteristics.json`: acquisition-time capability evidence.
- `frame_NNNN_request.json`, `frame_NNNN_result.json`: all exposed request/result keys, physical results and partial results where present.
- `frame_NNNN_raw_image.json`, `*_planeN.bin`: format, width/height, crop, timestamp, byte order, plane strides, original position/limit/capacity, data offset, saved length and SHA256. Readable buffer bytes `[0, limit)` are preserved; inaccessible capacity/last-row padding is not fabricated.
- Companion metadata and planes use the same layout. YUV is retained as three original planes with individual strides; it is not relabelled RAW.
- `frame_NNNN.dng`: an additional DngCreator output for RAW_SENSOR, matched to that image's TotalCaptureResult. A DNG error is retained, raw buffers stay intact and `run_succeeded=false`. DngCreator.writeImage accepts RAW_SENSOR, so RAW10 is explicitly marked not applicable; no fake RAW10 DNG is produced.

Metadata JSON preserves arrays and known Camera2 value classes, including CFA/black/white levels, rational matrices, gains and lens shading map dimensions/values. Mandatory stream combinations are serialized with descriptions, formats, sizes and input/output direction. All exposed keys are attempted. An unreadable key gets an explicit error; an unsupported opaque vendor class retains its type/string with `opaque_value_not_lossless`. Those cases are surfaced by the Python report and **do not constitute lossless full vendor metadata**. Private/hidden HAL state and unavailable analog/digital gains cannot be supplied by Camera2.

Frames become complete only after corresponding planes, metadata and DNG outcome are recorded. JSON publication uses a temporary file and atomic replacement when supported. A run is capture-complete only when the requested number of timestamp-matched frames is committed. `run_succeeded` additionally requires no DNG artifact errors; it does not certify manual accuracy or optical validity. Failure preserves partial files. Losing the foreground aborts acquisition; a killed process can leave `running`, which the host treats as nonterminal, never success.

## Validation and limits

Run no-hardware tests in the member environment:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_android_raw_probe.py
```

Tests use explicitly synthetic bytes and metadata. They establish decoding/traceability logic, not phone compatibility. See the [dated status report](../../docs/reports/2026-11-04-axon20-raw-probe.md) for actual execution and remaining physical acceptance.

Primary references: [ImageFormat layouts](https://developer.android.com/reference/android/graphics/ImageFormat), [Image.Plane buffers](https://developer.android.com/reference/android/media/Image.Plane), [Camera2 characteristics](https://developer.android.com/reference/android/hardware/camera2/CameraCharacteristics), [CaptureResult](https://developer.android.com/reference/android/hardware/camera2/CaptureResult), [DngCreator](https://developer.android.com/reference/android/hardware/camera2/DngCreator), [capture sessions](https://developer.android.com/reference/android/hardware/camera2/CameraCaptureSession).

## Latest device validation

The [2026-11-04 real-device report](../../docs/reports/2026-11-04-axon20-live-raw-validation.md) records 32 validated front-camera RAW frames (22 RAW_SENSOR, 10 RAW10), 22 DNGs and timestamp-matched RAW+JPEG/YUV tests. Dark/flat/point conditions remain unverified; optical and radiometric acceptance is pending. The earlier software-only report remains historical evidence.

The subsequent [external-occlusion report](../../docs/reports/2026-11-04-axon20-dark-capture.md) adds 30 RAW_SENSOR frames with operator-confirmed dark labels at ISO100 and 1/4/10 ms. Flat-field linearity and point-source PSF acceptance remain pending.

The [2026-11-23 exposure-response report](../../docs/reports/2026-11-23-axon20-exposure-response.md) adds 79 RAW_SENSOR/DNG pairs: 9 pilot frames, 40 white-surface illumination frames (10/40/100 ms plus a repeated 10 ms reference), and 30 fresh matched dark frames. All were exported; the formal 70 RAW frames passed integrity and readback checks. Global/parity exposure fits are descriptive only: spatial nonuniformity, first-frame offsets and unmeasured lamp stability remain. Calibrated flat-field/radiometric linearity and point-source PSF acceptance are still pending.

The [2026-11-25 remote-environment report](../../docs/reports/2026-11-25-axon20-remote-environment.md) records the separate experiment-PC Python/ADB deployment, 26 passing no-hardware tests, and authorized access to the existing app/19 retained run directories. ADB runs in the local desktop session and remains usable across checked SSH reconnects; paths and launch instructions are private host configuration. The deployed Python source, retained APK acquisition source and old optical acquisition checkout remain separately identified. No new capture or optical acceptance is implied.
