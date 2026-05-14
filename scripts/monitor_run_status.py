from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Any


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
    from diagnostics.run_status import RunStatusReader

    reader = RunStatusReader(Path(args.status_dir))
    try:
        while True:
            text = render_terminal_snapshot(reader, max_log_lines=args.max_log_lines)
            if args.once:
                print(text)
                return 0
            print("\033[2J\033[H", end="")
            print(text)
            time.sleep(max(0.05, float(args.poll_interval)))
    except KeyboardInterrupt:
        return 0


def render_terminal_snapshot(reader: Any, *, max_log_lines: int) -> str:
    status = reader.read()
    stats = reader.read_frame_stats()
    logs = reader.tail_log(max_lines=max_log_lines)
    frame = reader.read_frame_preview()
    mask = reader.read_mask_preview()

    if status is None:
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

    capture = _format_progress(status.capture_index, status.n_captures)
    last_age = _format_update_age(status.last_update_ns)
    frame_state = "available" if frame is not None else "unavailable"
    mask_state = "available" if mask is not None else "unavailable"
    stats = stats or {}

    lines = [
        f"run_id: {status.run_id}",
        f"plan_id: {_fmt(status.plan_id)}",
        f"phase: {_fmt(status.phase)}",
        f"capture: {capture}",
        f"mask: {_fmt(status.current_mask_id)}",
        f"wavelength: {_fmt_nm(status.current_wavelength_nm)}",
        f"target wavelength: {_fmt_nm(status.target_wavelength_nm)}",
        f"TLS: grating={_fmt(status.tls_grating)} moving={_fmt(status.tls_moving)}",
        (
            "LCD: "
            f"display={_fmt(status.lcd_display_index)} "
            f"physical={_fmt(status.lcd_physical_shape)} "
            f"logical={_fmt(status.lcd_logical_shape)} "
            f"subpixel_axis={_fmt(status.lcd_subpixel_axis)}"
        ),
        (
            "camera: "
            f"exposure={_fmt_us(status.camera_exposure_us)} "
            f"gain={_fmt_db(status.camera_gain_db)} "
            f"roi={_fmt(status.camera_roi)} "
            f"seq={_fmt(status.camera_frame_seq)} "
            f"max={_first_present(stats.get('peak_pixel_burst'), status.camera_max_pixel)} "
            f"p99.9={_fmt(stats.get('p99_9_avg'))} "
            f"margin={_fmt(stats.get('peak_margin_to_full_scale'))}"
        ),
        f"camera dtype full scale: {_first_present(stats.get('frame_dtype_full_scale'), status.camera_frame_dtype_full_scale)}",
        f"latest frame preview: {frame_state}",
        f"current mask preview: {mask_state}",
        f"last update: {last_age}",
        f"completed: {_fmt(status.completed)}",
        f"error: {_fmt(status.error)}",
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
    from diagnostics.run_status import RunStatusReader

    import tkinter as tk
    from tkinter import ttk

    reader = RunStatusReader(Path(args.status_dir))
    root = tk.Tk()
    root.title("Read-only Run Status Monitor")
    root.geometry("1100x760")

    state_var = tk.StringVar(value="status unavailable")
    frame_var = tk.StringVar(value="latest frame preview: unavailable")
    mask_var = tk.StringVar(value="current mask preview: unavailable")

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
    frame_label = ttk.Label(frame_box, textvariable=frame_var, anchor="center")
    frame_label.pack(fill="both", expand=True)

    mask_box = ttk.LabelFrame(root, text="Current Mask", padding=8)
    mask_box.grid(row=1, column=1, sticky="nsew", padx=(4, 8), pady=4)
    mask_label = ttk.Label(mask_box, textvariable=mask_var, anchor="center")
    mask_label.pack(fill="both", expand=True)

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

    def refresh() -> None:
        status = reader.read()
        stats = reader.read_frame_stats() or {}
        logs = reader.tail_log(max_lines=args.max_log_lines)
        state_var.set(_render_gui_state(status))
        _update_text_widget(meta_text, _render_gui_metadata(status, stats))
        _update_image_label(
            label=frame_label,
            text_var=frame_var,
            photos=photos,
            key="frame",
            path=_preview_path(Path(args.status_dir), getattr(status, "latest_frame_preview", None)),
            fallback="latest frame preview: unavailable",
        )
        _update_image_label(
            label=mask_label,
            text_var=mask_var,
            photos=photos,
            key="mask",
            path=_preview_path(Path(args.status_dir), getattr(status, "current_mask_preview", None)),
            fallback="current mask preview: unavailable",
        )
        _update_logs_text(logs_text, logs, max_log_lines=args.max_log_lines)
        if not args.once and root.winfo_exists():
            root.after(max(50, int(float(args.poll_interval) * 1000)), refresh)

    refresh()
    if args.once:
        root.update_idletasks()
        root.destroy()
        return 0
    root.mainloop()
    return 0


def _update_logs_text(widget: Any, logs: list[dict[str, Any]], *, max_log_lines: int) -> None:
    if logs:
        text = "\n".join(
            _format_log_row(row)
            for row in logs[-max(0, int(max_log_lines)):]
        )
    else:
        text = "logs: unavailable"
    _update_text_widget(widget, text, scroll_to_end=True)


def _update_text_widget(widget: Any, text: str, *, scroll_to_end: bool = False) -> None:
    widget.configure(state="normal")
    widget.delete("1.0", "end")
    widget.insert("1.0", text)
    if scroll_to_end:
        widget.see("end")
    widget.configure(state="disabled")


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


def _render_gui_metadata(status: Any | None, stats: dict[str, Any]) -> str:
    if status is None:
        return "metadata unavailable"
    return "\n".join(
        [
            f"mask: {_fmt(status.current_mask_id)}",
            f"LCD display: {_fmt(status.lcd_display_index)}",
            f"LCD physical: {_fmt(status.lcd_physical_shape)}",
            f"LCD logical: {_fmt(status.lcd_logical_shape)}",
            f"LCD subpixel axis: {_fmt(status.lcd_subpixel_axis)}",
            "",
            f"wavelength: {_fmt_nm(status.current_wavelength_nm)}",
            f"target wavelength: {_fmt_nm(status.target_wavelength_nm)}",
            f"TLS grating: {_fmt(status.tls_grating)}",
            f"TLS moving: {_fmt(status.tls_moving)}",
            "",
            f"camera exposure: {_fmt_us(status.camera_exposure_us)}",
            f"camera gain: {_fmt_db(status.camera_gain_db)}",
            f"camera ROI: {_fmt(status.camera_roi)}",
            f"camera seq: {_fmt(status.camera_frame_seq)}",
            f"camera max: {_first_present(stats.get('peak_pixel_burst'), status.camera_max_pixel)}",
            f"p99.9: {_fmt(stats.get('p99_9_avg'))}",
            f"peak margin: {_fmt(stats.get('peak_margin_to_full_scale'))}",
            f"dtype full scale: {_first_present(stats.get('frame_dtype_full_scale'), status.camera_frame_dtype_full_scale)}",
        ]
    )


def _update_image_label(
    *,
    label: Any,
    text_var: Any,
    photos: dict[str, Any],
    key: str,
    path: Path | None,
    fallback: str,
) -> None:
    if path is None or path.suffix.lower() != ".png" or not path.exists():
        label.configure(image="")
        photos.pop(key, None)
        text_var.set(fallback)
        return
    try:
        import tkinter as tk

        photo = tk.PhotoImage(file=str(path))
    except Exception:
        label.configure(image="")
        photos.pop(key, None)
        text_var.set(fallback)
        return
    photos[key] = photo
    text_var.set("")
    label.configure(image=photo)


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


def _first_present(primary: Any, fallback: Any) -> str:
    return _fmt(primary if primary is not None else fallback)


if __name__ == "__main__":
    raise SystemExit(main())
