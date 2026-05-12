from __future__ import annotations

import os
import queue
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import ttk
from typing import Any

from diagnostics.run_status import RunStatusReader

from .camera_monitor_panel import CameraMonitorPanel
from .lcd_monitor_panel import LCDMonitorPanel
from .tls_monitor_panel import TLSMonitorPanel


class TaskMonitorPanel(ttk.LabelFrame):
    def __init__(self, master):
        super().__init__(master, text="Task Monitor", padding=8)
        self.run_id_var = tk.StringVar(value="Run id: --")
        self.plan_id_var = tk.StringVar(value="Plan id: --")
        self.progress_var = tk.StringVar(value="Capture: --")
        self.phase_var = tk.StringVar(value="Phase: status unavailable")
        self.completed_var = tk.StringVar(value="Completed: --")
        self.error_var = tk.StringVar(value="Error: --")

        for var in (
            self.run_id_var,
            self.plan_id_var,
            self.progress_var,
            self.phase_var,
            self.completed_var,
            self.error_var,
        ):
            ttk.Label(self, textvariable=var, anchor="w").pack(side="left", padx=8)

    def update_status(self, status) -> None:
        if status is None:
            self.phase_var.set("Phase: status unavailable")
            self.completed_var.set("Completed: --")
            self.error_var.set("Error: --")
            return
        self.run_id_var.set(f"Run id: {status.run_id}")
        self.plan_id_var.set(f"Plan id: {status.plan_id or '--'}")
        if status.capture_index is None or status.n_captures is None:
            progress = "--"
        else:
            progress = f"{status.capture_index} / {status.n_captures}"
        self.progress_var.set(f"Capture: {progress}")
        self.phase_var.set(f"Phase: {status.phase or '--'}")
        self.completed_var.set(f"Completed: {_fmt_bool(status.completed)}")
        self.error_var.set(f"Error: {status.error or '--'}")


class LogStatusPanel(ttk.LabelFrame):
    def __init__(self, master):
        super().__init__(master, text="Monitor Messages", padding=8)
        self.message_var = tk.StringVar(value="Ready")
        ttk.Label(self, textvariable=self.message_var, anchor="w").pack(fill="x")

    def show(self, message: str) -> None:
        self.message_var.set(message)


class MonitorWindow:
    def __init__(
        self,
        *,
        status_dir: Path,
        frame_timeout_ms: int = 500,
        poll_ms: int = 500,
        bayer_pattern: str | None = None,
        no_camera: bool = False,
        title: str = "Read-only Experiment Monitor",
    ):
        self.status_dir = Path(status_dir)
        self.reader = RunStatusReader(self.status_dir)
        self.frame_timeout_ms = int(frame_timeout_ms)
        self.poll_ms = int(poll_ms)
        self.bayer_pattern = bayer_pattern
        self.no_camera = bool(no_camera)

        self.root = tk.Tk()
        self.root.title(title)
        self._frame_queue: queue.Queue[tuple[str, Any]] = queue.Queue()
        self._stop_event = threading.Event()
        self._camera_client = None
        self._camera_thread: threading.Thread | None = None
        self._preview_paused = tk.BooleanVar(value=False)

        self._build_layout()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(50, self._poll_frames)
        self.root.after(10, self.refresh_status)

        if self.no_camera:
            self.camera_panel.show_message("Camera preview disabled")
        else:
            self._start_camera_worker()

    def _build_layout(self) -> None:
        self.root.columnconfigure(0, weight=1)
        self.root.columnconfigure(1, weight=2)
        self.root.rowconfigure(1, weight=1)

        self.task_panel = TaskMonitorPanel(self.root)
        self.task_panel.grid(row=0, column=0, columnspan=2, sticky="ew", padx=8, pady=(8, 4))

        self.lcd_panel = LCDMonitorPanel(self.root)
        self.lcd_panel.grid(row=1, column=0, sticky="nsew", padx=(8, 4), pady=4)

        self.camera_panel = CameraMonitorPanel(self.root)
        self.camera_panel.grid(row=1, column=1, sticky="nsew", padx=(4, 8), pady=4)

        self.tls_panel = TLSMonitorPanel(self.root)
        self.tls_panel.grid(row=2, column=0, sticky="ew", padx=(8, 4), pady=(4, 8))

        bottom_right = ttk.Frame(self.root)
        bottom_right.grid(row=2, column=1, sticky="ew", padx=(4, 8), pady=(4, 8))
        bottom_right.columnconfigure(0, weight=1)

        self.log_panel = LogStatusPanel(bottom_right)
        self.log_panel.grid(row=0, column=0, sticky="ew")

        button_row = ttk.Frame(bottom_right)
        button_row.grid(row=1, column=0, sticky="ew", pady=(6, 0))
        ttk.Button(button_row, text="Refresh status", command=self.refresh_status).pack(side="left", padx=3)
        ttk.Button(button_row, text="Open status directory", command=self._open_status_dir).pack(side="left", padx=3)
        ttk.Checkbutton(
            button_row,
            text="Pause preview display",
            variable=self._preview_paused,
        ).pack(side="left", padx=3)

    def refresh_status(self) -> None:
        status = self.reader.read()
        mask_preview = self.reader.read_mask_preview() if status is not None else None
        self.task_panel.update_status(status)
        self.lcd_panel.update_status(status)
        self.lcd_panel.update_preview(mask_preview)
        self.tls_panel.update_status(status)
        self.camera_panel.update_from_status(status)

        if status is None:
            self.log_panel.show(f"Status unavailable: {self.status_dir}")
        else:
            self.log_panel.show(f"Status read from {self.status_dir}")

        if self.root.winfo_exists():
            self.root.after(self.poll_ms, self.refresh_status)

    def _start_camera_worker(self) -> None:
        self._camera_thread = threading.Thread(target=self._camera_loop, daemon=True)
        self._camera_thread.start()

    def _camera_loop(self) -> None:
        try:
            from devices.frame_stream import FrameStreamClient

            self._camera_client = FrameStreamClient(
                recv_timeout_ms=self.frame_timeout_ms,
                bayer_pattern=self.bayer_pattern,
            )
            while not self._stop_event.is_set():
                try:
                    packet = self._camera_client.recv_frame()
                    self._frame_queue.put(("frame", packet))
                except Exception as exc:
                    self._frame_queue.put(("camera_status", str(exc)))
                    time.sleep(max(0.1, self.frame_timeout_ms / 1000.0))
        except Exception as exc:
            self._frame_queue.put(("camera_status", f"Camera stream unavailable: {exc}"))

    def _poll_frames(self) -> None:
        while True:
            try:
                kind, payload = self._frame_queue.get_nowait()
            except queue.Empty:
                break
            if kind == "frame" and not self._preview_paused.get():
                self.camera_panel.update_from_packet(payload)
            elif kind == "camera_status":
                self.camera_panel.show_message(str(payload))

        if self.root.winfo_exists():
            self.root.after(50, self._poll_frames)

    def _open_status_dir(self) -> None:
        try:
            if os.name == "nt":
                os.startfile(str(self.status_dir))
            else:
                self.log_panel.show(f"Status directory: {self.status_dir}")
        except Exception as exc:
            self.log_panel.show(f"Could not open status directory: {exc}")

    def _on_close(self) -> None:
        self._stop_event.set()
        if self._camera_client is not None:
            try:
                self._camera_client.close()
            except Exception:
                pass
        if self.root.winfo_exists():
            self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


def _fmt_bool(value: bool | None) -> str:
    if value is None:
        return "--"
    return "yes" if value else "no"


def monitor_allowed_button_labels() -> set[str]:
    return {"Refresh status", "Open status directory", "Pause preview display"}
