"""Seeding an agent config from a starter template (docs/v4/TEMPLATES.md §4).

:func:`seed_from_template` is pure: it builds the *effective manifest* (the
pack manifest with the template's overlay fields), runs the one existing
seeding rule (:func:`~lkap_api.config_service.seed_config_from_manifest`,
unchanged), applies the template's extras and the capability gates of D-V4-8.
Knowledge seeds and tool rows need the database and the new agent row, so
``routers/agents.py`` runs them (:func:`apply_tool_seeds` creates the rows).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from lkap_contracts import providers as provider_registry
from lkap_contracts.agent_config import REQUIRED_SLOTS, AgentConfig, PipelineConfig, ProviderRef
from lkap_contracts.packs import PackManifest
from lkap_contracts.templates import RequiredKey, StarterTemplate
from sqlalchemy.ext.asyncio import AsyncSession

from lkap_api.config_service import ConnectionContext, seed_config_from_manifest
from lkap_api.db.models import Tool

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
