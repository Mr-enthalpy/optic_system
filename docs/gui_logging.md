# GUI session logging

Run the GUI with default session logging:

```bash
python app/main_gui.py --enable-tls --lcd-display-index 1 --lcd-subpixel-axis 1
```

Logs are written to:

```text
outputs/gui_logs/<run_id>/
  main_gui.log       # human-readable log
  events.jsonl       # structured event log (one JSON object per line)
  session_start.json # startup context metadata
```

## CLI options

| Option | Default | Description |
|---|---|---|
| `--log-dir` | `outputs/gui_logs` | Parent directory for GUI session logs |
| `--log-level` | `INFO` | Logging level: DEBUG, INFO, WARNING, ERROR, CRITICAL |
| `--run-id` | `auto` | Run identifier (default generates timestamp like `20260512_153012_main_gui`) |
| `--no-file-log` | off | Disable file logging (console logging remains active) |
| `--log-preview-stats-interval-ms` | `1000` | Minimum interval between PreviewStatsUpdated event writes |

## Logged content

### `main_gui.log`
- Startup command and parsed arguments
- Log directory paths
- Controller build start/finish
- Controller start success/failure with traceback
- Camera metadata (serial, dimensions, pixel format)
- LCD metadata (display index, shapes, subpixel axis)
- TLS metadata (connected, device ID, wavelength, grating)
- TLS auto-connect attempt and result
- GUI callback exceptions with traceback
- Tkinter callback exceptions with traceback
- Shutdown start/finish
- Status messages, errors, and warnings from the event bus

### `events.jsonl`
- All controller events (StatusMessage, CameraError, LCDError, TLSError,
  CameraSettingsApplied, CameraSettingsRefreshed, LCD* events, TLS* events,
  PreviewStatsUpdated)
- PreviewFrameUpdated is excluded (contains image array)
- PreviewStatsUpdated is throttled (default 1 s interval)
- Each line: `{"ts": "...", "monotonic_ns": ..., "event_type": "...", "payload": {...}}`

### `session_start.json`
- `run_id`, `argv`, `cwd`, `python`, `platform`, `git_commit`, `args`

## Reporting visual issues

When reporting a visual problem, include:

1. Visual description of what you observed
2. Log directory (`outputs/gui_logs/<run_id>/`)
3. Whether LCD, TLS, and camera were enabled
4. Approximate time of the issue
