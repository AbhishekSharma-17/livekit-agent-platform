"""Unit tests for `lkap_agent.logging` (W0-SCAFFOLD)."""

from __future__ import annotations

import json
import logging

import pytest

from lkap_agent.logging import REDACTED, configure_logging, get_logger, redact_secret_fields


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


# --------------------------------------------------------------------- V5-16
@pytest.mark.parametrize(
    "field",
    [
        "access_token",
        "refresh_token",
        "code",
        "state",
        "client_secret",
        "registration_access_token",
        "x_token",
    ],
)
def test_secret_named_fields_never_reach_the_output(field: str, capsys: pytest.CaptureFixture[str]) -> None:
    """V5-16: an OAuth value logged by mistake is masked, whatever the logger."""
    configure_logging(level="DEBUG", json_output=True)
    value = "at-secret-value-Zq81"

    get_logger("lkap_agent.tools.mcp_auth").warning("oops", mcp_server="crm", **{field: value})

    out = capsys.readouterr().out
    assert value not in out
    line = json.loads(out.strip().splitlines()[-1])
    assert line[field] == REDACTED and line["mcp_server"] == "crm"


def test_the_redaction_processor_keeps_other_fields() -> None:
    event = {"event": "x", "tool_id": "t1", "access_token": "a", "AUTHORIZATION": "Bearer b"}

    redacted = redact_secret_fields(None, "info", dict(event))

    assert redacted == {"event": "x", "tool_id": "t1", "access_token": REDACTED, "AUTHORIZATION": REDACTED}
