"""`cart_set` built-in tool (V6-23, D-V6-20): show lines and totals in a `cart` block.

An order to confirm, a quote, a list of parts: the model passes the lines (name, quantity,
price of one) and any adjustments under the subtotal (a discount as a negative amount, a tax, a
delivery fee), and the platform adds them up (`lkap_contracts.ui_protocol.cart_totals`, to the
cent), so the caller never sees a total that is not the sum of what is listed. The cart replaces
what the block showed. Display only: nothing is ordered or charged by this block (the agent's
own tools do that). Refused, with the reason, before anything is written: more lines than the
block's `max_lines`, a duplicate line id, a price or quantity out of range, a currency that is
not a three-letter code. A write: blocking, instant, never in the background; it answers the
total on a cascaded pipeline and nothing on a realtime model (a half cascade too). On phone
channels nothing is shown: the tool answers ``{"visible": false}`` with the total, so the model
can say it.
"""

from __future__ import annotations

import json
import re
import time
from typing import Annotated, Any, Final

from livekit.agents import FunctionTool, RunContext, ToolError, function_tool
from lkap_contracts.blocks import CartBlockConfig
from lkap_contracts.ui_protocol import CURRENCY_PATTERN, CartAdjustment, CartBlockState, CartLine, cart_totals
from packs.base import PackSessionContext
from pydantic import BaseModel, Field, ValidationError

from lkap_agent.tools.json_args import json_list
from lkap_agent.tools.untrusted import strip_control
from lkap_agent.ui.blocks import VOICE_ONLY_CHANNELS, describe_blocks, pick_block, session_block_specs

from .set_checklist import QUIET_MODES

__all__ = ["CartAdjustmentIn", "CartLineIn", "build_cart_set_tool", "priced_cart"]

_CURRENCY_RE: Final[re.Pattern[str]] = re.compile(CURRENCY_PATTERN)


class CartLineIn(BaseModel):
    """One line of the cart."""

    id: str = Field(description="A short id, e.g. water_filter.")
    name: str = Field(description="What it is, e.g. Water filter.")
    quantity: int = Field(default=1, description="How many.")
    unit_price: float = Field(description="The price of one, e.g. 24.50.")
    note: str = Field(default="", description="An optional short note, e.g. Monday morning.")


class CartAdjustmentIn(BaseModel):
    """A line under the subtotal."""

    label: str = Field(description="e.g. Discount, Tax or Delivery.")
    amount: float = Field(description="The amount; a discount is negative, e.g. -10.")


#: One line and one adjustment as the model writes them (V6-30).
CART_LINE_EXAMPLE: Final[str] = '{"id": "filter", "name": "Water filter", "quantity": 2, "unit_price": 24.5}'
CART_ADJUSTMENT_EXAMPLE: Final[str] = '{"label": "Discount", "amount": -5}'
#: JSON text of either list is read too (V6-30, F-2).
CartLineList = Annotated[list[CartLineIn], json_list(CART_LINE_EXAMPLE)]
CartAdjustmentList = Annotated[list[CartAdjustmentIn] | None, json_list(CART_ADJUSTMENT_EXAMPLE)]


def _text(value: str) -> str:
    return " ".join(strip_control(value).split())


def priced_cart(
    currency: str, lines: list[dict[str, Any]], adjustments: list[dict[str, Any]]
) -> dict[str, Any]:
    """A cart's state with its line totals, subtotal and total computed (V6-23).

    Shared with `update_block`, so every write of a cart adds up the same way.

    Raises:
        pydantic.ValidationError: When a line, an adjustment or the cart does not fit.
    """
    parsed = [CartLine.model_validate({**line, "line_total": 0}) for line in lines]
    extra = [CartAdjustment.model_validate(adjustment) for adjustment in adjustments]
    line_totals, subtotal, total = cart_totals(parsed, extra)
    state = CartBlockState(
        currency=currency,
        lines=[
            line.model_copy(update={"line_total": t}) for line, t in zip(parsed, line_totals, strict=True)
        ],
        adjustments=extra,
        subtotal=subtotal,
        total=total,
        updated_at=time.time(),
    )
    return state.model_dump(mode="json")


def build_cart_set_tool(ctx: PackSessionContext) -> FunctionTool[..., Any]:
    """Build the `cart_set` tool bound to `ctx`."""
    inventory = describe_blocks(session_block_specs(ctx.ui, ctx.config.panel), ["cart"])

    async def cart_set(
        context: RunContext[Any],
        lines: CartLineList,
        adjustments: CartAdjustmentList = None,
        currency: str = "",
        block_id: str = "",
    ) -> str | None:
        """Show the cart on the caller's screen (its lines, then discounts, tax or fees), replacing it.

        Args:
            lines: Every line of the cart; the platform adds up the totals.
            adjustments: Optional lines under the subtotal; a discount is a negative amount.
            currency: A three-letter code such as USD; leave empty for the block's currency.
            block_id: The cart block; leave empty when there is only one.
        """
        specs = session_block_specs(ctx.ui, ctx.config.panel)
        try:
            target = pick_block(specs, "cart", block_id or None)
        except ValueError as exc:
            raise ToolError(str(exc)) from exc
        spec = next(s for s in specs if s.id == target)
        try:
            config = CartBlockConfig.model_validate(spec.config)
        except ValidationError:
            config = CartBlockConfig()
        if len(lines) > config.max_lines:
            raise ToolError(f"That is {len(lines)} lines. This cart shows at most {config.max_lines}.")
        code = currency.strip().upper() or config.currency
        if _CURRENCY_RE.match(code) is None:
            raise ToolError("currency is a three-letter code such as USD, EUR or INR.")
        try:
            state = priced_cart(
                code,
                [
                    {
                        "id": line.id.strip(),
                        "name": _text(line.name),
                        "quantity": line.quantity,
                        "unit_price": line.unit_price,
                        "note": _text(line.note) or None,
                    }
                    for line in lines
                ],
                [{"label": _text(a.label), "amount": a.amount} for a in adjustments or []],
            )
        except ValidationError as exc:
            error = exc.errors()[0]
            where = ".".join(str(part) for part in error["loc"])
            raise ToolError(f"The cart does not fit: {where + ': ' if where else ''}{error['msg']}.") from exc
        total = f"{state['total']:.2f} {code}"
        if getattr(ctx, "channel", "web") in VOICE_ONLY_CHANNELS:
            return json.dumps({"visible": False, "total": total})
        await ctx.ui.set_block(target, state)
        show = getattr(ctx.ui, "show_block", None)
        if callable(show):
            show(target)
        ctx.log.debug(
            "builtin_tool.cart_set",
            call_id=context.function_call.call_id,
            block_id=target,
            lines=len(state["lines"]),
        )
        if ctx.pipeline_mode in QUIET_MODES:
            return None
        return f"The cart is on screen; its total is {total}. Say the total; do not read every line."

    return function_tool(
        cart_set,
        description=(
            "Show a cart on the caller's screen: its lines (name, quantity, price of one) and any "
            "discount, tax or fee; the platform adds up the totals. It orders or charges nothing. "
            f"Lines are objects, e.g. lines=[{CART_LINE_EXAMPLE}], adjustments=[{CART_ADJUSTMENT_EXAMPLE}]. "
            f"Cart blocks: {inventory}."
        ),
    )
