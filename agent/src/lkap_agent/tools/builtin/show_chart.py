"""`show_chart` built-in tool (V6-23, D-V6-20): show numbers as a chart in a `chart` block.

One big ``number``, ``bar`` s, a ``line``, a ``pie`` or a ``gauge`` (one value on a scale); the
console draws it as inline SVG, no chart library. The chart replaces what the block showed. At
most 200 points: a 201st is refused with the reason, before anything is written, and so is a
chart that does not fit its kind (two values on a gauge, a negative pie slice, more than eight
series); `ChartBlockState` checks every bound again. Every text is the agent's own words, shown
as plain text. A write: blocking, instant, never in the background; it answers nothing on a
realtime model (a half cascade too). On phone channels nothing is shown: the tool answers
``{"visible": false}`` and the model says the numbers.
"""

from __future__ import annotations

import json
import time
from typing import Any, Final

from livekit.agents import FunctionTool, RunContext, ToolError, function_tool
from lkap_contracts.blocks import ChartBlockConfig
from lkap_contracts.ui_protocol import MAX_CHART_POINTS, ChartBlockState
from packs.base import PackSessionContext
from pydantic import BaseModel, Field, ValidationError

from lkap_agent.tools.untrusted import strip_control
from lkap_agent.ui.blocks import VOICE_ONLY_CHANNELS, describe_blocks, pick_block, session_block_specs

from .set_checklist import QUIET_MODES

__all__ = ["ChartPointIn", "build_show_chart_tool"]

#: What the model hears (nothing on a realtime pipeline).
_SHOWN: Final[str] = "The chart is on screen. Say what it shows in a sentence; do not read every value."


class ChartPointIn(BaseModel):
    """One value of the chart."""

    label: str = Field(description="What the value is, e.g. Jan, or Home cover.")
    value: float = Field(description="The number.")
    series: str = Field(default="", description="Optional: which line or bar group it belongs to.")


def _text(value: str) -> str | None:
    text = " ".join(strip_control(value).split())
    return text or None


def build_show_chart_tool(ctx: PackSessionContext) -> FunctionTool[..., Any]:
    """Build the `show_chart` tool bound to `ctx`."""
    inventory = describe_blocks(session_block_specs(ctx.ui, ctx.config.panel), ["chart"])

    async def show_chart(
        context: RunContext[Any],
        points: list[ChartPointIn],
        kind: str = "",
        title: str = "",
        unit: str = "",
        caption: str = "",
        gauge_min: float = 0,
        gauge_max: float = 100,
        block_id: str = "",
    ) -> str | None:
        """Show numbers as a chart on the caller's screen, replacing what it showed.

        Args:
            points: The values, in order (at most 200). A number or gauge chart takes one.
            kind: number, bar, line, pie or gauge; leave empty for the block's usual kind.
            title: An optional heading.
            unit: An optional unit, e.g. USD or claims.
            caption: An optional one-line note under the chart.
            gauge_min: The low end of a gauge's scale.
            gauge_max: The high end of a gauge's scale.
            block_id: The chart block; leave empty when there is only one.
        """
        specs = session_block_specs(ctx.ui, ctx.config.panel)
        try:
            target = pick_block(specs, "chart", block_id or None)
        except ValueError as exc:
            raise ToolError(str(exc)) from exc
        if getattr(ctx, "channel", "web") in VOICE_ONLY_CHANNELS:
            return json.dumps({"visible": False})
        if len(points) > MAX_CHART_POINTS:
            raise ToolError(
                f"That is {len(points)} points; a chart shows at most {MAX_CHART_POINTS}. "
                "Group or trim them first."
            )
        spec = next(s for s in specs if s.id == target)
        try:
            configured = ChartBlockConfig.model_validate(spec.config).kind
        except ValidationError:
            configured = ChartBlockConfig().kind
        try:
            state = ChartBlockState.model_validate(
                {
                    "kind": kind.strip().lower() or configured,
                    "title": _text(title),
                    "unit": _text(unit),
                    "points": [
                        {"label": _text(p.label) or "", "value": p.value, "series": _text(p.series)}
                        for p in points
                    ],
                    "gauge_min": gauge_min,
                    "gauge_max": gauge_max,
                    "caption": _text(caption),
                    "updated_at": time.time(),
                }
            )
        except ValidationError as exc:
            error = exc.errors()[0]
            where = ".".join(str(part) for part in error["loc"])
            raise ToolError(
                f"The chart does not fit: {where + ': ' if where else ''}{error['msg']}."
            ) from exc
        await ctx.ui.set_block(target, state.model_dump(mode="json"))
        show = getattr(ctx.ui, "show_block", None)
        if callable(show):
            show(target)
        ctx.log.debug(
            "builtin_tool.show_chart",
            call_id=context.function_call.call_id,
            block_id=target,
            kind=state.kind,
            points=len(state.points),
        )
        if ctx.pipeline_mode in QUIET_MODES:
            return None
        return _SHOWN

    return function_tool(
        show_chart,
        description=(
            "Show numbers as a chart on the caller's screen: one big number, bars, lines, a pie or "
            "a gauge, at most 200 points. Say in a sentence what it shows; the numbers are on "
            f"screen. Chart blocks: {inventory}."
        ),
    )
