"""`switch_language` built-in tool (V5-31): change the language the agent speaks, mid-call.

Registered only when the agent lists more than one language
(`VoiceConfig.languages`). The running agent (`session.current_agent`, so a flow
node after a handoff) does the switch (`PlatformAgent.switch_language`): the
transcriber's language when it can switch and is not detecting, the voice when
the language has its own (`voices_by_language`), and the answer tells the model
to reply in the new language. Instant and never backgrounded
(`NEVER_BACKGROUND_TOOLS`); its reply is never silenced, so the model's next
sentence is already in the new language.
"""

from __future__ import annotations

from typing import Any

from livekit.agents import FunctionTool, RunContext, ToolError, function_tool
from lkap_contracts.agent_config import effective_languages
from lkap_contracts.providers import language_name
from packs.base import PackSessionContext

from lkap_agent.logging import get_logger

__all__ = ["build_switch_language_tool", "switch_language_description"]

logger = get_logger(__name__)


def _choices(languages: list[str]) -> str:
    return ", ".join(f"{language_name(code)} ({code})" for code in languages)


def switch_language_description(languages: list[str]) -> str:
    """The tool description the model reads, naming the agent's languages."""
    return (
        "Switch the conversation to another of your languages: the caller speaks it or asks for it. "
        f"Your languages: {_choices(languages)}. Pass the language code. After it succeeds, reply in "
        "that language."
    )


def build_switch_language_tool(ctx: PackSessionContext) -> FunctionTool[..., Any]:
    """Build `switch_language` bound to `ctx` (its languages are read from `ctx.config`)."""
    languages = effective_languages(ctx.config.voice)

    @function_tool(name="switch_language", description=switch_language_description(languages))
    async def switch_language(context: RunContext[Any], language: str) -> str:
        """Switch the conversation language.

        Args:
            language: The language code to switch to, e.g. "hi" or "en".
        """
        session = getattr(context, "session", None) or ctx.session
        try:
            agent = session.current_agent
        except RuntimeError as exc:
            raise ToolError("Switching language is not available right now.") from exc
        switch = getattr(agent, "switch_language", None)
        if not callable(switch):
            raise ToolError("Switching language is not available right now.")
        result = await switch(language, source="tool")
        if result is None:
            raise ToolError(
                f"'{language}' is not one of your languages ({_choices(languages)}). Keep speaking the "
                "current language and tell the caller which languages you can use."
            )
        message: str = result.message()
        logger.debug(
            "builtin_tool.switch_language",
            language=result.to_language,
            changed=result.changed,
            stt_switched=result.stt_switched,
            voice_switched=result.voice_switched,
        )
        return message

    return switch_language
