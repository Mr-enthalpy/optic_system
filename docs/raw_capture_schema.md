# Raw Capture HDF5 Schema

Canonical schema as written by `RawCaptureWriter` (`tasks/raw_capture_h5.py`).
Schema version: 2.

## Root attributes

| Attribute | Type | Description |
|-----------|------|-------------|
| `plan_id` | string | Capture plan identifier |
| `created_at_ns` | int64 | Writer creation timestamp (monotonic ns) |
| `software_version` | string | `"optic_system"` |
| `raw_capture_schema_version` | int | Schema version (currently 2) |
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
