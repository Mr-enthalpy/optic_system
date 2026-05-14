# Read-only run-status monitor

In `phase3-bishe-experimental-loop`, this monitor is used for long-running
thesis capture/calibration tasks.  It does not replace the Phase 3 workflow
defined in `docs/phase3_workflow.md`.

## Purpose

`scripts/monitor_run_status.py` provides read-only observation of running
capture or calibration tasks.

The monitor reads task-published files from a run-status directory:

* `state.json`
* `current_mask_preview.png` or `current_mask_preview.npy`
* `latest_frame_preview.png` or `latest_frame_preview.npy`
* `frame_stats.json`
* `log.jsonl`

It does not connect to hardware and does not control the task.

## Usage

Terminal 1 runs a capture task with `--status-dir`:

```bash
python scripts/capture_pupil_scan.py --plan plans/bishe_pupil_scan.yaml --hardware --status-dir outputs/run_status/latest
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

## Limitations

The latest frame is not a video stream. It is the most recent preview image or
array written by the task.

The monitor does not subscribe to a live camera stream; `latest_frame_preview`
means the last preview file explicitly published by the running task.

The GUI view currently displays `.png` preview files. If the publisher falls
back to `.npy` because image-writing dependencies such as `cv2` are unavailable,
terminal mode and `RunStatusReader` can still read the array, but the GUI may
show that preview as unavailable. GUI rendering for `.npy` previews can be
added later without changing the diagnostics boundary.

Refresh rate depends on task publishing frequency and the monitor
`--poll-interval`.

If a task does not publish a frame preview, only state, mask preview, frame
statistics, and logs that are present in the run-status directory are shown.

This monitor is infrastructure only. Capture and calibration tasks must
explicitly publish richer diagnostics by calling `write_frame_preview(...)`,
`write_frame_stats(...)`, and `append_log(...)`. Phase 3.1 task integration
should add those calls where live diagnostics are useful.

## Dependencies

Mask and frame previews are written as PNG images using `cv2` (OpenCV).
If `cv2` is not installed in the experiment environment, previews fall
back to `.npy` format.  The terminal monitor (`--no-gui`) can read `.npy`;
the tkinter GUI shows the preview as unavailable.  Install `opencv-python`
in the experiment venv for full GUI preview support.

Previews are downsampled to a maximum side of 768 pixels before writing.
Raw HDF5 data is unaffected — this is status-dir policy only.
