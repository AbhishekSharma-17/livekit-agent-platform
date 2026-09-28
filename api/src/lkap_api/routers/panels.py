"""``GET /v1/panels/presets``: ready-made panels a builder can start from (V6-08, D-V6-15).

Readable like ``/v1/templates``: any workspace member (``viewer``) or an API key with
``agents:read``, so the console's Create agent dialog, the panel editor and the MCP's
``agent_update(panel_preset=...)`` can offer them. The presets are constants in
:mod:`lkap_contracts.agent_config` (today the "Notebook" preset); choosing one copies its
``panel`` into the agent's config, which the usual save validation then checks.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from lkap_contracts.agent_config import PANEL_PRESETS, PanelPresetsResponse

from lkap_api.auth.deps import WorkspaceContext, require

router = APIRouter(tags=["panels"])

PresetReaderDep = Annotated[WorkspaceContext, Depends(require("viewer", "agents:read"))]


@router.get(
    "/v1/panels/presets",
    response_model=PanelPresetsResponse,
    summary="List ready-made panels",
    description=(
        "The ready-made panels a builder can start from, in the order the console offers them "
        "(today the Notebook: a wide panel with a status stamp, a notebook and a gallery). "
        "Choosing one replaces the agent's `panel` with a copy of the preset's."
    ),
)
async def list_panel_presets(_ctx: PresetReaderDep) -> PanelPresetsResponse:
    """Return every ready-made panel (copies, so a caller can never change the constants)."""
    return PanelPresetsResponse(items=[preset.model_copy(deep=True) for preset in PANEL_PRESETS])
