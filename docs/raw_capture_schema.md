# RawCapture local contract

The writer emits `artifact_type=raw_capture`, `software_version=optic_system`, `raw_capture_schema_version=3`, capture role, creation time and plan identity. Survey conversion accepts only this explicit version. Other historical layouts need an explicit conversion for the specific data; no generic schema adapter is used.

- `/raw/frames_avg`: `[N,H,W]` observed averages (float32 storage by default); optional `/raw/frames`: `[N,K,H,W]` burst retaining input dtype. Storage policy records accumulator and storage dtypes.
- `/masks/`: physical mono mask arrays, display-state IDs, optional family labels/parameters supplied by the experiment. Mask-table writes become immutable after capture begins.
- `/illumination/`: explicit mode, effective wavelength/TLS setpoint and illumination JSON. Pass-through is a mode; setpoint zero is not a physical wavelength.
- `/camera/`: per-row requested/readback exposure in microseconds and gain in dB, `/camera/frame_extent_json`, timestamps and status.
- `/lcd/`: display metadata, physical mapping policy, per-row display timestamp and settle.
- `/tls/`: per-illumination grating, settle, timestamp and status. Shared metadata is immutable once a row at that wavelength commits.
- `/profiles/`: required profile IDs and requirements JSON.
- `/capture/`: plan/runtime, schedule indices, burst count, `completed` bitmap and processing flags.

Rows commit in wavelength-major, then display-state order. Validate indices, schedule, burst shape and frame dimensions before mutation. Write corresponding frames and metadata before marking `completed=True`. Rejected/failed appends leave that row incomplete; committed rows, their shape and shared wavelength metadata remain immutable.

Finalization derives `capture_complete` and counts from committed rows covering the exact schedule. `run_succeeded` records absence of a reported run error independently; even a fully written run can fail afterward. A nonfinalized file records failure/pending state. Survey conversion requires both success and completeness and rechecks indices. Scientific calibration/alignment flags stay false until separate experimental evidence exists; there is no training-readiness contract.

Writer output creation is exclusive: existing acquired files cannot be overwritten. Preserve failed captures for diagnostics. `FullFramePSFSurvey` uses a separate local v1 contract with `artifact_type`, `schema_version=1`, `complete=True`, `[N,H,W]` frames, frame extent and explicit entry indices. Conversion retains full-frame amplitude and relevant source groups. An interrupted survey stays incomplete and is rejected by its reader.
