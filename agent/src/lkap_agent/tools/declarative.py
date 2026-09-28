"""Declarative tools built at runtime from admin-authored JSON.

docs/CONTRACTS.md §9: HTTP/webhook tools and MCP servers, defined by
`lkap_contracts.tools.HttpToolDefinition` / `McpServerDefinition` and shipped
to the worker inside `ResolvedAgentConfig.tools`. The api has already
substituted `{{ secret.NAME }}` in `headers`/`url`/`body_template` from the
tool's `http-tool-secret` credential before the worker ever sees these
definitions (CONTRACTS §9 "Note on tool secrets") — this module only renders
the remaining `{{ arg }}` placeholders against the model's call arguments.

Raw HTTP tool construction is verified against livekit-agents==1.8.3
(`llm/tool_context.py`, `llm/utils.py::_prepare_function_arguments`): a
`RawFunctionTool`'s wrapped function is called with the whole JSON args dict
bound to a parameter literally named `raw_arguments`, plus the injected
`RunContext` — `async def handler(raw_arguments: dict[str, object], context:
RunContext) -> str`, exactly as CONTRACTS §9 specifies.

V6-07 (D-V6-22/23): with a session :class:`~lkap_agent.tools.context.ToolCallContext`, the
url and body also take ``{{ ctx.* }}`` and ``{{ var.* }}``, rendered in the same single
pass as the arguments (``tools.context.render_template``); ``requires_vars``, a missing
``ctx``/``var`` value and an unconfirmed ``confirm_readback`` refuse the call before any
request is sent; ``bindings`` copy a 2xx result onto the panel (``tools.bindings``) before
the model reads it. A definition without these features builds and sends exactly what it
did before.
"""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Final, Literal
from urllib.parse import quote

import httpx
from livekit.agents import RunContext, ToolError, function_tool
from livekit.agents.llm import RawFunctionTool
from lkap_contracts.tool_context import PlaceholderRef, placeholder_issues
from lkap_contracts.tool_providers import COMPOSIO_HOST
from lkap_contracts.tools import (
    HttpToolDefinition,
    McpOAuthAuth,
    McpServerDefinition,
    ProviderToolDefinition,
    ToolExecutionMode,
)

from lkap_agent.logging import get_logger
from lkap_agent.settings import get_settings
from lkap_agent.tools._http_safety import (
    MAX_RESPONSE_BYTES,
    HttpToolSecurityError,
    check_url_allowed,
    check_url_public,
    guarded_transport,
    read_bounded,
)
from lkap_agent.tools.bindings import apply_bindings, parse_result
from lkap_agent.tools.context import (
    ToolCallContext,
    check_readback,
    check_requires,
    missing_message,
    missing_refs,
    readback_parameters,
    render_template,
)
from lkap_agent.tools.execution import (
    ResolvedExecution,
    ToolPolicy,
    attach_policy,
    resolve_execution,
    run_with_policy,
    tool_flags,
)
from lkap_agent.tools.mcp_auth import McpOAuthBinding, ServerToken, bearer_transport_factory
from lkap_agent.tools.untrusted import fence

_log = get_logger(__name__)

#: At most this much of an HTTP tool's response body is read (V5-27).
MAX_HTTP_RESPONSE_BYTES: Final = MAX_RESPONSE_BYTES

#: Matches `{{ arg_name }}`-style placeholders only (not `{{ secret.NAME }}`,
#: whose dot the identifier pattern excludes — those are left untouched here
#: because the api has already resolved them before this module runs).
_ARG_PATTERN = re.compile(r"{{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*}}")

Handler = Callable[[dict[str, object], RunContext[Any]], Awaitable[str]]

#: V5-29: ``ResolvedAgentConfig.tool_mocks`` — tool name → the fixture a test case's
#: session returns instead of calling out. Empty for every real session.
ToolMocks = Mapping[str, Any]


def mock_result_text(fixture: Any) -> str:
    """A mock fixture as the tool's result text: a string as-is, anything else as JSON."""
    return fixture if isinstance(fixture, str) else json.dumps(fixture, ensure_ascii=False, default=str)


def _mocked_request(
    name: str,
    fixture: Any,
    *,
    source: str,
    max_chars: int,
    bindings: list[Any] | None = None,
    tool_context: ToolCallContext | None = None,
) -> Handler:
    """The body of a mocked tool (V5-29): the fixture, fenced like a real result, no network.

    V6-07: the definition's ``bindings`` apply to the fixture as to a real result.
    """

    async def handler(raw_arguments: dict[str, object], context: RunContext[Any]) -> str:
        _log.debug("declarative_tool.mocked", tool=name, call_id=context.function_call.call_id)
        text = mock_result_text(fixture)
        if bindings:
            result = fixture if not isinstance(fixture, str) else parse_result(fixture)
            await apply_bindings(
                result, bindings, tool_context, tool=name, call_id=context.function_call.call_id
            )
        return fence(text, source=source, max_chars=max_chars)

    return handler


def _render(template: str, arguments: dict[str, Any], *, escape: Callable[[str], str]) -> str:
    """Substitute `{{ arg }}` placeholders in `template`, applying `escape` to each value."""

    def _sub(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in arguments:
            return match.group(0)  # leave unknown placeholders (e.g. an unresolved secret) as-is
        return escape(str(arguments[name]))

    return _ARG_PATTERN.sub(_sub, template)


def _url_escape(value: str) -> str:
    return quote(value, safe="")


def _json_escape(value: str) -> str:
    return json.dumps(value)[1:-1]


def _render_url(
    template: str,
    arguments: dict[str, Any],
    tool_context: ToolCallContext | None = None,
    missing: list[PlaceholderRef] | None = None,
) -> str:
    """Substitute arguments, ``ctx`` and ``var`` values into a url, percent-encoding each."""
    return render_template(
        template, arguments, tool_context, escape=_url_escape, missing=missing if missing is not None else []
    )


def _render_json_string(
    template: str,
    arguments: dict[str, Any],
    tool_context: ToolCallContext | None = None,
    missing: list[PlaceholderRef] | None = None,
) -> str:
    """Substitute into a JSON-string `body_template`, JSON-escaping each value.

    `json.dumps(v)[1:-1]` yields the escaped *content* of a JSON string
    (quotes stripped), so a `"` or newline in an argument (or a session value) can't
    break out of the enclosing string in the template.
    """
    return render_template(
        template, arguments, tool_context, escape=_json_escape, missing=missing if missing is not None else []
    )


def _resolve_json_pointer(data: Any, pointer: str) -> Any:
    """A minimal RFC 6901 JSON Pointer resolver, e.g. `"/data/summary"`."""
    if not pointer or pointer == "/":
        return data
    current = data
    for raw_segment in pointer.lstrip("/").split("/"):
        segment = raw_segment.replace("~1", "/").replace("~0", "~")
        if isinstance(current, list):
            current = current[int(segment)]
        elif isinstance(current, dict):
            current = current[segment]
        else:
            raise KeyError(f"cannot resolve {pointer!r} past segment {segment!r}")
    return current


def _with_schema_defaults(parameters: dict[str, Any], arguments: dict[str, Any]) -> dict[str, Any]:
    """Fill each argument the model left out with its JSON Schema ``default`` (V5-25).

    Tool templates fix values such as a booking's ``event_type_id`` this way; the
    model may still pass its own. Only top-level ``properties`` with a non-null
    ``default`` are filled.
    """
    properties = parameters.get("properties")
    if not isinstance(properties, dict):
        return arguments
    filled = dict(arguments)
    for name, schema in properties.items():
        if name not in filled and isinstance(schema, dict) and schema.get("default") is not None:
            filled[name] = schema["default"]
    return filled


def _reject_unresolved(rendered: str, *, where: str) -> None:
    """Raise if `rendered` still contains a `{{ ... }}` placeholder.

    A raw tool call gets no per-parameter validation (`validated_arguments`
    returns `RawFunctionTool` arguments as-is), so a model call that omits a
    required argument — or a `{{ secret.NAME }}` the api failed to resolve —
    leaves a literal placeholder in the rendered text. Sending that to a
    third party is worse than failing the call, so this checks every
    rendered surface (url, headers, body), not just headers.
    """
    if "{{" in rendered:
        raise ToolError(f"unresolved template in {where}")


def misconfigured_handler(name: str, reason: str) -> Handler:
    """A tool whose definition uses a session value where it may not (defense in depth, D-V6-22).

    The contract refuses such a definition at save; one that reaches the worker anyway
    (a row written around the api) is built as a tool that refuses every call, so the
    session still starts with its other tools and nothing is ever sent.
    """
    _log.warning("declarative_tool.misconfigured", tool=name, reason=reason)

    async def handler(raw_arguments: dict[str, object], context: RunContext[Any]) -> str:
        raise ToolError("this tool is set up incorrectly and cannot run; tell the caller it is unavailable")

    return handler


def _prepare_http(
    definition: HttpToolDefinition, raw_arguments: dict[str, object], tool_context: ToolCallContext | None
) -> dict[str, object]:
    """The V6-07 checks before an HTTP tool runs (a no-op for a definition without them).

    Strips ``confirmed`` and refuses until it is true (``confirm_readback``), refuses a
    missing ``requires_vars`` variable, then refuses when the url or body names a session
    value or variable that has none — each before any request is sent.
    """
    if not (definition.confirm_readback or definition.requires_vars or tool_context is not None):
        return raw_arguments
    arguments = check_readback(dict(raw_arguments), definition.confirm_readback, tool=definition.name)
    check_requires(definition.requires_vars, tool_context, tool=definition.name)
    missing = missing_refs((definition.url, definition.body_template), tool_context)
    if missing:
        _log.debug(
            "declarative_tool.context_missing",
            tool=definition.name,
            missing=[f"{ref.namespace}.{ref.name}" for ref in missing],
        )
        raise ToolError(missing_message(missing, tool_context))
    return arguments


def _handler_for(
    definition: HttpToolDefinition,
    *,
    platform_allowed_hosts: list[str] | None,
    user_agent: str | None = None,
    resolved: ResolvedExecution | None = None,
    mocks: ToolMocks | None = None,
    tool_context: ToolCallContext | None = None,
) -> Handler:
    """The raw tool body; with ``resolved`` it runs through `run_with_policy` (BACKGROUND-TOOLS §4.2).

    V5-29: a tool named in ``mocks`` returns its fixture instead of sending the request.
    V6-07: the read-back, required-variable and missing-value checks run first, outside the
    execution policy, so a refused call is never announced as running in the background.
    """
    issues = placeholder_issues(definition)
    if issues:
        return misconfigured_handler(definition.name, f"{issues[0].field}: {issues[0].message}")
    request = (
        _mocked_request(
            definition.name,
            mocks[definition.name],
            source=f"http:{definition.name}",
            max_chars=definition.max_result_chars,
            bindings=list(definition.bindings),
            tool_context=tool_context,
        )
        if mocks and definition.name in mocks
        else _request_for(
            definition,
            platform_allowed_hosts=platform_allowed_hosts,
            user_agent=user_agent,
            tool_context=tool_context,
        )
    )
    if resolved is None:

        async def unpoliced(raw_arguments: dict[str, object], context: RunContext[Any]) -> str:
            return await request(_prepare_http(definition, raw_arguments, tool_context), context)

        return unpoliced

    policy = resolved

    async def handler(raw_arguments: dict[str, object], context: RunContext[Any]) -> str:
        arguments = _prepare_http(definition, raw_arguments, tool_context)
        result: str = await run_with_policy(context, policy, lambda: request(arguments, context))
        return result

    return handler


def _request_for(
    definition: HttpToolDefinition,
    *,
    platform_allowed_hosts: list[str] | None,
    user_agent: str | None = None,
    tool_context: ToolCallContext | None = None,
) -> Handler:
    async def handler(raw_arguments: dict[str, object], context: RunContext[Any]) -> str:
        arguments: dict[str, Any] = _with_schema_defaults(definition.parameters, dict(raw_arguments))
        missing: list[PlaceholderRef] = []
        url = _render_url(definition.url, arguments, tool_context, missing)
        if missing:
            raise ToolError(missing_message(missing, tool_context))
        _reject_unresolved(url, where="url")

        try:
            check_url_allowed(
                url,
                tool_allowed_hosts=definition.allowed_hosts,
                platform_allowed_hosts=platform_allowed_hosts,
            )
        except HttpToolSecurityError as exc:
            raise ToolError(str(exc)) from exc

        headers = {name: _render(value, arguments, escape=str) for name, value in definition.headers.items()}
        for name, value in headers.items():
            _reject_unresolved(value, where=f"header {name!r}")

        body: str | None = None
        if definition.method in ("POST", "PUT", "PATCH"):
            body = (
                _render_json_string(definition.body_template, arguments, tool_context, missing)
                if definition.body_template is not None
                else json.dumps(arguments)
            )
            if missing:
                raise ToolError(missing_message(missing, tool_context))
            _reject_unresolved(body, where="body")

        request_headers = dict(headers)
        if body is not None and not any(name.lower() == "content-type" for name in request_headers):
            request_headers["Content-Type"] = "application/json"
        if user_agent and not any(name.lower() == "user-agent" for name in request_headers):
            # asks #29: a tool's own `User-Agent` header wins over the platform default.
            request_headers["User-Agent"] = user_agent

        try:
            async with (
                httpx.AsyncClient(
                    follow_redirects=False, timeout=definition.timeout_s, transport=guarded_transport()
                ) as client,
                client.stream(definition.method, url, content=body, headers=request_headers) as response,
            ):
                raw, cut = await read_bounded(response, MAX_HTTP_RESPONSE_BYTES)
        except httpx.HTTPError as exc:
            # V5-27: the exception text can carry the url, and the api substituted secrets into it.
            raise ToolError(f"HTTP request failed ({type(exc).__name__})") from exc

        _log.debug(
            "declarative_tool.http_call",
            tool=definition.name,
            call_id=context.function_call.call_id,
            method=definition.method,
            host=httpx.URL(url).host,
            status=response.status_code,
            truncated=cut,
        )

        result_text = raw.decode(response.encoding or "utf-8", errors="replace")
        extracted: Any = None
        if definition.result_path:
            try:
                payload = json.loads(result_text)
                extracted = _resolve_json_pointer(payload, definition.result_path)
                result_text = json.dumps(extracted)
            except (json.JSONDecodeError, KeyError, IndexError, TypeError, ValueError) as exc:
                raise ToolError(f"could not extract result_path {definition.result_path!r}: {exc}") from exc

        if definition.bindings and 200 <= response.status_code < 300:
            # V6-07 (D-V6-23): the lookup reaches the panel before the model reads it.
            result = extracted if definition.result_path else parse_result(result_text)
            await apply_bindings(
                result,
                definition.bindings,
                tool_context,
                tool=definition.name,
                call_id=context.function_call.call_id,
            )

        # V5-27 (S5-6, R-V5-15): a third-party body is data, never instructions.
        return fence(result_text, source=f"http:{definition.name}", max_chars=definition.max_result_chars)

    return handler


def build_http_tool(
    definition: HttpToolDefinition,
    *,
    platform_allowed_hosts: list[str] | None = None,
    user_agent: str | None = None,
    execution_default: ToolExecutionMode = "blocking",
    flow_node: bool = False,
    mocks: ToolMocks | None = None,
    context: ToolCallContext | None = None,
) -> RawFunctionTool[..., Any]:
    """Build one HTTP tool under its execution policy (docs/v4/BACKGROUND-TOOLS.md §4.2).

    A GET tool is a read tool: without a declared ``execution.mode`` it follows
    ``execution_default``. Any other method blocks unless it declares a mode (its
    duplicates then need confirmation and it is not cancellable by default). The
    policy's flags and duplicate settings are declared on the tool, and the body
    runs through `run_with_policy`. The tool carries a `ToolPolicy` whose
    ``rebind`` rebuilds it for another agent default or flow-ness
    (`execution.bind_agent_policy`).

    V6-07: ``context`` is the session's :class:`~lkap_agent.tools.context.ToolCallContext`
    (``{{ ctx.* }}``, ``{{ var.* }}`` and the panel for ``bindings``); a ``confirm_readback``
    tool's schema gains the ``confirmed`` parameter.
    """
    parameters = definition.parameters
    if not isinstance(parameters, dict) or parameters.get("type") != "object":
        parameters = {"type": "object", "properties": {}, **(parameters or {})}
    parameters = readback_parameters(parameters, definition.confirm_readback)
    resolved = resolve_execution(
        name=definition.name,
        kind="http",
        is_read=definition.method == "GET",
        declared=definition.execution,
        agent_default=execution_default,
        flow_node=flow_node,
    )
    flags, on_duplicate, duplicate_scope = tool_flags(resolved)
    tool = function_tool(
        _handler_for(
            definition,
            platform_allowed_hosts=platform_allowed_hosts,
            user_agent=user_agent,
            resolved=resolved,
            mocks=mocks,
            tool_context=context,
        ),
        raw_schema={
            "name": definition.name,
            "description": definition.description,
            "parameters": parameters,
        },
        flags=flags,
        on_duplicate=on_duplicate,
        duplicate_scope=duplicate_scope,
    )

    def _rebind(default: ToolExecutionMode, flow: bool) -> RawFunctionTool[..., Any]:
        return build_http_tool(
            definition,
            platform_allowed_hosts=platform_allowed_hosts,
            user_agent=user_agent,
            execution_default=default,
            flow_node=flow,
            mocks=mocks,
            context=context,
        )

    policy = ToolPolicy(resolved=resolved, rebind=_rebind, built_with=(execution_default, flow_node))
    attach_policy(tool, policy)
    return tool


def build_http_tools(
    defs: list[HttpToolDefinition | ProviderToolDefinition],
    *,
    platform_allowed_hosts: list[str] | None = None,
    user_agent: str | None = None,
    execution_default: ToolExecutionMode = "blocking",
    flow_node: bool = False,
    mocks: ToolMocks | None = None,
    context: ToolCallContext | None = None,
) -> list[RawFunctionTool[..., Any]]:
    """Build one `@function_tool(raw_schema=...)` per `HttpToolDefinition`.

    Args:
        defs: HTTP tool definitions from `ResolvedAgentConfig.tools`
            (already secret-substituted by the api).
        platform_allowed_hosts: `LKAP_HTTP_TOOL_ALLOWED_HOSTS`. Since F-14 a
            request must pass the intersection of this list with each
            definition's own `allowed_hosts`, plus a private-range deny-list,
            enforced in `_http_safety.check_url_allowed` (docs/CONTRACTS.md §3/§9).
        user_agent: `LKAP_HTTP_TOOL_USER_AGENT`, sent as `User-Agent` unless the
            definition's own headers set one (asks #29).
        execution_default: The agent's `tools.execution_default` (GET tools only).
            The worker's builder is called with the definitions alone, so
            `PlatformAgent` re-binds the tools to the agent's own default and
            flow-ness (`execution.bind_agent_policy`).
        flow_node: The tools run on flow nodes (the 1.8.3 gate, R-V4-39).
        mocks: V5-29, ``ResolvedAgentConfig.tool_mocks``: a tool (HTTP or connected-app
            action) named here returns its fixture, fenced like a real result, and never
            calls out. Only a test case's scratch session has any.
        context: V6-07, the session's tool context (placeholders, bindings); ``main.py``
            passes it only when a definition uses those features.

    Returns:
        One `RawFunctionTool` per definition, ready to pass to `Agent(tools=...)`.
        A `ProviderToolDefinition` (a connected app's action, V5-47) is built by
        `lkap_agent.tools.provider` instead: it calls Composio's execute route on the
        pinned Composio host, so the HTTP allowlists do not apply to it.
    """
    tools: list[RawFunctionTool[..., Any]] = []
    for definition in defs:
        if isinstance(definition, ProviderToolDefinition):
            if mocks and definition.name in mocks:
                tools.append(
                    build_mocked_provider_tool(
                        definition,
                        mocks[definition.name],
                        execution_default=execution_default,
                        flow_node=flow_node,
                        context=context,
                    )
                )
                continue
            from lkap_agent.tools.provider import build_provider_tool  # noqa: PLC0415 - avoids a cycle

            tools.append(
                build_provider_tool(
                    definition, execution_default=execution_default, flow_node=flow_node, context=context
                )
            )
            continue
        tools.append(
            build_http_tool(
                definition,
                platform_allowed_hosts=platform_allowed_hosts,
                user_agent=user_agent,
                execution_default=execution_default,
                flow_node=flow_node,
                mocks=mocks,
                context=context,
            )
        )
    return tools


def build_mocked_provider_tool(
    definition: ProviderToolDefinition,
    fixture: Any,
    *,
    execution_default: ToolExecutionMode = "blocking",
    flow_node: bool = False,
    context: ToolCallContext | None = None,
) -> RawFunctionTool[..., Any]:
    """A connected-app action that returns ``fixture`` instead of calling the vendor (V5-29).

    Same name, schema and execution policy as `tools.provider.build_provider_tool` builds,
    so the model sees the tool it would see in a real session (V6-07: the same checks and
    pins before the call, the same bindings on the fixture).
    """
    from lkap_agent.tools.provider import (  # noqa: PLC0415 - avoids a cycle
        prepare_provider_arguments,
        provider_parameters,
    )

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
    request = _mocked_request(
        definition.name,
        fixture,
        source=f"app:{definition.toolkit or definition.provider}",
        max_chars=definition.max_result_chars,
        bindings=list(definition.bindings),
        tool_context=context,
    )
    tool_context = context

    async def handler(raw_arguments: dict[str, object], context: RunContext[Any]) -> str:
        arguments = prepare_provider_arguments(definition, raw_arguments, tool_context)
        result: str = await run_with_policy(context, resolved, lambda: request(arguments, context))
        return result

    tool = function_tool(
        handler,
        raw_schema={"name": definition.name, "description": definition.description, "parameters": parameters},
        flags=flags,
        on_duplicate=on_duplicate,
        duplicate_scope=duplicate_scope,
    )

    def _rebind(default: ToolExecutionMode, flow: bool) -> RawFunctionTool[..., Any]:
        return build_mocked_provider_tool(
            definition, fixture, execution_default=default, flow_node=flow, context=tool_context
        )

    attach_policy(
        tool, ToolPolicy(resolved=resolved, rebind=_rebind, built_with=(execution_default, flow_node))
    )
    return tool


def check_origin_host(url: str) -> None:
    """Pin a provider-provisioned MCP server to ``https`` on the provider's host (D-V5-C10).

    An origin-tagged definition (the Composio app server or tool finder, V5-47) carries the
    workspace's Composio key in a header, so it may only ever connect to Composio itself,
    whatever `check_url_public` would allow.

    Raises:
        HttpToolSecurityError: The scheme is not ``https``, the host is not the provider's,
            or the url carries credentials or another port.
    """
    parsed = httpx.URL(url)
    if parsed.scheme != "https" or (parsed.host or "").lower() != COMPOSIO_HOST:
        raise HttpToolSecurityError(f"an app server must be https on {COMPOSIO_HOST}, not {parsed.host!r}")
    if parsed.port not in (None, 443) or parsed.userinfo:
        raise HttpToolSecurityError(f"an app server must be https on {COMPOSIO_HOST} without a port or login")


def check_mcp_host(url: str, ceiling: frozenset[str] | None) -> None:
    """The worker half of the MCP host policy (V5-09, D-V5-4): `https`, then the ceiling.

    `check_url_public` has already refused private and loopback hosts, so the api's
    "plain http only to a dev loopback host" exception never reaches the worker: every
    MCP server it connects to is `https`.

    Args:
        url: The MCP endpoint.
        ceiling: `Settings.mcp_host_ceiling`: `None` = any public host, a set = only those.

    Raises:
        HttpToolSecurityError: Not `https`, or a host outside the ceiling.
    """
    parsed = httpx.URL(url)
    if parsed.scheme != "https":
        raise HttpToolSecurityError(f"an MCP server must use https, not {parsed.scheme!r}")
    host = (parsed.host or "").lower().rstrip(".")
    if ceiling is not None and host not in ceiling:
        raise HttpToolSecurityError(f"host {host!r} is not on LKAP_MCP_ALLOWED_HOSTS")


#: `host_ceiling` default: read `LKAP_MCP_ALLOWED_HOSTS` from the worker's settings.
FROM_SETTINGS: Final = "settings"
HostCeiling = frozenset[str] | None | Literal["settings"]

#: Called with `(definition, reason)` for each MCP server `build_mcp_toolsets` refuses.
McpSkipCallback = Callable[[McpServerDefinition, str], None]


def build_mcp_toolsets(
    defs: list[McpServerDefinition],
    *,
    on_skipped: McpSkipCallback | None = None,
    transport_factory: Callable[[], httpx.AsyncBaseTransport] | None = None,
    flow_node: bool = False,
    host_ceiling: HostCeiling = FROM_SETTINGS,
    oauth: McpOAuthBinding | None = None,
    context: ToolCallContext | None = None,
) -> list[Any]:
    """Build one `MCPToolset` over a guarded server per `McpServerDefinition` whose URL is public.

    livekit-agents 1.8.2 deprecates `Agent`'s `mcp_servers` argument (`voice/agent.py`
    warns "Use `MCPToolset` instead") and builds its toolsets with no per-tool
    options (`agent_activity.py::_setup_toolsets`), so per-tool execution needs
    LKAP to build them: `MCPToolset(id=f"mcp_{name}", mcp_server=GuardedMCPServerHTTP(...),
    tool_options={tool: MCPToolOptions(flags, on_duplicate, duplicate_scope,
    report_progress)})` (`llm/mcp.py`). The server is exactly what
    :func:`build_guarded_mcp_servers` builds, so the URL check, the guarded
    transport and the no-redirect client all still apply. The worker passes the
    toolsets in `Agent(tools=[...])`; the SDK sets them up itself
    (`AgentActivity._setup_toolsets` awaits `toolset.setup()`: connect, then list
    the tools) and closes them with the activity.

    MCP tools never follow the agent default (D-V4-32). A tool with a
    non-blocking mode announces only through the server's progress
    notifications (`report_progress`); `max_duration_s` does not apply (the
    server's `timeout_s` bounds the call). Each toolset carries a
    ``{tool name: ToolPolicy}`` map for the activity feed.

    Args:
        defs: MCP server definitions from `ResolvedAgentConfig.tools`.
        on_skipped: Called with `(definition, reason)` for each refused server.
        transport_factory: Builds each client's transport; tests pass a fake.
        flow_node: The toolsets belong to a flow node (the 1.8.3 gate, R-V4-39).
        host_ceiling: `LKAP_MCP_ALLOWED_HOSTS` as `Settings.mcp_host_ceiling`; the default
            reads the worker's settings.
        oauth: The session's access to its signed-in servers (V5-16); without it an
            `oauth` server is skipped.
        context: V6-07, the session's tool context for servers with a ``tool_context``.

    Returns:
        `MCPToolset` instances, ready to pass to `Agent(tools=...)`.
    """
    servers = _guarded_mcp_servers(
        defs,
        on_skipped=on_skipped,
        transport_factory=transport_factory,
        host_ceiling=host_ceiling,
        oauth=oauth,
        context=context,
    )
    if not servers:
        return []
    from livekit.agents.llm.mcp import MCPToolOptions, MCPToolset  # noqa: PLC0415 - optional extra

    toolsets: list[Any] = []
    for definition, server in servers:
        options: dict[str, MCPToolOptions] = {}
        policies: dict[str, ToolPolicy] = {}
        for tool_name, declared in definition.tool_options.items():
            resolved = resolve_execution(
                name=tool_name,
                kind="mcp",
                is_read=False,
                declared=declared,
                agent_default="blocking",
                flow_node=flow_node,
            )
            flags, on_duplicate, duplicate_scope = tool_flags(resolved)
            options[tool_name] = MCPToolOptions(
                flags=flags,
                on_duplicate=on_duplicate,
                duplicate_scope=duplicate_scope,
                report_progress=resolved.report_progress,
            )
            policies[tool_name] = ToolPolicy(resolved=resolved)
        toolset = MCPToolset(id=f"mcp_{definition.name}", mcp_server=server, tool_options=options)
        toolsets.append(attach_policy(toolset, policies))
    return toolsets


_mcp_servers_alias_warned = False


def build_mcp_servers(
    defs: list[McpServerDefinition],
    *,
    on_skipped: McpSkipCallback | None = None,
    transport_factory: Callable[[], httpx.AsyncBaseTransport] | None = None,
    flow_node: bool = False,
    host_ceiling: HostCeiling = FROM_SETTINGS,
    oauth: McpOAuthBinding | None = None,
    context: ToolCallContext | None = None,
) -> list[Any]:
    """Deprecated alias of :func:`build_mcp_toolsets` (kept for one release; warns once).

    Returns `MCPToolset`s, not servers: every caller hands the result to
    `PlatformAgent`, which puts it in `Agent(tools=...)`.
    """
    global _mcp_servers_alias_warned  # noqa: PLW0603 - one warning per process by design
    if not _mcp_servers_alias_warned:
        _mcp_servers_alias_warned = True
        _log.warning(
            "declarative_tool.build_mcp_servers_deprecated",
            detail="build_mcp_servers is an alias of build_mcp_toolsets and returns MCPToolsets",
        )
    return build_mcp_toolsets(
        defs,
        on_skipped=on_skipped,
        transport_factory=transport_factory,
        flow_node=flow_node,
        host_ceiling=host_ceiling,
        oauth=oauth,
        context=context,
    )


def build_guarded_mcp_servers(
    defs: list[McpServerDefinition],
    *,
    on_skipped: McpSkipCallback | None = None,
    transport_factory: Callable[[], httpx.AsyncBaseTransport] | None = None,
    host_ceiling: HostCeiling = FROM_SETTINGS,
    oauth: McpOAuthBinding | None = None,
) -> list[Any]:
    """The guarded servers alone (what :func:`build_mcp_toolsets` wraps); see `_guarded_mcp_servers`."""
    return [
        server
        for _definition, server in _guarded_mcp_servers(
            defs,
            on_skipped=on_skipped,
            transport_factory=transport_factory,
            host_ceiling=host_ceiling,
            oauth=oauth,
        )
    ]


#: Why an `oauth` server without an api-issued access is skipped (no url, no provider detail).
MCP_OAUTH_UNAVAILABLE: Final = (
    "this server's sign-in is not available to the session (an admin may need to sign in again)"
)


def _guarded_mcp_servers(
    defs: list[McpServerDefinition],
    *,
    on_skipped: McpSkipCallback | None = None,
    transport_factory: Callable[[], httpx.AsyncBaseTransport] | None = None,
    host_ceiling: HostCeiling = FROM_SETTINGS,
    oauth: McpOAuthBinding | None = None,
    context: ToolCallContext | None = None,
) -> list[tuple[McpServerDefinition, Any]]:
    """Build one guarded `MCPServerHTTP` per `McpServerDefinition` whose URL is public.

    Every URL goes through `_http_safety.check_url_public` (http/https, a host,
    not in a private, loopback, link-local or metadata range) before a server
    is built: the api checks it at save time, but the worker is the process
    that connects. A refused server is skipped with a warning log and an
    `on_skipped` call, never an exception, so the session still starts with
    its other tools. Each built server is a
    :class:`~lkap_agent.tools.mcp_client.GuardedMCPServerHTTP`, whose client
    does not follow redirects and connects through `guarded_transport()`.

    V5-09 (D-V5-4): the URL must also be `https` and, when `LKAP_MCP_ALLOWED_HOSTS`
    is set, on it (:func:`check_mcp_host`; a provider-provisioned server too — the
    operator lists the provider's host). `LKAP_HTTP_TOOL_ALLOWED_HOSTS` does not
    apply unless `LKAP_MCP_ALLOWED_HOSTS=@http` says so: its empty-means-nothing
    semantics would refuse every MCP server whenever it is unset.

    Auth (`definition.auth`): `none` and `header` connect with the definition's
    headers. The api substitutes secrets into both `auth.headers` and the deprecated
    top-level `headers` mirror, which are equal once validated, so the top-level
    field is what the SDK receives. `oauth` (V5-16) connects through
    :class:`~lkap_agent.tools.mcp_auth.ApiIssuedBearer` over the same guarded transport,
    with the access the api issued for the server (`oauth`, matched by name and url);
    a server the api issued none for is skipped with :data:`MCP_OAUTH_UNAVAILABLE`.

    `livekit.agents.mcp` needs the optional `mcp` package (the `mcp` extra of
    `livekit-agents` in `agent/pyproject.toml`). When the import fails this
    logs an error and returns `[]`, so the session degrades (no MCP tools)
    instead of failing.

    Args:
        defs: MCP server definitions from `ResolvedAgentConfig.tools`
            (already secret-substituted by the api).
        on_skipped: Called with `(definition, reason)` for each refused server
            (the worker records it as a session event). `reason` names the
            host, never the full URL.
        transport_factory: Builds each client's transport; defaults to
            `guarded_transport`. Tests pass a fake.
        host_ceiling: `Settings.mcp_host_ceiling`, or :data:`FROM_SETTINGS` to read it.
        oauth: The session's access to its signed-in servers (V5-16).
        context: V6-07: a server whose ``tool_context`` names tools is built as a
            :class:`~lkap_agent.tools.mcp_context.ContextMCPServerHTTP` over this context;
            one whose definition places a session value where it may not is refused.

    Returns:
        ``(definition, server)`` pairs; :func:`build_mcp_toolsets` wraps each server
        in an `MCPToolset`.
    """
    if not defs:
        return []

    ceiling = get_settings().mcp_host_ceiling if host_ceiling == FROM_SETTINGS else host_ceiling
    kept: list[McpServerDefinition] = []
    tokens: dict[int, ServerToken] = {}
    for definition in defs:
        try:
            check_url_public(definition.url)
            if definition.origin is not None:
                check_origin_host(definition.url)
            check_mcp_host(definition.url, ceiling)
            issues = placeholder_issues(definition)
            if issues:
                raise HttpToolSecurityError(f"{issues[0].field}: {issues[0].message}")
            if isinstance(definition.auth, McpOAuthAuth):
                access = oauth.access_for(definition) if oauth is not None else None
                if oauth is None or access is None:
                    raise HttpToolSecurityError(MCP_OAUTH_UNAVAILABLE)
                tokens[id(definition)] = ServerToken(access, oauth)
        except HttpToolSecurityError as exc:
            reason = str(exc)
            _log.warning("declarative_tool.mcp_server_refused", mcp_server=definition.name, reason=reason)
            if on_skipped is not None:
                on_skipped(definition, reason)
            continue
        kept.append(definition)
    if not kept:
        return []

    try:
        # Imports `livekit.agents.llm.mcp`, which raises ImportError without the extra.
        from lkap_agent.tools.mcp_client import GuardedMCPServerHTTP  # noqa: PLC0415
    except ImportError:
        _log.error(
            "declarative_tool.mcp_extra_missing",
            detail="livekit-agents[mcp] extra is not installed; skipping MCP servers",
            count=len(kept),
        )
        return []

    base = transport_factory or guarded_transport
    built: list[tuple[McpServerDefinition, Any]] = []
    for definition in kept:
        token = tokens.get(id(definition))
        factory = (
            bearer_transport_factory(base, token, record_event=oauth.record_event if oauth else None)
            if token is not None
            else base
        )
        options: dict[str, Any] = {
            "url": definition.url,
            "transport_type": "streamable_http",
            "allowed_tools": definition.allowed_tools,
            "headers": definition.headers,
            "timeout": definition.timeout_s,
            "sse_read_timeout": definition.sse_read_timeout_s,
            "transport_factory": factory,
            "server_name": definition.name,
        }
        if definition.tool_context:
            # V6-07: pinned arguments, required variables, read-back and bindings per tool.
            from lkap_agent.tools.mcp_context import ContextMCPServerHTTP  # noqa: PLC0415 - optional extra

            server: Any = ContextMCPServerHTTP(
                tool_context=definition.tool_context, context=context, **options
            )
        else:
            server = GuardedMCPServerHTTP(**options)
        built.append((definition, server))
    return built
