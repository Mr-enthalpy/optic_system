# Completed Phases

This document preserves implementation evidence for completed mainline phases.
It is historical context for maintainers, not the active roadmap. The active
medium-term plan lives in `docs/roadmap.md`.

## Phase 0 -- Documentation and Boundary Reset

Status: complete.

Phase 0 redefined `optic_system` as the hardware-control and
synchronized-capture frontend for the mono-LCD programmable diffractive imaging
project.

Completed outcomes:

- repository-level boundary documented;
- neural training, reconstruction, and mask optimization assigned to
  `LCD_forward`;
- deprecated prototype assumptions removed from the active architecture;
- old experimental task paths documented as legacy unless audited;
- the historical `old/` prototype directory was removed from the active tree;
  see `docs/legacy_reference.md` for its replacement boundaries and Git
  history lookup policy;
- `tls_c1` identified as the active TLS backend;
- staged implementation documented for later capture work.

Reference documents:

- `README.md`
- `AGENTS.md`
- `docs/architecture.md`
- `docs/roadmap.md`
- `tasks/README.md`

## Phase 1 -- TLS SDK Integration Closure

Status: substantially complete.

Phase 1 made `tls_c1` usable through the normal application/control path and
removed the old pywinauto TLS automation path from active code paths.

Completed outcomes:

- `TLSService` can be assembled when TLS is explicitly enabled;
- TLS commands flow through `SessionController`;
- TLS state is reflected through control state and events;
- optional GUI TLS controls are available through the main control GUI;
- no-hardware tests pass by default;
- TLS hardware tests remain opt-in;
- pywinauto TLS automation is not an active backend.

Intended TLS path:

```text
GUI / task intent
  -> control command
  -> SessionController
  -> TLSService
  -> tls_c1 high-level API
  -> vendor SDK
```

## Phase 2A -- Minimal Capture Task Layer

Status: complete.

Phase 2A introduced the minimal capture path.

Core modules:

```text
tasks/capture_plan.py
tasks/raw_capture_h5.py
tasks/capture_forward_dataset.py
scripts/capture_forward_dataset.py
```

The capture task supports:

```text
load capture plan
initialize camera / LCD / optional TLS
for each wavelength:
    set TLS wavelength
    move and wait until idle
    for each mask:
        show LCD mono mask
        wait settle_ms
        acquire K frames
        average or store burst
        save frame data and metadata
write raw capture HDF5
```

No-hardware validation:

```text
tests/test_capture_plan.py
tests/test_raw_capture_h5.py
tests/test_capture_forward_dataset_dry_run.py
```

Example dry run:

```bash
python scripts/capture_forward_dataset.py --plan plan.yaml --output out.h5 --dry-run
```

Example hardware run:

```bash
python scripts/capture_forward_dataset.py --plan plan.yaml --output out.h5 --hardware --enable-tls
```

## Phase 2B -- Hardware Smoke Capture Validation

Status: complete for the current local hardware baseline.

Phase 2B validated that the Phase 2 capture task can control the real camera,
mono LCD, and optional TLS in a deterministic sequence and produce a valid raw
capture HDF5 file.

Hardware smoke artifacts:

```text
plans/hardware_smoke_no_tls.yaml
plans/hardware_smoke_with_tls.yaml
scripts/make_smoke_masks.py
scripts/inspect_raw_capture.py
tests/test_phase2_hardware_smoke.py
tests/test_lcd_service.py
docs/hardware_smoke.md
```

Important processing flags:

```json
{
  "scientific_calibration_valid": false,
  "optical_alignment_validated": false,
  "training_ready": false,
  "raw_capture_schema_version": 2,
  "capture_role": "minimal_capture"
}
```

Passing Phase 2B smoke capture proves deterministic hardware control and
metadata preservation. It does not certify scientific calibration or
training-ready data.

## Phase 2C -- GUI / Diagnostics / Architecture Consolidation

Status: historical stabilization stage.

Phase 2C reduced duplicated entry points and clarified diagnostics boundaries
after hardware smoke validation.

Completed or retained boundaries:

- `app/main_gui.py` is the manual/debug control GUI;
- `scripts/monitor_run_status.py` is the file-only read-only monitor;
- `RunStatusPublisher` / `RunStatusReader` provide the run-status diagnostics
  boundary;
- monitor behavior remains hardware-free;
- default tests remain no-hardware.

Non-goals retained from this stage:

- no neural training;
- no reconstruction implementation;
- no GenerMask optimization;
- no hidden LCD_forward invocation from hardware capture code.

## Raw Capture HDF5 Baseline

The Phase 2 raw capture HDF5 schema preserves raw acquisition data before any
conversion. Representative groups include:

```text
/raw/frames_avg
/raw/frames
/masks/masks_physical
/masks/mask_id
/tls/wavelength_nm
/camera/exposure_us
/camera/gain_db
/lcd/mapping_policy_json
/lcd/metadata_json
/profiles/requirements_json
/capture/processing_flags_json
```

Training-ready files for `LCD_forward` should be generated by explicit
conversion steps. The raw capture file should not be overwritten or treated as
training-ready by default.
