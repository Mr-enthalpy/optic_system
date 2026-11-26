# 2026-11-27 B2 rotation session: method and pre-capture validation

Status: **method implemented, offline checks passed, no B2 hardware capture has
been performed yet.** Everything below is derived either from synthetic images
or from the archived B1 five-band capture of 2026-11-27. Nothing here is a
measurement of how the real Axon 20 UDC PSF behaves under rotation.

## Question

B1 measured radial shape and plane ratios under a fixed display state. It cannot
say whether the azimuthal structure of the PSF belongs to the display or to the
object/mount side. B2 rotates the phone, with the pinhole fixed in the
laboratory frame, and asks:

* does a prominent azimuthal feature keep its **sensor** azimuth when the phone is
  rolled (structure fixed to the sensor/display), or
* does it keep its **laboratory** azimuth (structure fixed to the pinhole and
  everything on the object side of the pinhole)?

The two hypotheses predict different slopes of measured angular shift against
measured phone roll: 0 and a magnitude of 1. A four-fold PSF means the sign of
the slope alone does not identify the hypothesis; the magnitude does.

## Design

| Item | Value |
| --- | --- |
| Mount states | P1 -> P2 -> P3, per band |
| Bands | 450, 550, 650 nm (B1 start exposures: 3224 / 784 / 2151 us) |
| Brackets | 9 (3 states x 3 bands), B1 ladder and drift references unchanged |
| Roll measurement | phone accelerometer, `read_tilt.py`, continuously during each bracket |
| Angle used in analysis | measured `roll_P2 - roll_P1`, never a target angle |
| P1-P3 | the same-angle null, plus a mount-hysteresis control |
| Darks | one set before and one after the session |
| Directory | separate from B1 (`.../udc_rotation_*/`), no B1 coordinates reused |

Deliberate choices:

* **No derotation.** B2 translates the analysis window to each bracket's own
  centre and does nothing else geometric. The angle is the signal.
* **No fixed 20 degrees.** The holder is a rigid bracket on a stand and creeps
  back toward vertical. The settled roll is measured; if `|delta|` falls below
  10 degrees or rises above 45 degrees the operator is told and must either move
  the mount or explicitly accept the measured pair, which is then reported as
  measured.
* **P1-P3 is not assumed zero.** A residual is kept as data: below 10 degrees it
  is a small-rotation pair, above that the recorded residual is used.
* **Only unusable data stops the session.** A small or missing angular response
  is reported as unresolved, not converted into a verdict.
* **Resume.** Completed brackets are skipped; a resumed mount state is
  re-measured and the bracket keeps the roll of the state placement that
  preceded it in the log.

## Estimator

Peak anchor, then an **iterated full-disc centroid**.

The brightest pixel is not the centre of symmetry when the PSF carries a
four-fold pattern: with 25 % azimuthal modulation the maximum sits up to a pixel
away from the geometric centre, and one binned pixel is 18 degrees at r = 3 px.
A local quadratic fit inherits that displacement. A centroid over a full disc
averages `cos 4(theta)` to zero, so the modulation cancels and only the radial
falloff sets the first moment; it is iterated from the peak until it stops
moving. The quadratic vertex is kept as a cross-check, and a disagreement above
2 px is refused.

Radial grid: r = 1.5 to 6.0 px in 0.5 px steps, 180 azimuth samples (2 degree
step). Four CFA planes (B, Gb, Gr, R) are analysed separately because the
quad-CFA quadrants differ; a rotation must move all four by the same angle.

## Offline validation

### Synthetic

* a fractional centre is recovered to better than 0.25 px;
* a known 20 degree rotation is recovered with the right sign (within 1.5 deg);
* 70 degrees is resolved as -20 degrees through the four-fold wrap;
* pure translation of 2 px with unchanged azimuth does not produce a rotation
  (slope 0.00 against a 0.10 threshold);
* a display-fixed model gives slope 0.00 and a mount-fixed model slope 1.00
  against measured roll;
* a flat profile is rejected as "not a measurement";
* a clipped patch is refused, and a bracket with a missing temporal mean is
  unusable rather than silently zero-shift.

### On the archived B1 frames

B1 repeats its shortest exposure between the longer tiers at one fixed mount
position, so the angular shift between two of those repeats must be zero. That
scatter is the estimator's noise floor on real hardware, with no rotation at
all. All B1 batches were used read-only; nothing was recaptured.

Null shift between same-position repeats (Gr plane, 10-15 pairs per band):

| Band | pairs | median | MAD | worst |
| --- | --- | --- | --- | --- |
| 450 nm | 10 | -0.59 deg | 0.32 deg | 1.62 deg |
| 550 nm | 15 | -0.11 deg | 0.07 deg | 0.42 deg |
| 650 nm | 10 | -0.05 deg | 0.12 deg | 1.13 deg |

Gb behaves the same way (MAD 0.12-0.24 deg). Centre repeatability over the
unclipped repeats is 0.03-0.06 px peak-to-peak. A 20 degree rotation is therefore
two orders of magnitude above the measured noise floor, and the same-angle null
tolerance of 5 degrees in the analysis is generous by design.

The four-fold structure is strong and reproducible in B1: the harmonic-4
amplitude of the azimuthal profile rises from 0.05 at r = 1.5 px to 0.2-0.5 by
r = 6 px, and its phase reproduces within about 1-3 degrees between repeats of
the same band. Gr and Gb carry different phases, which is the quad-CFA quadrant
difference and the reason the planes are treated separately.

### One finding that changed the analysis

On the clipped tiers (peak at the 1023 DN ceiling) the global maximum is the
first code at the ceiling rather than the brightest part of the spot, and the
"centre" moved by up to 38 px. The analysis therefore refuses a clipped patch
and averages the bracket's unsaturated reference repeats instead, which is also
free signal-to-noise for the faint rings.

## Tests

`478 passed, 3 skipped` in the local environment (`pytest -q`), including new
rotation, driver and B2 analysis tests. No hardware was touched by the tests.

## Limits that remain

* **Field dependence is not addressed by centring.** Peak and centroid alignment
  fixes the analysis origin, not the position of the spot on the sensor. The
  operator still has to place the phone so the spot returns to the same field
  position; the recorded per-bracket centre makes a large failure visible.
* **The holder moves in tilt as well as roll.** Continuous logging plus the
  per-batch centre trajectory are the only detection, not a correction.
* **The roll reading is the accelerometer's**, with its own bias and mounting
  alignment error. Only differences between states are used.
* **Four-fold aliasing** limits the usable range to `|delta| < 45 deg`.
* **One geometry, one holder.** Nothing here generalises to a hand-held phone or
  to a different pinhole distance.
* **B2 has not run.** Until it does, the display-versus-mount question is open
  for this prototype.
