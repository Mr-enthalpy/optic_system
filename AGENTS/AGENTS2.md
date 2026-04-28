# AGENTS2.md

## Stage 2: LCD Control Integration

This document defines the next refactor stage after the camera preview prototype has been completed.

The previous stage established the minimal hardware-backed GUI prototype:

- camera sidecar connection;
- live camera frame streaming;
- GUI live preview;
- camera parameter display;
- camera parameter adjustment through the control layer.

The next stage adds LCD control while preserving the same architectural principle:

> GUI is a visualization and interaction frontend, not the control plane.

LCD control must be integrated through the device and control layers, not directly inside GUI callbacks.

---

## Current stage goal

The goal of this stage is:

> Add minimal LCD device control so that the system starts with the LCD in an all-transmissive state, supports displaying mono LCD masks, and exposes enough debug visibility to verify the physical LCD mapping.

This stage is still not the full calibration or mask-design system.

Do not implement full aperture search, calibration sequence, wavelength sweep, dataset export, or GenerMask optimization in this stage unless explicitly requested.

---

## Critical LCD representation rule

The LCD has a special representation mismatch.

The display API reports an RGB image buffer:

```text
[H, W, 3]
````

However, the physical LCD should be treated as a mono subpixel array:

```text
[H, 3W]
```

The RGB channels are not semantic color channels.

They represent three adjacent mono subpixel columns.

Therefore, the canonical physical mask format for this project is:

```text
mono physical mask: [H, 3W]
```

The display buffer format is only the final packed representation:

```text
display RGB buffer: [H, W, 3]
```

The mapping is:

```text
rgb[y, x, c] = mono[y, 3*x + c]
```

All mask generation, debug patterns, aperture masks, future GenerMask outputs, and physical LCD reasoning must use `[H, 3W]`.

Only the LCD device service is allowed to pack `[H, 3W]` into `[H, W, 3]`.

---

## Hard constraints

### 1. Do not modify `old/`

The `old/` directory remains reference-only.

Do not modify files under `old/`.

### 2. Preserve the current architecture

The current layer separation must be preserved:

```text
devices/
capture/
control/
gui/
app/
```

Do not move LCD logic into GUI.

Do not bypass the control layer.

### 3. LCD must default to all-transmissive state

On startup, after the LCD service is initialized, the LCD should be set to an all-transmissive mask by default.

The all-transmissive code must be configurable.

Do not hard-code the assumption that `255` is always transmissive in all physical configurations.

Use a centralized configuration parameter such as:

```python
transmissive_code = 255
opaque_code = 0
```

If the physical device later proves inverted, this should be changed in one place only.

### 4. Treat RGB as packing, not color

Do not write LCD logic that interprets RGB as real color.

Do not generate masks in `[H, W, 3]` unless the code is specifically inside the final packing/display boundary.

The canonical mask should be `[H, 3W]`.

### 5. Do not introduce full experiment workflow yet

This stage should only add minimal LCD control:

* initialize LCD;
* show all-transmissive mask;
* show all-opaque mask if useful;
* show a mono physical mask;
* show debug patterns;
* expose LCD state in GUI;
* allow minimal LCD debug commands.

Do not implement:

* full mask sequence execution;
* aperture search;
* wavelength sweep;
* calibration task runner;
* GenerMask optimization;
* dataset export;
* synchronized camera-LCD acquisition.

---

## Recommended new modules

### `devices/lcd_service.py`

This is the main new device abstraction.

Responsibilities:

* wrap the underlying LCD display backend;
* store reported display metadata;
* expose physical mono shape `[H, 3W]`;
* convert mono physical masks `[H, 3W]` to display RGB buffers `[H, W, 3]`;
* show all-transmissive mask;
* show all-opaque mask;
* show arbitrary mono masks;
* expose LCD metadata.

Expected interface:

```python
class LCDService:
    def get_metadata(self) -> dict: ...
    def mono_to_rgb(self, mask: np.ndarray) -> np.ndarray: ...
    def rgb_to_mono(self, rgb: np.ndarray) -> np.ndarray: ...
    def show_rgb_buffer(self, rgb: np.ndarray) -> None: ...
    def show_mono_mask(self, mask: np.ndarray, mask_id: str | None = None) -> None: ...
    def make_all_transmissive_mask(self) -> np.ndarray: ...
    def make_all_opaque_mask(self) -> np.ndarray: ...
    def show_all_transmissive(self) -> None: ...
    def show_all_opaque(self) -> None: ...
    def close(self) -> None: ...
```

This service is the only place where `[H, 3W] -> [H, W, 3]` packing should happen.

---

### `devices/lcd_backend.py` or equivalent

If needed, create a small backend adapter that wraps the old LCD display mechanism.

The backend should expose a minimal interface:

```python
class LCDBackend:
    def show(self, rgb: np.ndarray) -> None: ...
    def close(self) -> None: ...
```

Do not let this backend define the physical mask semantics.

The physical mask semantics belong to `LCDService`.

---

### `devices/lcd_debug_patterns.py`

Optional but recommended for hardware debugging.

This module should generate mono physical masks in `[H, 3W]`, not RGB buffers.

Useful debug patterns:

* all-transmissive;
* all-opaque;
* vertical subpixel bars;
* horizontal bars;
* center cross;
* four-corner markers;
* alternating subpixel stripe pattern;
* coarse checkerboard with safe minimum feature size.

All functions should make the physical width explicit.

Example convention:

```python
def make_center_cross(h: int, w_phys: int, ...) -> np.ndarray:
    ...
```

Here `w_phys = 3 * reported_width`.

---

## Control layer updates

### `control/commands.py`

Add LCD commands.

Recommended minimal commands:

```python
SetLCDAllTransmissive
SetLCDAllOpaque
ShowLCDMonoMask
ShowLCDDebugPattern
```

Command semantics:

* commands describe user or worker intent;
* commands must not contain GUI logic;
* commands must not directly manipulate the backend.

Example:

```python
@dataclass(frozen=True)
class SetLCDAllTransmissive(Command):
    pass

@dataclass(frozen=True)
class ShowLCDMonoMask(Command):
    mask: np.ndarray
    mask_id: str | None = None
```

---

### `control/events.py`

Add LCD events.

Recommended minimal events:

```python
LCDAllTransmissiveShown
LCDAllOpaqueShown
LCDMaskShown
LCDDebugPatternShown
LCDStatusChanged
LCDError
```

Events should report physical shape and current mask identity where relevant.

Example:

```python
@dataclass(frozen=True)
class LCDMaskShown(Event):
    mask_id: str | None
    physical_shape: tuple[int, int]
```

---

### `control/state.py`

Extend session state with LCD fields.

Recommended fields:

```python
lcd_connected: bool = False
lcd_reported_shape: tuple[int, int, int] | None = None
lcd_physical_shape: tuple[int, int] | None = None
lcd_current_mode: str | None = None
lcd_current_mask_id: str | None = None
lcd_last_error: str | None = None
```

The state must clearly distinguish:

* reported display shape `[H, W, 3]`;
* physical mono shape `[H, 3W]`.

---

### `control/session_controller.py`

The controller should receive LCD commands and call `LCDService`.

The GUI must not call `LCDService` directly.

Recommended methods:

```python
def show_lcd_all_transmissive(self) -> None: ...
def show_lcd_all_opaque(self) -> None: ...
def show_lcd_mono_mask(self, mask: np.ndarray, mask_id: str | None = None) -> None: ...
def show_lcd_debug_pattern(self, pattern_name: str) -> None: ...
```

On startup, the controller or application entry should set:

```python
lcd_service.show_all_transmissive()
```

and update state accordingly.

---

## GUI updates

The GUI may be extended, but only minimally.

Recommended options:

### Option A: Add `gui/lcd_panel.py`

A small LCD panel may show:

* LCD connected status;
* reported display shape `[H, W, 3]`;
* physical mono shape `[H, 3W]`;
* current LCD mode;
* current mask id;
* buttons:

  * `LCD FULL TRANSPARENT`;
  * `LCD FULL OPAQUE`;
  * `LCD CENTER CROSS`;
  * `LCD VERTICAL BARS`.

The buttons must send commands to the controller.

They must not call LCD service directly.

### Option B: Reuse `status_panel.py`

If keeping the GUI minimal, show LCD metadata and mode in the existing status panel first.

Do not overbuild the LCD GUI in this stage.

---

## Application entry updates

### `app/main_gui.py`

The application startup should now assemble:

* `CameraServiceClient`;
* `FrameStreamClient`;
* `PreviewWorker`;
* `LCDService`;
* `SessionController`;
* `MainWindow`.

Startup order should be:

1. connect or start camera sidecar;
2. open camera;
3. start camera stream;
4. initialize LCD backend/service;
5. set LCD to all-transmissive state;
6. initialize controller;
7. initialize GUI;
8. start preview worker;
9. enter GUI main loop.

Shutdown order should be:

1. stop preview worker;
2. stop camera stream;
3. close camera;
4. close LCD service;
5. release sidecar if owned;
6. close GUI.

---

## Hardware-aware debugging requirements

LCD debugging requires human visual observation.

A successful software call does not guarantee that the physical LCD displayed the intended pattern.

This stage should make physical validation easier by providing standard debug patterns and metadata visibility.

For each LCD debug pattern, the system should ideally expose:

* pattern name;
* physical shape `[H, 3W]`;
* packed RGB shape `[H, W, 3]`;
* transmissive / opaque code;
* subpixel order;
* current mask id.

Human observation should answer:

```text
Pattern visible: yes / no
All-transmissive appears correct: yes / no / unknown
All-opaque appears correct: yes / no / unknown
Vertical bars orientation correct: yes / no
Horizontal bars orientation correct: yes / no
Center cross centered: yes / no
Pattern stretched or cropped: yes / no
Subpixel mapping plausible: yes / no / unknown
```

This is not optional hardware polish. It is necessary because the LCD has a nontrivial RGB-to-mono-subpixel representation.

---

## Success criteria for Stage 2

This stage is successful if:

1. The previous camera live preview GUI still works.
2. Camera parameters can still be displayed and adjusted.
3. LCD service initializes without being controlled directly by GUI.
4. LCD defaults to all-transmissive state on startup.
5. LCD metadata reports both:

   * `[H, W, 3]` display shape;
   * `[H, 3W]` physical mono shape.
6. A mono physical mask `[H, 3W]` can be displayed through the LCD service.
7. At least one debug pattern can be shown.
8. Human visual inspection can confirm whether the displayed debug pattern is plausible.
9. GUI sends LCD actions as commands through the controller.
10. The system shuts down safely.

---

## What not to do in Stage 2

Do not implement the following yet:

* full aperture search;
* full LCD-camera synchronized acquisition;
* calibration dataset capture;
* wavelength sweep;
* GenerMask optimization;
* mask sequence scheduler;
* automatic reconstruction or forward-model training;
* complex experiment workflow engine.

The current task is LCD control integration only.

---

## Long-term direction

The LCD service introduced in this stage will become the physical display endpoint for future modules:

* aperture search;
* GenerMask families;
* calibration mask display;
* mask sequence experiments;
* learnable mask design;
* LCD-camera closed-loop experiments.

For that reason, the physical mask convention `[H, 3W]` must remain stable.

Future modules should produce physical mono masks.

They should not produce RGB display buffers directly.

```
```
