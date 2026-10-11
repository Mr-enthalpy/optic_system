# Active task modules

- `capture_plan.py`, `illumination.py`, `runtime_mode.py`: explicit experiment configuration, illumination and execution policy.
- `capture_dataset.py`, `hardware_protocols.py`, `raw_capture_h5.py`: narrow synchronous acquisition and RawCapture v3; fake backends live only in `testing/`.
- `profiles/`: CameraProfile / PupilProfile serialization, dependency semantics, broadband/per-band exposure search and broadband physical pupil scans.
- `measurement/`: small hardware-free coordinate, HDF5 string/mask and survey frame helpers. No registry, adapter catalog or artifact graph.
- `psf/build_full_frame_psf_survey.py`: complete raw observations to dense full-frame survey v1, without radiometric normalization.
- `psf/sensor_energy_center.py`: background-corrected centroid estimator and coordinate-origin profile diagnostics.
- `psf/roi.py`: experiment-sized dense sensor ROI extraction and explicit copy-based response scaling.
- `psf/bad_pixel_correction.py`: deterministic nearest-valid correction on an analysis copy.
- `psf/profile_requirements.py`: narrow CameraProfile/PupilProfile dependency checks.

See [workflow](../docs/profile_task_chain.md) and [data contract](../docs/raw_capture_schema.md). No training or external repository invocation belongs in tasks.
