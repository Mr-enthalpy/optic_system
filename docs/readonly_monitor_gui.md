# Read-only run-status monitor

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

Refresh rate depends on task publishing frequency and the monitor
`--poll-interval`.

If a task does not publish a frame preview, only state, mask preview, frame
statistics, and logs that are present in the run-status directory are shown.
