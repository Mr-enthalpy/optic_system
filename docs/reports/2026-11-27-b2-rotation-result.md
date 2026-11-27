# 2026-11-27 B2 rotation session: result

Companion to `2026-11-27-b2-rotation-method.md`, which holds the question, the
design and the offline validation. This record holds what the hardware actually
produced, the one criterion that was changed after the fact and why, and the
limits that keep the answer open.

## What ran

One session directory, `udc_rotation_20261127_trial450`. The name is historical:
the session was opened for 450 nm and extended to 550 and 650 nm, so the
directory name understates its contents. Never rename acquired data.

Plan B was used: capture every band at one mount angle, then rotate. Three
rotations, nine brackets:

| state | measured roll | bands captured |
| --- | --- | --- |
| P1 | -0.92 deg (450) / -0.69 deg (550, 650) | 450, 550, 650 |
| P2 | +17.25 deg | 450, 550, 650 |
| P3 | +0.81 deg | 450, 550, 650 |

P1 and P3 are the same nominal angle, so P1-to-P3 is a null pair: it measures
what the estimator reports when nothing rotated. Measured `delta_roll` between
adjacent states is 17.95-18.17 deg, inside the pre-registered 15-25 deg window
and well below the 45 deg four-fold wrap.

No darks were taken (`--no-dark`); the black level comes from each bracket's own
recorded characteristics, 64 DN in every case.

### Brackets that were re-shot, and which file the analysis reads

Three states needed a second attempt, all for exposure rather than angle:

* `P2_550nm_r1` - first P2 550 nm bracket, kept.
* `P2_650nm_r1` - re-shot at 4300 us after the first attempt under-exposed.
* `P3_550nm_r3` (310 us) and `P3_650nm_r3` (860 us) - re-shot after finding that
  the longer tiers of every bright-angle bracket reached the 1023 DN ceiling.

`brackets.jsonl` still names the original captures for P3, but
`find_bracket_record` globs `<label>*.json` and takes the newest file, so the
analysis reads `P3_550nm_r3.json` and `P3_650nm_r3.json`. Verified explicitly by
printing the resolved file per plan entry. This is convenient here and fragile
as a general rule: a future label prefix collision would silently change which
data is analysed.

Attempts that left empty or partial directories (`P3_550nm_r1`,
`P3_550nm_r2`, `P3_650nm_r1`, a partial `captures/P2_550nm`) are left in place.

## Completeness

All nine nominal brackets exist, are marked complete, and were analysed. Of the
36 band-state-plane comparisons, 34 are usable. The two refusals are both the R
plane of `P1_650nm`, whose r = 1.5 px ring reaches the ceiling; that is the
pre-registered refusal, and it is reported rather than worked around.

The 550 and 650 nm P3 brackets (the re-shoots) have peaks of 870 and 809 DN with
no core clipping and 8-10 clean rings. Every P1 and P3 450 nm bracket still
reaches 1023 DN in the core - the PSF is a point and two pixels sit on the
ceiling - while the rings that the azimuthal analysis actually reads stay below
877 DN. Core saturation is recorded per bracket as a fact, not used to discard
the measurement.

## The one criterion that changed, and when

The pre-registered centre rule refused a bracket when the converged centroid sat
more than 2 px from its quadratic anchor (`drift_from_anchor_px`). Applied to
this data set it left 7 of 36 pairs usable and returned `unresolved`.

Each bracket repeats its shortest exposure 6 times with nothing changed in
between. Re-centring those repeats independently, per bracket:

| bracket | centroid spread (peak-to-peak) | centroid sd | reported drift over the same 6 repeats |
| --- | --- | --- | --- |
| `P3_550nm_r3` | 0.09 px | 0.062, 0.025 px | 1.74 - 2.30 px |
| `P2_550nm_r1` | 0.67 px | 0.232, 0.113 px | 0.34 - 1.14 px |

A bracket whose centroid reproduces to 0.06 px was refused, while one whose
centroid walks 0.67 px was accepted. The drift field measures the distance from
the full-disc centroid to the brightest integer pixel, and on this PSF the
brightest pixel hops between near-equal lobes: the anchor jumps while the
centroid does not. It is not a stability test, and its ordering here is
inverted.

The rule was therefore changed to: accept when the centroid iteration converges,
and report the repeat-spread of the analysed centroid next to every bracket.
Nothing else moved - ring validity, clipping refusal, correlation threshold,
wrap limit, slope fit and null tolerance are untouched.

This change was made after the 650 nm shifts had been seen, at the user's
explicit direction, and it must be read that way. It did not produce a
favourable verdict: the pooled class stayed `unresolved`, and two planes still
have no class at all.

## Result

Shift is the rotation applied to the second profile to match the first. A
structure fixed to the display predicts 0; a structure fixed to the rotating
mount predicts minus `delta_roll`.

### As pre-registered: cross-correlation of the whole profile

| band | pair | `delta_roll` | Gr | Gb | B | R |
| --- | --- | --- | --- | --- | --- | --- |
| 450 | P1-P2 | +18.17 | -0.43 | -0.97 | -2.59 | -7.99 |
| 450 | P2-P3 | -16.44 | -2.56 | -2.44 | +2.14 | +8.08 |
| 450 | P1-P3 | +1.72 | -3.36 | -2.77 | -1.72 | +0.33 |
| 550 | P1-P2 | +17.95 | -11.31 | -17.43 | -14.84 | -22.91 |
| 550 | P2-P3 | -16.46 | +6.11 | +10.74 | +8.16 | +5.33 |
| 550 | P1-P3 | +1.50 | -2.60 | -3.78 | -3.33 | -3.05 |
| 650 | P1-P2 | +17.95 | -8.07 | -20.08 | -14.08 | refused |
| 650 | P2-P3 | -16.46 | +5.94 | +20.03 | +8.24 | +13.07 |
| 650 | P1-P3 | +1.50 | -2.39 | -3.58 | -3.85 | refused |

Fitted per band (`angular_analysis.json`, `summary.by_band`):

| band | slope | rms | n | class |
| --- | --- | --- | --- | --- |
| 450 | -0.131 | 3.35 deg | 12 | `display_consistent` |
| 550 | -0.719 | 4.43 deg | 12 | `mount_consistent` |
| 650 | -0.754 | 4.54 deg | 10 | `mount_consistent` |

Fitted per plane: Gr -0.293 `display_consistent`, Gb -0.654 `unresolved`,
B -0.493 `unresolved`, R -0.678 `unresolved`. Pooled: slope -0.522, rms 5.81 deg,
n = 34, class `unresolved`, reason "planes disagree on the hypothesis". The
same-angle null is -3.05 deg pooled, inside the 5 deg tolerance.

The response is symmetric in sign at every band: rotating one way moves the
pattern one way, rotating back moves it back. What differs between bands is the
magnitude.

* **450 nm:** the pattern does not follow the mount. Across a 18 deg rotation the
  shift stays within a few degrees of zero (-0.43 to -7.99), and the reverse
  rotation brings it back (+2.14 to +8.08). Both intervals contain the null
  value. This is a consistent negative, not a small positive.
* **550 nm:** the pattern partly follows the mount but not by a single factor:
  the P1-P2 rotation gives 0.93 of full mount-following, the P2-P3 rotation 0.46.
* **650 nm:** the pattern follows the mount more consistently, 0.79 of full on
  P1-P2 and 0.72 on P2-P3.

## Why the verdict stays unresolved

Two independent reasons, either of which is sufficient:

1. **The planes disagree.** Pooled across bands, Gr fits a display-fixed slope
   while Gb, B and R fit neither hypothesis. Quad-CFA planes see the same PSF
   through different sub-lattices, so a split this large is not a plane-level
   curiosity to be averaged away.

2. **The P2 state's azimuthal pattern is not the P1 pattern rotated.** Three
   things were measured at P2 and none of them is a rotation alone:

   * Peak signal per microsecond fell by roughly 5x (550 nm) and 7x (650 nm).
   * The four-fold content of the azimuthal profile dropped by a factor of 2-4
     at every plane and band: the fraction of ring variance in the
     `cos/sin(4 theta)` harmonic is 0.19-0.30 at P1 and P3 in 550 and 650 nm but
     0.04-0.18 at P2, and 0.09-0.22 at 450 nm on all three states.
   * The radial profile barely moved. Normalised to its own r = 1.5 px ring, the
     P2 profile differs from the P1/P3 mean by at most 0.13, and the P1-to-P3
     difference at the same nominal angle reaches 0.14 - i.e. the shape change at
     P2 is no larger than the change between two captures of one angle.

   The centre is not the explanation either: `P2_650nm_r1` repeats its centre to
   0.03 px, better than any other bracket, yet its on-band four-fold content is
   the lowest measured. So the change at P2 is in the angular structure itself,
   not in focus, centring or brightness alone. Whatever causes it - the sampling
   lattice at 17 degrees to the pattern, a stop that is not rotationally
   symmetric, or something else - is not established here, and both rotation
   pairs pass through P2, so every slope in the table mixes rotation with it.

Point 2 is why the 550 nm pair ratios differ by a factor of two and are not
averaged into a single number. It is a measurement limitation, not a verdict.

## After the fact: rotation from the phases of the azimuthal orders

This section was written after the registered analysis above, and it does not
replace it. It exists because one premise of the registered estimator turned out
to be false.

`angular_shift` takes the largest circular cross-correlation inside a +/-45 deg
window, and that window was justified by the spot being four-fold. The measured
azimuthal spectra do not support it: over r = 3-10 px the second order is the
largest in 31 of 35 usable bracket-plane combinations, with the third and fourth
orders close behind (0.3-1.1 of k=2), and a peak count on the r = 4-20 px profile
gives 4 to 13 directions depending on band and plane. A pattern with N periodic
spike directions has a correlation peak every 360/N degrees, so the global
maximum can land on a neighbouring spike, and the 45 deg window is not the
bound that was claimed.

The substitute estimator assumes nothing about N. A rigid rotation by D
multiplies the phase of order k by kD, so D is fitted jointly over orders 2-10,
each weighted by its own amplitude, with the four CFA planes' coefficients added
in the complex plane (`rotation_analysis.harmonic_coefficients`,
`fit_shift_from_harmonics`; driver `analyse_b2_phase.py`). A single order could
still be ambiguous, but the orders are incommensurate enough that the joint fit
is defined over the searched +/-90 deg. `phase rms` is the scatter of the orders
about the fitted rotation: it is the estimator's own statement about whether the
change was a rotation at all.

| band | pair | `delta_roll` | shift | phase rms | per-plane shift (Gr Gb B R) |
| --- | --- | --- | --- | --- | --- |
| 450 | P1-P2 | +18.17 | -1.82 | 23.95 deg | -2.2 +0.0 -2.9 -8.7 |
| 450 | P2-P3 | -16.44 | +0.24 | 25.83 deg | -2.8 -3.7 +2.4 +12.0 |
| 450 | P1-P3 | +1.72 | -2.54 | 7.78 deg | -4.9 -3.6 -1.4 +0.1 |
| 550 | P1-P2 | +17.95 | -16.78 | 16.68 deg | -13.1 -17.9 -22.4 -20.0 |
| 550 | P2-P3 | -16.46 | +14.04 | 22.67 deg | +10.1 +16.3 +13.8 +13.2 |
| 550 | P1-P3 | +1.50 | -1.74 | 14.24 deg | +0.2 -5.0 +2.2 -4.9 |
| 650 | P1-P2 | +17.95 | -18.86 | 15.96 deg | -27.0 -20.0 -17.8 - |
| 650 | P2-P3 | -16.46 | +15.72 | 12.87 deg | +26.6 +7.5 +15.7 +7.8 |
| 650 | P1-P3 | +1.50 | -2.28 | 10.00 deg | -3.3 -1.2 -2.1 - |

| band | slope | intercept | rms | n | class |
| --- | --- | --- | --- | --- | --- |
| 450 | -0.061 | -1.30 deg | 0.80 deg | 3 | `display_consistent` |
| 550 | -0.895 | -0.60 deg | 0.14 deg | 3 | `mount_consistent` |
| 650 | -1.005 | -0.80 deg | 0.02 deg | 3 | `mount_consistent` |

Read as `shift = a + b * (-delta_roll)`, the three bands share a small offset
`a` of -0.6 to -1.3 deg and differ only in `b`: 0.06 at 450 nm, 0.90 at 550 nm,
1.00 at 650 nm.

What this changes:

* **The 450 nm null is not an artefact of the four-fold assumption.** It was the
  main alternative explanation for a band that does not move while the others do,
  and an estimator that makes no such assumption returns 0.06 of mount following.
* **The two mount-following bands were underestimated.** 550 nm goes from 0.72
  and 0.93 to 0.90, 650 nm from 0.72 and 0.79 to 1.00 and 0.96. That is the
  signature of a global maximum sitting on a neighbouring spike, which is what
  the wrong N predicts.
* **The plane spread is in the data, not only in the estimator.** Per-plane
  shifts still span 6-9 deg at every band in both directions.

What this does not change:

* The registered verdict. This estimator was chosen after the spectra were seen,
  so it is a robustness check on a stated premise, not a second chance at the
  criterion. The session's formal outcome stays `unresolved`.
* The non-rigidity. `phase rms` is 8-26 deg at every band, so no pair is a pure
  rotation: the pattern rotates and changes shape at the same time.

## What this does and does not establish

Established:

* The estimator detects mount rotation at 550 and 650 nm with a slope magnitude
  of 0.7-0.8 and a null of -3.1 deg, so the angular structure is not frozen to
  the display at those wavelengths.
* At 450 nm no rotation response is detectable at the same rotation amplitude and
  the same estimator, in either direction.
* The pre-registered pooled test is unresolved; that is the session's formal
  outcome.
* That the 450 nm null does not depend on the four-fold premise: an estimator
  that fits the phases of orders 2-10 with no assumption about N returns 0.06 of
  mount following at 450 nm, 0.90 at 550 nm and 1.00 at 650 nm.

Not established:

* Whether the 450/550-650 split is optical (a wavelength-dependent balance
  between a display-fixed grid and a mount-fixed aperture) or an artefact of the
  P2 state. No measurement here separates those.
* That 450 nm has no four-fold diffraction component at all. Its pattern is
  weakly four-fold on all three states (harmonic fraction 0.09-0.22 against
  0.19-0.30 at 550 and 650 nm), so the statement is about the azimuthal structure
  this estimator measured, not about the whole PSF.
* Any universality. One holder, one pinhole geometry, one display state.
* That the pattern is "the PSF" in isolation. It is the azimuthal structure of
  the measured spot, whatever mixture of pinhole, LCD grid and lens produced it.
* That a single N describes the spot. k=2 dominates the spectrum, k=3 and k=4 are
  comparable, and the joint phase fit is only as rigid as those orders agree -
  which they do not, at 8-26 deg rms.

## Incident: the phone ran out of storage

Two bracket attempts failed with `TimeoutError: No terminal run state observed`.
The cause was not the instrument: `/data` was at 105 GB of 105 GB with 16 MB
free. The live view had written one 32 MB RAW frame every ~4.5 s since the
morning and never deleted any; 2119 such runs, self-labelled
`live_alignment_control_frame` / `not_session_data: true`, held ~68 GB. One
frame was truncated at 8 MB and its run stayed `running`, which then blocked
every subsequent `am start`.

With the user's explicit authorisation, 2116 of those runs were deleted - only
directories whose own `run.json` carries that label, each path re-checked
against `run_<epoch_ms>_<hash>` - after writing the full ID list to
`phone_view_runs_deleted_20261127.json`. The three newest were kept. Bracket,
dark and sweep runs were not touched; a bracket run was spot-checked afterwards.
`/data` went from 16 MB free to 51 GB free, `files/captures` from 2658 to 542
runs.

The root cause was fixed in `align_live.py`: a pulled frame is now deleted from
the phone once it is decoded in memory (`drop_view_run`, `--keep-phone-frames`
to opt out), with the outcome recorded in the page state.

A separate failure, unrelated to storage, was one `ADB export timed out` during
the first 650 nm P3 re-shoot with 50 GB free; the retry succeeded.

## Files

* Session: `D:\datasets\axon20\udc_rotation_20261127_trial450\`
  (`brackets.jsonl`, per-bracket `*.json`, `captures/`, `angular_analysis.json`,
  `harmonic_rotation.json`, run logs).
* Deletion manifest: `D:\datasets\axon20\phone_view_runs_deleted_20261127.json`.
* Code: `scripts/udc_hdr_psf/analyse_b2.py`, `analyse_b2_phase.py`,
  `rotation_analysis.py`, `drive_b2.py`, `drive_b1.py`, `align_live.py`.
* Figures in the session's `figures/`: `azimuthal_structure.png` and `spikes.png`.
  The first is the registered polar unroll at every ring; the second flat-fields,
  smooths and counts the peak directions, and is what showed that N is not 4.
