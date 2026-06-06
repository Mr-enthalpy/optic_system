# Outgoing Measured Evidence Handoffs

Reserved for future measured evidence consumed by `LCD_forward`.

Examples may include manifests referencing `FullFramePSFSurvey`,
`SensorEnergyCenterProfile`, support reports, stability reports, layout
profiles, and adaptive peak-cluster dictionaries.

This directory is intentionally non-functional. It reserves a future handoff
boundary only.

Rules:

- do not import `LCD_forward`;
- do not implement an `LCD_forward` client or adapter here;
- do not define final measured-evidence schemas here;
- do not duplicate large artifacts in Git;
- real data should live in third-party storage or ignored output paths;
- commit only manifests/spec examples when appropriate.
