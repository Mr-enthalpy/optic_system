# Read-only run-status monitor

## Purpose

`scripts/monitor_run_status.py` provides read-only observation of running
capture or calibration tasks.

The monitor reads task-published files from a run-status directory:

* `state.json`
* `current_mask_preview.png` or `current_mask_preview.npy`
* `latest_frame_preview.npy` by default; `.png` is still readable for legacy
  or explicitly requested previews
* `frame_stats.json`
* `log.jsonl`

It does not connect to hardware and does not control the task.

## Usage

Terminal 1 runs a capture task with `--status-dir`:

```bash
python scripts/capture_forward_dataset.py --plan plan.yaml --hardware --status-dir outputs/run_status/latest
```

Terminal 2 opens the monitor with the same `--status-dir`:

```bash
python scripts/monitor_run_status.py --status-dir outputs/run_status/latest
```

For terminal-only polling:

```bash
python scripts/monitor_run_status.py --status-dir outputs/run_status/latest --no-gui
```

For one snapshot, useful in tests or scripts:

```bash
python scripts/monitor_run_status.py --status-dir outputs/run_status/latest --no-gui --once
```

## Safety

The monitor never connects to camera, LCD, or TLS hardware.

The monitor may be opened and closed at any time. Closing it does not stop the
running task.

The monitor shows only task-published previews and metadata. It does not write
raw capture HDF5, change exposure or gain, change LCD masks, move TLS hardware,
or modify task state.

The GUI keeps task metadata in a separate top-right panel with its own scroll
area. Frame and mask previews are displayed in the main middle panels, and the
recent-log panel renders only the newest `--max-log-lines` entries. Preview
images are scaled to fit their GUI panels while preserving aspect ratio, so a
large camera frame is not cropped to its upper-left corner.

The frame panel has a preview encoding selector below the image. Because the
camera service may not expose reliable Bayer-filter metadata, the task
publishes the latest camera frame as a raw `.npy` array and the monitor applies
the selected display encoding locally:

* `Raw mono`
* `Bayer RGGB -> RGB`
* `Bayer BGGR -> RGB`
* `Bayer GRBG -> RGB`
* `Bayer GBRG -> RGB`

## Limitations

The latest frame is not a subscribed camera stream. It is the most recent raw
preview array written by the task.

The monitor does not subscribe to a live camera stream; `latest_frame_preview`
means the last preview file explicitly published by the running task.

The GUI can display both `.npy` raw arrays and `.png` preview files. Bayer RGB
preview modes require OpenCV in the experiment venv; raw mono preview still
works without Bayer decoding.

Refresh rate depends on task publishing frequency and the monitor
`--poll-interval`.

If a task does not publish a frame or mask preview, only state, frame
statistics, metadata, and logs that are present in the run-status directory are
shown.

This monitor is infrastructure only. Capture and calibration tasks must
explicitly publish richer diagnostics by calling `write_frame_preview(...)`,
`write_frame_stats(...)`, and `append_log(...)`. Future task integrations
should add those calls where live diagnostics are useful.

## Dependencies

Mask previews are written as PNG images using `cv2` (OpenCV) when available and
fall back to `.npy` otherwise. Frame previews are written as raw `.npy` arrays
by default so the monitor can choose the Bayer display encoding.

Mask previews are always lightweight diagnostics and may be downsampled before
PNG or NPY publication. Raw frame NPY previews are never downsampled before
publishing, preserving the full sensor data for the monitor. Raw frame `.npy`
previews are not downsampled before publishing. Raw HDF5 data is unaffected;
this is status-dir policy only.
