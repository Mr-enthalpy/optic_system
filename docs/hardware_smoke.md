# Hardware validation

Real camera, LCD and TLS communication, calibration and fixed-display PSF measurements have now been executed; the [2026-10-17 report](reports/2026-10-17-fixed-display-psf.md) records what passed, the actual acquisition commit and scientific limits. The earlier rebuild report describes its own no-hardware scope. Default pytest still uses fake/mock devices or pure arrays and validates software only.

Use this checkout's Python environment. Install external camera/TLS packages and configure vendor SDK paths through environment variables. Select `OPTIC_SYSTEM_LCD_DISPLAY_INDEX` and `OPTIC_SYSTEM_LCD_SUBPIXEL_AXIS` (0 or 1); physical display initialization fails without both selections. Optional `OPTIC_SYSTEM_EXPECT_LCD_LOGICAL_SHAPE` verifies the actual display geometry. Never use resolution to choose the target device.

The GUI and camera CLI open headlessly with trigger disabled; stream startup is explicit. PreConfigGUI is rejected. TLS wavelength target and movement remain separate; broadband calibration uses pass-through. See [profile workflow](profile_task_chain.md) and [raw schema](raw_capture_schema.md).

Hardware entry points are opt-in:

```powershell
$env:OPTIC_SYSTEM_RUN_PHASE2_HARDWARE_TESTS="1"
# After device ownership and camera SDK checks; this opens the real camera.
.\.venv\Scripts\python.exe -m pytest -q tests/test_phase2_hardware_smoke.py::test_camera_capture_one_hardware
```

Camera sidecar tests use `OPTIC_SYSTEM_HARDWARE_TEST=1`; dedicated TLS tests use `TLS_C1_RUN_HARDWARE_TESTS=1`. Phase2 tests only enable TLS movement with `OPTIC_SYSTEM_RUN_TLS_HARDWARE_TESTS=1`. Read their hardware-specific environment fields before enabling motion.

`test_tls_status_hardware` and `tests/test_tls_hardware_smoke.py` set a grating/wavelength and can move the instrument; their names do not make them read-only. For a connect/status/close-only check, use the existing TLSService API with `close` in a `finally` block, without set/move/pass-through calls. Enable motion only after the current optical safety and wavelength range have been confirmed. Do not run an entire opt-in directory merely to query one device.

LCD output requires the confirmed physical display and actual interactive Windows desktop. Remote-control privacy/black-screen mode can replace the physical panel output even while the framebuffer reports success; disable it for experiments and verify the session after display-topology changes. Once identity/output is verified, routine captures need no repeated human observation of each pattern. An authorized persistent local desktop task may be coordinated through SSH; it is not a new network camera/display service.

For Camera+LCD without TLS, run only
`tests/test_phase2_hardware_smoke.py::test_capture_no_tls_hardware` in the
confirmed interactive desktop session. This test explicitly selects the existing
`diagnostic` runtime: real camera/LCD devices are required, fake devices remain
forbidden, and no TLS connection or movement occurs. The plan's 550 nm value is
only a nominal smoke-test label, not a measured illumination wavelength.
RawCapture records `runtime_mode=diagnostic`; scientific calibration and optical
alignment flags remain false. Formal hardware capture still requires a valid
TLS setpoint for monochromatic illumination. Do not relax that check to make a
smoke test pass, or convert this uncalibrated smoke capture into a PSF survey.

Before sensing experiments validate: actual frame extent/bit depth, trigger/free-run behavior, exposure/gain readback and settling, LCD axis/mask identity, TLS pass-through and monochromatic movement, full-frame energy/valid pixels, dark/background, repeatability and source drift. Record raw data and reports for failed as well as successful acquisition. A smoke capture establishes communication and storage, not optical alignment or the wavelength-dependent sensing mechanism.

Use the measured transport full-scale and verified optical valid-pixel domain for safety checks. Some cameras place metadata in raw pixels; an all-pixel GUI maximum may include those bytes and is not automatically an optical saturation metric. Preserve them in raw observations and exclude only verified coordinates in analysis/calibration. Never exclude a bright optical pixel merely to pass saturation checks. Use raw burst maxima as well as the averaged preview: averaging does not undo clipped frames or prove source stability.

After a camera transport update, run client and sidecar from the same checkout. Verify frame bytes against their notifications, including after a deliberate consumer pause longer than the ring duration. Where the camera embeds timestamps/counters in raw pixels, compare those bytes against SDK packet metadata and check counter order independently. Embedded header pixels are not optical samples: preserve them in raw measurements and exclude their verified coordinates in optical analysis. No hardware acceptance is implied by the no-hardware overwrite regressions.
