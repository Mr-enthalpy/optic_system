# optic_system Roadmap

## Current Status

Completed:

- minimal hardware capture layer: camera/LCD/TLS synchronized control, raw HDF5 export
- full-frame scout survey and fixed-size peak-patch baseline data contract
- first-pass diffraction support analysis report

Active:

- hardware pipeline testing: profile-driven calibration chain hardware validation
- support-candidate stability audit

## Remaining Work

### Hardware Pipeline Testing and System Calibration

- broadband pass-through `CameraProfile` hardware validation
- broadband LCD pupil scan hardware validation
- `PupilProfile` hardware validation
- selected-pupil-open per-band `CameraProfile` hardware validation
- hardware-data-driven profile task fixes

### Support Stability Audit and Adaptive Peak Clustering

- `SupportCandidateStabilityReport`
- `AdaptivePeakLayoutProfile`
- `AdaptivePeakClusterPSFDictionary`

### Cross-Repository Collaboration Interfaces

- evolve `handoffs/` placeholders toward working collaboration protocols when external repository contracts exist
- measured-evidence handoff publication (for `LCD_forward` consumption)
- measured-response handoff publication (for `reconstruction` consumption)
- reserve and define external-repository capture plan consumption protocol (placeholder until external repos exist)

### Deferred Artifact Data Management

Artifact-ID provenance, bundle integrity, and catalog/index work are deferred
until multiple artifact generations must be located, selected, superseded, or
cleaned up. When that need becomes concrete, implement the data-management
sequence in this order:

```text
minimal artifact identity + catalog/index
  -> provenance references resolved from source artifacts
  -> optional integrity/hash policy
```

Current task APIs continue to use explicit paths or typed objects. They must not
require callers to supply both a source path and a manually duplicated source ID.

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

Peer repositories are responsible for the remaining research loop:

```text
lcd_mask_families:
  reusable mask-family definitions, schemas, generators, projection rules, and versioned mask identity

LCD_forward:
  measured-response/operator modelling
  mask-family evaluation, parameter selection, operator-aware mask-sequence design, and operator diagnostics from evidence
  H-matrix/operator diagnostics

reconstruction:
  inverse-problem solving
  forward/adjoint consumption
  reconstruction pipelines and evaluation
```

`optic_system` must not train forward surrogates, generate operator packages,
own mask-family design, build reconstruction networks, or run
mask-optimization loops. It may generate the measured, metadata-rich artifacts
and handoffs that make those external tasks possible.

See [`docs/cross_repository_boundary.md`](cross_repository_boundary.md) for the
normative handoff boundary.

## Design Intent

The intended representation shift from traditional dense ROI kernels to
adaptive peak-cluster representation:

```text
traditional dense ROI kernel:
  one fixed window represents the PSF

mainline peak-cluster representation:
  each real diffraction peak cluster has its own support, coordinates,
  and local raw data
```
