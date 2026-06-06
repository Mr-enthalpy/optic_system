# Outgoing Measured Evidence Handoffs

Reserved for future measured evidence consumed by `LCD_forward`.

Examples may include manifests referencing `FullFramePSFSurvey`,
`SensorEnergyCenterProfile`, support reports, stability reports, layout
profiles, and adaptive peak-cluster dictionaries.

This directory is intentionally non-functional. It reserves a future handoff
boundary only.

The current peak-patch measured-evidence HDF5 publisher is a v1 compatibility
artifact derived from the existing `PeakPatchPSFDictionary` path. It does not
define the final external `MeasuredEvidenceHandoff` schema.

Rules:

- do not import `LCD_forward`;
- do not implement an `LCD_forward` client or adapter here;
- do not define final measured-evidence schemas here;
- do not duplicate large artifacts in Git;
- real data should live in third-party storage or ignored output paths;
- commit only manifests/spec examples when appropriate.
