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
2. **The P2 state is not a clean rotation of P1.** Peak signal per microsecond
   fell by roughly 5x (550 nm) and 7x (650 nm) at P2, and the four-fold content
   of the azimuthal pattern collapsed there: the fraction of ring variance in the
   cos/sin(4 theta) harmonic is 0.19-0.30 at P1 and P3 in 550 and 650 nm, but
   0.002-0.18 at P2. Its centre also repeats worst (0.23 px at 550 nm, 1.00 px at
   450 nm). Cross-correlating P1 against P2 is therefore not guaranteed to be
   comparing one PSF at two angles; it may be comparing two different PSFs.
   Both rotation pairs involve P2, so this affects every slope in the table.

Point 2 is why the 550 nm pair ratios differ by a factor of two and are not
averaged into a single number. It is a measurement limitation, not a verdict.

## What this does and does not establish

Established:

* The estimator detects mount rotation at 550 and 650 nm with a slope magnitude
  of 0.7-0.8 and a null of -3.1 deg, so the angular structure is not frozen to
  the display at those wavelengths.
* At 450 nm no rotation response is detectable at the same rotation amplitude and
  the same estimator, in either direction.
* The pre-registered pooled test is unresolved; that is the session's formal
  outcome.

Not established:

* Whether the 450/550-650 split is optical (a wavelength-dependent balance
  between a display-fixed grid and a mount-fixed aperture) or an artefact of the
  P2 state. No measurement here separates those.
* Any universality. One holder, one pinhole geometry, one display state.
* That the pattern is "the PSF" in isolation. It is the azimuthal structure of
  the measured spot, whatever mixture of pinhole, LCD grid and lens produced it.

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
  run logs).
* Deletion manifest: `D:\datasets\axon20\phone_view_runs_deleted_20261127.json`.
* Code: `scripts/udc_hdr_psf/analyse_b2.py`, `rotation_analysis.py`,
  `drive_b2.py`, `drive_b1.py`, `align_live.py`.
