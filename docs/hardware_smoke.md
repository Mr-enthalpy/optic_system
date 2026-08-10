# Hardware Smoke Capture Guide — Phase 2B

This document describes how to run hardware smoke capture validation on the
current local experimental setup.

## LCD Identity Model

The software does **not** infer which display is the target mono LCD.
The user must select the display index and subpixel axis.
The software only checks internal consistency and records metadata.

- **display_index**: which SDL display to use for the mono LCD panel.
  Configure via ``--lcd-display-index`` or ``OPTIC_SYSTEM_LCD_DISPLAY_INDEX``.
  If not provided, SDL auto-detection is used (may select a normal monitor).

- **subpixel_axis**: which axis carries the 3 subpixels.
  ``0`` = height tripled ``[3H, W]``; ``1`` = width tripled ``[H, 3W]``.
  Configure via ``--lcd-subpixel-axis`` or ``OPTIC_SYSTEM_LCD_SUBPIXEL_AXIS``.
  Defaults to ``1`` if not set.

- **OPTIC_SYSTEM_EXPECT_LCD_LOGICAL_SHAPE** (optional): enforce a specific
  expected logical shape (e.g. ``"2560,540"``). If set and the display
  does not match, the hardware test fails.

## Current Local Hardware Setup

The following values describe the **current local setup** used for Phase 2B
smoke validation.  They are not universal defaults — replace with your own
hardware configuration.

| Component | Detail |
|-----------|--------|
| Camera | FlyCapture2 PT Grey Grasshopper3, serial `15471217`, 2448×2048, raw8 |
| Camera SDK | ``C:\Program Files\Point Grey Research\FlyCapture2`` |
| LCD | SDL display index `1`, reported `(2560, 540, 3)`, physical `(2560, 1620)`, subpixel_axis=1 |
| TLS | Zolix Omni-λ, serial `OM319069`, USB |
| TLS SDK | ``tls_c1`` (local deployment) |

## Environment Variables

### Required for hardware capture

| Variable | Purpose | Example |
|----------|---------|---------|
| ``FLYCAPTURE2_SDK_DIR`` | FlyCapture2 SDK root | ``C:\Program Files\Point Grey Research\FlyCapture2`` |
| ``FLYCAPTURE2_DLL_DIR`` | FlyCapture2 DLL directory | ``C:\Program Files\Point Grey Research\FlyCapture2\bin64\vs2015`` |

### Required for hardware tests

| Variable | Purpose | Example |
|----------|---------|---------|
| ``OPTIC_SYSTEM_RUN_PHASE2_HARDWARE_TESTS`` | Enable Phase 2B hardware tests | ``1`` |
| ``OPTIC_SYSTEM_LCD_DISPLAY_INDEX`` | Target LCD SDL display | ``1`` |
| ``OPTIC_SYSTEM_LCD_SUBPIXEL_AXIS`` | Subpixel axis (0 or 1) | ``1`` |

### Required for TLS (optional)

| Variable | Purpose | Example |
|----------|---------|---------|
| ``OPTIC_SYSTEM_RUN_TLS_HARDWARE_TESTS`` | Enable TLS sub-tests | ``1`` |
| ``TLS_C1_SERIAL`` | TLS device serial number | ``OM319069`` |
| ``TLS_C1_SAFE_GRATING`` | Safe grating number | ``1`` |
| ``TLS_C1_SAFE_WAVELENGTH_NM`` | Safe test wavelength | ``650.0`` |

### Optional

| Variable | Purpose | Example |
|----------|---------|---------|
| ``OPTIC_SYSTEM_EXPECT_LCD_LOGICAL_SHAPE`` | Enforce expected LCD dimensions | ``"2560,540"`` |
| ``OPTIC_SYSTEM_CAMERA_INDEX`` | Camera index | ``0`` |

## Step-by-Step Commands

### 1. Generate Smoke Masks

The generated ``.npy`` shape **must** match the ``physical_shape`` reported by
``LCDService.get_metadata()`` at capture time.  If the LCD logical shape or
subpixel axis changes, re-generate masks.

```bash
# cmd.exe
python scripts/make_smoke_masks.py --output-dir plans/generated_masks --logical-shape 2560 540 --subpixel-axis 1

# PowerShell
python scripts/make_smoke_masks.py --output-dir plans/generated_masks --logical-shape 2560 540 --subpixel-axis 1
```

Generated files (``[2560, 1620]`` physical mono, ``uint8``):

| File | Pattern |
|------|---------|
| ``all_black.npy`` | All zeros (opaque) |
| ``all_white.npy`` | All 255 (transmissive) |
| ``vertical_stripes_coarse.npy`` | Vertical stripes |
| ``horizontal_stripes_coarse.npy`` | Horizontal stripes |

### 2. Dry-Run (no hardware)

```bash
# cmd.exe / PowerShell
python scripts/capture_forward_dataset.py --plan plans/hardware_smoke_no_tls.yaml --output out.h5 --dry-run
```

### 3. Hardware Capture — Camera + LCD

```cmd
:: cmd.exe
set FLYCAPTURE2_SDK_DIR=C:\Program Files\Point Grey Research\FlyCapture2
set FLYCAPTURE2_DLL_DIR=C:\Program Files\Point Grey Research\FlyCapture2\bin64\vs2015

python scripts/capture_forward_dataset.py ^
  --plan plans/hardware_smoke_no_tls.yaml ^
  --output data/raw/hardware_smoke_no_tls.h5 ^
  --hardware ^
  --lcd-display-index 1 ^
  --lcd-subpixel-axis 1
```

```powershell
# PowerShell
$env:FLYCAPTURE2_SDK_DIR="C:\Program Files\Point Grey Research\FlyCapture2"
$env:FLYCAPTURE2_DLL_DIR="C:\Program Files\Point Grey Research\FlyCapture2\bin64\vs2015"

python scripts/capture_forward_dataset.py `
  --plan plans/hardware_smoke_no_tls.yaml `
  --output data/raw/hardware_smoke_no_tls.h5 `
  --hardware `
  --lcd-display-index 1 `
  --lcd-subpixel-axis 1
```

### 4. Hardware Capture — Camera + LCD + TLS

```cmd
:: cmd.exe
set FLYCAPTURE2_SDK_DIR=C:\Program Files\Point Grey Research\FlyCapture2
set FLYCAPTURE2_DLL_DIR=C:\Program Files\Point Grey Research\FlyCapture2\bin64\vs2015
set TLS_C1_SERIAL=OM319069

python scripts/capture_forward_dataset.py ^
  --plan plans/hardware_smoke_with_tls.yaml ^
  --output data/raw/hardware_smoke_with_tls.h5 ^
  --hardware ^
  --enable-tls ^
  --lcd-display-index 1 ^
  --lcd-subpixel-axis 1
```

```powershell
# PowerShell
$env:FLYCAPTURE2_SDK_DIR="C:\Program Files\Point Grey Research\FlyCapture2"
$env:FLYCAPTURE2_DLL_DIR="C:\Program Files\Point Grey Research\FlyCapture2\bin64\vs2015"
$env:TLS_C1_SERIAL="OM319069"

python scripts/capture_forward_dataset.py `
  --plan plans/hardware_smoke_with_tls.yaml `
  --output data/raw/hardware_smoke_with_tls.h5 `
  --hardware `
  --enable-tls `
  --lcd-display-index 1 `
  --lcd-subpixel-axis 1
```

### 5. Inspect Output

```bash
python scripts/inspect_raw_capture.py data/raw/hardware_smoke_no_tls.h5
```

Prints plan ID, capture count, frame/mask shapes, wavelengths, processing flags,
and dataset listing without scientific interpretation.

### 6. Run Hardware Tests

```cmd
:: cmd.exe
set OPTIC_SYSTEM_RUN_PHASE2_HARDWARE_TESTS=1
set OPTIC_SYSTEM_LCD_DISPLAY_INDEX=1
set OPTIC_SYSTEM_LCD_SUBPIXEL_AXIS=1
set FLYCAPTURE2_SDK_DIR=C:\Program Files\Point Grey Research\FlyCapture2
set FLYCAPTURE2_DLL_DIR=C:\Program Files\Point Grey Research\FlyCapture2\bin64\vs2015

pytest tests/test_phase2_hardware_smoke.py -v

:: With TLS:
set OPTIC_SYSTEM_RUN_TLS_HARDWARE_TESTS=1
set TLS_C1_SERIAL=OM319069
pytest tests/test_phase2_hardware_smoke.py -v
```

```powershell
# PowerShell
$env:OPTIC_SYSTEM_RUN_PHASE2_HARDWARE_TESTS="1"
$env:OPTIC_SYSTEM_LCD_DISPLAY_INDEX="1"
$env:OPTIC_SYSTEM_LCD_SUBPIXEL_AXIS="1"
$env:FLYCAPTURE2_SDK_DIR="C:\Program Files\Point Grey Research\FlyCapture2"
$env:FLYCAPTURE2_DLL_DIR="C:\Program Files\Point Grey Research\FlyCapture2\bin64\vs2015"

pytest tests/test_phase2_hardware_smoke.py -v

# With TLS:
$env:OPTIC_SYSTEM_RUN_TLS_HARDWARE_TESTS="1"
$env:TLS_C1_SERIAL="OM319069"
pytest tests/test_phase2_hardware_smoke.py -v
```

Expected hardware test results (current local setup)::

```
test_camera_capture_one_hardware   PASSED   serial=15471217  2448×2048
test_lcd_show_mask_hardware        PASSED   display=1  physical=(2560,1620)
test_tls_status_hardware           PASSED   device=0  grating=1
test_capture_no_tls_hardware       PASSED   3 captures  HDF5 valid
test_capture_with_tls_hardware     PASSED   4 captures  HDF5 valid
```

## Expected Physical Mask Shapes

Physical mono shape depends on ``subpixel_axis``:

| subpixel_axis | logical (H,W) | physical shape |
|:---:|------|--------|
| 0 | (540, 2560) | (1620, 2560) |
| 1 | (2560, 540) | (2560, 1620) |

The current local LCD reports ``reported=(2560,540,3)`` and uses
``subpixel_axis=1``, giving physical shape ``(2560, 1620)``.

The direction reported by SDL (height-first or width-first) determines which
axis is correct.  Always verify that ``physical_shape`` from
``LCDService.get_metadata()`` matches the generated mask ``.npy`` files.

## Common Failure Modes

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| Mask displayed on normal monitor | Wrong ``display_index`` | Set ``--lcd-display-index`` to the correct SDL display number |
| ``mask shape does not match LCD physical shape`` | Wrong ``subpixel_axis`` or mask generated for different logical shape | Re-generate masks with correct ``--logical-shape`` and ``--subpixel-axis`` |
| ``mask ...: either array or path must be provided`` | Missing mask ``.npy`` files in ``plans/generated_masks/`` | Run ``make_smoke_masks.py`` before hardware capture |
| ``missing required key 'mask_id'`` | Plan file references mask IDs not present in generated masks | Update plan's ``mask_id`` fields to match generated file names |
| ``TLS is requested but no TLS device is available`` | TLS enabled without ``tls_c1`` installed or ``TLS_C1_SERIAL`` | Install ``tls_c1`` and set ``TLS_C1_SERIAL``, or omit ``--enable-tls`` |
| ``FlyCapture2 SDK headers were not found`` | ``FLYCAPTURE2_SDK_DIR`` not set | Set ``FLYCAPTURE2_SDK_DIR`` to the SDK root |
| ``No SDL display was detected`` | No display connected, or display index out of range | Check ``display_index``; run with ``--lcd-display-index 0`` to try primary monitor |
| Camera capture times out | Camera not connected or stream not started | Verify camera is powered and connected; check USB/FireWire cable |

## Scientific Validity Disclaimer

Hardware smoke tests verify **control and data integrity only**.
They do not validate optical alignment, PSF quality, mask-response
calibration, or any scientific property of the captured frames.

Every current raw capture HDF5 records audit flags equivalent to:

```json
{
  "scientific_calibration_valid": false,
  "optical_alignment_validated":   false,
  "training_ready":                false,
  "raw_capture_schema_version":    3,
  "capture_role":                  "minimal_capture"
}
```

This remains true even when all hardware tests pass.
Later conversion/export steps may introduce training-ready data only when the
system is optically configured and validated.
