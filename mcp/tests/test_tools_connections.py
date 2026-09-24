"""Connection tools and the secret non-negotiables (D-V3-4, R-V3-3).

The LiveKit boundary is the api tests' in-process Twirp server
(``connection_fakes.fake_livekit``), which verifies the key and secret exactly
as LiveKit does; everything else is the real scratch api.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from conftest import OPERATOR_SCOPES, all_log_text, dumped
from connection_fakes import KEY_B, SECRET_B, fake_livekit
from lkap_api.db.models import LiveKitConnection
from lkap_api.db.session import Database
from lkap_api.errors import BadRequestError
from lkap_api.settings import Settings
from lkap_api.vault import Vault

from lkap_mcp.secrets import INLINE_PLACEHOLDER, REF_PLACEHOLDER


def assert_logs_were_captured(caplog: pytest.LogCaptureFixture) -> None:
    """The "no secret in any log record" asserts are not vacuous: all three layers were captured."""
    names = {record.name for record in caplog.records}
    assert "lkap_mcp.client" in names  # the MCP's own request log
    assert any(name.startswith("lkap_api") for name in names)  # the api's structlog → stdlib
    assert "aiosqlite" in names  # the SQL parameters: the vault row is written encrypted


async def _stored_secrets(database: Database, settings: Settings, connection_id: str) -> tuple[Any, Any]:
    async with database.session() as session:
        row = await session.get(LiveKitConnection, connection_id)
    assert row is not None
    vault = Vault(settings.master_key)
    return vault.decrypt(row.api_key_ct), vault.decrypt(row.api_secret_ct)


async def test_connection_create_plan_shows_two_requests_with_placeholders_and_sends_nothing(
    key: Any, mcp_session: Any, admin: Any
) -> None:
    raw = await key(OPERATOR_SCOPES)

    async with mcp_session(raw) as mcp:
        result = await mcp.call(
            "connection_create",
            name="Cloud V3",
            url="wss://project.livekit.cloud",
            api_key=KEY_B,
            api_secret="env:LKAP_TEST_UNSET_SECRET",
            plan=True,
        )
        sent = [r.url.path for r in mcp.transport.requests if r.url.path.startswith("/v1/connections")]

    assert result["ok"] is True
    plan = result["plan"]
    assert [(step["method"], step["path"]) for step in plan] == [
        ("POST", "/v1/connections/test"),
        ("POST", "/v1/connections"),
    ]
    for step in plan:
        assert step["body"]["api_key"] == INLINE_PLACEHOLDER
        assert step["body"]["api_secret"] == REF_PLACEHOLDER
        assert step["body"]["deployment_type"] == "cloud"
    assert sent == []
    assert KEY_B not in dumped(result)
    listed = (await admin.get("/v1/connections")).json()
    assert [c["slug"] for c in listed["items"]] == ["default"]


async def test_connection_create_inline_secrets_reach_the_vault_and_nothing_else(
    key: Any,
    mcp_session: Any,
    database: Database,
    api_settings: Settings,
    caplog: pytest.LogCaptureFixture,
) -> None:
    raw = await key(OPERATOR_SCOPES)

    async with fake_livekit() as (fake, url), mcp_session(raw) as mcp:
        result = await mcp.call(
            "connection_create",
            name="Cloud Inline",
            url=url,
            api_key=KEY_B,
            api_secret=SECRET_B,
            agent_name="lkap-v3-test",
        )
        listed = await mcp.call("connection_list")
        detail = await mcp.call(
            "connection_get", id=result["data"]["connection"]["id"], include_worker_env=True
        )

    assert result["ok"] is True, result
    connection = result["data"]["connection"]
    assert connection["fingerprint"].endswith(KEY_B[-4:])
    assert result["data"]["test"]["ok"] is True
    assert fake.calls and fake.calls[0].endswith("ListRooms")
    assert await _stored_secrets(database, api_settings, connection["id"]) == (
        {"api_key": KEY_B},
        {"api_secret": SECRET_B},
    )
    assert_logs_were_captured(caplog)
    for value in (KEY_B, SECRET_B):
        for output in (result, listed, detail):
            assert value not in dumped(output)
        assert value not in all_log_text(caplog)


async def test_connection_create_api_error_quoting_the_secret_is_scrubbed(
    key: Any, mcp_session: Any, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    raw = await key(OPERATOR_SCOPES)

    async def leaky_probe(*args: object, **kwargs: object) -> object:
        raise BadRequestError(
            f"LiveKit refused wss://{SECRET_B}.livekit.cloud with key {KEY_B}",
            details={"url": f"wss://x/?secret={SECRET_B}"},
        )

    monkeypatch.setattr("lkap_api.routers.connections.probe_connection", leaky_probe)

    async with mcp_session(raw) as mcp:
        result = await mcp.call(
            "connection_create", name="Leaky", url="wss://p.livekit.cloud", api_key=KEY_B, api_secret=SECRET_B
        )

    assert result["ok"] is False
    assert result["error"]["code"] == "bad_request"
    assert "<redacted>" in result["error"]["message"]
    text = dumped(result)
    assert SECRET_B not in text and KEY_B not in text
    mcp_logs = "\n".join(repr(r.__dict__) for r in caplog.records if r.name.startswith("lkap_mcp"))
    assert SECRET_B not in mcp_logs and KEY_B not in mcp_logs


async def test_connection_create_inline_secrets_off_is_inline_secret_refused(
    key: Any, mcp_session: Any
) -> None:
    raw = await key(OPERATOR_SCOPES)

    async with mcp_session(raw, inline_secrets="off") as mcp:
        result = await mcp.call(
            "connection_create",
            name="No Inline",
            url="wss://p.livekit.cloud",
            api_key=KEY_B,
            api_secret=SECRET_B,
        )
        sent = mcp.transport.calls("POST", "/v1/connections")

    assert result["ok"] is False
    assert result["error"]["code"] == "inline_secret_refused"
    assert "env:NAME" in result["error"]["hint"] and "file:" in result["error"]["hint"]
    assert sent == []
    assert KEY_B not in dumped(result)


async def test_connection_create_file_and_env_references_resolve_and_never_surface(
    key: Any,
    mcp_session: Any,
    database: Database,
    api_settings: Settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    raw = await key(OPERATOR_SCOPES)
    env_file = tmp_path / "x.env"
    env_file.write_text(f"LIVEKIT_URL=wss://unused.example\nLIVEKIT_API_SECRET={SECRET_B}\n")
    env_file.chmod(0o600)
    monkeypatch.setenv("LKAP_TEST_LK_KEY", KEY_B)

    async with fake_livekit() as (_fake, url), mcp_session(raw) as mcp:
        result = await mcp.call(
            "connection_create",
            name="Cloud Ref",
            url=url,
            api_key="env:LKAP_TEST_LK_KEY",
            api_secret=f"file:{env_file}#LIVEKIT_API_SECRET",
        )

    assert result["ok"] is True, result
    assert result["warnings"] == []
    stored = await _stored_secrets(database, api_settings, result["data"]["connection"]["id"])
    assert stored == ({"api_key": KEY_B}, {"api_secret": SECRET_B})
    assert SECRET_B not in dumped(result) and KEY_B not in dumped(result)
    assert_logs_were_captured(caplog)
    assert SECRET_B not in all_log_text(caplog) and KEY_B not in all_log_text(caplog)


async def test_connection_create_failed_probe_saves_nothing(key: Any, mcp_session: Any, admin: Any) -> None:
    raw = await key(OPERATOR_SCOPES)

    async with fake_livekit() as (_fake, url), mcp_session(raw) as mcp:
        result = await mcp.call(
            "connection_create",
            name="Wrong",
            url=url,
            api_key=KEY_B,
            api_secret="not-the-right-secret-000000",
        )

    assert result["ok"] is False and result["error"]["code"] == "connection_test_failed"
    assert (await admin.get("/v1/connections")).json()["total"] == 1


async def test_connection_rotate_needs_confirmation_and_plan_hides_the_values(
    key: Any, mcp_session: Any
) -> None:
    raw = await key(OPERATOR_SCOPES)

    async with mcp_session(raw) as mcp:
        unconfirmed = await mcp.call("connection_rotate", id="default", api_key=KEY_B, api_secret=SECRET_B)
        planned = await mcp.call(
            "connection_rotate", id="default", api_key=KEY_B, api_secret=SECRET_B, plan=True
        )
        sent = mcp.transport.calls("POST", "/v1/connections/default/rotate")

    assert unconfirmed["error"]["code"] == "needs_confirmation"
    assert planned["plan"][0]["body"] == {"api_key": INLINE_PLACEHOLDER, "api_secret": INLINE_PLACEHOLDER}
    assert sent == []


async def test_connection_fleet_stop_needs_confirmation(key: Any, mcp_session: Any) -> None:
    raw = await key(OPERATOR_SCOPES)

    async with mcp_session(raw) as mcp:
        read = await mcp.call("connection_fleet", id="default")
        stop = await mcp.call("connection_fleet", id="default", action="stop")

    assert read["ok"] is True and read["data"]["summary"]["ready"] == 0
    assert stop["error"]["code"] == "needs_confirmation"
