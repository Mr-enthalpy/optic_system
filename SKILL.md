# SKILL.md — optic_system

## 1. Repository role

`optic_system` is the hardware-control and measured-data frontend for the
mono-LCD programmable diffractive imaging project.  Its primary responsibility
is to:

- control or represent experimental acquisition (camera, LCD, TLS);
- preserve raw experimental data as structured HDF5 with full acquisition
  metadata;
- build profile-aware calibration artifacts that downstream measured-data
  tasks consume;
- construct measured artifacts from acquired data, such as surveys, support
  or layout diagnostics, and exportable measured dictionaries;
- publish measured evidence and measured response handoffs toward external
  modelling and reconstruction repositories.

The repository prioritises reproducibility, explicit data contracts,
provenance, safety checks, and full measurement context.  It does not train
models, reconstruct scenes, or optimise masks.

## 2. Boundary with peer repositories

`optic_system` stops at measured artifacts, diagnostics, and handoff
publication. It participates in a four-repository system:

| Repository | Role |
|---|---|
| `lcd_mask_families` | reusable mask-family definitions, schemas, generators, projection rules, and versioned mask identity |
| `optic_system` | hardware integration, visualization, synchronized acquisition, raw capture preservation, measured artifacts, diagnostics, handoff publication |
| `LCD_forward` | measured-response/operator modelling, LCD-to-response surrogate learning, mask-family evaluation, parameter selection, operator-aware mask-sequence design, and H-matrix/operator diagnostics |
| `reconstruction` | inverse-problem solving, forward/adjoint consumption, reconstruction pipelines, learned reconstruction, task-level evaluation |

Rules:

- Do not implement forward surrogates, reconstruction networks, inverse
  solvers, mask-optimisation loops, or training pipelines.
- Do not add hidden calls into downstream modelling packages.
- Do not implement `lcd_mask_families`, `LCD_forward`, or `reconstruction`
  clients, adapters, imports, or final schemas until those contracts exist.
- Handoff publication must be explicit and traceable.
- If a change's primary purpose is to learn from artifacts rather than to
  produce, validate, or preserve them, it belongs outside this repository.

## 3. Data and schema discipline

- Raw measurements are preserved as factual records.  Derived artifacts are
  produced by explicit conversion or analysis steps — never by silently
  reinterpreting raw captures in place.
- Do not casually delete acquired data.  Old, superseded, invalid, or
  pre-mainline captures and artifacts should be renamed, downgraded with
  explicit metadata/status flags, quarantined, or migrated.  Delete acquired
  data only when the user explicitly asks for deletion.
- Schema documentation and schema contract tests are authoritative for
  persisted data formats. Current examples include
  `docs/raw_capture_schema.md` and
  `tests/test_raw_capture_schema_contract.py`.
- Schema changes must update the relevant documentation and contract tests in
  the same change.
- Schema compatibility across file generations is intentionally strict.
  There is no silent fallback path for legacy schema variants inside
  mainline readers. Old data must go through explicit, discoverable
  migration logic; current examples include `tasks/migrations/` and
  `scripts/migrate_*.py`.

## 4. Hardware and experiment safety

- Runtime mode is explicit: `hardware`, `no_hardware`, `synthetic`, or
  `diagnostic`.  Real hardware tasks default to hardware mode; anything
  else requires explicit choice (e.g. `dry_run=True` or an explicit
  `runtime_policy` override).
- Hardware tests are strictly opt-in via environment variables and
  `pytest` custom markers (`hardware`, `phase2_hardware`).  Default CI
  must be hardware-free.
- Never change acquisition behaviour (exposure timing, settle periods,
  frame discarding, loop ordering, trigger mode) without tests and an
  explicit rationale recorded in the PR or commit message.
- Dry-run and simulation paths use `FakeDeviceBundle` (from
  `tasks/testing/`).  Real-hardware paths go through adapter wrappers in
  `tasks/capture_forward_dataset.py` that delegate to `devices/`.
- Monochromatic illumination in hardware mode requires an explicit TLS
  setpoint.  Pass-through is `illumination.mode=broadband_passthrough`,
  not a wavelength value of any kind.

### Capture, diagnostics, and monitoring boundaries

- Live monitoring must remain read-only and hardware-free.  If richer live
  monitor output is needed, the capture or calibration task should publish
  diagnostics files; the monitor must not take over camera, LCD, or TLS
  ownership.
- Hardware paths should read real device capability bounds from device APIs
  when available.  Configuration bounds are fallback expectations for
  no-hardware paths, plan constraints, or explicit audit metadata.
- Capture records should preserve raw dtype, shape, requested/readback
  exposure and gain, timestamps, frame statistics, and saturation diagnostics.
  Diagnostic fields must not rewrite or reinterpret the acquired frame facts.
- Run-status `frame_stats.json` is a live visibility diagnostic unless a task
  explicitly promotes it into a measured audit artifact. If promoted, it must
  record strict non-finite status/count fields and explicit full-scale source
  semantics instead of relying on inferred full scale from floating arrays.
- Safety decisions, bad-pixel exclusion, and full-frame audit are separate
  layers.  A valid-pixel mask may define the safety decision domain, but
  excluded-domain saturation or non-finite pixels must still be recorded as
  diagnostic facts.
- Avoid non-standard JSON values such as `NaN` or `Infinity` in persisted
  metadata.  When a diagnostic value is not finite, record explicit status and
  count fields and use `null` for unavailable numeric peak/fraction values.

## 5. Artifact and metadata design principles

Every derived artifact should carry enough context to be interpretable
later, independent of the runtime environment that produced it:

- source identifiers (raw capture path, survey id, profile ids);
- coordinate frame and camera frame extent;
- units and normalisation conventions;
- algorithm parameters, policy flags, and version/schema information;
- timestamps or run identifiers where relevant.

Artifacts should be restartable from persisted files.  Downstream tasks
that load a profile, survey, or layout should not require earlier
calibration stages to be re-run.

Avoid artifacts that are valid only by implicit notebook state or local
assumptions about hardware layout.

## 6. Testing and validation expectations

- The default test suite should remain hardware-free unless a test is
  explicitly marked or gated as hardware opt-in.
- Add or update tests when introducing new schemas, validators, exporters,
  CLI behaviour, serialisation formats, or coordinate conventions.
- Prefer small, reproducible fixtures (HDF5 files built via the public
  writer API, fake devices, deterministic seeds) over opaque pre-built
  data dependencies.
- Contract tests enforce schema presence and absence.  Validation tests
  enforce parameter constraints, profile dependency rules, and runtime
  mode gating.
- Hardware tests remain opt-in and are not required for CI.

## 7. Acceptable work for future agents

The following kinds of work belong in `optic_system`:

- acquisition plans, capture plan schemas, and plan validation;
- profile definitions and calibration procedures;
- device adapters and hardware protocol definitions;
- raw HDF5 writer extensions, storage policy changes, and schema
  migrations;
- measured-artifact builders (survey, support, layout, dictionary);
- diagnostic analysis and report generation from measured data;
- CLI entry points for capture, conversion, and inspection;
- documentation (schema docs, profile chain rules, architecture docs);
- explicit, standalone migration scripts for pre-mainline data.

## 8. Work to reject or redirect

The following must be redirected to `lcd_mask_families`, `LCD_forward`,
`reconstruction`, or a dedicated experiment layer:

- forward surrogate model training;
- differentiable renderers or PSF emulators;
- reconstruction networks or learned inverse solvers;
- mask-optimisation loops;
- GenerMask parameter learning;
- mask-family design systems;
- operator package generation;
- evaluation metrics whose primary object is reconstruction quality rather
  than measured-system validity.

## 9. Style of reasoning

When uncertain whether a piece of work belongs here, ask:

> Is this preserving, validating, organising, or diagnosing measured
> experimental facts?

If yes, it likely belongs in `optic_system`.  If the main purpose is to design
mask families, learn operators, reconstruct scenes, or optimize masks, it
belongs in `lcd_mask_families`, `LCD_forward`, `reconstruction`, or another
external experiment layer.
