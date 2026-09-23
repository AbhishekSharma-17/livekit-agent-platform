"""Session-concurrency helpers shared by every route that starts a session.

``connect`` / ``text-sessions`` (browser sessions) and ``POST /v1/calls``
(outbound phone calls, ruling R-V2-23) all count an agent's live sessions
against ``AgentLimits.max_concurrent_sessions``. The count lives here, not in a
router, so ``lkap_api.telephony.calls`` never imports a router module.

**Reservation (R-V2-34, REVIEW-V2 R2-15).** Counting and inserting are two
statements, so a burst of simultaneous starts could all read the same count and
all insert (a burst of 50 against a cap of 5 admitted 14). The three starters
therefore hold a *slot lock* from the count until their ``db.commit()`` has
returned, so the next reader sees the new row:

* a per-process :class:`asyncio.Lock` keyed by the agent (or, for the
  outbound-call cap, the workspace); and
* on Postgres, ``SELECT … FOR UPDATE`` on the agent (or workspace) row, which
  serialises api processes. SQLAlchemy drops ``FOR UPDATE`` on SQLite, where
  the guarantee rests on the in-process lock alone: **a SQLite deployment runs
  exactly one api process** (RUNBOOK); Postgres is required for ``api ×2``.

:func:`reserve_session_slot` is the one-call form for the browser starters;
:func:`slot_lock` is the building block ``routers/calls.py`` nests (workspace
outer, agent inner) around ``prepare_outbound_call``, which does its own counts.
"""

from __future__ import annotations

import asyncio
import weakref
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Literal

from lkap_contracts.agent_config import AgentLimits
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from lkap_api.auth.ratelimit import AgentBusyError
from lkap_api.db.models import Agent, Workspace
from lkap_api.db.models import Session as SessionRow

__all__ = ["LIVE_STATUSES", "live_session_count", "reserve_session_slot", "slot_lock"]

#: Session states that occupy a concurrency slot.
LIVE_STATUSES = ("created", "active")

LockScope = Literal["agent", "workspace"]

#: Per event loop (pytest gives every test its own), one lock per scope and id.
#: Values are weak, so a lock disappears once nobody holds or awaits it.
_LOCKS: weakref.WeakKeyDictionary[
    asyncio.AbstractEventLoop, weakref.WeakValueDictionary[str, asyncio.Lock]
] = weakref.WeakKeyDictionary()


def _process_lock(scope: LockScope, key: str) -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    locks = _LOCKS.get(loop)
    if locks is None:
        locks = weakref.WeakValueDictionary()
        _LOCKS[loop] = locks
    name = f"{scope}:{key}"
    lock = locks.get(name)
    if lock is None:
        lock = asyncio.Lock()
        locks[name] = lock
    return lock


@asynccontextmanager
async def slot_lock(
    db: AsyncSession, *, workspace_id: str, agent_id: str | None = None
) -> AsyncIterator[None]:
    """Hold the slot lock of one agent (``agent_id``) or of the workspace until the block exits.

    Takes the per-process lock, then (Postgres only) row-locks the agent or
    workspace row in ``db``'s transaction. The caller commits **inside** the
    block, so the row it inserted is visible before the next holder counts.

    Args:
        db: The request's session (its transaction carries the row lock).
        workspace_id: The workspace; alone, the lock guards the dialing
            policy's ``max_concurrent_outbound``.
        agent_id: The agent, whose ``max_concurrent_sessions`` the lock guards.
    """
    scope: LockScope = "agent" if agent_id is not None else "workspace"
    async with _process_lock(scope, agent_id or workspace_id):
        # A no-op on SQLite (the dialect drops FOR UPDATE); a row lock on Postgres.
        if agent_id is not None:
            row = select(Agent.id).where(Agent.workspace_id == workspace_id, Agent.id == agent_id)
        else:
            row = select(Workspace.id).where(Workspace.id == workspace_id)
        await db.execute(row.with_for_update())
        yield


async def live_session_count(db: AsyncSession, agent: Agent) -> int:
    """Count the agent's sessions that still hold a concurrency slot."""
    return int(
        (
            await db.execute(
                select(func.count())
                .select_from(SessionRow)
                .where(
                    SessionRow.workspace_id == agent.workspace_id,
                    SessionRow.agent_id == agent.id,
                    SessionRow.status.in_(LIVE_STATUSES),
                )
            )
        ).scalar_one()
    )


@asynccontextmanager
async def reserve_session_slot(db: AsyncSession, agent: Agent, limits: AgentLimits) -> AsyncIterator[None]:
    """Reserve one of the agent's ``max_concurrent_sessions`` slots (R-V2-34).

    Use as ``async with reserve_session_slot(db, agent, limits): …; await db.commit()``:
    the lock is held from the count until the caller's commit has returned.

    Raises:
        AgentBusyError: The agent already runs ``max_concurrent_sessions`` sessions.
    """
    async with slot_lock(db, workspace_id=agent.workspace_id, agent_id=agent.id):
        if await live_session_count(db, agent) >= limits.max_concurrent_sessions:
            raise AgentBusyError(
                "this agent is at its concurrent session limit; try again shortly",
                details={"max_concurrent_sessions": limits.max_concurrent_sessions},
            )
        yield
