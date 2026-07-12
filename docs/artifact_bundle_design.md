# Artifact Bundle And Catalog Location Design

## Status

This is the target contract for a later catalog implementation. It does not
change existing task output paths, rewrite legacy manifests, or provide a
global artifact lookup service.

## Location And Bundle Boundary

A future catalog location identifies an artifact generation directory, not an
arbitrary HDF5 file or JSON sidecar:

```json
{
  "storage_root": "primary",
  "rel_path": "artifacts/full_frame_psf_survey/survey_20260712_001"
}
```

`rel_path` is always relative to the configured root. `StorageConfig.resolve()`
checks the resolved path remains under that root, including after resolving
existing junctions or symlinks.

Each generation directory will contain one canonical bundle manifest and its
payloads. A representative future manifest is:

```json
{
  "artifact_id": "survey_20260712_001",
  "artifact_type": "full_frame_psf_survey",
  "schema_version": 1,
  "payloads": {
    "data": {
      "rel_path": "survey.h5",
      "media_type": "application/x-hdf5",
      "size_bytes": 123,
      "sha256": "sha256:..."
    },
    "manifest_sidecar": {
      "rel_path": "survey.manifest.json",
      "media_type": "application/json",
      "sha256": "sha256:..."
    }
  }
}
```

The bundle validator must verify payload presence, containment, digest, media
type, and artifact-specific agreement between HDF5 and any JSON sidecar. The
current `check_validity()` helper is not a bundle or HDF5 validator; unsupported
types fail closed until those validators are implemented.

## References And Legacy Paths

Future `ArtifactRef` values identify dependencies by artifact type, artifact
ID, and schema version only. A separate `ArtifactLocation` record owns
`storage_root` plus `rel_path`. Task-local legacy fields such as
`source_raw_capture_h5`, `source_survey_h5`, and `peak_layout_profile` remain
readable during migration, but they are not portable catalog references and
must not be copied into catalog records.

There is no automatic lookup by artifact ID in the current task APIs. Callers
continue to pass explicit paths or objects until a catalog contract is added.

## Lifecycle Model

Catalog state must remain orthogonal:

- `artifact_type` identifies the payload kind.
- `run_state` records `pending`, `in_progress`, `complete`, or `failed`.
- `validity_state` records `unknown`, `valid`, or `invalid` structural status.
- `selection_state` records `active`, `superseded`, or `quarantined` use policy.
- trust flags record acquisition, alignment, calibration, and downstream-use
  decisions independently.

An immutable artifact record, append-only lifecycle events, and a small mutable
current-selection index are preferred over one ever-growing record per logical
key. All mutable catalog writes must use a temporary file followed by atomic
replacement.

Catalog selection must require matching logical-key fields, finalized payload
digest verification, structural validity, required trust flags, and dependency
closure. A cleaner creates a new candidate generation; it never modifies a raw
capture, automatically promotes itself, or automatically supersedes the prior
generation.

## Deferred Implementation

Before catalog implementation, the repository needs artifact-specific JSON and
HDF5 validators, payload inventories and digests, and a canonical logical-key
definition. Destructive garbage collection is out of scope for the first
catalog version; initial tooling may only report orphan candidates or a dry-run
plan. Resume semantics for large HDF5 products will use committed-entry markers
and finalized state, while small JSON-derived artifacts can use atomic writes.
