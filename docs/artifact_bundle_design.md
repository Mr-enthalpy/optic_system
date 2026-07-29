# Artifact Bundle And Catalog Location Design

## Status

The local bundle data structures and integrity helpers now exist in
`tasks/artifacts/bundle.py`. They do not change existing task output paths,
rewrite legacy manifests, provide global artifact lookup, or implement a
catalog.

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

Each generation directory may contain one canonical bundle manifest and its
payloads. The current `ArtifactBundleManifest` serializes the following
representation:

```json
{
  "bundle_schema_version": 1,
  "artifact_id": "survey_20260712_001",
  "artifact_type": "full_frame_psf_survey",
  "manifest_schema_version": 2,
  "payload_schema_version": 1,
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
      "size_bytes": 456,
      "sha256": "sha256:..."
    }
  }
}
```

`ArtifactLocation` stores only `storage_root` plus a relative generation
directory. `ArtifactPayload` records a relative payload path, declared media
type, size, and canonical `sha256:<64 lowercase hexadecimal characters>`.
Payload paths cannot be absolute or contain parent traversal, and local
validation repeats containment after resolving the candidate path under the
generation directory. Payload roles must also use distinct canonical relative
paths. Validation checks normalized resolved paths and `Path.samefile()`, so an
alleged `manifest_sidecar` cannot point to the same physical file as `data`
through case differences, symlink aliases, or hard links.

`bundle_schema_version` versions only this inventory envelope.
`manifest_schema_version` identifies the canonical JSON contract and
`payload_schema_version` identifies the HDF5/container contract. An axis that
does not apply is serialized as `null`; it is never inferred from the other
axis. A newer bundle envelope or artifact contract is `unsupported`; a missing
or malformed required version is `invalid`.

`validate_bundle()` verifies payload presence, regular-file status, byte count,
and streaming SHA-256. It also verifies the primary payload's declared media
type against actual JSON or HDF5 bytes without using a filename suffix:
JSON-primary artifacts must declare `application/json`, HDF5-primary artifacts
must declare `application/x-hdf5`, and `manifest_sidecar` must declare
`application/json`. Bundle schema v1 fixes `data` as its only primary payload
role: callers cannot select another role for artifact validation. It dispatches
to `check_validity(bundle.artifact_type, data_path)` and compares manifest and
payload versions independently with the bundle. It does not infer an artifact type
from a filename or media type. A bundle with inventory but without `data` is
`unsupported` for full artifact validation, not silently treated as valid.

### Bundle And Native Identity

`ArtifactBundleManifest.artifact_id` is the immutable identity of one external
storage generation. It is deliberately not required to equal a task-native ID
such as `survey_id`, `dictionary_id`, or `camera_profile_id`: those IDs remain
inside the validated payload manifest. This is the generation-identity model
that the future catalog will use. A catalog record may later make the native-ID
relationship explicit, but this local bundle foundation neither invents a
second native-ID field nor infers equality from a filename.

Payload-native IDs must still be internally consistent. For example, HDF5
embedded manifests and declared JSON sidecars are compared canonically, so two
payloads that name different surveys cannot form a valid bundle even when the
bundle generation ID is intentionally different.

When `manifest_sidecar` is declared, integrity verification alone is not
sufficient. The sidecar must parse as the declared artifact type and schema,
and it must canonically equal the primary payload's manifest: the direct JSON
manifest for JSON-primary artifacts or the canonical embedded manifest for the
supported HDF5 products. A differing artifact ID, schema version, or metadata
is an `invalid` `manifest_sidecar_mismatch`, even when both payload digests
match their inventory records.

Bundle JSON writes use a temporary file, flush/close, and `os.replace`. The
validator does not register an artifact, choose a current generation, promote
trust, supersede a predecessor, or write catalog events.

## Local Validation Boundary

Bundle validation composes the artifact validator selected by the declared
`artifact_type`; it does not redefine JSON or HDF5 contracts. Structural
validity remains distinct from scientific trust and catalog selection.

Storage roots are external to the repository. `StorageConfig` rejects both a
root inside the repository and a root that contains the repository.

## References And Legacy Paths

Future `ArtifactRef` values identify dependencies by artifact type, artifact
ID, and manifest schema version only. A separate `ArtifactLocation` record
owns `storage_root` plus `rel_path`. Task-local legacy fields such as
`source_raw_capture_h5`, `source_survey_h5`, and `peak_layout_profile` are
readable only through exact schema-v1 adapters. Current schema-v2 manifests use
artifact IDs. Explicit migrations discard the old location after the caller
supplies the dependency identity; a legacy path is never promoted into a
catalog reference.

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

Before catalog implementation, the repository still needs a canonical logical
key, immutable catalog records/events/index semantics, dependency-closure
queries, and an explicit trust-promotion policy. Destructive garbage collection
is out of scope for the first catalog version; initial tooling may only report
orphan candidates or a dry-run plan. Resume semantics for large HDF5 products
will use committed-entry markers and finalized state, while small JSON-derived
artifacts can use atomic writes.
