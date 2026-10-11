# Local experimental roadmap

The immediate objective is traceable experimental evidence of wavelength-dependent display diffraction in a UDC-like laboratory prototype. This is a local acquisition/diagnostics roadmap, not the research plan of every future repository.

1. Validate real broadband exposure calibration, broadband pupil scan and per-wavelength pupil-open camera calibration. Verify display identity, timing, readback, full-sensor extent and saturation exclusion on the actual setup.
2. Acquire repeated full-frame PSFs over wavelengths at one fixed display state. Retain raw bursts and source settings; extract experiment-selected dense ROIs without losing spectral amplitude differences.
3. Compare wavelength-dependent spatial response and energy after explicit radiometric controls. Measure repeatability, dark/background, source drift, gain/exposure dependence and sensor sensitivity; separate optical effects from acquisition artifacts.
4. Collect traceable target/material observations if the measured spectral response supports that experiment. Computational decoding can consume the measurements elsewhere; full spectral recovery is not a required deliverable of this repository.
5. Evaluate dynamic display coding as a separate experiment only after fixed-state measurements justify it.

Software regression passing is not optical or scientific acceptance. Real hardware calibration and mechanism measurements remain to be performed after the local rebuild. Future Meta Repository planning, repository integration and compatible commit pins are owned outside optic_system.
