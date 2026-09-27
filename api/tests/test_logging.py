"""Unit tests for `lkap_api.logging` (W0-SCAFFOLD)."""

from __future__ import annotations

import logging

import pytest

from lkap_api.logging import AccessLogQueryFilter, configure_logging, get_logger


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


# ---------------------------------------------------------------- V5-27 (S5-32)
def test_configure_logging_runs_the_oauth_redactor(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(level="INFO", json_output=True)
    get_logger("test.redact").info(
        "oops", access_token="at-SECRET-1", state="st-SECRET-2", refresh_token="rt-SECRET-3", ok=1
    )
    out = capsys.readouterr().out
    assert "SECRET" not in out
    assert out.count("[redacted]") == 3 and '"ok": 1' in out


def _access_record(path: str) -> logging.LogRecord:
    return logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        __file__,
        1,
        '%s - "%s %s HTTP/%s" %d',
        ("127.0.0.1:5000", "GET", path, "1.1", 302),
        None,
    )


@pytest.mark.parametrize(
    ("path", "hidden"),
    [
        ("/v1/oauth/mcp/callback?code=abc&state=def", True),
        ("/api/v1/oauth/mcp/callback?code=abc&state=def", True),
        ("/v1/tool-providers/composio/callback?flow=row.nonce&connected_account_id=ca_1", True),
        ("/prefix/v1/tool-providers/composio/callback?flow=row.nonce", True),
        ("/v1/sessions/s1/assets/a1/content?exp=1&sig=abcdef", True),
        ("/v1/tools?kind=mcp", False),
        ("/v1/knowledge-bases/k/documents?limit=5", False),
    ],
)
def test_access_log_filter_matches_under_a_root_path(path: str, hidden: bool) -> None:
    record = _access_record(path)
    assert AccessLogQueryFilter().filter(record)
    message = record.getMessage()
    if hidden:
        assert "?[redacted]" in message and path.split("?", 1)[1] not in message
    else:
        assert path in message


def test_configure_logging_installs_the_access_log_filter() -> None:
    access = logging.getLogger("uvicorn.access")
    access.filters = [f for f in access.filters if not isinstance(f, AccessLogQueryFilter)]
    configure_logging(level="INFO", json_output=True)
    assert any(isinstance(f, AccessLogQueryFilter) for f in access.filters)
