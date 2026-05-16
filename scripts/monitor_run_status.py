from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Any


RAW_MONO_ENCODING = "Raw mono"
BAYER_RGGB_ENCODING = "Bayer RGGB -> RGB"
BAYER_BGGR_ENCODING = "Bayer BGGR -> RGB"
BAYER_GRBG_ENCODING = "Bayer GRBG -> RGB"
BAYER_GBRG_ENCODING = "Bayer GBRG -> RGB"
FRAME_PREVIEW_ENCODINGS = (
    RAW_MONO_ENCODING,
    BAYER_RGGB_ENCODING,
    BAYER_BGGR_ENCODING,
    BAYER_GRBG_ENCODING,
    BAYER_GBRG_ENCODING,
)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _ensure_repo_on_path() -> None:
    root = str(_repo_root())
    if root not in sys.path:
        sys.path.insert(0, root)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read-only file monitor for a running capture task"
    )
    parser.add_argument(
        "--status-dir",
        required=True,
        help="run-status directory published by the task",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=0.5,
        help="poll interval in seconds",
    )
    parser.add_argument(
        "--no-gui",
        action="store_true",
        default=False,
        help="use terminal polling instead of tkinter",
    )
    parser.add_argument(
        "--max-log-lines",
        type=int,
        default=30,
        help="maximum recent log lines to display",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        default=False,
        help="render one update and exit",
    )
    return parser


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    return build_arg_parser().parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    _ensure_repo_on_path()

    if args.no_gui:
        return run_terminal(args)

    try:
        return run_tk_gui(args)
    except Exception as exc:
        print(f"Warning: GUI unavailable ({exc}); falling back to --no-gui.", file=sys.stderr)
        return run_terminal(args)


def run_terminal(args: argparse.Namespace) -> int:
    _ensure_repo_on_path()
    from diagnostics.run_status import RunStatusReader, read_lcd_state, read_tls_state, read_mask_preview

    reader = RunStatusReader(Path(args.status_dir))
    try:
        while True:
            text = render_terminal_snapshot(
                args.status_dir, reader, max_log_lines=args.max_log_lines
            )
            if args.once:
                print(text)
                return 0
            print("\033[2J\033[H", end="")
            print(text)
            time.sleep(max(0.05, float(args.poll_interval)))
    except KeyboardInterrupt:
        return 0


def render_terminal_snapshot(
    status_dir: str,
    reader: Any,
    *,
    max_log_lines: int,
) -> str:
    from diagnostics.run_status import read_lcd_state as _read_lcd, read_tls_state as _read_tls, read_mask_preview as _read_mask

    sd = Path(status_dir)
    status = reader.read()
    stats = reader.read_frame_stats()
    logs = reader.tail_log(max_lines=max_log_lines)
    frame = reader.read_frame_preview()
    mask = _read_mask(sd)
    lcd = _read_lcd(sd) or {}
    tls = _read_tls(sd) or {}
    stats = stats or {}

    if status is None and not lcd and not tls:
        lines = [
            "run_id: unavailable",
            "phase: status unavailable",
            "latest frame preview: unavailable",
            "current mask preview: unavailable",
            "",
            "Recent logs:",
            "logs: unavailable",
        ]
        return "\n".join(lines)

    capture = _format_progress(
        status.capture_index if status else None,
        status.n_captures if status else None,
    )
    last_age = _format_update_age(status.last_update_ns if status else None)
    frame_state = "available" if frame is not None else "unavailable"
    mask_state = "available" if mask is not None else "unavailable"

    lines = []

    # task state
    if status is not None:
        lines += [
            f"run_id: {status.run_id}",
            f"plan_id: {_fmt(status.plan_id)}",
            f"phase: {_fmt(status.phase)}",
            f"capture: {capture}",
            f"completed: {_fmt(status.completed)}",
            f"error: {_fmt(status.error)}",
            f"last update: {last_age}",
        ]
    else:
        lines += [
            "run_id: --",
            "plan_id: --",
            "phase: task state unavailable",
        ]

    # LCD state
    if lcd:
        lines += [
            "",
            f"--- LCD ---",
            f"connected: {_fmt(lcd.get('connected'))}",
            f"display: {_fmt(lcd.get('display_index'))}",
            f"physical: {_fmt(lcd.get('physical_shape'))}",
            f"logical: {_fmt(lcd.get('logical_shape'))}",
            f"subpixel_axis: {_fmt(lcd.get('subpixel_axis'))}",
            f"mode: {_fmt(lcd.get('current_mode'))}",
            f"mask: {_fmt(lcd.get('current_mask_id'))}",
        ]

    # TLS state
    if tls:
        lines += [
            "",
            f"--- TLS ---",
            f"connected: {_fmt(tls.get('connected'))}",
            f"current wavelength: {_fmt_nm(tls.get('current_wavelength_nm'))}",
            f"target wavelength: {_fmt_nm(tls.get('target_wavelength_nm'))}",
            f"grating: {_fmt(tls.get('grating'))}",
            f"moving: {_fmt(tls.get('moving'))}",
        ]

    # camera stats
    if stats:
        lines += [""] + ["--- Camera ---"] + [f"{key}: {_fmt(value)}" for key, value in stats.items()]

    lines += [
        "",
        f"latest frame preview: {frame_state}",
        f"current mask preview: {mask_state}",
        "",
        "Recent logs:",
    ]

    if not logs:
        lines.append("logs: unavailable")
    else:
        for row in logs[-max(0, int(max_log_lines)):]:
            lines.append(_format_log_row(row))
    return "\n".join(lines)


def run_tk_gui(args: argparse.Namespace) -> int:
    _ensure_repo_on_path()
    from diagnostics.run_status import RunStatusReader, read_lcd_state, read_tls_state, read_mask_preview

    import tkinter as tk
    from tkinter import ttk

    reader = RunStatusReader(Path(args.status_dir))
    sd = Path(args.status_dir)
    root = tk.Tk()
    root.title("Read-only Run Status Monitor")
    root.geometry("1100x760")

    state_var = tk.StringVar(value="status unavailable")
    frame_var = tk.StringVar(value="latest frame preview: unavailable")
    mask_var = tk.StringVar(value="current mask preview: unavailable")
    frame_encoding_var = tk.StringVar(value=RAW_MONO_ENCODING)

    root.columnconfigure(0, weight=1)
    root.columnconfigure(1, weight=1)
    root.rowconfigure(1, weight=1)
    root.rowconfigure(2, weight=0)

    state_box = ttk.LabelFrame(root, text="Task State", padding=8)
    state_box.grid(row=0, column=0, sticky="nsew", padx=(8, 4), pady=(8, 4))
    ttk.Label(state_box, textvariable=state_var, justify="left", anchor="w").pack(fill="x")

    meta_box = ttk.LabelFrame(root, text="Metadata", padding=8)
    meta_box.grid(row=0, column=1, sticky="nsew", padx=(4, 8), pady=(8, 4))
    meta_box.rowconfigure(0, weight=1)
    meta_box.columnconfigure(0, weight=1)
    meta_text = tk.Text(
        meta_box,
        height=7,
        wrap="none",
        state="disabled",
        font=("Consolas", 9),
    )
    meta_scroll = ttk.Scrollbar(meta_box, orient="vertical", command=meta_text.yview)
    meta_text.configure(yscrollcommand=meta_scroll.set)
    meta_text.grid(row=0, column=0, sticky="nsew")
    meta_scroll.grid(row=0, column=1, sticky="ns")

    frame_box = ttk.LabelFrame(root, text="Latest Frame", padding=8)
    frame_box.grid(row=1, column=0, sticky="nsew", padx=(8, 4), pady=4)
    frame_box.rowconfigure(0, weight=1)
    frame_box.columnconfigure(0, weight=1)
    frame_label = ttk.Label(frame_box, textvariable=frame_var, anchor="center")
    frame_label.grid(row=0, column=0, sticky="nsew")
    frame_encoding_menu = ttk.Combobox(
        frame_box,
        textvariable=frame_encoding_var,
        values=FRAME_PREVIEW_ENCODINGS,
        state="readonly",
        width=20,
    )
    frame_encoding_menu.grid(row=1, column=0, sticky="ew", pady=(8, 0))

    mask_box = ttk.LabelFrame(root, text="Current Mask", padding=8)
    mask_box.grid(row=1, column=1, sticky="nsew", padx=(4, 8), pady=4)
    mask_box.rowconfigure(0, weight=1)
    mask_box.columnconfigure(0, weight=1)
    mask_label = ttk.Label(mask_box, textvariable=mask_var, anchor="center")
    mask_label.grid(row=0, column=0, sticky="nsew")

    logs_box = ttk.LabelFrame(root, text="Recent Logs", padding=8)
    logs_box.grid(row=2, column=0, columnspan=2, sticky="nsew", padx=8, pady=(4, 8))
    logs_box.rowconfigure(0, weight=1)
    logs_box.columnconfigure(0, weight=1)
    logs_text = tk.Text(
        logs_box,
        height=8,
        wrap="none",
        state="disabled",
        font=("Consolas", 9),
    )
    logs_scroll = ttk.Scrollbar(logs_box, orient="vertical", command=logs_text.yview)
    logs_text.configure(yscrollcommand=logs_scroll.set)
    logs_text.grid(row=0, column=0, sticky="nsew")
    logs_scroll.grid(row=0, column=1, sticky="ns")

    photos: dict[str, Any] = {}

    def refresh(*, schedule_next: bool = True) -> None:
        status = reader.read()
        stats = reader.read_frame_stats() or {}
        logs = reader.tail_log(max_lines=args.max_log_lines)
        lcd = read_lcd_state(sd) or {}
        tls = read_tls_state(sd) or {}

        state_var.set(_render_gui_state(status))
        _update_text_widget(
            meta_text,
            _render_gui_metadata(status, stats, lcd, tls),
            preserve_scroll=True,
        )
        _update_image_label(
            label=frame_label,
            text_var=frame_var,
            photos=photos,
            key="frame",
            path=_preview_path(
                sd,
                getattr(status, "latest_frame_preview", None),
            ),
            fallback="latest frame preview: unavailable",
            frame_encoding=frame_encoding_var.get(),
        )
        _update_image_label(
            label=mask_label,
            text_var=mask_var,
            photos=photos,
            key="mask",
            path=_mask_preview_path(sd, lcd),
            fallback="current mask preview: unavailable",
        )
        _update_logs_text(logs_text, logs, max_log_lines=args.max_log_lines)
        if schedule_next and not args.once and root.winfo_exists():
            root.after(
                max(50, int(float(args.poll_interval) * 1000)),
                lambda: refresh(schedule_next=True),
            )

    frame_encoding_menu.bind(
        "<<ComboboxSelected>>",
        lambda _event: refresh(schedule_next=False),
    )
    refresh()
    if args.once:
        root.update_idletasks()
        root.destroy()
        return 0
    root.mainloop()
    return 0


def _mask_preview_path(status_dir: Path, lcd: dict[str, Any]) -> Path | None:
    if "mask_preview" in lcd:
        preview_rel = lcd["mask_preview"]
        if preview_rel is not None:
            return _preview_path(status_dir, str(preview_rel))
        return None
    candidates = sorted(status_dir.glob("current_mask_preview.*"))
    return candidates[0] if candidates else None


# ---------------------------------------------------------------------------
# display helpers
# ---------------------------------------------------------------------------


def _update_logs_text(widget: Any, logs: list[dict[str, Any]], *, max_log_lines: int) -> None:
    if logs:
        text = "\n".join(
            _format_log_row(row)
            for row in logs[-max(0, int(max_log_lines)):]
        )
    else:
        text = "logs: unavailable"
    _update_text_widget(
        widget, text,
        preserve_scroll=True,
        follow_bottom_if_already_at_bottom=True,
    )


def _update_text_widget(
    widget: Any,
    text: str,
    *,
    preserve_scroll: bool = True,
    follow_bottom_if_already_at_bottom: bool = False,
    bottom_threshold: float = 0.98,
) -> None:
    old_text = getattr(widget, "_optic_last_text", None)
    if old_text == text:
        return

    try:
        first, last = widget.yview()
    except Exception:
        first, last = 0.0, 1.0

    was_at_bottom = last >= bottom_threshold

    widget.configure(state="normal")
    widget.delete("1.0", "end")
    widget.insert("1.0", text)
    widget.configure(state="disabled")
    object.__setattr__(widget, "_optic_last_text", text)

    if follow_bottom_if_already_at_bottom and was_at_bottom:
        widget.see("end")
    elif preserve_scroll:
        try:
            widget.yview_moveto(first)
        except Exception:
            pass


def _render_gui_state(status: Any | None) -> str:
    if status is None:
        return "run_id: unavailable\nphase: status unavailable"
    return "\n".join(
        [
            f"run_id: {status.run_id}",
            f"plan_id: {_fmt(status.plan_id)}",
            f"phase: {_fmt(status.phase)}",
            f"capture: {_format_progress(status.capture_index, status.n_captures)}",
            f"completed: {_fmt(status.completed)}",
            f"error: {_fmt(status.error)}",
            f"last update: {_format_update_age(status.last_update_ns)}",
        ]
    )


def _render_gui_metadata(
    status: Any | None,
    stats: dict[str, Any],
    lcd: dict[str, Any],
    tls: dict[str, Any],
) -> str:
    lines: list[str] = []

    if lcd:
        lines += [
            "--- LCD ---",
            f"connected: {_fmt(lcd.get('connected'))}",
            f"display: {_fmt(lcd.get('display_index'))}",
            f"physical: {_fmt(lcd.get('physical_shape'))}",
            f"logical: {_fmt(lcd.get('logical_shape'))}",
            f"subpixel axis: {_fmt(lcd.get('subpixel_axis'))}",
            f"mode: {_fmt(lcd.get('current_mode'))}",
            f"mask: {_fmt(lcd.get('current_mask_id'))}",
        ]
    else:
        lines += ["LCD: unavailable"]

    if tls:
        lines += [
            "",
            "--- TLS ---",
            f"connected: {_fmt(tls.get('connected'))}",
            f"current wavelength: {_fmt_nm(tls.get('current_wavelength_nm'))}",
            f"target wavelength: {_fmt_nm(tls.get('target_wavelength_nm'))}",
            f"grating: {_fmt(tls.get('grating'))}",
            f"moving: {_fmt(tls.get('moving'))}",
        ]
    else:
        lines += ["", "TLS: unavailable"]

    if stats:
        lines += (
            [""] + ["--- Camera ---"] + [f"{key}: {_fmt(value)}" for key, value in stats.items()]
        )

    return "\n".join(lines)


def _update_image_label(
    *,
    label: Any,
    text_var: Any,
    photos: dict[str, Any],
    key: str,
    path: Path | None,
    fallback: str,
    frame_encoding: str | None = None,
) -> None:
    if path is None or not path.exists():
        label.configure(image="")
        photos.pop(key, None)
        text_var.set(fallback)
        return
    try:
        from PIL import ImageTk

        image = _load_preview_image(path, frame_encoding=frame_encoding)
        max_w, max_h = _image_label_target_size(label)
        fit_w, fit_h = _fit_size(image.width, image.height, max_w, max_h)
        if (fit_w, fit_h) != (image.width, image.height):
            from PIL import Image
            resampling = getattr(Image, "Resampling", Image).LANCZOS
            image = image.resize((fit_w, fit_h), resampling)
        photo = ImageTk.PhotoImage(image)
    except Exception:
        label.configure(image="")
        photos.pop(key, None)
        text_var.set(fallback)
        return
    photos[key] = photo
    text_var.set("")
    label.configure(image=photo)


def _image_label_target_size(label: Any) -> tuple[int, int]:
    try:
        label.update_idletasks()
    except Exception:
        pass
    parent = getattr(label, "master", None)
    if parent is not None:
        try:
            parent.update_idletasks()
        except Exception:
            pass
        pw = int(parent.winfo_width()) - 16
        ph = int(parent.winfo_height()) - 16
        if pw > 16 and ph > 16:
            return pw, ph

    width = max(1, int(label.winfo_width()))
    height = max(1, int(label.winfo_height()))
    if width <= 1:
        width = 512
    if height <= 1:
        height = 512
    return width, height


def _load_preview_image(path: Path, *, frame_encoding: str | None = None) -> Any:
    from PIL import Image

    if path.suffix.lower() == ".npy":
        import numpy as np

        array = np.load(str(path))
        return _array_to_pil_image(array, frame_encoding=frame_encoding)
    image = Image.open(path)
    return image.convert("RGB") if image.mode not in {"L", "RGB"} else image


def _array_to_pil_image(array: Any, *, frame_encoding: str | None = None) -> Any:
    from PIL import Image

    import numpy as np

    arr = np.asarray(array)
    if arr.ndim == 2 and frame_encoding in _BAYER_CV2_CODE_NAMES:
        try:
            import cv2

            gray = _as_uint8_display(arr)
            rgb = cv2.cvtColor(
                gray,
                getattr(cv2, _BAYER_CV2_CODE_NAMES[str(frame_encoding)]),
            )
            return Image.fromarray(rgb, mode="RGB")
        except Exception:
            return Image.fromarray(_as_uint8_display(arr), mode="L")
    if arr.ndim == 2:
        return Image.fromarray(_as_uint8_display(arr), mode="L")
    if arr.ndim == 3 and arr.shape[2] in {3, 4}:
        arr8 = _as_uint8_display(arr)
        mode = "RGBA" if arr.shape[2] == 4 else "RGB"
        return Image.fromarray(arr8, mode=mode)
    squeezed = np.squeeze(arr)
    if squeezed.ndim == 2:
        return Image.fromarray(_as_uint8_display(squeezed), mode="L")
    raise ValueError(f"unsupported preview array shape: {arr.shape}")


_BAYER_CV2_CODE_NAMES = {
    BAYER_RGGB_ENCODING: "COLOR_BayerRGGB2RGB",
    BAYER_BGGR_ENCODING: "COLOR_BayerBGGR2RGB",
    BAYER_GRBG_ENCODING: "COLOR_BayerGRBG2RGB",
    BAYER_GBRG_ENCODING: "COLOR_BayerGBRG2RGB",
}


def _as_uint8_display(array: Any) -> Any:
    import numpy as np

    arr = np.asarray(array)
    if arr.dtype == np.uint8:
        return arr
    if arr.size == 0:
        return arr.astype(np.uint8)
    finite = arr[np.isfinite(arr)] if np.issubdtype(arr.dtype, np.floating) else arr
    if finite.size == 0:
        return np.zeros(arr.shape, dtype=np.uint8)
    mn = float(np.min(finite))
    mx = float(np.max(finite))
    if mx <= mn:
        return np.zeros(arr.shape, dtype=np.uint8)
    scaled = (arr.astype(np.float64) - mn) * (255.0 / (mx - mn))
    return np.clip(scaled, 0, 255).astype(np.uint8)


def _fit_size(width: int, height: int, max_width: int, max_height: int) -> tuple[int, int]:
    width = max(1, int(width))
    height = max(1, int(height))
    max_width = max(1, int(max_width))
    max_height = max(1, int(max_height))
    scale = min(max_width / width, max_height / height, 1.0)
    return max(1, int(width * scale)), max(1, int(height * scale))


def _preview_path(status_dir: Path, value: str | None) -> Path | None:
    if not value:
        return None
    path = Path(value)
    return path if path.is_absolute() else status_dir / path


def _format_progress(index: int | None, total: int | None) -> str:
    if index is None and total is None:
        return "--"
    return f"{_fmt(index)} / {_fmt(total)}"


def _format_update_age(last_update_ns: int | None) -> str:
    if last_update_ns is None:
        return "--"
    age_s = max(0.0, (time.monotonic_ns() - int(last_update_ns)) / 1_000_000_000.0)
    return f"{age_s:.1f} s ago"


def _format_log_row(row: dict[str, Any]) -> str:
    level = str(row.get("level", "--"))
    message = str(row.get("message", ""))
    source = row.get("source")
    extras = [
        f"{key}={value}"
        for key, value in row.items()
        if key not in {"time_ns", "level", "source", "message"}
    ]
    prefix = f"[{level}]"
    if source:
        prefix += f" {source}:"
    suffix = f" {' '.join(extras)}" if extras else ""
    return f"{prefix} {message}{suffix}"


def _fmt(value: Any) -> str:
    if value is None:
        return "--"
    return str(value)


def _fmt_nm(value: Any) -> str:
    return "--" if value is None else f"{float(value):g} nm"


def _fmt_us(value: Any) -> str:
    return "--" if value is None else f"{float(value):g} us"


def _fmt_db(value: Any) -> str:
    return "--" if value is None else f"{float(value):g} dB"


if __name__ == "__main__":
    raise SystemExit(main())
