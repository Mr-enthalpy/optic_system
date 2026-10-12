# Architecture

`optic_system` is independently runnable acquisition/calibration software for a UDC-like laboratory display-camera prototype. The hardware boundary remains:

```text
GUI -> control commands -> SessionController -> devices/capture -> hardware
CameraServiceClient -> sidecar RPC -> shared memory -> private FrameStreamClient
```

Manual/debug GUI hardware lifecycle belongs to SessionController. Device wrappers isolate LCD and optional TLS SDKs. Capture/calibration tasks deliberately use narrow synchronous adapters because movement/wait and burst capture are blocking; they share device services, not a parallel SDK integration. The read-only monitor only reads RunStatusPublisher files.

```text
app/, gui/      manual controls and preview
control/        commands, events, state, session lifecycle
 devices/       camera sidecar/transport, physical mono LCD, TLS services
capture/        preview and frame acquisition
 diagnostics/   logs and file-only run status
 tasks/         CapturePlan, explicit illumination/runtime, raw HDF5 writer
 tasks/profiles/ broadband/per-band exposure search and pupil calibration
 tasks/measurement/ small coordinate, JSON/HDF5 and survey frame readers
 tasks/psf/     full-frame survey, sensor center, dense ROI, bad-pixel correction
 scripts/       explicit calibration, capture, inspection, ROI and monitor CLIs
 plans/         minimal opt-in hardware smoke examples
 tests/         no-hardware regressions and opt-in hardware entry points
```

Data follows RawCapture -> FullFramePSFSurvey -> center/ROI -> dense measured ROI PSF -> response analysis. RawCapture v3 preserves physical observations and commits rows after frames and metadata succeed. FullFramePSFSurvey v1 copies complete successful measurements without background subtraction or normalization, retaining source camera/LCD/TLS/illumination and capture-plan indices. Dense ROI is an array plus minimal measurement metadata; there is no production ROI dictionary or generic artifact framework.

Sensor coordinates use x right, y down; frame arrays use `[H,W]`. Full-sensor extent must be verified explicitly. SensorEnergyCenterProfile stores an origin and estimator diagnostics. Experiment-selected ROI geometry is separate. Intensity processing operates on copies. Source/gain/sensor-response radiometric calibration remains an experimental responsibility.

No sibling repository import, implicit external command, cross-repository RPC or parent-name dependency is permitted. A future Meta Repository may consume these measured outputs and pin this independent repository as a Submodule; its integration plans and version pins remain outside this checkout.

Acquisition services/calibration/capture stay local to the acquisition host. Offline Survey/center/ROI processing needs only the analysis dependencies and explicit delivered-file paths. Hosts exchange closed, verified files through existing transport; no frame stream or timing-sensitive control crosses the network. See [dual-environment workflow](dual_environment_workflow.md).
