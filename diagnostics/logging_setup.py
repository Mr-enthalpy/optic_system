from __future__ import annotations

import argparse
import json
import logging
import os
import platform
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass(frozen=True)
class GuiLogContext:
    run_id: str
    log_root: Path
    human_log_path: Path | None
    event_log_path: Path | None
    session_start_path: Path | None
    logger: logging.Logger


def _make_timestamp_run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_main_gui")


def setup_gui_logging(
    *,
    log_dir: str | Path = "outputs/gui_logs",
    run_id: str | None = None,
    log_level: str = "INFO",
    enable_file_log: bool = True,
) -> GuiLogContext:
    if run_id in (None, "", "auto"):
        run_id = _make_timestamp_run_id()

    log_root = Path(log_dir) / run_id
    human_log_path: Path | None = None
    event_log_path: Path | None = None
    session_start_path: Path | None = None

    logger = logging.getLogger("optic_system.gui")
    _level = getattr(logging, log_level.upper(), logging.INFO)
    logger.setLevel(_level)

    _remove_tagged_handlers(logger)

    if enable_file_log:
        try:
            log_root.mkdir(parents=True, exist_ok=True)
            human_log_path = log_root / "main_gui.log"
            event_log_path = log_root / "events.jsonl"
            session_start_path = log_root / "session_start.json"

            fh = logging.FileHandler(str(human_log_path), encoding="utf-8")
            fh.setLevel(_level)
            fh.setFormatter(logging.Formatter(
                "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
            ))
            fh._optic_system_gui_handler = True  # type: ignore[attr-defined]
            logger.addHandler(fh)
        except Exception:
            pass

    _ensure_console_handler(logger, _level)

    logger.propagate = False

    return GuiLogContext(
        run_id=run_id,
        log_root=log_root,
        human_log_path=human_log_path,
        event_log_path=event_log_path,
        session_start_path=session_start_path,
        logger=logger,
    )


def _ensure_console_handler(logger: logging.Logger, level: int) -> None:
    for handler in logger.handlers:
        if isinstance(handler, logging.StreamHandler) and not isinstance(handler, logging.FileHandler):
            if getattr(handler, "_optic_system_gui_handler", False):
                return
    ch = logging.StreamHandler(sys.stderr)
    ch.setLevel(level)
    ch.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    ))
    ch._optic_system_gui_handler = True  # type: ignore[attr-defined]
    logger.addHandler(ch)


def _remove_tagged_handlers(logger: logging.Logger) -> None:
    for h in list(logger.handlers):
        if getattr(h, "_optic_system_gui_handler", False):
            try:
                h.close()
            except Exception:
                pass
    logger.handlers[:] = [
        h for h in logger.handlers
        if not getattr(h, "_optic_system_gui_handler", False)
    ]


def close_gui_logging(logger: logging.Logger) -> None:
    _remove_tagged_handlers(logger)


def _try_git_commit() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return None


def write_session_start(
    context: GuiLogContext,
    *,
    argv: list[str],
    args: argparse.Namespace,
) -> None:
    if context.session_start_path is None:
        return

    args_dict: dict[str, object] = {}
    for key, value in vars(args).items():
        if isinstance(value, Path):
            args_dict[key] = str(value)
        else:
            args_dict[key] = value

    payload: dict[str, object] = {
        "run_id": context.run_id,
        "argv": argv,
        "cwd": os.getcwd(),
        "python": sys.executable,
        "platform": platform.platform(),
        "git_commit": _try_git_commit(),
        "args": args_dict,
    }

    try:
        context.session_start_path.write_text(
            json.dumps(payload, indent=2, default=str, ensure_ascii=False),
            encoding="utf-8",
        )
        context.logger.info("Session start metadata written to %s", context.session_start_path)
    except Exception as exc:
        context.logger.warning("Failed to write session_start.json: %s", exc)
