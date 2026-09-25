"""Picked actions become agent tools (docs/v5/COMPOSIO.md D-V5-C7, D-V5-C8; V5-47).

Materialisation turns each picked action of a connected app into one
``tools`` row of kind ``provider`` (:class:`ProviderToolDefinition`):

* the name is ``<toolkit>_<action>`` in lower snake case, at most 64
  characters (a long slug is cut and suffixed with a short hash, so the
  name stays stable), unique in the workspace;
* the description is the first sentence of Composio's (editable later);
* the parameters are pinned from the action's input schema, with the
  Composio tool version, so a vendor change never alters a live agent
  until an admin refreshes the schema (:func:`refresh_schema` diffs);
* the execution policy follows the action's risk: reads run ``auto`` with
  a short announcement, writes and destructive actions block and are never
  cancellable; every action is bounded to 20 seconds;
* ``max_result_chars=1500``, ``result_path="data"``, ``silent_reply=False``.

One tool per ``(connection, action)``: picking an action again reuses its
tool. Attaching to an agent appends the tool ids to ``tools.tool_ids``,
turns ``tools.apps.mode`` from ``off`` to ``actions`` and writes a new
config version, exactly like a save.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Final

from lkap_contracts.agent_config import AgentConfig
from lkap_contracts.tool_providers import AppActionOut
from lkap_contracts.tools import ProviderToolDefinition, ToolExecution
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lkap_api.auth.deps import WorkspaceContext
from lkap_api.db.models import Agent, AgentConfigVersion, Credential, Tool, utcnow
from lkap_api.errors import ConflictError, NotFoundError, UnprocessableEntityError
from lkap_api.logging import get_logger

log = get_logger(__name__)

#: The longest model-facing tool name (``TOOL_NAME_PATTERN``).
MAX_TOOL_NAME: Final = 64

#: Every action is bounded (D-V5-C7): a slow vendor must not hold the call.
ACTION_MAX_DURATION_S: Final = 20.0

#: What the model is told when a read runs past the inline threshold (D-V5-C7).
READ_ANNOUNCE: Final = "Let me look that up"

#: The longest description kept from the vendor's text.
MAX_DESCRIPTION: Final = 300

_NON_NAME = re.compile(r"[^a-z0-9_]+")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s")


def tool_name_for(toolkit: str, slug: str) -> str:
    """The model-facing name of an action: ``<toolkit>_<action>`` lower snake, ≤ 64 characters.

    Composio slugs already start with the toolkit (``GOOGLECALENDAR_FIND_FREE_SLOTS``);
    one that does not gets the toolkit prefixed. A name longer than 64 characters keeps
    its first 55 and ends with ``_`` plus 8 hex characters of the slug's SHA-1, so two
    long slugs never collide and the name is the same on every import.

    Args:
        toolkit: The app's slug, e.g. ``googlecalendar``.
        slug: The action's slug.

    Returns:
        A name matching ``TOOL_NAME_PATTERN``.
    """
    base = _NON_NAME.sub("_", slug.strip().lower()).strip("_")
    prefix = _NON_NAME.sub("_", toolkit.strip().lower()).strip("_")
    if prefix and not base.startswith(f"{prefix}_") and base != prefix:
        base = f"{prefix}_{base}" if base else prefix
    if not base or not (base[0].isalpha() or base[0] == "_"):
        base = f"app_{base}"
    if len(base) > MAX_TOOL_NAME:
        digest = hashlib.sha1(slug.encode("utf-8"), usedforsecurity=False).hexdigest()[:8]
        base = f"{base[: MAX_TOOL_NAME - 9].rstrip('_')}_{digest}"
    return base


def first_sentence(text: str, *, fallback: str) -> str:
    """The first sentence of a vendor description (D-V5-C8), or ``fallback`` when empty."""
    cleaned = " ".join((text or "").split())
    if not cleaned:
        return fallback
    sentence = _SENTENCE_END.split(cleaned, maxsplit=1)[0]
    return sentence[:MAX_DESCRIPTION]


def execution_for(action: AppActionOut) -> ToolExecution:
    """The execution policy of an action by its risk (D-V5-C7)."""
    if action.risk == "read":
        return ToolExecution(mode="auto", announce=READ_ANNOUNCE, max_duration_s=ACTION_MAX_DURATION_S)
    return ToolExecution(mode="blocking", cancellable=False, max_duration_s=ACTION_MAX_DURATION_S)


def pinned_parameters(parameters: dict[str, Any]) -> dict[str, Any]:
    """The action's input schema as a function-tool schema (always an object)."""
    if parameters.get("type") == "object":
        return dict(parameters)
    return {"type": "object", "properties": {}, **parameters}


def definition_for(
    action: AppActionOut,
    *,
    name: str,
    toolkit: str,
    connection_id: str,
    credential_id: str,
    subject: str,
) -> ProviderToolDefinition:
    """The ``provider`` definition of one picked action (D-V5-C8)."""
    return ProviderToolDefinition(
        name=name,
        description=first_sentence(action.description, fallback=action.name or action.slug),
        parameters=pinned_parameters(action.parameters),
        tool_slug=action.slug,
        toolkit=toolkit,
        connection_id=connection_id,
        credential_id=credential_id,
        subject=subject,
        execution=execution_for(action),
        schema_version=action.version,
        risk=action.risk,
    )


def _definition(tool: Tool) -> dict[str, Any]:
    return tool.definition if isinstance(tool.definition, dict) else {}


async def provider_tools_of(db: AsyncSession, workspace_id: str, connection_id: str) -> list[Tool]:
    """Every ``provider`` tool of one connection."""
    rows = (
        await db.execute(select(Tool).where(Tool.workspace_id == workspace_id, Tool.kind == "provider"))
    ).scalars()
    return [row for row in rows.all() if _definition(row).get("connection_id") == connection_id]


async def _unique_name(db: AsyncSession, workspace_id: str, wanted: str, taken: set[str]) -> str:
    names = set(
        (await db.execute(select(Tool.name).where(Tool.workspace_id == workspace_id))).scalars().all()
    )
    names |= taken
    if wanted not in names:
        return wanted
    for index in range(2, 100):
        suffix = f"_{index}"
        candidate = f"{wanted[: MAX_TOOL_NAME - len(suffix)]}{suffix}"
        if candidate not in names:
            return candidate
    raise ConflictError(f"too many tools are named like '{wanted}'; rename some first")


@dataclass(frozen=True)
class MaterialiseResult:
    """What :func:`materialise_actions` did."""

    created: list[str]
    existing: list[str]
    tool_ids: list[str]


async def materialise_actions(
    db: AsyncSession,
    ctx: WorkspaceContext,
    *,
    actions: list[AppActionOut],
    toolkit: str,
    connection_id: str,
    subject: str,
    connection_agent_id: str | None,
    key: Credential,
) -> MaterialiseResult:
    """Create (or reuse) one ``provider`` tool per action of a connection.

    Args:
        db: Open session.
        ctx: The caller's workspace context.
        actions: The picked actions, as listed by Composio (already risk-checked).
        toolkit: The connection's app.
        connection_id: The connected-app row id.
        subject: The connection's subject (copied onto each tool).
        connection_agent_id: The agent of an ``agent:<id>`` connection: its tools are owned
            by that agent; a workspace connection's tools are shared.
        key: The workspace's Composio key row (``credential_id`` of every tool).

    Returns:
        Ids created, ids that already existed, and every id in ``actions`` order.
    """
    existing = {
        str(_definition(row).get("tool_slug", "")).upper(): row
        for row in await provider_tools_of(db, ctx.workspace_id, connection_id)
    }
    created: list[str] = []
    reused: list[str] = []
    ordered: list[str] = []
    taken: set[str] = set()
    for action in actions:
        row = existing.get(action.slug.upper())
        if row is not None:
            reused.append(row.id)
            ordered.append(row.id)
            continue
        name = await _unique_name(db, ctx.workspace_id, tool_name_for(toolkit, action.slug), taken)
        taken.add(name)
        definition = definition_for(
            action,
            name=name,
            toolkit=toolkit,
            connection_id=connection_id,
            credential_id=key.id,
            subject=subject,
        )
        tool = Tool(
            workspace_id=ctx.workspace_id,
            agent_id=connection_agent_id,
            kind="provider",
            name=name,
            definition=definition.model_dump(mode="json"),
            enabled=True,
        )
        db.add(tool)
        await db.flush()
        created.append(tool.id)
        ordered.append(tool.id)
    log.info("apps_materialised", connection_id=connection_id, created=len(created), reused=len(reused))
    return MaterialiseResult(created=created, existing=reused, tool_ids=ordered)


async def attach_tools(
    db: AsyncSession, ctx: WorkspaceContext, agent_id: str, tool_ids: list[str], *, note: str
) -> Agent:
    """Append ``tool_ids`` to an agent's ``tools.tool_ids`` as a new config version.

    ``tools.apps.mode`` turns from ``off`` to ``actions`` (the console's "Use picked
    actions"); any other mode is left alone (actions combine with a server or finder).
    A save that changes nothing writes no version.
    """
    agent = await db.scalar(select(Agent).where(Agent.id == agent_id, Agent.workspace_id == ctx.workspace_id))
    if agent is None:
        raise UnprocessableEntityError(f"unknown agent '{agent_id}'")
    try:
        config = AgentConfig.model_validate(agent.config)
    except ValidationError as exc:
        raise ConflictError("the agent's stored configuration does not parse; fix it first") from exc
    ids = list(dict.fromkeys([*config.tools.tool_ids, *tool_ids]))
    mode = config.tools.apps.mode
    config.tools.tool_ids = ids
    if mode == "off":
        config.tools.apps.mode = "actions"
    new_config = config.model_dump(mode="json")
    if new_config == agent.config:
        return agent
    agent.config = new_config
    agent.config_version += 1
    agent.updated_at = utcnow()
    db.add(
        AgentConfigVersion(
            agent_id=agent.id,
            config_version=agent.config_version,
            config=agent.config,
            created_by=ctx.actor.id,
            note=note,
        )
    )
    await db.flush()
    return agent


# ------------------------------------------------------------------ refresh schema
class SchemaRefreshOut(BaseModel):
    """``POST /v1/tool-providers/composio/tools/{id}/refresh-schema``: what changed upstream."""

    tool_id: str
    tool_slug: str
    changed: bool = Field(description="Whether the action's inputs or version differ from the pinned ones")
    applied: bool = Field(description="Whether the new inputs were written to the tool")
    schema_version_before: str | None = None
    schema_version_after: str | None = None
    added: list[str] = Field(default_factory=list, description="Input fields Composio added")
    removed: list[str] = Field(default_factory=list, description="Input fields Composio removed")
    modified: list[str] = Field(default_factory=list, description="Input fields whose definition changed")
    required_before: list[str] = Field(default_factory=list)
    required_after: list[str] = Field(default_factory=list)


def schema_diff(before: dict[str, Any], after: dict[str, Any]) -> tuple[list[str], list[str], list[str]]:
    """``(added, removed, modified)`` property names between two object schemas."""
    old = before.get("properties") if isinstance(before.get("properties"), dict) else {}
    new = after.get("properties") if isinstance(after.get("properties"), dict) else {}
    assert isinstance(old, dict) and isinstance(new, dict)  # noqa: S101 - narrowed above
    added = sorted(set(new) - set(old))
    removed = sorted(set(old) - set(new))
    modified = sorted(name for name in set(old) & set(new) if old[name] != new[name])
    return added, removed, modified


def _required(schema: dict[str, Any]) -> list[str]:
    value = schema.get("required")
    return sorted(str(v) for v in value) if isinstance(value, list) else []


async def load_provider_tool(db: AsyncSession, ctx: WorkspaceContext, tool_id: str) -> Tool:
    """A ``provider`` tool of the caller's workspace, or 404."""
    row = await db.scalar(select(Tool).where(Tool.id == tool_id, Tool.workspace_id == ctx.workspace_id))
    if row is None or row.kind != "provider":
        raise NotFoundError(f"unknown app action tool '{tool_id}'")
    return row


def refresh_result(tool: Tool, action: AppActionOut, *, apply: bool) -> SchemaRefreshOut:
    """Compare a tool's pinned schema with the action's current one; with ``apply``, write it."""
    definition = ProviderToolDefinition.model_validate(tool.definition)
    fresh = pinned_parameters(action.parameters)
    added, removed, modified = schema_diff(definition.parameters, fresh)
    changed = bool(
        added
        or removed
        or modified
        or _required(definition.parameters) != _required(fresh)
        or (action.version or None) != definition.schema_version
    )
    applied = False
    if apply and changed:
        updated = definition.model_copy(update={"parameters": fresh, "schema_version": action.version})
        tool.definition = updated.model_dump(mode="json")
        tool.updated_at = utcnow()
        applied = True
    return SchemaRefreshOut(
        tool_id=tool.id,
        tool_slug=definition.tool_slug,
        changed=changed,
        applied=applied,
        schema_version_before=definition.schema_version,
        schema_version_after=action.version,
        added=added,
        removed=removed,
        modified=modified,
        required_before=_required(definition.parameters),
        required_after=_required(fresh),
    )


__all__ = [
    "ACTION_MAX_DURATION_S",
    "MAX_TOOL_NAME",
    "READ_ANNOUNCE",
    "MaterialiseResult",
    "SchemaRefreshOut",
    "attach_tools",
    "definition_for",
    "execution_for",
    "first_sentence",
    "load_provider_tool",
    "materialise_actions",
    "pinned_parameters",
    "provider_tools_of",
    "refresh_result",
    "schema_diff",
    "tool_name_for",
]
