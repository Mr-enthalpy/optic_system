from __future__ import annotations

import argparse
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from capture.preview_worker import PreviewWorker
from control.commands import ConnectTLS, SetTLSGrating
from control.events import StatusMessage
from control.session_controller import SessionController
from devices.camera_service import CameraServiceClient
from devices.frame_stream import FrameStreamClient
from devices.lcd_service import LCDService
from devices.tls_service import TLSService, TLSServiceUnavailableError
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

    tls_service = None
    if getattr(args, "enable_tls", False):
        try:
            tls_service = TLSService(
                default_serial_number=args.tls_serial_number,
            )
        except TLSServiceUnavailableError as exc:
            print(
                f"Error: --enable-tls was requested but the tls_c1 SDK "
                f"is not installed or could not be imported.\n"
                f"  {exc}\n"
                f"Install tls_c1 and try again, or omit --enable-tls to "
                f"start without TLS support."
            )
            raise SystemExit(1) from exc

    return SessionController(
        camera_service=camera_service,
        preview_worker=preview_worker,
        lcd_service=lcd_service,
        tls_service=tls_service,
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
    parser.add_argument(
        "--enable-tls",
        action="store_true",
        default=False,
        help="enable TLS wavelength control via tls_c1 SDK",
    )
    parser.add_argument(
        "--tls-serial-number",
        default=None,
        help="TLS device serial number (overrides TLS_C1_SERIAL env var)",
    )
    parser.add_argument(
        "--tls-safe-grating",
        type=int,
        default=1,
        help="default TLS grating to set after connect",
    )
    parser.add_argument(
        "--tls-mono",
        default=None,
        help="TLS monochromator type (default: Omni)",
    )
    parser.add_argument(
        "--tls-port-type",
        default=None,
        help="TLS port type (default: USB)",
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

    if getattr(args, "enable_tls", False) and controller.tls_service is not None:
        try:
            controller.dispatch(
                ConnectTLS(
                    serial_number=args.tls_serial_number,
                    mono=args.tls_mono,
                    port_type=args.tls_port_type,
                )
            )
            controller.dispatch(SetTLSGrating(args.tls_safe_grating))
        except Exception as exc:
            controller.bus.publish(
                StatusMessage("warning", f"TLS auto-connect failed: {exc}")
            )

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
