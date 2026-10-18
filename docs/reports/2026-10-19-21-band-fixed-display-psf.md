# Fixed-display PSF dataset: 450–650 nm at 10 nm sampling

Measurement session: 2026-10-18 to 2026-10-19, Asia/Shanghai. Experiment ID: `spectral21_20261018_reset01`.

All 21 nominal wavelengths produced valid, unsaturated full-sensor observations. The main scan contains 896 raw frames: 32 per wavelength from 450 through 640 nm, and 256 at 650 nm. Independent 450/550/650 nm references add 960 frames; matched pre/post darks add 192. This is one complete wavelength scan with reference checks, not independent temporal replication of every band or demonstrated 10 nm spectral resolution.

The operator reported an intervening imaging-system angle displacement and corrected it. This session therefore establishes a new baseline. Earlier observations and the [2026-10-17 report](2026-10-17-fixed-display-psf.md) remain historical evidence; their geometry, Profiles and darks were not substituted into this dataset.

## Source, apparatus and calibration

Acquisition used the clean, independent deployed commit `18dd598852b1d44ea80b4f6d53e1d2d5656f9e9b`. The development source was `e616214ddd7c936648a764555cb9bc9ec0254748`; advancing integrated source did not change the instrument deployment. Windows acquisition used Python 3.13.2 x64, flycapture2_c 0.6.0, tls_c1_spectrometer 0.1.0rc0 and pygame 2.6.1. Offline analysis used the repository's Python 3.13.5 environment without importing camera or TLS SDKs.

Finite experiment-specific launch/observation helpers, plans and raw calibration bursts are retained with the private experiment record, outside Git. Capture helper SHA-256 was `0461FB87BC4DF0A4965786F157515E69E47AE640F24C94F231D2BCA7569CE448`; desktop launcher SHA-256 was `BB2BE44660BF3A791DF4052BDA5D41CFABFFB8D43D18497DAFC223084C75283F`. The production device services, CapturePlan and RawCapture writer were reused. Hardware timing remained local in the confirmed interactive desktop session; SSH coordinated the existing authorized demand-only task.

The source was a xenon lamp and fixed point target. The mechanical output slit had no reliable numeric width record and was held unchanged. The operator reported an external 450–650 nm filter before the LCD. Its transmission curve, monochromator bandwidth, spectral purity and absolute source power were not independently calibrated. TLS serial was OM319069, grating 1. Nominal 10 nm spacing describes setpoint sampling only. Monochromatic waits and camera settling/discarded frames are preserved in plans and diagnostics; broadband used explicit pass-through, never wavelength zero.

Camera serial 15471217 used RAW8 BayerGB, full-scale 255 DN, full sensor `[2048,2448]`, Format7 origin `(0,0)`, gamma 1, gain 0 dB, trigger disabled and frame-rate setting 20 fps. RAW8 does not establish native ADC bit depth. Only row 0, x=0…7 contains verified embedded timestamp/counter bytes; those bytes were preserved but excluded from optical peak/saturation statistics. No entire sensor row or unexplained optical pixels were discarded.

LCD identity remained manually verified display index 1, subpixel axis 1, RGB shape `[2560,540,3]`, logical `[2560,540]`, physical mono `[2560,1620]`. Remote privacy-screen mode was disabled. Every illuminated run used the same physical pupil-open mask, SHA-256 `A2BCDA51D641FBAC5BF6E8315ADFE31DEECCB43526EDEFE29653E19865BB4A31`.

Because geometry had changed, calibration was repeated rather than assuming the old pupil remained valid:

| Stage | Actual result |
| --- | --- |
| Broadband01 | Two 32-frame probes; actual 6.2584877 µs, 0 dB; maxima 154/155 DN, no saturation |
| Coarse pupil | 84 measurements / 2688 frames; full-panel localization, maximum 171 DN |
| Fine pupil | 100 measurements / 3200 frames; maximum 171 DN; LCD center `(1063.8871,1877.9593)`, circular radius 54.5539, window `(1009,1823,110,110)` |
| Pupil fit | Ellipse axes 118.3298/60.6155; R² 0.99912585; RMSE 3381 DN-sum against an approximately 300k plateau; effective illuminated pupil, not mechanical ground truth |
| Per-band calibration | All 21 wavelengths independently probed: 182 probes / 5824 frames, maximum 184 DN, no saturation; no exposure interpolation |

Profile IDs are `reset21_20261018_broadband_01`, `reset21_20261018_pupil_fine_01` and `reset21_20261018_bands_21`. The combined per-band profile depends on that same new pupil. Their SHA-256 values, respectively, are:

```text
FC560DD69790601A118161F4AE3B8AA6283E3CF65ED38059B442A583B8172559
ADD4511C14C0339D2E2984AB088C251EEDB1404B0E6C294EF6E5C1227655BE3C
BE6DAD3EA8A486B16F1115BD94FE887E93EF9E4146826EC020A83EA0A48F3EDF
```

## Runs and data integrity

| Run | Conditions | Raw frames |
| --- | --- | --- |
| R21RefPre32 / R21RefRepeat32 | Separate 450/550 nm references, 32 frames per band per run | 128 |
| R21RefPre650 / R21RefRepeat650 | Separate 650 nm references, 256 frames per run | 512 |
| R21Scan32 | 450–640 nm in 10 nm steps; 32 frames per band | 640 |
| R21Scan650 | 650 nm; 256 frames | 256 |
| R21RefPost650 | Independent 650 nm post-reference | 256 |
| R21RefPost32 | Independent 550/450 nm post-references | 64 |
| R21DarkPre01 / R21DarkPost01 | Physically blocked; three exposure settings, 32 each per run; no TLS connection | 192 |

All eight illuminated RawCapture v3 files closed successfully with both `capture_complete` and `run_succeeded` true. All ten runs have successful diagnostics and empty cleanup-error lists. Distinct legal plans avoid repeated illumination identities inside a plan; different burst lengths use ordinary separate files, without changing the data schema.

Delivery verified source/receiver SHA-256 for **76 files / 1,382,432,827 bytes**, including copies of plans and Profiles accompanying runs. A final independent local rehash also passed. All **2048** formal measurement/dark frames matched their production frame hashes and embedded headers. Averaged frames matched recomputation exactly; both dark means and sample variances matched exactly. These totals exclude calibration bursts: their raw files remain on the acquisition host; not all calibration raw bursts received independent development-host rehashing. Delivered calibration reports/Profiles/plans were separately hash-checked.

Main RawCapture SHA-256 values:

```text
R21Scan32   CF70AA635791967AC6152A3E43DD71F3CFF0824A9D198125869D1DD764BEF8C9
R21Scan650  52765EDEA1BE9C32DD803E0ED4C138DE3A622E7F4A167FAC516CF6979E466C6A
```

All original files remain on the acquisition host and in verified development-host copies. Source data were not normalized, background-subtracted, overwritten or deleted. FullFramePSFSurvey v1 and dense ROI extraction used existing hardware-free APIs with matching real Profiles, full-sensor extent and original wavelength/display indices. Available memory was checked before explicitly allowing large Surveys. Every extracted ROI exactly matched its source averaged measurement.

## Per-band quality

Gain was 0 dB throughout; saturation fraction was zero in every valid optical pixel. Peak is the maximum valid optical raw pixel over the burst. Signal is the **signed 512-ROI sum after matching pre/post-dark and residual-border subtraction**, at the listed actual exposure; it is not calibrated optical throughput. CV describes individual-frame integrated-signal variation, not uncertainty of the averaged PSF. Exposure commands/readbacks were 201.6/201.8213272, 403.2/403.6426544 and 1612.8/1614.5706177 µs.

| nm | Actual µs | Frames | Peak DN | Signal DN-sum | Frame CV % |
| --- | --- | --- | --- | --- | --- |
| 450 | 403.643 | 32 | 121 | 27260 | 1.72 |
| 460 | 201.821 | 32 | 133 | 29594 | 2.75 |
| 470 | 201.821 | 32 | 171 | 44222 | 2.77 |
| 480 | 201.821 | 32 | 155 | 51396 | 2.77 |
| 490 | 201.821 | 32 | 146 | 61087 | 2.82 |
| 500 | 201.821 | 32 | 115 | 52951 | 2.75 |
| 510 | 201.821 | 32 | 106 | 54421 | 2.96 |
| 520 | 201.821 | 32 | 113 | 53791 | 3.15 |
| 530 | 201.821 | 32 | 118 | 52363 | 3.27 |
| 540 | 201.821 | 32 | 116 | 49428 | 1.94 |
| 550 | 403.643 | 32 | 177 | 81176 | 3.08 |
| 560 | 403.643 | 32 | 166 | 75886 | 3.00 |
| 570 | 403.643 | 32 | 147 | 64774 | 2.89 |
| 580 | 403.643 | 32 | 128 | 72914 | 2.45 |
| 590 | 403.643 | 32 | 182 | 84172 | 3.55 |
| 600 | 403.643 | 32 | 169 | 69531 | 2.88 |
| 610 | 403.643 | 32 | 160 | 57766 | 3.72 |
| 620 | 403.643 | 32 | 148 | 48022 | 4.55 |
| 630 | 403.643 | 32 | 133 | 41121 | 3.88 |
| 640 | 403.643 | 32 | 118 | 34050 | 3.61 |
| 650 | 1614.571 | 256 | 146 | 30902 | 14.49 |

All bands had clear positive point-source signal. The illuminated border background ranged approximately 25.0001–25.0194 DN; average signal peaks after background processing ranged 76.8–148.8 DN. No band was rejected for saturation or integrity failure. This does not establish equal confidence in all bands: 460–640 nm non-reference conditions have only one independent scan observation, 650 nm retains substantially greater amplitude fluctuation, and the nominal filter-edge bands have uncalibrated transmission and spectral purity.

## Darks, averaging and reference checks

Matched pre/post darks covered every actual exposure at the same gain. Valid full-frame means were approximately 24.9984–24.9985 DN; dark peaks were at most 36 DN. The change in each 512-ROI mean was less than 0.00005 DN. About 99.76% of ROI dark samples equal exactly 25 DN. This quantized black-level observation is not an intrinsic read-noise or radiometric calibration.

Analysis copies subtract the mean of matching pre/post-dark estimates, then a residual 40-pixel ROI-border mean. Shape comparisons apply explicit 2×2 averaging and signed unit-total normalization, without spatial alignment. Relative L2 is `||a-b|| / sqrt((||a||²+||b||²)/2)`. Shared finite dark estimates and overlapping cumulative frame subsets create correlated uncertainty; they do not provide independent confidence intervals.

| Reference nm | Frames per observation | Pre/repeat shape relative L2 | Pre/post shape relative L2 | Pre/post signal change | Local centroid difference |
| --- | --- | --- | --- | --- | --- |
| 450 | 32 | 0.01652 | 0.01707 | +0.347% | 0.122 px |
| 550 | 32 | 0.00888 | 0.01005 | +2.511% | 0.095 px |
| 650 | 256 | 0.00626 | 0.00925 | −1.386% | 0.059 px |

These actual independent references support same-session stability of the averaged shapes at the three reference wavelengths. Centroids use positive-clamped energy and remain noise/shape-sensitive; neither centroid nor a changing brightest Bayer pixel is pure mechanical drift. References do not establish independent stability for every intervening wavelength.

Wavelength-specific averaging is appropriate here: the aim is the mean system PSF, not a fitted model of xenon fluctuations. At 650 nm, the main burst's first/second-half mean shape relative L2 was 0.01048, but signal changed −4.46%. Its 32-frame block-mean signal CV was 3.25%. Thus 256-frame averaging produced a repeatable observed mean shape in this session while retaining a real amplitude-stability limitation; it did not eliminate slow drift or calibrate source power. No fluctuation model was fitted.

## Coordinates, ROI extent and delivered overview

The fixed core sensor ROI is half-open `(1122,810,512,512)` with NumPy `[H,W]` arrays. A larger common `(866,554,1024,1024)` ROI was also extracted for all 21 bands with exact amplitude preservation. ROI position/size were not adapted per wavelength. Full-frame Surveys remain available.

Logarithmic displays reveal weak diffraction structure outside the 512 core and, in places, near/beyond the larger window. Comparing signed sums under the same large-ROI background gives 512/1024 ratios approximately 0.930–1.025; ratios above one expose background sensitivity, not physical energy gain. Neither ROI is certified to contain the full PSF energy. The global positive-clamped sensor energy center is also background-biased and was not silently used as a new crop center.

Retained outputs under the experiment ID include:

- `received/<run>/`: RawCapture, actual plan, Profiles, diagnostic, sender hashes and verified transfer receipt; each illuminated run also has `analysis/survey.h5`, sensor-center metadata, exact-amplitude ROI NPZs and frame/mean verification.
- `final_quality_v3/`: raw-amplitude and normalized-shape atlases, response/quality figure, `measurements.csv`, `measurements.json` and `summary.json`.
- `roi_extent_review/`: all 21 exact-amplitude 1024 ROI NPZs, signed extent diagnostics and logarithmic reference views.
- `completion_overview/`: 21-band larger-ROI log atlas, independent-reference figure and `delivery_audit.json` containing file sizes and SHA-256 values.

All final figures were rendered and visually inspected. Raw-amplitude plots, matched-dark normalized-shape plots and a separately labelled display-only constant-black-level log atlas are distinct. Generated figures, private configuration and measurement data remain outside Git. An initial private JSON export failed on a NumPy boolean; its partial analysis output was retained, the boolean converted explicitly, and analysis rerun to a new directory. No acquisition or measurement was repeated to conceal a failure.

## Acceptance and stopping point

The deliverable is a complete, traceable 21-band measured PSF dataset with safe exposures, real Profiles, burst retention, verified delivery and basic quality evidence. Averaged reference shapes are repeatable in this corrected geometry. Amplitude at 650 nm remains source-sensitive; intermediate bands lack independent temporal repeats. This session does not establish 21 separable spectral channels, a 10 nm resolving capability, an isolated LCD contribution or material/task information gain. Lens/CFA/sensor response, source spectrum, filter transmission, unknown bandwidth and slit remain confounders.

All measurement jobs ended successfully and released their owned connections. The final read-only check found the desktop task idle, no camera listener and a clean unchanged instrument checkout. Incident light remains physically blocked after the operator's post-dark action; the lamp was not powered off. No new experiment was started after collection. Further low-dimensional features, material discrimination or display-control comparisons await research review and separate scope.

No production source or SDK change was needed for this round. Validation consisted of the actual hardware observations, file/frame hashes, exact means/variances, Profile/geometry/index checks and SDK-free real-data Survey/ROI processing above; the full software suite was not rerun for these documentation/private-analysis changes. This report is a local member-repository change, not a new hardware deployment or an automatic Meta Repository pin update.
