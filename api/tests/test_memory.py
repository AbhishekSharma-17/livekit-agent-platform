"""V5-40: caller memory — identity, recall, remember, forget, purge, retention, the migration.

Every caller below is fictional; the phone numbers are in the reserved 555-01xx
range and the emails use `example.com`. The memory backend is
`tests/fakes/memory.FakeMemoryStore` (it fails any call that is handed something
other than a 64-hex subject id); the Mem0 integration test at the end runs only
with the `lkap-api[memory]` extra installed, with fakes for the model and the
embedder (no vendor is called).
"""

from __future__ import annotations

import asyncio
import datetime as dt
import importlib.util
import json
import os
import shutil
import sqlite3
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from alembic.config import Config
from conftest import create_agent, inference_config
from fakes.memory import FakeMemoryStore
from lkap_contracts.agent_config import AgentConfig, MemoryConfig, PrivacyConfig
from lkap_contracts.api_models import MEMORY_FORGOTTEN_EVENT, MEMORY_RECALLED_EVENT, MEMORY_STORED_EVENT
from lkap_contracts.common import ProviderRef
from sqlalchemy import select

from alembic import command
from lkap_api.config_service import validate_agent_config
from lkap_api.db.constants import DEFAULT_WORKSPACE_ID
from lkap_api.db.models import Agent, Credential, MemoryEvent, MemorySubject, SessionEvent, utcnow
from lkap_api.db.models import Session as SessionRow
from lkap_api.db.session import Database
from lkap_api.jobs import kinds as jobs_kinds
from lkap_api.jobs.handlers import load_all_handlers
from lkap_api.livekit_tokens import new_participant_identity
from lkap_api.memory import identity as memory_identity
from lkap_api.memory.identity import (
    GENERATED_IDENTITY_RE,
    MEMORY_KEY_PROVIDER_ID,
    WORKSPACE_SCOPE,
    caller_identity,
    subject_id,
    workspace_key,
)
from lkap_api.memory.service import (
    EXTRACTION_PROVIDERS,
    remember_session,
    resolve_extraction_model,
    sweep_memory_retention,
    transcript_messages,
)
from lkap_api.memory.store import MemoryUnavailableError, set_memory_store
from lkap_api.memory.validation import NO_MODEL_MESSAGE
from lkap_api.privacy.redact import EMAIL_TOKEN, NUMBER_TOKEN
from lkap_api.settings import Settings
from lkap_api.vault import Vault

CALLER = "+15550100123"
OTHER_CALLER = "+15550100188"
TRANSCRIPT = [
    {"role": "user", "text": "Hi, I'm Ada, write to ada@example.com.", "ts": 1.0},
    {"role": "assistant", "text": "Thanks Ada, noted.", "ts": 2.0},
    {"role": "user", "text": "My policy number is 7730021, I prefer mornings.", "ts": 3.0},
]


@pytest.fixture
def store() -> Iterator[FakeMemoryStore]:
    fake = FakeMemoryStore()
    set_memory_store(fake)
    try:
        yield fake
    finally:
        set_memory_store(None)


@pytest.fixture
def no_backend(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """The `lkap-api[memory]` extra is not installed."""

    def _unavailable(_settings: Settings) -> Any:
        raise MemoryUnavailableError("caller memory needs the `lkap-api[memory]` extra")

    set_memory_store(None)
    monkeypatch.setattr("lkap_api.memory.service.get_memory_store", _unavailable)
    yield


def _memory_config(**memory: Any) -> AgentConfig:
    return inference_config(memory=MemoryConfig.model_validate({"enabled": True, "verbatim": True, **memory}))


async def _make_session(
    database: Database,
    config: AgentConfig,
    *,
    channel: str = "web",
    identity: str = "customer-42",
    caller: dict[str, Any] | None = None,
    status: str = "ended",
    agent_id: str | None = None,
    transcript: list[dict[str, Any]] | None = None,
) -> tuple[str, str]:
    async with database.session() as db:
        if agent_id is None:
            agent = Agent(
                slug=f"memory-{os.urandom(4).hex()}",
                name="Demo — memory",
                config=json.loads(config.model_dump_json()),
            )
            db.add(agent)
            await db.flush()
            agent_id = agent.id
        row = SessionRow(
            agent_id=agent_id,
            config_version=1,
            room_name=f"room-memory-{os.urandom(4).hex()}",
            participant_identity=identity,
            participant_name="Caller",
            status=status,
            channel=channel,
            caller=caller,
            pipeline_mode="cascaded",
            transcript=json.loads(json.dumps(TRANSCRIPT if transcript is None else transcript)),
        )
        db.add(row)
        await db.flush()
        return row.id, agent_id


async def _events(database: Database, session_id: str, kind: str | None = None) -> list[SessionEvent]:
    async with database.session() as db:
        stmt = select(SessionEvent).where(SessionEvent.session_id == session_id).order_by(SessionEvent.id)
        if kind is not None:
            stmt = stmt.where(SessionEvent.type == kind)
        rows = list((await db.execute(stmt)).scalars())
        db.expunge_all()
        return rows


async def _key(database: Database, settings: Settings) -> bytes:
    async with database.session() as db:
        key = await workspace_key(db, Vault(settings.master_key), DEFAULT_WORKSPACE_ID, create=True)
    assert key is not None
    return key


async def _subject(database: Database, settings: Settings, identity: str) -> str:
    return subject_id(await _key(database, settings), identity)


async def _recall(service_client: httpx.AsyncClient, session_id: str, **extra: Any) -> dict[str, Any]:
    response = await service_client.post(
        "/internal/v1/memory/recall", json={"session_id": session_id, **extra}
    )
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


# ------------------------------------------------------------------------------ identity


def test_subject_id_is_a_keyed_hex_hash_and_a_rotated_key_changes_it() -> None:
    first = subject_id(b"k" * 32, CALLER)

    assert memory_identity.SUBJECT_ID_RE.match(first)
    assert first == subject_id(b"k" * 32, CALLER)
    assert first != subject_id(b"r" * 32, CALLER)  # rotated key: a different id, old memories orphaned
    assert first != subject_id(b"k" * 32, OTHER_CALLER)
    assert CALLER not in first and CALLER.lstrip("+") not in first


def test_the_platform_minted_identity_is_recognised_as_anonymous() -> None:
    assert GENERATED_IDENTITY_RE.match(new_participant_identity())


def _row(**fields: Any) -> SessionRow:
    base: dict[str, Any] = {"channel": "web", "participant_identity": "customer-42", "caller": None}
    return SessionRow(**{**base, **fields})


@pytest.mark.parametrize(
    ("fields", "hint", "expected"),
    [
        (
            {"channel": "sip_in", "caller": {"direction": "inbound", "from": CALLER, "to": "+15550100999"}},
            None,
            CALLER,
        ),
        (
            {"channel": "sip_out", "caller": {"direction": "outbound", "from": "+15550100999", "to": CALLER}},
            None,
            CALLER,
        ),
        ({"channel": "sip_in", "caller": None, "participant_identity": "sip_in-caller"}, CALLER, CALLER),
        ({"channel": "sip_in", "caller": {"from": "anonymous"}}, None, None),
        ({"channel": "web"}, None, "customer-42"),
        ({"channel": "test", "participant_identity": " customer-42 "}, None, "customer-42"),
        ({"channel": "web", "participant_identity": "user-0a1b2c3d"}, None, None),
        ({"channel": "api", "participant_identity": "api-caller"}, None, None),
        ({"channel": "web", "participant_identity": ""}, None, None),
        ({"channel": "web"}, CALLER, "customer-42"),  # the hint only ever applies to phone calls
    ],
)
def test_caller_identity_rules(fields: dict[str, Any], hint: str | None, expected: str | None) -> None:
    assert caller_identity(_row(**fields), caller_e164=hint) == expected


async def test_the_memory_key_is_created_once_and_kept_in_the_vault(
    database: Database, settings: Settings
) -> None:
    first = await _key(database, settings)
    again = await _key(database, settings)

    assert first == again and len(first) == 32
    async with database.session() as db:
        rows = list(
            (
                await db.execute(select(Credential).where(Credential.provider_id == MEMORY_KEY_PROVIDER_ID))
            ).scalars()
        )
    assert len(rows) == 1
    assert first.hex() not in rows[0].fingerprint
    assert Vault(settings.master_key).decrypt(rows[0].ciphertext) == {"key": first.hex()}


# -------------------------------------------------------------------------------- recall


async def test_recall_with_memory_off_makes_no_memory_call(
    database: Database, service_client: httpx.AsyncClient, store: FakeMemoryStore
) -> None:
    """Compatibility: an agent saved before V5-40 (memory off) never touches the memory."""
    session_id, _ = await _make_session(database, inference_config(), status="active")

    body = await _recall(service_client, session_id)

    assert body == {"status": "disabled", "memories": [], "remember": False}
    assert store.recall_calls == [] and store.remember_calls == []
    assert await _events(database, session_id, MEMORY_RECALLED_EVENT) == []


async def test_recall_returns_the_callers_memories_newest_first_and_records_them(
    database: Database, settings: Settings, service_client: httpx.AsyncClient, store: FakeMemoryStore
) -> None:
    session_id, agent_id = await _make_session(
        database,
        _memory_config(),
        channel="sip_in",
        caller={"direction": "inbound", "from": CALLER, "to": "+15550100999"},
        status="active",
    )
    subject = await _subject(database, settings, CALLER)
    store.seed(subject, agent_id, "Prefers mornings.", "Has a dog called Rex.")

    body = await _recall(service_client, session_id)

    assert body == {
        "status": "recalled",
        "memories": ["Has a dog called Rex.", "Prefers mornings."],
        "remember": True,
    }
    assert store.recall_calls == [(subject, agent_id)]  # hex id only; the fake checks it
    [event] = await _events(database, session_id, MEMORY_RECALLED_EVENT)
    assert event.payload == {
        "status": "recalled",
        "count": 2,
        "memories": ["Has a dog called Rex.", "Prefers mornings."],
        "forgotten": False,
    }
    async with database.session() as db:
        audit = (
            await db.execute(select(MemoryEvent).where(MemoryEvent.session_id == session_id))
        ).scalar_one()
    assert (audit.kind, audit.count, audit.subject_id) == ("recalled", 2, subject)


async def test_the_raw_phone_number_never_reaches_the_store_or_the_tables(
    database: Database, settings: Settings, service_client: httpx.AsyncClient, store: FakeMemoryStore
) -> None:
    session_id, _ = await _make_session(
        database,
        _memory_config(),
        channel="sip_in",
        caller={"direction": "inbound", "from": CALLER, "to": "+15550100999"},
    )
    await _recall(service_client, session_id)
    await remember_session(database, Vault(settings.master_key), settings, session_id)

    assert store.remember_calls and all(CALLER not in call.subject_id for call in store.remember_calls)
    async with database.session() as db:
        subjects = list((await db.execute(select(MemorySubject))).scalars())
        audits = list((await db.execute(select(MemoryEvent))).scalars())
    assert subjects and all(CALLER not in row.subject_id for row in subjects)
    assert all(CALLER not in row.subject_id for row in audits)


async def test_workspace_scope_shares_memories_across_agents(
    database: Database, settings: Settings, service_client: httpx.AsyncClient, store: FakeMemoryStore
) -> None:
    session_id, _ = await _make_session(database, _memory_config(scope="workspace"), status="active")
    subject = await _subject(database, settings, "customer-42")
    store.seed(subject, WORKSPACE_SCOPE, "Shared across agents.")

    body = await _recall(service_client, session_id)

    assert body["memories"] == ["Shared across agents."]
    assert store.recall_calls == [(subject, WORKSPACE_SCOPE)]


async def test_recall_masks_memories_when_the_storage_tier_is_not_full(
    database: Database, settings: Settings, service_client: httpx.AsyncClient, store: FakeMemoryStore
) -> None:
    config = _memory_config().model_copy(update={"privacy": PrivacyConfig(storage_tier="redacted")})
    session_id, agent_id = await _make_session(database, config, status="active")
    store.seed(await _subject(database, settings, "customer-42"), agent_id, "Email is ada@example.com.")

    body = await _recall(service_client, session_id)

    assert body["memories"] == [f"Email is {EMAIL_TOKEN}."]


@pytest.mark.parametrize("identity", ["user-0a1b2c3d", "web-caller"])
async def test_an_anonymous_caller_is_not_remembered(
    identity: str, database: Database, service_client: httpx.AsyncClient, store: FakeMemoryStore
) -> None:
    session_id, _ = await _make_session(database, _memory_config(), identity=identity, status="active")

    body = await _recall(service_client, session_id)

    assert body == {"status": "no_identity", "memories": [], "remember": False}
    assert store.recall_calls == []


async def test_a_phone_session_uses_the_workers_number_when_the_row_has_none(
    database: Database, settings: Settings, service_client: httpx.AsyncClient, store: FakeMemoryStore
) -> None:
    session_id, agent_id = await _make_session(
        database, _memory_config(), channel="sip_in", identity="sip_in-caller", status="active"
    )
    subject = await _subject(database, settings, CALLER)
    store.seed(subject, agent_id, "Calls about the roof claim.")

    body = await _recall(service_client, session_id, caller_e164=CALLER)

    assert body["memories"] == ["Calls about the roof claim."]


async def test_recall_without_the_backend_says_unavailable(
    database: Database, service_client: httpx.AsyncClient, no_backend: None
) -> None:
    session_id, _ = await _make_session(database, _memory_config(), status="active")

    body = await _recall(service_client, session_id)

    assert body == {"status": "unavailable", "memories": [], "remember": False}
    [event] = await _events(database, session_id, MEMORY_RECALLED_EVENT)
    assert event.payload["status"] == "unavailable"


async def test_a_failing_or_slow_backend_never_blocks_the_session(
    database: Database,
    settings: Settings,
    service_client: httpx.AsyncClient,
    store: FakeMemoryStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_id, _ = await _make_session(database, _memory_config(), status="active")
    await _key(database, settings)
    monkeypatch.setattr("lkap_api.memory.service.RECALL_TIMEOUT_S", 0.05)

    async def _slow(*_args: Any, **_kwargs: Any) -> list[Any]:
        await asyncio.sleep(1)
        return []

    monkeypatch.setattr(store, "recall", _slow)

    body = await _recall(service_client, session_id)

    assert body["status"] == "failed" and body["memories"] == []


async def test_recall_rejects_a_non_e164_hint(service_client: httpx.AsyncClient) -> None:
    response = await service_client.post(
        "/internal/v1/memory/recall", json={"session_id": "s1", "caller_e164": "555-0100"}
    )
    assert response.status_code == 422


async def test_recall_needs_the_service_token(client: httpx.AsyncClient) -> None:
    response = await client.post("/internal/v1/memory/recall", json={"session_id": "s1"})
    assert response.status_code in (401, 403)


# ------------------------------------------------------------------------------ remember


async def test_the_summary_enqueues_the_write_and_it_stores_once(
    admin_client: httpx.AsyncClient,
    service_client: httpx.AsyncClient,
    database: Database,
    settings: Settings,
    store: FakeMemoryStore,
) -> None:
    agent = await create_agent(admin_client, config=json.loads(_memory_config().model_dump_json()))
    connect = await admin_client.post(
        f"/v1/agents/{agent['id']}/connect", json={"participant_identity": "customer-42"}
    )
    session_id = str(connect.json()["sessionId"])

    response = await service_client.put(
        f"/internal/v1/sessions/{session_id}/summary",
        json={"status": "ended", "usage": {}, "transcript": TRANSCRIPT},
    )
    again = await remember_session(database, Vault(settings.master_key), settings, session_id)

    assert response.status_code == 204
    assert again is None  # already stored: no second write
    [call] = store.remember_calls
    assert call.model is None  # verbatim: no model call
    assert [m.role for m in call.messages] == ["user", "user"]  # verbatim keeps the caller's lines only
    [event] = await _events(database, session_id, MEMORY_STORED_EVENT)
    assert event.payload["status"] == "stored" and event.payload["count"] == 2
    async with database.session() as db:
        row = (await db.execute(select(MemorySubject))).scalar_one()
    assert row.scope_key == agent["id"]
    assert row.retention_until - row.last_seen_at == dt.timedelta(days=90)


async def test_a_summary_with_memory_off_enqueues_nothing(
    admin_client: httpx.AsyncClient,
    service_client: httpx.AsyncClient,
    database: Database,
    store: FakeMemoryStore,
) -> None:
    agent = await create_agent(admin_client)
    session_id = str(
        (
            await admin_client.post(f"/v1/agents/{agent['id']}/connect", json={"participant_identity": "c-1"})
        ).json()["sessionId"]
    )

    await service_client.put(
        f"/internal/v1/sessions/{session_id}/summary",
        json={"status": "ended", "usage": {}, "transcript": TRANSCRIPT},
    )

    assert store.remember_calls == []
    assert await _events(database, session_id, MEMORY_STORED_EVENT) == []


async def test_the_write_masks_the_transcript_when_the_tier_is_not_full(
    database: Database, settings: Settings, store: FakeMemoryStore
) -> None:
    config = _memory_config().model_copy(update={"privacy": PrivacyConfig(storage_tier="redacted")})
    session_id, _ = await _make_session(database, config)

    await remember_session(database, Vault(settings.master_key), settings, session_id)

    [call] = store.remember_calls
    assert call.messages[0].content == f"Hi, I'm Ada, write to {EMAIL_TOKEN}."
    assert call.messages[1].content == f"My policy number is {NUMBER_TOKEN}, I prefer mornings."
    [event] = await _events(database, session_id, MEMORY_STORED_EVENT)
    assert "ada@example.com" not in json.dumps(event.payload)


async def _openrouter_credential(database: Database, settings: Settings) -> str:
    async with database.session() as db:
        row = Credential(
            provider_id="openrouter-llm",
            label="Demo — OpenRouter",
            ciphertext=Vault(settings.master_key).encrypt({"api_key": "sk-or-test-0000"}),
            fingerprint="…0000",
        )
        db.add(row)
        await db.flush()
        return row.id


async def test_the_extraction_model_is_the_agents_llm_at_the_registry_base_url(
    database: Database, settings: Settings, store: FakeMemoryStore
) -> None:
    credential_id = await _openrouter_credential(database, settings)
    llm = ProviderRef(
        provider_id="openrouter-llm",
        model="openai/gpt-4o-mini",
        credential_id=credential_id,
        fields={"base_url": "http://169.254.169.254/v1"},  # never used for memory
    )
    config = _memory_config(verbatim=False)
    config = config.model_copy(update={"pipeline": config.pipeline.model_copy(update={"llm": llm})})
    session_id, _ = await _make_session(database, config)

    await remember_session(database, Vault(settings.master_key), settings, session_id)

    [call] = store.remember_calls
    assert call.model is not None
    assert call.model.base_url == EXTRACTION_PROVIDERS["openrouter-llm"]
    assert (call.model.model, call.model.api_key) == ("openai/gpt-4o-mini", "sk-or-test-0000")
    assert "sk-or-test-0000" not in repr(call.model)
    assert [m.role for m in call.messages] == ["user", "assistant", "user"]


async def test_without_a_callable_model_the_write_is_skipped_with_a_reason(
    database: Database, settings: Settings, store: FakeMemoryStore
) -> None:
    session_id, _ = await _make_session(database, _memory_config(verbatim=False))

    event = await remember_session(database, Vault(settings.master_key), settings, session_id)

    assert event is not None and event.status == "skipped"
    assert event.reason is not None and "OpenAI or OpenRouter" in event.reason
    assert store.remember_calls == []


@pytest.mark.parametrize(
    ("identity", "transcript", "reason"),
    [
        ("user-0a1b2c3d", TRANSCRIPT, "no caller identity"),
        ("customer-42", [], "nothing was said"),
    ],
)
async def test_the_write_is_skipped_without_identity_or_words(
    identity: str,
    transcript: list[dict[str, Any]],
    reason: str,
    database: Database,
    settings: Settings,
    store: FakeMemoryStore,
) -> None:
    session_id, _ = await _make_session(database, _memory_config(), identity=identity, transcript=transcript)

    event = await remember_session(database, Vault(settings.master_key), settings, session_id)

    assert event is not None and (event.status, event.reason) == ("skipped", reason)
    assert store.remember_calls == []


async def test_a_failed_write_is_recorded_and_keeps_the_subject_row(
    database: Database, settings: Settings, store: FakeMemoryStore
) -> None:
    store.fail_with = RuntimeError("backend down")
    session_id, _ = await _make_session(database, _memory_config())

    event = await remember_session(database, Vault(settings.master_key), settings, session_id)

    assert event is not None and event.status == "failed"
    async with database.session() as db:
        assert (await db.execute(select(MemorySubject))).scalar_one() is not None


def test_the_memory_job_kinds_are_registered() -> None:
    kinds = load_all_handlers()
    assert jobs_kinds.MEMORY_REMEMBER in kinds and jobs_kinds.MEMORY_PURGE in kinds


def test_transcript_messages_skip_empty_and_unknown_turns() -> None:
    turns = [
        {"role": "user", "text": "  "},
        {"role": "system", "text": "x"},
        {"role": "user", "text": "a\nb"},
        "junk",
    ]
    assert [m.content for m in transcript_messages(turns, masked=False, callers_only=False)] == ["a b"]


# ------------------------------------------------------------------------ forget / purge


async def _remembered(
    database: Database, settings: Settings, service_client: httpx.AsyncClient, identity: str = "customer-42"
) -> tuple[str, str]:
    session_id, _ = await _make_session(database, _memory_config(), identity=identity)
    await _recall(service_client, session_id)
    await remember_session(database, Vault(settings.master_key), settings, session_id)
    return session_id, await _subject(database, settings, identity)


async def test_the_session_memory_route_shows_what_was_recalled_and_stored(
    database: Database,
    settings: Settings,
    admin_client: httpx.AsyncClient,
    service_client: httpx.AsyncClient,
    store: FakeMemoryStore,
) -> None:
    session_id, subject = await _remembered(database, settings, service_client)

    body = (await admin_client.get(f"/v1/sessions/{session_id}/memory")).json()

    assert body["enabled"] is True and body["subject_id"] == subject
    assert (body["recall_status"], body["recalled"]) == ("empty", [])
    assert body["store_status"] == "stored"
    assert body["stored"] == [
        "Hi, I'm Ada, write to ada@example.com.",
        "My policy number is 7730021, I prefer mornings.",
    ]
    assert body["forgotten_at"] is None


async def test_forget_removes_the_subject_its_memories_and_the_copies_on_sessions(
    database: Database,
    settings: Settings,
    admin_client: httpx.AsyncClient,
    service_client: httpx.AsyncClient,
    store: FakeMemoryStore,
) -> None:
    session_id, subject = await _remembered(database, settings, service_client)
    other_session, other = await _remembered(database, settings, service_client, identity="customer-77")
    assert store.count() == 4

    response = await admin_client.delete(f"/v1/memory/subjects/{subject}")

    assert response.status_code == 200, response.text
    assert response.json() == {"subject_id": subject, "forgotten": True, "sessions_updated": 1}
    assert store.count() == 2  # the other caller keeps theirs
    detail = (await admin_client.get(f"/v1/sessions/{session_id}/memory")).json()
    assert detail["stored"] == [] and detail["forgotten_at"] is not None
    [forgotten] = await _events(database, session_id, MEMORY_FORGOTTEN_EVENT)
    assert forgotten.payload == {"reason": "caller"}
    async with database.session() as db:
        remaining = list((await db.execute(select(MemorySubject.subject_id))).scalars())
    assert remaining == [other]
    assert (await admin_client.get(f"/v1/sessions/{other_session}/memory")).json()["stored"]
    # A third session of the forgotten caller knows nothing.
    third, _ = await _make_session(database, _memory_config(), status="active")
    assert (await _recall(service_client, third))["memories"] == []


async def test_forget_an_unknown_subject_is_a_404(
    admin_client: httpx.AsyncClient, store: FakeMemoryStore
) -> None:
    assert (await admin_client.delete(f"/v1/memory/subjects/{'a' * 64}")).status_code == 404
    assert (await admin_client.delete("/v1/memory/subjects/not-hex")).status_code == 404


async def test_forget_without_the_backend_deletes_nothing(
    database: Database,
    settings: Settings,
    admin_client: httpx.AsyncClient,
    service_client: httpx.AsyncClient,
    store: FakeMemoryStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, subject = await _remembered(database, settings, service_client)

    def _unavailable(_settings: Settings) -> Any:
        raise MemoryUnavailableError("no extra")

    monkeypatch.setattr("lkap_api.memory.service.get_memory_store", _unavailable)
    response = await admin_client.delete(f"/v1/memory/subjects/{subject}")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "memory_unavailable"
    assert store.count() == 2
    async with database.session() as db:
        assert (await db.execute(select(MemorySubject))).scalar_one().subject_id == subject


async def test_purge_needs_confirm(admin_client: httpx.AsyncClient) -> None:
    assert (await admin_client.post("/v1/memory/purge", json={})).status_code == 422


async def test_purge_fans_out_and_a_new_key_orphans_every_old_id(
    database: Database,
    settings: Settings,
    admin_client: httpx.AsyncClient,
    service_client: httpx.AsyncClient,
    store: FakeMemoryStore,
) -> None:
    session_id, subject = await _remembered(database, settings, service_client)
    _, other = await _remembered(database, settings, service_client, identity="customer-77")

    response = await admin_client.post("/v1/memory/purge", json={"confirm": True})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "queued" and body["subjects"] == 2 and body["job_id"]
    assert store.purge_calls == [sorted([subject, other])]
    assert store.count() == 0
    detail = (await admin_client.get(f"/v1/sessions/{session_id}/memory")).json()
    assert detail["stored"] == [] and detail["forgotten_at"] is not None
    async with database.session() as db:
        assert list((await db.execute(select(MemorySubject))).scalars()) == []
        keys = list(
            (
                await db.execute(select(Credential).where(Credential.provider_id == MEMORY_KEY_PROVIDER_ID))
            ).scalars()
        )
    assert keys == []
    assert await _subject(database, settings, "customer-42") != subject
    again = await admin_client.post("/v1/memory/purge", json={"confirm": True})
    assert again.json()["status"] in {"queued", "nothing_to_purge"}


async def test_purge_with_nothing_stored(admin_client: httpx.AsyncClient, store: FakeMemoryStore) -> None:
    body = (await admin_client.post("/v1/memory/purge", json={"confirm": True})).json()
    assert body == {"status": "nothing_to_purge", "subjects": 0, "job_id": None}


# ------------------------------------------------------------------------------ retention


async def test_retention_forgets_expired_subjects_only(
    database: Database, settings: Settings, service_client: httpx.AsyncClient, store: FakeMemoryStore
) -> None:
    old_session, old = await _remembered(database, settings, service_client)
    _, fresh = await _remembered(database, settings, service_client, identity="customer-77")
    async with database.session() as db:
        row = (await db.execute(select(MemorySubject).where(MemorySubject.subject_id == old))).scalar_one()
        row.retention_until = utcnow() - dt.timedelta(minutes=1)

    swept = await sweep_memory_retention(database, settings)

    assert swept == 1
    async with database.session() as db:
        left = list((await db.execute(select(MemorySubject.subject_id))).scalars())
        audit = list(
            (await db.execute(select(MemoryEvent.kind).where(MemoryEvent.subject_id == old))).scalars()
        )
    assert left == [fresh]
    assert "expired" in audit
    [forgotten] = await _events(database, old_session, MEMORY_FORGOTTEN_EVENT)
    assert forgotten.payload == {"reason": "retention"}
    assert await sweep_memory_retention(database, settings) == 0


async def test_retention_without_the_backend_keeps_the_rows(
    database: Database, settings: Settings, no_backend: None
) -> None:
    async with database.session() as db:
        db.add(
            MemorySubject(
                subject_id="b" * 64,
                scope_key="workspace",
                retention_until=utcnow() - dt.timedelta(days=1),
            )
        )

    assert await sweep_memory_retention(database, settings) == 0


# ----------------------------------------------------------------------------- validation


def _memory_issues(config: AgentConfig, credentials: dict[str, str] | None = None) -> list[str]:
    result = validate_agent_config(config, credential_providers=credentials or {})
    return [issue.message for issue in result.issues if issue.path.startswith("memory")]


def test_memory_on_without_a_callable_model_warns() -> None:
    assert NO_MODEL_MESSAGE in _memory_issues(_memory_config(verbatim=False))


def test_memory_verbatim_or_off_has_no_model_warning() -> None:
    assert NO_MODEL_MESSAGE not in _memory_issues(_memory_config())
    assert _memory_issues(inference_config()) == []


def test_memory_with_an_openrouter_llm_and_its_key_has_no_model_warning() -> None:
    config = _memory_config(verbatim=False)
    llm = ProviderRef(provider_id="openrouter-llm", model="openai/gpt-4o-mini", credential_id="cred-1")
    config = config.model_copy(update={"pipeline": config.pipeline.model_copy(update={"llm": llm})})
    assert NO_MODEL_MESSAGE not in _memory_issues(config, {"cred-1": "openrouter-llm"})


async def test_resolve_extraction_model_ignores_other_providers(
    database: Database, settings: Settings
) -> None:
    async with database.session() as db:
        model, reason = await resolve_extraction_model(
            db, Vault(settings.master_key), settings, _memory_config(), workspace_id=DEFAULT_WORKSPACE_ID
        )
    assert model is None and reason is not None


# ------------------------------------------------------------------------------ migration

API_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def scratch_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """A scratch copy of the v1 seed (never the live database)."""
    for name in ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET", "LKAP_MASTER_KEY"):
        monkeypatch.delenv(name, raising=False)
    path = tmp_path / "lkap.db"
    shutil.copy(API_ROOT / "tests" / "fixtures" / "v1_seed.sqlite", path)
    yield path


def _migrate(database: Path, revision: str, *, downgrade: bool = False) -> None:
    config = Config(str(API_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(API_ROOT / "alembic"))
    config.cmd_opts = None  # type: ignore[assignment]
    url = f"sqlite+aiosqlite:///{database}"
    config.set_main_option("sqlalchemy.url", url)
    config.attributes["configure_logger"] = False
    os.environ["LKAP_DATABASE_URL"] = url
    try:
        (command.downgrade if downgrade else command.upgrade)(config, revision)
    finally:
        os.environ.pop("LKAP_DATABASE_URL", None)


def _tables(database: Path) -> set[str]:
    connection = sqlite3.connect(database)
    try:
        return {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        connection.close()


def test_v5_008_upgrade_downgrade_upgrade(scratch_db: Path) -> None:
    _migrate(scratch_db, "v5_007_telephony_amd")
    assert "memory_subjects" not in _tables(scratch_db)

    _migrate(scratch_db, "v5_008_memory")
    assert {"memory_subjects", "memory_events"} <= _tables(scratch_db)

    _migrate(scratch_db, "v5_007_telephony_amd", downgrade=True)
    assert not {"memory_subjects", "memory_events"} & _tables(scratch_db)

    _migrate(scratch_db, "v5_008_memory")
    assert {"memory_subjects", "memory_events"} <= _tables(scratch_db)


# ------------------------------------------------------------------ Mem0 (needs the extra)

needs_mem0 = pytest.mark.skipif(importlib.util.find_spec("mem0") is None, reason="needs lkap-api[memory]")


class _HashEmbedder:
    """A deterministic 16-wide embedder standing in for fastembed (no model download)."""

    dimension = 16

    async def warm(self) -> None:
        return None

    async def embed(self, texts: list[str]) -> list[list[float]]:
        vectors = []
        for text in texts:
            vector = [0.0] * self.dimension
            for word in text.lower().split():
                vector[sum(map(ord, word)) % self.dimension] += 1.0
            vectors.append([value + 0.01 for value in vector])
        return vectors


@pytest.fixture
async def mem0_store(settings: Settings, tmp_path: Path) -> AsyncIterator[Any]:
    from lkap_api.memory.mem0 import COLLECTION_NAME, Mem0MemoryStore, prepare_environment

    prepare_environment(settings)
    qdrant = pytest.importorskip("qdrant_client")
    client = qdrant.QdrantClient(path=str(tmp_path / "qdrant"))
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        content = json.dumps({"memory": [{"id": "0", "text": "Prefers morning calls."}]})
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    store = Mem0MemoryStore(
        vector_store={
            "provider": "qdrant",
            "config": {
                "collection_name": COLLECTION_NAME,
                "embedding_model_dims": _HashEmbedder.dimension,
                "client": client,
                "on_disk": True,
            },
        },
        embedder=_HashEmbedder(),
        http=http,
        qdrant_client=client,
    )
    store.requests = requests  # type: ignore[attr-defined]
    try:
        yield store
    finally:
        await store.aclose()


@needs_mem0
async def test_mem0_backend_round_trip_with_fakes(mem0_store: Any) -> None:
    """The one Mem0 integration test: verbatim and extracted writes, recall, forget (no vendor)."""
    from mem0.memory import telemetry

    from lkap_api.memory.store import ExtractionModel, MemoryMessage

    assert telemetry.MEM0_TELEMETRY is False
    subject, other = "c" * 64, "d" * 64
    model = ExtractionModel(
        provider_id="openrouter-llm",
        model="demo/model",
        base_url="https://openrouter.ai/api/v1",
        api_key="sk-test",
    )

    verbatim = await mem0_store.remember(
        subject, "agent-1", [MemoryMessage("user", "Call me Ada.")], model=None
    )
    extracted = await mem0_store.remember(
        subject, "agent-1", [MemoryMessage("user", "Mornings suit me best.")], model=model
    )
    await mem0_store.remember(other, "agent-1", [MemoryMessage("user", "Evenings only.")], model=None)
    recalled = await mem0_store.recall(subject, "agent-1", query=None, k=10)

    assert [item.text for item in verbatim] == ["Call me Ada."]
    assert [item.text for item in extracted] == ["Prefers morning calls."]
    assert {item.text for item in recalled} == {"Call me Ada.", "Prefers morning calls."}
    [request] = mem0_store.requests
    assert str(request.url) == "https://openrouter.ai/api/v1/chat/completions"
    assert request.headers["Authorization"] == "Bearer sk-test"

    assert await mem0_store.forget(subject) == 2
    assert await mem0_store.recall(subject, "agent-1", query=None, k=10) == []
    assert [item.text for item in await mem0_store.recall(other, "agent-1", query=None, k=10)] == [
        "Evenings only."
    ]
    assert await mem0_store.purge([other]) == 1
