# Acquisition host and offline analysis host

This workflow uses independent `optic_system` checkouts and complete files. It adds no remote device service. Synthetic portability validation, real independent acquisition-host deployment and the first real fixed-display data delivery have completed; see the [2026-10-17 acceptance report](reports/2026-10-17-fixed-display-psf.md). Those results apply to the recorded apparatus and source version, not every future host or optical configuration.

## Host boundaries and dependencies

| Acquisition host | Development / analysis host |
|---|---|
| Camera sidecar, local ZMQ/shared memory, physical LCD and TLS | Research Agent, code and experiment preparation |
| Broadband camera calibration, pupil scan, per-band pupil-open calibration | Read/check profiles; no claim of physical calibration |
| Local synchronized capture, RawCapture v3, file RunStatus | Inspect raw, Survey v1, sensor center, dense ROI and response diagnostics |
| Local disk and device configuration | Verified received data and separate analysis outputs |

The acquisition PC needs only an independent `optic_system` checkout, Python, necessary device packages and local configuration. It needs no Meta Repository, Agent runtime, GPU or learning stack. Shared memory and timing-sensitive control stay on that PC; a running capture does not call the development PC. The established camera sidecar is local isolation, not a network transport.

From each independent checkout, create its own environment. Use a known Python 3.12/3.13 executable to create `.venv`, then:

```powershell
# Acquisition host: local services, calibration, capture and status preview.
.\.venv\Scripts\python.exe -m pip install -r requirements-acquisition.txt
# Analysis host: offline arrays, HDF5, profiles and explicit bad-pixel correction.
.\.venv\Scripts\python.exe -m pip install -r requirements-analysis.txt
# Developers running all local tests/manual GUI use the full existing set.
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pytest -q
```

Vendor `flycapture2_c` and `tls_c1` are installed separately only on the acquisition host. Analysis commands do not import device services, vendor SDKs, pygame, ZMQ or OpenCV. The terminal status monitor is file-only; GUI/previews may additionally need Pillow/OpenCV from the acquisition requirements.

## A run is a directory, not a new data format

Choose all paths explicitly, for example:

```text
run_<id>/
  raw.h5
  plan.yaml
  profiles/
    broadband_camera.json
    pupil.json
    per_band_camera.json
  calibration_reports/
  masks/                 # actual masks/configuration referenced by the plan
  status/
  reports/               # analysis results, preferably created on receipt
```

Keep the exact acquisition plan, requested parameters, profiles used by the run, calibration reports and any sensor-validity masks needed to reproduce processing. Broadband profiles/reports are calibration context; survey conversion requires the matching PupilProfile and per-band pupil-open CameraProfile. Keep raw physical observations unchanged. Do not publish private SDK paths, credentials or device settings into Git.

HDF5 already records profile IDs, plan content, physical masks, camera settings/readback, illumination, extent, timing and completion flags. No duplicate manifest or provenance service is needed. The survey's existing JSON sidecar is part of its current format, not a new handoff format.

Capture rows commit after their frames and metadata. Only a closed RawCapture whose `capture_complete` and `run_succeeded` are true and whose row schedule/frame count agrees can pass `inspect_raw_capture --require-success`. Survey conversion additionally checks geometry, monochromatic illumination and matching profiles. These checks do not establish radiometric calibration or optical validity. A matching file hash alone does not establish acquisition success.

Failed/interrupted files and status remain on the acquisition host for diagnosis. They may be copied to a clearly separated diagnostic location, but must not be converted into formal surveys. Never turn failure flags into success or overwrite a run to retry; use a new run/output path.

## Closed-file transfer and receiver acceptance

Use existing lab access: SSH/SCP, SMB or remote desktop. On Windows, `robocopy` can copy GB files with bounded retries (`/Z /R:3 /W:2`); never use `/MIR`, `/PURGE` or `/MOVE` for measurement delivery. A remote desktop is an access method, not a measurement transport. Network access and authentication must be checked on the actual hosts during Phase B.

After the local acquisition process has exited and closed the HDF5, check it and obtain its sender hash:

```powershell
$py = '.\.venv\Scripts\python.exe'
& $py scripts/inspect_raw_capture.py $raw --require-success
if ($LASTEXITCODE -ne 0) { throw 'Do not deliver this run as successful' }
$senderHash = (Get-FileHash -LiteralPath $raw -Algorithm SHA256).Hash
```

Use the sender's hash through the trusted existing access channel. Do not recompute an expected hash from an untrusted or damaged received file. For a locally accessible sender directory or an SMB source, the small helper performs copying and receiver verification:

```powershell
# $source and $destination are explicit local/SMB file paths.
& ./scripts/copy_measurement_file.ps1 -Source $source -Destination $destination -ExpectedSha256 $senderHash
```

It streams the source under a read lock that denies concurrent writers on Windows/SMB, writes a uniquely named `.partial`, flushes/closes it, verifies SHA-256, then renames within the receiver directory without replacing an existing destination. It retries up to three times. Identical existing destinations are verified and left untouched; mismatched ones cause failure. Failed partials remain for diagnosis and must never be analysis inputs. It does not delete or modify source files. Repeat for the plan, profiles and required reports/masks; do not copy active status snapshots as if they were finalized data.

Use a PowerShell runtime permitted by the host's script policy (Phase A used PowerShell 7). If policy blocks scripts, use an approved/signed script or the existing transport plus manual `Get-FileHash` checks. Do not automatically change the machine execution policy. The actual acquisition host's policy is a deployment check.

For SCP or `robocopy`, copy to an explicit staging filename/directory first. Require the transport to report success, compare `Get-FileHash` with the sender hash, and only then move into a new receiving run location without overwriting anything. Do not open a partial transfer as a valid experiment input. Verify raw success again on the receiving PC. Interrupted transfer, changing source, hash mismatch or destination collision are failures; retain the original and diagnose/retry into a new path.

## Offline processing from any working directory

Set `$checkout` to the analysis checkout and `$run` to the received run. Inputs/outputs may be outside the source tree; do not reuse acquisition-host paths or its environment. Invoke scripts by their checkout path:

```powershell
$py = Join-Path $checkout '.venv/Scripts/python.exe'
& $py "$checkout/scripts/inspect_raw_capture.py" "$run/raw.h5" --require-success
if ($LASTEXITCODE -ne 0) { throw 'Received raw is not eligible' }
& $py "$checkout/scripts/build_psf_survey.py" --raw "$run/raw.h5" --pupil-profile "$run/profiles/pupil.json" --camera-profile "$run/profiles/per_band_camera.json" --output "$run/reports/survey.h5" --allow-large-survey
if ($LASTEXITCODE -ne 0) { throw 'Survey conversion failed' }
& $py "$checkout/scripts/estimate_sensor_center.py" --survey "$run/reports/survey.h5" --output "$run/reports/center.json"
if ($LASTEXITCODE -ne 0) { throw 'Center estimation failed' }
# Example geometry only: choose the ROI for the actual sensor/experiment.
& $py "$checkout/scripts/extract_roi_psf.py" --survey "$run/reports/survey.h5" --wavelength-index 0 --display-index 0 --roi 4 3 3 3 --output "$run/reports/roi.npz"
if ($LASTEXITCODE -ne 0) { throw 'ROI extraction failed' }
# Or use --center-profile ... --roi-shape HEIGHT WIDTH instead of --roi.
```

Every result path must be new. CLI errors exit nonzero; stop subsequent commands on failure. Indexing uses the original capture-plan wavelength and display indices. Coordinates are half-open sensor `(x,y,width,height)` and arrays `[H,W]`. Dense PSF values keep measured amplitude; ROI stdout reports shape, total/max intensity and wavelength without normalization. Center-estimator background subtraction is diagnostic and never alters survey/raw arrays. `--valid-pixel-mask` accepts a separately supplied boolean NPY for center estimation.

Camera/Pupil profiles are read at explicit receiving paths and validated by their IDs/dependencies. Recorded acquisition plan paths and raw provenance are measurement facts, not files offline conversion needs to open. Center profiles bind to their particular survey path and coordinate frame: derive them on the receiving host; if moving a processed survey, recompute its center at the new location rather than bypassing source checks.

Basic analysis can load an ROI without device packages:

```python
import json
import numpy as np
with np.load(roi_path, allow_pickle=False) as data:
    psf = data["psf"].copy()
    metadata = json.loads(str(data["metadata_json"]))
print(psf.shape, psf.sum(), psf.max(), metadata["wavelength_nm"])
otf = np.fft.fft2(np.fft.ifftshift(psf))  # raw-amplitude OTF; DC equals PSF sum
```

Compare wavelengths at the same display state and ROI. Any exposure/gain/source-power correction or normalization must be explicit on analysis copies. `relative_response` already supports explicit exposure scaling; this does not calibrate gain, source drift or RGB/Bayer sensitivity. No spectral decoding model or task evaluation implementation is added in this change.

## Acquisition PC deployment

1. Independently clone `optic_system` from `udc-main`, then check out the agreed published commit. Record the exact SHA; do not automatically pull a moving branch into an installed acquisition environment. Create its own `.venv`, install acquisition dependencies and vendor packages following their instructions. The Meta pin records accepted integration; deploying a newer version still requires explicit verification.

```powershell
git clone --branch udc-main git@github.com:Mr-enthalpy/optic_system.git optic_system
Set-Location optic_system
python -m venv .venv  # use the verified 3.12/3.13 interpreter
.\.venv\Scripts\python.exe -m pip install -r requirements-acquisition.txt
```

2. Configure camera SDK directories through `FLYCAPTURE2_SDK_DIR`/`FLYCAPTURE2_DLL_DIR`; select the optional local sidecar interpreter with `OPTIC_SYSTEM_SIDECAR_PYTHON` only if required. Verify camera index, sensor bit depth, trigger/start semantics and full-sensor extent. Select the TLS serial explicitly when needed.
3. Identify the physical LCD display index and subpixel axis; verify physical mask dimensions and pupil geometry. The panel may require an interactive Windows desktop session. Do not assume an SSH background process or remote-desktop session can present masks to that panel. Validate on the actual console/session before remote capture.
4. Create a writable local run directory with enough disk space. Put private stage plans/device settings outside Git. Set approved `$displayIndex`, `$subpixelAxis`, `$cameraIndex` and, if needed, `$tlsSerial`; they are not repository defaults.
5. Execute the existing calibration chain, preserving reports and dependencies. Example commands from the acquisition checkout (variables and plan files must be prepared for actual hardware):

```powershell
$py = '.\.venv\Scripts\python.exe'
$deviceArgs = @('--camera-index', $cameraIndex, '--lcd-display-index', $displayIndex, '--lcd-subpixel-axis', $subpixelAxis)
# If required: $deviceArgs += @('--tls-serial-number', $tlsSerial)
& $py scripts/calibrate_profiles.py broadband --plan "$config/broadband_plan.json" --output "$run/profiles/broadband_camera.json" --report "$run/calibration_reports/broadband.json" @deviceArgs
if ($LASTEXITCODE -ne 0) { throw 'Broadband calibration failed' }
& $py scripts/calibrate_profiles.py pupil --plan "$config/pupil_plan.json" --camera-profile "$run/profiles/broadband_camera.json" --output "$run/profiles/pupil.json" --report "$run/calibration_reports/pupil.json" @deviceArgs
if ($LASTEXITCODE -ne 0) { throw 'Pupil scan failed' }
& $py scripts/calibrate_profiles.py per-band --plan "$config/bands_plan.json" --pupil-profile "$run/profiles/pupil.json" --output "$run/profiles/per_band_camera.json" --report "$run/calibration_reports/bands.json" @deviceArgs
if ($LASTEXITCODE -ne 0) { throw 'Per-band calibration failed' }
& $py scripts/capture_dataset.py --hardware --enable-tls --plan "$run/plan.yaml" --output "$run/raw.h5" --pupil-profile "$run/profiles/pupil.json" --camera-profile "$run/profiles/per_band_camera.json" --status-dir "$run/status" --run-id $runId @deviceArgs
if ($LASTEXITCODE -ne 0) { throw 'Capture failed; retain raw and status' }
& $py scripts/inspect_raw_capture.py "$run/raw.h5" --require-success
```

Use [profile task chain](profile_task_chain.md) for stage-plan fields and [hardware smoke](hardware_smoke.md) for opt-in checks. Verify each process exit and output before continuing. Calibration CLI writes probe/scan reports but does not publish capture RunStatus; redirect its console output to a per-stage local log if needed. Capture publishes file status:

```powershell
& $py scripts/monitor_run_status.py --status-dir "$run/status" --no-gui --once
```

Remote monitoring reads copied/read-only status files; do not forward live camera shared memory. Long captures should be launched in a validated persistent local interactive session, not a session terminated when SSH/RDP disconnects. The first experiment used an authorized demand-only desktop task and continued locally through observed network outages. This does not prove durability after a desktop logout, process kill or power failure. On reconnect, inspect local task/status/files before issuing another start.

Stop locally with Ctrl+C where possible. The existing cleanup path closes device services; abrupt process/power loss may leave an incomplete HDF5 or stale sidecar. Preserve raw/logs/status, inspect read-only, restore the local device state through existing controls, then retry as a new run. Do not claim graceful stop is guaranteed after a hard kill or bypass profile dependencies. Transfer only closed successful data to the formal receiving directory, retaining failed runs separately.

After deployment and remote access checks, perform a small real hardware smoke run, then a controlled fixed-display multi-wavelength run and verified delivery. Dark/background, exposure/gain, source drift, sensor response, wavelength response repeatability and optical stability require separate experimental acceptance. Software, hashes and hardware communication alone do not prove spectral sensing.
