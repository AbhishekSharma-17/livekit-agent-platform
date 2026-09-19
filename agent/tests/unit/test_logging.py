"""Unit tests for `lkap_agent.logging` (W0-SCAFFOLD)."""

from __future__ import annotations

import logging

from lkap_agent.logging import configure_logging, get_logger


def test_configure_logging_sets_root_level_and_single_handler() -> None:
    configure_logging(level="DEBUG", json_output=False)
    root = logging.getLogger()
    assert root.level == logging.DEBUG
    assert len(root.handlers) == 1


def test_configure_logging_is_idempotent_and_replaces_handlers() -> None:
    configure_logging(level="INFO", json_output=True)
    configure_logging(level="WARNING", json_output=True)
    root = logging.getLogger()
    assert root.level == logging.WARNING
    assert len(root.handlers) == 1


def test_get_logger_emits_without_raising() -> None:
    configure_logging(level="INFO", json_output=True)
    log = get_logger("test.logger")
    log.info("hello", key="value")
