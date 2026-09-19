"""`packs.base.StructuredLLM` over a plain `llm.LLM` (docs/ARCHITECTURE.md D9).

Packs run their deterministic workflows off one JSON-extraction call. The
design deliberately does **not** rely on provider-side structured output
(ARCHITECTURE §15.4): `inference.LLM` accepts `extra_kwargs`, but the schema
support behind it varies per upstream model. Instead this module prompts for
JSON, parses it with Pydantic and retries once with the validation error fed
back — which works identically on every configured LLM and is trivially
fakeable offline.
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any, Final

from livekit.agents import llm
from pydantic import BaseModel, ValidationError

from lkap_agent.logging import get_logger

__all__ = ["PromptJsonStructuredLLM", "StructuredExtractionError"]

logger = get_logger(__name__)

_FENCE_RE: Final[re.Pattern[str]] = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)

_SYSTEM_PROMPT: Final[str] = (
    "You extract structured data. Reply with a single JSON object and nothing "
    "else: no prose, no markdown fence, no explanation. Every required property "
    "of the schema must be present. Use null for anything the input does not state."
)


class StructuredExtractionError(RuntimeError):
    """The LLM did not produce JSON matching the requested schema."""


def _strip_fence(text: str) -> str:
    """Return the JSON body of `text`, unwrapping a markdown fence if present."""
    match = _FENCE_RE.search(text)
    if match:
        return match.group(1).strip()
    stripped = text.strip()
    # Some models prepend a sentence; fall back to the outermost object.
    start, end = stripped.find("{"), stripped.rfind("}")
    if start != -1 and end > start:
        return stripped[start : end + 1]
    return stripped


class PromptJsonStructuredLLM:
    """Extracts a Pydantic model from free text using an ordinary chat LLM."""

    def __init__(self, model: llm.LLM[Any], *, max_repairs: int = 1) -> None:
        """Wrap a chat LLM.

        Args:
            model: The LLM the agent is configured with (or the dedicated
                `workflow_llm` slot).
            max_repairs: How many times to re-prompt with the validation error.
        """
        self._llm = model
        self._max_repairs = max_repairs

    async def extract(
        self,
        *,
        instructions: str,
        input_text: str,
        schema: type[BaseModel],
        timeout_s: float = 45,
    ) -> BaseModel:
        """Return a validated `schema` instance extracted from `input_text`.

        Args:
            instructions: Task-specific guidance prepended to the JSON contract.
            input_text: The source text (usually a transcript excerpt).
            schema: The Pydantic model to produce.
            timeout_s: Wall-clock budget for the whole attempt chain.

        Returns:
            A validated instance of `schema`.

        Raises:
            StructuredExtractionError: On timeout, an LLM error, or JSON that
                still fails validation after the repair attempt.
        """
        try:
            return await asyncio.wait_for(
                self._extract_with_repair(instructions, input_text, schema),
                timeout=timeout_s,
            )
        except TimeoutError as exc:
            raise StructuredExtractionError(
                f"structured extraction of {schema.__name__} timed out after {timeout_s}s"
            ) from exc

    async def _extract_with_repair(
        self, instructions: str, input_text: str, schema: type[BaseModel]
    ) -> BaseModel:
        schema_json = json.dumps(schema.model_json_schema(), separators=(",", ":"))
        prompt = (
            f"{instructions.strip()}\n\n"
            f"JSON Schema of your reply:\n{schema_json}\n\n"
            f"Input:\n{input_text.strip()}"
        )
        last_error: Exception | None = None
        raw = ""

        for attempt in range(self._max_repairs + 1):
            if attempt:
                prompt = (
                    f"{prompt}\n\nYour previous reply was rejected:\n{raw}\n\n"
                    f"Validation error:\n{last_error}\n\nReply with corrected JSON only."
                )
            raw = await self._complete(prompt)
            try:
                return schema.model_validate_json(_strip_fence(raw))
            except ValidationError as exc:
                last_error = exc
                logger.debug(
                    "structured extraction rejected, retrying",
                    schema=schema.__name__,
                    attempt=attempt,
                    error_count=exc.error_count(),
                )

        raise StructuredExtractionError(
            f"{schema.__name__} extraction failed after {self._max_repairs + 1} attempts: {last_error}"
        )

    async def _complete(self, prompt: str) -> str:
        """Run one non-streaming completion and return the concatenated text."""
        chat_ctx = llm.ChatContext.empty()
        chat_ctx.add_message(role="system", content=_SYSTEM_PROMPT)
        chat_ctx.add_message(role="user", content=prompt)

        chunks: list[str] = []
        try:
            stream = self._llm.chat(chat_ctx=chat_ctx)
            async with stream:
                async for chunk in stream:
                    delta: Any = chunk.delta
                    if delta is not None and delta.content:
                        chunks.append(delta.content)
        except Exception as exc:
            raise StructuredExtractionError(f"workflow LLM call failed: {exc}") from exc
        return "".join(chunks)
