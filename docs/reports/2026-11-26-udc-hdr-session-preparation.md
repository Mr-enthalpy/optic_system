# 2026-11-26 UDC HDR session preparation

Status: **preparation and software validation only. No hardware capture was executed.** The operator deferred the physical session, so this record must not be read as acquisition evidence or as a PSF measurement.

## Purpose

Prepare one attended session that would collect, under a fixed point-source geometry:

- per-band exposure brackets (base / 25x / 100x) for HDR PSF fusion;
- matched dark sets at the start and end;
- a 0.7 / 2 / 6 / 16 / 40 / 70 ms overexposure ladder at 550 nm;
- a closing 550 nm base-exposure drift reference;
- same-session lens-shading (off/on) and display-panel (black/gray/white) A/B captures;
- one full-resolution same-request JPEG companion test.

## Committed software

`optic_system` branch `codex/udc-task-pipeline`, pushed to origin:

- `b397e5c` — HDR bracket fusion plus per-batch `shading`, `tonemap`, `display` and plan-level `companion_size`; app readback fields; `scripts/fuse_udc_hdr_psf.py`.
- `e0290aa` — documentation of the fusion and control semantics.

Local `.venv` suite: **442 passed, 3 skipped**. The remote deployed copy was created by an independent clone of a full-history bundle and its own virtual environment (offline wheels): commit `e0290aa`, clean tree, targeted suite **62 passed**.

## Application build and signing

- Source commit and digest recorded by the build: `e0290aa`, dirty `false`, app-source digest `3d0cc1ed…b29f`.
- The first 0.3 build used a newly generated debug key and was rejected by the phone with `INSTALL_FAILED_UPDATE_INCOMPATIBLE` (signature mismatch). That build was never installed.
- The final APK was rebuilt from the same source with the original 2026-11-04 debug certificate (`SHA-256 230559b9…de6`) and installed successfully: `versionCode=3`, `versionName=0.3`.
- Final APK `SHA-256 246c1de7…256f`. The existing 160 phone runs and the granted camera permission were preserved; no phone data was deleted.

## Preflight readings (read-only or install-only)

| Item | Value |
| --- | --- |
| TLS | connected, idle, grating 1, readback 550.001 nm; no move command issued |
| Camera | id 1 (front), `RAW_SENSOR` 3264×2448 |
| Exposure range | 13 333 ns – 426 526 605 ns |
| ISO range | 100 – 6200 |
| White / black level | 1023 / `[64,64,64,64]` |
| `lensShadingApplied` | true |
| Shading modes | `[0,1,2]` (OFF, FAST, HIGH_QUALITY) |
| Tonemap modes | `[0,1,2]` |
| Phone free space | 87.6 GB on `/data` |
| Host free space | 204.3 GB on `D:` → 31 bands selected instead of the 16-band fallback |

## Validated plan set

35 plans were constructed and round-tripped, then each was accepted by `scripts/capture_udc.py --validate-only`:

- 31 band brackets × 3 exposures (625 µs base for 400 nm, per-band table);
- dark start and dark end at 625 / 15 625 / 62 500 µs;
- 550 nm overexposure ladder;
- 550 nm drift reference;
- lens-shading off/on at fixed exposure;
- display black/gray/white at fixed exposure;
- full-size JPEG companion with `RAW_SENSOR`.

Validation opens no devices and writes no observations. Two independent validation runs (local and deployed) both selected 31 bands and accepted all 35 plans.

## Not performed

No TLS wavelength command, no phone capture, no export/decode of new runs, no fusion, no phone-run deletion. No claim is made about saturation, seam consistency, background level, kernel sign, or spectral purity.

## Remaining limits

- The vendor UDC display patch is uncontrolled; full-screen panels are software overlays at fixed window brightness only.
- `lensShadingApplied=true` means the shading OFF/ON A/B may be a null result.
- HDR headroom weighting, far-exposure outer-ring background subtraction and seam gates have synthetic tests only.
- The session still requires an attended operator to confirm geometry, occlude for the start dark, restore the point source, and occlude again for the end dark; those confirmations are file-gated and dated.
