"""Shared fixtures: structlog routed to caplog, and a reaper for real child processes."""

from __future__ import annotations

import logging
import os
import signal
from collections.abc import Iterator

import pytest

from lkap_supervisor.logging import configure_logging


@pytest.fixture
def log_capture(caplog: pytest.LogCaptureFixture) -> Iterator[pytest.LogCaptureFixture]:
    """Configure structlog at DEBUG and re-attach pytest's handler after it replaced the root handlers."""
    configure_logging(level="DEBUG")
    root = logging.getLogger()
    root.addHandler(caplog.handler)
    caplog.set_level(logging.DEBUG)
    try:
        yield caplog
    finally:
        root.removeHandler(caplog.handler)


def all_log_text(caplog: pytest.LogCaptureFixture, capsys: pytest.CaptureFixture[str]) -> str:
    """Everything logged: record payloads plus whatever reached stdout/stderr."""
    records = "".join(f"{r.name} {r.getMessage()} {r.msg!r} {r.args!r}\n" for r in caplog.records)
    captured = capsys.readouterr()
    return records + captured.out + captured.err


class Reaper:
    """Remembers pids started by a test and SIGKILLs any still alive at teardown."""

    def __init__(self) -> None:
        self.pids: set[int] = set()

    def track(self, pid: int) -> int:
        self.pids.add(pid)
        return pid

    def reap(self) -> list[int]:
        leftovers: list[int] = []
        for pid in self.pids:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                continue
            leftovers.append(pid)
            try:
                os.killpg(os.getpgid(pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                os.kill(pid, signal.SIGKILL)
        return leftovers


@pytest.fixture
def reaper() -> Iterator[Reaper]:
    """Track real child pids; none may survive the test."""
    tracker = Reaper()
    yield tracker
    leftovers = tracker.reap()
    assert not leftovers, f"test leaked child processes: {leftovers}"
