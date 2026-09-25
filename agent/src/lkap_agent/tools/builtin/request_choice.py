"""`request_choice` built-in tool (V5-08, B1): ask the caller to pick from a few options.

The tool writes the question and the options into a `choices` block, then
waits on `UiChannel.request_block` (V5-02) for the caller's tap. The caller may
answer by voice instead: the model then calls `resolve_choice`.

* **cascaded**: blocking. The tool returns `{"selected": [...], "labels":
  [...]}` on a tap, or a "not picked" line after a timeout, a cancel or a
  barge-in (the caller speaking while the options are up cancels the request,
  R-V5-1; the block shows `cancelled`).
* **realtime / half_cascade**: returns `None` at once and the wait runs on the
  session's `BackgroundRunner`, exactly like `request_form`; a tap arrives as
  an *urgent* background result, a missed pick as a routine note.
  `PlatformAgent` keeps the model silent after the call (R-V5-1, D-W2-9i).
  An answer the model itself recorded with `resolve_choice` (`via: "voice"`)
  is not announced back to it.
* **phone channels** (`sip_in`, `sip_out`): nothing is shown (the caller has no
  screen); the tool answers `{"channel": "voice_only"}` at once and the model
  asks out loud (`PlatformAgent` does not silence the reply there, in any mode).
* **text channel**: a typed chat has no panel to tap (asks #30), so the tool
  answers at once and the model asks in the conversation.
"""

from __future__ import annotations

import json
from typing import Any, Final

from livekit.agents import FunctionTool, RunContext, ToolError, function_tool
from lkap_contracts.ui_protocol import UiPatchOp
from packs.base import PackSessionContext
from pydantic import BaseModel, Field, ValidationError

from lkap_agent.tools.builtin.request_form import BACKGROUND_FORM_MODES
from lkap_agent.ui.blocks import (
    VOICE_ONLY_CHANNELS,
    choice_selection_error,
    describe_blocks,
    pick_block,
    session_block_specs,
)

__all__ = [
    "CHOICE_TIMEOUT_S",
    "DEFAULT_MAX_OPTIONS",
    "VIA_VOICE",
    "VOICE_ONLY_CHANNELS",
    "ChoiceOptionIn",
    "build_request_choice_tool",
    "choice_labels",
]

#: How long the caller has to tap an option.
CHOICE_TIMEOUT_S: Final[float] = 60.0

#: `ChoicesBlockConfig.max_options` default.
DEFAULT_MAX_OPTIONS: Final[int] = 8

#: `via` value `resolve_choice` puts in the answer, so the background waiter stays quiet.
VIA_VOICE: Final[str] = "voice"


class ChoiceOptionIn(BaseModel):
    """One option the model offers."""

    id: str = Field(description="Short machine id of the option, e.g. minor.")
    label: str = Field(description="What the caller sees, e.g. Yes, minor injuries.")
    hint: str = Field(default="", description="Optional short line under the label.")


def choice_labels(state: dict[str, Any], selected: list[str]) -> list[str]:
    """The labels of `selected`, in order (ids stand in for unknown options)."""
    raw = state.get("options")
    options: list[Any] = raw if isinstance(raw, list) else []
    by_id = {o.get("id"): o.get("label") for o in options if isinstance(o, dict)}
    return [str(by_id.get(s) or s) for s in selected]


def _validate_options(options: list[ChoiceOptionIn], max_options: int) -> None:
    if len(options) < 2:
        raise ToolError("Give at least two options.")
    if len(options) > max_options:
        raise ToolError(f"Give at most {max_options} options.")
    ids = [o.id.strip() for o in options]
    if not all(ids) or len(set(ids)) != len(ids):
        raise ToolError("Option ids must be unique and non-empty.")
    if not all(o.label.strip() for o in options):
        raise ToolError("Every option needs a label.")


def _text_channel_note(prompt: str, options: list[ChoiceOptionIn]) -> str:
    labels = "; ".join(o.label for o in options)
    return (
        "Nothing was shown: this is a text chat, and options cannot be tapped here. "
        f"Ask the question in the conversation: {prompt} Options: {labels}."
    )


def build_request_choice_tool(ctx: PackSessionContext) -> FunctionTool[..., Any]:
    """Build the `request_choice` tool bound to `ctx`."""
    inventory = describe_blocks(session_block_specs(ctx.ui, ctx.config.panel), ["choices"])

    async def wait_for_choice(target: str) -> dict[str, Any] | None:
        """Wait for the caller's pick; `{"selected", "labels", "via"?}` or `None`."""
        values = await ctx.ui.request_block(target, timeout_s=CHOICE_TIMEOUT_S)
        if values is None:
            return None
        state = ctx.ui.state.blocks.get(target) or {}
        selected = values.get("selected")
        error = choice_selection_error(state, selected)
        if error is not None or not isinstance(selected, list):
            ctx.log.warning("builtin_tool.request_choice.bad_answer", block_id=target, error=error)
            return None
        answer: dict[str, Any] = {"selected": selected, "labels": choice_labels(state, selected)}
        if values.get("via") == VIA_VOICE:
            answer["via"] = VIA_VOICE
        return answer

    async def request_choice(
        context: RunContext[Any],
        prompt: str,
        options: list[ChoiceOptionIn],
        multi: bool = False,
        block_id: str = "",
    ) -> str | None:
        """Show the caller a few options on their screen and wait for their pick.

        Args:
            prompt: The question, e.g. Was anyone injured?
            options: The options, in order (two or more).
            multi: Whether the caller may pick more than one.
            block_id: The choices block to use; leave empty when there is only one.
        """
        specs = session_block_specs(ctx.ui, ctx.config.panel)
        try:
            target = pick_block(specs, "choices", block_id or None)
        except ValueError as exc:
            raise ToolError(str(exc)) from exc
        config = next((s.config for s in specs if s.id == target), {})
        max_options = config.get("max_options")
        _validate_options(options, max_options if isinstance(max_options, int) else DEFAULT_MAX_OPTIONS)
        if not prompt.strip():
            raise ToolError("Give the question to show above the options.")
        channel = getattr(ctx, "channel", "web")
        call_id = context.function_call.call_id
        if channel in VOICE_ONLY_CHANNELS:
            ctx.log.debug("builtin_tool.request_choice", call_id=call_id, block_id=target, voice_only=True)
            return json.dumps({"channel": "voice_only"})
        if channel == "text":
            ctx.log.debug("builtin_tool.request_choice", call_id=call_id, block_id=target, text_channel=True)
            return _text_channel_note(prompt, options)

        option_rows: list[dict[str, Any]] = [
            {"id": o.id.strip(), "label": o.label.strip(), **({"hint": o.hint} if o.hint.strip() else {})}
            for o in options
        ]
        is_multi = bool(multi or config.get("multi") is True)
        content: dict[str, Any] = {
            "prompt": prompt.strip(),
            "options": option_rows,
            "multi": is_multi,
            "selected": [],
            "reveal": None,
        }
        try:
            await ctx.ui.patch_block(
                target, [UiPatchOp(op="set", path=f"/{k}", value=v) for k, v in content.items()]
            )
        except ValidationError as exc:
            raise ToolError(f"Those options do not fit the block: {exc.errors()[0]['msg']}.") from exc
        background = ctx.pipeline_mode in BACKGROUND_FORM_MODES
        ctx.log.debug(
            "builtin_tool.request_choice",
            call_id=call_id,
            block_id=target,
            options=[o["id"] for o in option_rows],
            multi=is_multi,
            background=background,
        )

        if background:
            ctx.background.submit(
                name="request_choice",
                coro=wait_for_choice(target),
                urgent=lambda answer: answer is not None and answer.get("via") != VIA_VOICE,
                urgent_instructions=lambda answer: (
                    f"The caller just picked {', '.join(answer['labels'])} on screen for "
                    f"{content['prompt']!r}. Acknowledge it briefly and continue."
                ),
                routine_note=lambda answer: (
                    None
                    if answer is not None
                    else f"The caller did not pick an option in the {target} block on screen."
                ),
                call_id=call_id,
            )
            return None

        answer = await wait_for_choice(target)
        if answer is None:
            return (
                "The caller did not pick an option on screen (they spoke, closed it or it timed out). "
                "If they answered out loud, call resolve_choice with the option ids they chose; "
                "otherwise ask again."
            )
        return json.dumps({"selected": answer["selected"], "labels": answer["labels"]})

    return function_tool(
        request_choice,
        description=(
            "Show the caller a few options on their screen and wait for their pick. "
            "Read at most three options aloud and say they can also tap one. "
            "If the caller answers out loud instead, call resolve_choice with the option ids. "
            f"Choices blocks: {inventory}."
        ),
    )
