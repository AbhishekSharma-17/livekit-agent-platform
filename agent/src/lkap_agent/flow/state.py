"""Where a running flow keeps its state (CONTRACTS-V2 §4.5 runtime contract).

`FlowState{current_node, path, variables, disposition}` lives on
`AgentSession.userdata.flow`. The worker sets no other session userdata, so a
flow session gets a :class:`FlowUserdata`; the same `FlowState` object is
mirrored into the pack's `SessionContext.userdata["flow"]` so pack hooks and
tools can read it without touching the session.

`ScopedKbClient` wraps the session's `KbClient` so each node searches only its
own knowledge bases: auto-inject and the `search_knowledge` built-in both read
`SessionContext.kb` at call time, and the flow runtime moves the scope on every
node entry.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from lkap_contracts.api_models import KbHit
from lkap_contracts.flow import FlowState
from packs.base import KbClient

from lkap_agent.logging import get_logger

__all__ = ["FlowUserdata", "ScopedKbClient", "attach_flow_state"]

logger = get_logger(__name__)


@dataclass(slots=True)
class FlowUserdata:
    """`AgentSession.userdata` for a flow session: `session.userdata.flow`."""

    flow: FlowState


def attach_flow_state(session: Any, state: FlowState) -> None:
    """Expose `state` as `session.userdata.flow`.

    An unset userdata (the worker's default) gets a :class:`FlowUserdata`; an
    existing object gets a `flow` attribute, a dict a `"flow"` key. Never raises.
    """
    try:
        current = session.userdata
    except (ValueError, AttributeError):
        current = None
    try:
        if current is None:
            session.userdata = FlowUserdata(flow=state)
        elif isinstance(current, dict):
            current["flow"] = state
        else:
            current.flow = state
    except Exception:
        logger.warning("could not attach the flow state to session.userdata", exc_info=True)


class ScopedKbClient:
    """A `packs.base.KbClient` whose default knowledge bases follow the current flow node."""

    def __init__(self, inner: KbClient, kb_ids: list[str]) -> None:
        self._inner = inner
        self._kb_ids = list(kb_ids)

    @property
    def kb_ids(self) -> list[str]:
        """The knowledge bases searched when a caller passes no `kb_ids`."""
        return list(self._kb_ids)

    def set_scope(self, kb_ids: list[str]) -> None:
        """Point default searches at `kb_ids` (a node entry)."""
        self._kb_ids = list(kb_ids)

    async def search(self, query: str, k: int = 4, kb_ids: list[str] | None = None) -> list[KbHit]:
        """Search `kb_ids`, or the current node's knowledge bases; none → no hits."""
        targets = kb_ids if kb_ids is not None else self._kb_ids
        if not targets:
            return []
        return await self._inner.search(query, k=k, kb_ids=targets)
