"""Flow variables: extraction on node exit and `{{ name }}` rendering (CONTRACTS-V2 §4.5).

Extraction runs the session's `workflow_llm` through the existing
`StructuredLLM` (prompt for JSON, Pydantic parse, one repair retry) against a
schema generated from the flow's `VariableSpec`s. Every field is optional:
the model returns `null` for anything the conversation did not state, and a
`null` never overwrites a value an earlier node already captured.

Rendering is what lifts Dograh's limitation (its extracted variables never
reach later prompts): the next node's instructions, transition speech and
farewell are rendered from `FlowState.variables` *after* the extraction that
the transition triggered has settled, and every node prompt carries a short
"known so far" block.

V6-13 (ask #31): a variable whose value came from a third party (a tool result bound with
``var:``) or from the live extraction (the caller's words, restated) is fenced where it
reaches the **instructions** (``render_template(..., untrusted=...)`` and
:func:`known_variables_block`), never where it is spoken (transition speech, farewell,
announcements render without ``untrusted``). :func:`untrusted_variable_sources` says which
names those are, from the session's ``userdata``.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any, Final

from livekit.agents import llm as lk_llm
from lkap_contracts.flow import VariableSpec
from packs.base import StructuredLLM
from pydantic import BaseModel, Field, create_model

from lkap_agent.logging import get_logger
from lkap_agent.tools.untrusted import fence

__all__ = [
    "BOUND_SOURCE",
    "EXTRACTED_SOURCE",
    "EXTRACTED_VARIABLES_USERDATA_KEY",
    "VariableValue",
    "coerce_variable",
    "extract_variables",
    "known_variables_block",
    "referenced_variables",
    "render_template",
    "transcript_text",
    "untrusted_variable_sources",
    "variables_model",
]

logger = get_logger(__name__)

#: A value `FlowState.variables` can hold.
VariableValue = str | int | float | bool | None

#: `{{ name }}` placeholders; names follow `lkap_contracts.flow.VARIABLE_NAME_PATTERN`.
_PLACEHOLDER_RE: Final[re.Pattern[str]] = re.compile(r"\{\{\s*([a-z][a-z0-9_]{0,63})\s*\}\}")

#: Longest transcript excerpt sent to the extraction model (the tail is kept).
_MAX_TRANSCRIPT_CHARS: Final[int] = 12_000

#: ``SessionContext.userdata`` key of the set of variable names the live extraction wrote (V6-13).
EXTRACTED_VARIABLES_USERDATA_KEY: Final[str] = "lkap.extracted_variables"
#: The fence ``source`` of a variable a tool result binding wrote.
BOUND_SOURCE: Final[str] = "tool_binding"
#: The fence ``source`` of a variable the live extraction wrote.
EXTRACTED_SOURCE: Final[str] = "extraction"
#: The ``SessionContext.userdata`` key of the bound names (``tools.context.BOUND_VARIABLES_USERDATA_KEY``,
#: repeated here so this module does not import the tool context).
_BOUND_VARIABLES_KEY: Final[str] = "lkap.bound_variables"
#: Longest fenced variable value in the instructions.
_MAX_FENCED_VALUE_CHARS: Final[int] = 500

_EXTRACT_INSTRUCTIONS: Final[str] = (
    "Extract the following variables from the conversation between a voice agent "
    "(assistant) and a caller (user). Only use what the caller actually said or "
    "confirmed; never guess. Use null for anything not stated."
)


def untrusted_variable_sources(userdata: Any) -> dict[str, str]:
    """Each variable name whose value is not the admin's, with its fence source.

    Names a tool result binding wrote (``lkap.bound_variables``) are ``tool_binding``; names the
    live extraction wrote (:data:`EXTRACTED_VARIABLES_USERDATA_KEY`) are ``extraction``. A name
    in both reads ``tool_binding``.
    """
    if not isinstance(userdata, Mapping):
        return {}
    sources: dict[str, str] = {}
    for key, source in (
        (EXTRACTED_VARIABLES_USERDATA_KEY, EXTRACTED_SOURCE),
        (_BOUND_VARIABLES_KEY, BOUND_SOURCE),
    ):
        names = userdata.get(key)
        if isinstance(names, set | frozenset | list | tuple):
            for name in names:
                sources[str(name)] = source
    return sources


def _render_value(name: str, value: VariableValue, untrusted: Mapping[str, str] | None) -> str:
    text = _format_value(value)
    source = untrusted.get(name) if untrusted else None
    if source is None:
        return text
    return fence(text, source=source, max_chars=_MAX_FENCED_VALUE_CHARS)


def render_template(
    text: str,
    variables: Mapping[str, VariableValue],
    *,
    missing: str = "",
    untrusted: Mapping[str, str] | None = None,
) -> str:
    """Replace every `{{ name }}` in `text` with the variable's value.

    Args:
        text: Authored text (instructions, transition speech, farewell, greeting).
        variables: `FlowState.variables`.
        missing: Substituted for a name that is unset or `None`.
        untrusted: Name → fence source (:func:`untrusted_variable_sources`); those values are
            fenced. Pass it only for text that becomes instructions, never for spoken text.

    Returns:
        The rendered text.
    """

    def _sub(match: re.Match[str]) -> str:
        name = match.group(1)
        value = variables.get(name)
        return missing if value is None else _render_value(name, value, untrusted)

    return _PLACEHOLDER_RE.sub(_sub, text)


def referenced_variables(text: str) -> set[str]:
    """Every variable name `text` references as `{{ name }}`."""
    return set(_PLACEHOLDER_RE.findall(text))


def _format_value(value: VariableValue) -> str:
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value)


def known_variables_block(
    variables: Mapping[str, VariableValue],
    specs: Iterable[VariableSpec],
    *,
    untrusted: Mapping[str, str] | None = None,
) -> str | None:
    """A prompt block listing every captured variable, or `None` when nothing is known yet.

    Values named in ``untrusted`` (:func:`untrusted_variable_sources`) are fenced.
    """
    descriptions = {spec.name: spec.description for spec in specs}
    lines = []
    for name, value in variables.items():
        if value is None:
            continue
        note = f" ({descriptions[name]})" if descriptions.get(name) else ""
        lines.append(f"- {name}{note}: {_render_value(name, value, untrusted)}")
    if not lines:
        return None
    return (
        "Information already collected in this call (do not ask for it again unless the "
        "caller corrects it):\n" + "\n".join(lines)
    )


def variables_model(specs: Iterable[VariableSpec]) -> type[BaseModel]:
    """Build the extraction schema for `specs`: one optional field per variable.

    `enum` variables carry their options as a JSON-schema `enum` hint and are
    validated after the fact (:func:`extract_variables` drops values outside
    the options instead of failing the whole extraction).
    """
    fields: dict[str, Any] = {}
    for spec in specs:
        python_type: Any
        match spec.type:
            case "number":
                python_type = float | None
            case "boolean":
                python_type = bool | None
            case _:
                python_type = str | None
        extra: dict[str, Any] = {}
        if spec.type == "enum" and spec.options:
            extra["enum"] = [*spec.options, None]
        description = spec.description or spec.name
        if spec.type in ("date", "phone", "email"):
            description = f"{description} ({_FORMAT_HINTS[spec.type]})"
        fields[spec.name] = (
            python_type,
            Field(default=None, description=description, json_schema_extra=extra or None),
        )
    model: type[BaseModel] = create_model("FlowVariables", **fields)
    return model


_FORMAT_HINTS: Final[dict[str, str]] = {
    "date": "ISO 8601 date, YYYY-MM-DD",
    "phone": "phone number in E.164 when possible",
    "email": "email address",
}


def transcript_text(chat_ctx: lk_llm.ChatContext) -> str:
    """The user/assistant text of `chat_ctx`, one `role: text` line per message (tail-bounded)."""
    lines: list[str] = []
    for item in chat_ctx.items:
        if not isinstance(item, lk_llm.ChatMessage) or item.role not in ("user", "assistant"):
            continue
        text = item.text_content
        if text:
            lines.append(f"{item.role}: {text}")
    joined = "\n".join(lines)
    return joined[-_MAX_TRANSCRIPT_CHARS:]


async def extract_variables(
    structured: StructuredLLM,
    specs: list[VariableSpec],
    transcript: str,
    *,
    timeout_s: float,
) -> dict[str, VariableValue]:
    """Extract `specs` from `transcript` with the workflow LLM.

    Args:
        structured: The session's `StructuredLLM` (`SessionContext.workflow_llm`).
        specs: The variables to extract (a node's `extract` list, resolved).
        transcript: :func:`transcript_text` of the conversation so far.
        timeout_s: Budget for the whole call chain (first try + repair).

    Returns:
        The non-null values, type-checked against their specs. Never raises:
        a failed extraction logs a warning and returns `{}`.
    """
    if not specs or not transcript.strip():
        return {}
    schema = variables_model(specs)
    lines = [f"- {s.name} ({s.type}): {s.description or s.name}" for s in specs]
    for spec in specs:
        if spec.type == "enum" and spec.options:
            lines.append(f"  {spec.name} must be one of: {', '.join(spec.options)}")
    instructions = f"{_EXTRACT_INSTRUCTIONS}\nVariables:\n" + "\n".join(lines)
    try:
        result = await structured.extract(
            instructions=instructions, input_text=transcript, schema=schema, timeout_s=timeout_s
        )
    except Exception as exc:
        logger.warning("flow variable extraction failed", variables=[s.name for s in specs], error=str(exc))
        return {}
    raw = result.model_dump()
    values: dict[str, VariableValue] = {}
    for spec in specs:
        value = _coerce(spec, raw.get(spec.name))
        if value is not None:
            values[spec.name] = value
    logger.debug("flow variables extracted", names=sorted(values))
    return values


def coerce_variable(spec: VariableSpec, value: Any) -> VariableValue:
    """Normalise one extracted value; `None` for anything that does not fit the spec."""
    return _coerce(spec, value)


def _coerce(spec: VariableSpec, value: Any) -> VariableValue:
    """Normalise one extracted value; `None` for anything that does not fit the spec."""
    if value is None:
        return None
    match spec.type:
        case "number":
            if isinstance(value, bool) or not isinstance(value, int | float):
                return None
            return int(value) if float(value).is_integer() else float(value)
        case "boolean":
            return value if isinstance(value, bool) else None
        case "enum":
            text = str(value).strip()
            if spec.options and text not in spec.options:
                lowered = {option.lower(): option for option in spec.options}
                return lowered.get(text.lower())
            return text or None
        case _:
            text = str(value).strip()
            return text or None
