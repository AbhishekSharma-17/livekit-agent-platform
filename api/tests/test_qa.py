"""Tests for `lkap_api.qa`: judge resolution, JSON repair retry, `session_qa` persistence."""

from __future__ import annotations

import json

import httpx
import respx
from conftest import inference_config
from lkap_contracts.agent_config import AgentConfig, QaConfig
from lkap_contracts.common import ProviderRef
from sqlalchemy import select
from test_webhooks import _make_endpoint

from lkap_api.db.models import Agent, Credential, SessionQa, WebhookDelivery
from lkap_api.db.models import Session as SessionRow
from lkap_api.db.session import Database
from lkap_api.jobs.context import JobContext
from lkap_api.jobs.service import JobsService
from lkap_api.qa.rubric import DEFAULT_TAGS
from lkap_api.qa.scorer import score_session
from lkap_api.settings import Settings
from lkap_api.vault import Vault

CHAT_URL = "https://api.openai.com/v1/chat/completions"

TRANSCRIPT = [
    {"role": "user", "text": "I want to cancel my policy", "ts": 0.0, "interrupted": False},
    {
        "role": "assistant",
        "text": "Sure, I can help you cancel it right away.",
        "ts": 1.2,
        "interrupted": False,
    },
]


def _judge_response(
    score: int = 8, sentiment: str = "positive", tags: list[str] | None = None
) -> httpx.Response:
    content = json.dumps(
        {"score": score, "sentiment": sentiment, "tags": tags or [], "summary": "handled well"}
    )
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


async def _make_credential(database: Database, vault: Vault, *, provider_id: str = "openai-llm") -> str:
    async with database.session() as session:
        cred = Credential(
            provider_id=provider_id,
            label="test credential",
            ciphertext=vault.encrypt({"api_key": "sk-test-key"}),
            fingerprint="…test",
        )
        session.add(cred)
        await session.flush()
        return cred.id


async def _make_agent(database: Database, config: AgentConfig) -> str:
    async with database.session() as session:
        agent = Agent(
            slug=f"qa-agent-{id(config)}", name="QA test agent", config=json.loads(config.model_dump_json())
        )
        session.add(agent)
        await session.flush()
        return agent.id


async def _make_session(database: Database, agent_id: str, *, transcript: list[dict[str, object]]) -> str:
    async with database.session() as session:
        row = SessionRow(
            agent_id=agent_id,
            config_version=1,
            room_name=f"room-{agent_id}-{len(transcript)}",
            participant_identity="caller",
            participant_name="Caller",
            status="ended",
            pipeline_mode="cascaded",
            transcript=transcript,
        )
        session.add(row)
        await session.flush()
        return row.id


async def _get_qa(database: Database, session_id: str) -> SessionQa | None:
    async with database.session() as session:
        row = await session.get(SessionQa, session_id)
        if row is not None:
            session.expunge(row)
        return row


def _ctx(database: Database, settings: Settings, vault: Vault, http: httpx.AsyncClient) -> JobContext:
    jobs = JobsService(database=database, settings=settings, vault=vault, http_client=http)
    return JobContext(database=database, settings=settings, vault=vault, http=http, jobs=jobs)


async def test_score_session_yields_a_score_and_tags_from_the_fixture_transcript(
    database: Database, settings: Settings
) -> None:
    vault = Vault(settings.master_key)
    credential_id = await _make_credential(database, vault)
    config = inference_config(
        qa=QaConfig(enabled=True, model=ProviderRef(provider_id="openai-llm", credential_id=credential_id))
    )
    agent_id = await _make_agent(database, config)
    session_id = await _make_session(database, agent_id, transcript=TRANSCRIPT)

    with respx.mock:
        respx.post(CHAT_URL).mock(return_value=_judge_response(score=8, tags=[DEFAULT_TAGS[1]]))
        async with httpx.AsyncClient() as http:
            await score_session(_ctx(database, settings, vault, http), session_id)

    qa = await _get_qa(database, session_id)
    assert qa is not None
    assert qa.status == "done"
    assert 1 <= qa.score <= 10  # type: ignore[operator]
    assert qa.sentiment == "positive"
    assert DEFAULT_TAGS[1] in qa.tags
    assert qa.model == "openai-llm/gpt-4.1"  # ProviderSpec("openai-llm").default_model
    assert qa.scored_at is not None
    assert qa.error is None


async def test_malformed_json_first_response_is_repaired(database: Database, settings: Settings) -> None:
    vault = Vault(settings.master_key)
    credential_id = await _make_credential(database, vault)
    config = inference_config(
        qa=QaConfig(enabled=True, model=ProviderRef(provider_id="openai-llm", credential_id=credential_id))
    )
    agent_id = await _make_agent(database, config)
    session_id = await _make_session(database, agent_id, transcript=TRANSCRIPT)

    with respx.mock:
        route = respx.post(CHAT_URL).mock(
            side_effect=[
                httpx.Response(200, json={"choices": [{"message": {"content": "not json at all, sorry!"}}]}),
                _judge_response(score=5, sentiment="neutral"),
            ]
        )
        async with httpx.AsyncClient() as http:
            await score_session(_ctx(database, settings, vault, http), session_id)

    assert route.call_count == 2
    qa = await _get_qa(database, session_id)
    assert qa is not None
    assert qa.status == "done"
    assert qa.score == 5
    assert qa.sentiment == "neutral"


async def test_malformed_json_twice_marks_the_qa_row_failed(database: Database, settings: Settings) -> None:
    vault = Vault(settings.master_key)
    credential_id = await _make_credential(database, vault)
    config = inference_config(
        qa=QaConfig(enabled=True, model=ProviderRef(provider_id="openai-llm", credential_id=credential_id))
    )
    agent_id = await _make_agent(database, config)
    session_id = await _make_session(database, agent_id, transcript=TRANSCRIPT)

    with respx.mock:
        route = respx.post(CHAT_URL).mock(
            return_value=httpx.Response(200, json={"choices": [{"message": {"content": "still not json"}}]})
        )
        async with httpx.AsyncClient() as http:
            await score_session(_ctx(database, settings, vault, http), session_id)

    assert route.call_count == 2
    qa = await _get_qa(database, session_id)
    assert qa is not None
    assert qa.status == "failed"
    assert qa.error is not None and "malformed JSON after repair" in qa.error


async def test_qa_disabled_skips_scoring_entirely(database: Database, settings: Settings) -> None:
    vault = Vault(settings.master_key)
    config = inference_config(qa=QaConfig(enabled=False))
    agent_id = await _make_agent(database, config)
    session_id = await _make_session(database, agent_id, transcript=TRANSCRIPT)

    async with httpx.AsyncClient() as http:
        await score_session(_ctx(database, settings, vault, http), session_id)

    assert await _get_qa(database, session_id) is None


async def test_no_resolvable_judge_marks_the_qa_row_failed(database: Database, settings: Settings) -> None:
    """`inference_config()`'s default `pipeline.llm` is `livekit-inference-llm`

    (no credential; CONTRACTS-V2 §4.1), which this package does not have a
    documented HTTP endpoint for (see `qa/llm_client.py`) — an honest failure,
    not a guess at an endpoint.
    """
    vault = Vault(settings.master_key)
    config = inference_config(qa=QaConfig(enabled=True))
    agent_id = await _make_agent(database, config)
    session_id = await _make_session(database, agent_id, transcript=TRANSCRIPT)

    async with httpx.AsyncClient() as http:
        await score_session(_ctx(database, settings, vault, http), session_id)

    qa = await _get_qa(database, session_id)
    assert qa is not None
    assert qa.status == "failed"
    assert qa.error is not None and "unsupported judge provider" in qa.error


async def test_missing_credential_marks_the_qa_row_failed(database: Database, settings: Settings) -> None:
    vault = Vault(settings.master_key)
    config = inference_config(
        qa=QaConfig(enabled=True, model=ProviderRef(provider_id="openai-llm", credential_id="does-not-exist"))
    )
    agent_id = await _make_agent(database, config)
    session_id = await _make_session(database, agent_id, transcript=TRANSCRIPT)

    async with httpx.AsyncClient() as http:
        await score_session(_ctx(database, settings, vault, http), session_id)

    qa = await _get_qa(database, session_id)
    assert qa is not None
    assert qa.status == "failed"
    assert qa.error == "unknown credential_id: does-not-exist"


async def test_judge_http_failure_marks_the_qa_row_failed_without_raising(
    database: Database, settings: Settings
) -> None:
    vault = Vault(settings.master_key)
    credential_id = await _make_credential(database, vault)
    config = inference_config(
        qa=QaConfig(enabled=True, model=ProviderRef(provider_id="openai-llm", credential_id=credential_id))
    )
    agent_id = await _make_agent(database, config)
    session_id = await _make_session(database, agent_id, transcript=TRANSCRIPT)

    with respx.mock:
        respx.post(CHAT_URL).mock(side_effect=httpx.ConnectError("boom"))
        async with httpx.AsyncClient() as http:
            await score_session(_ctx(database, settings, vault, http), session_id)

    qa = await _get_qa(database, session_id)
    assert qa is not None
    assert qa.status == "failed"
    assert qa.error is not None and "judge call failed" in qa.error


async def test_missing_session_is_a_no_op(database: Database, settings: Settings) -> None:
    vault = Vault(settings.master_key)
    async with httpx.AsyncClient() as http:
        await score_session(_ctx(database, settings, vault, http), "does-not-exist")
    assert await _get_qa(database, "does-not-exist") is None


# --------------------------------------------------------------- V2-20-1: session.qa_completed
async def test_a_done_rescore_emits_session_qa_completed(database: Database, settings: Settings) -> None:
    vault = Vault(settings.master_key)
    await _make_endpoint(database, vault, "https://hooks.example.com/qa", events=["session.qa_completed"])
    credential_id = await _make_credential(database, vault)
    config = inference_config(
        qa=QaConfig(enabled=True, model=ProviderRef(provider_id="openai-llm", credential_id=credential_id))
    )
    agent_id = await _make_agent(database, config)
    session_id = await _make_session(database, agent_id, transcript=TRANSCRIPT)

    with respx.mock:
        respx.post(CHAT_URL).mock(return_value=_judge_response(score=7))
        async with httpx.AsyncClient() as http:
            await score_session(_ctx(database, settings, vault, http), session_id)

    async with database.session() as session:
        deliveries = (await session.execute(select(WebhookDelivery))).scalars().all()
    (delivery,) = [d for d in deliveries if d.event_type == "session.qa_completed"]
    assert delivery.payload["data"]["session_id"] == session_id
    assert delivery.payload["data"]["status"] == "done"
    assert delivery.payload["data"]["scored_by"] == "api"


async def test_a_failed_rescore_also_emits_session_qa_completed(
    database: Database, settings: Settings
) -> None:
    vault = Vault(settings.master_key)
    await _make_endpoint(database, vault, "https://hooks.example.com/qa", events=["session.qa_completed"])
    config = inference_config(qa=QaConfig(enabled=True))  # no credential -> unresolvable judge -> "failed"
    agent_id = await _make_agent(database, config)
    session_id = await _make_session(database, agent_id, transcript=TRANSCRIPT)

    async with httpx.AsyncClient() as http:
        await score_session(_ctx(database, settings, vault, http), session_id)

    async with database.session() as session:
        deliveries = (await session.execute(select(WebhookDelivery))).scalars().all()
    (delivery,) = [d for d in deliveries if d.event_type == "session.qa_completed"]
    assert delivery.payload["data"]["status"] == "failed"


async def test_qa_disabled_skip_emits_no_webhook(database: Database, settings: Settings) -> None:
    vault = Vault(settings.master_key)
    await _make_endpoint(database, vault, "https://hooks.example.com/qa", events=[])  # wildcard
    config = inference_config(qa=QaConfig(enabled=False))
    agent_id = await _make_agent(database, config)
    session_id = await _make_session(database, agent_id, transcript=TRANSCRIPT)

    async with httpx.AsyncClient() as http:
        await score_session(_ctx(database, settings, vault, http), session_id)

    async with database.session() as session:
        deliveries = (await session.execute(select(WebhookDelivery))).scalars().all()
    assert deliveries == []
