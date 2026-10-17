# optic_system local development rules

This repository is the independent hardware-facing acquisition and calibration platform for display-induced diffractive spectral sensing in a UDC-like laboratory prototype. Do not describe the prototype as a commercial phone UDC or claim a sensing mechanism from software tests.

## Ownership

Own hardware control, calibration, raw observations, full-frame survey measurements, sensor-center estimation, dense measured ROI PSFs and basic optical diagnostics. First measure wavelength dependence under a fixed display state. Dynamic display coding requires a separate experimental request.

Do not implement training, forward surrogates, classifiers, reconstruction models, mask optimization, phone adapters or complete spectral-cube reconstruction here. Do not introduce peak identity/cluster/support-promotion PSF architectures, generic artifact registries/catalogs/bundles/provenance graphs/digest frameworks, speculative cross-repository APIs, plugins or general experiment schedulers without a concrete requested consumer. Prefer thin functions and explicit dataclasses; remove unused code and references instead of retaining deprecated/future placeholders.

## Hardware boundaries

- GUI -> control commands -> SessionController -> device/capture services. GUI must not own SDK calls or hardware lifecycle.
- Camera SDK access belongs in the sidecar. Preserve ZMQ and shared-memory isolation, private capture streams and no-hardware imports. Blocking task adapters are an explicit narrow exception to controller dispatch; do not grow a second control framework.
- LCD is a physical mono subpixel device. User/config selects display index and axis. Axis 0 is `[3H,W]`, axis 1 `[H,3W]`. Check physical shape, preserve metadata, pack RGB only at the device boundary. Never infer device identity from resolution or silently choose a desktop display.
- TLS uses optional `tls_c1` via `devices/tls_service.py`. No vendor GUI automation, low-level SDK calls in tasks, committed DLLs or mandatory SDK import. Set target and move are separate commands. Broadband zero-order uses `set_pass_through()`, never wavelength zero.
- Use typed `IlluminationSpec`. Hardware task runtime defaults to hardware; fake/missing-device/test-settle behavior must be explicit. CLI capture requires an explicit mode.
- Changes to startup/trigger/LCD/TLS semantics require matching documentation and meaningful no-hardware tests in the same change.

## Data contract

- Never delete acquired data without the user's explicit deletion request. Preserve raw frames before conversion; outputs must not overwrite measurements.
- RawCapture records physical masks/display identity, explicit illumination, requested/readback exposure and gain, frame extent, timestamps/settles, profile IDs and completion/failure facts. A row is completed only after corresponding frames and metadata are written. Capture completeness and run success are separate facts.
- Scientific calibration/alignment validity must not be inferred from software or smoke tests. Do not add training-readiness fields to physical acquisition records.
- The data path is RawCapture -> FullFramePSFSurvey -> sensor energy center or explicit ROI -> dense measured ROI PSF -> response analysis. Keep raw and full-frame measurements intact.
- PSF-producing capture requires PupilProfile and a per-band pupil-open CameraProfile tied to that pupil. Broadband CameraProfile serves only broadband pupil scan. Check physical pupil/display identity and profile dependencies before capture.
- Sensor center is a measured coordinate origin, not a crop-size policy. Record background, corrected energy, fallback and valid-pixel exclusions. ROI sizes come from experiment configuration; use explicit half-open sensor `(x,y,width,height)` and array `[H,W]` conventions. Reject mismatched frame extent, invalid shape and wavelength/display indices.
- Preserve relative intensity. Normalization, exposure scaling and bad-pixel correction must be explicit analysis operations on copies; do not overwrite observed intensity.
- Use small hardware-free `tasks/measurement/` helpers where actually needed. Local file versions are explicit; reject incompatible layouts. Only implement targeted historical conversion for real requested data.
- The run-status monitor is file-only and hardware-free. Missing optional diagnostics do not indicate monitor failure.

## Independent maintenance

Repository code must not hardcode usernames/drives, parent directory names, sibling source trees/virtual environments or Meta Repository locations. Driver/data/private config paths come from explicit configuration/environment. Keep experiments, environments, private settings and DLLs out of Git.

Maintain this repository's branches, commits, PRs and CI independently. The maintained default/development line is `udc-main`; retain historical branches without rewriting them. When used in the UDC Meta Repository, its root AGENTS.md owns research ontology, cross-repository planning and integration acceptance; this file owns local maintenance and data/hardware rules. Do not duplicate a total cross-repository roadmap here. Compatible commit combinations belong to Meta Repository Git Submodule pins, not a hand-maintained table in optic_system. Do not create Meta Repository or `.gitmodules` here unless requested.

Record experimental acceptance in dated reports under `docs/reports/`, with the actual acquisition commit, measured conditions, completeness checks and scientific limits. Keep raw data, generated outputs and machine-specific plans/launch helpers outside Git. Distinguish an experiment's deployed commit from a later software integration commit; do not claim the later code was hardware-tested merely because it contains those fixes. A historical report remains dated evidence, not a current deployment recipe or device default.

## Tests

Use this checkout's `.venv/Scripts/python.exe -m pytest -q`. Default tests must require no hardware or DLLs. Real camera/LCD/TLS tests are opt-in; report exactly whether they ran. Retain regressions for profile serialization/dependencies, calibration algorithms, raw commit failures, sensor coordinates/indexing and synthetic RawCapture -> Survey -> ROI intensity preservation. The scientific criterion is reliable traceable observations of wavelength-dependent diffraction and spectral/material information, not reconstruction PSNR or optimized mask conditioning.
