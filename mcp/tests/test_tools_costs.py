"""The cost tools (V4-15, docs/v4/COSTS.md §6, R-V4-51): read-only, against the scratch api."""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

import httpx
from conftest import BUILDER_SCOPES, READ_ONLY_SCOPES

from lkap_mcp.catalog import declared_specs
from lkap_mcp.registry import READ

COST_TOOLS = ("cost_estimate", "pricing_quote", "cost_summary")


def _mutations(mcp: Any) -> list[httpx.Request]:
    """Requests that would change state (the cost routes are POSTs that write nothing)."""
    return [
        r
        for r in mcp.transport.requests
        if r.method in ("PUT", "PATCH", "DELETE")
        or (r.method == "POST" and not r.url.path.endswith(("/cost-estimate", "/cost-estimates", "/quotes")))
    ]


def test_the_cost_tools_are_declared_read_only() -> None:
    specs = {spec.name: spec for spec in declared_specs() if spec.name in COST_TOOLS}
    assert set(specs) == set(COST_TOOLS)
    for spec in specs.values():
        assert spec.annotations == READ
    assert "not a bill" in (specs["cost_estimate"].description or "")


async def test_cost_estimate_for_a_template_with_a_session_length(key: Any, mcp_session: Any) -> None:
    raw = await key(READ_ONLY_SCOPES)

    async with mcp_session(raw) as mcp:
        result = await mcp.call("cost_estimate", template_id="blank", session_minutes=3)
        writes = _mutations(mcp)

    assert result["ok"] is True, result
    data = result["data"]
    assert data["session_minutes"] == 3.0
    minutes = next(a for a in data["assumptions"] if a["key"] == "session_minutes")
    assert minutes["source"] == "request"
    assert {line["slot"] for line in data["lines"]} >= {"livekit_agent", "livekit_participant"}
    assert writes == []


async def test_cost_estimate_for_an_agent_never_writes(key: Any, mcp_session: Any, admin: Any) -> None:
    created = await admin.post("/v1/agents", json={"name": "Costed", "template_id": "blank"})
    assert created.status_code == 201, created.text
    agent = created.json()
    raw = await key(READ_ONLY_SCOPES)

    async with mcp_session(raw) as mcp:
        result = await mcp.call("cost_estimate", agent_id_or_slug=agent["slug"], channel="text")
        sent = [r for r in mcp.transport.requests if r.url.path.endswith("/cost-estimate")]
        writes = _mutations(mcp)

    assert result["ok"] is True, result
    assert json.loads(sent[0].content)["channel"] == "text"
    assert all(line["slot"] not in ("stt", "tts") for line in result["data"]["lines"])
    assert writes == []
    after = (await admin.get(f"/v1/agents/{agent['id']}")).json()
    assert after["config_version"] == agent["config_version"]


async def test_cost_estimate_needs_exactly_one_source(key: Any, mcp_session: Any) -> None:
    raw = await key(READ_ONLY_SCOPES)

    async with mcp_session(raw) as mcp:
        none = await mcp.call("cost_estimate")
        two = await mcp.call("cost_estimate", template_id="blank", agent_id_or_slug="x")
        sent = [r for r in mcp.transport.requests if "cost-estimate" in r.url.path]

    assert none["ok"] is False and none["error"]["code"] == "invalid_arguments"
    assert two["ok"] is False
    assert sent == []


async def test_pricing_quote_returns_the_source_date_and_share(key: Any, mcp_session: Any) -> None:
    raw = await key(READ_ONLY_SCOPES)

    async with mcp_session(raw) as mcp:
        priced = await mcp.call(
            "pricing_quote", provider_id="livekit-inference-tts", model="cartesia/sonic-3"
        )
        unpriced = await mcp.call("pricing_quote", provider_id="elevenlabs-tts")

    assert priced["ok"] is True, priced
    data = priced["data"]
    assert data["quotes"][0]["source"] == "table"
    assert data["quotes"][0]["as_of"] and data["price_version"]
    assert Decimal(data["per_minute_usd"]) == Decimal("0.02025")
    assert unpriced["data"]["note"] == "no price"
    assert unpriced["data"]["per_minute_usd"] is None


async def test_cost_summary_wraps_the_analytics_summary(key: Any, mcp_session: Any) -> None:
    raw = await key(BUILDER_SCOPES)

    async with mcp_session(raw) as mcp:
        result = await mcp.call("cost_summary", range="7d")
        sent = [r for r in mcp.transport.requests if r.url.path == "/v1/analytics/summary"]

    assert result["ok"] is True, result
    assert sent[0].url.params["range"] == "7d"
    assert {"estimated_usd", "accuracy_pct", "top_drivers"} <= set(result["data"])
