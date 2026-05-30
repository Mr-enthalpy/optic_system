# optic_system Roadmap

## Current phase

Phase 2A (minimal capture task layer) and Phase 2B (hardware smoke capture validation)
are complete.  Phase 2C (GUI / diagnostics / architecture consolidation) is in progress.

The repository has exited Phase 0 (documentation and boundary reset) and Phase 1
(TLS SDK integration closure).  Camera, LCD, and TLS paths are stable.  The mainline
is consolidating entry points and diagnostics before resuming long-term Phase 3
(profile-driven experimental capture and LCD_forward conversion).

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

**Phase 2A and Phase 2B complete.  Phase 2C in progress.**

Phase 2A created the minimal capture path: ``tasks/capture_plan.py``,
``tasks/raw_capture_h5.py``, ``tasks/capture_forward_dataset.py``, and the
CLI entry point ``scripts/capture_forward_dataset.py``.

Phase 2B validated real-hardware smoke capture: camera, mono LCD (axis-aware
subpixel model), and optional TLS in a deterministic sequence producing
``raw_capture.h5``.

Phase 2C is consolidating GUI roles, diagnostics boundaries, file-only
monitoring, and stale entry points before long-term Phase 3 conversion work.

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

### Completion criteria

Phase 2A:
- [x] A minimal capture plan can be loaded and executed
- [x] Raw frames are acquired through the device path
- [x] Raw capture HDF5 is written with masks, frames, and full metadata
- [x] No-hardware capture tests pass by default

Phase 2B:
- [x] Real camera / LCD smoke capture produces valid ``raw_capture.h5``
- [x] Axis-aware LCD LCDService records full metadata
- [x] Opt-in hardware smoke tests pass

Phase 2C is in progress  --  see AGENTS.md Phase 2C section for goals and
non-goals.

---

### Phase 2C  --  GUI / diagnostics / architecture consolidation

**In progress.**

Primary goal: stabilize the GUI and diagnostics architecture after hardware
smoke validation and before long-term raw-capture conversion work.

Allowed work:

- remove duplicated or unsafe GUI/monitor entry points
- clarify that ``app/main_gui.py`` is the control GUI
- clarify that ``scripts/monitor_run_status.py`` is the file-only read-only monitor
- improve ``RunStatusPublisher`` / ``RunStatusReader``
- improve documentation around diagnostics boundaries
- clean empty legacy stubs and stale documentation
- keep default tests hardware-free

Non-goals:

- pupil scan
- dOTF
- PSF dictionary
- thesis-specific experiment scripts
- LCD_forward conversion
- neural training
- GenerMask optimization

---

## Phase 3  --  Profile-driven experimental capture and `LCD_forward` conversion

### Goal

Build a profile-driven capture architecture that keeps experimental
dependencies explicit from hardware calibration through raw HDF5 preservation
and later `LCD_forward` conversion.

This is the long-term mainline Phase 3. It is distinct from the
bachelor-thesis experimental branch, which has its own thesis-specific
Phase 3.0--3.7 workflow.

Phase 3 absorbs useful bachelor-thesis branch results by rewriting them into
durable mainline abstractions:

- `PupilProfile`
- `CameraProfile`
- profile-aware capture plans
- PSF dictionary capture requirements
- dOTF diagnostic boundaries
- H-matrix diagnostic export contracts
- raw-capture metadata links to profile artifacts

This is branch-result abstraction, not branch-workflow promotion. The mainline
must not directly merge thesis phase numbers, thesis defaults, or
thesis-specific shortcuts.

### Mainline interpretation

The bachelor-thesis branch demonstrated that measured mono-LCD PSF
dictionaries, dOTF diagnostics, and frequency-domain H-matrix analysis are
useful for evaluating the system. Its phase numbers, default masks, exposure
assumptions, and thesis-specific workflow must not become mainline architecture.

Mainline Phase 3 re-expresses thesis outputs as profile-driven reusable tasks
and keeps `optic_system` within its hardware-control boundary: camera / LCD /
TLS control, raw HDF5 capture, metadata preservation, diagnostics, and explicit
export toward `LCD_forward`. It does not train forward surrogates or
reconstruction models.

### Stage organization

Phase 3 is organized by dependency type, not thesis phase number.
Phase 3A and Phase 3B are inserted profile phases between Phase 2 capture
infrastructure and the original raw-to-`LCD_forward` conversion work. Phase 3C
is the original conversion layer, now consuming profile-aware raw captures and
diagnostics.

#### Phase 3A  --  Profile-driven experimental calibration

Define and generate profile artifacts that describe the optical setup and
safe camera settings.

Required tasks:

- `calibrate_broadband_passthrough_camera_profile`
- `scan_pupil_broadband`
- `calibrate_per_band_pupil_open_camera_profile`

#### Phase 3B  --  Profile-dependent PSF capture task families

Capture PSF-producing datasets only when explicit profile dependencies are
declared.

Required task families:

- Full-frame PSF scout survey for peak layout discovery
- `PeakLayoutProfile` derivation
- Peak-patch PSF dictionary capture and derived artifact build
- dOTF diagnostic capture
- mask-family PSF capture

#### Phase 3C  --  Raw-to-`LCD_forward` conversion

Convert preserved raw HDF5 into downstream-compatible files explicitly and
reproducibly.

Expected conversion work:

- Conversion scripts that read raw capture HDF5
- Mask downsampling or encoding into `[N, T, 1, Hm, Wm]`
- Peak-patch PSF dictionary export
- Peak table and patch coordinate metadata transfer
- PSF stack construction
- Frame averaging
- Dark / flat correction if available
- Wavelength metadata preservation
- Camera / TLS metadata preservation
- Profile metadata transfer
- Train / val / test split generation
- Output files: `train.h5`, `val.h5`, `test.h5`

### Required profile artifacts

#### `PupilProfile`

A `PupilProfile` describes the effective LCD physical region that participates
in the current optical system.

It should record at least:

- `pupil_profile_id`
- LCD physical coordinate convention
- LCD display index
- `subpixel_axis`
- estimated pupil center in LCD physical coordinates
- estimated pupil radius / aperture window
- camera-side PSF center if available
- recommended ROI candidates
- fit quality / confidence metrics
- source raw capture file
- creation timestamp and software version

#### `CameraProfile`

A `CameraProfile` describes safe camera exposure parameters under a specific
illumination and LCD state.

It should record at least:

- `camera_profile_id`
- illumination mode
- TLS / monochromator setpoint semantics
- LCD state used during calibration
- dependency on `PupilProfile`, if any
- exposure time
- gain
- peak pixel value
- saturation margin
- frames per capture
- valid downstream task families

Two camera-profile families are required:

1. `broadband_passthrough`

   This profile is used for broadband pupil scanning.

   The TLS / monochromator may use device setpoint `0` to represent
   pass-through mode, but this must not be treated as a physical wavelength.

   Metadata must distinguish:

   ```yaml
   illumination:
     mode: broadband_passthrough
     tls_setpoint_nm: 0
     effective_wavelength_nm: null
   ```

   This profile is valid for broadband pupil scan tasks, not for PSF dictionary
   capture.

2. `per_band_pupil_open`

   This profile is used for PSF, dOTF, and mask-family capture tasks.

   It must depend on a `PupilProfile`.

   The LCD state must be `selected_pupil_open`, not full-panel all-open. This
   avoids overly conservative per-band exposure settings caused by full-LCD-open
   calibration.

### Required profile-producing tasks

#### `calibrate_broadband_passthrough_camera_profile`

This task finds safe camera settings for broadband mixed-light pupil scanning.

```yaml
illumination:
  mode: broadband_passthrough
  tls_setpoint_nm: 0
  effective_wavelength_nm: null
  source: xenon
lcd_state:
  mode: safe_probe_mask
output:
  camera_profile_id: broadband_passthrough_safe_v*
  valid_for:
    - pupil_scan_broadband
```

Setpoint `0` is a TLS / monochromator device pass-through sentinel. It must
not be stored in scientific `wavelengths_nm` fields.

#### `scan_pupil_broadband`

This task scans the LCD physical surface under broadband pass-through
illumination and produces a `PupilProfile`.

```yaml
requires:
  camera_profile_id: broadband_passthrough_safe_v*
illumination:
  mode: broadband_passthrough
output:
  pupil_profile_id: pupil_profile_v*
```

#### `calibrate_per_band_pupil_open_camera_profile`

This task finds per-band safe camera settings under the selected effective
pupil state.

```yaml
requires:
  pupil_profile_id: pupil_profile_v*
illumination:
  mode: monochromatic
  wavelengths_nm: [450, 550, 650]
lcd_state:
  mode: selected_pupil_open
  outside_pupil: closed_or_neutral
output:
  camera_profile_id: per_band_pupil_open_v*
  valid_for:
    - psf_dictionary_capture
    - dotf_capture
    - mask_family_psf_capture
```

### Task dependency rules

All PSF-producing tasks must declare explicit dependencies:

```yaml
requires:
  pupil_profile_id: ...
  camera_profile_id: ...
```

PSF-producing tasks must not silently use:

- the most recent camera settings
- full-LCD-open exposure settings
- broadband-passthrough exposure settings
- thesis-branch phase-specific defaults

Tasks that violate this rule are not mainline-compatible.

### Artifact manifests

Each profile-producing task should emit a JSON or YAML manifest. Raw capture
HDF5 files should record profile IDs and enough profile metadata references to
support future reprocessing.

Example camera-profile manifest:

```yaml
artifact_type: camera_profile
camera_profile_id: per_band_pupil_open_2026xxxx
depends_on:
  pupil_profile_id: pupil_profile_2026xxxx
illumination:
  mode: monochromatic
  wavelengths_nm: [450, 550, 650]
lcd_state:
  mode: selected_pupil_open
  pupil_profile_id: pupil_profile_2026xxxx
camera:
  gain_db: 0.0
  per_wavelength:
    "450":
      exposure_us: 780.0
      peak_pixel: 230
      saturation_margin: 25
    "550":
      exposure_us: 490.0
      peak_pixel: 214
      saturation_margin: 41
    "650":
      exposure_us: 2240.0
      peak_pixel: 236
      saturation_margin: 19
valid_for:
  - psf_capture
  - dotf_capture
```

Example PSF task plan dependency:

```yaml
task_type: psf_dictionary_capture
requires:
  pupil_profile_id: pupil_profile_2026xxxx
  camera_profile_id: per_band_pupil_open_2026xxxx
illumination:
  mode: monochromatic
  wavelengths_nm: [450, 550, 650]
output:
  raw_capture_h5: data/raw/...
```

### Required task families

Phase 3 may introduce or refactor the following mainline task families:

```text
tasks/profiles/
  camera_profile.py
  pupil_profile.py
  calibrate_broadband_camera_profile.py
  scan_pupil_broadband.py
  calibrate_per_band_pupil_open_camera_profile.py

tasks/psf/
  build_full_frame_psf_survey.py
  derive_peak_layout_profile.py
  build_peak_patch_psf_dictionary.py
  export_peak_patch_dictionary_to_lcd_forward.py
  compact_dense_export.py
  capture_psf_dictionary.py
  capture_dotf_dataset.py
  capture_mask_family_psf.py

tasks/diagnostics/
  inspect_psf_dictionary.py
  compute_dotf_diagnostic.py
  compute_h_matrix_diagnostic.py

tasks/conversion/
  convert_peak_patch_to_lcd_forward.py
  convert_raw_to_lcd_forward.py
```

Existing Phase 2 capture infrastructure must remain intact.

### Plan organization

New plans should be organized by dependency family rather than by thesis phase
number:

```text
plans/profiles/
  broadband_passthrough_camera_safety.yaml
  pupil_scan_broadband.yaml
  per_band_pupil_open_camera_safety.yaml

plans/psf/
  psf_dictionary_single_lambda.yaml
  psf_dictionary_multilambda.yaml
  dotf_edge_perturb.yaml
  mask_family_psf_capture.yaml

plans/diagnostics/
  psf_dictionary_report.yaml
  dotf_report.yaml
  h_matrix_report.yaml

plans/smoke/
  hardware_smoke_no_tls.yaml
  hardware_smoke_with_tls.yaml
```

The old thesis phase labels, such as `3.0.5` or `3.1`, may be mentioned in
migration notes but must not become mainline task names.

### Allowed work

- Add profile artifact dataclasses and manifest serialization
- Add broadband-passthrough camera safety calibration
- Add broadband pupil scan
- Add per-band pupil-open camera safety calibration
- Make PSF-producing tasks depend explicitly on `PupilProfile` and `CameraProfile`
- Add profile-dependent full-frame scout survey, peak layout profile, and peak-patch PSF dictionary build and export
- Add metadata fields linking raw captures to profile IDs
- Add diagnostic scripts for PSF dictionaries, dOTF, and H-matrix analysis
- Add documentation describing how thesis-branch artifacts map into mainline abstractions
- Add no-hardware tests for profile loading, validation, and dependency checks
- Add explicit raw-to-`LCD_forward` conversion that preserves profile metadata

### Non-goals

- Do not merge the thesis branch wholesale
- Do not preserve thesis-specific phase numbering as architecture
- Do not treat TLS setpoint `0` as a physical wavelength
- Do not use full-LCD-open exposure profiles for PSF-producing tasks
- Do not train forward surrogates inside `optic_system`
- Do not implement reconstruction inside `optic_system`
- Do not start GenerMask optimization inside this phase
- Do not collapse profile metadata into ad-hoc filename conventions
- Do not bypass raw capture HDF5 preservation
- Do not directly output `LCD_forward` training data while bypassing raw capture metadata
- Do not evaluate reconstruction quality inside `optic_system`

### Completion criteria

- `PupilProfile` and `CameraProfile` artifacts are defined and documented
- Broadband-passthrough camera safety calibration exists
- Broadband pupil scan depends on the broadband-passthrough profile
- Per-band pupil-open camera calibration depends on a `PupilProfile`
- PSF-producing tasks refuse to run without explicit `pupil_profile_id` and `camera_profile_id`
- Raw capture HDF5 metadata records profile IDs and illumination mode
- TLS setpoint `0` is recorded only as broadband pass-through device state and never as a scientific wavelength
- Thesis-branch useful outputs are represented as mainline artifacts or diagnostics
- Full-frame PSF survey artifacts can be built from small raw scout captures
- `PeakLayoutProfile` artifacts can be derived from full-frame surveys
- `PeakLayoutProfile` artifacts record survey masks/wavelengths as provenance,
  with explicit validity scope instead of implying a production mask whitelist
- Peak-patch PSF dictionaries can be built from raw capture using a `PeakLayoutProfile`
- Peak-patch dictionaries store patches and full-frame sensor coordinates, not full-frame production PSF stacks
- Default tests remain hardware-free
- Hardware execution remains opt-in
- Phase 4 can consume profile-aware mask-family calibration data without depending on thesis-specific workflow
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
