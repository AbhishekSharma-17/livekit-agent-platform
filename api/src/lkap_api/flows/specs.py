"""`GET /v1/flows/node-specs`: one JSON Schema per flow node kind (CONTRACTS-V2 §3.4).

The flow builder generates each node's inspector form from these schemas
(``web/src/lib/schema-form``), so a field added to a node model in
``lkap_contracts.flow`` shows up in the builder without a web change.
"""

from __future__ import annotations

from typing import Final

from lkap_contracts.api_models import NodeSpecSchema, NodeSpecsResponse
from lkap_contracts.flow import AgentNode, EndNode, GlobalNode, NodeBase, QaNode, StartNode, TransferNode

#: Every node kind in palette order, with its builder label.
NODE_KINDS: Final[tuple[tuple[str, str, type[NodeBase]], ...]] = (
    ("start", "Start", StartNode),
    ("agent", "Agent step", AgentNode),
    ("end", "End", EndNode),
    ("transfer", "Transfer", TransferNode),
    ("global", "Global rules", GlobalNode),
    ("qa", "QA scoring", QaNode),
)


def node_specs() -> NodeSpecsResponse:
    """Build the node-spec document from the contract models.

    Returns:
        One :class:`NodeSpecSchema` per node kind; ``json_schema`` is the
        model's validation schema (``$defs`` included, so it is self-contained).
    """
    return NodeSpecsResponse(
        nodes=[
            NodeSpecSchema(kind=kind, label=label, json_schema=model.model_json_schema(mode="validation"))
            for kind, label, model in NODE_KINDS
        ]
    )
