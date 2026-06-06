# Research idea: low-cost LCD programmable diffractive multispectral imaging

The long-term research idea is organized as a layered chain:

```text
end-to-end joint optimization
  <= multi-frame joint reconstruction
  <= differentiable masks + LCD-to-PSF peak-cluster forward model
  <= dynamic coding + diffractive coding
  <= low-cost programmable LCD hardware
```

The bottom layer is the hardware source of degrees of freedom. A mono LCD is
not chosen because it is an ideal optical modulator. It is chosen because it is
low-cost, dynamically refreshable, easy to control, and widely available. Its
non-idealities include pixel-grid diffraction, polarization dependence,
non-ideal transmittance, diffraction side peaks, display nonuniformity, and
mask-display errors. The central strategy is not to eliminate all of these
imperfections, but to convert the stable, repeatable, and controllable part of
the non-ideal diffraction response into an encoding resource. This distinguishes
the route from conventional SLM, DMD, CASSI, and custom DOE systems.

The physical coding layer consists of dynamic coding and diffractive coding.
Dynamic coding provides diversity across multiple captured frames by changing
LCD masks over time. Diffractive coding provides wavelength-dependent PSF and
OTF variations. Together, they make the system rely not on single-shot hardware
spectral separation, but on multi-frame, multi-mask, multi-wavelength
transfer-function diversity. The relevant criterion is not whether a PSF appears
visually complex, but whether the multi-frame frequency-domain observation
matrix has a favorable singular-value spectrum over the target spatial-frequency
bands.

The model layer is the differentiable mask-to-PSF forward model. It must solve
two problems. First, it should predict the PSF induced by a new LCD mask,
instead of requiring every candidate mask to be physically measured. Second, it
should allow mask parameters to be optimized by backpropagating reconstruction
loss through the optical encoding model. The peak-cluster PSF representation is
central here. It is not merely a storage-efficient PSF compression method. Its
purpose is to capture large-area, low-energy, but stable diffraction patterns
that would be truncated or made impractical by a dense ROI kernel. A
conventional dense PSF kernel has description and storage complexity `O(WH)`, or
`O(W^2)` for a square kernel. In contrast, a peak-cluster representation has
complexity `O(Kd)`, where `K` is the number of diffraction peaks or peak
clusters and `d` is the number of degrees of freedom per cluster. When the
per-cluster model family is fixed, this becomes effectively `O(K)`. This makes
it possible to model broad, sparse, and stable diffraction structures at finite
cost.

## Medium-Term Representation Target

The current `PeakPatchPSFDictionary` is a v1 data-contract baseline. It proves
that `optic_system` can move from a full-frame scout survey to a measured,
coordinate-preserving peak-patch artifact and export it in a form that
`LCD_forward` can read. It is not the final PSF representation.

The medium-term target is an `AdaptivePeakClusterPSFDictionary`. In that
representation, one PSF entry is an indexed collection of physical diffraction
peak clusters rather than a dense stack of equal-size patches:

```text
PSF entry
  -> peak_cluster_0:
       center_xy
       support_type
       radius_px or bbox_xyxy
       raw_patch
       support_mask
       background
       energy
       peak_value
       full-frame coordinate metadata
  -> peak_cluster_1:
       ...
```

Different peak clusters in the same PSF may use different radii, rectangles, or
support masks. The important unit is not a fixed ROI window; it is a real,
traceable diffraction support with original full-frame coordinates and local
raw data. Fixed-size peak patches remain useful as compatibility output, but
the production scientific representation should preserve adaptive per-cluster
support.

The staged path is:

```text
FullFramePSFSurvey
  -> first-pass PeakLayoutProfile
  -> fixed-size PeakPatchPSFDictionary baseline
  -> PeakSupportAnalysisReport
  -> SupportCandidateStabilityReport
  -> AdaptivePeakLayoutProfile
  -> AdaptivePeakClusterPSFDictionary
```

The support-analysis and stability-audit stages exist to avoid promoting
apparent full-frame background integration into a peak-cluster layout. Only
stable, significant diffraction components should become adaptive peak-cluster
support.

The inverse-problem layer is multi-frame joint reconstruction. The frames should
not be reconstructed independently and fused afterward. Instead, all frames
should be written as a single joint observation system. The goal is to use
different masks to induce different frequency-domain transfer structures,
thereby improving spectral-channel separability. In this sense, multi-frame
joint reconstruction is the algorithmic realization of the H-matrix analysis,
not merely repeated image acquisition.

The top layer is end-to-end joint optimization. The final objective is not to
optimize only a reconstruction network or a regularizer. It is to jointly
optimize mask design, optical coding, the differentiable forward model, and the
reconstruction algorithm under the physical constraints of a cheap LCD. The
target is not that a particular hand-designed LCD mask can work. The target is
that the system can automatically discover dynamic diffractive coding strategies
that are most useful for multispectral recovery under the constraints of
low-cost LCD hardware.

This research route therefore treats the LCD not as a poor substitute for an
ideal modulator, but as a low-cost programmable diffractive element whose stable
non-idealities can be measured, modeled, and optimized.

## Repository boundary

This research route is a cross-repository loop, not an `optic_system`
monolith.

`optic_system` supports the route by controlling hardware, visualizing and
synchronizing acquisition, preserving raw captures, building profile artifacts,
storing full-frame PSF scout surveys, deriving support and layout evidence,
building measured peak-cluster dictionary evidence, recording diagnostics, and
publishing measured handoffs. It stops at measured artifacts, support evidence,
layout evidence, adaptive peak-cluster dictionary evidence, diagnostics, and
handoff publication.

The current peak layout derivation is a replaceable first-pass implementation.
It provides an auditable threshold-and-component baseline for estimating peak
patch geometry, not the final peak tracking or diffraction-order modelling
method. Its detection policy is explicitly marked as a high-energy layout
baseline because it may miss low-energy but stable far-field diffraction peaks.

`lcd_mask_families` owns reusable mask-family definitions, schemas, generators,
projection rules, and versioned mask identity. `optic_system` may consume
explicit masks or mask specs from that repository once it exists, but it does
not own mask-family design.

`LCD_forward` owns mask-to-operator work: LCD mask-to-peak-cluster/operator
surrogate modelling, LCD-to-measured-response learning, mask-family evaluation,
parameter selection, operator-aware mask-sequence design from measured
evidence, peak-cluster operator package generation, and H-matrix/operator
diagnostics.

`reconstruction` owns inverse problems: forward/adjoint consumption,
multi-frame joint reconstruction, reconstruction pipelines, learned
reconstruction, task-level evaluation, and reconstruction-driven capture-plan
proposals.

End-to-end optimization is therefore a cross-repository research loop:

```text
lcd_mask_families
  -> mask specs / physical mask generation rules
  -> optic_system measured acquisition
  -> LCD_forward measured-response/operator learning and design
  -> reconstruction inverse-problem evaluation
  -> capture-plan or mask-family feedback
```

`optic_system` must not train peak-cluster forward models, differentiable
forward surrogates, reconstruction networks, or mask-optimization loops. It
must also not implement cross-repository APIs before those repository contracts
exist.
