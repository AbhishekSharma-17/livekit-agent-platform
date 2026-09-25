"""Cost tools: the per-minute estimate, price quotes and the cost summary (docs/v4/COSTS.md §6, R-V4-51).

All three are read tools. The estimate is **at list prices, not a bill**: it
multiplies each price (the workspace's own, else OpenRouter's live sheet, else
the list-price table, each with its source and date) by a usage model whose
assumptions are named and overridable. Actual cost rides on ``session_list``
and ``session_get`` (``estimated_usd``, ``variance_usd``, ``reconciled_usd``,
``drivers``); workspace prices are written through ``api_request`` (``PUT
/v1/workspace/prices``) — there is no write tool for them in v1.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from lkap_contracts.agent_config import AgentConfig
from pydantic import Field

from lkap_mcp.registry import READ, Registry
from lkap_mcp.results import ToolResult
from lkap_mcp.tools._common import seg

EstimateChannel = Literal["web", "phone", "text"]
SummaryRange = Literal["today", "7d", "30d", "all"]

_ASSUMPTION_KEYS = (
    "session_minutes, caller_talk_ratio, agent_talk_ratio, stt_billing (stream|segments), speech_wpm, "
    "chars_per_word, agent_turns_per_min, prompt_tokens, history_tokens_per_turn, output_tokens_per_turn, "
    "tool_calls_per_session, kb_queries_per_turn, images_per_session, participants"
)


def register(registry: Registry) -> None:
    """Declare the cost tools."""
    client = registry.ctx.client

    @registry.tool(scopes={"agents:read"}, annotations=READ, data="CostEstimate")
    async def cost_estimate(
        agent_id_or_slug: Annotated[
            str | None, Field(description="An agent's id or slug (its saved config)")
        ] = None,
        template_id: Annotated[
            str | None, Field(description="A starter id from lkap://templates, e.g. receptionist")
        ] = None,
        config: Annotated[
            AgentConfig | None, Field(description="An unsaved draft config (validated, never stored)")
        ] = None,
        session_minutes: Annotated[
            float | None, Field(gt=0, le=240, description="Call length to assume (default 5)")
        ] = None,
        assumptions: Annotated[
            dict[str, float | str] | None,
            Field(description=f"Overrides by key: {_ASSUMPTION_KEYS}"),
        ] = None,
        channel: EstimateChannel = "web",
        workspace_averages: Annotated[
            bool, Field(description="Use the workspace's own session averages (needs 10 ended sessions)")
        ] = False,
    ) -> ToolResult:
        """Estimate what an agent costs per minute and per call: an estimate at list prices, not a bill.

        Give exactly one of agent_id_or_slug, template_id or config. The result
        breaks the figure down by part of the agent (plain labels), shows a
        low-high band, names every unpriced part, and dates every price.
        """
        given = [value for value in (agent_id_or_slug, template_id, config) if value is not None]
        if len(given) != 1:
            return ToolResult.fail(
                "invalid_arguments", "give exactly one of agent_id_or_slug, template_id or config"
            )
        overrides: dict[str, float | str] = dict(assumptions or {})
        if session_minutes is not None:
            overrides["session_minutes"] = session_minutes
        body: dict[str, Any] = {"channel": channel, "workspace_averages": workspace_averages}
        if overrides:
            body["assumptions"] = overrides
        if agent_id_or_slug is not None:
            result = await client.post(f"/v1/agents/{seg(agent_id_or_slug)}/cost-estimate", body)
        elif template_id is not None:
            result = await client.post("/v1/cost-estimates", {**body, "template_id": template_id})
        else:
            assert config is not None  # noqa: S101 - exactly one source, checked above
            result = await client.post(
                "/v1/cost-estimates", {**body, "config": config.model_dump(mode="json")}
            )
        warnings = [f"No published price for: {item}" for item in result.get("unpriced", [])[:10]]
        return ToolResult.success(result, warnings=warnings)

    @registry.tool(scopes={"agents:read"}, annotations=READ, data="PriceQuoteItem")
    async def pricing_quote(
        provider_id: Annotated[str, Field(description="A registry id, e.g. livekit-inference-tts")],
        model: Annotated[
            str | None, Field(description="The model id; default: the entry's default model")
        ] = None,
    ) -> ToolResult:
        """The prices LKAP would use for one provider/model, with source and date, and its ≈ $/min share."""
        response = await client.post(
            "/v1/pricing/quotes", {"items": [{"provider_id": provider_id, "model": model}]}
        )
        items = response.get("items") or [{}]
        data = {**items[0], "price_version": response.get("price_version"), "as_of": response.get("as_of")}
        return ToolResult.success(data)

    @registry.tool(scopes={"sessions:read"}, annotations=READ, data="AnalyticsSummary")
    async def cost_summary(range: SummaryRange = "30d") -> ToolResult:  # noqa: A002 - the api's own parameter
        """Sessions, minutes, actual cost and the estimate beside it (accuracy, top cost drivers)."""
        return ToolResult.success(await client.get("/v1/analytics/summary", params={"range": range}))
