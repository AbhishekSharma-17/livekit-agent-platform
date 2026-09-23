"""Session-concurrency helpers shared by every route that starts a session.

``connect`` / ``text-sessions`` (browser sessions) and ``POST /v1/calls``
(outbound phone calls, ruling R-V2-23) all count an agent's live sessions
against ``AgentLimits.max_concurrent_sessions``. The count lives here, not in a
router, so ``lkap_api.telephony.calls`` never imports a router module.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from lkap_api.db.models import Agent
from lkap_api.db.models import Session as SessionRow

__all__ = ["LIVE_STATUSES", "live_session_count"]

#: Session states that occupy a concurrency slot.
LIVE_STATUSES = ("created", "active")


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
