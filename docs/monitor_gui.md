# Read-only monitor GUI

## Purpose

`app/monitor_gui.py` is a read-only monitor for running capture tasks.

It observes:

* camera preview frames when the camera sidecar frame stream is available
* current LCD mask id and mask preview from a run-status directory
* TLS current wavelength, target wavelength, grating, and moving state
* capture task progress when `state.json` is available

The monitor does not own hardware lifecycle. It is safe to open, close, and
reopen while a capture task continues running.

## Usage

Start the capture task with a status directory:

```bash
python scripts/capture_forward_dataset.py \
  --plan plans/bishe_psf_repeatability.yaml \
  --output data/raw/repeatability_raw.h5 \
  --hardware \
  --status-dir outputs/run_status/repeatability_001
```

Start the monitor with the same status directory:

```bash
python app/monitor_gui.py \
  --status-dir outputs/run_status/repeatability_001 \
  --frame-timeout-ms 500 \
  --log-dir outputs/gui_logs
```

The monitor can also run without camera preview:

```bash
python app/monitor_gui.py \
  --status-dir outputs/run_status/repeatability_001 \
  --no-camera
```

If `--status-dir` is omitted, `--run-id` resolves to
`outputs/run_status/<run-id>`:

```bash
python app/monitor_gui.py --run-id repeatability_001 --no-camera
```

## What it displays

Camera panel:

* live camera preview when a frame stream exists
* frame sequence
* max pixel
* width and height
* pixel format
* last frame timestamp

LCD panel:

* current mask id
* current mask preview
* mask preview shape
* last status update age

TLS panel:

* current wavelength
* target wavelength
* grating
* moving state
* last status update age

Task panel:

* run id
* plan id
* capture index and total capture count
* phase
* completed state
* error text, if any

## What it does not do

The monitor does not:

* control camera, LCD, or TLS hardware
* start or stop capture tasks
* send `SessionController.dispatch` commands
* construct `LCDService` or `TLSService`
* show LCD mask-control buttons
* show TLS control buttons
* write raw experimental data
* stop a capture task when the window is closed

Raw HDF5 remains the authoritative capture output. Run status is only a
monitoring aid.

## Troubleshooting

Status unavailable:

* check that the capture task was started with `--status-dir`
* verify that the monitor uses the same directory
* missing status files are tolerated; the monitor stays open

Camera stream unavailable:

* the capture task may not have started the camera sidecar stream yet
* the monitor can continue showing status without camera preview
* use `--no-camera` to disable frame-stream subscription

Stale status timestamp:

* the capture task may be between updates
* the capture task may have completed or failed
* check the task panel `phase`, `completed`, and `error` fields

Mask preview missing:

* the task writes a preview after a mask is shown
* older runs or failed early starts may not have a preview
* the monitor continues showing other status fields

