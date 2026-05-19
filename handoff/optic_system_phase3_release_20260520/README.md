# optic_system Phase 3 Release 2026-05-20

This directory is the Git-tracked release descriptor.

It does not contain the large release artifact payload.

Canonical artifact payload:

- `D:/datasets/optic_system/phase3_release_20260520/`

This path is the local canonical payload location on the acquisition
workstation. It is a location hint, not a hard dependency; relocated copies
should be verified with `MANIFEST.json` and `SHA256SUMS.txt`.

Tracked files here:

- `RELEASE.json`
- `MANIFEST.json`
- `SHA256SUMS.txt`
- `data_contract.md`

The payload itself is organized as:

```text
optic_system_phase3_release_20260520/
|-- common/
|-- lcd_forward/
`-- thesis/
```

Do not commit copied `.h5`, `.npy`, or bulk `.png` payloads into this
repository.
