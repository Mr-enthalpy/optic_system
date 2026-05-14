"""
Minimal CLI entry-point for Phase 2 raw capture.

Usage (dry-run — no hardware required)::

    python scripts/capture_forward_dataset.py --plan example_plan.json --output out.h5 --dry-run

Hardware mode::

    python scripts/capture_forward_dataset.py --plan plan.yaml --output out.h5 --hardware --enable-tls

Defaults are safe: CLI runs as dry-run unless ``--hardware`` is passed.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _ensure_repo_on_path() -> None:
    root = str(_repo_root())
    if root not in sys.path:
        sys.path.insert(0, root)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Phase 2 minimal capture — raw HDF5 export"
    )
    parser.add_argument(
        "--plan",
        required=True,
        help="path to capture plan (JSON or YAML)",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="output HDF5 path (defaults to <plan_id>.h5 in cwd)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="use fake devices, no hardware required",
    )
    parser.add_argument(
        "--hardware",
        action="store_true",
        default=False,
        help="enable real hardware execution (camera sidecar + LCD + optional TLS)",
    )
    parser.add_argument(
        "--store-burst",
        action="store_true",
        default=None,
        help="store individual burst frames (overrides plan setting)",
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
        "--camera-index",
        type=int,
        default=0,
    )
    parser.add_argument(
        "--lcd-display-index",
        type=int,
        default=None,
    )
    parser.add_argument(
        "--lcd-subpixel-axis",
        type=int,
        choices=[0, 1],
        default=None,
        help="LCD subpixel axis: 0 = height-tripled [3H,W], 1 = width-tripled [H,3W]",
    )
    parser.add_argument(
        "--no-auto-sidecar",
        action="store_true",
        help="connect to an existing sidecar instead of auto-starting one",
    )
    parser.add_argument(
        "--status-dir",
        default=None,
        help="optional run-status directory for scripts/monitor_run_status.py",
    )
    parser.add_argument(
        "--run-id",
        default="auto",
        help="run id stored in status output; use 'auto' for a timestamp id",
    )
    parser.add_argument(
        "--no-status",
        action="store_true",
        default=False,
        help="disable optional run-status output",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    if not args.hardware and not args.dry_run:
        print(
            "No execution mode selected. Defaulting to --dry-run.\n"
            "Pass --hardware for real device execution."
        )
        args.dry_run = True

    _ensure_repo_on_path()

    from tasks.capture_plan import CapturePlan, CapturePlanError
    from tasks.capture_forward_dataset import (
        FakeCamera,
        FakeDeviceBundle,
        FakeLCD,
        FakeTLS,
        run_capture_forward_dataset,
    )

    plan_path = Path(args.plan)
    if not plan_path.exists():
        print(f"Error: plan file not found: {plan_path}")
        return 1

    try:
        if plan_path.suffix in (".yaml", ".yml"):
            plan = CapturePlan.load_yaml(plan_path)
        else:
            plan = CapturePlan.load_json(plan_path)
    except CapturePlanError as exc:
        print(f"Error loading plan: {exc}")
        return 1

    if args.store_burst is not None:
        plan.store_burst = args.store_burst

    output_path = Path(args.output) if args.output else Path(f"{plan.plan_id}.h5")
    status_dir, run_id = _resolve_status_args(args, plan.plan_id)
    if status_dir is not None:
        print(f"Run status: {status_dir}")
        print(f"Run id: {run_id}")

    if args.dry_run or not args.hardware:
        print(f"[dry-run] plan: {plan.plan_id}")
        print(f"[dry-run] wavelengths: {plan.n_wavelengths}, masks: {plan.n_masks}")
        print(f"[dry-run] total captures: {plan.n_captures}")
        print(f"[dry-run] output: {output_path}")

        devices = FakeDeviceBundle(
            camera=FakeCamera(),
            lcd=FakeLCD(),
            tls=FakeTLS() if args.enable_tls else None,
        )
        if args.enable_tls:
            devices.tls.connect()

        result = run_capture_forward_dataset(
            plan=plan,
            devices=devices,
            output_path=output_path,
            enable_tls=args.enable_tls,
            dry_run=True,
            status_dir=status_dir,
            run_id=run_id,
        )
        print(f"[dry-run] wrote {plan.n_captures} captures to {result}")
        return 0

    print("Hardware mode: starting capture ...")
    print(f"  plan: {plan.plan_id}")
    print(f"  wavelengths: {plan.n_wavelengths}, masks: {plan.n_masks}")
    print(f"  total captures: {plan.n_captures}")
    print(f"  output: {output_path}")

    try:
        return _run_hardware(args, plan, output_path, status_dir=status_dir, run_id=run_id)
    except Exception as exc:
        print(f"Hardware capture failed: {exc}")
        return 1


def _run_hardware(
    args: argparse.Namespace,
    plan: CapturePlan,
    output_path: Path,
    *,
    status_dir: Path | None,
    run_id: str | None,
) -> int:
    from devices.camera_service import CameraServiceClient
    from devices.frame_stream import FrameStreamClient
    from devices.lcd_service import LCDService
    from devices.tls_service import TLSService, TLSServiceUnavailableError
    from capture.frame_capture import FrameCaptureHelper
    from tasks.capture_forward_dataset import (
        CameraCaptureAdapter,
        LCDAdapter,
        TLSAdapter,
        run_capture_forward_dataset,
    )

    camera_service = CameraServiceClient(
        auto_ensure=not args.no_auto_sidecar,
    )

    try:
        open_reply = camera_service.open_camera(
            index=args.camera_index,
            context_type="IIDC",
            disable_trigger=True,
        )
        print(f"  camera open: {open_reply.get('serial')} "
              f"{open_reply.get('width')}x{open_reply.get('height')}")

        camera_service.start_stream()
        print("  stream started")

        frame_stream = FrameStreamClient()
        capture_helper = FrameCaptureHelper(frame_stream)

        lcd_service = LCDService(display_index=args.lcd_display_index, subpixel_axis=args.lcd_subpixel_axis)
        meta = lcd_service.get_metadata()
        print(f"  lcd: display_index={meta.get('display_index')} "
              f"reported={meta.get('reported_shape')} "
              f"logical={meta.get('logical_shape')} "
              f"physical={meta.get('physical_shape')} "
              f"subpixel_axis={meta.get('subpixel_axis')}")

        tls_service: TLSService | None = None
        tls_adapter = None
        if args.enable_tls:
            try:
                tls_service = TLSService(
                    default_serial_number=args.tls_serial_number,
                )
                tls_service.connect()
                st = tls_service.get_status()
                print(f"  tls connected: device {st.device_id}, "
                      f"{st.current_wavelength_nm} nm, grating {st.grating}")
                tls_adapter = TLSAdapter(tls_service)
            except TLSServiceUnavailableError as exc:
                print(f"TLS SDK not available: {exc}")
                return 1

        devices: FakeDeviceBundle = type(
            "_Bundle", (), {
                "camera": CameraCaptureAdapter(capture_helper),
                "lcd": LCDAdapter(lcd_service),
                "tls": tls_adapter,
            }
        )()

        result = run_capture_forward_dataset(
            plan=plan,
            devices=devices,
            output_path=output_path,
            enable_tls=args.enable_tls,
            dry_run=False,
            status_dir=status_dir,
            run_id=run_id,
        )
        print(f"Capture complete: {result}")
    finally:
        try:
            camera_service.stop_stream()
        except Exception:
            pass
        try:
            camera_service.close_camera()
        except Exception:
            pass
        try:
            camera_service.close()
        except Exception:
            pass
        if tls_service is not None:
            try:
                tls_service.close()
            except Exception:
                pass

    return 0


def _resolve_status_args(
    args: argparse.Namespace,
    plan_id: str,
) -> tuple[Path | None, str | None]:
    if args.no_status or not args.status_dir:
        return None, None

    run_id = _make_run_id(plan_id) if args.run_id == "auto" else str(args.run_id)
    status_dir_text = str(args.status_dir).replace("{run_id}", run_id)
    return Path(status_dir_text), run_id


def _make_run_id(plan_id: str) -> str:
    return f"{plan_id}_{time.strftime('%Y%m%d_%H%M%S')}"


if __name__ == "__main__":
    raise SystemExit(main())
