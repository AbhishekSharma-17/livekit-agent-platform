"""The per-minute estimator, the estimate/quotes/prices routes and the session snapshot (COSTS.md §3, §7)."""

from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal
from typing import Any

import httpx
import pytest
from conftest import create_agent, inference_config
from fastapi import FastAPI
from lkap_contracts.agent_config import AgentConfig, PipelineConfig, QaConfig, RecordingConfig
from lkap_contracts.common import ProviderRef
from lkap_contracts.pricing import PriceQuote, Unit
from sqlalchemy import select

from lkap_api.costs import assumptions as assumptions_mod
from lkap_api.costs import snapshot as snapshot_mod
from lkap_api.costs.assumptions import (
    MIN_SESSIONS,
    apply_overrides,
    default_assumptions,
    workspace_assumptions,
)
from lkap_api.costs.estimate import build_estimate, table_quote
from lkap_api.costs.snapshot import attach_estimate
from lkap_api.db.constants import DEFAULT_WORKSPACE_ID
from lkap_api.db.models import Agent, AuditLog, utcnow
from lkap_api.db.models import Session as SessionRow
from lkap_api.db.session import Database


@pytest.fixture(autouse=True)
def _fresh_averages() -> None:
    assumptions_mod.clear_cache()


def _worked_example_config(**updates: Any) -> AgentConfig:
    return AgentConfig(
        instructions="You are a receptionist.",
        pipeline=PipelineConfig(
            mode="cascaded",
            stt=ProviderRef(provider_id="livekit-inference-stt", model="deepgram/nova-3"),
            llm=ProviderRef(provider_id="livekit-inference-llm", model="openai/gpt-4o-mini"),
            tts=ProviderRef(provider_id="livekit-inference-tts", model="cartesia/sonic-3"),
        ),
    ).model_copy(update=updates)


def _estimate(config: AgentConfig, quote: Any = table_quote, **kw: Any) -> Any:
    overrides = {"prompt_tokens": 1500, "tool_calls_per_session": 0, **kw.pop("overrides", {})}
    return build_estimate(config, apply_overrides(default_assumptions(config), overrides), quote, **kw)


def _lines(estimate: Any) -> dict[tuple[str, str], Any]:
    return {(line.slot, line.unit): line for line in estimate.lines}


# ------------------------------------------------------------------------ the estimator
def test_worked_example_reproduces_to_four_decimals() -> None:
    """COSTS.md §3.3 with the Cloud rows: ≈ $0.0373/min."""
    estimate = _estimate(_worked_example_config())
    lines = _lines(estimate)

    assert lines[("stt", "audio_s_in")].quantity_per_min == Decimal("60.000")
    assert lines[("stt", "audio_s_in")].usd_per_min == Decimal("0.004800")
    assert lines[("llm", "tokens_in")].quantity_per_min == Decimal("7860.000")
    assert lines[("llm", "tokens_out")].quantity_per_min == Decimal("180.000")
    assert lines[("tts", "chars")].quantity_per_min == Decimal("405.000")
    assert lines[("tts", "chars")].usd_per_min == Decimal("0.020250")
    assert lines[("livekit_agent", "minutes")].usd_per_min == Decimal("0.010000")
    assert lines[("livekit_participant", "minutes")].usd_per_min == Decimal("0.001000")
    assert estimate.per_minute_usd is not None
    assert estimate.per_minute_usd.mid.quantize(Decimal("0.0001")) == Decimal("0.0373")
    assert estimate.per_minute_usd.low <= estimate.per_minute_usd.mid <= estimate.per_minute_usd.high
    assert estimate.per_session_usd is not None
    assert estimate.per_session_usd.mid == (estimate.per_minute_usd.mid * 5).quantize(Decimal("0.000001"))
    assert estimate.sources == ["table"]
    assert estimate.unpriced == []
    assert estimate.priced_share == 1.0
    assert any("entry tier" in caveat for caveat in estimate.caveats)
    assert any("silence" in caveat for caveat in estimate.caveats)


def test_labels_are_plain_words() -> None:
    estimate = _estimate(_worked_example_config())
    for line in estimate.lines:
        lowered = line.label.lower()
        for jargon in ("token", "stt", "tts", "llm", "egress", "sip"):
            assert jargon not in lowered.split(), (line.label, jargon)


def _only_text_rates(provider_id: str, model: str | None, unit: Unit) -> PriceQuote | None:
    if unit in ("tokens_in", "tokens_out"):
        return PriceQuote(
            provider_id=provider_id,
            model=model,
            unit=unit,
            usd_per_unit=Decimal("0.000001"),
            source="table",
            source_url="https://example.test/pricing",
            as_of="2026-09-25",
        )
    return table_quote(provider_id, model, unit)


def test_realtime_with_only_text_rates_leaves_audio_unpriced_never_at_the_text_rate() -> None:
    config = AgentConfig(
        instructions="x",
        pipeline=PipelineConfig(mode="realtime", realtime=ProviderRef(provider_id="xai-realtime")),
    )
    estimate = _estimate(config, _only_text_rates)
    lines = _lines(estimate)

    audio_in = lines[("realtime", "audio_tokens_in")]
    audio_out = lines[("realtime", "audio_tokens_out")]
    for line in (audio_in, audio_out):
        assert line.quote is None
        assert line.usd_per_min is None
        assert line.note == "audio-token price unknown"
    assert lines[("realtime", "tokens_in")].usd_per_min is not None  # the text part at the text rate
    assert any("audio heard" in entry for entry in estimate.unpriced)


def test_realtime_gemini_live_prices_audio_at_25_tokens_per_second() -> None:
    config = AgentConfig(
        instructions="x",
        pipeline=PipelineConfig(
            mode="realtime", realtime=ProviderRef(provider_id="google-realtime", model="gemini-3.8-live")
        ),
    )
    lines = _lines(_estimate(config))
    # 60 s x 0.45 x 25 = 675 tokens/min out at $12/1M.
    assert lines[("realtime", "audio_tokens_out")].quantity_per_min == Decimal("675.000")
    assert lines[("realtime", "audio_tokens_out")].usd_per_min == Decimal("0.008100")
    assert ("realtime", "tokens_in") not in lines  # split model: never the folded unit


def test_half_cascade_has_a_voice_line_and_no_speech_to_text_line() -> None:
    config = AgentConfig(
        instructions="x",
        pipeline=PipelineConfig(
            mode="half_cascade",
            realtime=ProviderRef(provider_id="openai-realtime", model="gpt-realtime"),
            tts=ProviderRef(provider_id="livekit-inference-tts", model="cartesia/sonic-3"),
        ),
    )
    slots = {line.slot for line in _estimate(config).lines}
    lines = _lines(_estimate(config))
    assert "tts" in slots
    assert "stt" not in slots
    assert ("realtime", "audio_tokens_in") in lines
    assert ("realtime", "audio_tokens_out") not in lines


def test_phone_adds_phone_minutes_with_the_given_model() -> None:
    lines = _lines(_estimate(_worked_example_config(), channel="phone", sip_model="local-inbound"))
    sip = lines[("livekit_sip", "minutes")]
    assert sip.model == "local-inbound"
    assert sip.usd_per_min == Decimal("0.010000")
    trunk = _lines(_estimate(_worked_example_config(), channel="phone"))[("livekit_sip", "minutes")]
    assert trunk.model == "trunk" and trunk.usd_per_min == Decimal("0.004000")
    assert ("livekit_sip", "minutes") not in _lines(_estimate(_worked_example_config()))


@pytest.mark.parametrize(
    ("audio_only", "model", "price"), [(True, "audio", "0.005000"), (False, "video", "0.020000")]
)
def test_recording_adds_an_egress_line(audio_only: bool, model: str, price: str) -> None:
    config = _worked_example_config(recording=RecordingConfig(enabled=True, audio_only=audio_only))
    line = _lines(_estimate(config))[("livekit_egress", "minutes")]
    assert line.model == model
    assert line.usd_per_min == Decimal(price)


def test_quality_review_adds_a_per_session_judge_line() -> None:
    config = _worked_example_config(qa=QaConfig(enabled=True))
    estimate = _estimate(config)
    judge = [line for line in estimate.lines if line.slot == "qa_judge"]
    assert {line.unit for line in judge} == {"tokens_in", "tokens_out"}
    assert all(line.quantity_per_min is None and line.usd_per_session is not None for line in judge)
    assert estimate.per_session_usd is not None and estimate.per_minute_usd is not None
    judge_usd = sum((line.usd_per_session for line in judge), Decimal(0))
    assert estimate.per_session_usd.mid == (estimate.per_minute_usd.mid * 5 + judge_usd).quantize(
        Decimal("0.000001")
    )


def test_text_drops_the_audio_lines_and_keeps_the_call_minutes() -> None:
    config = _worked_example_config(
        pipeline=_worked_example_config().pipeline.model_copy(
            update={"avatar": ProviderRef(provider_id="bey-avatar")}
        )
    )
    slots = {line.slot for line in _estimate(config, channel="text").lines}
    assert slots == {"llm", "livekit_agent", "livekit_participant"}


def test_unpriced_names_every_unpriced_line_and_the_band_holds() -> None:
    config = AgentConfig(
        instructions="x",
        pipeline=PipelineConfig(
            mode="cascaded",
            stt=ProviderRef(provider_id="deepgram-stt", model="nova-3"),
            llm=ProviderRef(provider_id="openai-llm", model="gpt-4.1"),
            tts=ProviderRef(provider_id="elevenlabs-tts", model="eleven_flash_v2_5"),
            avatar=ProviderRef(provider_id="bey-avatar"),
        ),
    )
    estimate = _estimate(config)
    unpriced_lines = [line for line in estimate.lines if line.quote is None]
    assert len(estimate.unpriced) == len(unpriced_lines) == 2
    assert any(entry.startswith("Agent's voice") and "characters" in entry for entry in estimate.unpriced)
    assert any(entry.startswith("Video avatar") for entry in estimate.unpriced)
    assert all(line.note == "no price" and line.usd_per_min is None for line in unpriced_lines)
    assert estimate.per_minute_usd is not None
    assert estimate.per_minute_usd.low <= estimate.per_minute_usd.mid <= estimate.per_minute_usd.high
    assert 0 < estimate.priced_share < 1


def test_a_config_with_nothing_priced_has_no_figure() -> None:
    config = AgentConfig(
        instructions="x",
        pipeline=PipelineConfig(mode="cascaded", tts=ProviderRef(provider_id="elevenlabs-tts")),
    )

    def nothing(provider_id: str, model: str | None, unit: Unit) -> PriceQuote | None:
        return None

    estimate = _estimate(config, nothing)
    assert estimate.per_minute_usd is None and estimate.per_session_usd is None


def test_workspace_prices_win_over_the_table() -> None:
    from lkap_contracts.pricing import WorkspacePrice

    from lkap_api.costs.prices import PriceBook

    book = PriceBook(
        workspace_prices=[
            WorkspacePrice(
                provider_id="livekit-inference-tts",
                model="cartesia/sonic-3",
                unit="chars",
                usd_per_unit=Decimal("0.00001"),
                as_of="2026-09-20",
            )
        ]
    )
    line = _lines(_estimate(_worked_example_config(), book))[("tts", "chars")]
    assert line.quote is not None and line.quote.source == "workspace"
    assert line.usd_per_min == Decimal("0.004050")


def test_an_unknown_assumption_is_refused() -> None:
    with pytest.raises(ValueError):
        apply_overrides(default_assumptions(None), {"made_up": 1})
    with pytest.raises(ValueError):
        apply_overrides(default_assumptions(None), {"agent_talk_ratio": 1.5})


# ------------------------------------------------------------------------ routes
async def test_estimate_route_for_a_draft_config(admin_client: httpx.AsyncClient) -> None:
    config = json.loads(_worked_example_config().model_dump_json())
    response = await admin_client.post(
        "/v1/cost-estimates",
        json={"config": config, "assumptions": {"prompt_tokens": 1500, "tool_calls_per_session": 0}},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert Decimal(body["per_minute_usd"]["mid"]).quantize(Decimal("0.0001")) == Decimal("0.0373")
    prompt = next(a for a in body["assumptions"] if a["key"] == "prompt_tokens")
    assert prompt["source"] == "request"


async def test_estimate_route_refuses_a_bad_draft_and_a_missing_source(
    admin_client: httpx.AsyncClient,
) -> None:
    bad = await admin_client.post("/v1/cost-estimates", json={"config": {"instructions": 1}})
    assert bad.status_code == 422
    none = await admin_client.post("/v1/cost-estimates", json={})
    assert none.status_code == 422
    two = await admin_client.post("/v1/cost-estimates", json={"agent_id": "a", "template_id": "blank"})
    assert two.status_code == 422
    unknown = await admin_client.post(
        "/v1/cost-estimates", json={"template_id": "blank", "assumptions": {"nope": 1}}
    )
    assert unknown.status_code == 422


async def test_estimate_route_for_a_template_uses_its_pack_default(admin_client: httpx.AsyncClient) -> None:
    response = await admin_client.post("/v1/cost-estimates", json={"template_id": "blank"})
    assert response.status_code == 200, response.text
    slots = {line["slot"] for line in response.json()["lines"]}
    assert {"livekit_agent", "livekit_participant"} <= slots
    assert slots & {"llm", "realtime"}


async def test_agent_cost_estimate_route_is_read_only(
    admin_client: httpx.AsyncClient, database: Database
) -> None:
    agent = await create_agent(admin_client, name="Estimate me")
    before = await _audit_count(database)

    response = await admin_client.post(f"/v1/agents/{agent['id']}/cost-estimate")
    with_body = await admin_client.post(f"/v1/agents/{agent['slug']}/cost-estimate", json={"channel": "text"})

    assert response.status_code == 200, response.text
    assert with_body.status_code == 200
    assert {line["slot"] for line in with_body.json()["lines"]} >= {"livekit_agent"}
    assert all(line["slot"] not in ("stt", "tts") for line in with_body.json()["lines"])
    assert await _audit_count(database) == before  # nothing written, nothing audited
    missing = await admin_client.post("/v1/agents/nope/cost-estimate")
    assert missing.status_code == 404


async def _audit_count(database: Database) -> int:
    async with database.session() as session:
        return len((await session.execute(select(AuditLog.id))).scalars().all())


async def test_quotes_route_returns_price_version_and_slot_shares(admin_client: httpx.AsyncClient) -> None:
    response = await admin_client.post(
        "/v1/pricing/quotes",
        json={
            "items": [
                {"provider_id": "livekit-inference-stt", "model": "deepgram/nova-3"},
                {"provider_id": "livekit-inference-llm", "model": "openai/gpt-oss-120b"},
                {"provider_id": "livekit-inference-tts", "model": "cartesia/sonic-3"},
                {"provider_id": "not-a-provider"},
            ]
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["price_version"]
    assert body["as_of"]
    stt, oss, tts, unknown = body["items"]
    assert Decimal(stt["per_minute_usd"]) == Decimal("0.0048")
    assert stt["kind"] == "stt" and stt["quotes"][0]["source"] == "table"
    assert oss["per_minute_usd"] is not None and oss["note"] is None  # R-V4-58: the Groq route
    oss_in = next(q for q in oss["quotes"] if q["unit"] == "tokens_in")
    assert Decimal(oss_in["usd_per_unit"]) == Decimal("0.15") / Decimal(1_000_000)
    assert "Groq" in oss_in["tier_note"]
    assert Decimal(tts["per_minute_usd"]) == Decimal("0.02025")
    assert unknown["note"] == "unknown provider"
    too_many = await admin_client.post(
        "/v1/pricing/quotes", json={"items": [{"provider_id": "openai-llm"}] * 101}
    )
    assert too_many.status_code == 422


async def test_workspace_prices_round_trip_validate_and_audit(
    admin_client: httpx.AsyncClient, database: Database
) -> None:
    empty = await admin_client.get("/v1/workspace/prices")
    assert empty.status_code == 200 and empty.json() == {"prices": []}

    row = {
        "provider_id": "bey-avatar",
        "unit": "minutes",
        "usd_per_unit": "0.10",
        "note": "Starter plan",
        "as_of": "x",
    }
    saved = await admin_client.put("/v1/workspace/prices", json={"prices": [row]})
    assert saved.status_code == 200, saved.text
    stored = saved.json()["prices"][0]
    assert stored["as_of"] == utcnow().date().isoformat()
    assert (await admin_client.get("/v1/workspace/prices")).json()["prices"] == [stored]

    unknown = await admin_client.put(
        "/v1/workspace/prices", json={"prices": [{**row, "provider_id": "made-up-avatar"}]}
    )
    bad_unit = await admin_client.put("/v1/workspace/prices", json={"prices": [{**row, "unit": "seconds"}]})
    duplicate = await admin_client.put("/v1/workspace/prices", json={"prices": [row, row]})
    assert unknown.status_code == 422
    assert bad_unit.status_code == 422
    assert duplicate.status_code == 422

    async with database.session() as session:
        actions = (
            (
                await session.execute(
                    select(AuditLog.action).where(AuditLog.action == "workspace.prices_updated")
                )
            )
            .scalars()
            .all()
        )
    assert len(actions) == 1


async def test_a_workspace_price_prices_the_avatar_in_the_agent_estimate(
    admin_client: httpx.AsyncClient,
) -> None:
    await admin_client.put(
        "/v1/workspace/prices",
        json={
            "prices": [{"provider_id": "bey-avatar", "unit": "minutes", "usd_per_unit": "0.10", "as_of": ""}]
        },
    )
    config = json.loads(
        inference_config()
        .model_copy(
            update={
                "pipeline": inference_config().pipeline.model_copy(
                    update={"avatar": ProviderRef(provider_id="bey-avatar")}
                )
            }
        )
        .model_dump_json()
    )
    body = (await admin_client.post("/v1/cost-estimates", json={"config": config})).json()
    avatar = next(line for line in body["lines"] if line["slot"] == "avatar")
    assert avatar["quote"]["source"] == "workspace"
    assert Decimal(avatar["usd_per_min"]) == Decimal("0.1")


async def test_assumptions_route_lists_defaults(admin_client: httpx.AsyncClient) -> None:
    body = (await admin_client.get("/v1/cost-estimates/assumptions")).json()
    keys = {a["key"]: a for a in body["assumptions"]}
    assert keys["session_minutes"]["source"] == "default"
    assert keys["agent_talk_ratio"]["low"] == 0.3
    assert body["sessions_sampled"] == 0


async def test_new_routes_say_estimate_in_their_openapi_description(app: FastAPI) -> None:
    spec = app.openapi()
    for path, method in (
        ("/v1/cost-estimates", "post"),
        ("/v1/pricing/quotes", "post"),
        ("/v1/cost-estimates/assumptions", "get"),
        ("/v1/workspace/prices", "get"),
        ("/v1/workspace/prices", "put"),
        ("/v1/agents/{agent_id}/cost-estimate", "post"),
    ):
        assert "estimate" in spec["paths"][path][method]["description"].lower(), path


# ------------------------------------------------------------------------ workspace averages
async def _ended_sessions(database: Database, agent_id: str, count: int) -> None:
    now = utcnow()
    async with database.session() as session:
        for index in range(count):
            session.add(
                SessionRow(
                    workspace_id=DEFAULT_WORKSPACE_ID,
                    agent_id=agent_id,
                    config_version=1,
                    room_name=f"lkap-avg-{index}",
                    participant_identity="u",
                    participant_name="U",
                    status="ended",
                    pipeline_mode="cascaded",
                    created_at=now - dt.timedelta(hours=1 + index),
                    started_at=now - dt.timedelta(hours=1 + index),
                    ended_at=now - dt.timedelta(hours=1 + index) + dt.timedelta(minutes=2),
                    latency={"turns": 8},
                    usage={
                        "model_usage": [
                            {"type": "llm_usage", "input_tokens": 12000, "output_tokens": 400},
                            {"type": "tts_usage", "characters_count": 1200},
                            {"type": "stt_usage", "audio_duration": 120.0},
                        ]
                    },
                )
            )


@pytest.mark.parametrize(("count", "derived"), [(MIN_SESSIONS - 1, False), (MIN_SESSIONS, True)])
async def test_workspace_averages_need_ten_sessions(
    admin_client: httpx.AsyncClient, database: Database, count: int, derived: bool
) -> None:
    agent = await create_agent(admin_client, name=f"Averages {count}")
    await _ended_sessions(database, str(agent["id"]), count)

    async with database.session() as session:
        averages = await workspace_assumptions(session, DEFAULT_WORKSPACE_ID, use_cache=False)

    assert bool(averages.assumptions) is derived
    if derived:
        minutes = averages.assumptions["session_minutes"]
        assert minutes.source == "workspace" and minutes.value == 2.0
        assert averages.assumptions["agent_turns_per_min"].value == 4.0
        assert averages.assumptions["output_tokens_per_turn"].value == 50.0
        assert averages.assumptions["stt_billing"].value == "stream"
        response = await admin_client.post(
            f"/v1/agents/{agent['id']}/cost-estimate", json={"workspace_averages": True}
        )
        sources = {a["key"]: a["source"] for a in response.json()["assumptions"]}
        assert sources["session_minutes"] == "workspace"
        plain = await admin_client.post(f"/v1/agents/{agent['id']}/cost-estimate")
        assert {a["key"]: a["source"] for a in plain.json()["assumptions"]}["session_minutes"] == "default"


# ------------------------------------------------------------------------ the snapshot
async def _estimate_of(database: Database, session_id: str) -> tuple[Any, Any]:
    async with database.session() as session:
        row = await session.get(SessionRow, session_id)
        assert row is not None
        return row.estimate, row.channel


async def test_connect_snapshots_the_estimate_after_the_response(
    admin_client: httpx.AsyncClient, database: Database
) -> None:
    agent = await create_agent(admin_client, name="Snapshot")
    response = await admin_client.post(f"/v1/agents/{agent['id']}/connect", json={})
    assert response.status_code == 200, response.text

    estimate, _channel = await _estimate_of(database, response.json()["sessionId"])

    assert estimate is not None
    assert estimate["channel"] == "web"
    assert estimate["price_version"]
    assert {line["slot"] for line in estimate["lines"]} >= {"stt", "llm", "tts", "livekit_agent"}


async def test_a_text_session_snapshot_has_call_minutes_and_no_audio_lines(
    admin_client: httpx.AsyncClient, database: Database
) -> None:
    agent = await create_agent(admin_client, name="Text snapshot")
    response = await admin_client.post(f"/v1/agents/{agent['id']}/text-sessions", json={})
    assert response.status_code == 200, response.text

    estimate, channel = await _estimate_of(database, response.json()["sessionId"])

    assert channel == "text"
    slots = {line["slot"] for line in estimate["lines"]}
    assert "livekit_agent" in slots and "livekit_participant" in slots
    assert not slots & {"stt", "tts", "avatar", "livekit_sip"}


async def test_the_snapshot_is_idempotent_and_pinned(
    admin_client: httpx.AsyncClient, database: Database
) -> None:
    agent = await create_agent(admin_client, name="Idempotent snapshot")
    session_id = (await admin_client.post(f"/v1/agents/{agent['id']}/connect", json={})).json()["sessionId"]
    first, _ = await _estimate_of(database, session_id)

    # Change the agent afterwards: the stored snapshot must not move.
    new_config = json.loads(inference_config().model_dump_json())
    new_config["qa"] = {"enabled": True}
    await admin_client.put(f"/v1/agents/{agent['id']}", json={"config": new_config})
    await attach_estimate(database, session_id)

    second, _ = await _estimate_of(database, session_id)
    assert second == first


async def test_a_failing_estimate_never_fails_session_creation(
    admin_client: httpx.AsyncClient, database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("estimator down")

    monkeypatch.setattr(snapshot_mod, "build_estimate", boom)
    agent = await create_agent(admin_client, name="Boom")

    response = await admin_client.post(f"/v1/agents/{agent['id']}/connect", json={})

    assert response.status_code == 200
    estimate, _ = await _estimate_of(database, response.json()["sessionId"])
    assert estimate is None


async def test_attach_estimate_ignores_an_unknown_session(database: Database) -> None:
    await attach_estimate(database, "f" * 32)  # never raises


async def test_the_agent_row_is_untouched_by_an_estimate(
    admin_client: httpx.AsyncClient, database: Database
) -> None:
    agent = await create_agent(admin_client, name="Untouched")
    await admin_client.post(f"/v1/agents/{agent['id']}/cost-estimate")
    async with database.session() as session:
        row = await session.get(Agent, str(agent["id"]))
        assert row is not None and row.config_version == agent["config_version"]
