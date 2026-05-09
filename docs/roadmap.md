# optic_system Roadmap

## Current phase

Phase 1 is substantially complete.  Phase 2 (minimal capture task layer) may begin.

The repository has exited Phase 0 (documentation and boundary reset).  Camera, LCD,
and TLS paths are stable.  The task layer and HDF5 export are the next priorities.

---

## Phase 0  --  Documentation and boundary reset

### Goal

Re-anchor the repository as a hardware-control and synchronized-capture frontend. Replace obsolete early-GUI-prototype constraints. Clarify project boundaries and prepare for staged implementation.

### Allowed work

- README updates
- AGENTS updates
- Architecture documentation
- Roadmap documentation
- Task status documentation
- Comments clarifying active vs legacy code paths
- Documentation-only changes

### Non-goals

- Do not implement new features
- Do not modify camera / LCD / TLS device logic
- Do not modify SessionController behavior
- Do not add capture task code
- Do not add HDF5 writer implementation
- Do not add TLS GUI or app assembly
- Do not delete old tasks
- Do not introduce new runtime dependencies

### Completion criteria

- `docs/architecture.md` exists and clearly describes current architecture boundaries
- `docs/roadmap.md` exists and clearly describes Phase 0-4
- `tasks/README.md` exists and classifies old task status
- Documentation no longer excludes all hardware automation / synchronization / wavelength control as blanket out-of-scope items
- Documentation clearly states that full scheduler, neural training, and mask optimization are not in current scope
- Documentation clearly states `tls_c1` is the active TLS backend and pywinauto is deprecated
- Documentation clearly states new minimal capture tasks will be implemented separately in Phase 2
- No functional code changes introduced

---

## Phase 1  --  TLS SDK integration closure

**Status: substantially complete.**

### Goal

Make the `tls_c1` backend usable through the normal application/control path. Remove the active pywinauto TLS automation path. Expose TLS through control commands and events.

### Allowed work

- `devices/tls_service.py` implementation
- TLS commands (`SetTLSWavelength`, `MoveTLS`, `RefreshTLSStatus`, etc.)
- TLS events
- TLS state fields
- `SessionController` TLS handling
- Optional GUI TLS panel
- App assembly support for optional TLS (e.g. `--enable-tls`)
- No-hardware TLS tests
- Opt-in hardware TLS tests

### Non-goals

- No full wavelength sweep workflow
- No full calibration workflow
- No capture automation
- No training integration
- No direct GUI-to-TLS calls
- No pywinauto fallback path

### Completion criteria (all met)

- [x] `TLSService` can be constructed through the app assembly path when explicitly enabled
- [x] TLS state is observable through control state
- [x] TLS commands flow through `SessionController`
- [x] TLS hardware tests pass opt-in; no-hardware tests pass by default
- [x] Pywinauto TLS automation path is fully removed from active code paths
- [x] Default import and default tests remain hardware-free

---

## Phase 2  --  Minimal capture task layer

### Goal

Create a clean minimal capture path instead of reviving historical task scripts.

### Allowed work

- `tasks/capture_plan.py`  --  capture plan data structures
- `tasks/raw_capture_h5.py`  --  raw capture HDF5 writer
- `tasks/capture_forward_dataset.py`  --  minimal capture orchestration
- Load capture plan
- Initialize camera / LCD / optional TLS
- Set TLS wavelength if enabled
- Move TLS and wait until idle
- Show physical mono LCD mask
- Wait `settle_ms`
- Acquire K frames
- Average or store burst
- Write raw capture HDF5 with metadata

### Non-goals

- No full scheduler
- No full calibration engine
- No GenerMask optimization loop
- No training
- No direct `LCD_forward` training invocation
- No reviving legacy task scripts as active architecture

### Completion criteria

- A minimal capture plan can be loaded and executed
- Raw frames are acquired through `SessionController` -> camera path
- Raw capture HDF5 is written with masks, frames, camera metadata, LCD metadata, TLS metadata, and timing metadata
- Task code uses control-layer semantics
- No-hardware capture tests pass by default

---

## Phase 3  --  Raw capture to `LCD_forward` conversion

### Goal

Convert raw experimental captures into training-ready HDF5 for `LCD_forward`.

### Allowed work

- Conversion scripts that read raw capture HDF5
- Mask downsampling or encoding into `[N, T, 1, Hm, Wm]`
- PSF ROI extraction
- Frame averaging
- Dark / flat correction if available
- Wavelength metadata preservation
- Camera / TLS metadata preservation
- Train / val / test split generation
- Output files: `train.h5`, `val.h5`, `test.h5`

### Non-goals

- No training of forward surrogates
- No training of reconstruction models
- No model evaluation
- No on-the-fly conversion during acquisition (raw capture must be preserved first)

### Completion criteria

- Raw capture HDF5 files can be converted to `LCD_forward` training-ready HDF5
- Conversion preserves all relevant metadata
- Output conforms to `LCD_forward` expected tensor shapes and conventions
- Conversion is explicit, reproducible, and does not modify original raw captures

---

## Phase 4  --  Family-aware GenerMask calibration and closed-loop experiments

### Goal

Support structured mask-family calibration and controlled optimization loops.

### Allowed work

- GenerMask family registry
- Family-aware calibration plans
- Held-out family validation
- Perturbation robustness audits
- Repeated forward-surrogate retraining through `LCD_forward`
- Controlled mask-design experiments

### Non-goals

- Do not implement GenerMask training logic inside `optic_system`
- Do not collapse mask reasoning into network latent variables inside `optic_system`
- Do not begin Phase 4 before Phase 1-3 are stable

### Completion criteria

- Mask families are explicitly registered and discoverable
- Capture plans can reference specific mask families
- Physical mask visibility and metadata are preserved throughout the pipeline
- `LCD_forward` retraining can be invoked externally with structured data
- Held-out family generalization and perturbation robustness are measurable
