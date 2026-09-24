"""``GET /v1/templates`` and ``GET /v1/templates/{template_id}`` (docs/v4/TEMPLATES.md D-V4-3, D-V4-4).

Readable like ``/v1/packs``: any workspace member (``viewer``) or an API key
with ``agents:read``, so the console's New agent dialog and the MCP's
``lkap://templates`` work for builders and read-only keys alike.

Importing this module loads the catalogue, so a malformed entry fails
application startup instead of the first request (R-V4-5).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from lkap_contracts.api_models import TemplateOut, TemplatesResponse
from lkap_contracts.packs import PackManifest
from lkap_contracts.templates import StarterTemplate

from lkap_api.auth.deps import WorkspaceContext, require
from lkap_api.deps import SettingsDep
from lkap_api.errors import NotFoundError, UnprocessableEntityError
from lkap_api.logging import get_logger
from lkap_api.packs import discover_manifests
from lkap_api.templates.catalog import (
    DERIVED_PREFIX,
    available_templates,
    derived_template,
    get_template,
    load_catalog,
    missing_pack_templates,
)

log = get_logger(__name__)

router = APIRouter(prefix="/v1/templates", tags=["templates"])

TemplateReaderDep = Annotated[WorkspaceContext, Depends(require("viewer", "agents:read"))]

load_catalog()


def gallery(packs: list[str]) -> list[TemplateOut]:
    """The starters in gallery order for the installed ``packs`` (``LKAP_PACKS``)."""
    manifests = discover_manifests(packs)
    for template in missing_pack_templates(manifests):
        log.warning("template_pack_missing", template_id=template.id, pack_id=template.pack_id)
    return [
        TemplateOut(template=template, pack=manifest, derived=derived)
        for template, manifest, derived in available_templates(manifests)
    ]


def resolve_template(packs: list[str], template_id: str) -> tuple[StarterTemplate, PackManifest]:
    """The starter ``template_id`` names and the manifest of its pack.

    Accepts catalogue ids and derived ``pack:<pack_id>`` ids.

    Raises:
        UnprocessableEntityError: The id is unknown, or its pack is not installed;
            ``details.known`` lists the ids that would work.
    """
    manifests = discover_manifests(packs)
    by_id = {manifest.id: manifest for manifest in manifests}
    known = [template.id for template, _, _ in available_templates(manifests)]
    if template_id.startswith(DERIVED_PREFIX):
        manifest = by_id.get(template_id.removeprefix(DERIVED_PREFIX))
        if manifest is not None:
            return derived_template(manifest), manifest
    else:
        template = get_template(template_id)
        if template is not None:
            manifest = by_id.get(template.pack_id)
            if manifest is not None:
                return template, manifest
            raise UnprocessableEntityError(
                f"template '{template_id}' needs pack '{template.pack_id}', which is not installed "
                f"(LKAP_PACKS); known templates: {', '.join(known)}",
                details={"template_id": template_id, "pack_id": template.pack_id, "known": known},
            )
    raise UnprocessableEntityError(
        f"unknown template '{template_id}'; known templates: {', '.join(known)}",
        details={"template_id": template_id, "known": known},
    )


@router.get(
    "",
    response_model=TemplatesResponse,
    summary="List starter templates",
    description=(
        "The starter templates for the New agent gallery, in gallery order: the catalogue "
        "starters whose pack is installed, then one derived `pack:<id>` entry per installed "
        "pack that no catalogue starter references."
    ),
)
async def list_templates(settings: SettingsDep, _ctx: TemplateReaderDep) -> TemplatesResponse:
    """Return every available starter."""
    return TemplatesResponse(items=gallery(settings.packs_list))


@router.get(
    "/{template_id}",
    response_model=TemplateOut,
    summary="Get a starter template",
    description="One starter (catalogue or derived `pack:<id>`) with the manifest of the pack it layers on.",
)
async def get_template_out(template_id: str, settings: SettingsDep, _ctx: TemplateReaderDep) -> TemplateOut:
    """Return one starter.

    Raises:
        NotFoundError: No available starter has this id.
    """
    for item in gallery(settings.packs_list):
        if item.template.id == template_id:
            return item
    raise NotFoundError(f"unknown template '{template_id}'")
