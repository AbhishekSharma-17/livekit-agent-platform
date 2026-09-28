"""`show_code` built-in tool (V6-23, D-V6-20): show read-only code or text in a `code` block.

A configuration snippet, a JSON record, a command to type: shown in a fixed-width font with a
language label, replacing what the block showed. Display only: nothing shown here is ever run,
and the console renders it as the text of a code block (never as Markdown or HTML). The text
keeps its lines and tabs but loses every other control character; it is refused over the
block's `max_chars` (at most 20,000). The language is a plain label (``python``, ``json``,
``c++``); anything else is refused. A write: blocking, instant, never in the background; it
answers nothing on a realtime model (a half cascade too). On phone channels nothing is shown:
the tool answers ``{"visible": false}``.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any, Final

from livekit.agents import FunctionTool, RunContext, ToolError, function_tool
from lkap_contracts.blocks import CodeBlockConfig
from lkap_contracts.ui_protocol import CODE_LANGUAGE_PATTERN, CodeBlockState
from packs.base import PackSessionContext
from pydantic import ValidationError

from lkap_agent.tools.untrusted import strip_control
from lkap_agent.ui.blocks import VOICE_ONLY_CHANNELS, describe_blocks, pick_block, session_block_specs

from .set_checklist import QUIET_MODES

__all__ = ["build_show_code_tool"]

_LANGUAGE_RE: Final[re.Pattern[str]] = re.compile(CODE_LANGUAGE_PATTERN)
_SHOWN: Final[str] = "The code is on screen. Say in a sentence what it is; do not read it aloud."


def build_show_code_tool(ctx: PackSessionContext) -> FunctionTool[..., Any]:
    """Build the `show_code` tool bound to `ctx`."""
    inventory = describe_blocks(session_block_specs(ctx.ui, ctx.config.panel), ["code"])

    async def show_code(
        context: RunContext[Any], code: str, language: str = "", title: str = "", block_id: str = ""
    ) -> str | None:
        """Show code or text in a fixed-width font on the caller's screen (read-only, never run).

        Args:
            code: The code or text, with its line breaks.
            language: An optional label, e.g. python, json or shell.
            title: An optional heading.
            block_id: The code block; leave empty when there is only one.
        """
        specs = session_block_specs(ctx.ui, ctx.config.panel)
        try:
            target = pick_block(specs, "code", block_id or None)
        except ValueError as exc:
            raise ToolError(str(exc)) from exc
        if getattr(ctx, "channel", "web") in VOICE_ONLY_CHANNELS:
            return json.dumps({"visible": False})
        text = strip_control(code).replace("\r\n", "\n").strip("\n")
        if not text.strip():
            raise ToolError("Pass the code to show.")
        spec = next(s for s in specs if s.id == target)
        try:
            max_chars = CodeBlockConfig.model_validate(spec.config).max_chars
        except ValidationError:
            max_chars = CodeBlockConfig().max_chars
        if len(text) > max_chars:
            raise ToolError(f"The code is {len(text)} characters; this block shows at most {max_chars}.")
        label = language.strip().lower() or None
        if label is not None and _LANGUAGE_RE.match(label) is None:
            raise ToolError("language is a short label such as python, json or shell.")
        heading = " ".join(strip_control(title).split()) or None
        try:
            state = CodeBlockState(code=text, language=label, title=heading, updated_at=time.time())
        except ValidationError as exc:
            raise ToolError(f"The code does not fit: {exc.errors()[0]['msg']}.") from exc
        await ctx.ui.set_block(target, state.model_dump(mode="json"))
        show = getattr(ctx.ui, "show_block", None)
        if callable(show):
            show(target)
        ctx.log.debug(
            "builtin_tool.show_code",
            call_id=context.function_call.call_id,
            block_id=target,
            language=label,
            chars=len(text),
        )
        if ctx.pipeline_mode in QUIET_MODES:
            return None
        return _SHOWN

    return function_tool(
        show_code,
        description=(
            "Show code or text in a fixed-width font on the caller's screen, such as a setting to "
            "copy or a record, with a language label. Read-only: nothing shown is ever run. Say in a "
            f"sentence what it is. Code blocks: {inventory}."
        ),
    )
