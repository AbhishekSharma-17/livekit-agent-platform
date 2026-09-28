"""Connected-app actions run through Composio (docs/v5/COMPOSIO.md §5, D-V5-C9; V5-47).

One raw-schema function tool per :class:`~lkap_contracts.tools.ProviderToolDefinition`.
The api has already substituted the Composio key into ``headers`` (the
``{{ secret }}`` rule of HTTP tools, D-V5-C10); the worker never provisions
anything and never sees a credential id.

A call is ``POST https://backend.composio.dev/api/v3.1/tools/execute/{tool_slug}``
with ``{user_id: subject, arguments, version}`` (plus ``connected_account_id``
when the definition carries one), through the guarded transport, without
following redirects, bounded by ``timeout_s``. The answer
``{data, error, successful, …}`` is reduced like an HTTP tool's: ``result_path``
(a top-level key such as ``data`` or a JSON pointer), then ``max_result_chars``, inside an
``<untrusted source="app:<toolkit>">`` fence (V5-27, R-V5-15: an app's data is never instructions).

Errors are spoken-safe (D-V5-C9): ``successful=false`` becomes a
``ToolError`` with the first sentence of Composio's message, an auth-shaped
failure (an expired, missing or revoked connection) becomes
:data:`REAUTH_MESSAGE`, which the session observer turns into a
``tool_needs_reauth`` event, and any URL in any error text is removed before
the model sees it — a sign-in link is never read aloud.

V6-07 (D-V6-22/23): ``pinned_arguments`` are applied over the model's arguments (string
values rendered with ``{{ ctx.* }}``/``{{ var.* }}``) and hidden from its schema;
``requires_vars`` and ``confirm_readback`` refuse before the execute call; ``bindings``
copy a successful answer (after ``result_path``) onto the panel before the model reads it.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any, Final
from urllib.parse import quote

import httpx
from livekit.agents import RunContext, ToolError, function_tool
from livekit.agents.llm import RawFunctionTool
from lkap_contracts.tool_context import placeholder_issues
from lkap_contracts.tool_providers import COMPOSIO_HOST
from lkap_contracts.tools import ProviderToolDefinition, ToolExecutionMode

from lkap_agent.logging import get_logger
from lkap_agent.tools._http_safety import guarded_transport
from lkap_agent.tools.bindings import HeldBindings
from lkap_agent.tools.context import (
    ToolCallContext,
    check_readback,
    check_requires,
    hide_pinned,
    pin_arguments,
    readback_parameters,
)
from lkap_agent.tools.execution import (
    ResolvedExecution,
    ToolPolicy,
    attach_policy,
    resolve_execution,
    run_with_policy,
    tool_flags,
)
from lkap_agent.tools.untrusted import fence

_log = get_logger(__name__)

#: The REST base every execute call uses (D-V5-C10: the one Composio host, https).
EXECUTE_BASE: Final = f"https://{COMPOSIO_HOST}/api/v3.1/tools/execute"

#: What the model is told when the app's connection needs a person (D-V5-C9). The session
#: observer matches it to record ``tool_needs_reauth``; never includes a link.
REAUTH_MESSAGE: Final = "This app needs to be reconnected by an admin"

#: Longest error text passed to the model.
MAX_ERROR_CHARS: Final = 200

_URL_RE = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)
_SENTENCE_END = re.compile(r"(?<=[.!?])\s")

#: Words in a vendor error ``code``/``slug``/``message`` that mean the connection needs a person.
_AUTH_MARKERS: Final[tuple[str, ...]] = (
    "connected account",
    "connected_account",
    "connectedaccount",
    "no connection",
    "not connected",
    "reconnect",
    "re-authenticate",
    "reauth",
    "expired",
    "revoked",
    "unauthorized",
    "invalid_grant",
    "token has been",
    "auth_config",
    "no active connection",
)

TransportFactory = Callable[[], httpx.AsyncBaseTransport]


def spoken_safe(text: object, *, limit: int = MAX_ERROR_CHARS) -> str:
    """Vendor text made safe to hand the model: URLs removed, one sentence, capped."""
    cleaned = " ".join(_URL_RE.sub("", str(text or "")).split())
    sentence = _SENTENCE_END.split(cleaned, maxsplit=1)[0] if cleaned else ""
    return sentence[:limit].strip()


def _is_auth_shaped(*parts: object) -> bool:
    text = " ".join(str(part or "") for part in parts).lower()
    return any(marker in text for marker in _AUTH_MARKERS)


def _error_fields(body: Any) -> tuple[str, str, str]:
    """``(message, code, slug)`` from an execute answer's ``error`` (a string or an object)."""
    if not isinstance(body, dict):
        return "", "", ""
    error = body.get("error")
    if isinstance(error, dict):
        return (
            str(error.get("message") or error.get("detail") or ""),
            str(error.get("code") or ""),
            str(error.get("slug") or ""),
        )
    if error:
        return str(error), "", ""
    return "", "", ""


def _extract(body: Any, result_path: str | None) -> Any:
    """The part of an execute answer the model sees: a top-level key or a JSON pointer."""
    if not result_path:
        return body
    if not result_path.startswith("/"):
        if isinstance(body, dict) and result_path in body:
            return body[result_path]
        raise KeyError(result_path)
    current = body
    for raw in result_path.lstrip("/").split("/"):
        segment = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(current, list):
            current = current[int(segment)]
        elif isinstance(current, dict):
            current = current[segment]
        else:
            raise KeyError(segment)
    return current


def _request_body(definition: ProviderToolDefinition, arguments: dict[str, Any]) -> dict[str, Any]:
    body: dict[str, Any] = {
        "user_id": definition.subject,
        "arguments": arguments,
        "version": definition.schema_version or "latest",
    }
    if definition.connected_account_id:
        body["connected_account_id"] = definition.connected_account_id
    return body


Handler = Callable[[dict[str, object], RunContext[Any]], Any]
#: The action's request; it holds its bound value for the handler (V6-21, S6-2).
Body = Callable[[dict[str, object], RunContext[Any], HeldBindings], Any]


def provider_parameters(definition: ProviderToolDefinition) -> dict[str, Any]:
    """The schema the model sees: pinned arguments hidden, ``confirmed`` added for a read-back."""
    parameters = definition.parameters
    if not isinstance(parameters, dict) or parameters.get("type") != "object":
        parameters = {"type": "object", "properties": {}, **(parameters or {})}
    parameters = hide_pinned(parameters, definition.pinned_arguments)
    return readback_parameters(parameters, definition.confirm_readback)


def prepare_provider_arguments(
    definition: ProviderToolDefinition, raw_arguments: dict[str, object], tool_context: ToolCallContext | None
) -> dict[str, object]:
    """The V6-07 checks and pins before an action runs (a no-op for a definition without them).

    Raises:
        ToolError: Unconfirmed read-back, a missing required variable, or a pinned value that
            names a session value or variable the session does not have. Nothing is sent.
    """
    if not (definition.confirm_readback or definition.requires_vars or definition.pinned_arguments):
        return raw_arguments
    arguments = check_readback(
        dict(raw_arguments), definition.confirm_readback, tool=definition.name, context=tool_context
    )
    check_requires(definition.requires_vars, tool_context, tool=definition.name)
    return pin_arguments(arguments, definition.pinned_arguments, tool_context, tool=definition.name)


def _request_for(
    definition: ProviderToolDefinition,
    transport_factory: TransportFactory,
    tool_context: ToolCallContext | None = None,
) -> Body:
    url = f"{EXECUTE_BASE}/{quote(definition.tool_slug, safe='')}"

    async def request(raw_arguments: dict[str, object], context: RunContext[Any], held: HeldBindings) -> str:
        arguments: dict[str, Any] = dict(raw_arguments)
        headers = {**definition.headers, "Content-Type": "application/json", "Accept": "application/json"}
        if any("{{" in value for value in definition.headers.values()):
            raise ToolError("this app's key is not configured")
        try:
            async with httpx.AsyncClient(
                follow_redirects=False, timeout=definition.timeout_s, transport=transport_factory()
            ) as client:
                response = await client.post(
                    url, content=json.dumps(_request_body(definition, arguments)), headers=headers
                )
        except httpx.TimeoutException as exc:
            raise ToolError(f"{definition.toolkit or 'the app'} did not answer in time") from exc
        except httpx.HTTPError as exc:
            raise ToolError(f"could not reach the app service ({type(exc).__name__})") from exc
        try:
            body: Any = response.json()
        except ValueError:
            body = None
        message, code, slug = _error_fields(body)
        _log.debug(
            "provider_tool.execute",
            tool=definition.name,
            tool_slug=definition.tool_slug,
            call_id=context.function_call.call_id,
            status=response.status_code,
            successful=body.get("successful") if isinstance(body, dict) else None,
        )
        failed = response.status_code >= 400 or (isinstance(body, dict) and body.get("successful") is False)
        if response.status_code in (401, 403) or (failed and _is_auth_shaped(message, code, slug)):
            _log.info("provider_tool.needs_reauth", tool=definition.name, status=response.status_code)
            raise ToolError(REAUTH_MESSAGE)
        if response.status_code >= 400:
            raise ToolError(
                spoken_safe(message) or f"the app answered with an error ({response.status_code})"
            )
        if not isinstance(body, dict):
            raise ToolError("the app answered with something that is not JSON")
        if body.get("successful") is False:
            raise ToolError(spoken_safe(message) or "the app could not complete that action")
        try:
            extracted = _extract(body, definition.result_path)
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise ToolError(f"the app's answer has no {definition.result_path!r}") from exc
        text = extracted if isinstance(extracted, str) else json.dumps(extracted)
        # V5-27 (S5-6, R-V5-15): an email or ticket body is data, never instructions.
        answer = fence(
            text, source=f"app:{definition.toolkit or 'unknown'}", max_chars=definition.max_result_chars
        )
        # V6-07 (D-V6-23): the action's answer reaches the panel before the model reads it —
        # held until the tool-output guardrail has passed it (V6-21, S6-2).
        return held.hold(extracted, answer)

    return request


def _handler_for(
    definition: ProviderToolDefinition,
    resolved: ResolvedExecution,
    transport_factory: TransportFactory,
    tool_context: ToolCallContext | None = None,
) -> Handler:
    issues = placeholder_issues(definition)
    if issues:
        from lkap_agent.tools.declarative import misconfigured_handler  # noqa: PLC0415 - avoids a cycle

        return misconfigured_handler(definition.name, f"{issues[0].field}: {issues[0].message}")
    request = _request_for(definition, transport_factory, tool_context)
    bindings = list(definition.bindings)

    async def handler(raw_arguments: dict[str, object], context: RunContext[Any]) -> str:
        arguments = prepare_provider_arguments(definition, raw_arguments, tool_context)
        held = HeldBindings()
        result: str = await run_with_policy(context, resolved, lambda: request(arguments, context, held))
        # V6-21 (S6-2): the bindings apply after the tool-output guardrail, never on a withheld result.
        await held.apply(result, context, bindings, tool_context, tool=definition.name)
        return result

    return handler


def build_provider_tool(
    definition: ProviderToolDefinition,
    *,
    execution_default: ToolExecutionMode = "blocking",
    flow_node: bool = False,
    transport_factory: TransportFactory | None = None,
    context: ToolCallContext | None = None,
) -> RawFunctionTool[..., Any]:
    """Build one connected-app action as a raw-schema function tool under its execution policy.

    Materialised actions declare their mode (reads ``auto``, writes ``blocking``), so the
    agent default rarely applies; a read action without a declared mode follows it like a
    GET HTTP tool. Destructive actions always block (the contract refuses otherwise).

    Args:
        definition: The resolved definition (key already substituted into ``headers``).
        execution_default: The agent's ``tools.execution_default``.
        flow_node: The tool runs on a flow node (the 1.8.3 gate, R-V4-39).
        transport_factory: Builds the client transport; defaults to the guarded transport.
        context: V6-07, the session's tool context (pinned values, variables, bindings).

    Returns:
        A ``RawFunctionTool`` carrying its ``ToolPolicy`` (rebindable to an agent default).
    """
    factory = transport_factory or guarded_transport
    parameters = provider_parameters(definition)
    resolved = resolve_execution(
        name=definition.name,
        kind="provider",
        is_read=definition.risk == "read",
        declared=definition.execution,
        agent_default=execution_default,
        flow_node=flow_node,
    )
    flags, on_duplicate, duplicate_scope = tool_flags(resolved)
    tool = function_tool(
        _handler_for(definition, resolved, factory, context),
        raw_schema={"name": definition.name, "description": definition.description, "parameters": parameters},
        flags=flags,
        on_duplicate=on_duplicate,
        duplicate_scope=duplicate_scope,
    )

    def _rebind(default: ToolExecutionMode, flow: bool) -> RawFunctionTool[..., Any]:
        return build_provider_tool(
            definition,
            execution_default=default,
            flow_node=flow,
            transport_factory=transport_factory,
            context=context,
        )

    attach_policy(
        tool, ToolPolicy(resolved=resolved, rebind=_rebind, built_with=(execution_default, flow_node))
    )
    return tool


def build_provider_tools(
    defs: list[ProviderToolDefinition],
    *,
    execution_default: ToolExecutionMode = "blocking",
    flow_node: bool = False,
    transport_factory: TransportFactory | None = None,
) -> list[RawFunctionTool[..., Any]]:
    """One function tool per connected-app action (see :func:`build_provider_tool`)."""
    return [
        build_provider_tool(
            definition,
            execution_default=execution_default,
            flow_node=flow_node,
            transport_factory=transport_factory,
        )
        for definition in defs
    ]


__all__ = [
    "EXECUTE_BASE",
    "REAUTH_MESSAGE",
    "build_provider_tool",
    "build_provider_tools",
    "prepare_provider_arguments",
    "provider_parameters",
    "spoken_safe",
]
