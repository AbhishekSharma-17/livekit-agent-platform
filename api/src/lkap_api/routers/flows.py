"""Flow node specs and draft-flow validation (PLAN-V2 V2-16, CONTRACTS-V2 §3.4).

* ``GET /v1/flows/node-specs`` — the JSON Schema of every node kind, which the
  builder turns into node forms.
* ``POST /v1/agents/{agent_id}/flow/validate`` — validates an **unsaved** flow
  in the context of the agent's stored configuration: structural findings get
  a per-node/per-edge path (so the canvas can put a dot on the node), then the
  same checks a save runs (``config_service.validate`` with every registered
  validator, including :func:`lkap_api.flows.validation.flow_issues`). Only the
  flow's own findings are returned. The flow itself is saved with
  ``PUT /v1/agents/{id}`` (``config.flow``).

The router carries no prefix so the validate route keeps the CONTRACTS path
under ``/v1/agents``; ``main.py`` includes it as before.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Body
from lkap_contracts.api_models import Issue, NodeSpecsResponse, ValidationResult

from lkap_api.config_service import validate, validation_context_for
from lkap_api.deps import AdminCtxDep, DbDep
from lkap_api.flows import draft_flow_issues, node_specs
from lkap_api.routers.agents import agent_config_of, load_scoped_agent

router = APIRouter(tags=["flows"])

#: Paths a flow-scoped validation reports: the flow itself and R-V2-11's QA note.
_FLOW_PATH_PREFIXES: tuple[str, ...] = ("flow", "qa.enabled")


def _result(issues: list[Issue]) -> ValidationResult:
    errors = [f"{i.path}: {i.message}" for i in issues if i.severity == "error"]
    warnings = [f"{i.path}: {i.message}" for i in issues if i.severity == "warning"]
    return ValidationResult(ok=not errors, errors=errors, warnings=warnings, issues=issues)


@router.get(
    "/v1/flows/node-specs",
    response_model=NodeSpecsResponse,
    summary="Flow node specs",
    description=(
        "The JSON Schema of every flow node kind (start, agent, end, transfer, global, qa), "
        "generated from the contract models. The flow builder renders node forms from it."
    ),
)
async def get_node_specs(_ctx: AdminCtxDep) -> NodeSpecsResponse:
    """Return one schema per node kind."""
    return node_specs()


@router.post(
    "/v1/agents/{agent_id}/flow/validate",
    response_model=ValidationResult,
    summary="Validate an unsaved flow",
    description=(
        "Body `{flow: FlowSpec}`. Checks the graph's structure (one start node, reachable nodes, "
        "valid edges) with a path per finding, then the agent-level references a save checks: node "
        "tools and knowledge bases, per-node provider overrides, variables. Nothing is saved."
    ),
)
async def validate_flow(
    agent_id: str,
    db: DbDep,
    ctx: AdminCtxDep,
    payload: Annotated[
        dict[str, Any], Body(examples=[{"flow": {"nodes": [], "edges": [], "variables": []}}])
    ],
) -> ValidationResult:
    """Validate a draft flow against the agent's stored configuration."""
    row = await load_scoped_agent(db, ctx, agent_id)
    raw = payload.get("flow")
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        return _result([Issue(path="flow", message="must be an object")])
    spec, structural = draft_flow_issues(raw)
    if spec is None:
        return _result(structural)
    config = agent_config_of(row).model_copy(update={"flow": spec})
    context = await validation_context_for(
        db, config, workspace_id=row.workspace_id, connection_id=row.connection_id
    )
    full = validate(context)
    return _result([i for i in full.issues if i.path.startswith(_FLOW_PATH_PREFIXES)])
