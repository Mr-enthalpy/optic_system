# Hardware validation

No real camera, LCD or TLS test was executed as part of the UDC rebuild. The default pytest suite uses fake/mock devices or pure arrays. That validates software only.

Use this checkout's Python environment. Install external camera/TLS packages and configure vendor SDK paths through environment variables. Select `OPTIC_SYSTEM_LCD_DISPLAY_INDEX` and `OPTIC_SYSTEM_LCD_SUBPIXEL_AXIS` (0 or 1); physical display initialization fails without both selections. Optional `OPTIC_SYSTEM_EXPECT_LCD_LOGICAL_SHAPE` verifies the actual display geometry. Never use resolution to choose the target device.

The GUI and camera CLI open headlessly with trigger disabled; stream startup is explicit. PreConfigGUI is rejected. TLS wavelength target and movement remain separate; broadband calibration uses pass-through. See [profile workflow](profile_task_chain.md) and [raw schema](raw_capture_schema.md).

Hardware entry points are opt-in:

```powershell
$env:OPTIC_SYSTEM_RUN_PHASE2_HARDWARE_TESTS="1"
# Configure LCD index/axis, vendor paths and actual mask plans first.
.\.venv\Scripts\python.exe -m pytest -q tests/test_phase2_hardware_smoke.py
```

Camera sidecar tests use `OPTIC_SYSTEM_HARDWARE_TEST=1`; dedicated TLS tests use `TLS_C1_RUN_HARDWARE_TESTS=1`. Phase2 tests only enable TLS movement with `OPTIC_SYSTEM_RUN_TLS_HARDWARE_TESTS=1`. Read their hardware-specific environment fields before enabling motion.

Before sensing experiments validate: actual frame extent/bit depth, trigger/free-run behavior, exposure/gain readback and settling, LCD axis/mask identity, TLS pass-through and monochromatic movement, full-frame energy/valid pixels, dark/background, repeatability and source drift. Record raw data and reports for failed as well as successful acquisition. A smoke capture establishes communication and storage, not optical alignment or the wavelength-dependent sensing mechanism.
