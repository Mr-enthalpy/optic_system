# Legacy Reference

The historical `old/` prototype directory was removed from the active
repository surface.

It was retained only as a reconstruction reference. Its former responsibilities
are now covered by the current architecture:

- `SessionController` and control commands for GUI-driven device intent;
- device services for camera, LCD, and TLS boundaries;
- camera sidecar and shared-memory frame streaming for image acquisition;
- capture tasks and raw HDF5 writers for metadata-first measurements;
- `scripts/monitor_run_status.py` for read-only run-status monitoring;
- `TLSService` and `tls_c1` for active TLS control.

For historical inspection, use Git history before the cleanup commit that
deleted `old/`.
