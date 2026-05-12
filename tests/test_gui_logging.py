from __future__ import annotations

import argparse
import json
import logging
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pytest

from control.events import Event, PreviewFrameUpdated, PreviewStatsUpdated, StatusMessage
from diagnostics.logging_setup import (
    GuiLogContext,
    close_gui_logging,
    setup_gui_logging,
    write_session_start,
)
from diagnostics.event_log_sink import EventLogSink


def _cleanup(logger: logging.Logger) -> None:
    close_gui_logging(logger)
    for h in list(logger.handlers):
        try:
            h.close()
        except Exception:
            pass
        logger.removeHandler(h)


class TestSetupGuiLogging:

    def test_creates_directory_and_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            context = setup_gui_logging(log_dir=tmpdir, run_id="test_run")

            try:
                assert context.run_id == "test_run"
                assert context.log_root == Path(tmpdir) / "test_run"
                assert context.log_root.is_dir()
                assert context.human_log_path is not None
                assert context.event_log_path is not None
                assert context.session_start_path is not None
                assert isinstance(context.logger, logging.Logger)
            finally:
                _cleanup(context.logger)

    def test_auto_run_id(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            context = setup_gui_logging(log_dir=tmpdir, run_id="auto")
            try:
                assert context.run_id != "auto"
                assert context.run_id.endswith("_main_gui")
            finally:
                _cleanup(context.logger)

    def test_none_run_id(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            context = setup_gui_logging(log_dir=tmpdir, run_id=None)
            try:
                assert context.run_id is not None
                assert context.run_id.endswith("_main_gui")
            finally:
                _cleanup(context.logger)

    def test_no_file_log(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            context = setup_gui_logging(log_dir=tmpdir, run_id="no_file", enable_file_log=False)
            try:
                assert context.human_log_path is None
                assert context.event_log_path is None
                assert context.session_start_path is None
                assert isinstance(context.logger, logging.Logger)
            finally:
                _cleanup(context.logger)

    def test_handler_deduplication(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ctx1 = setup_gui_logging(log_dir=tmpdir, run_id="dedup")
            n_handlers_before = len(ctx1.logger.handlers)

            ctx2 = setup_gui_logging(log_dir=tmpdir, run_id="dedup")
            n_handlers_after = len(ctx2.logger.handlers)

            assert n_handlers_after == n_handlers_before
            _cleanup(ctx2.logger)

    def test_handler_deduplication_disable_after_enable(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ctx1 = setup_gui_logging(log_dir=tmpdir, run_id="dedup2", enable_file_log=True)
            has_file_before = any(
                isinstance(h, logging.FileHandler) for h in ctx1.logger.handlers
            )
            assert has_file_before

            ctx2 = setup_gui_logging(log_dir=tmpdir, run_id="dedup2", enable_file_log=False)
            has_file_after = any(
                isinstance(h, logging.FileHandler) for h in ctx2.logger.handlers
            )
            assert not has_file_after

            _cleanup(ctx2.logger)

    def test_writes_to_human_log(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            context = setup_gui_logging(log_dir=tmpdir, run_id="write_test")
            try:
                context.logger.info("test message")

                assert context.human_log_path is not None
                content = context.human_log_path.read_text(encoding="utf-8")
                assert "test message" in content
            finally:
                _cleanup(context.logger)


class TestWriteSessionStart:

    def test_writes_and_parses(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            context = setup_gui_logging(log_dir=tmpdir, run_id="session_test")
            try:
                parser = argparse.ArgumentParser()
                parser.add_argument("--foo", default="bar")
                args = parser.parse_args([])

                write_session_start(context, argv=["prog", "--foo", "bar"], args=args)

                assert context.session_start_path is not None
                assert context.session_start_path.exists()
                data = json.loads(context.session_start_path.read_text(encoding="utf-8"))
                assert data["run_id"] == "session_test"
                assert data["args"]["foo"] == "bar"
                assert "cwd" in data
                assert "python" in data
                assert "platform" in data
            finally:
                _cleanup(context.logger)

    def test_no_path_does_not_crash(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            context = setup_gui_logging(log_dir=tmpdir, run_id="no_sess", enable_file_log=False)
            try:
                parser = argparse.ArgumentParser()
                args = parser.parse_args([])
                write_session_start(context, argv=[], args=args)
            finally:
                _cleanup(context.logger)


class TestEventLogSink:

    def _temp_logger(self, tmpdir: str) -> logging.Logger:
        logger = logging.getLogger("test_sink")
        _cleanup(logger)
        logger.setLevel(logging.DEBUG)
        return logger

    def _read_jsonl(self, path: Path) -> list[dict]:
        lines = path.read_text(encoding="utf-8").strip().split("\n")
        return [json.loads(line) for line in lines if line.strip()]

    def test_writes_status_message(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            jsonl_path = Path(tmpdir) / "events.jsonl"
            logger = self._temp_logger(tmpdir)
            sink = EventLogSink(jsonl_path, logger)

            event = StatusMessage(level="info", message="hello world")
            sink(event)

            records = self._read_jsonl(jsonl_path)
            assert len(records) == 1
            assert records[0]["event_type"] == "StatusMessage"
            assert records[0]["payload"]["level"] == "info"
            assert records[0]["payload"]["message"] == "hello world"
            assert "ts" in records[0]
            assert "monotonic_ns" in records[0]

    def test_skips_previewframeupdated(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            jsonl_path = Path(tmpdir) / "events.jsonl"
            logger = self._temp_logger(tmpdir)
            sink = EventLogSink(jsonl_path, logger)

            frame_event = PreviewFrameUpdated(preview_bgr=np.zeros((10, 10, 3), dtype=np.uint8))
            sink(frame_event)

            if jsonl_path.exists():
                content = jsonl_path.read_text(encoding="utf-8").strip()
                assert content == "" or content is None

    def test_throttles_preview_stats(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            jsonl_path = Path(tmpdir) / "events.jsonl"
            logger = self._temp_logger(tmpdir)
            sink = EventLogSink(jsonl_path, logger, preview_stats_interval_ms=500)

            for _ in range(5):
                sink(PreviewStatsUpdated(
                    max_pixel=100.0, frame_seq=1, timestamp_ns=0,
                    width=640, height=480, stride=1920, pixel_format="raw8",
                ))

            records = self._read_jsonl(jsonl_path)
            assert len(records) == 1

    def test_does_not_crash_on_unserializable(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            jsonl_path = Path(tmpdir) / "events.jsonl"
            logger = self._temp_logger(tmpdir)
            sink = EventLogSink(jsonl_path, logger)

            @dataclass
            class BadEvent(Event):
                bad_field: object

            event = BadEvent(bad_field=lambda: None)

            sink(event)

    def test_no_path_still_logs_to_logger(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "test.log"
            logger = self._temp_logger(tmpdir)
            fh = logging.FileHandler(str(log_path), encoding="utf-8")
            logger.addHandler(fh)

            sink = EventLogSink(None, logger)
            sink(StatusMessage(level="error", message="bad thing"))

            fh.close()
            logger.removeHandler(fh)
            content = log_path.read_text(encoding="utf-8")
            assert "bad thing" in content

    def test_event_has_all_required_fields(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            jsonl_path = Path(tmpdir) / "events.jsonl"
            logger = self._temp_logger(tmpdir)
            sink = EventLogSink(jsonl_path, logger)

            sink(StatusMessage(level="success", message="done"))
            records = self._read_jsonl(jsonl_path)
            rec = records[0]
            assert "ts" in rec
            assert "monotonic_ns" in rec
            assert rec["event_type"] == "StatusMessage"
            assert "payload" in rec


class TestImportMainGui:

    def test_import_does_not_require_hardware(self):
        import app.main_gui  # noqa: F401
