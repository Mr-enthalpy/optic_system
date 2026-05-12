from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _ensure_repo_on_path() -> None:
    root = str(_repo_root())
    if root not in sys.path:
        sys.path.insert(0, root)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read-only monitor for a running capture task"
    )
    parser.add_argument(
        "--status-dir",
        default=None,
        help="run-status directory written by scripts/capture_forward_dataset.py",
    )
    parser.add_argument(
        "--frame-timeout-ms",
        type=int,
        default=500,
        help="camera frame stream receive timeout in milliseconds",
    )
    parser.add_argument(
        "--poll-ms",
        type=int,
        default=500,
        help="status file polling interval in milliseconds",
    )
    parser.add_argument(
        "--bayer-pattern",
        choices=["BG", "GB", "RG", "GR"],
        default=None,
        help="optional Bayer pattern for raw camera preview decoding",
    )
    parser.add_argument(
        "--no-camera",
        action="store_true",
        default=False,
        help="do not subscribe to the camera frame stream",
    )
    parser.add_argument(
        "--log-dir",
        default=None,
        help="optional local monitor log directory",
    )
    parser.add_argument(
        "--run-id",
        default=None,
        help="run id used to infer outputs/run_status/<run-id> when --status-dir is omitted",
    )
    return parser


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    return build_arg_parser().parse_args(argv)


def resolve_status_dir(args: argparse.Namespace) -> Path:
    if args.status_dir:
        return Path(args.status_dir)
    if args.run_id:
        return Path("outputs") / "run_status" / str(args.run_id)
    return Path("outputs") / "run_status" / "unknown"


def configure_logging(log_dir: str | None) -> None:
    if not log_dir:
        logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
        return
    path = Path(log_dir)
    path.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=str(path / "monitor_gui.log"),
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )


def create_monitor_window(args: argparse.Namespace):
    _ensure_repo_on_path()
    from gui.monitor_window import MonitorWindow

    return MonitorWindow(
        status_dir=resolve_status_dir(args),
        frame_timeout_ms=args.frame_timeout_ms,
        poll_ms=args.poll_ms,
        bayer_pattern=args.bayer_pattern,
        no_camera=args.no_camera,
    )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    configure_logging(args.log_dir)
    window = create_monitor_window(args)
    window.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
