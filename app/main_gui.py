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
from diagnostics.logging_setup import GuiLogContext, setup_gui_logging, write_session_start
from diagnostics.event_log_sink import EventLogSink
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
            subpixel_axis=args.lcd_subpixel_axis,
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
        "--lcd-subpixel-axis",
        type=int,
        choices=[0, 1],
        default=None,
        help="LCD subpixel axis: 0 = height-tripled [3H,W], 1 = width-tripled [H,3W]",
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
    parser.add_argument(
        "--log-dir",
        default="outputs/gui_logs",
        help="parent directory for GUI session logs",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="logging level for GUI session logs",
    )
    parser.add_argument(
        "--run-id",
        default="auto",
        help="run identifier for log subdirectory (default: auto generates timestamp)",
    )
    parser.add_argument(
        "--no-file-log",
        action="store_true",
        help="disable file logging (console logging still active)",
    )
    parser.add_argument(
        "--log-preview-stats-interval-ms",
        type=int,
        default=1000,
        help="minimum interval between PreviewStatsUpdated event log writes (ms)",
    )
    return parser.parse_args(argv)


def _log_device_metadata(controller: SessionController, log: GuiLogContext) -> None:
    logger = log.logger
    state = controller.state.get()

    logger.info("--- Device metadata ---")

    logger.info(
        "Camera: serial=%s, dims=%dx%d, stride=%d, pixel_format=%s",
        state.camera_serial, state.frame_width, state.frame_height,
        state.frame_stride, state.pixel_format,
    )
    logger.info(
        "Sidecar: running=%s, owned=%s, pid=%s",
        state.sidecar_running, state.sidecar_owned, state.sidecar_pid,
    )

    if controller.lcd_service is not None:
        try:
            lcd_meta = controller.lcd_service.get_metadata()
            logger.info(
                "LCD: enabled, display_index=%s, reported_shape=%s, logical_shape=%s, "
                "physical_shape=%s, subpixel_axis=%s, transmissive=%s, opaque=%s",
                lcd_meta.get("display_index"),
                lcd_meta.get("reported_shape"),
                lcd_meta.get("logical_shape"),
                lcd_meta.get("physical_shape"),
                lcd_meta.get("subpixel_axis"),
                lcd_meta.get("transmissive_code"),
                lcd_meta.get("opaque_code"),
            )
        except Exception as exc:
            logger.warning("LCD metadata unavailable: %s", exc)
    else:
        logger.info("LCD: disabled")

    if controller.tls_service is not None:
        try:
            tls_status = controller.tls_service.get_status()
            logger.info(
                "TLS: enabled, connected=%s, device_id=%s, current_wl=%s nm, "
                "target_wl=%s nm, grating=%s, moving=%s",
                tls_status.connected, tls_status.device_id,
                tls_status.current_wavelength_nm,
                tls_status.target_wavelength_nm,
                tls_status.grating, tls_status.moving,
            )
        except Exception as exc:
            logger.warning("TLS metadata unavailable: %s", exc)
    else:
        logger.info("TLS: disabled")

    logger.info("--- End device metadata ---")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    log_context = setup_gui_logging(
        log_dir=args.log_dir,
        run_id=args.run_id,
        log_level=args.log_level,
        enable_file_log=not args.no_file_log,
    )
    logger = log_context.logger

    write_session_start(log_context, argv=argv or sys.argv, args=args)

    logger.info("Starting main_gui.py (run_id=%s)", log_context.run_id)
    if log_context.human_log_path:
        logger.info("Log directory: %s", log_context.log_root)
        logger.info("Human log:     %s", log_context.human_log_path)
        logger.info("Event log:     %s", log_context.event_log_path)

    try:
        logger.info("Building controller ...")
        controller = build_controller(args)
        logger.info("Controller built")
    except SystemExit:
        logger.exception("Controller build failed with SystemExit")
        raise
    except Exception:
        logger.exception("Controller build failed")
        raise

    event_sink = EventLogSink(
        log_context.event_log_path,
        logger,
        preview_stats_interval_ms=args.log_preview_stats_interval_ms,
    )
    controller.bus.subscribe(event_sink)

    start_error: Exception | None = None
    try:
        logger.info("Starting controller ...")
        controller.start()
        logger.info("Controller started")
    except Exception as exc:
        logger.exception("Controller start failed")
        start_error = exc

    _log_device_metadata(controller, log_context)

    if getattr(args, "enable_tls", False) and controller.tls_service is not None:
        logger.info("Auto-connecting TLS ...")
        try:
            controller.dispatch(
                ConnectTLS(
                    serial_number=args.tls_serial_number,
                    mono=args.tls_mono,
                    port_type=args.tls_port_type,
                )
            )
            controller.dispatch(SetTLSGrating(args.tls_safe_grating))
            logger.info("TLS auto-connect commands dispatched, grating=%s", args.tls_safe_grating)
        except Exception as exc:
            logger.warning("TLS auto-connect dispatch failed: %s", exc)
            controller.bus.publish(
                StatusMessage("warning", f"TLS auto-connect failed: {exc}")
            )

        state = controller.state.get()
        logger.info(
            "TLS state after auto-connect: connected=%s, device_id=%s, "
            "current_wl=%s, target_wl=%s, grating=%s, moving=%s, last_error=%s",
            state.tls_connected,
            state.tls_device_id,
            state.tls_current_wavelength_nm,
            state.tls_target_wavelength_nm,
            state.tls_grating,
            state.tls_moving,
            state.tls_last_error,
        )

    logger.info("Opening MainWindow ...")
    window = MainWindow(controller, logger=logger)
    if start_error is not None:
        window.root.after(
            0,
            lambda: controller.bus.publish(StatusMessage("error", f"Startup failed: {start_error}")),
        )

    try:
        logger.info("GUI main loop started")
        window.run()
    finally:
        logger.info("Shutting down controller ...")
        controller.shutdown(force=True)
        logger.info("Shutdown complete")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
