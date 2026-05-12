from __future__ import annotations

import dataclasses
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from control.events import Event, PreviewFrameUpdated, PreviewStatsUpdated


class EventLogSink:
    def __init__(
        self,
        path: str | Path | None,
        logger: logging.Logger,
        *,
        preview_stats_interval_ms: int = 1000,
    ):
        self._path = Path(path) if path is not None else None
        self._logger = logger
        self._preview_stats_interval_s = max(preview_stats_interval_ms, 0) / 1000.0
        self._last_preview_stats_monotonic: float = 0.0

    def __call__(self, event: Event) -> None:
        if isinstance(event, PreviewFrameUpdated):
            return

        if isinstance(event, PreviewStatsUpdated):
            now = time.monotonic()
            if now - self._last_preview_stats_monotonic < self._preview_stats_interval_s:
                return
            self._last_preview_stats_monotonic = now

        if self._path is not None:
            self._write_jsonl(event)

        self._log_to_human(event)

    def _write_jsonl(self, event: Event) -> None:
        try:
            payload = dataclasses.asdict(event)
        except Exception:
            return

        line = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "monotonic_ns": time.monotonic_ns(),
            "event_type": type(event).__name__,
            "payload": payload,
        }

        try:
            serialized = json.dumps(line, default=_json_fallback, ensure_ascii=False)
            with open(str(self._path), "a", encoding="utf-8") as fh:
                fh.write(serialized + "\n")
        except Exception as exc:
            self._logger.warning("Failed to serialize event %s: %s", type(event).__name__, exc)

    def _log_to_human(self, event: Event) -> None:
        from control.events import (
            CameraError,
            LCDError,
            StatusMessage,
            TLSError,
        )

        if isinstance(event, StatusMessage):
            level = event.level.lower()
            if level in ("error", "critical"):
                self._logger.error("StatusMessage: [%s] %s", event.level, event.message)
            elif level == "warning":
                self._logger.warning("StatusMessage: [%s] %s", event.level, event.message)
            else:
                self._logger.info("StatusMessage: [%s] %s", event.level, event.message)
        elif isinstance(event, CameraError):
            self._logger.error("CameraError [%s]: %s", event.source, event.message)
        elif isinstance(event, LCDError):
            self._logger.error("LCDError [%s]: %s", event.source, event.message)
        elif isinstance(event, TLSError):
            self._logger.error("TLSError [%s]: %s", event.source, event.message)


def _json_fallback(obj: object) -> object:
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        return float(obj)
    if isinstance(obj, np.ndarray):
        return f"<ndarray shape={obj.shape} dtype={obj.dtype}>"
    if isinstance(obj, tuple):
        return list(obj)
    return str(obj)
