"""Tests for `lkap_api.costs`: usage -> priced lines, idempotent persistence, honest "no price"."""

from __future__ import annotations

import json
from decimal import Decimal

import httpx
from conftest import create_agent, inference_config
from lkap_contracts.agent_config import PipelineConfig
from lkap_contracts.common import ProviderRef
from sqlalchemy import select
from test_internal import API_KEY, _credential, _openai_config, _session_for

from lkap_api.costs import (
    ComputedLine,
    add_egress_cost_line,
    compute_usage_lines,
    config_for_session,
    cost_session,
)
from lkap_api.db.models import Agent
from lkap_api.db.models import Session as SessionRow
from lkap_api.db.models import SessionCost as SessionCostRow
from lkap_api.db.session import Database

LLM_USAGE = {
    "model_usage": [
        {
            "type": "llm_usage",
            "provider": "api.openai.com",  # the vendor's own free-text string; never trusted for pricing
            "model": "gpt-4.1",
            "input_tokens": 1000,
            "output_tokens": 500,
        }
    ]
}


def _pipeline(**slots: ProviderRef) -> PipelineConfig:
    return PipelineConfig(mode="cascaded", **slots)


def test_compute_usage_lines_prices_llm_tokens_to_six_decimal_places() -> None:
    pipeline = _pipeline(llm=ProviderRef(provider_id="openai-llm"), tts=None, stt=None)

    lines = compute_usage_lines(pipeline, LLM_USAGE)

    by_unit = {line.unit: line for line in lines}
    assert by_unit["tokens_in"].cost_usd == Decimal("0.002")
    assert by_unit["tokens_out"].cost_usd == Decimal("0.004")
    assert by_unit["tokens_in"].provider_id == "openai-llm"
    assert by_unit["tokens_in"].model == "gpt-4.1"


def test_compute_usage_lines_marks_an_unpriced_provider_as_no_price() -> None:
    pipeline = _pipeline(tts=ProviderRef(provider_id="cartesia-tts"))
    usage = {
        "model_usage": [
            {"type": "tts_usage", "provider": "Cartesia", "model": "sonic-3", "characters_count": 400}
        ]
    }

    lines = compute_usage_lines(pipeline, usage)

    assert len(lines) == 1
    assert lines[0].cost_usd is None
    assert lines[0].unit_price_usd is None
    assert lines[0].note == "no price"


def test_compute_usage_lines_ignores_usage_with_no_bound_pipeline_slot() -> None:
    """An `llm_usage` entry with no `llm`/`realtime` ref configured produces no line."""
    pipeline = _pipeline()  # every slot None

    lines = compute_usage_lines(pipeline, LLM_USAGE)

    assert lines == []


def test_compute_usage_lines_handles_missing_or_malformed_usage() -> None:
    pipeline = _pipeline(llm=ProviderRef(provider_id="openai-llm"))
    assert compute_usage_lines(pipeline, None) == []
    assert compute_usage_lines(pipeline, {}) == []
    assert compute_usage_lines(pipeline, {"model_usage": "not-a-list"}) == []


def test_computed_line_key_identifies_provider_model_unit() -> None:
    line = ComputedLine("openai-llm", "gpt-4.1", "tokens_in", Decimal(1))
    assert line.key == ("openai-llm", "gpt-4.1", "tokens_in")


async def test_cost_session_persists_priced_lines_and_is_idempotent(database: Database) -> None:
    async with database.session() as session:
        agent = Agent(
            id="b" * 32,
            slug="cost-idempotent",
            name="Cost idempotent",
            pack_id="generic",
            ui_panel_id="generic",
            published=True,
            config=inference_config(
                pipeline=inference_config().pipeline.model_copy(
                    update={"llm": ProviderRef(provider_id="openai-llm", model="gpt-4.1")}
                )
            ).model_dump(mode="json"),
            config_version=1,
        )
        session.add(agent)
        row = SessionRow(
            id="c" * 32,
            agent_id=agent.id,
            config_version=1,
            room_name="lkap-costidem",
            participant_identity="u",
            participant_name="U",
            status="ended",
            pipeline_mode="cascaded",
            usage=LLM_USAGE,
        )
        session.add(row)
        await session.flush()

        await cost_session(session, row)
        first_cost = row.cost_usd
        await cost_session(session, row)  # second call must not double the total

    assert first_cost == 0.006
    async with database.session() as verify:
        row2 = await verify.get(SessionRow, "c" * 32)
        assert row2 is not None
        assert row2.cost_usd == 0.006
        lines = (
            (await verify.execute(select(SessionCostRow).where(SessionCostRow.session_id == "c" * 32)))
            .scalars()
            .all()
        )
        assert len(lines) == 2  # tokens_in + tokens_out, never duplicated


async def test_cost_session_noop_without_usage(database: Database) -> None:
    async with database.session() as session:
        agent = Agent(
            id="d" * 32,
            slug="cost-nousage",
            name="No usage",
            pack_id="generic",
            ui_panel_id="generic",
            published=True,
            config=inference_config().model_dump(mode="json"),
            config_version=1,
        )
        session.add(agent)
        row = SessionRow(
            id="e" * 32,
            agent_id=agent.id,
            config_version=1,
            room_name="lkap-nousage",
            participant_identity="u",
            participant_name="U",
            status="ended",
            pipeline_mode="cascaded",
            usage=None,
        )
        session.add(row)
        await session.flush()

        await cost_session(session, row)

        assert row.cost_usd is None


async def test_add_egress_cost_line_is_a_noop_when_unpriced(database: Database) -> None:
    """LiveKit Egress has no `pricing.PRICES` entry (documented, honest gap) — never persisted."""
    async with database.session() as session:
        agent = Agent(
            id="f" * 32,
            slug="egress-noop",
            name="Egress",
            pack_id="generic",
            ui_panel_id="generic",
            published=True,
            config=inference_config().model_dump(mode="json"),
            config_version=1,
        )
        session.add(agent)
        row = SessionRow(
            id="0" * 32,
            agent_id=agent.id,
            config_version=1,
            room_name="lkap-egress",
            participant_identity="u",
            participant_name="U",
            status="ended",
            pipeline_mode="cascaded",
            recording_status="ready",
            recording_duration_s=120.0,
        )
        session.add(row)
        await session.flush()

        await add_egress_cost_line(session, row)

        assert row.cost_usd is None


async def test_config_for_session_prefers_the_pinned_version_over_the_current_config(
    admin_client: httpx.AsyncClient, database: Database
) -> None:
    """A later agent edit must not silently re-price an old session's usage."""
    agent = await create_agent(admin_client, name="Version pin test")
    response = await admin_client.post(f"/v1/agents/{agent['id']}/connect", json={})
    session_id = str(response.json()["sessionId"])

    # Bump the agent's *current* config to something with a different pipeline
    # after the session started; the session pinned `config_version=1`.
    new_config = json.loads(inference_config().model_dump_json())
    new_config["instructions"] = "Changed after the session started."
    await admin_client.put(f"/v1/agents/{agent['id']}", json={"config": new_config})

    async with database.session() as session:
        row = await session.get(SessionRow, session_id)
        assert row is not None
        config = await config_for_session(session, row)

    assert config is not None
    assert config.instructions != "Changed after the session started."


async def test_session_detail_shows_priced_and_unpriced_lines(
    admin_client: httpx.AsyncClient, service_client: httpx.AsyncClient
) -> None:
    credential_id = await _credential(admin_client, "openai-llm", {"api_key": API_KEY})
    session_id, _agent = await _session_for(admin_client, _openai_config(credential_id))

    await service_client.put(
        f"/internal/v1/sessions/{session_id}/summary",
        json={
            "status": "ended",
            "usage": {
                "model_usage": [
                    {
                        "type": "llm_usage",
                        "provider": "api.openai.com",
                        "model": "gpt-4.1",
                        "input_tokens": 1000,
                        "output_tokens": 500,
                    },
                    {
                        "type": "stt_usage",
                        "provider": "livekit",
                        "model": "inference",
                        "audio_duration": 10.0,
                    },
                ]
            },
            "transcript": [],
        },
    )

    detail = (await admin_client.get(f"/v1/sessions/{session_id}")).json()

    assert detail["cost_usd"] == "0.006"
    assert detail["cost"]["total_usd"] == "0.006"
    lines_by_unit = {line["unit"]: line for line in detail["cost"]["lines"]}
    assert lines_by_unit["tokens_in"]["cost_usd"] == "0.002"
    assert lines_by_unit["tokens_out"]["cost_usd"] == "0.004"
    # `stt` resolves to `livekit-inference-stt`, which has no PRICES entry.
    assert lines_by_unit["audio_s_in"]["cost_usd"] is None
    assert lines_by_unit["audio_s_in"]["note"] == "no price"
