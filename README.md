# optic_system

`optic_system` is the hardware-facing acquisition and calibration platform for investigating display-induced diffractive spectral sensing in an under-display-camera-like optical configuration.

The scientific question is whether display-induced diffraction contains reproducible wavelength-dependent information that can extend ordinary RGB camera spectral/material sensing. This is a UDC-like laboratory optical prototype, not a commercial phone UDC. Hardware smoke tests do not establish this mechanism.

This repository owns hardware control, calibration, raw observations, measured PSFs and basic optical diagnostics. It does not implement mask optimization, forward surrogate training, classification or reconstruction training. Complete spectral recovery is not a required output here. First experiments measure wavelength-dependent response at a fixed display state; dynamic display coding is a separate subsequent experimental variable.

## Validated state

The maintained development and default branch is `udc-main`. The historical `master` history is retained; new acquisition work belongs on `udc-main` or a feature branch based on it.

On 2026-10-17 the laboratory completed real broadband camera calibration, LCD pupil scanning, per-band calibration and three fixed-display 450/550/650 nm PSF runs, with three 550 nm reference runs and matching blocked-light darks. The 384 illuminated and 96 dark frames were transferred with SHA-256 verification and processed offline into surveys, dense ROIs and basic response diagnostics. The configured optical system showed repeatable wavelength-dependent shapes; the independent display contribution and material/task information gain remain unestablished. See the [dated experimental acceptance report](docs/reports/2026-10-17-fixed-display-psf.md) for measured results, failure records and limits.

The current source integrates the separately tested dual-host workflow and the acquisition fixes used during that experiment. It is not the exact historical acquisition commit; source versions and regression results are distinguished in the report. Raw data, Profiles, private desktop launch helpers and generated analysis outputs remain outside Git.

## Local setup

Use Python 3.12 or 3.13 and a repository-local virtual environment. From this checkout in PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pytest -q
```

Normal imports and default tests need no camera, LCD, TLS or vendor DLL. For hardware, install `flycapture2_c` and `tls_c1` into this environment using their package instructions. Configure SDK directories with `FLYCAPTURE2_SDK_DIR` / `FLYCAPTURE2_DLL_DIR`; optionally select a sidecar interpreter with `OPTIC_SYSTEM_SIDECAR_PYTHON`. No driver source checkout or sibling repository is assumed. Keep private configuration under ignored `local_config/` and measurements under ignored `data/` or an explicitly selected external path.

For separate hosts, install `requirements-acquisition.txt` on the acquisition PC or `requirements-analysis.txt` on the offline analysis PC. The full `requirements.txt` remains the development/test environment. See [dual-environment workflow](docs/dual_environment_workflow.md) for independent deployment, closed-file SHA-256 transfer, and Survey/center/ROI CLI commands. The acquisition PC needs no research Agent or Meta Repository; the analysis PC needs no vendor SDK or hardware connection.

## Hardware boundaries

The manual/debug GUI is `app/main_gui.py`. GUI intent travels through control commands, `SessionController`, and device services. The camera runs in a sidecar, using ZMQ control and shared-memory frames. Startup is headless: `PreConfigGUI` is rejected; open camera disables trigger by default, then stream startup is explicit. Capture tasks use narrow blocking adapters and a private frame stream because movement/wait and burst capture are synchronous.

Select the physical LCD display index and subpixel axis explicitly (CLI flags or `OPTIC_SYSTEM_LCD_DISPLAY_INDEX` / `OPTIC_SYSTEM_LCD_SUBPIXEL_AXIS`). An unconfigured physical display fails before opening a window. Axis 0 maps physical mono masks `[3H,W]`; axis 1 maps `[H,3W]`. RGB packing occurs only at the device boundary. Mask shape must match the selected device; resolution never identifies the target LCD.

TLS target-setting and physical movement remain distinct. Broadband zero-order uses `set_pass_through()`, never `set_wavelength_nm(0)`. Capture plans encode illumination explicitly; a wavelength label without TLS does not imply broadband pass-through.

## Measured data workflow

```text
Broadband Camera Calibration -> Broadband LCD Pupil Scan -> PupilProfile
 -> Per-Wavelength Pupil-Open Camera Calibration
 -> Full-frame RawCapture -> FullFramePSFSurvey
 -> Sensor Energy Center / explicit ROI -> Dense Measured ROI PSF
 -> Spectral Response Analysis
```

`CameraProfile` and `PupilProfile` supply dependency validation and measured acquisition settings. Broadband exposure settings only serve pupil scanning. PSF capture uses a per-band pupil-open profile tied to the same pupil; display masks must fit that physical pupil. The first experiment may use just one fixed state. Capture applies each band's exposure/gain and records both requested settings and readback. See [profile workflow](docs/profile_task_chain.md).

Minimal capture explicitly selects hardware or synthetic execution:

```powershell
.\.venv\Scripts\python.exe scripts/capture_dataset.py --plan plans/synthetic_capture.yaml --output data/smoke.h5 --dry-run
```

For measured PSF acquisition, pass `--hardware --enable-tls --camera-profile local_config/bands.json --pupil-profile local_config/pupil.json` and explicit LCD flags. A capture plan must record verified `camera.frame_extent` when full-sensor geometry cannot be obtained from camera metadata. Declare mode `full_sensor`, origin `[0,0]`, `shape_hw` and `sensor_shape_hw`; survey conversion rejects unknown/cropped/mismatched extents. Do not label a hardware crop as full sensor.

Survey conversion is available through `scripts/build_psf_survey.py` and the hardware-free Python function below. `scripts/estimate_sensor_center.py` exposes the existing center estimator; `scripts/extract_roi_psf.py` accepts explicit coordinates or a center profile and experiment-selected shape. All accept explicit input/output paths and reject existing outputs.

```python
from tasks.psf import build_full_frame_psf_survey, ROI, extract_roi_psf, relative_response

build_full_frame_psf_survey(
    source_raw_capture_h5="data/raw.h5", output_h5="data/survey.h5",
    pupil_profile_manifest="local_config/pupil.json",
    camera_profile_manifest="local_config/bands.json",
    allow_large_survey=True,  # explicit permission for full-resolution data
)
measurement = extract_roi_psf(
    "data/survey.h5", wavelength_index=0, display_index=0,
    roi=ROI(x=100, y=100, width=400, height=300),
)
# measurement.psf is the actual 2D measurement, without normalization.
analysis = relative_response(measurement, exposure_us=measurement.metadata["camera_settings"]["readback_exposure_us"])
```

ROI coordinates are half-open sensor pixels, x right / y down; arrays are `[H,W]`. Size is selected by the experiment. Center profiles can locate the ROI but do not define its size or modify full-frame observations. Indexing uses the original capture-plan wavelength and display-state indices. Sources, illumination, physical display metadata, camera settings and ROI geometry accompany the returned array. `scripts/extract_roi_psf.py` optionally saves one analysis NPZ; there is no ROI dictionary hierarchy.

Survey and ROI extraction preserve measured amplitude. Explicit analysis may divide by exposure or normalize total energy on a copy. Exposure scaling alone does not calibrate gain, source power, sensor spectral sensitivity or broadband drift. Bad-pixel correction is also a separate copy-producing analysis operation. The current measured PSF interface consumes 2D raw sensor frames; an RGB camera's Bayer/channel response must be interpreted and calibrated for its specific sensor, not inferred from preview colors.

RawCapture v3 commits a row only after its frames and metadata succeed. `capture_complete` and `run_succeeded` are independent facts; incomplete/failed runs cannot become a survey. Existing raw/survey files cannot be overwritten. [Raw schema](docs/raw_capture_schema.md) describes the explicit local layout. Other layouts require an explicit conversion, never a silent reader fallback.

`scripts/monitor_run_status.py` reads only task-published files. Missing previews/stats/logs are valid. It does not connect to hardware. Passing default tests or smoke tests does not establish radiometric calibration, optical alignment or a scientifically useful sensing channel.

## Independent development and Submodule use

This checkout imports, tests and runs independently of its parent directory and sibling repositories. It has its own dependencies, CI, Git history and branches. There is no `.gitmodules`, cross-repository scheduler, package registry or manual compatibility-commit table here. The [UDC Meta Repository](https://github.com/Mr-enthalpy/Using-the-diffraction-defects-of-under-display-cameras) owns cross-repository research plans and compatible Submodule pins; [AGENTS.md](AGENTS.md) owns only local hardware/data/maintenance rules. A Meta pin restores an exact accepted commit, while this repository remains independently usable. See [architecture](docs/architecture.md), [local experimental roadmap](docs/roadmap.md) and [hardware validation](docs/hardware_smoke.md).
