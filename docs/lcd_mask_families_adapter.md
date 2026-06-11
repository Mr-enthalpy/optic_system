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
capture plan / task intent -> render mask spec through adapter
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

## Usage

Mask instance:

```python
from capture.mask_family_adapter import render_mask_instance_file

rendered = render_mask_instance_file("path/to/stripes_instance.yaml")
mask = rendered.mask
metadata = rendered.capture_metadata()
```

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

## Capture-Plan Fragment

This PR does not add a broad capture-plan schema. A future small hook may use a
fragment like this if it fits the active capture task cleanly:

```yaml
masks:
  source: lcd_mask_families_spec
  spec_path: path/to/mask_sequence.yaml
```

Until such a hook exists, callers can render a spec directly through the helper
and pass the resulting display mask array through existing LCD device
boundaries.

## LCD Boundary

Rendered mask arrays must enter hardware display only through existing
`LCDService` / LCD adapter paths. Do not bypass physical mono mask validation,
packing policy, display index selection, or LCD metadata recording.

The adapter produces the display mask array requested by the external spec.
Existing LCD code remains responsible for accepting or rejecting that array as
the configured physical mono mask.

## Metadata

When an adapter-rendered mask is used for capture, record enough identity to
reconstruct what was requested:

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
```

`RenderedCaptureMask.capture_metadata()` returns a JSON-friendly dictionary
with these fields under local names. Do not use filenames alone as mask
identity.

## Failure Criteria

Stop the integration attempt if using `lcd_mask_families` requires broad
capture-plan rewrites, raw schema rewrites, direct external object propagation,
or importing downstream modelling/reconstruction concepts into `optic_system`.
