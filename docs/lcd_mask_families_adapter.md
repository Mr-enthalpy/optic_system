# lcd_mask_families Adapter

This is an experimental integration note for an optional wrapper around
`lcd_mask_families` v0.1 mask specs.

The adapter asks one bounded question:

```text
Can optic_system consume lcd_mask_families v0.1 specs through a local adapter
while preserving its hardware/data/diagnostic boundary?
```

## Boundary

`lcd_mask_families` owns:

```text
family + parameters + grid + projection policy -> mask
```

`optic_system` owns:

```text
PupilProfile + mask spec -> physical LCD pupil placement
  -> LCDService display -> raw capture metadata
```

The adapter is a handoff consumer only. It does not design mask families,
optimize masks, interpret PSF effects, model LCD response, call `LCD_forward`,
or participate in reconstruction.

## Dependency

`lcd_mask_families` is optional. Normal `optic_system` imports and default
no-hardware tests must work without it.

For local development, install the sibling package explicitly:

```bash
pip install -e C:\Users\hanni\PycharmProjects\lcd_mask_families
```

The adapter imports `lcd_mask_families` only inside
`capture.mask_family_adapter`. Missing dependency errors are raised as
`LcdMaskFamiliesUnavailableError` with an explicit message.

## Public API Surface

The adapter uses only the v0.1 public package exports:

```python
load_mask_instance_spec
load_mask_sequence_spec
render_mask_instance
render_mask_sequence
CONTRACT_VERSION
__version__
```

It does not import internal family modules, registries, hashing helpers, or
private functions.

## Adapter Layers

### Render-only helper

The render-only helper checks optional dependency loading and
`lcd_mask_families` v0.1 spec rendering. It is profile-unaware and has no
hardware semantics.

Use it for dry-run inspection, metadata inspection, and offline rendering
tests. Do not treat its output as ready for real hardware capture.

### Profile-aware adapter

The profile-aware entrypoint requires optic_system PupilProfile metadata before
returning a metadata-bound local object:

```python
from capture.mask_family_adapter import render_mask_instance_file_for_pupil_profile

rendered = render_mask_instance_file_for_pupil_profile(
    "path/to/stripes_instance.yaml",
    pupil_profile,
)
```

This gate records PupilProfile identity and effective LCD pupil geometry, but
does not place pixels into a full LCD-shaped array. Its renderer metadata
records `physical_placement_implemented: false`.

### Strict physical embedding

The strict embedding entrypoint requires:

* an already rendered local pupil mask;
* optic_system PupilProfile metadata with an explicit `aperture_window`;
* the full physical LCD shape `lcd_shape_hw`.

```python
from capture.mask_family_adapter import render_and_embed_mask_instance_file_for_pupil_profile

physical = render_and_embed_mask_instance_file_for_pupil_profile(
    "path/to/stripes_instance.yaml",
    pupil_profile,
    lcd_shape_hw=(1080, 5760),
)
```

`PupilProfile.aperture_window` is interpreted as `x0, y0, x1, y1` with
Python-style exclusive upper bounds:

```python
physical_mask[y0:y1, x0:x1] = local_mask
```

Strict embedding validates exact shape and bounds:

* `0 <= x0 < x1 <= lcd_width`;
* `0 <= y0 < y1 <= lcd_height`;
* `local_mask.shape == (y1 - y0, x1 - x0)`;
* `rendered.grid["shape_hw"] == local_mask.shape`;
* coordinate frame is `normalized_lcd_pupil` or `pixel_index`;
* projection output and local mask dtype are `uint8`.

It does not resize masks, crop masks, pad masks, interpolate values, wrap
coordinates, infer LCD shape, or derive a display slice from center/radius.
Center/radius-only profiles are rejected for physical embedding until a
separate deterministic policy is defined.

### Capture task

A future capture task hook must consume strict physical embedding output for
real capture, send `RenderedPhysicalMask.physical_mask` through `LCDService`,
and record both mask identity and PupilProfile placement geometry in raw
metadata.

## Usage

Mask instance:

```python
from capture.mask_family_adapter import render_mask_instance_file

rendered = render_mask_instance_file("path/to/stripes_instance.yaml")
mask = rendered.mask
metadata = rendered.capture_metadata()
```

This is a render-only, profile-unaware call. `metadata["usage_scope"]` is
`dry_run_profile_unaware`.

Mask sequence:

```python
from capture.mask_family_adapter import render_mask_sequence_file

rendered_masks = render_mask_sequence_file("path/to/mask_sequence.yaml")
for rendered in rendered_masks:
    mask = rendered.mask
    mask_id = rendered.mask_id
```

The adapter returns `RenderedCaptureMask`, an `optic_system`-local neutral
object. Downstream code should not depend on `lcd_mask_families.RenderedMask`
layout.

Strict embedding returns `RenderedPhysicalMask`, which carries both
`local_mask` and `physical_mask`. `physical_mask` is the full LCD-shaped array
that may be passed to existing LCD display paths.

## Capture-Plan Fragment

This PR does not add a broad capture-plan schema. A future small hook may use a
fragment like this if it fits the active capture task cleanly:

```yaml
masks:
  source: lcd_mask_families_spec
  spec_path: path/to/mask_sequence.yaml
```

Until such a hook exists, callers may render a spec directly only for dry-run
or offline inspection. Profile-unaware helper output must not be sent directly
into a hardware capture path.

## LCD Boundary

Rendered mask arrays must enter hardware display only through existing
`LCDService` / LCD adapter paths. Do not bypass physical mono mask validation,
packing policy, display index selection, or LCD metadata recording.

The render-only adapter produces the local pupil mask requested by the external
spec. Strict physical embedding places that local mask into a full LCD-shaped
array. Existing LCD code remains responsible for accepting or rejecting that
array as the configured physical mono mask.

## Metadata

When an adapter-rendered mask is used for capture, record enough identity to
reconstruct what was requested and where the abstract mask was bound in the
physical LCD pupil:

```text
lcd_mask_families_contract_version
lcd_mask_families_renderer_version
mask_hash
mask_id
family_id
family_version
grid
projection
source_spec_path or source_spec_uri
pupil_profile_id
lcd_coordinate_convention
lcd_display_index
subpixel_axis
lcd_physical_center
lcd_physical_radius or aperture_window
lcd_shape_hw
placement_window_xyxy
outside_value
local_mask_shape_hw
physical_mask_shape_hw
```

`RenderedCaptureMask.capture_metadata()` returns render/profile metadata.
`RenderedPhysicalMask.capture_metadata()` returns physical placement metadata.
Do not use filenames alone as mask identity.

## Failure Criteria

Stop the integration attempt if using `lcd_mask_families` requires broad
capture-plan rewrites, raw schema rewrites, direct external object propagation,
or importing downstream modelling/reconstruction concepts into `optic_system`.
