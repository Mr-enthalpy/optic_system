# Local experimental roadmap

The acquisition platform now supports a measured fixed-display wavelength-response study in a UDC-like laboratory prototype. Detailed evidence and limits are recorded once in the [2026-10-17 acceptance report](reports/2026-10-17-fixed-display-psf.md); this roadmap defines the next local measurement needs.

## Completed initial measurement

Real broadband exposure calibration, broadband pupil scanning and per-band pupil-open camera calibration were followed by three 450/550/650 nm runs, three reference runs and matching darks. Full-frame raw data, verified delivery, Survey/ROI amplitude preservation and initial response/OTF diagnostics were checked. Independent acquisition and hardware-free offline analysis remain separate environments.

The configured system showed repeatable wavelength-dependent shape differences. This establishes initial measurement feasibility, not an isolated display-layer mechanism, calibrated spectral efficiency or material/task information gain. In particular, 650 nm amplitude variation and Bayer/lens/source/slit contributions remain material limits.

## Next measurements

1. Record source-power stability and actual spectral bandwidth; add reference and dark observations across time. Investigate the larger 650 nm fluctuations without discarding failed observations or assuming averaging removes drift.
2. Agree a safe comparable reference configuration with the operator to separate display contribution from lens, sensor and illumination effects. Do not disassemble the optical path automatically.
3. Repeat the fixed display measurement over longer times and varied legal wavelength orders, using the same verified geometry and explicit raw-amplitude/normalized-shape analyses. Establish useful frequencies and uncertainty from actual repeats before selecting more wavelengths.
4. Supply traceable observations to spectral-observability analysis when the measured diversity and noise support it. Target/material measurements or dynamic display coding need a separate experiment and acceptance criteria; neither follows automatically from visible PSF differences.

The Meta Repository owns overall research decisions and compatible member pins. This repository owns local acquisition/calibration, raw facts, basic diagnostics and their tests. No training, reconstruction or generic experiment framework is required by this roadmap.
