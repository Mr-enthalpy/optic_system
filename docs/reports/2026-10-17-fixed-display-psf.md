# First fixed-display multi-wavelength PSF measurement

Date: 2026-10-17, Asia/Shanghai. The UDC-like laboratory apparatus completed a real calibration, capture, verified delivery and offline-analysis cycle. This report records initial measurement acceptance, not commercial-phone UDC validation or material/task sensing success. Raw measurements, Profiles, private configuration, run plans and generated figures remain in the retained experiment record outside Git; this is the curated summary.

## Source and environment

Actual acquisition used commit `18dd598852b1d44ea80b4f6d53e1d2d5656f9e9b`, with finite, experiment-specific observation/stop/desktop helpers outside the source checkout. Those helpers saved raw calibration bursts and per-frame camera diagnostics and enforced the experiment's stop conditions. The instrument checkout was clean and independent of the Meta Repository. Do not infer that a subsequent integrated release was itself hardware-tested.

The acquisition PC used Windows, Python 3.13.2 x64, flycapture2_c 0.6.0, tls_c1_spectrometer 0.1.0rc0 and pygame 2.6.1. The development PC used Python 3.13.5, NumPy 2.5.4 and h5py 3.16.0. Offline processing did not import camera/TLS SDKs. Agent-backed SSH verified the known host key; SCP transferred closed files with sender/receiver SHA-256 checks. Hardware timing stayed local in the confirmed interactive desktop session, coordinated through an authorized demand-only task.

The maintained source now combines that acquisition line with Phase A's separate dual-environment commit `7d7a23cb0690ccbe172b4eca8747689caa5155cc`. The combined source passed **374 tests, with 3 hardware opt-in skips**, using its independent `.venv/Scripts/python.exe -m pytest -q` on Python 3.13.5. Before integration, the acquisition version passed 366 tests/3 skips on both hosts; the private finite-capture guard passed two hardware-free cases. These software checks are distinct from the real observations below. A restricted-sandbox attempt failed on temporary-directory permissions; the full combined suite passed with ordinary local filesystem permissions. No ACLs were loosened to hide test failures.

The combined source's four public offline CLIs also inspected the verified real PSFSpectral02 capture, built a new full-resolution Survey, estimated its center with the explicit header-pixel mask and extracted the fixed ROI. The result retained the original first-condition 650 nm index and exactly matched the original measured ROI amplitude. This is real-data software compatibility verification on the development host, not a new hardware deployment.

## Apparatus and measured calibration

The apparatus used a xenon source, fixed point target, mono LCD, FlyCapture2 camera and Zolix Omni-lambda monochromator. The operator confirmed optical safety and a 450–650 nm range. Broadband attenuation was a mechanical output slit with no reliable numeric scale; its setting and geometry were held fixed for the accepted calibration/capture group. Source power and actual monochromator bandwidth were not independently calibrated.

Camera transport was RAW8 BayerGB, uint8, full-scale 255 DN, full sensor `[2048,2448]`, Format7 mode 0 with sensor origin `(0,0)`. RAW8 does not establish the native ADC bit depth. Gamma was 1, gain 0 dB, frame-rate setting 20 fps, trigger disabled. Only the eight verified embedded timestamp/counter pixels at `(x=0..7,y=0)` were excluded from optical statistics; their raw bytes were preserved. No optical saturation pixel was declared defective to make a test pass.

The confirmed LCD mapping was reported RGB `[2560,540,3]`, logical `[2560,540]`, physical mono `[2560,1620]`, subpixel axis 1. Display identity was manually verified in the actual desktop. Remote privacy/black-screen mode had previously overridden the physical output and was disabled. These are historical measured dimensions, not defaults identifying a future panel.

| Stage | Measured result | Acceptance and limits |
| --- | --- | --- |
| Broadband04 | Three 32-frame preflight/search observations, actual 6.2584877 us, 0 dB; raw peaks 206/192/198 DN | No saturation at the accepted slit setting; profile and full probe reports retained |
| Pupil02 | 122 measurements, 3904 raw frames; maximum 225 DN; LCD center `(1060.0572,1876.1523)` | X/Y bar and radius curves inspected; selected circular radius 54.8339, window `(1005,1821,110,110)` inside the physical panel |
| Pupil fit | Ellipse axes 118.6824/60.9266; R-squared 0.999106; RMSE 4838.74 DN-sum against an approximately 426k DN-sum plateau | Effective illuminated pupil estimate, not independent mechanical ground truth |
| Bands02 | Six 32-frame probes over 550/450/650 nm; upper-probe maxima 125/121/127 DN | Same PupilProfile, fixed pupil-open physical mask, no saturation, requested/readback settings retained |

| Wavelength nm | Exposure command us | Actual exposure us | Gain dB |
| --- | --- | --- | --- |
| 450 | 290 | 290.0958061218262 | 0 |
| 550 | 190 | 189.18514251708984 | 0 |
| 650 | 940 | 939.7268295288086 | 0 |

The Profile IDs are `phase_c_20261017_broadband_04`, `phase_c_20261017_pupil_02` and `phase_c_20261017_bands_02`; the last depends on the same pupil. Their SHA-256 values, in that order, are:

```text
06F39106E489756BD90A1E45FBC332BF8E856FB6A83DC80E0E99EC0817172E78
A5618D6D3246DB4DCCD429040E985A9E86D2AA37F13384C7BA3D8EB5B85C99B8
3125EA46B336B27B5FC82CEE3661E1BA5E0018136D6B55F6EB2CF47E9DE4CFD7
```

Broadband used explicit pass-through, not wavelength zero. Monochromatic acquisition used grating 1 and a 3-second TLS wait. SDK wavelength readbacks were approximately 450.0/550.0009766/649.9990234 nm; these are device status, not an independent accuracy/bandwidth measurement. LCD settled for 2 seconds; camera parameters settled for 300 ms plus 80 discarded frames before each 32-frame observation. The Profile/report records carry the complete settings and ranges.

Earlier Pupil01 stopped on real optical saturation and produced no accepted PupilProfile. Its raw burst and diagnostic were retained. Broadband03 failed before probing due to an experiment-wrapper LCD API mistake; a separate new run corrected the call. No failed output was overwritten or made successful by changing flags.

## Formal capture and delivery

Every illuminated condition retained 32 full-frame raw samples and its observed mean. All six RawCapture v3 files have complete schedules and both `capture_complete=true` and `run_succeeded=true`, with no cleanup error. The physical mask stored in each HDF5 was checked against the same fixed pupil mask, whose SHA-256 is `D7775CA34AD3757D7306E280CC5232504E11311B471682981D21185A89067F96`.

| Run | Start local time | Wavelength order nm | Raw frames | HDF5 bytes |
| --- | --- | --- | --- | --- |
| PSFRef01 | 20:15:04 | 550 | 32 | 3,734,654 |
| PSFRef02 | 20:19:12 | 550 | 32 | 3,746,854 |
| PSFSpectral01 | 20:21:04 | 550,450,650 | 96 | 10,345,826 |
| PSFSpectral02 | 20:29:19 | 650,450,550 | 96 | 10,333,214 |
| PSFSpectral03 | 20:34:34 | 550,450,650 | 96 | 10,343,728 |
| PSFRef03 | 20:38:25 | 550 | 32 | 3,744,601 |

RawCapture SHA-256 values:

```text
PSFRef01       A258E07F4BC57A1FF1F821DA76846F6E6D4CB9CF35B5B4D33345C32EFA90B1DA
PSFRef02       454D90D68A00D7D53E87F831A27614B70D31E48BC1D616C07867B3BD5A4633F3
PSFSpectral01  5FB406C127F8FDDDBC3FFAD2DC15A405C1DB1D88A65C5E4E964C76FF7E5B9A12
PSFSpectral02  1D56EC31C1CAF0037C5EFB56F0EAAFDFA1025CC7AAB46CD4DAAC75A2799E89E2
PSFSpectral03  989EFAB67AF0DEDF35948B4147F167E907D0F3722905938483D3BAB7C7DA4949
PSFRef03       37CCFB0ACE96A38578948EEBAD095158DC748112BFEDD0A694091FBF9FF32C1D
```

Each delivery included raw HDF5, plan, diagnostic and three Profiles: 36 source files across six runs. All sender/receiver file sizes and hashes matched. All 384 illuminated frames matched their producer SHA-256 and embedded timestamps/counters; stored float32 means matched float64 raw means converted to float32. Per-frame producer metadata was retained in the companion diagnostic, not all in HDF5: the current writer stores only selected fields. Frame-counter gaps from latest-frame/stale-notification handling were retained and inspected, not described as equally spaced or consecutive sampling. In Ref02 the producer reported zero dropped frames while the client stale-notification increase matched the gaps.

The writer's reserved `scientific_calibration_valid` and `optical_alignment_validated` flags remained false. Experimental acceptance is this separate interpretation of recorded evidence, not a retroactive flag edit. Pupil-scan raw bursts remain on the acquisition host; 248 report/plan/sample-summary/mask files were delivered, but not every pupil raw frame was copied and independently rehashed.

At 21:32, after operator confirmation of incident-light blocking, DarkBands01 collected 32 frames at each of the three actual exposure/gain settings, without connecting to or moving TLS. The closed diagnostic burst/mean/sample-variance delivery comprised 11 files, 601,769,187 bytes. All file hashes, 96 producer frame hashes/headers, means and sample variances verified exactly. Valid-domain dark means were approximately 24.99844 DN, peaks 36–37 DN, no saturation. These are explicitly blocked-light diagnostic arrays, not falsely labelled monochromatic surveys.

Only one late dark burst per setting was measured. About 99.71% of ROI raw dark samples equalled 25 DN; their small RAW8 variance must not be interpreted as calibrated intrinsic read noise. The matching camera settings do not establish absence of long-term thermal/black-level drift. All devices owned by these finite tasks were released, with no cleanup errors. Xenon power was left unchanged; the dark task did not restore illumination automatically.

## Offline response and repeatability

All six real files passed the existing hardware-free RawCapture v3 -> FullFramePSFSurvey v1 -> sensor-center / dense-ROI path. Sufficient host memory was checked before explicitly allowing a full-resolution survey. Survey values and extracted ROIs remained identical to the measured averages. The common sensor ROI was half-open `(x=992,y=800,width=512,height=512)`; NumPy arrays use `[H,W]`. Full frames were retained.

The default positive-clamped full-frame center was background-biased, around `(1231,1031–1033)`. It was reported separately rather than used as the true spot center or ROI-size policy. A local ROI centroid was used for temporal drift diagnostics. Original-amplitude figures were kept separate from normalized-shape figures. All processing below operated on copies.

For shape comparison, subtract the matching 32-frame dark mean, then each light frame's residual quiet 40-pixel-border mean; average 32 frames, explicitly average 2x2 Bayer cells and normalize the signed response by total energy. Use identical sensor coordinates without spatial realignment. Two-by-two averaging reduces Bayer periodicity but does not calibrate CFA/QE or lens effects.

| Comparison | Pair count | Relative L2 minimum | Median | Maximum |
| --- | --- | --- | --- | --- |
| Within run, different 8-frame blocks | 54 | 0.01880 | 0.03306 | 0.08025 |
| Same wavelength, independent 32-frame runs | 9 | 0.00975 | 0.01668 | 0.02162 |
| Cross wavelength, 32-frame run means | 27 | 0.47386 | 0.54370 | 0.74849 |

Relative L2 is `norm(a-b) / sqrt((sum(a*a)+sum(b*b))/2)`. The smallest cross-wavelength distance was about 21.9 times the largest same-wavelength independent-run distance. This is descriptive separation in this measurement set, not an independent-pair hypothesis test, p-value, decoder performance or task-information result. Pairs share observations and dark estimates; 8-frame blocks also have different averaging noise from 32-frame means.

Matching-dark correction changed each condition's normalized shape by relative L2 only 0.0030–0.0063 compared with the explicit border-only analysis. Same/cross-wavelength separation was also present for 256/512/1024-pixel ROIs. Signed encircled response can be nonmonotone or exceed unity when faint tails and background residuals dominate; no calibrated energy-coverage percentage is claimed.

Frame-level integrated-energy CV after explicit background/dark processing was approximately 1.78–1.87% at 450 nm, 2.16–3.16% at 550 nm and 7.91–12.55% at 650 nm. The earlier matching 650 nm calibration burst had about 10.3% CV, so the larger fluctuation was not unique to the formal scan. Averaging helps, but does not establish source stability or remove systematic drift. The cause was not independently isolated as xenon power alone.

The final 550 nm reference energy was approximately 1.68% above the first after matched-dark/residual-border processing; local centroid drift was approximately 0.058 sensor pixels. Same-wavelength centroid drift over the three multi-band runs was at most approximately 0.16 pixels. Wavelength-dependent center differences must not be treated as pure temporal drift.

Full-frame/ROI amplitude images, normalized-shape/marginal/encircled-response plots, OTF magnitude/phase and time-ordered repeatability plots were rendered and inspected in the retained offline record. OTF phase was displayed only where both compared magnitudes exceeded 0.05 DC, a display rule rather than an acceptance threshold. Analysis preserved negative residuals except when clipping the displayed intensity image. A local peak-signal/empirical-mean-SD ratio of roughly 80–244 includes source variation and is not calibrated photon SNR.

## Acceptance and next decision

The first hardware/optical measurement gate passed under the recorded fixed conditions: verified sensor extent, accepted pupil geometry, conservative exposure, zero formal-capture saturation (maximum 129 DN), repeatable spatial observations and reliable device release. The data gate passed with real Profiles/plans, successful raw files, retained failures, verified delivery and independent Survey/ROI processing.

The preliminary scientific evidence is a repeatable wavelength-dependent response of the **whole configured optical system** at 450/550/650 nm, larger than the observed short-run normalized-shape variation. Lens chromatic response, Bayer/sensor sensitivity, source output/bandwidth, slit, alignment and time dependence remain confounders. The independent display-induced diffraction contribution, general spectral observability and practical material/task information gain beyond RGB remain open.

Next measurements should add source/bandwidth controls, longer/interleaved reference and dark repeats, and an operator-approved comparable reference optical configuration. Only then should the measured diversity and noise determine useful frequencies/wavelength sampling for observability analysis. No optical disassembly, classifier, dynamic mask optimization or full spectral-cube reconstruction follows from this initial result.

The current software integration is for independent development and version-pinned restoration; the experiment host remains at its recorded acquisition SHA until a separately verified deployment. No raw data, private launcher, host address, user path, SDK binary or machine-specific configuration is included in this report or release.
