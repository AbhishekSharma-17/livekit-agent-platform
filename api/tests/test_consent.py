"""V5-15: consent and disclosure on the api side.

The ``consent`` event folded into ``sessions.consent_state``, the recording
start refused until the caller agrees, the "not recorded" note at the end of
the session, workspace compliance settings (``PUT`` / ``GET``), the resolved
config's wording, and the validators.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

import httpx
import pytest
from conftest import create_agent, inference_config
from connection_fakes import add_agent, connection_row, fake_livekit
from lkap_contracts.agent_config import DisclosureConfig, PanelLayout, RecordingConfig, ResolvedAgentConfig
from lkap_contracts.compliance import COMPLIANCE_PRESETS
from lkap_contracts.ui_protocol import BlockSpec
from sqlalchemy import select

from lkap_api.config_service import CONSENT_BLOCK_MISSING_MESSAGE, ValidationContext, consent_issues, validate
from lkap_api.db.models import Session as SessionRow
from lkap_api.db.models import SessionEvent, StorageConfig
from lkap_api.db.session import Database
from lkap_api.recordings.consent import NOT_RECORDED_DECLINED, NOT_RECORDED_NO_ANSWER
from lkap_api.settings import Settings
from lkap_api.vault import Vault

GATED = RecordingConfig(enabled=True, require_consent=True)


def _hash(text: str = "May we record?") -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _consent(accepted: bool, *, kind: str = "recording", ts: float = 1758000000.0) -> dict[str, Any]:
    return {
        "ts": ts,
        "type": "consent",
        "payload": {
            "kind": kind,
            "accepted": accepted,
            "method": "tap",
            "text_hash": _hash(),
            "block_id": "rec",
        },
    }


# ------------------------------------------------------------------ recording start


async def _gated_session(database: Database, settings: Settings, url: str) -> str:
    vault = Vault(settings.master_key)
    async with database.session() as session:
        storage = StorageConfig(
            workspace_id="00000000000000000000000000000001",
            name="MinIO",
            kind="s3",
            bucket="lkap-recordings",
            access_key_ct=vault.encrypt({"access_key": "AKIA_TEST"}),
            secret_key_ct=vault.encrypt({"secret_key": "secret_test_value"}),
            is_default=True,
        )
        session.add(storage)
        await session.flush()
        conn = connection_row(
            vault, url=url, capabilities={"egress_enabled": True}, storage_config_id=storage.id
        )
        session.add(conn)
        await session.flush()
        agent = await add_agent(session, inference_config(recording=GATED), connection_id=conn.id)
        row = SessionRow(
            id="c" * 32,
            agent_id=agent.id,
            connection_id=conn.id,
            config_version=1,
            room_name="lkap-consent",
            participant_identity="u",
            participant_name="U",
            status="active",
            pipeline_mode="cascaded",
        )
        session.add(row)
        await session.flush()
        return row.id


async def test_a_consent_gated_recording_starts_only_after_the_caller_agrees(
    service_client: httpx.AsyncClient, database: Database, settings: Settings
) -> None:
    async with fake_livekit() as (fake, url):
        session_id = await _gated_session(database, settings, url)
        start = f"/internal/v1/sessions/{session_id}/recording/start"
        events = f"/internal/v1/sessions/{session_id}/events"

        refused = await service_client.post(start)
        assert refused.status_code == 409, refused.text
        await service_client.post(events, json={"events": [_consent(False)]})
        assert (await service_client.post(start)).status_code == 409
        assert fake.calls == []

        await service_client.post(events, json={"events": [_consent(True, ts=1758000001.0)]})
        started = await service_client.post(start)
        assert started.status_code == 200, started.text
        assert len(fake.calls) == 1


# ------------------------------------------------------------------ consent_state


async def test_consent_events_fold_into_the_latest_answer_per_kind(
    service_client: httpx.AsyncClient, database: Database, settings: Settings
) -> None:
    async with fake_livekit() as (_fake, url):
        session_id = await _gated_session(database, settings, url)
    events = [
        _consent(False),
        _consent(True, kind="ai_disclosure", ts=1758000001.0),
        _consent(True, ts=1758000002.0),
        {"ts": 1758000003.0, "type": "consent", "payload": {"kind": "recording", "accepted": "maybe"}},
    ]
    response = await service_client.post(
        f"/internal/v1/sessions/{session_id}/events", json={"events": events}
    )
    assert response.status_code == 202

    async with database.session() as session:
        row = await session.get(SessionRow, session_id)
        assert row is not None
        stored = (
            await session.execute(select(SessionEvent).where(SessionEvent.session_id == session_id))
        ).scalars()
        assert len(list(stored)) == 4
    latest = row.consent_state["latest"]  # type: ignore[index]
    assert set(latest) == {"recording", "ai_disclosure"}
    assert latest["recording"]["accepted"] is True
    assert latest["recording"]["at"] == 1758000002.0
    assert latest["recording"]["text_hash"] == _hash()


# ------------------------------------------------------------------ the "not recorded" note


async def _finish(
    admin_client: httpx.AsyncClient,
    service_client: httpx.AsyncClient,
    events: list[dict[str, Any]],
    recording: RecordingConfig,
) -> dict[str, Any]:
    config = json.loads(inference_config(recording=recording).model_dump_json())
    agent = await create_agent(admin_client, config=config)
    session_id = (await admin_client.post(f"/v1/agents/{agent['id']}/connect", json={})).json()["sessionId"]
    if events:
        posted = await service_client.post(
            f"/internal/v1/sessions/{session_id}/events", json={"events": events}
        )
        assert posted.status_code == 202
    response = await service_client.put(
        f"/internal/v1/sessions/{session_id}/summary",
        json={"status": "ended", "usage": {}, "transcript": []},
    )
    assert response.status_code == 204, response.text
    detail: dict[str, Any] = (await admin_client.get(f"/v1/sessions/{session_id}")).json()
    return detail


@pytest.mark.parametrize(
    ("events", "expected"),
    [([_consent(False)], NOT_RECORDED_DECLINED), ([], NOT_RECORDED_NO_ANSWER)],
    ids=["declined", "never-answered"],
)
async def test_a_consent_gated_session_that_was_not_recorded_says_why(
    admin_client: httpx.AsyncClient,
    service_client: httpx.AsyncClient,
    events: list[dict[str, Any]],
    expected: str,
) -> None:
    detail = await _finish(admin_client, service_client, events, GATED)
    assert detail["recording"]["status"] == "none"
    assert detail["recording"]["error"] == expected


async def test_an_ungated_session_gets_no_note(
    admin_client: httpx.AsyncClient, service_client: httpx.AsyncClient
) -> None:
    detail = await _finish(admin_client, service_client, [], RecordingConfig(enabled=True))
    assert detail["recording"]["error"] is None


# ------------------------------------------------------------------ workspace compliance


async def test_workspace_compliance_merges_and_validates(admin_client: httpx.AsyncClient) -> None:
    first = await admin_client.put(
        "/v1/workspaces/default", json={"settings": {"compliance": {"jurisdiction": "eu"}}}
    )
    assert first.status_code == 200, first.text
    second = await admin_client.put(
        "/v1/workspaces/default",
        json={"settings": {"compliance": {"recording_text": "May we record?", "counsel_note_ack": True}}},
    )
    assert second.json()["settings"]["compliance"] == {
        "jurisdiction": "eu",
        "recording_text": "May we record?",
        "counsel_note_ack": True,
    }


@pytest.mark.parametrize(
    "compliance",
    [{"jurisdiction": "mars"}, {"states": ["CA"]}, "eu", {"disclosure_text": "x" * 2001}],
    ids=["jurisdiction", "unknown-key", "not-an-object", "too-long"],
)
async def test_workspace_compliance_refuses_bad_values(
    admin_client: httpx.AsyncClient, compliance: Any
) -> None:
    response = await admin_client.put("/v1/workspaces/default", json={"settings": {"compliance": compliance}})
    assert response.status_code == 422, response.text


async def test_get_compliance_returns_the_presets_and_the_effective_wording(
    admin_client: httpx.AsyncClient,
) -> None:
    default = (await admin_client.get("/v1/workspaces/default/compliance")).json()
    assert default["settings"]["jurisdiction"] == "in"
    assert default["effective"]["disclosure_text"] == COMPLIANCE_PRESETS["in"].disclosure_text
    assert [p["jurisdiction"] for p in default["presets"]] == ["eu", "in", "us"]
    assert "two-party-consent" in default["presets"][2]["counsel_note"]

    await admin_client.put(
        "/v1/workspaces/default",
        json={"settings": {"compliance": {"jurisdiction": "us", "disclosure_text": "An AI is answering."}}},
    )
    changed = (await admin_client.get("/v1/workspaces/default/compliance")).json()
    assert changed["effective"] == {
        "jurisdiction": "us",
        "disclosure_text": "An AI is answering.",
        "recording_text": COMPLIANCE_PRESETS["us"].recording_text,
    }


# ------------------------------------------------------------------ resolved config


async def _resolved(
    admin_client: httpx.AsyncClient, service_client: httpx.AsyncClient
) -> ResolvedAgentConfig:
    agent = await create_agent(admin_client)
    session_id = (await admin_client.post(f"/v1/agents/{agent['id']}/connect", json={})).json()["sessionId"]
    response = await service_client.get(f"/internal/v1/sessions/{session_id}/resolved")
    assert response.status_code == 200, response.text
    return ResolvedAgentConfig.model_validate(response.json())


async def test_the_resolved_config_carries_the_workspace_wording(
    admin_client: httpx.AsyncClient, service_client: httpx.AsyncClient
) -> None:
    default = await _resolved(admin_client, service_client)
    assert default.compliance is not None
    assert default.compliance.disclosure_text == COMPLIANCE_PRESETS["in"].disclosure_text
    assert default.config.disclosure.enabled is True

    await admin_client.put(
        "/v1/workspaces/default",
        json={"settings": {"compliance": {"jurisdiction": "eu", "recording_text": "May we record?"}}},
    )
    changed = await _resolved(admin_client, service_client)
    assert changed.compliance is not None
    assert (changed.compliance.jurisdiction, changed.compliance.recording_text) == ("eu", "May we record?")


# ------------------------------------------------------------------ validators


def _issues(**overrides: Any) -> list[tuple[str, str, str]]:
    ctx = ValidationContext(config=inference_config(**overrides), jurisdiction="eu")
    return [(i.path, i.severity, i.message) for i in consent_issues(ctx)]


def test_an_agent_saved_before_v5_15_raises_no_consent_issue() -> None:
    assert _issues() == []
    assert _issues(recording=RecordingConfig(enabled=True)) == []


def test_require_consent_without_a_consent_block_warns_that_voice_still_works() -> None:
    assert _issues(recording=GATED) == [
        ("recording.require_consent", "warning", CONSENT_BLOCK_MISSING_MESSAGE)
    ]
    block = BlockSpec(id="rec", type="consent", config={"kind": "recording"})
    assert _issues(recording=GATED, panel=PanelLayout(blocks=[block])) == []


def test_require_consent_with_recording_off_warns() -> None:
    ((path, severity, _),) = _issues(recording=RecordingConfig(require_consent=True))
    assert (path, severity) == ("recording.require_consent", "warning")


def test_a_disabled_disclosure_warns_and_names_the_jurisdiction() -> None:
    ((path, severity, message),) = _issues(disclosure=DisclosureConfig(enabled=False))
    assert (path, severity) == ("disclosure.enabled", "warning")
    assert "European Union" in message and "counsel" in message


def test_a_banner_only_disclosure_without_a_banner_block_warns() -> None:
    banner = DisclosureConfig(position="banner")
    ((path, _, _),) = _issues(disclosure=banner)
    assert path == "disclosure.position"
    block = BlockSpec(id="ai", type="consent", config={"kind": "ai_disclosure"})
    assert _issues(disclosure=banner, panel=PanelLayout(blocks=[block])) == []


def test_terms_and_custom_consent_blocks_need_their_own_wording() -> None:
    blocks = [
        BlockSpec(id="t", type="consent", config={"kind": "terms"}),
        BlockSpec(id="c", type="consent", config={"kind": "custom", "text": "I agree to the survey."}),
        BlockSpec(id="r", type="consent", config={"kind": "recording"}),
    ]
    assert _issues(panel=PanelLayout(blocks=blocks)) == [
        ("panel.blocks[0].config.text", "error", "a terms or custom consent block needs its own wording")
    ]


def test_validate_runs_the_consent_checks() -> None:
    result = validate(ValidationContext(config=inference_config(disclosure=DisclosureConfig(enabled=False))))
    assert any(issue.path == "disclosure.enabled" for issue in result.issues)
