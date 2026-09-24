"""Starter templates (docs/v4/TEMPLATES.md §7, PLAN-V4 V4-01): the catalogue is tested, not trusted.

Structural checks derive every claim the gallery makes (required keys, vision
model, tool references, block configs, seed files) from the contracts, the
provider registry and the **real** installed packs; the conftest's fake
``insurance_claim`` manifest (a realtime pipeline) would derive a different
key list, so these tests build their own app over the real ``LKAP_PACKS``.
"""

from __future__ import annotations

import json
import sys
import types
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from lkap_contracts import providers as provider_registry
from lkap_contracts.agent_config import PipelineConfig, ProviderRef
from lkap_contracts.blocks import validate_panel_block_configs
from lkap_contracts.packs import PackManifest
from lkap_contracts.providers import vision_support
from lkap_contracts.templates import StarterTemplate
from lkap_contracts.tools import BLOCK_TOOL_NAMES, BUILTIN_TOOL_NAMES
from sqlalchemy import select, update

from lkap_api.config_service import host_allowed
from lkap_api.db.models import Agent, AgentConfigVersion, KnowledgeBase, LiveKitConnection, Tool
from lkap_api.db.session import Database
from lkap_api.flows import allowed_tool_names
from lkap_api.kb.embed import FakeEmbedder
from lkap_api.packs import clear_manifest_cache, discover_manifests
from lkap_api.settings import Settings
from lkap_api.templates.catalog import (
    CatalogError,
    clear_catalog_cache,
    derived_template,
    load_catalog,
    load_catalog_from,
    template_root,
)
from lkap_api.templates.seed import effective_manifest, requirements_from_pipeline, seed_from_template

#: Pinned: adding or removing a starter is a deliberate edit (R-V4-5).
EXPECTED_IDS = (
    "blank",
    "knowledge_assistant",
    "receptionist",
    "vision_assistant",
    "phone_agent",
    "lead_qualification",
    "survey_intake",
    "insurance_claim",
)

REAL_PACKS = "packs.insurance_claim,packs.generic"

#: Tool names a template may switch off: built-ins, block tools and the telephony tools.
DISABLEABLE_TOOLS = {*BUILTIN_TOOL_NAMES, *BLOCK_TOOL_NAMES, "send_dtmf", "transfer_call"}

_PIPELINE_SLOTS = ("realtime", "stt", "llm", "tts", "avatar", "image_gen", "workflow_llm")


def _real_manifests() -> dict[str, PackManifest]:
    clear_manifest_cache()
    return {manifest.id: manifest for manifest in discover_manifests(REAL_PACKS.split(","))}


MANIFESTS = _real_manifests()
CATALOG = {template.id: template for template in load_catalog()}


def _template(template_id: str) -> StarterTemplate:
    return CATALOG[template_id]


def _manifest_of(template: StarterTemplate) -> PackManifest:
    return MANIFESTS[template.pack_id]


def _pipeline_of(template: StarterTemplate) -> PipelineConfig:
    return template.pipeline or _manifest_of(template).recommended_pipeline


def _needs_a_required_key(template: StarterTemplate) -> bool:
    return any(not key.optional for key in template.requires.provider_keys)


# --------------------------------------------------------------------------- fixtures
@pytest.fixture
def real_packs(settings: Settings) -> Iterator[Settings]:
    """The real generic and insurance packs, not the conftest's fakes."""
    settings.packs = REAL_PACKS
    clear_manifest_cache()
    clear_catalog_cache()
    yield settings
    clear_manifest_cache()
    clear_catalog_cache()


async def _set_default_caps(database: Database, **caps: bool) -> None:
    async with database.session() as session:
        await session.execute(
            update(LiveKitConnection).where(LiveKitConnection.is_default.is_(True)).values(capabilities=caps)
        )


@pytest.fixture
async def templates_app(real_packs: Settings, database: Database, monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    """An app on the real packs, with a default connection reporting no SIP and no Egress."""
    from lkap_api.main import create_app

    async def _fake_embedder(*_args: object, **_kwargs: object) -> FakeEmbedder:
        return FakeEmbedder()

    monkeypatch.setattr("lkap_api.routers.agents.resolve_embedder", _fake_embedder)
    await _set_default_caps(database, sip_enabled=False, egress_enabled=False)
    application = create_app(real_packs)
    application.state.db = database
    return application


@pytest.fixture
async def admin(templates_app: FastAPI, real_packs: Settings) -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=templates_app),
        base_url="http://api.test",
        headers={"X-Admin-Token": real_packs.admin_token},
    ) as client:
        yield client


@pytest.fixture
async def anonymous(templates_app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=templates_app), base_url="http://api.test"
    ) as client:
        yield client


async def _create(admin: httpx.AsyncClient, template_id: str, **extra: Any) -> dict[str, Any]:
    response = await admin.post(
        "/v1/agents", json={"name": f"From {template_id}", "template_id": template_id, **extra}
    )
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


# --------------------------------------------------------------------------- catalogue structure
def test_the_catalogue_ids_are_pinned() -> None:
    assert tuple(CATALOG) == EXPECTED_IDS


@pytest.mark.parametrize("template_id", EXPECTED_IDS)
def test_template_json_round_trips_and_its_directory_is_its_id(template_id: str) -> None:
    template = _template(template_id)
    directory = template_root(template_id)
    raw = json.loads(directory.joinpath("template.json").read_text(encoding="utf-8"))

    assert StarterTemplate.model_validate(raw).id == directory.name == template_id
    assert (
        StarterTemplate.model_validate_json(template.model_dump_json()).model_dump() == template.model_dump()
    )
    if template.pack_id == "generic" and template_id != "blank":
        assert directory.joinpath("instructions.md").is_file()
        assert template.instructions


@pytest.mark.parametrize("template_id", EXPECTED_IDS)
def test_template_pack_is_installed_and_disabled_tools_are_real(template_id: str) -> None:
    template = _template(template_id)

    assert template.pack_id in MANIFESTS
    assert set(template.builtin_tools_disabled or []) <= DISABLEABLE_TOOLS


@pytest.mark.parametrize("template_id", EXPECTED_IDS)
def test_template_pipeline_names_registry_providers_and_models(template_id: str) -> None:
    pipeline = _pipeline_of(_template(template_id))

    for slot in _PIPELINE_SLOTS:
        ref = getattr(pipeline, slot)
        if not isinstance(ref, ProviderRef):
            continue
        spec = provider_registry.get(ref.provider_id)
        assert spec.kind == slot or (slot == "workflow_llm" and spec.kind == "llm")
        if ref.model is not None:
            assert ref.model in {model.id for model in spec.models}, f"{slot}: unknown model {ref.model}"


@pytest.mark.parametrize("template_id", EXPECTED_IDS)
def test_camera_or_screen_share_starters_use_a_vision_model(template_id: str) -> None:
    template = _template(template_id)
    capabilities = template.capabilities or _manifest_of(template).capabilities
    if not (capabilities.camera or capabilities.screen_share):
        pytest.skip("no camera or screen share")
    llm = _pipeline_of(template).llm

    assert llm is not None
    assert vision_support(llm.provider_id, llm.model) is True


@pytest.mark.parametrize("template_id", EXPECTED_IDS)
def test_kb_seed_files_exist_and_names_are_prefixed(template_id: str) -> None:
    template = _template(template_id)

    for seed in template.kb_seeds:
        assert seed.kb_name.startswith(f"{template.name} · ")
        for file_name in seed.files:
            assert template_root(template_id).joinpath("seeds", file_name).is_file()


@pytest.mark.parametrize("template_id", EXPECTED_IDS)
def test_tool_seeds_are_valid_unique_and_allowed_to_reach_their_host(template_id: str) -> None:
    template = _template(template_id)
    names = [seed.definition.name for seed in template.tool_seeds]

    assert len(names) == len(set(names))
    for seed in template.tool_seeds:
        definition = seed.definition
        assert definition.allowed_hosts
        assert host_allowed(definition.url, allowed_hosts=definition.allowed_hosts)
        assert all(host == "example.com" for host in definition.allowed_hosts)


@pytest.mark.parametrize("template_id", EXPECTED_IDS)
def test_flow_tool_references_resolve_for_the_seeded_config(template_id: str) -> None:
    template = _template(template_id)
    if template.flow is None:
        pytest.skip("prompt starter")
    manifest = _manifest_of(template)
    config = seed_from_template(template, manifest, credentials_by_provider={})
    tool_names_by_id = {f"seed-{i}": seed.definition.name for i, seed in enumerate(template.tool_seeds)}
    config.tools.tool_ids = list(tool_names_by_id)

    allowed = allowed_tool_names(
        config, tool_names_by_id=tool_names_by_id, pack_tool_names=manifest.tool_names
    )

    for node in template.flow.nodes:
        for name in getattr(node, "tools", []):
            assert name in allowed, f"node {node.id!r} references {name!r}, which the agent will not have"


def test_lead_qualification_starts_at_company_with_a_single_edge() -> None:
    """R-V4-30: the lead starter's start never routes; declining is handled from `company`."""
    flow = _template("lead_qualification").flow
    assert flow is not None

    from_start = [edge for edge in flow.edges if edge.source == "start"]
    assert [(edge.id, edge.target) for edge in from_start] == [("start_company", "company")]
    from_company = [(edge.id, edge.target) for edge in flow.edges if edge.source == "company"]
    # `company_needs` stays first: it is the `max_turns` fallback of `company`.
    assert from_company == [("company_needs", "needs"), ("company_nurture", "nurture")]


@pytest.mark.parametrize("template_id", EXPECTED_IDS)
def test_panel_block_configs_are_valid(template_id: str) -> None:
    template = _template(template_id)
    panel = effective_manifest(template, _manifest_of(template)).default_panel

    assert panel is not None
    assert validate_panel_block_configs(panel.blocks) == []


@pytest.mark.parametrize("template_id", EXPECTED_IDS)
def test_every_next_step_has_exactly_one_target(template_id: str) -> None:
    template = _template(template_id)

    assert template.next_steps
    for step in template.next_steps:
        assert (step.section is None) != (step.href is None), step


@pytest.mark.parametrize("template_id", EXPECTED_IDS)
def test_declared_keys_equal_the_keys_the_pipeline_needs(template_id: str) -> None:
    """The "Needs keys" badge can never lie (``purpose`` is prose, so it is not compared)."""
    template = _template(template_id)
    derived = requirements_from_pipeline(_pipeline_of(template))

    assert [(k.provider_id, k.optional) for k in template.requires.provider_keys] == [
        (k.provider_id, k.optional) for k in derived
    ]


def test_requirements_from_pipeline_marks_image_gen_optional_and_realtime_required() -> None:
    pipeline = PipelineConfig(
        mode="realtime",
        realtime=ProviderRef(provider_id="google-realtime"),
        image_gen=ProviderRef(provider_id="google-image-gen"),
        stt=ProviderRef(provider_id="deepgram-stt"),  # not used in realtime mode
    )

    keys = requirements_from_pipeline(pipeline)

    assert [(k.provider_id, k.optional) for k in keys] == [
        ("google-realtime", False),
        ("google-image-gen", True),
    ]


def test_requirements_from_pipeline_reports_one_shared_openrouter_key_under_its_home() -> None:
    """R-V4-7: three OpenRouter slots (and an optional OpenRouter image slot) need one key."""
    pipeline = PipelineConfig(
        mode="cascaded",
        stt=ProviderRef(provider_id="openrouter-stt"),
        llm=ProviderRef(provider_id="openrouter-llm"),
        tts=ProviderRef(provider_id="openrouter-tts"),
        image_gen=ProviderRef(provider_id="openrouter-image-gen"),
    )

    keys = requirements_from_pipeline(pipeline)

    assert [(k.provider_id, k.optional) for k in keys] == [("openrouter-llm", False)]


# --------------------------------------------------------------------------- loader rules
def _write_entry(
    root: Path, template_id: str, data: dict[str, Any], files: dict[str, str] | None = None
) -> None:
    directory = root / template_id
    directory.mkdir(parents=True)
    (directory / "template.json").write_text(json.dumps(data), encoding="utf-8")
    for name, text in (files or {}).items():
        path = directory / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def _minimal(template_id: str = "fixture", **extra: Any) -> dict[str, Any]:
    return {
        "id": template_id,
        "name": "Fixture",
        "tagline": "A loader fixture",
        "description": "A loader fixture.",
        "category": "blank",
        **extra,
    }


@pytest.mark.parametrize(
    ("extra", "files", "message"),
    [
        ({"voice": {"greeting": "Hi"}}, {}, "voice.greeting"),
        ({"knowledge": {"kb_ids": ["kb1"]}}, {}, "knowledge.kb_ids"),
        ({"id": "other"}, {}, "must equal the directory"),
        ({"instructions": "x"}, {"instructions.md": "y"}, "both template.json and instructions.md"),
        ({"kb_seeds": [{"kb_name": "Fixture · Docs", "files": ["missing.md"]}]}, {}, "seeds/missing.md"),
    ],
)
def test_a_malformed_catalogue_entry_is_an_error(
    tmp_path: Path, extra: dict[str, Any], files: dict[str, str], message: str
) -> None:
    _write_entry(tmp_path, "fixture", _minimal(**extra), files)

    with pytest.raises(CatalogError, match=message.replace(".", r"\.")):
        load_catalog_from(tmp_path)


def test_the_loader_folds_instructions_md_in_and_orders_by_order_then_id(tmp_path: Path) -> None:
    _write_entry(tmp_path, "zeta", _minimal("zeta", order=1))
    _write_entry(tmp_path, "beta", _minimal("beta", order=5), {"instructions.md": "  Be kind.\n"})
    _write_entry(tmp_path, "alpha", _minimal("alpha", order=5))

    loaded = load_catalog_from(tmp_path)

    assert [t.id for t in loaded] == ["zeta", "alpha", "beta"]
    assert loaded[2].instructions == "Be kind."


# --------------------------------------------------------------------------- seeding through the api
@pytest.mark.parametrize("template_id", EXPECTED_IDS)
async def test_every_starter_creates_validates_and_seeds_its_rows(
    admin: httpx.AsyncClient, database: Database, template_id: str
) -> None:
    template = _template(template_id)
    manifest = _manifest_of(template)

    agent = await _create(admin, template_id)

    assert agent["pack_id"] == template.pack_id
    config = agent["config"]
    if not _needs_a_required_key(template):
        validation = (await admin.post(f"/v1/agents/{agent['id']}/validate")).json()
        errors = [issue for issue in validation["issues"] if issue["severity"] == "error"]
        assert validation["ok"] is True and errors == [], validation
    async with database.session() as session:
        tools = (await session.execute(select(Tool).where(Tool.agent_id == agent["id"]))).scalars().all()
        kbs = (
            (
                await session.execute(
                    select(KnowledgeBase).where(KnowledgeBase.id.in_(config["knowledge"]["kb_ids"]))
                )
            )
            .scalars()
            .all()
        )
        [version] = (
            (
                await session.execute(
                    select(AgentConfigVersion).where(AgentConfigVersion.agent_id == agent["id"])
                )
            )
            .scalars()
            .all()
        )
    assert sorted(config["tools"]["tool_ids"]) == sorted(tool.id for tool in tools)
    assert len(tools) == len(template.tool_seeds)
    assert {tool.name for tool in tools} == {seed.definition.name for seed in template.tool_seeds}
    assert len(config["knowledge"]["kb_ids"]) == len(template.kb_seeds) + len(manifest.kb_seeds) == len(kbs)
    assert version.config_version == 1
    assert version.note == f"created from template {template_id}"
    assert agent["mode"] == ("flow" if template.flow is not None else "prompt")


async def test_a_failed_validation_rolls_back_the_agent_and_its_tool_rows(
    admin: httpx.AsyncClient, database: Database
) -> None:
    """One transaction (D-V4-3): a self-hosted connection has no LiveKit Inference, so a keyless
    receptionist is invalid there, and neither the agent nor its two seeded tools survive."""
    async with database.session() as session:
        await session.execute(
            update(LiveKitConnection)
            .where(LiveKitConnection.is_default.is_(True))
            .values(deployment_type="self_hosted")
        )

    response = await admin.post("/v1/agents", json={"name": "Front desk", "template_id": "receptionist"})

    assert response.status_code == 422, response.text
    assert any("LiveKit Inference" in error for error in response.json()["error"]["details"]["errors"])
    async with database.session() as session:
        assert (await session.execute(select(Agent))).scalars().all() == []
        assert (await session.execute(select(Tool))).scalars().all() == []


async def test_two_receptionists_in_one_workspace_have_disjoint_tool_rows(
    admin: httpx.AsyncClient, database: Database
) -> None:
    first = await _create(admin, "receptionist")
    second = await _create(admin, "receptionist")

    first_ids, second_ids = (
        set(first["config"]["tools"]["tool_ids"]),
        set(second["config"]["tools"]["tool_ids"]),
    )
    assert len(first_ids) == len(second_ids) == 2
    assert not first_ids & second_ids
    async with database.session() as session:
        rows = (
            (await session.execute(select(Tool).where(Tool.id.in_(first_ids | second_ids)))).scalars().all()
        )
    assert {row.id: row.agent_id for row in rows} == {
        **dict.fromkeys(first_ids, first["id"]),
        **dict.fromkeys(second_ids, second["id"]),
    }


@pytest.mark.parametrize("template_id", ["phone_agent", "survey_intake"])
async def test_the_voice_merge_keeps_the_overlay_greeting(admin: httpx.AsyncClient, template_id: str) -> None:
    template = _template(template_id)

    voice = (await _create(admin, template_id))["config"]["voice"]

    assert template.voice is not None
    assert voice["greeting"] == template.greeting
    assert voice["user_away_timeout_s"] == template.voice.user_away_timeout_s


async def test_phone_agent_dtmf_is_gated_to_a_sip_capable_connection(
    admin: httpx.AsyncClient, database: Database
) -> None:
    without_sip = await _create(admin, "phone_agent")
    await _set_default_caps(database, sip_enabled=True, egress_enabled=False)
    with_sip = await _create(admin, "phone_agent")

    assert without_sip["config"]["capabilities"]["dtmf"] is False
    assert with_sip["config"]["capabilities"]["dtmf"] is True


async def test_blank_equals_the_generic_pack_field_for_field(admin: httpx.AsyncClient) -> None:
    from_template = await _create(admin, "blank")
    response = await admin.post("/v1/agents", json={"name": "From the pack", "pack_id": "generic"})
    assert response.status_code == 201, response.text
    from_pack = response.json()

    assert from_template["config"] == from_pack["config"]
    assert (from_template["pack_id"], from_template["ui_panel_id"], from_template["mode"]) == (
        from_pack["pack_id"],
        from_pack["ui_panel_id"],
        from_pack["mode"],
    )


async def test_a_pack_only_create_is_the_derived_starter(
    admin: httpx.AsyncClient, database: Database
) -> None:
    response = await admin.post("/v1/agents", json={"name": "Plain", "pack_id": "generic"})
    assert response.status_code == 201, response.text

    async with database.session() as session:
        note = await session.scalar(
            select(AgentConfigVersion.note).where(AgentConfigVersion.agent_id == response.json()["id"])
        )
    assert note == "created from template pack:generic"


async def test_an_explicit_config_keeps_the_plain_created_note(
    admin: httpx.AsyncClient, database: Database
) -> None:
    config = (await _create(admin, "blank"))["config"]
    response = await admin.post("/v1/agents", json={"name": "Explicit", "config": config})
    assert response.status_code == 201, response.text

    async with database.session() as session:
        note = await session.scalar(
            select(AgentConfigVersion.note).where(AgentConfigVersion.agent_id == response.json()["id"])
        )
    assert note == "created"


async def test_an_mcp_shaped_body_takes_the_templates_pack(admin: httpx.AsyncClient) -> None:
    """The MCP tool always sends the default ``pack_id``; ``template_id`` wins (R-V4-3)."""
    agent = await _create(admin, "insurance_claim", pack_id="generic")

    assert agent["pack_id"] == "insurance_claim"
    assert agent["ui_panel_id"] == "insurance_notebook"


async def test_template_id_with_a_config_is_a_422(admin: httpx.AsyncClient) -> None:
    config = (await _create(admin, "blank"))["config"]

    response = await admin.post("/v1/agents", json={"name": "Both", "template_id": "blank", "config": config})

    assert response.status_code == 422
    assert "one of template_id, config" in response.json()["error"]["message"]
    assert response.json()["error"]["details"]["issues"][0]["path"] == "template_id"


async def test_an_unknown_template_id_is_a_422_naming_the_known_ids(admin: httpx.AsyncClient) -> None:
    response = await admin.post("/v1/agents", json={"name": "Nope", "template_id": "time_machine"})

    assert response.status_code == 422
    error = response.json()["error"]
    assert "unknown template 'time_machine'" in error["message"]
    assert error["details"]["known"] == list(EXPECTED_IDS)
    assert "receptionist" in error["message"]


# --------------------------------------------------------------------------- GET /v1/templates
FAKE_PACK = PackManifest(
    id="third_party",
    version="0.1.0",
    name="Third-party desk",
    description="A pack from somewhere else. It has one code tool and a camera.",
    ui_panel_id="generic",
    default_instructions="You help people.",
    default_greeting="Hello!",
    recommended_pipeline=PipelineConfig(
        mode="cascaded",
        stt=ProviderRef(provider_id="livekit-inference-stt"),
        llm=ProviderRef(provider_id="livekit-inference-llm", model="google/gemini-3.5-flash"),
        tts=ProviderRef(provider_id="livekit-inference-tts"),
    ),
    capabilities={"camera": True},
    tool_names=["lookup_order"],
    state_schema={"type": "object"},
)


@pytest.fixture
def third_party_pack(real_packs: Settings, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    module = types.ModuleType("third_party_pack.manifest")
    module.MANIFEST = FAKE_PACK  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "third_party_pack", types.ModuleType("third_party_pack"))
    monkeypatch.setitem(sys.modules, "third_party_pack.manifest", module)
    real_packs.packs = f"{REAL_PACKS},third_party_pack"
    clear_manifest_cache()
    yield
    clear_manifest_cache()


async def test_list_templates_is_the_catalogue_in_gallery_order(admin: httpx.AsyncClient) -> None:
    response = await admin.get("/v1/templates")

    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert [item["template"]["id"] for item in items] == list(EXPECTED_IDS)
    assert all(item["derived"] is False for item in items)
    assert items[0]["pack"]["id"] == "generic"
    receptionist = items[EXPECTED_IDS.index("receptionist")]["template"]
    assert receptionist["instructions"] and receptionist["voice"] == {"user_away_timeout_s": 20}


async def test_an_installed_pack_without_a_starter_gets_a_derived_entry(
    admin: httpx.AsyncClient, third_party_pack: None
) -> None:
    items = (await admin.get("/v1/templates")).json()["items"]

    assert [item["template"]["id"] for item in items] == [*EXPECTED_IDS, "pack:third_party"]
    derived = items[-1]
    assert derived["derived"] is True
    assert derived["template"]["category"] == "example"
    assert derived["template"]["chips"] == ["code_tools", "camera"]
    assert derived["template"]["requires"]["provider_keys"] == []
    assert derived["pack"]["id"] == "third_party"
    assert derived["template"] == json.loads(derived_template(FAKE_PACK).model_dump_json())


async def test_a_derived_template_id_creates_from_its_pack(
    admin: httpx.AsyncClient, third_party_pack: None
) -> None:
    agent = await _create(admin, "pack:third_party")

    assert agent["pack_id"] == "third_party"
    assert agent["config"]["instructions"] == "You help people."


async def test_a_starter_whose_pack_is_missing_is_omitted(
    admin: httpx.AsyncClient, real_packs: Settings
) -> None:
    real_packs.packs = "packs.generic"
    clear_manifest_cache()

    ids = [item["template"]["id"] for item in (await admin.get("/v1/templates")).json()["items"]]
    create = await admin.post("/v1/agents", json={"name": "Claims", "template_id": "insurance_claim"})

    assert ids == [i for i in EXPECTED_IDS if i != "insurance_claim"]
    assert create.status_code == 422
    assert "not installed" in create.json()["error"]["message"]


async def test_get_one_template(admin: httpx.AsyncClient) -> None:
    found = await admin.get("/v1/templates/blank")
    missing = await admin.get("/v1/templates/time_machine")

    assert found.status_code == 200
    assert found.json()["template"]["id"] == "blank" and found.json()["pack"]["id"] == "generic"
    assert missing.status_code == 404


@pytest.mark.parametrize("path", ["/v1/templates", "/v1/templates/blank"])
async def test_the_template_routes_need_a_signed_in_caller(anonymous: httpx.AsyncClient, path: str) -> None:
    response = await anonymous.get(path)

    assert response.status_code == 401
