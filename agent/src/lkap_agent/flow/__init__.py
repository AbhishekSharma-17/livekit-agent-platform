"""Flow runtime (PLAN-V2 V2-15): a `FlowSpec` executed as LiveKit agent handoffs.

Public surface used by the worker (`main._assemble` / `main.run_session`):

* :func:`is_flow` / :func:`flow_of` — does this session run a flow?
* :func:`prepare_flow_resolved` — start-node greeting and `qa` node overrides
  applied to the resolved config before the session is built.
* :class:`FlowServices` + :func:`build_flow_agent` — the first node's agent,
  handed to `session.start` in place of a plain `PlatformAgent`.
"""

from __future__ import annotations

from lkap_agent.flow.node_agent import FlowNodeAgent
from lkap_agent.flow.runtime import (
    FlowRuntime,
    FlowServices,
    TransferHandler,
    flow_of,
    is_flow,
    prepare_flow_resolved,
)
from lkap_agent.flow.state import FlowUserdata, ScopedKbClient

__all__ = [
    "FlowNodeAgent",
    "FlowRuntime",
    "FlowServices",
    "FlowUserdata",
    "ScopedKbClient",
    "TransferHandler",
    "build_flow_agent",
    "flow_of",
    "is_flow",
    "prepare_flow_resolved",
]


def build_flow_agent(services: FlowServices) -> FlowNodeAgent:
    """Create the session's `FlowRuntime` and return the agent `session.start` runs.

    Raises:
        ValueError: If `services.resolved` carries no flow (a prompt agent).
    """
    spec = flow_of(services.resolved)
    if spec is None:
        raise ValueError("build_flow_agent needs a resolved config with a non-empty flow")
    return FlowRuntime(spec, services).start_agent()
