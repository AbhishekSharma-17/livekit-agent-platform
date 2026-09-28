"""Dataset lookups: the ``dataset`` tool kind (V6-16, D-V6-27).

One raw-schema function tool per :class:`~lkap_contracts.tools.DatasetToolDefinition`. The
model sees one string argument per key column the admin did not pin; the tool posts
``POST /internal/v1/datasets/{dataset_id}/lookup`` to the api with the service token and the
**session id**, so the api answers only from the session's own workspace (a dataset of another
workspace is a 404, the tenant rule). The api is the platform's own service, so the call does
not go through the outbound guard the third-party tools use (PLAN-V6 §0.1: datasets are
api-local).

The found rows are third-party data (someone's spreadsheet): they reach the model inside an
``<untrusted source="dataset:<tool>">`` fence (R-V5-15), at most ``max_result_chars``.
Nothing is ever written to a dataset from a call.

V6-07 (ask #35): ``requires_vars`` refuse before the lookup; ``pinned_arguments`` fix a key
column's value, rendered with ``{{ ctx.* }}``/``{{ var.* }}`` (``{{ ctx.caller_phone }}`` looks
the caller up by the number they call from) and hidden from the model; ``bindings`` copy the
found rows (a list; ``/0/<column>`` is the first row's) onto the panel before the model reads
them. A lookup always runs blocking: it is quick and the model needs its answer.

Privacy: key values (a phone number) are never logged; only the key column names and the
number of rows found.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any, Final

import httpx
from livekit.agents import RunContext, ToolError, function_tool
from livekit.agents.llm import RawFunctionTool
from lkap_contracts.tool_context import placeholder_issues, variable_label
from lkap_contracts.tools import DatasetToolDefinition, ToolExecutionMode

from lkap_agent.logging import get_logger
from lkap_agent.settings import get_settings
from lkap_agent.tools.bindings import HeldBindings
from lkap_agent.tools.context import ToolCallContext, check_requires, format_value, hide_pinned, pin_arguments
from lkap_agent.tools.declarative import note_http_status
from lkap_agent.tools.execution import (
    ToolPolicy,
    attach_policy,
    resolve_execution,
    run_with_policy,
    tool_flags,
)
from lkap_agent.tools.untrusted import fence

__all__ = [
    "LOOKUP_TIMEOUT_S",
    "build_dataset_tool",
    "dataset_parameters",
    "lookup_keys",
]

_log = get_logger(__name__)

#: How long one lookup may take (the api reads an indexed table; this is generous).
LOOKUP_TIMEOUT_S: Final = 5.0
_SERVICE_TOKEN_HEADER: Final = "X-Service-Token"
_NO_MOCK: Final = object()

#: Builds the client a lookup is posted with (tests pass one mounted on a fake api).
ClientFactory = Callable[[], httpx.AsyncClient]


def _default_client() -> httpx.AsyncClient:
    settings = get_settings()
    return httpx.AsyncClient(
        base_url=settings.api_base_url.rstrip("/"),
        timeout=LOOKUP_TIMEOUT_S,
        headers={_SERVICE_TOKEN_HEADER: settings.service_token},
    )


def dataset_parameters(definition: DatasetToolDefinition) -> dict[str, Any]:
    """The schema the model sees: one string per key column it supplies (pinned ones hidden).

    With one such column it is required; with several, any of them may be given (a row must
    match every one given).
    """
    properties = {
        column: {"type": "string", "description": f"The {variable_label(column)} to look up."}
        for column in definition.key_columns
    }
    schema: dict[str, Any] = {"type": "object", "properties": properties}
    schema = hide_pinned(schema, definition.pinned_arguments)
    supplied = list(schema["properties"])
    if len(supplied) == 1:
        schema["required"] = supplied
    return schema


def lookup_keys(
    definition: DatasetToolDefinition, raw_arguments: dict[str, object], tool_context: ToolCallContext | None
) -> dict[str, str]:
    """The key values to look up: the model's arguments with the admin's pinned values over them.

    Raises:
        ToolError: A required variable is missing, a pinned value names a session value or
            variable the session does not have, or no key value was given. Nothing is sent.
    """
    check_requires(definition.requires_vars, tool_context, tool=definition.name)
    arguments = pin_arguments(
        {name: raw_arguments.get(name) for name in definition.key_columns if name in raw_arguments},
        definition.pinned_arguments,
        tool_context,
        tool=definition.name,
    )
    keys: dict[str, str] = {}
    for column in definition.key_columns:
        value = format_value(arguments.get(column))
        if value is not None:
            keys[column] = value.strip()
    if not keys:
        wanted = [variable_label(column) for column in definition.key_columns]
        raise ToolError(
            f"Give the {' or the '.join(wanted)} to look up first; ask the caller for it. "
            "Nothing was looked up."
        )
    return keys


def _api_message(response: httpx.Response) -> str | None:
    try:
        body = response.json()
    except ValueError:
        return None
    error = body.get("error") if isinstance(body, dict) else None
    message = error.get("message") if isinstance(error, dict) else None
    return str(message)[:200] if message else None


async def _post_lookup(
    definition: DatasetToolDefinition, session_id: str, keys: dict[str, str], client_factory: ClientFactory
) -> dict[str, Any]:
    payload = {
        "session_id": session_id,
        "keys": keys,
        "match": definition.match,
        "return_columns": list(definition.return_columns),
        "max_rows": definition.max_rows,
    }
    try:
        async with client_factory() as client:
            response = await client.post(
                f"/internal/v1/datasets/{definition.dataset_id}/lookup", json=payload
            )
    except httpx.TimeoutException as exc:
        raise ToolError("the lookup table did not answer in time; try once more") from exc
    except httpx.HTTPError as exc:
        raise ToolError(f"could not reach the lookup table ({type(exc).__name__})") from exc
    if response.status_code == httpx.codes.NOT_FOUND:
        raise ToolError("this lookup table is not available; tell the caller you cannot check that right now")
    if response.status_code in (httpx.codes.CONFLICT, httpx.codes.UNPROCESSABLE_ENTITY):
        raise ToolError(_api_message(response) or "the lookup could not run")
    if response.status_code >= 400:
        raise ToolError(f"the lookup failed ({response.status_code})")
    try:
        body: Any = response.json()
    except ValueError as exc:
        raise ToolError("the lookup answered with something that is not JSON") from exc
    if not isinstance(body, dict) or not isinstance(body.get("rows"), list):
        raise ToolError("the lookup answered in an unexpected shape")
    return body


def _answer(definition: DatasetToolDefinition, rows: list[Any], *, truncated: bool) -> str:
    """The model's view of the found rows: fenced data, then a plain note when more matched."""
    text = fence(
        json.dumps(rows, ensure_ascii=False),
        source=f"dataset:{definition.name}",
        max_chars=definition.max_result_chars,
    )
    if truncated:
        text += (
            f" More than {definition.max_rows} records matched; "
            "ask the caller for more detail to narrow it down."
        )
    return text


def _request(
    definition: DatasetToolDefinition,
    tool_context: ToolCallContext | None,
    client_factory: ClientFactory,
    mock: object,
) -> Callable[[dict[str, str], RunContext[Any], HeldBindings], Any]:
    async def request(keys: dict[str, str], context: RunContext[Any], held: HeldBindings) -> str:
        call_id = context.function_call.call_id
        if mock is not _NO_MOCK:
            # V5-29: a test case's scratch session answers from the fixture, fenced the same way.
            rows: list[Any] = mock if isinstance(mock, list) else [mock]
            truncated = False
        else:
            session = getattr(tool_context, "session", None) if tool_context is not None else None
            session_id = getattr(session, "session_id", None)
            if not session_id:
                raise ToolError("this lookup is only available during a call")
            body = await _post_lookup(definition, str(session_id), keys, client_factory)
            rows = list(body["rows"])
            truncated = bool(body.get("truncated"))
        _log.debug(
            "dataset_tool.lookup",
            tool=definition.name,
            call_id=call_id,
            key_columns=sorted(keys),
            match=definition.match,
            rows=len(rows),
        )
        # Ask #116: a flow tool step reads "found nothing" like an HTTP 404 (the `empty` outcome).
        note_http_status(context, 200 if rows else 404)
        if not rows:
            return "No matching record was found. Check the details with the caller before trying again."
        # V6-07 (D-V6-23): the found record reaches the panel before the model reads it — held
        # until the tool-output guardrail has passed it (V6-21, S6-2).
        return held.hold(rows, _answer(definition, rows, truncated=truncated))

    return request


def build_dataset_tool(
    definition: DatasetToolDefinition,
    *,
    execution_default: ToolExecutionMode = "blocking",
    flow_node: bool = False,
    context: ToolCallContext | None = None,
    client_factory: ClientFactory | None = None,
    mock: object = _NO_MOCK,
) -> RawFunctionTool[..., Any]:
    """Build one dataset lookup as a raw-schema function tool (always blocking).

    Args:
        definition: The dataset tool's definition (from ``ResolvedAgentConfig.tools``).
        execution_default: The agent's ``tools.execution_default`` (a lookup ignores it).
        flow_node: The tool runs on a flow node.
        context: The session's tool context: its session id scopes the lookup to the session's
            workspace, and it carries pinned values, variables and the panel for bindings.
        client_factory: Builds the api client (defaults to one with the worker's service token).
        mock: V5-29, the fixture a test case's session returns instead of looking up.

    Returns:
        A ``RawFunctionTool`` carrying its ``ToolPolicy``.
    """
    factory = client_factory or _default_client
    tool_context = context
    resolved = resolve_execution(
        name=definition.name,
        kind="dataset",
        is_read=True,
        declared=None,
        agent_default=execution_default,
        flow_node=flow_node,
    )
    flags, on_duplicate, duplicate_scope = tool_flags(resolved)
    request = _request(definition, tool_context, factory, mock)
    bindings = list(definition.bindings)

    async def handler(raw_arguments: dict[str, object], context: RunContext[Any]) -> str:
        keys = lookup_keys(definition, raw_arguments, tool_context)
        held = HeldBindings()
        result: str = await run_with_policy(context, resolved, lambda: request(keys, context, held))
        # V6-21 (S6-2): the bindings apply after the tool-output guardrail, never on a withheld result.
        await held.apply(result, context, bindings, tool_context, tool=definition.name)
        return result

    issues = placeholder_issues(definition)
    if issues:
        # D-V6-22 defence in depth: a row written around the api never looks anything up.
        from lkap_agent.tools.declarative import misconfigured_handler  # noqa: PLC0415 - avoids a cycle

        body = misconfigured_handler(definition.name, f"{issues[0].field}: {issues[0].message}")
    else:
        body = handler

    tool = function_tool(
        body,
        raw_schema={
            "name": definition.name,
            "description": definition.description,
            "parameters": dataset_parameters(definition),
        },
        flags=flags,
        on_duplicate=on_duplicate,
        duplicate_scope=duplicate_scope,
    )

    def _rebind(default: ToolExecutionMode, flow: bool) -> RawFunctionTool[..., Any]:
        return build_dataset_tool(
            definition,
            execution_default=default,
            flow_node=flow,
            context=tool_context,
            client_factory=client_factory,
            mock=mock,
        )

    attach_policy(
        tool, ToolPolicy(resolved=resolved, rebind=_rebind, built_with=(execution_default, flow_node))
    )
    return tool
