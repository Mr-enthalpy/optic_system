# optic_system Roadmap

## Current Stage

Current stage:

```text
Phase 3.5C -- real-data operationalization and support stability audit.
```

Completed baseline:

```text
Phase 2 -- minimal hardware capture layer.
Phase 3.5A -- full-frame scout -> peak layout -> fixed-size peak-patch dictionary data contract.
Phase 3.5B -- first-pass diffraction support analysis report.
```

Active work:

```text
accelerate and stream support analysis on real 2048 x 2448 full-frame data;
define real-data parameter presets;
aggregate connected components across repeat / wavelength / mask;
derive stable support candidates for future adaptive peak-cluster layout.
```

Not yet started:

```text
adaptive per-cluster-radius PSF dictionary as production format;
LCD-to-PSF peak-cluster forward model training;
multi-frame joint reconstruction;
differentiable mask / GenerMask optimization.
```

The current mainline has a working baseline data contract and a first support
diagnostic layer. It does not yet have the final adaptive peak-cluster
dictionary or any learning-side forward model.

---

## Repository Boundary

`optic_system` is responsible for measured artifact construction:

```text
controls hardware;
records raw captures;
builds profile artifacts;
builds scout surveys;
builds support reports;
builds measured peak-cluster dictionaries;
exports metadata-rich artifacts.
```

`LCD_forward` is responsible for learning and inverse problems:

```text
trains LCD-to-PSF forward models;
renders multi-frame observations;
solves or trains multi-frame joint reconstruction;
optimizes differentiable masks and GenerMask parameters.
```

`optic_system` must not train forward surrogates, reconstruction networks, or
mask-optimization loops. It may generate the measured, metadata-rich artifacts
that make those downstream tasks possible.

---

## Long-Term Path

The mainline path is staged from reproducible hardware capture to adaptive
peak-cluster data artifacts, then to learning-side forward modelling:

```text
Phase 2
  -> Phase 3
  -> Phase 3.5
  -> Phase 3.6
  -> Phase 4 in LCD_forward
  -> Phase 5 in LCD_forward
  -> Phase 6 in LCD_forward
  -> Phase 7 long-term joint optimization
```

The intended representation shift is:

```text
traditional dense ROI kernel:
  one fixed window represents the PSF

mainline peak-cluster representation:
  each real diffraction peak cluster has its own support, coordinates,
  and local raw data
```

---

## Phase 0 -- Documentation and Boundary Reset

**Status: complete.**

Phase 0 re-anchored this repository as a hardware-control and
synchronized-capture frontend. It clarified that neural training,
reconstruction models, and mask optimization are outside `optic_system`.

---

## Phase 1 -- TLS SDK Integration Closure

**Status: substantially complete.**

Phase 1 made `tls_c1` the active TLS backend through the normal
application/control path. The deprecated pywinauto TLS GUI automation path is
not an active backend.

Completion baseline:

- `TLSService` can be constructed through app assembly when explicitly enabled.
- TLS state is observable through control state.
- TLS commands flow through `SessionController`.
- No-hardware tests pass by default.
- Hardware tests remain opt-in.

---

## Phase 2 -- Minimal Hardware Capture Layer

**Status: complete.**

Purpose: provide deterministic, metadata-complete raw capture without reviving
legacy task scripts.

Completed baseline:

- capture plans can be loaded and executed;
- camera, mono LCD, and optional TLS can be controlled in sequence;
- raw HDF5 captures preserve frames, masks, camera metadata, LCD metadata, TLS
  metadata, timing metadata, and processing flags;
- default tests remain hardware-free;
- hardware execution remains explicit and opt-in.

---

## Phase 3 -- Stable Capture and Profile-Aware Experimental Artifacts

Purpose: make real hardware acquisition reproducible and metadata-complete.

Phase 3 is not a broad bucket for every PSF-related task. It is the stable
capture and profile-artifact layer that supports later peak-cluster work.

### Phase 3A -- Profile-Driven Experimental Calibration

**Status: initial mainline task modules implemented.**

Artifacts and tasks:

```text
PupilProfile
CameraProfile
broadband passthrough camera safety
broadband pupil scan
per-band selected-pupil-open camera profile
```

Required principles:

- PSF-producing tasks must declare explicit `PupilProfile` and `CameraProfile`
  dependencies.
- Broadband pass-through TLS setpoint `0` is a device state, not a scientific
  wavelength.
- Full-LCD-open exposure profiles must not silently stand in for selected-pupil
  PSF capture profiles.

Mainline dependency chain:

```text
determine broadband pass-through camera profile
  -> scan LCD pupil under broadband pass-through
     (bar profiles -> radius scan -> ellipse fit)
  -> generate PupilProfile
  -> open selected LCD pupil and determine per-band camera profile
  -> all later PSF-producing capture tasks
```

Camera-profile determination uses gain-outer binary exposure search. Per-band
profile calibration must keep TLS wavelength as the outermost loop and perform
all camera probes for one wavelength before moving the spectrometer again.

The bachelor-thesis branch task logic may be used as audited reference
material, but its old ordering is not the mainline workflow.

### Phase 3B -- Full-Frame Scout and Peak-Patch Data-Contract Baseline

Artifacts and tasks:

```text
FullFramePSFSurvey
first-pass PeakLayoutProfile
fixed-size PeakPatchPSFDictionary
peak-patch LCD_forward-readable export
```

Phase 3B is a baseline data contract, not the final peak-cluster algorithm. The
fixed-size peak-patch dictionary remains useful as v1 compatibility output and
as a reproducible bridge to downstream experiments.

---

## Phase 3.5 -- Support-Aware Peak-Cluster Preparation

Purpose: convert raw/full-frame empirical diffraction evidence into reliable
peak-cluster support.

This phase prepares adaptive supports. It does not yet define the production
adaptive dictionary.

### Phase 3.5A -- Diffraction Support Analysis

Artifact:

```text
PeakSupportAnalysisReport
```

Baseline algorithm:

```text
5th-percentile background
corr = max(psf-bg, 0)
tau sweep
far-field noise/significant split
connected-component candidate table
```

### Phase 3.5B -- Real-Data Operationalization

Required work:

```text
scipy connected-component backend
streaming / energy-only support analysis
real full-frame parameter presets
explicit handling of 2048 x 2448 survey-scale data
```

### Phase 3.5C -- Support-Candidate Stability Audit

Required work:

```text
aggregate components across repeat / wavelength / mask
estimate component centroid stability
estimate energy stability
estimate hit rate
merge consistent components into support candidates
reject noise-floor or unstable components
```

The output should be a traceable support-candidate artifact, not a final layout.

### Phase 3.5D -- Adaptive Peak-Cluster Layout

Target artifact:

```text
AdaptivePeakLayoutProfile
```

Expected contents:

```text
per-cluster center
per-cluster radius or bbox
per-cluster support type: circle / square / rectangle / mask
per-cluster validity scope
```

Phase 3.5 ends when stable, traceable, adaptive peak supports can be produced
from real data.

---

## Phase 3.6 -- Adaptive Peak-Cluster PSF Dictionary

Purpose: replace fixed-size peak patches with adaptive per-cluster support.

Target artifact:

```text
AdaptivePeakClusterPSFDictionary
```

Target data model:

```text
PSF entry
  -> peak_cluster_0:
       center_xy
       support_type
       radius_px or bbox_xyxy
       raw_patch
       support_mask
       background
       energy
       peak_value
       full-frame coordinate metadata
  -> peak_cluster_1:
       ...
```

The production dictionary should not be:

```text
patches: [N_entry, K, Hp, Wp]
```

except as a compatibility baseline. Instead, it should be a variable-size or
indexed collection of per-cluster local raw data and support metadata.

Completion criteria:

- Each PSF entry stores discrete peak-cluster records.
- Different diffraction peaks within the same PSF may use different support
  radii or window sizes.
- Every cluster records original full-frame sensor coordinates.
- Circle and rectangle/square support types are represented explicitly.
- Raw local patch data is preserved.
- Optional support masks encode non-rectangular supports.
- Fixed-size peak-patch dictionary remains available only as v1 compatibility
  output.

---

## Phase 4 -- LCD_forward Peak-Cluster Forward Modelling

Purpose: move from measured data artifacts to a learnable differentiable
forward model.

This phase belongs primarily to `LCD_forward`, not `optic_system`.

Target model:

```text
M, lambda
  -> peak-cluster parameters
  -> sparse/adaptive PSF representation
  -> rendered frames
```

The model should learn or predict:

```text
cluster amplitude
cluster center shift
cluster radius / width
anisotropy
orientation
local residual or shape coefficients
```

The complexity target is:

```text
O(Kd)
```

where `K` is the number of diffraction peak clusters and `d` is the number of
degrees of freedom per cluster. If the per-cluster model family is fixed, this
becomes effectively:

```text
O(K)
```

This phase should compare against dense kernels:

```text
O(WH)
O(W^2) for square kernels
```

---

## Phase 5 -- Multi-Frame Joint Reconstruction

Purpose: use multiple LCD masks and their wavelength-dependent transfer
structures as one joint inverse problem.

The frames should be modelled jointly, not reconstructed independently and
fused afterward.

Target analysis:

```text
multi-frame H-matrix singular-value spectrum
spectral-channel separability
single-frame vs multi-frame comparison
regularized least-squares baseline
learned reconstruction baseline
```

This phase belongs to `LCD_forward` or a learning-side experiment workspace.

---

## Phase 6 -- Differentiable Mask and GenerMask Optimization

Purpose: optimize mask families under real LCD constraints.

This phase introduces:

```text
u -> GenerMask(u) -> M -> LCD-to-PSF peak-cluster forward model -> frames -> reconstruction loss
```

Rules:

- `M` remains an explicit intermediate physical object.
- `GenerMask` is a low-dimensional physical synthesis map, not an arbitrary
  neural generator.
- Different `GenerMask` families should be auditable.
- Mask optimization must preserve display feasibility, perturbation robustness,
  throughput, and support stability.

This phase belongs primarily to `LCD_forward`.

---

## Phase 7 -- End-to-End Joint Optimization

Purpose: jointly optimize mask family, dynamic coding sequence, forward model,
and reconstruction under the low-cost LCD system constraints.

This is the long-term target, not the current `optic_system` responsibility.

---

## Immediate Next PRs

The next `optic_system` PRs should focus on measured artifact construction and
diagnostics:

```text
1. scipy connected-component backend for PeakSupportAnalysisReport
2. streaming / energy-only mode for large full-frame data
3. real-data presets for support analysis
4. SupportCandidateStabilityReport
5. AdaptivePeakLayoutProfile
6. AdaptivePeakClusterPSFDictionary
```

Training and validation of the peak-cluster forward model are explicitly
deferred to `LCD_forward`.
