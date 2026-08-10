# Raw Capture HDF5 Schema

Canonical schema as written by `RawCaptureWriter` (`tasks/raw_capture_h5.py`).
Schema version: 3. Historical schema-v2 files remain historical inputs; this
writer does not rewrite or silently reinterpret them.

## Deleted fields (do not reintroduce)

- `nominal_wavelength_nm` — deleted; use `illumination_json` → `illumination.mode`
- `camera.average_burst` — deleted; use `store_burst` at plan level
- `/camera/exposure_us`, `/camera/gain_db` — never existed in this schema;
  the actual fields are `requested_exposure_us`, `requested_gain_db`,
  `readback_exposure_us`, `readback_gain_db`
- pass-through is `illumination.mode=broadband_passthrough`, not a wavelength
  value of any kind

## Root attributes

| Attribute | Type | Description |
|-----------|------|-------------|
| `plan_id` | string | Capture plan identifier |
| `created_at_ns` | int64 | Writer creation timestamp (monotonic ns) |
| `software_version` | string | `"optic_system"` |
| `artifact_type` | string | `"raw_capture"` (required in schema v3) |
| `raw_capture_schema_version` | int | Schema version (currently 3) |
| `capture_role` | string | One of `minimal_capture`, `profile_capture`, `psf_capture`, `survey_capture` |
| `hdf5_writer_version` | string | Writer version (`"1.0"`) |

## `/raw` — Frame data

| Dataset | Shape | Dtype | Description |
|---------|-------|-------|-------------|
| `frames_avg` | `[N_capture, H, W]` | policy dtype (default float32) | Per-capture averaged frames |

| Dataset | Shape | Dtype | Description |
|---------|-------|-------|-------------|
| `frames` | `[N_capture, K, H, W]` | policy dtype | Raw burst frames (only if `store_burst=True`) |

### `/raw` attributes

| Attribute | Type | Description |
|-----------|------|-------------|
| `store_burst` | bool | Whether burst frames are stored |
| `frames_per_capture` | int | Burst size K |
| `storage_policy_json` | string | Full `RawFrameStoragePolicy` serialized |
| `average_compute_dtype` | string | Intended averaging precision |
| `frames_avg_stored_dtype` | string | dtype of `frames_avg` |
| `burst_stored_dtype` | string | dtype of `frames` (or `"preserve_input"`) |
| `frame_height` | int | Frame height in pixels |
| `frame_width` | int | Frame width in pixels |
| `frames_avg_input_dtype` | string | Input dtype before cast |
| `burst_input_dtype` | string | Burst input dtype (only if `store_burst=True`) |

## `/masks` — LCD mask data

| Dataset | Shape | Dtype | Description |
|---------|-------|-------|-------------|
| `masks_physical` | `[N_mask, Hlcd, Wlcd_phys]` | uint8 | Physical mono mask arrays |
| `mask_id` | `[N_mask]` | string | Per-mask identifier |
| `family_id` | `[N_mask]` | string | Mask family identifier |
| `family_params_json` | `[N_mask]` | string | Family parameter JSON |
| `has_mask_array` | `[N_mask]` | bool | Whether array data was written for this mask |

### `/masks` attributes

| Attribute | Type | Description |
|-----------|------|-------------|
| `mask_count` | int | Total number of mask slots |

## `/illumination` — Illumination parameters

| Dataset | Shape | Dtype | Description |
|---------|-------|-------|-------------|
| `illumination_json` | `[N_illumination]` | string | Full illumination spec as JSON |
| `tls_setpoint_nm` | `[N_illumination]` | float64 | TLS setpoint value (0.0 for pass-through, NaN if N/A) |
| `effective_wavelength_nm` | `[N_illumination]` | float64 | Effective wavelength (NaN for broadband) |

## `/tls` — TLS device status

| Dataset | Shape | Dtype | Description |
|---------|-------|-------|-------------|
| `grating` | `[N_illumination]` | int64 | Grating number |
| `settle_ms` | `[N_illumination]` | int64 | Settle time in ms |
| `timestamp_ns` | `[N_illumination]` | int64 | TLS status timestamp |
| `status_json` | `[N_illumination]` | string | Full TLS status dict as JSON |

## `/camera` — Camera parameters

| Dataset | Shape | Dtype | Description |
|---------|-------|-------|-------------|
| `requested_exposure_us` | `[N_capture]` | float64 | Requested exposure (µs), -1 if none |
| `requested_gain_db` | `[N_capture]` | float64 | Requested gain (dB), -1 if none |
| `readback_exposure_us` | `[N_capture]` | float64 | Read-back exposure (µs), -1 if unavailable |
| `readback_gain_db` | `[N_capture]` | float64 | Read-back gain (dB), -1 if unavailable |
| `frame_extent_json` | `[N_capture]` | string | `CameraFrameExtent` as JSON |
| `timestamp_ns` | `[N_capture]` | int64 | Camera timestamp |
| `status_json` | `[N_capture]` | string | Full camera status dict as JSON |

## `/lcd` — LCD device metadata

| Dataset | Shape | Dtype | Description |
|---------|-------|-------|-------------|
| `settle_ms` | `[N_capture]` | int64 | LCD settle time per capture |
| `display_timestamp_ns` | `[N_capture]` | int64 | Timestamp after mask display |
| `mapping_policy_json` | scalar | string | Axis-aware mapping policy |
| `metadata_json` | scalar | string | LCD metadata (display_index, shapes, subpixel_axis) |

## `/profiles` — Profile identifiers

| Dataset | Shape | Dtype | Description |
|---------|-------|-------|-------------|
| `requirements_json` | scalar | string | Plan `requires` block as JSON |
| `pupil_profile_id` | scalar | string | Pupil profile identifier |
| `camera_profile_id` | scalar | string | Camera profile identifier |

## `/capture` — Capture indexing

| Dataset | Shape | Dtype | Description |
|---------|-------|-------|-------------|
| `capture_index` | `[N_capture]` | int64 | Sequential capture index |
| `wavelength_index` | `[N_capture]` | int64 | Index into `/illumination/` and `/tls/` rows |
| `mask_index` | `[N_capture]` | int64 | Index into `/masks/` rows |
| `burst_count` | `[N_capture]` | int64 | Frames per burst |
| `completed` | `[N_capture]` | bool | Whether capture completed |
| `plan_json` | scalar | string | Full capture plan as JSON |
| `plan_id` | scalar | string | Capture plan identifier |
| `runtime_mode` | scalar | string | Runtime mode (`hardware`, `no_hardware`, etc.) |
| `runtime_policy_json` | scalar | string | Full runtime policy as JSON |
| `processing_flags_json` | scalar | string | Processing flags (scientific validity, training readiness) |

Schema v3 uses the wavelength-major schedule:

```text
capture_index = wavelength_index * n_masks + mask_index
```

Completed rows have unique capture indices and unique wavelength/mask
combinations. A complete capture exactly covers the full Cartesian product.
The writer sets
`capture/completed[row]` only after the frame and all row metadata have been
written. The first committed capture fixes the spatial shape of both
`frames_avg` and the optional burst dataset; later appends with a different
frame shape fail without resizing or changing earlier committed rows.

Illumination and TLS datasets are shared by every capture with the same
`wavelength_index`. The first committed capture for an index establishes those
values. Later captures must supply exactly matching shared metadata or the row
is rejected without changing the established wavelength entry.

Processing flags record `n_captures_written` and `n_captures_total`,
which must agree with the capture bitmap and planned frame rows. They also
separate:

- `capture_complete`: whether committed rows exactly cover the planned schedule;
- `run_succeeded`: whether finalization recorded no task error;
- `error`: the task error string, or null when `run_succeeded` is true.
- `last_completed_capture_index`: the capture index stored on the final row
  whose `capture/completed` commit marker is true, or `-1` when none are true.

A run may therefore have `capture_complete: true` and `run_succeeded: false`
when all captures were committed before a later task failure. These fields and
the root `artifact_type` are v3 requirements; they are not retroactively
imposed on schema v2 files.

This v3 writer contract is exception-safe when `finalize()` runs. It does not
provide a crash-durable transaction between row completion and processing-flag
updates, and it does not implement resume. Crash recovery and explicit
finalized/in-progress state remain a later durability task.
