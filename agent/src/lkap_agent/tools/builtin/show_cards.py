"""`show_cards` built-in tool (V5-43, B10): show options side by side as cards.

Plans, repair shops, offers: each card has a title, an optional subtitle and
picture, a few facts, badges and up to three buttons. The tool replaces the
block's cards (``replace``, the default) or upserts them by id. A picture is
either a file already shown in the session (``image_asset_id``) or an
``https://`` image on one of the block's ``image_hosts``; anything else is
refused with the reason. A tap on a card (``select``) or a button reaches the
model as a message from ``PlatformAgent``. On phone channels nothing is shown:
the tool answers ``{"visible": false}`` and the model describes the options.
"""

from __future__ import annotations

import json
from typing import Annotated, Any, Final

from livekit.agents import FunctionTool, RunContext, ToolError, function_tool
from lkap_contracts.blocks import CardsBlockConfig
from lkap_contracts.ui_protocol import MAX_CARDS, Card, CardsBlockState, https_url_problem
from packs.base import PackSessionContext
from pydantic import BaseModel, Field, ValidationError

from lkap_agent.tools.json_args import json_list
from lkap_agent.ui.blocks import VOICE_ONLY_CHANNELS, describe_blocks, pick_block, session_block_specs

__all__ = ["CardActionIn", "CardFactIn", "CardIn", "build_show_cards_tool"]

#: `CardsBlockConfig.max_cards` default.
DEFAULT_MAX_CARDS: Final[int] = 10


class CardFactIn(BaseModel):
    """One label and value line on a card."""

    label: str = Field(description="e.g. Excess")
    value: str = Field(description="e.g. 100 GBP")


class CardActionIn(BaseModel):
    """A button on a card."""

    name: str = Field(description="Lower-case machine name the tap reports, e.g. choose.")
    label: str = Field(description="Button text, e.g. Choose Gold.")


#: JSON text of a card's facts and buttons is read too (V6-30, F-2).
CardFactList = Annotated[list[CardFactIn], json_list('{"label": "Excess", "value": "100 GBP"}')]
CardActionList = Annotated[list[CardActionIn], json_list('{"name": "choose", "label": "Choose Gold"}')]


class CardIn(BaseModel):
    """One card the model shows."""

    id: str = Field(description="Short machine id, e.g. gold.")
    title: str = Field(description="e.g. Gold cover.")
    subtitle: str = Field(default="", description="Optional short line under the title.")
    image_url: str = Field(default="", description="Optional https picture on an allowed site.")
    image_asset_id: str = Field(
        default="", description="Optional id of a picture already shown in this call."
    )
    facts: CardFactList = Field(default=[], description="Up to eight label/value lines.")
    badges: list[str] = Field(default=[], description="Up to five short badges, e.g. Recommended.")
    actions: CardActionList = Field(default=[], description="Up to three buttons.")


#: JSON text of the cards is read too (V6-30, F-2).
CardList = Annotated[list[CardIn], json_list('{"id": "gold", "title": "Gold cover", "facts": []}')]


def _config(spec_config: dict[str, Any]) -> CardsBlockConfig:
    try:
        return CardsBlockConfig.model_validate(spec_config)
    except ValidationError:
        return CardsBlockConfig()


def build_show_cards_tool(ctx: PackSessionContext) -> FunctionTool[..., Any]:
    """Build the `show_cards` tool bound to `ctx`."""
    inventory = describe_blocks(session_block_specs(ctx.ui, ctx.config.panel), ["cards"])

    def checked(card: CardIn, config: CardsBlockConfig, assets: set[str]) -> dict[str, Any]:
        image_url = card.image_url.strip() or None
        if image_url is not None:
            if not config.image_hosts:
                raise ToolError(
                    "This cards block shows no pictures from the web; use image_asset_id or none."
                )
            problem = https_url_problem(image_url, allowed_hosts=config.image_hosts)
            if problem is not None:
                raise ToolError(f"Card {card.id!r}: {problem}.")
        asset = card.image_asset_id.strip() or None
        if asset is not None and asset not in assets:
            raise ToolError(f"Card {card.id!r}: no picture {asset!r} was shown in this call.")
        try:
            model = Card.model_validate(
                {
                    "id": card.id.strip(),
                    "title": card.title.strip(),
                    "subtitle": card.subtitle.strip() or None,
                    "image_url": image_url,
                    "image_asset_id": asset,
                    "facts": [{"label": f.label.strip(), "value": f.value.strip()} for f in card.facts],
                    "badges": [b.strip() for b in card.badges if b.strip()],
                    "actions": [{"name": a.name.strip(), "label": a.label.strip()} for a in card.actions],
                }
            )
        except ValidationError as exc:
            error = exc.errors()[0]
            where = ".".join(str(p) for p in error["loc"])
            raise ToolError(f"Card {card.id!r} does not fit ({where}: {error['msg']}).") from exc
        return model.model_dump(mode="json")

    async def show_cards(
        context: RunContext[Any], cards: CardList, replace: bool = True, block_id: str = ""
    ) -> str:
        """Show options side by side as cards in the caller's side panel.

        Args:
            cards: The cards, in order.
            replace: True replaces what the block shows; false adds or updates cards by id.
            block_id: The cards block; leave empty when there is only one.
        """
        specs = session_block_specs(ctx.ui, ctx.config.panel)
        try:
            target = pick_block(specs, "cards", block_id or None)
        except ValueError as exc:
            raise ToolError(str(exc)) from exc
        if getattr(ctx, "channel", "web") in VOICE_ONLY_CHANNELS:
            return json.dumps({"visible": False})
        if not cards:
            raise ToolError("Pass at least one card.")
        config = _config(next((s.config for s in specs if s.id == target), {}))
        assets = {a.asset_id for a in ctx.ui.state.assets}
        rows = [checked(card, config, assets) for card in cards]
        state = ctx.ui.state.blocks.get(target) or {}
        current = [c for c in state.get("cards") or [] if isinstance(c, dict)] if not replace else []
        by_id = {c.get("id"): c for c in current}
        for row in rows:
            by_id[row["id"]] = row
        merged = list(by_id.values())
        limit = min(config.max_cards, MAX_CARDS)
        if len(merged) > limit:
            raise ToolError(f"This block shows at most {limit} cards.")
        selected = state.get("selected") if not replace and state.get("selected") in by_id else None
        try:
            value = CardsBlockState.model_validate({"cards": merged, "selected": selected})
        except ValidationError as exc:
            raise ToolError(f"Those cards do not fit the block: {exc.errors()[0]['msg']}.") from exc
        await ctx.ui.set_block(target, value.model_dump(mode="json"))
        ctx.log.debug(
            "builtin_tool.show_cards",
            call_id=context.function_call.call_id,
            block_id=target,
            cards=[r["id"] for r in rows],
            replace=replace,
        )
        return (
            f"Showing {len(merged)} card(s). Say the one thing that sets each apart in a few words; "
            "never read every fact. You will be told when the caller taps one."
        )

    return function_tool(
        show_cards,
        description=(
            "Show options side by side as cards (plans, repair shops, offers) in the caller's side panel, "
            f"each with a title, a few facts and up to three buttons. Cards blocks: {inventory}."
        ),
    )
