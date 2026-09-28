"""Seeding an agent config from a starter template (docs/v4/TEMPLATES.md §4).

:func:`seed_from_template` is pure: it builds the *effective manifest* (the
pack manifest with the template's overlay fields), runs the one existing
seeding rule (:func:`~lkap_api.config_service.seed_config_from_manifest`,
unchanged), applies the template's extras and the capability gates of D-V4-8.
Knowledge seeds and tool rows need the database and the new agent row, so
``routers/agents.py`` runs them (:func:`apply_tool_seeds` creates the rows).

V6-22 (D-V6-21, ask #105): a starter may also set live extraction, rules and test
cases (applied here, before any kit, so a kit keeps a same-named field or rule the
starter declares), seed lookup tables from its ``seeds/`` directory
(:func:`seed_datasets`: reused by name, else read like an upload and stored ready in the
creation's own transaction) and add tool kits (:func:`apply_template_rows`, through
``kit_apply.add_kit_to_new_agent``: the kit endpoint's plan, tool checks and config
changes, without a version of its own).
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from lkap_contracts import providers as provider_registry
from lkap_contracts.agent_config import REQUIRED_SLOTS, AgentConfig, PipelineConfig, ProviderRef
from lkap_contracts.datasets import DatasetKeyColumn
from lkap_contracts.kits import ToolKitInstantiate
from lkap_contracts.packs import PackManifest
from lkap_contracts.templates import DatasetSeed, RequiredKey, StarterTemplate
from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from lkap_api.config_service import ConnectionContext, seed_config_from_manifest
from lkap_api.db.models import Agent, Dataset, DatasetKey, DatasetRow, Tool, new_id
from lkap_api.errors import UnprocessableEntityError
from lkap_api.logging import get_logger
from lkap_api.storage.base import StorageBackend

if TYPE_CHECKING:
    from lkap_api.auth.deps import WorkspaceContext
    from lkap_api.settings import Settings
    from lkap_api.vault import Vault

log = get_logger(__name__)

#: Slots whose provider seeding drops (rather than substitutes) without a key, so their key is optional.
OPTIONAL_KEY_SLOTS: tuple[str, ...] = ("avatar", "image_gen")

#: Slots outside the mode's required ones that still take a provider.
_EXTRA_SLOTS: tuple[str, ...] = ("avatar", "image_gen", "workflow_llm")


def requirements_from_pipeline(pipeline: PipelineConfig) -> list[RequiredKey]:
    """The vendor keys a recommended pipeline uses (TEMPLATES §4).

    Every provider reference of the slots the pipeline's mode runs (plus
    ``avatar``, ``image_gen`` and ``workflow_llm``) whose registry spec needs a
    credential, in slot order, once per credential home (R-V4-7: providers that
    share a key, like the OpenRouter entries, report it once, under the home's
    id). ``avatar``/``image_gen`` keys
    are ``optional`` (seeding drops those slots without a key instead of
    substituting LiveKit Inference). ``purpose`` is left empty: it is prose the
    catalogue writes, not something a pipeline says.

    Args:
        pipeline: A template's ``pipeline`` or the pack's ``recommended_pipeline``.

    Returns:
        The required keys; empty for a LiveKit Inference-only pipeline.
    """
    keys: dict[str, RequiredKey] = {}
    for slot in (*REQUIRED_SLOTS[pipeline.mode], *_EXTRA_SLOTS):
        ref = getattr(pipeline, slot)
        if not isinstance(ref, ProviderRef):
            continue
        try:
            spec = provider_registry.get(ref.provider_id)
        except KeyError:
            continue
        if not spec.requires_credential:
            continue
        optional = slot in OPTIONAL_KEY_SLOTS
        # R-V4-7: providers sharing a credential home need one key, reported
        # under the home (three OpenRouter slots → one `openrouter-llm` key).
        home = provider_registry.credential_home(spec)
        existing = keys.get(home)
        if existing is None:
            keys[home] = RequiredKey(provider_id=home, optional=optional)
        elif existing.optional and not optional:
            keys[home] = RequiredKey(provider_id=home, optional=False)
    return list(keys.values())


def effective_manifest(template: StarterTemplate, manifest: PackManifest) -> PackManifest:
    """The pack manifest with the template's overlay fields (TEMPLATES §4 step 1).

    Only the fields the template sets are replaced; ``default_voice`` is merged.
    The pack's id, tool names, state schema, panel id and mode addenda are
    untouched (the worker reads those from the real pack).
    """
    update: dict[str, Any] = {}
    if template.instructions is not None:
        update["default_instructions"] = template.instructions
    if template.greeting is not None:
        update["default_greeting"] = template.greeting
    if template.pipeline is not None:
        update["recommended_pipeline"] = template.pipeline.model_copy(deep=True)
    if template.capabilities is not None:
        update["capabilities"] = template.capabilities.model_copy(deep=True)
    if template.builtin_tools_disabled is not None:
        update["builtin_tools_disabled"] = list(template.builtin_tools_disabled)
    if template.panel is not None:
        update["default_panel"] = template.panel.model_copy(deep=True)
    if template.default_voice:
        update["default_voice"] = {**manifest.default_voice, **template.default_voice}
    return manifest.model_copy(update=update) if update else manifest


def apply_gates(config: AgentConfig, connection: ConnectionContext | None) -> None:
    """Switch off what the connection cannot run, so a starter always creates (D-V4-8, R-V4-4).

    ``capabilities.dtmf`` stays on only when the connection reports SIP;
    ``recording.enabled`` only with Egress and a storage config. Without a
    connection both gates close. Transfer targets pass through untouched.
    """
    caps = connection.capabilities if connection is not None else None
    if config.capabilities.dtmf and not (caps is not None and caps.sip_enabled):
        config.capabilities.dtmf = False
    if config.recording.enabled and not (
        caps is not None and caps.egress_enabled and config.recording.storage_config_id is not None
    ):
        config.recording.enabled = False


def seed_from_template(
    template: StarterTemplate,
    manifest: PackManifest,
    *,
    credentials_by_provider: Mapping[str, list[str]],
    connection: ConnectionContext | None = None,
) -> AgentConfig:
    """Build the config a starter seeds, before knowledge and tool rows (TEMPLATES §4 steps 1–3, 5).

    Args:
        template: The starter (a catalogue entry or a pack's derived one).
        manifest: The manifest of ``template.pack_id``.
        credentials_by_provider: ``{provider_id: [credential_id, ...]}`` of the workspace.
        connection: The connection the new agent will be bound to.

    Returns:
        A complete configuration; ``knowledge.kb_ids`` and ``tools.tool_ids`` are
        filled by the caller once the rows exist.
    """
    effective = effective_manifest(template, manifest)
    config = seed_config_from_manifest(
        effective, credentials_by_provider=credentials_by_provider, connection=connection
    )
    if template.panel is not None and effective.default_panel is not None:
        # Blocks must be stored so the block-tool gating (`allowed_tool_names`) sees them.
        config.panel = effective.default_panel.model_copy(deep=True)
    if template.voice is not None:
        config.voice = config.voice.model_copy(update=template.voice.model_dump(exclude_unset=True))
    config.tools.http_request_enabled = template.http_request_enabled
    if template.max_tool_steps is not None:
        config.tools.max_tool_steps = template.max_tool_steps
    if template.knowledge is not None:
        config.knowledge = config.knowledge.model_copy(
            update=template.knowledge.model_dump(exclude_unset=True)
        )
    if template.flow is not None:
        config.flow = template.flow.model_copy(deep=True)
    if template.qa is not None:
        config.qa = template.qa.model_copy(deep=True)
    if template.telephony is not None:
        config.telephony = template.telephony.model_copy(deep=True)
    if template.recording is not None:
        config.recording = template.recording.model_copy(deep=True)
    if template.pack_settings:
        config.pack_settings = {**config.pack_settings, **template.pack_settings}
    if template.timezone is not None:
        config.timezone = template.timezone
    # V6-22: before any kit, so a kit keeps the starter's field or rule of the same name.
    if template.extraction is not None:
        config.extraction = template.extraction.model_copy(deep=True)
    if template.rules:
        config.rules = [rule.model_copy(deep=True) for rule in template.rules]
    if template.tests:
        config.tests = [test.model_copy(deep=True) for test in template.tests]
    apply_gates(config, connection)
    return config


async def apply_tool_seeds(
    db: AsyncSession, template: StarterTemplate, *, workspace_id: str, agent_id: str
) -> list[str]:
    """Create the template's HTTP tool rows for a new agent (TEMPLATES §4 step 6).

    The rows are agent-scoped, so creating the same starter twice yields
    independent rows. Not committed here (the caller owns the transaction).

    Args:
        db: The agent-creation session.
        template: The starter.
        workspace_id: The agent's workspace.
        agent_id: The new agent's id (the row must be flushed already).

    Returns:
        The new tool ids, in ``tool_seeds`` order.
    """
    ids: list[str] = []
    for seed in template.tool_seeds:
        definition = seed.definition
        row = Tool(
            workspace_id=workspace_id,
            agent_id=agent_id,
            kind=definition.kind,
            name=definition.name,
            definition=definition.model_dump(mode="json"),
            enabled=seed.enabled,
        )
        db.add(row)
        await db.flush()
        ids.append(row.id)
    return ids


# ------------------------------------------------------------------ V6-22: lookup tables and kits
def _key_names(columns: list[Any]) -> list[str]:
    return [str(column.get("name")) for column in columns if isinstance(column, Mapping)]


async def _existing_dataset(db: AsyncSession, workspace_id: str, seed: DatasetSeed) -> Dataset | None:
    """A table of the seed's name in the workspace that did not fail and keys on the same columns."""
    rows = (
        await db.execute(
            select(Dataset)
            .where(
                Dataset.workspace_id == workspace_id,
                Dataset.name == seed.name,
                Dataset.status != "failed",
            )
            .order_by(Dataset.created_at)
        )
    ).scalars()
    wanted = [column.name for column in seed.key_columns]
    return next((row for row in rows.all() if _key_names(row.key_columns or []) == wanted), None)


async def _create_seed_dataset(
    db: AsyncSession, storage: StorageBackend, seed: DatasetSeed, *, workspace_id: str, template_id: str
) -> Dataset:
    """Read ``seeds/<file>`` like an upload and store the table and its rows ready (no job)."""
    from lkap_api.datasets.normalise import normalise_key  # noqa: PLC0415 - keeps the import light
    from lkap_api.datasets.parse import dataset_format, parse_dataset  # noqa: PLC0415
    from lkap_api.datasets.service import _unique_slug, check_quota, storage_key_for  # noqa: PLC0415
    from lkap_api.templates.catalog import template_root  # noqa: PLC0415 - catalog imports this module

    source = template_root(template_id).joinpath("seeds", seed.file)
    if not source.is_file():
        raise UnprocessableEntityError(
            f"the starter '{template_id}' is missing its lookup table file 'seeds/{seed.file}'",
            details={"template_id": template_id, "file": seed.file},
        )
    data = source.read_bytes()
    key_spec = {column.name: column.type for column in seed.key_columns}
    parsed = parse_dataset(data, dataset_format(seed.file), key_spec)
    await check_quota(db, workspace_id, len(parsed.rows))
    dataset_id = new_id()
    storage_key = storage_key_for(workspace_id, dataset_id, seed.file)
    content_type = "application/json" if parsed.format == "json" else "text/csv"
    await storage.put(storage_key, data, content_type=content_type)
    row = Dataset(
        id=dataset_id,
        workspace_id=workspace_id,
        name=seed.name,
        slug=await _unique_slug(db, workspace_id, seed.name),
        format=parsed.format,
        columns=[column.model_dump(mode="json") for column in parsed.columns],
        key_columns=[
            DatasetKeyColumn(name=column.name, type=column.type).model_dump(mode="json")
            for column in parsed.key_columns
        ],
        row_count=len(parsed.rows),
        storage_key=storage_key,
        sha256=hashlib.sha256(data).hexdigest(),
        status="ready",
    )
    db.add(row)
    await db.flush()
    keys = [(column.name, column.type) for column in parsed.key_columns]
    row_values: list[dict[str, Any]] = []
    key_values: list[dict[str, Any]] = []
    # The same rows and keys the import job writes (`datasets.service.import_rows`), in one step.
    for ordinal, cells in enumerate(parsed.rows):
        row_id = new_id()
        normalised = {name: normalise_key(cells.get(name), kind) for name, kind in keys}
        row_values.append(
            {"id": row_id, "dataset_id": dataset_id, "ordinal": ordinal, "keys": normalised, "row": cells}
        )
        key_values.extend(
            {"row_id": row_id, "column_name": name, "dataset_id": dataset_id, "value": value}
            for name, value in normalised.items()
            if value is not None
        )
    await db.execute(insert(DatasetRow), row_values)
    if key_values:
        await db.execute(insert(DatasetKey), key_values)
    return row


async def seed_datasets(
    db: AsyncSession, storage: StorageBackend, template: StarterTemplate, *, workspace_id: str
) -> dict[str, Dataset]:
    """The starter's lookup tables, by seed name: reused by name, else created ready (V6-22).

    Like the knowledge seeds, a table of the same name in the workspace that did not fail and
    keys on the same columns is reused, so creating the starter twice keeps one table.

    Args:
        db: The agent-creation session (not committed here).
        storage: Where the seed file's bytes are kept, like an upload's.
        template: The starter.
        workspace_id: The new agent's workspace.

    Returns:
        ``{seed name: dataset row}``.

    Raises:
        UnprocessableEntityError: A seed file is missing or does not read as a lookup table.
        DatasetQuotaExceededError: The workspace has no room for another table.
    """
    out: dict[str, Dataset] = {}
    for seed in template.dataset_seeds:
        existing = await _existing_dataset(db, workspace_id, seed)
        if existing is None:
            existing = await _create_seed_dataset(
                db, storage, seed, workspace_id=workspace_id, template_id=template.id
            )
            log.info(
                "dataset_seeded", dataset_id=existing.id, template_id=template.id, rows=existing.row_count
            )
        out[seed.name] = existing
    return out


async def apply_template_rows(
    db: AsyncSession,
    *,
    vault: Vault,
    app_settings: Settings,
    ctx: WorkspaceContext,
    storage: StorageBackend,
    template: StarterTemplate,
    agent: Agent,
    config: AgentConfig,
) -> AgentConfig:
    """Create a new agent's rows from its starter: HTTP tools, lookup tables, then kits (V6-22).

    TEMPLATES §4 step 6: the HTTP tool seeds (:func:`apply_tool_seeds`), then the lookup
    tables (:func:`seed_datasets`), then each kit in order, added the way
    ``POST /v1/tool-kits/{id}/instantiate`` adds it (its tools created through the tool
    checks), without a configuration version of its own. The caller validates the result.

    Args:
        db: The agent-creation session (the agent row is flushed).
        vault: The vault (the tool checks).
        app_settings: The api settings (the network guard).
        ctx: The creating admin's workspace context.
        storage: Where seed files are kept.
        template: The starter.
        agent: The new agent row.
        config: The seeded configuration.

    Returns:
        The configuration with every tool attached and every kit added.
    """
    from lkap_api.templates.kit_apply import add_kit_to_new_agent  # noqa: PLC0415 - it imports the routers

    if template.tool_seeds:
        tool_ids = await apply_tool_seeds(db, template, workspace_id=ctx.workspace_id, agent_id=agent.id)
        config.tools.tool_ids = [*config.tools.tool_ids, *tool_ids]
    datasets = await seed_datasets(db, storage, template, workspace_id=ctx.workspace_id)
    for kit in template.kits:
        dataset = datasets[kit.dataset] if kit.dataset is not None else None
        payload = ToolKitInstantiate(
            agent_id=agent.id,
            variant=kit.variant,
            block_prefix=kit.block_prefix,
            settings=dict(kit.settings),
            dataset_id=dataset.id if dataset is not None else None,
            key_columns=list(kit.key_columns) if kit.key_columns is not None else None,
            add_test_case=kit.add_test_case,
        )
        config, notes = await add_kit_to_new_agent(
            db, vault, app_settings, ctx, kit.kit_id, payload, agent, config
        )
        if notes:
            log.info("template_kit_notes", template_id=template.id, kit_id=kit.kit_id, notes=len(notes))
    return config
