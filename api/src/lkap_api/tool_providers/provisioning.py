"""The app server and the tool finder, provisioned on agent save (docs/v5/COMPOSIO.md D-V5-C6, C11).

``AgentConfig.tools.apps.mode`` decides what the api provisions at Composio
for one agent; the worker never provisions anything (D-V5-C11):

* ``server`` — an **app server**: a Tool Router session with the meta tools
  off and the agent's picked actions preloaded, so its MCP endpoint lists
  exactly those actions;
* ``router`` — a **tool finder**: a Tool Router session whose meta tools
  (search, get schemas, multi-execute; connection tools only when the flag
  allows) let the agent find and run actions during the conversation;
* ``actions`` / ``off`` — nothing (picked actions attach as ``provider``
  tools through ``tools.tool_ids``).

Both use ``POST /api/v3.1/tool_router/session`` rather than
``/api/v3.1/mcp/servers``: Composio's API reference marks the whole MCP
server API deprecated in favour of a session's MCP endpoint (recorded in
``docs/v5/_briefs/v5-47-live.md``).

The result is one agent-owned ``tools`` row of kind ``mcp`` per agent,
carrying ``origin = {provider: composio, kind, remote_id: <session id>,
config_hash}``, the session's MCP url (``https`` on the Composio host only),
``x-api-key: {{ secret.api_key }}`` bound to the workspace's Composio key,
``allowed_tools`` and per-tool execution (D-V5-C7). Its id is kept in
``tools.tool_ids``. A save that changes nothing Composio sees reuses the
session (``config_hash``); a change creates a new session and deletes the
old one; ``off``, another mode or deleting the agent deletes it (best
effort, audited).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Annotated, Any, Final

from fastapi import Depends
from lkap_contracts.agent_config import AgentConfig
from lkap_contracts.tool_providers import (
    ROUTER_CONNECTION_TOOLS,
    ROUTER_EXECUTE_TOOLS,
    ROUTER_SEARCH_TOOLS,
    AppsMode,
    action_risk,
    agent_subject,
    router_allowed_tools,
    workspace_subject,
)
from lkap_contracts.tools import McpOriginKind, McpServerDefinition, McpServerOrigin, ToolExecution
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lkap_api.auth import audit
from lkap_api.auth.deps import WorkspaceContext
from lkap_api.db.models import Agent, Tool, utcnow
from lkap_api.deps import VaultDep
from lkap_api.errors import UnprocessableEntityError
from lkap_api.logging import get_logger
from lkap_api.tool_providers import service
from lkap_api.tool_providers.adapter import AdapterFactory, ToolProviderError, ToolProviderNotFoundError
from lkap_api.tool_providers.bindings import is_composio_url
from lkap_api.tool_providers.router import get_adapter_factory
from lkap_api.vault import Vault

log = get_logger(__name__)

#: The agent-owned MCP rows' names (also the model-facing toolset ids).
SERVER_TOOL_NAME: Final = "composio_app_server"
ROUTER_TOOL_NAME: Final = "composio_tool_finder"

#: MCP connect timeout for a provisioned server (the session lists its tools on connect).
MCP_TIMEOUT_S: Final = 10.0

#: What the model is told while a search runs past the inline threshold (D-V5-C7).
SEARCH_ANNOUNCE: Final = "One moment"


def _definition(tool: Tool) -> dict[str, Any]:
    return tool.definition if isinstance(tool.definition, dict) else {}


def origin_of(tool: Tool) -> McpServerOrigin | None:
    """The Composio origin of an ``mcp`` row, or ``None``."""
    if tool.kind != "mcp":
        return None
    raw = _definition(tool).get("origin")
    if not isinstance(raw, dict) or raw.get("provider") != "composio":
        return None
    try:
        return McpServerOrigin.model_validate(raw)
    except ValidationError:
        return None


async def origin_rows(db: AsyncSession, workspace_id: str, agent_id: str) -> list[Tool]:
    """The agent's provisioned Composio MCP rows."""
    rows = (
        await db.execute(
            select(Tool).where(
                Tool.workspace_id == workspace_id, Tool.agent_id == agent_id, Tool.kind == "mcp"
            )
        )
    ).scalars()
    return [row for row in rows.all() if origin_of(row) is not None]


@dataclass(frozen=True)
class SessionPlan:
    """What one agent's app server or tool finder asks Composio for."""

    kind: McpOriginKind
    subject: str
    options: dict[str, Any]
    allowed_tools: list[str]
    tool_options: dict[str, ToolExecution]
    toolkits: list[str]

    @property
    def config_hash(self) -> str:
        """A stable fingerprint of everything Composio sees (subject and options)."""
        body = json.dumps({"subject": self.subject, "options": self.options}, sort_keys=True)
        return hashlib.sha256(body.encode("utf-8")).hexdigest()[:24]


def _usable(conn: service.AppConnection, agent_id: str) -> bool:
    return conn.status == "active" and (
        conn.subject.startswith("ws:") or conn.subject == agent_subject(agent_id)
    )


def plan_session(
    apps: AppsMode, connections: list[service.AppConnection], *, workspace_id: str, agent_id: str
) -> SessionPlan:
    """The Tool Router session an agent's ``tools.apps`` asks for (D-V5-C6, C7).

    The subject is the workspace's (``ws:<id>``) unless every usable connection was
    made for this agent alone. Only connections of that subject count; their
    connected accounts are pinned per app.

    Raises:
        UnprocessableEntityError: The mode is not ``server``/``router``, no connected app
            is usable, or an app server would expose no action.
    """
    kind: McpOriginKind
    if apps.mode == "server":
        kind = "server"
    elif apps.mode == "router":
        kind = "router"
    else:
        raise UnprocessableEntityError(f"tools.apps.mode '{apps.mode}' provisions nothing")
    allowed = {slug.lower() for slug in apps.allowed_toolkits}
    usable = [c for c in connections if _usable(c, agent_id) and (not allowed or c.toolkit in allowed)]
    if not usable:
        raise UnprocessableEntityError(
            "no connected app is available to this agent"
            + (" among tools.apps.allowed_toolkits" if allowed else "")
            + "; connect one under Tools, Apps",
            details={"path": "tools.apps.allowed_toolkits"},
        )
    workspace_wide = [c for c in usable if c.subject == workspace_subject(workspace_id)]
    chosen = workspace_wide or usable
    subject = chosen[0].subject
    chosen = [c for c in chosen if c.subject == subject]
    toolkits = sorted({c.toolkit for c in chosen})
    denied = {slug.upper() for slug in apps.denied_actions}
    accounts = {
        toolkit: sorted(
            {c.connected_account_id for c in chosen if c.toolkit == toolkit and c.connected_account_id}
        )
        for toolkit in toolkits
    }
    options: dict[str, Any] = {
        "toolkits": {"enable": toolkits},
        "manage_connections": {"enable": False},
        "workbench": {"enable": False},
    }
    pinned = {toolkit: ids for toolkit, ids in accounts.items() if ids}
    if pinned:
        options["connected_accounts"] = pinned
    tool_options: dict[str, ToolExecution] = {}
    if kind == "server":
        picked: dict[str, list[str]] = {}
        for conn in chosen:
            for slug in conn.picked_actions:
                if slug.upper() not in denied:
                    picked.setdefault(conn.toolkit, []).append(slug.upper())
        exposed = sorted({slug for slugs in picked.values() for slug in slugs})
        if not exposed:
            raise UnprocessableEntityError(
                "the app server has no action to offer: pick actions of a connected app first",
                details={"path": "tools.apps.mode"},
            )
        options["tools"] = {
            toolkit: {"enable": sorted(set(slugs))} for toolkit, slugs in sorted(picked.items())
        }
        options["preload"] = {"tools": exposed}
        options["search"] = {"enable": False}
        options["execute"] = {"enable_multi_execute": False}
        for slug in exposed:
            if action_risk(slug) == "read":
                tool_options[slug] = ToolExecution(
                    mode="auto", announce="Let me look that up", max_duration_s=20
                )
            else:
                tool_options[slug] = ToolExecution(mode="blocking", cancellable=False, max_duration_s=20)
        allowed_tools = exposed
    else:
        flags = apps.router
        options["search"] = {"enable": flags.search}
        options["execute"] = {"enable_multi_execute": flags.execute}
        options["manage_connections"] = {"enable": flags.manage_connections}
        if denied:
            by_toolkit: dict[str, list[str]] = {}
            for slug in sorted(denied):
                toolkit = next((t for t in toolkits if slug.startswith(f"{t.upper()}_")), None)
                if toolkit is not None:
                    by_toolkit.setdefault(toolkit, []).append(slug)
            if by_toolkit:
                options["tools"] = {toolkit: {"disable": slugs} for toolkit, slugs in by_toolkit.items()}
        allowed_tools = router_allowed_tools(flags)
        for name in allowed_tools:
            if name in ROUTER_SEARCH_TOOLS:
                tool_options[name] = ToolExecution(mode="auto", announce=SEARCH_ANNOUNCE, max_duration_s=20)
            elif name in ROUTER_EXECUTE_TOOLS or name in ROUTER_CONNECTION_TOOLS:
                tool_options[name] = ToolExecution(mode="blocking", cancellable=False)
    return SessionPlan(
        kind=kind,
        subject=subject,
        options=options,
        allowed_tools=allowed_tools,
        tool_options=tool_options,
        toolkits=toolkits,
    )


@dataclass
class AppsProvisioner:
    """Provisions and tears down an agent's app server / tool finder (the agents router's hook)."""

    vault: Vault
    factory: AdapterFactory

    async def on_save(
        self, db: AsyncSession, ctx: WorkspaceContext, agent: Agent, config: AgentConfig
    ) -> AgentConfig:
        """Make Composio match ``config.tools.apps`` and return the config with ``tool_ids`` set.

        Idempotent: a save that changes nothing Composio sees makes no vendor call.
        Called after validation and before the config is stored.
        """
        return await sync_agent_apps(db, self.vault, self.factory, ctx, agent, config)

    async def on_delete(self, db: AsyncSession, ctx: WorkspaceContext, agent: Agent) -> None:
        """Delete the agent's sessions at Composio (best effort) and their rows."""
        for row in await origin_rows(db, ctx.workspace_id, agent.id):
            await _teardown(db, self.vault, self.factory, ctx, row)


def get_apps_provisioner(
    vault: VaultDep, factory: Annotated[AdapterFactory, Depends(get_adapter_factory)]
) -> AppsProvisioner:
    """The hook ``routers/agents.py`` calls on create, update and delete."""
    return AppsProvisioner(vault=vault, factory=factory)


AppsProvisionerDep = Annotated[AppsProvisioner, Depends(get_apps_provisioner)]


def _audit(db: AsyncSession, ctx: WorkspaceContext, action: str, agent_id: str, **payload: Any) -> None:
    audit.record(
        db,
        workspace_id=ctx.workspace_id,
        actor_type=ctx.actor.actor_type,
        actor_id=ctx.actor.id,
        action=action,
        target_type="agent",
        target_id=agent_id,
        payload=payload,
    )


async def _delete_session(adapter: Any, session_id: str) -> bool:
    try:
        await adapter.delete_router_session(session_id)
    except ToolProviderNotFoundError:
        return True
    except ToolProviderError as exc:
        log.info("apps_session_delete_failed", reason=exc.reason)
        return False
    return True


async def _teardown(
    db: AsyncSession, vault: Vault, factory: AdapterFactory, ctx: WorkspaceContext, row: Tool
) -> None:
    origin = origin_of(row)
    deleted = False
    if origin is not None:
        try:
            adapter, _ = await service.workspace_adapter(
                db, vault, factory, ctx.workspace_id, require_enabled=False
            )
        except service.AppsNotEnabledError:
            adapter = None
        if adapter is not None:
            deleted = await _delete_session(adapter, origin.remote_id)
        _audit(
            db,
            ctx,
            f"apps.{origin.kind}.delete",
            row.agent_id or "",
            tool_id=row.id,
            deleted_at_vendor=deleted,
        )
    await db.delete(row)
    await db.flush()


async def sync_agent_apps(
    db: AsyncSession,
    vault: Vault,
    factory: AdapterFactory,
    ctx: WorkspaceContext,
    agent: Agent,
    config: AgentConfig,
) -> AgentConfig:
    """Provision, update or remove the agent's app server / tool finder (D-V5-C11).

    Args:
        db: Open session (the caller commits).
        vault: Decrypts the key and the connection rows.
        factory: Builds the Composio adapter.
        ctx: The caller's workspace context.
        agent: The agent row (flushed, so it has an id).
        config: The validated configuration about to be stored.

    Returns:
        ``config`` with ``tools.tool_ids`` holding the provisioned row (and no stale one).

    Raises:
        UnprocessableEntityError: Nothing to provision for the requested mode, or Composio
            answered with an MCP address that is not ``https`` on its own host.
        ApiError: Composio refused or failed the session (the save is rolled back).
    """
    apps = config.tools.apps
    wanted: McpOriginKind | None = (
        "server" if apps.mode == "server" else "router" if apps.mode == "router" else None
    )
    existing = await origin_rows(db, ctx.workspace_id, agent.id)
    tool_ids = [tid for tid in config.tools.tool_ids if tid not in {row.id for row in existing}]
    keep: Tool | None = None
    for row in existing:
        origin = origin_of(row)
        if keep is None and origin is not None and origin.kind == wanted:
            keep = row
        else:
            await _teardown(db, vault, factory, ctx, row)
    if wanted is None:
        config.tools.tool_ids = tool_ids
        return config

    adapter, key = await service.workspace_adapter(db, vault, factory, ctx.workspace_id)
    connections = await service.list_connection_records(db, vault, ctx.workspace_id)
    plan = plan_session(apps, connections, workspace_id=ctx.workspace_id, agent_id=agent.id)
    previous = origin_of(keep) if keep is not None else None
    same_key = keep is not None and _definition(keep).get("credential_id") == key.id
    if keep is not None and previous is not None and previous.config_hash == plan.config_hash and same_key:
        config.tools.tool_ids = [*tool_ids, keep.id]
        return config

    try:
        created = await adapter.create_router_session(subject=plan.subject, options=plan.options)
    except ToolProviderError as exc:
        raise service.api_error(exc) from exc
    session_id = str(created.get("session_id") or "")
    mcp = created.get("mcp") if isinstance(created.get("mcp"), dict) else {}
    url = str(mcp.get("url") or "") if isinstance(mcp, dict) else ""
    if not session_id or not is_composio_url(url):
        if session_id:
            await _delete_session(adapter, session_id)
        raise UnprocessableEntityError(
            "Composio answered with an app server address LKAP does not allow "
            "(only https on Composio's own host is accepted)",
            details={"path": "tools.apps.mode"},
        )
    definition = McpServerDefinition(
        name=SERVER_TOOL_NAME if plan.kind == "server" else ROUTER_TOOL_NAME,
        url=url,
        headers={"x-api-key": "{{ secret.api_key }}"},
        credential_id=key.id,
        allowed_tools=plan.allowed_tools,
        timeout_s=MCP_TIMEOUT_S,
        tool_options=plan.tool_options,
        origin=McpServerOrigin(kind=plan.kind, remote_id=session_id, config_hash=plan.config_hash),
    )
    if keep is None:
        keep = Tool(
            workspace_id=ctx.workspace_id,
            agent_id=agent.id,
            kind="mcp",
            name=definition.name,
            definition=definition.model_dump(mode="json"),
            enabled=True,
        )
        db.add(keep)
    else:
        keep.name = definition.name
        keep.definition = definition.model_dump(mode="json")
        keep.enabled = True
        keep.updated_at = utcnow()
    await db.flush()
    if previous is not None and previous.remote_id != session_id:
        await _delete_session(adapter, previous.remote_id)
    _audit(
        db,
        ctx,
        f"apps.{plan.kind}.create",
        agent.id,
        tool_id=keep.id,
        toolkits=plan.toolkits,
        tools=len(plan.allowed_tools),
        replaced=previous is not None,
    )
    log.info("apps_session_provisioned", agent_id=agent.id, kind=plan.kind, toolkits=len(plan.toolkits))
    config.tools.tool_ids = [*tool_ids, keep.id]
    return config


__all__ = [
    "MCP_TIMEOUT_S",
    "ROUTER_TOOL_NAME",
    "SERVER_TOOL_NAME",
    "AppsProvisioner",
    "AppsProvisionerDep",
    "SessionPlan",
    "get_apps_provisioner",
    "origin_of",
    "origin_rows",
    "plan_session",
    "sync_agent_apps",
]
