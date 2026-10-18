# Local experimental roadmap

The acquisition platform supports measured fixed-display wavelength-response studies in a UDC-like laboratory prototype. Detailed evidence is recorded once in dated reports: the [initial three-band measurement](reports/2026-10-17-fixed-display-psf.md) and the [21-band dataset after correcting the optical angle](reports/2026-10-19-21-band-fixed-display-psf.md).

## Completed initial measurement

Real broadband exposure calibration, broadband pupil scanning and per-band pupil-open camera calibration were followed by three 450/550/650 nm runs, three reference runs and matching darks. Full-frame raw data, verified delivery, Survey/ROI amplitude preservation and initial response/OTF diagnostics were checked. Independent acquisition and hardware-free offline analysis remain separate environments.

The configured system showed repeatable wavelength-dependent shape differences. This establishes initial measurement feasibility, not an isolated display-layer mechanism, calibrated spectral efficiency or material/task information gain. In particular, 650 nm amplitude variation and Bayer/lens/source/slit contributions remain material limits.

## Completed 21-band measurement

Following an operator-reported angle displacement and correction, broadband/pupil and all 21 per-band calibrations were repeated. The new fixed-display dataset covers 450–650 nm at 10 nm setpoint sampling, with 32 raw frames per band through 640 nm and 256 at 650 nm. Independent reference observations and matched pre/post darks accompany it. All 2048 formal illuminated/dark frames passed production-hash and exact mean/variance checks; full-frame Surveys and common 512/1024 ROIs preserve measured amplitudes.

All bands produced unsaturated signal. Reference mean shapes were stable within the recorded same-session differences; 650 nm retains greater amplitude fluctuation, and intermediate bands have only one complete scan. Weak structure extends beyond the core ROI. Bandwidth, filter response and isolated LCD attribution remain unmeasured. The report provides the per-band quality table and these limits; 10 nm sampling is not a spectral-resolution result.

## Research review before further acquisition

Pause new experiment design after delivery of this dataset. Review the measured overview and uncertainty before separately deciding whether to pursue low-dimensional spectral features, material discrimination or a safe comparable display-control experiment. Do not fit lamp instability into the system PSF model, assume averaging removes drift, or automatically disassemble/reconfigure the optical path. Wavelength-specific finite averaging is permitted when supported by actual reference and convergence observations.

New target/material measurements, additional diversity, dynamic display coding and control configurations require explicit scope and acceptance criteria. No classifier, decoder training, optical reconfiguration or new acquisition follows automatically from completing this scan.

The Meta Repository owns overall research decisions and compatible member pins. This repository owns local acquisition/calibration, raw facts, basic diagnostics and their tests. No training, reconstruction or generic experiment framework is required by this roadmap.
