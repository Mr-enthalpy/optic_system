# Cross-Repository Boundary

This document defines placeholder boundaries for the four-repository system.
It is normative for `optic_system`, but it does not define implemented APIs or
final external schemas.

## Repository Roles

`lcd_mask_families` owns reusable mask-family definitions, schemas, generators,
projection rules, and versioned mask identity.

`optic_system` owns hardware integration, visualization, device control,
synchronized acquisition, raw capture preservation, profile-aware measured
artifacts, full-frame surveys, support/stability/layout diagnostics, and
measured evidence handoff publication.

`LCD_forward` owns LCD mask-to-peak-cluster/operator modelling,
LCD-to-measured-response surrogate learning, mask-family evaluation, parameter
selection, operator-aware mask-sequence design from measured evidence,
peak-cluster operator package generation, and H-matrix/operator diagnostics.

`reconstruction` owns inverse-problem solving, forward/adjoint consumption,
reconstruction pipelines, learned reconstruction, task-level evaluation, and
reconstruction-driven capture-plan proposals.

## Allowed `optic_system` Inputs

- Explicit masks, mask specs, or mask sequence specs from
  `lcd_mask_families`, once that repository exists.
- Capture-plan handoff requests from `LCD_forward` or `reconstruction`.
- Local hardware configuration, runtime policy, and capture-task plans.

`MaskSpecHandoff` now has an experimental local consumer:
`capture.mask_family_adapter` can optionally render `lcd_mask_families` v0.1
mask instance or sequence specs through that package's public API. This adapter
does not move mask-family ownership into `optic_system`; it is a bounded
execution wrapper. Profile-unaware rendering is dry-run/offline only. Real
capture use must bind the rendered mask identity to an optic_system
`PupilProfile` and strictly embed it into a full physical LCD mask before any
array reaches `LCDService`.

Other inputs remain future handoff categories. `optic_system` must not
implement external repository imports, clients, or schema validators before
those contracts exist.

## Allowed `optic_system` Outputs

- Raw capture HDF5 and metadata-first measured artifacts.
- Profile artifacts, full-frame surveys, support/stability/layout diagnostics,
  and adaptive peak-cluster dictionary evidence.
- Measured evidence handoffs for `LCD_forward`.
- Measured response or target-capture handoffs for `reconstruction`.

Large acquired data should live in third-party storage or ignored output paths.
Committed handoffs should normally be manifests, small specs, or examples.

## Explicitly Out of Scope

`optic_system` must not own:

- mask-family design or final external mask-family schemas;
- LCD-to-response surrogate training;
- peak-cluster operator package generation;
- forward/adjoint operator implementation for reconstruction;
- reconstruction pipelines or learned reconstruction;
- hidden calls into `lcd_mask_families`, `LCD_forward`, or `reconstruction`;
- generated large artifacts in Git.

## Handoff Categories

The categories below reserve boundaries only. They are not implemented APIs.

### MaskSpecHandoff

Source: `lcd_mask_families`.

Consumers: `optic_system`, `LCD_forward`, and `reconstruction`.

Meaning: shared mask family identity, mask instance specs, explicit masks, or
mask sequence specs.

Current status: experimental optional wrapper only. `optic_system` may consume
`lcd_mask_families` v0.1 specs through `capture.mask_family_adapter`, converting
rendered masks into local neutral objects. The render-only helpers are
profile-unaware and must not be treated as hardware-capture-ready. Capture
intended use requires PupilProfile identity and effective LCD pupil geometry:
`pupil_profile_id`, coordinate convention, display index, subpixel axis,
physical center, and an explicit `aperture_window`. Strict physical embedding
also requires the full `lcd_shape_hw`, validates exact local-mask/window shape,
and rejects out-of-bounds placement. It does not resize, crop, pad,
interpolate, wrap, or infer placement from center/radius. `lcd_mask_families`
continues to own family definitions, parameters, grid semantics, projection
rules, rendering, and stable mask hashes.

### MeasuredEvidenceHandoff

Source: `optic_system`.

Consumer: `LCD_forward`.

Meaning: manifests or references to measured evidence such as
`FullFramePSFSurvey`, `SensorEnergyCenterProfile`, support reports, stability
reports, layout profiles, and adaptive peak-cluster dictionaries.

### MeasuredResponseHandoff

Source: `optic_system`.

Consumer: `reconstruction`.

Meaning: measured response or target-capture references suitable for future
reconstruction experiments.

### OperatorHandoff

Source: `LCD_forward`.

Consumer: `reconstruction`.

Meaning: learned or fitted operator packages, H-matrix diagnostics, and
forward/adjoint package references.

### CapturePlanHandoff

Source: `LCD_forward` or `reconstruction`.

Consumer: `optic_system`.

Meaning: future capture-plan requests proposed from operator diagnostics,
mask-family evaluation, or reconstruction evaluation.

These categories should remain placeholders until the external repositories
exist and their contracts are explicitly designed.
