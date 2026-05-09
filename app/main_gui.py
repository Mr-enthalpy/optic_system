from __future__ import annotations

import argparse
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from capture.preview_worker import PreviewWorker
from control.events import StatusMessage
from control.session_controller import SessionController
from devices.camera_service import CameraServiceClient
from devices.frame_stream import FrameStreamClient
from devices.lcd_service import LCDService
from gui.main_window import MainWindow


def build_controller(args: argparse.Namespace) -> SessionController:
    camera_service = CameraServiceClient(
        auto_ensure=not args.no_auto_sidecar,
        timeout_ms=args.rpc_timeout_ms,
    )
    frame_stream = FrameStreamClient(recv_timeout_ms=args.frame_timeout_ms)
    preview_worker = PreviewWorker(frame_stream)
    lcd_service = None
    if not args.disable_lcd:
        lcd_service = LCDService(
            display_index=args.lcd_display_index,
            transmissive_code=args.lcd_transmissive_code,
            opaque_code=args.lcd_opaque_code,
        )

    return SessionController(
        camera_service=camera_service,
        preview_worker=preview_worker,
        lcd_service=lcd_service,
        camera_index=args.camera_index,
        context_type=args.context_type,
        preconfigure=False,
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Launch the hardware-backed camera preview GUI with minimal LCD control"
    )
    parser.add_argument("--camera-index", type=int, default=0)
    parser.add_argument("--context-type", default="IIDC")
    parser.add_argument(
        "--no-auto-sidecar",
        action="store_true",
        help="connect to an existing sidecar instead of auto-starting one",
    )
    parser.add_argument("--rpc-timeout-ms", type=int, default=3000)
    parser.add_argument("--frame-timeout-ms", type=int, default=500)
    parser.add_argument(
        "--disable-lcd",
        action="store_true",
        help="disable LCD initialization and LCD debug controls",
    )
    parser.add_argument(
        "--lcd-display-index",
        type=int,
        default=None,
        help="override the SDL display index used for the LCD backend",
    )
    parser.add_argument(
        "--lcd-transmissive-code",
        type=int,
        default=255,
        help="mono code used for the all-transmissive LCD state",
    )
    parser.add_argument(
        "--lcd-opaque-code",
        type=int,
        default=0,
        help="mono code used for the all-opaque LCD state",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    controller = build_controller(args)

    start_error: Exception | None = None
    try:
        controller.start()
    except Exception as exc:
        start_error = exc

    window = MainWindow(controller)
    if start_error is not None:
        window.root.after(
            0,
            lambda: controller.bus.publish(StatusMessage("error", f"Startup failed: {start_error}")),
        )

    try:
        window.run()
    finally:
        controller.shutdown(force=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
