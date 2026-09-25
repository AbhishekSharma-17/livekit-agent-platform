"""Tests for `lkap_api.costs`: usage -> priced lines, idempotent persistence, honest "no price"."""

from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal
from typing import Any, cast

import httpx
import pytest
from conftest import create_agent, inference_config
from lkap_contracts.agent_config import AgentConfig, PipelineConfig, QaConfig, RecordingConfig
from lkap_contracts.api_models import CostEstimate, EstimateSlot
from lkap_contracts.common import ProviderRef
from lkap_contracts.pricing import Unit
from sqlalchemy import select
from test_internal import API_KEY, _credential, _openai_config, _session_for

from lkap_api.costs import (
    ComputedLine,
    add_egress_cost_line,
    compute_usage_lines,
    config_for_session,
    cost_session,
    infra_lines,
    render_cost,
)
from lkap_api.costs.assumptions import apply_overrides, default_assumptions
from lkap_api.costs.estimate import build_estimate, estimated_usd_for, table_quote
from lkap_api.costs.service import drivers, egress_line, sip_model_for_number
from lkap_api.costs.snapshot import attach_estimate, trim
from lkap_api.db.models import Agent, PhoneNumber, ProviderCatalogCache, utcnow
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


async def test_add_egress_cost_line_prices_audio_egress_once(database: Database) -> None:
    """V4-15: egress is priced by model (`audio` for an audio-only recording, $0.005/min), once."""
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
        await add_egress_cost_line(session, row)  # idempotent

        assert row.cost_usd == 0.01  # 2 min x $0.005 (audio-only, the config default)
        lines = (
            (await session.execute(select(SessionCostRow).where(SessionCostRow.session_id == row.id)))
            .scalars()
            .all()
        )
        assert [(line.provider_id, line.model, line.unit) for line in lines] == [
            ("livekit-egress", "audio", "minutes")
        ]


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


# ------------------------------------------------------------------ V4-15: complete actual cost
def _by_unit(lines: list[ComputedLine]) -> dict[str, ComputedLine]:
    return {line.unit: line for line in lines}


def test_realtime_usage_is_priced_on_the_splits_never_the_folded_total() -> None:
    pipeline = PipelineConfig(
        mode="realtime", realtime=ProviderRef(provider_id="openai-realtime", model="gpt-realtime")
    )
    usage = {
        "model_usage": [
            {
                "type": "llm_usage",
                "model": "gpt-realtime",
                "input_tokens": 7000,
                "input_audio_tokens": 6000,
                "input_text_tokens": 1000,
                "output_tokens": 2100,
                "output_audio_tokens": 2000,
                "output_text_tokens": 100,
            }
        ]
    }

    lines = _by_unit(compute_usage_lines(pipeline, usage))

    assert "tokens_in" not in lines and "tokens_out" not in lines
    assert lines["audio_tokens_in"].quantity == 6000
    assert lines["audio_tokens_in"].cost_usd == Decimal("0.192")  # 6000 x $32/1M
    assert lines["text_tokens_in"].quantity == 1000
    assert lines["text_tokens_in"].cost_usd == Decimal("0.004")
    assert lines["audio_tokens_out"].cost_usd == Decimal("0.128")
    assert lines["text_tokens_out"].cost_usd == Decimal("0.0016")
    assert all(line.slot == "realtime" for line in lines.values())


def test_audio_usage_without_an_audio_price_is_unpriced_not_text_rated() -> None:
    pipeline = PipelineConfig(mode="realtime", realtime=ProviderRef(provider_id="xai-realtime"))
    usage = {
        "model_usage": [
            {"type": "llm_usage", "input_tokens": 7000, "input_audio_tokens": 6000, "output_tokens": 50}
        ]
    }

    lines = _by_unit(compute_usage_lines(pipeline, usage))

    assert lines["audio_tokens_in"].cost_usd is None
    assert lines["audio_tokens_in"].note == "audio-token price unknown"
    assert lines["tokens_in"].quantity == 1000  # the text part only


def test_cached_tokens_are_split_off_only_with_a_cached_price() -> None:
    with_cache = PipelineConfig(
        mode="cascaded", llm=ProviderRef(provider_id="livekit-inference-llm", model="openai/gpt-4.1")
    )
    no_cache = PipelineConfig(
        mode="cascaded", llm=ProviderRef(provider_id="groq-llm", model="openai/gpt-oss-120b")
    )
    entry = {"type": "llm_usage", "input_tokens": 1000, "input_cached_tokens": 400, "output_tokens": 10}

    cached = _by_unit(compute_usage_lines(with_cache, {"model_usage": [entry]}))
    plain = _by_unit(compute_usage_lines(no_cache, {"model_usage": [entry]}))

    assert cached["cached_tokens_in"].quantity == 400
    assert cached["cached_tokens_in"].cost_usd == Decimal("0.0002")  # 400 x $0.50/1M
    assert cached["tokens_in"].quantity == 600
    assert "cached_tokens_in" not in plain
    assert plain["tokens_in"].quantity == 1000
    assert plain["tokens_in"].note == "cache discount not modelled"


def test_token_billed_speech_is_priced_on_tokens() -> None:
    tts = PipelineConfig(mode="cascaded", tts=ProviderRef(provider_id="openai-tts", model="gpt-4o-mini-tts"))
    stt = PipelineConfig(
        mode="cascaded", stt=ProviderRef(provider_id="openai-stt", model="gpt-4o-transcribe")
    )
    tts_entry = {"type": "tts_usage", "characters_count": 0, "input_tokens": 100, "output_tokens": 2000}
    stt_entry = {"type": "stt_usage", "audio_duration": 30.0, "input_tokens": 300, "output_tokens": 50}

    voice = _by_unit(compute_usage_lines(tts, {"model_usage": [tts_entry]}))
    hearing = _by_unit(compute_usage_lines(stt, {"model_usage": [stt_entry]}))

    assert voice["tokens_in"].cost_usd == Decimal("0.00006")
    assert voice["tokens_out"].cost_usd == Decimal("0.024")  # 2000 x $12/1M
    assert "chars" not in voice
    assert hearing["audio_tokens_in"].cost_usd == Decimal("0.00075")  # 300 x $2.50/1M
    assert hearing["tokens_out"].cost_usd == Decimal("0.0005")
    assert "audio_s_in" not in hearing


def test_turn_detector_requests_are_shown_unpriced() -> None:
    pipeline = PipelineConfig(
        mode="cascaded", turn_detection=ProviderRef(provider_id="inference-turn-detector")
    )

    lines = compute_usage_lines(pipeline, {"model_usage": [{"type": "eot_usage", "total_requests": 12}]})

    assert [(line.unit, line.quantity, line.cost_usd, line.note) for line in lines] == [
        ("requests", Decimal(12), None, "no price")
    ]


def _timed_session(seconds: float, channel: str = "web", **extra: Any) -> SessionRow:
    start = dt.datetime(2026, 9, 25, 10, 0, tzinfo=dt.UTC)
    return SessionRow(
        id="1" * 32,
        agent_id="a" * 32,
        config_version=1,
        room_name="r",
        participant_identity="u",
        participant_name="U",
        status="ended",
        pipeline_mode="cascaded",
        channel=channel,
        started_at=start,
        ended_at=start + dt.timedelta(seconds=seconds),
        **extra,
    )


def test_call_minutes_have_a_ten_second_minimum_and_participants_count_twice() -> None:
    lines = {line.provider_id: line for line in infra_lines(_timed_session(5), inference_config())}

    assert lines["livekit-agent"].quantity == Decimal(10) / Decimal(60)
    assert lines["livekit-participant"].quantity == Decimal(5) / Decimal(60) * 2
    assert "livekit-sip" not in lines


def test_phone_minutes_follow_the_channel_and_the_number() -> None:
    web = {line.provider_id for line in infra_lines(_timed_session(60), inference_config())}
    phone = {
        line.provider_id: line
        for line in infra_lines(_timed_session(60, "sip_in"), inference_config(), sip_model="local-inbound")
    }

    assert "livekit-sip" not in web
    assert phone["livekit-sip"].model == "local-inbound"
    assert phone["livekit-sip"].cost_usd == Decimal("0.01")
    assert sip_model_for_number("+14155550100", "livekit") == "local-inbound"
    assert sip_model_for_number("+18005550100", "livekit") == "toll-free-inbound"
    assert sip_model_for_number("+14155550100", "trunk") == "trunk"


async def test_a_sip_session_is_costed_at_its_numbers_rate(
    admin_client: httpx.AsyncClient, database: Database
) -> None:
    agent = await create_agent(admin_client, name="Phone cost")
    start = utcnow() - dt.timedelta(minutes=2)
    async with database.session() as session:
        session.add(PhoneNumber(e164="+14155550199", source="livekit", inbound_agent_id=str(agent["id"])))
        row = SessionRow(
            id="2" * 32,
            agent_id=str(agent["id"]),
            config_version=1,
            room_name="lkap-phone-cost",
            participant_identity="sip",
            participant_name="sip",
            status="ended",
            pipeline_mode="cascaded",
            channel="sip_in",
            caller={"from": "+15550001111", "to": "+14155550199"},
            started_at=start,
            ended_at=start + dt.timedelta(minutes=2),
            usage={"model_usage": [{"type": "llm_usage", "input_tokens": 1, "output_tokens": 1}]},
        )
        session.add(row)
        await session.flush()
        await cost_session(session, row)

    async with database.session() as verify:
        rows = (
            (await verify.execute(select(SessionCostRow).where(SessionCostRow.session_id == "2" * 32)))
            .scalars()
            .all()
        )
    persisted = {r.provider_id: r for r in rows}
    assert persisted["livekit-sip"].model == "local-inbound"
    assert persisted["livekit-sip"].price_source == "table"
    assert persisted["livekit-agent"].cost_usd == pytest.approx(0.02)


def test_a_video_recording_is_priced_as_video_egress() -> None:
    config = inference_config(recording=RecordingConfig(enabled=True, audio_only=False))
    line = egress_line(_timed_session(60, recording_duration_s=60.0), config)
    assert line is not None and line.model == "video" and line.cost_usd == Decimal("0.02")


async def test_an_openrouter_slot_is_costed_live_from_the_cached_sheet(database: Database) -> None:
    base = inference_config()
    config = base.model_copy(
        update={
            "pipeline": base.pipeline.model_copy(
                update={"llm": ProviderRef(provider_id="openrouter-llm", model="openai/gpt-4.1-mini")}
            )
        }
    )
    async with database.session() as session:
        session.add(
            ProviderCatalogCache(
                provider_id="openrouter-llm",
                credential_id=None,
                kind="models",
                items=[
                    {
                        "id": "openai/gpt-4.1-mini",
                        "label": "GPT-4.1 mini",
                        "meta": {"pricing": {"prompt": "0.0000004", "completion": "0.0000016"}},
                    }
                ],
                ttl_s=21600,
            )
        )
        agent = Agent(
            id="3" * 32,
            slug="openrouter-cost",
            name="OpenRouter cost",
            pack_id="generic",
            ui_panel_id="generic",
            published=True,
            config=config.model_dump(mode="json"),
            config_version=1,
        )
        session.add(agent)
        row = SessionRow(
            id="4" * 32,
            agent_id=agent.id,
            config_version=1,
            room_name="lkap-openrouter-cost",
            participant_identity="u",
            participant_name="U",
            status="ended",
            pipeline_mode="cascaded",
            usage={
                "model_usage": [
                    {
                        "type": "llm_usage",
                        "model": "openai/gpt-4.1-mini",
                        "input_tokens": 1000,
                        "output_tokens": 500,
                    }
                ]
            },
        )
        session.add(row)
        await session.flush()
        await cost_session(session, row)

    async with database.session() as verify:
        lines = (
            (await verify.execute(select(SessionCostRow).where(SessionCostRow.session_id == "4" * 32)))
            .scalars()
            .all()
        )
    by_unit = {line.unit: line for line in lines}
    assert by_unit["tokens_in"].price_source == "live"
    assert by_unit["tokens_in"].cost_usd == pytest.approx(0.0004)
    assert by_unit["tokens_out"].cost_usd == pytest.approx(0.0008)


# ------------------------------------------------------------------ estimate vs actual
def _worked_config(**updates: Any) -> AgentConfig:
    return AgentConfig(
        instructions="x",
        pipeline=PipelineConfig(
            mode="cascaded",
            stt=ProviderRef(provider_id="livekit-inference-stt", model="deepgram/nova-3"),
            llm=ProviderRef(provider_id="livekit-inference-llm", model="openai/gpt-4o-mini"),
            tts=ProviderRef(provider_id="livekit-inference-tts", model="cartesia/sonic-3"),
        ),
    ).model_copy(update=updates)


def _snapshot(config: AgentConfig | None = None) -> CostEstimate:
    config = config or _worked_config()
    assumptions = apply_overrides(
        default_assumptions(config), {"prompt_tokens": 1500, "tool_calls_per_session": 0}
    )
    return build_estimate(config, assumptions, table_quote)


async def test_cost_session_sets_estimated_usd_from_the_snapshot(
    admin_client: httpx.AsyncClient, database: Database
) -> None:
    agent = await create_agent(admin_client, name="Estimated usd")
    session_id = (await admin_client.post(f"/v1/agents/{agent['id']}/connect", json={})).json()["sessionId"]
    async with database.session() as session:
        row = await session.get(SessionRow, session_id)
        assert row is not None and row.estimate is not None
        row.started_at = utcnow() - dt.timedelta(minutes=3)
        row.ended_at = row.started_at + dt.timedelta(minutes=3)
        row.status = "ended"
        row.usage = {"model_usage": [{"type": "llm_usage", "input_tokens": 10, "output_tokens": 10}]}
        expected = estimated_usd_for(CostEstimate.model_validate(row.estimate), Decimal(3))
        assert expected is not None
        await cost_session(session, row)
        assert row.estimated_usd == pytest.approx(float(expected))

    detail = (await admin_client.get(f"/v1/sessions/{session_id}")).json()
    assert Decimal(detail["estimated_usd"]) == pytest.approx(expected)
    assert Decimal(detail["cost"]["estimated_usd"]) == pytest.approx(expected)
    assert detail["cost"]["estimate_per_minute_usd"] is not None
    assert detail["cost"]["variance_usd"] is not None
    assert detail["cost"]["drivers"]
    assert detail["cost"]["price_version"]
    listed = (await admin_client.get("/v1/sessions")).json()["items"]
    assert any(item["id"] == session_id and item["estimated_usd"] is not None for item in listed)


def test_estimated_usd_is_mid_times_minutes_plus_per_session_lines() -> None:
    snapshot = _snapshot(_worked_config(qa=QaConfig(enabled=True)))
    judge = sum(
        (line.usd_per_session or Decimal(0) for line in snapshot.lines if line.slot == "qa_judge"), Decimal(0)
    )
    assert judge > 0
    assert snapshot.per_minute_usd is not None
    expected = (snapshot.per_minute_usd.mid * 4 + judge).quantize(Decimal("0.000001"))
    assert estimated_usd_for(snapshot, Decimal(4)) == expected


def _actual(
    slot: str, provider_id: str, model: str | None, unit: str, qty: float, cost: float | None
) -> ComputedLine:
    return ComputedLine(
        provider_id,
        model,
        cast(Unit, unit),
        Decimal(str(qty)),
        unit_price_usd=None if cost is None else Decimal("0.00001"),
        cost_usd=None if cost is None else Decimal(str(cost)),
        slot=cast(EstimateSlot, slot),
    )


TTS = ("tts", "livekit-inference-tts", "cartesia/sonic-3", "chars")
LLM_IN = ("llm", "livekit-inference-llm", "openai/gpt-4o-mini", "tokens_in")
LLM_OUT = ("llm", "livekit-inference-llm", "openai/gpt-4o-mini", "tokens_out")
STT = ("stt", "livekit-inference-stt", "deepgram/nova-3", "audio_s_in")


def _reason(line: ComputedLine, *, minutes: float = 5, turns: int | None = 15, persisted: Any = None) -> str:
    rows = {} if persisted is None else {(line.provider_id, line.model, line.unit): persisted}
    found = drivers(_snapshot(), [line], rows, minutes=Decimal(str(minutes)), turns=turns)
    return next(d.reason for d in found if (d.slot, d.unit) == (line.slot, line.unit))


@pytest.mark.parametrize(
    ("line", "kw", "reason"),
    [
        (_actual(*TTS, 405 * 5, 0.1), {}, "as estimated"),
        (_actual(*TTS, 405 * 20, 0.4), {"minutes": 20}, "more minutes"),
        (_actual(*TTS, 405, 0.02), {"minutes": 1}, "fewer minutes"),
        (_actual(*TTS, 700 * 5, 0.2), {}, "more talk"),
        (_actual(*TTS, 200 * 5, 0.05), {}, "less talk"),
        (_actual(*LLM_IN, 60000, 0.009), {}, "longer prompts"),
        (_actual(*LLM_OUT, 900, 0.0005), {"turns": 40}, "more turns"),
        (_actual(*STT, 300, None), {}, "unpriced line"),
        (_actual("avatar", "bey-avatar", None, "minutes", 5, 0.5), {}, "not estimated"),
    ],
)
def test_every_driver_reason_is_reachable(line: ComputedLine, kw: dict[str, Any], reason: str) -> None:
    assert _reason(line, **kw) == reason


def test_a_price_change_since_the_estimate_is_its_own_reason() -> None:
    line = _actual(*TTS, 405 * 5, 0.2)
    persisted = SessionCostRow(unit_price_usd=0.0001, price_version="2099-01-01")
    assert _reason(line, persisted=persisted) == "price changed"


def test_drivers_are_sorted_by_the_size_of_the_difference() -> None:
    found = drivers(
        _snapshot(),
        [_actual(*TTS, 700 * 5, 0.175), _actual(*STT, 300, 0.024)],
        {},
        minutes=Decimal(5),
        turns=15,
    )
    priced = [d for d in found if d.delta_usd is not None]
    sizes = [abs(d.delta_usd or Decimal(0)) for d in priced]
    assert sizes == sorted(sizes, reverse=True)
    assert all(d.delta_usd is None for d in found[len(priced) :])
    unused = next(d for d in found if (d.slot, d.unit) == ("llm", "tokens_in"))
    assert unused.actual_quantity == 0  # estimated, never used


def test_a_session_without_a_snapshot_says_no_estimate() -> None:
    row = _timed_session(120, usage={"model_usage": []})
    cost = render_cost(row, [], inference_config())
    assert cost.estimated_usd is None
    assert cost.drivers == []
    assert cost.variance_usd is None


async def test_attach_estimate_twice_keeps_the_first(
    admin_client: httpx.AsyncClient, database: Database
) -> None:
    agent = await create_agent(admin_client, name="Twice")
    session_id = (await admin_client.post(f"/v1/agents/{agent['id']}/connect", json={})).json()["sessionId"]
    async with database.session() as session:
        row = await session.get(SessionRow, session_id)
        assert row is not None
        first = row.estimate
    await attach_estimate(database, session_id)
    async with database.session() as session:
        row = await session.get(SessionRow, session_id)
        assert row is not None and row.estimate == first
    assert trim(_snapshot())["price_version"]
