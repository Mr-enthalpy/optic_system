# 2026-11-27 UDC five-band measured-PSF sweep (B1)

Status: **hardware capture executed and accepted. Five narrow bands of the
LCD-camera prototype's PSF were measured under one fixed display state, and all
five passed the hard criteria.** This is development evidence for the laboratory
prototype. It is not a phone-UDC measurement, not a spectral-purity or
radiometric calibration, and not a reconstruction result.

## Question

The 2026-10-19 21-band sweep and the 2026-11-26 session both established that the
rig can produce a per-plane PSF, but neither could answer whether the *measured*
PSF carries wavelength-dependent structure that a spectral reconstruction could
use. That is what this session measures, at the same five band centres used by
the narrow-band UDC PSF sweep of Feng et al. (CVPR 2021), so the two can be
compared in kind even though the geometry differs.

Three things were asked of the data, in this order:

1. does the source actually change wavelength between bands (a session whose
   monochromator silently stopped moving invalidated 18 runs on 2026-11-26);
2. what is the plane ratio at each band, with a stated uncertainty;
3. does the normalised radial shape differ between bands, and by how much.

## Geometry and conditions as set up

| Item | Value |
| --- | --- |
| Source | 50 um pinhole, 750 mm from the phone |
| Camera | front camera id 1, `RAW_SENSOR` 3264x2448, quad-CFA BGGR, 2x2 binned, ISO 100 |
| Lens | f = 3.56 mm, f/2 (measured rig) |
| Wavelengths | 450, 500, 550, 600, 650 nm, monochromator grating 1 |
| Display | full-screen uniform black overlay, shading off, tonemap `high_quality` |
| Reference geometry | **not matched**: 750 mm here against 1 m in the reference paper |

The pinhole and the mount were not touched between bands; only the monochromator
moved. The 1 m reference geometry is the one deliberate mismatch: the bench tops
out at 75 cm.

## Method

Per band, one capture plan with an HDR ladder:

- 5-6 tiers at a geometric ratio of 4, from a per-band start tier to the 400 ms
  exposure cap;
- the start tier repeated after every tier (`t1, t0, t2, t0, ...`, so 4-5
  references per bracket), which is what makes a 4 % lamp drift measurable
  rather than confusable with sensor non-linearity;
- 3 frames per batch, fused by `tasks.measurement.hdr.merge_exposure_bracket`.

The start tier is not fixed. A ceiling probe first measured where the pipeline
stops being linear, and each band's start tier is then placed so the core peak
lands at 80 % of the span; a retry rescales from the measured peak rather than
halving blindly.

Analysis (`scripts/udc_hdr_psf/psf_analysis.py`): a 176x176 window around the
measured peak, per-CFA-phase radial bands `(0,1) (1,2) (2,3) (3,5) (5,8) (8,12)
(12,20) (20,40) (40,80)` px, plane ratios from the **integrated flux** inside
r <= 20 px (flux, not ring average: the plane's signal is proportional to the
photons it receives, and a ring average conflates that with the shape), a seam
test per ring between a tier and the next shorter one, and lamp-drift correction
from the interleaved references.

### Hard criteria and weak indicators

The split was set before the sweep ran, after the 2026-11-26 session showed that
a single seam mismatch could stop a bracket whose numbers were otherwise
reproducible. A criterion that describes how ideal the data is must not veto
whether the data is usable:

**Hard** - a failure means the band cannot be used and must be recaptured:
capture completeness and readback, no pixel of the core at the ceiling in the
shortest tier, peak above 5 % of the span, and a colour change between the two
gate bands (B/R ratio > 5, R/G ratio > 3).

**Weak** - reported with the band and carried into the analysis limits, never a
stop: seam agreement, the number of rings that could be judged at all, the
distance of the peak from the 80 % target, and the magnitude and rate of the
measured drift.

## Results

Five bands, all accepted on the hard criteria, none rejected.

| nm | start tier | peak | peak of span | core at ceiling | R/G | B/R | drift | repeatability | seams judged / outside |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 450 | 3224 us | 784 DN | 81.7 % | 0 | 0.789 | 17.42 | +0.59 % | 1.80 % | 15 / 3 |
| 500 | 789 us | 569 DN | 59.3 % | 0 | 0.0783 | 5.898 | +0.56 % | 3.49 % | 22 / 4 |
| 550 | 784 us | 720 DN | 75.0 % | 0 | 0.0980 | 1.181 | +0.30 % | 2.69 % | 23 / 3 |
| 600 | 836 us | 695 DN | 72.5 % | 0 | 2.817 | 0.0450 | -1.84 % | 4.11 % | 21 / 2 |
| 650 | 2151 us | 622 DN | 64.9 % | 0 | 7.548 | 0.0569 | +1.14 % | 2.91 % | 11 / 1 |

Span is black 64.000 DN to white 1023 DN. Repeatability is the spread of the
4-5 interleaved reference exposures of one bracket, per ring, worst judgeable
ring; it is the width of the lamp, not of the sensor.

Across the range:

- `B/R(450) / B/R(650)` = **306** (17.42 vs 0.0569);
- `R/G(650) / R/G(450)` = **9.57** (7.548 vs 0.789).

Per-plane integrated flux inside r <= 20 px, which is what those ratios are made
of:

| nm | B | Gr | Gb | R |
| --- | --- | --- | --- | --- |
| 450 | 6234.7 | 439.6 | 467.9 | 357.9 |
| 500 | 1858.0 | 3398.1 | 4646.4 | 315.0 |
| 550 | 499.0 | 3588.2 | 5035.1 | 422.6 |
| 600 | 211.8 | 1603.7 | 1738.2 | 4706.3 |
| 650 | 225.8 | 526.2 | 525.1 | 3967.3 |

The bands are not comparable in absolute signal (the monochromator throughput and
the tier each band starts at both differ), so only the ratios carry across. The
dominant plane moves blue -> green -> red as the band moves 450 -> 650 nm.

### Shape

Dominant plane of each band, cumulative fraction of the r > 2 px flux:

| nm | dominant | r <= 3 | r <= 8 | r <= 20 | half-flux band |
| --- | --- | --- | --- | --- | --- |
| 450 | B | 0.388 | 0.525 | 0.785 | 3-5 px |
| 500 | Gb | 0.342 | 0.589 | 0.806 | 3-5 px |
| 550 | Gb | 0.373 | 0.622 | 0.804 | 3-5 px |
| 600 | R | 0.414 | 0.659 | 0.817 | 2-3 px |
| 650 | R | 0.395 | 0.619 | 0.788 | 2-3 px |

The enclosed fraction at r <= 20 px stays inside 0.785-0.817 and the half-flux
radius inside 2-5 px, so no band has a qualitatively different shape. The spread
that exists is inside r <= 8 px and is of the same order as the 3-5 % per-ring
uncertainty below, so this session bounds a normalised shape difference at that
level rather than resolving one.

The peak position moves by at most 2 binned pixels (`x` 1542-1543, `y` 1223-1225)
across the five bands, i.e. <= 4 native pixels over 200 nm. Any axial chromatic
shift of this rig is below that scale.

## Completeness and consistency checks actually performed

- TLS readback: 450.0, 499.999, 550.001, 600.0, 649.999 nm. Every batch of every
  accepted band reports `capture_complete: true` and `run_succeeded: true`.
- Shortest tier of each band: no pixel at the declared white (per-frame maxima
  870/801/872, 627/647/625, 895/842/751, 765/759/754, 668/693/697 DN for
  450/500/550/600/650 nm). Longer tiers do put a few core pixels at 1023, which is
  what the fusion discards.
- Opening and closing dark sets, three batches each at 1171 / 18736 / 400000 us
  with the source occluded, 2.8 h apart: the black level is 64.000 DN in all four
  planes at both ends, the largest change in any plane any exposure being
  0.038 DN, and 0.063 DN inside the window the bands use. No plane gained offset
  while the bands were captured.
- `reanalyse_band.py` re-reads an accepted band's own frames and reproduces the
  recorded peak, clipping fraction, both plane ratios and the whole seam verdict
  bit for bit. It was written to check exactly that a code change had not moved a
  number; the one statistic it did change (repeatability on 450 nm, from 190.9 %
  to 1.80 %) was a wrong unit in the statistic, not a measurement.
- Offline suites: 444 passed, 3 skipped (`pytest -q`), including two suites that
  cover this method with synthetic frames and no hardware.

## Session shape

| Item | Value |
| --- | --- |
| Spectral gate before the sweep | passed: `B/R(450)/B/R(650)` 302.3, `R/G(650)/R/G(450)` 9.62 |
| Gate agreement with the later 450 nm band | B/R 17.31 vs 17.42 (0.6 %) |
| Broadband pass-through white anchor | core 438 DN, B/R 0.239, R/G 1.028 |
| Bands recaptured | 500 nm once (start tier clipped at 3156 us), 550 nm twice (clipped at 1064 us, then too dim at 266 us) |
| Runs created | 65; the phone kept its copy of every accepted bracket |
| Session size | 44 GB host |

The gate was first recorded as failed under the previous rule set, which treated
a single seam mismatch and a 3x3-median core reading as failures. It is kept as
written and re-judged from the same frames, with no recapture, into a separate
re-judged record that carries the previous verdict and what changed. The
re-judged gate passes on the current hard criteria; its weak indicators are the
ones tabulated above for 450 and 650 nm.

## Limits

- **Per-ring shape uncertainty is 3-5 %.** Every seam disagreement in this session
  is a longer tier reading *low* by 1.5-5 % at a ring holding 30-95 DN, i.e. a mild
  compression with level rather than a random seam. It is the same signature in
  the gate and in all five bands, and it bounds any claim about the profile
  beyond the core.
- **The drift correction is noise-limited on three of five bands** (450, 500 and
  550 nm): the straight-line residual of the reference fits (0.9-1.3 %) exceeds
  the change it reports (0.3-0.6 %), so the correction applied there is inside the
  estimator's own scatter. 650 nm is marginal in the same sense (1.11 % residual
  against a 1.14 % change). It is applied uniformly because every band of one
  sweep must be corrected the same way; the stored numbers let a reader see
  whether it mattered.
- **The plane ratios include out-of-band CFA leakage, and at 450 nm they are
  made of it.** The off-colour flux is light, not an offset: the dark set
  contributes at most 44 DN inside the same window, with alternating sign plane to
  plane. So 450 nm's `R/G` = 0.789 is a ratio of the red and green *leakages*
  (358 and 454 DN against 6235 DN in blue), and it is where those two leakages
  cross rather than a contradiction of `R/G`(500) = 0.078. For a band whose ratio
  denominator is a leakage term, read the plane fluxes, not the ratio. The
  headline contrast is conservative for the same reason: leakage lifts the minor
  plane at both ends of the range, so removing it would widen the 306x, not
  narrow it.
- **No spectral-purity claim.** The monochromator bandwidth, stray light and the
  LCD stack's own scattering are not characterised here; a "450 nm" band is the
  instrument's 450 nm setting.
- **No radiometric claim.** All numbers are DN of the RAW pipeline at one ISO and
  one shading state; no absolute spectral response, no flat-field, no
  per-band exposure normalisation across bands.
- **Five bands at 50 nm spacing, one tilt state, one session.** The 400-650 nm
  intent is not met: nothing below 450 nm was measured. The rotation experiment
  (450/550/650 nm at two roll angles, to separate a PSF that rotates with the
  display from one that does not) is a separate session and has not been run;
  the roll reader for it is committed with this method.
- The 1 m reference geometry is not matched, so this is a measurement of this rig
  and not a reproduction of the reference paper's conditions.

## Provenance

`optic_system` branch `codex/udc-task-pipeline`:

- `b580cf7` - the method: `scripts/udc_hdr_psf/` (driver, analysis, re-measure and
  summary tools, dark-set probes, tilt reader, seam/pedestal diagnostics) and
  `tests/test_udc_hdr_psf_analysis.py`, `tests/test_udc_hdr_psf_sweep.py`.

The driver that actually ran on the acquisition host has content hash
`23f31c30…a1a6`, which is what the session record stores per band. The committed
version differs from it in the module docstring only (the paragraph that used to
say the script was not part of the repository); the analysis module is byte
identical (`2f6961e1…765f`). So the recorded hash identifies the executed code
and the committed code is the same program.

Raw frames, session records and the operator-gate confirmations stay on the
acquisition host and are not in Git. This report contains no machine paths.
