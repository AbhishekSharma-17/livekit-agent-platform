"""``GET /v1/templates`` and ``GET /v1/templates/{template_id}`` (docs/v4/TEMPLATES.md D-V4-3, D-V4-4),
plus the tool templates ``GET /v1/tool-templates`` and ``POST /v1/tool-templates/{id}/instantiate``
(V5-25, D-V5-36) and the tool kits ``GET /v1/tool-kits``, ``GET /v1/tool-kits/{id}`` and
``POST /v1/tool-kits/{id}/instantiate`` (V6-18, D-V6-26; the Cal.com set is also the ``booking``
kit, and ``/v1/tool-templates`` stays).

Readable like ``/v1/packs``: any workspace member (``viewer``) or an API key
with ``agents:read``, so the console's New agent dialog and the MCP's
``lkap://templates`` work for builders and read-only keys alike. Instantiating
a tool template creates tool rows, so it needs what ``POST /v1/tools`` needs
(``builder`` or ``agents:write``). The router has no prefix so both families
live here without another ``include_router``.

Importing this module loads the catalogue, so a malformed entry fails
application startup instead of the first request (R-V4-5).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from lkap_contracts import pricing
from lkap_contracts.api_models import TemplateEstimate, TemplateOut, TemplatesResponse, ToolCreate
from lkap_contracts.kits import ToolKit, ToolKitInstantiate, ToolKitInstantiated, ToolKitsResponse
from lkap_contracts.packs import PackManifest
from lkap_contracts.templates import StarterTemplate
from lkap_contracts.tools import ToolTemplateInstantiate, ToolTemplateInstantiated, ToolTemplatesResponse

from lkap_api.auth.deps import WorkspaceContext, require
from lkap_api.costs.assumptions import default_assumptions
from lkap_api.costs.estimate import build_estimate, table_quote, template_estimate
from lkap_api.deps import DbDep, SettingsDep, VaultDep
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
from lkap_api.templates.kit_apply import instantiate_kit
from lkap_api.templates.kits import get_kit, load_kits
from lkap_api.templates.seed import seed_from_template
from lkap_api.templates.tools import (
    ToolTemplateError,
    group_templates,
    instantiate_definition,
    load_tool_templates,
    tool_template,
)
from lkap_api.tool_providers.adapter import AdapterFactory
from lkap_api.tool_providers.router import get_adapter_factory

log = get_logger(__name__)

router = APIRouter(tags=["templates"])

TemplateReaderDep = Annotated[WorkspaceContext, Depends(require("viewer", "agents:read"))]
#: Instantiating a tool template creates tool rows: the `POST /v1/tools` requirement.
ToolWriterDep = Annotated[WorkspaceContext, Depends(require("builder", "agents:write"))]
#: V6-18: a connected app's kit variant picks actions through the Apps adapter (tests override it).
AppsFactoryDep = Annotated[AdapterFactory, Depends(get_adapter_factory)]

load_catalog()
load_tool_templates()  # V5-25: a malformed tool template fails startup too
load_kits()  # V6-18: so does a malformed tool kit


#: ``(template id, pack id, PRICE_VERSION) -> estimate`` (D-V4-42: default assumptions, list prices).
_ESTIMATES: dict[tuple[str, str, str], TemplateEstimate | None] = {}


def template_estimate_for(template: StarterTemplate, manifest: PackManifest) -> TemplateEstimate | None:
    """The gallery pill: an estimate at list prices and default assumptions, cached per price version.

    ``None`` when nothing in the starter's pipeline is priced, or when its
    config cannot be seeded (an estimate never breaks the gallery).
    """
    key = (template.id, manifest.id, pricing.PRICE_VERSION)
    if key not in _ESTIMATES:
        try:
            config = seed_from_template(template, manifest, credentials_by_provider={})
            estimate = build_estimate(config, default_assumptions(config), table_quote)
            _ESTIMATES[key] = template_estimate(estimate)
        except (ValueError, KeyError) as exc:
            log.warning("template_estimate_failed", template_id=template.id, error=type(exc).__name__)
            _ESTIMATES[key] = None
    return _ESTIMATES[key]


def gallery(packs: list[str]) -> list[TemplateOut]:
    """The starters in gallery order for the installed ``packs`` (``LKAP_PACKS``)."""
    manifests = discover_manifests(packs)
    for template in missing_pack_templates(manifests):
        log.warning("template_pack_missing", template_id=template.id, pack_id=template.pack_id)
    return [
        TemplateOut(
            template=template,
            pack=manifest,
            derived=derived,
            estimate=template_estimate_for(template, manifest),
        )
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
                f"(LKAP_PACKS). Known templates: {', '.join(known)}",
                details={"template_id": template_id, "pack_id": template.pack_id, "known": known},
            )
    raise UnprocessableEntityError(
        f"unknown template '{template_id}'. Known templates: {', '.join(known)}",
        details={"template_id": template_id, "known": known},
    )


@router.get(
    "/v1/templates",
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
    "/v1/templates/{template_id}",
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


# ------------------------------------------------------------------ tool templates (V5-25)
@router.get(
    "/v1/tool-templates",
    response_model=ToolTemplatesResponse,
    tags=["tools"],
    summary="List tool templates",
    description=(
        "Ready-made HTTP tools grouped by service (the Cal.com booking set first). Each names the "
        "`http-tool-secret` names its key must hold and the arguments an admin may fix."
    ),
)
async def list_tool_templates(_ctx: TemplateReaderDep) -> ToolTemplatesResponse:
    """Return every tool template in gallery order."""
    return ToolTemplatesResponse(items=list(load_tool_templates()))


@router.post(
    "/v1/tool-templates/{template_id}/instantiate",
    response_model=ToolTemplateInstantiated,
    status_code=201,
    tags=["tools"],
    summary="Add tools from a template",
    description=(
        "Creates HTTP tools from one template (`cal_com.booking_create`) or every template of a group "
        "(`cal_com`, or the subset in `names`). `credential_id` is an `http-tool-secret` key holding the "
        "template's secret names; `defaults` fix arguments such as `event_type_id`. The tools are checked "
        "like `POST /v1/tools` (binding the key needs `providers:write`, R-V2-33) and are not attached to an "
        "agent (use the agent's `tools.tool_ids`)."
    ),
)
async def instantiate_tool_template(
    template_id: str,
    payload: ToolTemplateInstantiate,
    db: DbDep,
    vault: VaultDep,
    settings: SettingsDep,
    ctx: ToolWriterDep,
) -> ToolTemplateInstantiated:
    """Create the template's tool rows in the caller's workspace.

    Raises:
        NotFoundError: No template or group has this id.
        UnprocessableEntityError: ``names`` or ``defaults`` do not fit, a required default is
            missing, or a tool fails the ``POST /v1/tools`` checks (key kind, secret names).
    """
    # Deferred: the tools router imports the vault, the db models and the tool-provider package.
    from lkap_api.routers.tools import create_tool  # noqa: PLC0415

    single = tool_template(template_id)
    templates = [single] if single is not None else group_templates(template_id)
    if not templates:
        raise NotFoundError(f"unknown tool template '{template_id}'")
    if payload.names is not None:
        if single is not None:
            raise UnprocessableEntityError("names applies to a group of templates, not to one template")
        known = {t.definition.name for t in templates}
        unknown = sorted(set(payload.names) - known)
        if unknown:
            raise UnprocessableEntityError(
                f"unknown template name(s) {', '.join(unknown)}. Known: {', '.join(sorted(known))}",
                details={"unknown": unknown},
            )
        templates = [t for t in templates if t.definition.name in payload.names]
    accepted = {d.name for t in templates for d in t.defaults}
    extra = sorted(set(payload.defaults) - accepted)
    if extra:
        raise UnprocessableEntityError(
            f"default(s) {', '.join(extra)} are not arguments these templates take",
            details={"unknown_defaults": extra},
        )
    created = ToolTemplateInstantiated(tool_ids=[], names=[], template_ids=[])
    for template in templates:
        try:
            definition = instantiate_definition(
                template, payload.defaults, credential_id=payload.credential_id
            )
        except ToolTemplateError as exc:
            raise UnprocessableEntityError(str(exc), details={"template_id": template.id}) from exc
        tool = await create_tool(
            ToolCreate(
                agent_id=payload.agent_id,
                kind="http",
                name=definition.name,
                definition=definition,
                enabled=payload.enabled,
            ),
            db,
            vault,
            settings,
            ctx,
        )
        created.tool_ids.append(tool.id)
        created.names.append(tool.name)
        created.template_ids.append(template.id)
    log.info("tool_template_instantiated", template_id=template_id, tool_count=len(created.tool_ids))
    return created


# ------------------------------------------------------------------ tool kits (V6-18)
@router.get(
    "/v1/tool-kits",
    response_model=ToolKitsResponse,
    tags=["tools"],
    summary="List tool kits",
    description=(
        "Ready-made kits for common jobs (look a record up, open a case, take down details, verify "
        "the caller, send a payment link, hand over to the team, log the call, book appointments): "
        "each lists its variants (where its tools come from), the blocks, instructions, extraction "
        "fields, rules and flow steps it adds, and the settings it needs. Names are shown with the "
        "kit's default prefix."
    ),
)
async def list_tool_kits(_ctx: TemplateReaderDep) -> ToolKitsResponse:
    """Return every kit in gallery order."""
    return ToolKitsResponse(items=list(load_kits()))


@router.get(
    "/v1/tool-kits/{kit_id}",
    response_model=ToolKit,
    tags=["tools"],
    summary="Get a tool kit",
    description="One kit, shown with its default prefix and the settings' examples.",
)
async def get_tool_kit(kit_id: str, _ctx: TemplateReaderDep) -> ToolKit:
    """Return one kit.

    Raises:
        NotFoundError: No kit has this id.
    """
    kit = get_kit(kit_id)
    if kit is None:
        raise NotFoundError(f"unknown tool kit '{kit_id}'")
    return kit


@router.post(
    "/v1/tool-kits/{kit_id}/instantiate",
    response_model=ToolKitInstantiated,
    tags=["tools"],
    summary="Add a tool kit to an agent",
    description=(
        "Adds the kit's tools, panel blocks, instruction snippet (between `<!-- kit:<id>:<prefix> -->` "
        "markers), extraction fields, rules, optional flow steps (after `flow_anchor`) and test case "
        "to one agent in one new configuration version; `dry_run=true` only lists what it would add "
        "and validates the result. Adding a kit again with the same `block_prefix` adds nothing. No "
        "key is needed: without `credential_id` HTTP tools are stored without their key header. "
        "Binding a key needs `admin` and `providers:write`, as for `POST /v1/tools`; a connected "
        "app's variant needs them too (the Apps rule). A kit that would add a validation error is "
        "refused (422) and nothing is kept."
    ),
)
async def instantiate_tool_kit(
    kit_id: str,
    payload: ToolKitInstantiate,
    db: DbDep,
    vault: VaultDep,
    settings: SettingsDep,
    ctx: ToolWriterDep,
    factory: AppsFactoryDep,
) -> ToolKitInstantiated:
    """Add (or preview) a kit on one agent of the caller's workspace."""
    return await instantiate_kit(db, vault, settings, ctx, factory, kit_id, payload)
