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
  "schema_version": 2,
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

`bundle_schema_version` versions this inventory envelope. The separate
`schema_version` field is the schema version of the bundled artifact and must
agree with the primary payload. A newer bundle envelope is `unsupported`; a
missing or malformed envelope version is `invalid`.

`validate_bundle()` verifies payload presence, regular-file status, byte count,
and streaming SHA-256. It also verifies the primary payload's declared media
type against actual JSON or HDF5 bytes without using a filename suffix:
JSON-primary artifacts must declare `application/json`, HDF5-primary artifacts
must declare `application/x-hdf5`, and `manifest_sidecar` must declare
`application/json`. When the explicit `data` payload role is present, it
dispatches to `check_validity(bundle.artifact_type, data_path)` and requires the
payload schema version to agree with the bundle. It does not infer an artifact
type from a filename or media type. A bundle with inventory but without an
explicit primary payload is `unsupported` for full artifact validation, not
silently treated as valid.

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

`tasks/artifacts/validation.py` distinguishes five local outcomes:

- `valid`: the declared local representation is readable and structurally
  consistent.
- `invalid`: a validator found a structural contradiction.
- `unsupported`: the type is known but no complete structural validator exists,
  or the artifact schema is newer than the local reader.
- `legacy_unversioned`: the serialized representation lacks explicit schema
  version metadata.
- `unreadable`: the location or serialized payload cannot be read or parsed.

These are not trust states. A structurally valid raw capture can remain
scientifically unreviewed, and an unsupported representation is not evidence
that it is corrupt. JSON manifest validation checks only the JSON contract;
HDF5 validation checks embedded manifests plus the HDF5 datasets/metadata that
belong to that artifact type.

Current `raw_capture` schema v3 validation requires its root identity
attributes and all fixed raw, masks, illumination, TLS, camera, LCD, profiles,
and capture metadata surfaces, including finalized written/planned capture
counts. An incomplete acquisition is represented by the `capture/completed`
bitmap and matching processing flags, not by omitting v3 datasets. The writer
sets each bitmap entry only after the frame and all row metadata have been
written. Every committed row must map the unique pair
`(wavelength_index, mask_index)` to the canonical wavelength-major
`capture_index = wavelength_index * n_masks + mask_index`.
`capture_complete` records exact coverage of that Cartesian schedule, while
`run_succeeded` records whether the enclosing run ended without an error; these
are intentionally independent. This contract is exception-safe when
finalization runs, but it is not crash-durable: row state and processing flags
are not flushed as one recoverable transaction, and resume remains deferred.
Historical
schema v2 remains structurally readable through its original narrower contract:
it does not require the later root `artifact_type`, finalized capture-count
flags, or fully initialized mask/LCD metadata. Broadband pass-through remains a
valid illumination identity:
`effective_wavelength_nm = null`, `tls_setpoint_nm = 0`, and no wavelength
label. HDF5 survey/dictionary numeric arrays use the documented `NaN` sentinel;
schema-v2 JSON manifests encode the same broadband wavelength as `null`.
Schema-v1 readers accept historical `NaN` only in approved wavelength arrays
and normalize it before validation and sidecar comparison. Schema-v2 `NaN`, all
infinities, and non-finite values in every other field are invalid.
Schema-v2 manifest and raw-v3 row validation also parse camera frame extents
without integer/string/bool coercion and reject unknown extent fields.
For support reports, the embedded `component_policy.analysis_mode` and
`component_table_written` fields must also agree with whether the HDF5
`components` group exists.

Current-schema `SensorEnergyCenterProfile` manifests must explicitly include
all per-entry background, corrected-energy, and fallback arrays. Their numeric
diagnostics are finite, corrected energies are nonnegative, and fallback values
are strict booleans. Compatibility defaults for legacy loading are not accepted
by strict validation.

Normal artifact `from_dict()` and JSON/YAML loading are strict and reject a
missing `schema_version`. A caller that intentionally handles an unversioned
representation must invoke `from_dict(..., legacy_mode=True)` explicitly;
compatibility loading does not itself migrate, register, or promote that data.

Derived HDF5 provenance is index-bound. Survey entries must satisfy
`capture_index = wavelength_index * n_masks + mask_index`. Dictionary schema v2
stores `entry_wavelength_index` and `entry_mask_index`, and each listed source
capture must resolve to that exact pair. This avoids ambiguous reverse lookup
when source plans contain repeated or equivalent illumination metadata.

Scientific payload validation is non-coercive: survey frames and dictionary
patches use real numeric dtypes; support energies, fractions, centers, radii,
counts, and component fields enforce their finite/numeric/integer/boolean
contracts. The broadband `NaN` sentinel is allowed only in documented HDF5
wavelength arrays.

`PeakPatchPSFDictionary` schema v2 also records `extent_compatibility`.
Mismatched raw/layout extents require an explicit override and non-empty reason;
successful structural validation retains a warning for that audited exception.

A syntactically readable JSON scalar or array is structurally `invalid` when a
mapping is required, not `unreadable`. A newer schema is `unsupported`, because
it may be valid under a newer reader and is not evidence of corrupt data.

Only explicit artifact-contract violations produce `invalid`. An unexpected
validator failure, missing optional validator dependency, or unhandled validator
case is `unsupported` with reason code `validator_failed`, so a validator defect
cannot be persisted later as an invalid-data decision. Strict validation checks
the raw JSON field types before running compatibility loaders; compatibility
coercions remain a task-loading concern, not a way to make malformed current
artifacts structurally valid.

Storage roots are intentionally external to the repository: `StorageConfig`
rejects a root inside the repository and a root that contains the repository.

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

Before catalog implementation, the repository still needs a canonical logical
key, immutable catalog records/events/index semantics, dependency-closure
queries, and an explicit trust-promotion policy. Destructive garbage collection
is out of scope for the first catalog version; initial tooling may only report
orphan candidates or a dry-run plan. Resume semantics for large HDF5 products
will use committed-entry markers and finalized state, while small JSON-derived
artifacts can use atomic writes.
