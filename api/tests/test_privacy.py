"""V5-30: the post-call privacy scrub, post-call fields in the outputs, purge on delete, validators.

Every person, card and address below is fictional (the card numbers are the
documented test numbers card networks publish; the emails are `example.com`).
"""

from __future__ import annotations

import csv
import io
import json
from collections import Counter
from typing import Any

import httpx
import pytest
import respx
from conftest import create_agent, inference_config
from lkap_contracts.agent_config import AgentConfig, PrivacyConfig, QaConfig
from lkap_contracts.common import ProviderRef
from lkap_contracts.qa import QaField
from sqlalchemy import select

from lkap_api import privacy
from lkap_api.config_service import validate_agent_config
from lkap_api.db.models import Credential, SessionAsset, SessionEvent, SessionQa
from lkap_api.db.models import Session as SessionRow
from lkap_api.db.session import Database
from lkap_api.jobs import kinds as jobs_kinds
from lkap_api.jobs.context import JobContext
from lkap_api.jobs.handlers import load_all_handlers
from lkap_api.jobs.service import JobsService
from lkap_api.privacy.fields import csv_cell, qa_fields
from lkap_api.privacy.redact import (
    CARD_TOKEN,
    EMAIL_TOKEN,
    NUMBER_TOKEN,
    luhn_valid,
    redact_text,
    scrub_value,
)
from lkap_api.settings import Settings
from lkap_api.storage.resolve import default_storage
from lkap_api.vault import Vault

CHAT_URL = "https://api.openai.com/v1/chat/completions"

#: A fictional caller. 4111 1111 1111 1111 is the network's published test card number.
TRANSCRIPT = [
    {"role": "user", "text": "Hi, this is Jane Roe, my card is 4111 1111 1111 1111.", "ts": 1.0},
    {"role": "assistant", "text": "Thanks. What email should we use?", "ts": 2.0},
    {"role": "user", "text": "jane.roe@example.com, and call me on +1 555 010 0199.", "ts": 3.0},
    {"role": "assistant", "text": "Noted. The deductible is $500.", "ts": 4.0},
]

EVENTS = [
    {"ts": 1.0, "type": "user_turn", "payload": {"text": TRANSCRIPT[0]["text"]}},
    {
        "ts": 2.5,
        "type": "tool_call_started",
        "payload": {
            "call_id": "call_4111111111111111",
            "tool": "lookup_policy",
            "args_redacted": {"email": "jane.roe@example.com"},
        },
    },
    {
        "ts": 2.6,
        "type": "tool_call_ended",
        "payload": {
            "call_id": "call_4111111111111111",
            "tool": "lookup_policy",
            "status": "ok",
            "result_preview": "Policy HLM-7730021 for jane.roe@example.com",
        },
    },
    {"ts": 3.0, "type": "user_turn", "payload": {"text": TRANSCRIPT[2]["text"]}},
]


# --------------------------------------------------------------------------- deterministic pass


def test_redact_masks_cards_emails_and_long_numbers_and_keeps_short_ones() -> None:
    counts: Counter[str] = Counter()
    text = (
        "Card 4111-1111-1111-1111, mail jane.roe@example.com, phone (555) 010-0199, $1,250 on 12 March 2026"
    )

    masked = redact_text(text, counts)

    assert masked == f"Card {CARD_TOKEN}, mail {EMAIL_TOKEN}, phone {NUMBER_TOKEN}, $1,250 on 12 March 2026"
    assert counts == {"card": 1, "email": 1, "number": 1}


def test_a_digit_run_that_fails_luhn_is_a_number_not_a_card() -> None:
    assert not luhn_valid("4111111111111112")
    assert redact_text("ref 4111 1111 1111 1112") == f"ref {NUMBER_TOKEN}"


def test_scrub_value_keeps_identifiers() -> None:
    payload = {"call_id": "call_4111111111111111", "session_id": "5550100199", "text": "4111111111111111"}

    assert scrub_value(payload) == {
        "call_id": "call_4111111111111111",
        "session_id": "5550100199",
        "text": CARD_TOKEN,
    }


# --------------------------------------------------------------------------- the job


async def _make_session(
    database: Database,
    config: AgentConfig,
    *,
    status: str = "ended",
    events: list[dict[str, Any]] | None = None,
    qa_summary: str | None = None,
) -> str:
    from lkap_api.db.models import Agent

    async with database.session() as session:
        agent = Agent(
            slug=f"privacy-{id(config)}-{status}",
            name="Demo — privacy",
            config=json.loads(config.model_dump_json()),
        )
        session.add(agent)
        await session.flush()
        row = SessionRow(
            agent_id=agent.id,
            config_version=1,
            room_name=f"room-privacy-{agent.id}",
            participant_identity="caller",
            participant_name="Caller",
            status=status,
            pipeline_mode="cascaded",
            transcript=json.loads(json.dumps(TRANSCRIPT)),
            final_ui_state={"v": 1, "blocks": {"form": {"values": {"email": "jane.roe@example.com"}}}},
        )
        session.add(row)
        await session.flush()
        import datetime as dt

        for event in events if events is not None else EVENTS:
            session.add(
                SessionEvent(
                    session_id=row.id,
                    ts=dt.datetime.fromtimestamp(float(event["ts"]), tz=dt.UTC),
                    type=str(event["type"]),
                    payload=json.loads(json.dumps(event["payload"])),
                )
            )
        if qa_summary is not None:
            session.add(SessionQa(session_id=row.id, status="done", scored_by="worker", summary=qa_summary))
        return row.id


def _ctx(database: Database, settings: Settings, http: httpx.AsyncClient) -> JobContext:
    vault = Vault(settings.master_key)
    jobs = JobsService(database=database, settings=settings, vault=vault, http_client=http)
    return JobContext(database=database, settings=settings, vault=vault, http=http, jobs=jobs)


async def _load(database: Database, session_id: str) -> tuple[SessionRow, list[SessionEvent]]:
    async with database.session() as session:
        row = (
            await session.execute(
                select(SessionRow)
                .where(SessionRow.id == session_id)
                .execution_options(lkap_cross_workspace=True)
            )
        ).scalar_one()
        events = list(
            (
                await session.execute(
                    select(SessionEvent)
                    .where(SessionEvent.session_id == session_id)
                    .order_by(SessionEvent.id)
                )
            ).scalars()
        )
        session.expunge_all()
        return row, events


def _redacted(**privacy_fields: Any) -> AgentConfig:
    return inference_config(
        privacy=PrivacyConfig.model_validate({"storage_tier": "redacted", **privacy_fields})
    )


async def test_the_scrub_job_masks_the_fixture_transcript_and_records_scrubbed_at(
    database: Database, settings: Settings
) -> None:
    """V5-30 acceptance: card numbers and emails replaced, `scrubbed_at` recorded."""
    session_id = await _make_session(database, _redacted(), qa_summary="Caller jane.roe@example.com asked.")

    async with httpx.AsyncClient() as http:
        result = await privacy.scrub_session(_ctx(database, settings, http), session_id)

    assert result.status == "scrubbed"
    # transcript + events + final panel state + QA summary
    assert result.counts == {"card": 2, "email": 6, "number": 3}
    row, events = await _load(database, session_id)
    texts = [turn["text"] for turn in row.transcript or []]
    assert texts[0] == f"Hi, this is Jane Roe, my card is {CARD_TOKEN}."
    assert texts[2] == f"{EMAIL_TOKEN}, and call me on {NUMBER_TOKEN}."
    assert texts[3] == "Noted. The deductible is $500."
    assert row.final_ui_state == {"v": 1, "blocks": {"form": {"values": {"email": EMAIL_TOKEN}}}}
    started = next(e for e in events if e.type == "tool_call_started")
    assert started.payload["args_redacted"] == {"email": EMAIL_TOKEN}  # `redacted` keeps tool payloads
    assert started.payload["call_id"] == "call_4111111111111111"
    marker = events[-1]
    assert marker.type == privacy.PRIVACY_SCRUBBED_EVENT
    assert marker.payload == {
        "tier": "redacted",
        "replaced": {"card": 2, "email": 6, "number": 3},
        "model_pass": "none",
        "tool_payloads_dropped": 0,
    }
    async with database.session() as session:
        assert await privacy.scrubbed_at(session, session_id) == marker.ts
        qa = await session.get(SessionQa, session_id)
        assert qa is not None and qa.summary == f"Caller {EMAIL_TOKEN} asked."


async def test_the_scrub_runs_once(database: Database, settings: Settings) -> None:
    session_id = await _make_session(database, _redacted())
    async with httpx.AsyncClient() as http:
        ctx = _ctx(database, settings, http)
        assert (await privacy.scrub_session(ctx, session_id)).status == "scrubbed"
        assert (await privacy.scrub_session(ctx, session_id)).status == "already_scrubbed"
    _, events = await _load(database, session_id)
    assert [e.type for e in events].count(privacy.PRIVACY_SCRUBBED_EVENT) == 1


async def test_basic_drops_the_tool_payloads(database: Database, settings: Settings) -> None:
    """V5-30 acceptance: `basic` drops tool payloads."""
    config = inference_config(privacy=PrivacyConfig(storage_tier="basic"))
    session_id = await _make_session(database, config)

    async with httpx.AsyncClient() as http:
        result = await privacy.scrub_session(_ctx(database, settings, http), session_id)

    assert result.tool_payloads_dropped == 2
    _, events = await _load(database, session_id)
    started = next(e for e in events if e.type == "tool_call_started")
    ended = next(e for e in events if e.type == "tool_call_ended")
    assert started.payload == {"call_id": "call_4111111111111111", "tool": "lookup_policy"}
    assert ended.payload == {"call_id": "call_4111111111111111", "tool": "lookup_policy", "status": "ok"}


async def test_full_tier_and_unfinished_sessions_are_left_alone(
    database: Database, settings: Settings
) -> None:
    """Compatibility: an agent saved before `privacy` (tier `full`) is never scrubbed."""
    full = await _make_session(database, inference_config())
    active = await _make_session(database, _redacted(), status="active")

    async with httpx.AsyncClient() as http:
        ctx = _ctx(database, settings, http)
        assert (await privacy.scrub_session(ctx, full)).status == "not_due"
        assert (await privacy.scrub_session(ctx, active)).status == "not_due"
        assert await privacy.enqueue_scrub_if_due(ctx.jobs, full, inference_config()) is None

    row, _ = await _load(database, full)
    assert row.transcript == TRANSCRIPT


async def _openai_key(database: Database, settings: Settings) -> str:
    async with database.session() as session:
        credential = Credential(
            provider_id="openai-llm",
            label="test key",
            ciphertext=Vault(settings.master_key).encrypt({"api_key": "sk-test-not-a-real-key"}),
            fingerprint="…test",
        )
        session.add(credential)
        await session.flush()
        return credential.id


def _model_reply(texts: list[str]) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({"texts": texts})}}]})


async def test_the_scrub_model_masks_names_through_the_fence(database: Database, settings: Settings) -> None:
    key = await _openai_key(database, settings)
    config = _redacted(scrub_model={"provider_id": "openai-llm", "credential_id": key})
    session_id = await _make_session(database, config)
    cleaned = [
        f"Hi, this is [name], my card is {CARD_TOKEN}.",
        "Thanks. What email should we use?",
        f"{EMAIL_TOKEN}, and call me on {NUMBER_TOKEN}.",
        "Noted. The deductible is $500.",
    ]

    with respx.mock:
        route = respx.post(CHAT_URL).mock(return_value=_model_reply(cleaned))
        async with httpx.AsyncClient() as http:
            result = await privacy.scrub_session(_ctx(database, settings, http), session_id)

    assert result.model_pass == "done"
    sent = json.loads(route.calls[0].request.content)
    user = sent["messages"][1]["content"]
    assert user.startswith('<untrusted source="transcript">') and user.endswith("</untrusted>")
    assert "4111" not in user  # the deterministic pass runs first
    row, events = await _load(database, session_id)
    assert (row.transcript or [])[0]["text"] == cleaned[0]
    first_turn = next(e for e in events if e.type == "user_turn")
    assert first_turn.payload["text"] == cleaned[0]


async def test_a_scrub_model_reply_of_the_wrong_shape_is_repaired_once_then_ignored(
    database: Database, settings: Settings
) -> None:
    key = await _openai_key(database, settings)
    session_id = await _make_session(
        database, _redacted(scrub_model={"provider_id": "openai-llm", "credential_id": key})
    )

    with respx.mock:
        route = respx.post(CHAT_URL).mock(return_value=_model_reply(["only one"]))
        async with httpx.AsyncClient() as http:
            result = await privacy.scrub_session(_ctx(database, settings, http), session_id)

    assert route.call_count == 2
    assert result.status == "scrubbed" and result.model_pass == "failed"
    row, _ = await _load(database, session_id)
    assert (row.transcript or [])[0]["text"] == f"Hi, this is Jane Roe, my card is {CARD_TOKEN}."


async def test_a_scrub_model_the_api_cannot_call_is_skipped(database: Database, settings: Settings) -> None:
    session_id = await _make_session(
        database, _redacted(scrub_model={"provider_id": "livekit-inference-llm"})
    )

    async with httpx.AsyncClient() as http:
        result = await privacy.scrub_session(_ctx(database, settings, http), session_id)

    assert (result.status, result.model_pass) == ("scrubbed", "unsupported")


def test_the_scrub_job_kind_is_registered() -> None:
    assert jobs_kinds.SESSION_SCRUB in load_all_handlers()


# --------------------------------------------------------------------------- routes


async def _ended_session(
    admin_client: httpx.AsyncClient, service_client: httpx.AsyncClient, config: AgentConfig
) -> str:
    agent = await create_agent(admin_client, config=json.loads(config.model_dump_json()))
    session_id = str(
        (await admin_client.post(f"/v1/agents/{agent['id']}/connect", json={})).json()["sessionId"]
    )
    response = await service_client.put(
        f"/internal/v1/sessions/{session_id}/summary",
        json={"status": "ended", "usage": {}, "transcript": TRANSCRIPT},
    )
    assert response.status_code == 204, response.text
    return session_id


async def test_scrub_route_queues_once_and_the_detail_shows_scrubbed_at(
    admin_client: httpx.AsyncClient, service_client: httpx.AsyncClient
) -> None:
    session_id = await _ended_session(admin_client, service_client, _redacted())

    first = await admin_client.post(f"/v1/sessions/{session_id}/scrub")
    detail = (await admin_client.get(f"/v1/sessions/{session_id}")).json()
    second = await admin_client.post(f"/v1/sessions/{session_id}/scrub")

    assert first.status_code == 202 and first.json()["status"] == "queued"
    assert detail["scrubbed_at"] is not None
    assert detail["transcript"][2]["text"] == f"{EMAIL_TOKEN}, and call me on {NUMBER_TOKEN}."
    assert second.json() == {
        "status": "already_scrubbed",
        "job_id": None,
        "scrubbed_at": detail["scrubbed_at"],
    }


async def test_scrub_route_refuses_a_full_tier(
    admin_client: httpx.AsyncClient, service_client: httpx.AsyncClient
) -> None:
    session_id = await _ended_session(admin_client, service_client, inference_config())

    response = await admin_client.post(f"/v1/sessions/{session_id}/scrub")
    detail = (await admin_client.get(f"/v1/sessions/{session_id}")).json()

    assert response.status_code == 409
    assert response.json()["error"]["details"]["reason"] == "storage_tier_full"
    assert detail["scrubbed_at"] is None


FIELDS = [
    QaField(name="claim_type", type="select", options=["auto", "home"]),
    QaField(name="injury", type="boolean"),
    QaField(name="status", description="collides with a fixed column"),
]


async def _qa_with_fields(service_client: httpx.AsyncClient, session_id: str, fields: dict[str, Any]) -> None:
    response = await service_client.put(
        f"/internal/v1/sessions/{session_id}/qa",
        json={"status": "done", "score": 7, "sentiment": "neutral", "raw": {"score": 7, "fields": fields}},
    )
    assert response.status_code == 204, response.text


async def test_the_fields_reach_the_session_detail(
    admin_client: httpx.AsyncClient, service_client: httpx.AsyncClient
) -> None:
    config = inference_config(qa=QaConfig(enabled=True, fields=FIELDS))
    session_id = await _ended_session(admin_client, service_client, config)
    await _qa_with_fields(service_client, session_id, {"claim_type": "home", "injury": False, "status": None})

    qa = (await admin_client.get(f"/v1/sessions/{session_id}")).json()["qa"]

    assert qa["fields"] == {"claim_type": "home", "injury": False, "status": None}


async def test_the_csv_has_one_column_per_field(
    admin_client: httpx.AsyncClient, service_client: httpx.AsyncClient
) -> None:
    """V5-30 acceptance: the sessions CSV has one column per post-call field."""
    config = inference_config(qa=QaConfig(enabled=True, fields=FIELDS))
    session_id = await _ended_session(admin_client, service_client, config)
    await _qa_with_fields(
        service_client, session_id, {"claim_type": "auto", "injury": True, "status": "=1+1"}
    )
    agent_id = (await admin_client.get(f"/v1/sessions/{session_id}")).json()["agent_id"]

    response = await admin_client.get("/v1/sessions/export.csv", params={"agent_id": agent_id})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "attachment" in response.headers["content-disposition"]
    rows = list(csv.reader(io.StringIO(response.text)))
    header, row = rows[0], rows[1]
    assert header[-3:] == ["claim_type", "injury", "field_status"]
    values = dict(zip(header, row, strict=True))
    assert values["session_id"] == session_id
    assert (values["claim_type"], values["injury"], values["field_status"]) == ("auto", "true", "'=1+1")
    assert values["qa_score"] == "7"


async def test_the_csv_without_fields_has_only_the_fixed_columns(
    admin_client: httpx.AsyncClient, service_client: httpx.AsyncClient
) -> None:
    await _ended_session(admin_client, service_client, inference_config())

    rows = list(csv.reader(io.StringIO((await admin_client.get("/v1/sessions/export.csv")).text)))

    assert len(rows[0]) == 13 and len(rows) == 2


def test_fields_helpers() -> None:
    assert qa_fields(None) == {} and qa_fields({"fields": "x"}) == {}
    assert qa_fields({"fields": {"a": 1}}) == {"a": 1}
    assert [csv_cell(v) for v in (None, True, 2.5, "@SUM(A1)", "plain")] == [
        "",
        "true",
        "2.5",
        "'@SUM(A1)",
        "plain",
    ]


async def test_deleting_a_session_removes_its_files_at_once(
    admin_client: httpx.AsyncClient, service_client: httpx.AsyncClient, database: Database, settings: Settings
) -> None:
    """S5-36: purge on delete, not at the end of the recording retention."""
    session_id = await _ended_session(admin_client, service_client, inference_config())
    storage = default_storage(settings)
    key = f"sessions/{session_id}/fixture.png"
    await storage.put(key, b"\x89PNG fictional", content_type="image/png")
    async with database.session() as session:
        row = (
            await session.execute(
                select(SessionRow)
                .where(SessionRow.id == session_id)
                .execution_options(lkap_cross_workspace=True)
            )
        ).scalar_one()
        session.add(
            SessionAsset(
                session_id=session_id,
                workspace_id=row.workspace_id,
                kind="upload",
                name="photo.png",
                mime="image/png",
                size=14,
                storage_key=key,
                sha256="0" * 64,
            )
        )

    response = await admin_client.delete(f"/v1/sessions/{session_id}")

    assert response.status_code == 204
    assert not await storage.exists(key)
    async with database.session() as session:
        remaining = (
            await session.execute(
                select(SessionAsset)
                .where(SessionAsset.session_id == session_id)
                .execution_options(lkap_cross_workspace=True)
            )
        ).all()
    assert remaining == []


# --------------------------------------------------------------------------- validators


def _issues(config: AgentConfig, credentials: dict[str, str] | None = None) -> list[tuple[str, str]]:
    result = validate_agent_config(config, credential_providers=credentials or {})
    return [
        (issue.path, issue.severity)
        for issue in result.issues
        if issue.path.startswith(("privacy", "qa.fields"))
    ]


def test_stt_redact_on_a_provider_without_the_capability_warns() -> None:
    """V5-30 acceptance: a provider without the capability warns at validation."""
    config = inference_config(privacy=PrivacyConfig(stt_redact=["pci"]))
    assert _issues(config) == [("privacy.stt_redact", "warning")]


def test_stt_redact_on_deepgram_is_clean() -> None:
    config = inference_config(privacy=PrivacyConfig(stt_redact=["pci"]))
    pipeline = config.pipeline.model_copy(
        update={"stt": ProviderRef(provider_id="deepgram-stt", credential_id="cred-dg")}
    )
    assert _issues(config.model_copy(update={"pipeline": pipeline}), {"cred-dg": "deepgram-stt"}) == []


@pytest.mark.parametrize(
    ("privacy_config", "expected"),
    [
        ({"scrub_model": {"provider_id": "openai-llm", "credential_id": "k"}}, "warning"),  # tier full
        ({"storage_tier": "redacted", "scrub_model": {"provider_id": "livekit-inference-llm"}}, "warning"),
        ({"storage_tier": "redacted", "scrub_model": {"provider_id": "openai-llm"}}, "warning"),  # no key
        (
            {
                "storage_tier": "redacted",
                "scrub_model": {"provider_id": "openai-llm", "credential_id": "gone"},
            },
            "error",
        ),
        ({"storage_tier": "redacted", "scrub_model": {"provider_id": "deepgram-stt"}}, "error"),
    ],
)
def test_scrub_model_findings(privacy_config: dict[str, Any], expected: str) -> None:
    config = inference_config(privacy=PrivacyConfig.model_validate(privacy_config))
    assert _issues(config, {"k": "openai-llm"}) == [("privacy.scrub_model", expected)]


def test_a_usable_scrub_model_is_clean() -> None:
    config = _redacted(scrub_model={"provider_id": "openai-llm", "credential_id": "k"})
    assert _issues(config, {"k": "openai-llm"}) == []


def test_fields_without_qa_warn() -> None:
    config = inference_config(qa=QaConfig(fields=[QaField(name="injury", type="boolean")]))
    assert _issues(config) == [("qa.fields", "warning")]
    assert _issues(inference_config(qa=QaConfig(enabled=True, fields=[QaField(name="injury")]))) == []


def test_defaults_raise_no_privacy_finding() -> None:
    """Compatibility: an agent saved before V5-30 validates exactly as before."""
    assert _issues(inference_config()) == []


def test_scrub_value_keeps_links() -> None:
    """A signed download link's `exp=` timestamp must survive the scrub of the final panel state."""
    state = {"document": {"url": "https://example.com/a?exp=1758000000&sig=abc", "note": "call 5550100199"}}

    assert scrub_value(state) == {
        "document": {"url": "https://example.com/a?exp=1758000000&sig=abc", "note": f"call {NUMBER_TOKEN}"}
    }
