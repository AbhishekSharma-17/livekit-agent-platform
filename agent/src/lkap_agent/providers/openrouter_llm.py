"""OpenRouter LLM tool schemas with nested objects written out (V6-30, F-2).

livekit-agents 1.8.3 sends a tool whose parameter is a list of Pydantic models (``set_details``'
``items``, ``draw_on_canvas``' ``shapes``, ``notebook_write``' ``fields``…) with the item schema
in ``$defs`` and ``items: {"$ref": "#/$defs/DetailIn"}`` (``llm.utils.build_strict_openai_schema``).
OpenRouter translates an OpenAI tool schema into each upstream's own format; for Google's Gemini
models that translation does not follow ``$ref``, and the model is shown an array of **strings**:
in the V6 demos run Gemini 3.5 Flash quoted the schema it had received as
``fields: {items: {nullable: true, type: STRING}, nullable: true, type: ARRAY}`` and sent
``"Policyholder: Maya Singh"`` where an object was expected, burning a tool step per retry.

:class:`OpenRouterLLM` is the stock ``openai.LLM`` (as ``LLM.with_openrouter`` builds it) whose
streams write every ``$ref`` out in place (:func:`inline_schema_refs`) before the request goes out.
The result is the same JSON Schema, valid for OpenAI's strict mode too; a self-referencing model
keeps its ``$ref`` (it cannot be written out). Only the ``openai`` tool format is rewritten.

Since V6-31 each stream also leaves out the optional request parameters the model's catalog record
does not list (:func:`lkap_agent.providers.reasoning.filter_request_kwargs`), so OpenRouter's
``require_parameters`` routing never 404s on a parameter such as ``temperature`` or
``parallel_tool_calls`` that a reasoning model's endpoints refuse.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from typing import Any, Final

from livekit.agents import llm
from livekit.plugins.openai import llm as openai_llm

from lkap_agent.logging import get_logger
from lkap_agent.providers.reasoning import filter_request_kwargs

__all__ = ["InlineRefToolContext", "OpenRouterLLM", "inline_schema_refs", "with_inline_tool_schemas"]

logger = get_logger(__name__)

_DEFS_PREFIX: Final[str] = "#/$defs/"


def inline_schema_refs(schema: dict[str, Any]) -> dict[str, Any]:
    """``schema`` with each ``{"$ref": "#/$defs/<name>"}`` replaced by that definition.

    Keywords beside a ``$ref`` (a ``description``) are kept over the definition's. A reference
    back into a definition being written out (a recursive model) is left as it is, and then
    ``$defs`` is kept; otherwise ``$defs`` is dropped.

    Args:
        schema: A JSON Schema object with its definitions under ``$defs``.

    Returns:
        A new schema; ``schema`` is not modified.
    """
    defs = schema.get("$defs")
    if not isinstance(defs, dict) or not defs:
        return schema
    kept_ref = False

    def resolve(node: Any, active: tuple[str, ...]) -> Any:
        nonlocal kept_ref
        if isinstance(node, list):
            return [resolve(item, active) for item in node]
        if not isinstance(node, dict):
            return node
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith(_DEFS_PREFIX):
            name = ref[len(_DEFS_PREFIX) :]
            target = defs.get(name)
            if isinstance(target, dict) and name not in active:
                resolved = resolve(target, (*active, name))
                siblings = {key: resolve(value, active) for key, value in node.items() if key != "$ref"}
                return {**resolved, **siblings}
            kept_ref = True
            return {key: value for key, value in node.items()}
        return {key: resolve(value, active) for key, value in node.items() if key != "$defs"}

    inlined = resolve(schema, ())
    if kept_ref:
        inlined["$defs"] = {name: resolve(body, (name,)) for name, body in defs.items()}
    return dict(inlined)


def _inline_tool(tool_schema: dict[str, Any]) -> dict[str, Any]:
    function = tool_schema.get("function")
    if not isinstance(function, dict):
        return tool_schema
    parameters = function.get("parameters")
    if not isinstance(parameters, dict):
        return tool_schema
    return {**tool_schema, "function": {**function, "parameters": inline_schema_refs(parameters)}}


class InlineRefToolContext(llm.ToolContext):
    """A ``ToolContext`` whose ``openai``-format tool schemas carry no ``$ref`` (see
    :func:`inline_schema_refs`)."""

    def parse_function_tools(self, format: str, **kwargs: Any) -> list[dict[str, Any]]:
        """The provider-format tool schemas; the ``openai`` ones with their references written out."""
        schemas: list[dict[str, Any]] = super().parse_function_tools(format, **kwargs)  # type: ignore[call-overload]
        if format != "openai":
            return schemas
        return [_inline_tool(schema) for schema in schemas]


class OpenRouterLLM(openai_llm.LLM):
    """``openai.LLM`` for OpenRouter: every chat stream sends its tool schemas without ``$ref``
    and only the optional parameters the model accepts."""

    #: The chat request parameters the model accepts (V6-31); ``None`` = unknown, send everything.
    lkap_request_parameters: frozenset[str] | None = None

    def chat(self, **kwargs: Any) -> openai_llm.LLMStream:
        """Open a chat stream (``openai.LLM.chat``) with an :class:`InlineRefToolContext`.

        The stream's request task starts on the next loop iteration, so swapping the tool context
        and filtering the request's keyword arguments here, synchronously, is always before the
        request is built.
        """
        stream = super().chat(**kwargs)
        tools: Sequence[llm.Tool | llm.Toolset] = getattr(stream, "_tools", None) or []
        if hasattr(stream, "_tool_ctx"):
            stream._tool_ctx = InlineRefToolContext(list(tools))
        extra = getattr(stream, "_extra_kwargs", None)
        accepted = self.lkap_request_parameters
        if accepted is not None and isinstance(extra, dict):
            stream._extra_kwargs = filter_request_kwargs(extra, accepted, model=self.model)
        return stream


def with_inline_tool_schemas(model: Any, *, request_parameters: Collection[str] | None = None) -> Any:
    """Make an ``openai.LLM`` built for OpenRouter an :class:`OpenRouterLLM` (in place); others unchanged.

    Args:
        model: The object ``LLM.with_openrouter`` returned.
        request_parameters: The parameters the model accepts (V6-31); ``None`` = unknown.
    """
    if type(model) is openai_llm.LLM:
        model.__class__ = OpenRouterLLM
    elif not isinstance(model, OpenRouterLLM):
        logger.debug("openrouter llm left as built", llm_class=type(model).__name__)
    if isinstance(model, OpenRouterLLM):
        accepted = frozenset(request_parameters) if request_parameters is not None else None
        model.lkap_request_parameters = accepted
    return model
