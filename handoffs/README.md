# Handoffs

This directory reserves future cross-repository handoff boundaries for
`optic_system`.

It is intentionally non-functional:

- it must not import external repositories;
- it must not define final external schemas;
- it must not contain generated large artifacts;
- real data should live in third-party storage or ignored output paths;
- only manifests, small specs, or examples should be committed when
  appropriate.

See `docs/cross_repository_boundary.md` for the normative boundary.
