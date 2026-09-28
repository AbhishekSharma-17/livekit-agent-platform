"""V6-22 (D-V6-21, D-V6-30): the insurance experience as the ``claims_intake`` starter, and the flip.

The starter is built from the generic pack, blocks and tool kits: the Notebook preset (its
notebook named for the intake kit), ``record_lookup`` on the seeded ``Demo — Policy directory``
lookup table, ``structured_intake`` with the claim fields and the rules ported from the legacy
pack's ``rules.py``, ``notify_escalate``, ``generate_image`` and ``pin_frame`` in the
instructions, two knowledge bases and the FNOL golden test cases.

Two layers prove the FNOL bar (D-V6-30). Here, the **plumbing**: the starter creates, validates,
seeds its table and kits, and its golden cases run through V5-29's runner offline (a scripted
worker behind the room, ``respx`` answering the persona and the five judges), with every D-V6-30
behaviour carried by an expectation the judges read or checked on the tool calls. The
**behaviour** itself (extraction into the notebook, the claim-type checklist, the caveat, the
escalation, the hand-off note, the sketch and pin tools) is driven through the worker's real
extraction and rules engines in ``agent/tests/unit/test_claims_intake_golden_v6_22.py``, from the
config fixture this module keeps in step (:func:`test_the_worker_fixture_is_the_seeded_config`).
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
import re
from collections.abc import AsyncIterator, Callable, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from fastapi import FastAPI
from lkap_contracts.agent_config import NOTEBOOK_PRESET, AgentConfig, ProviderRef
from lkap_contracts.datasets import DatasetLookupIn
from sqlalchemy import func, select

from lkap_api.agent_tests import runner
from lkap_api.agent_tests.runner import RunnerLimits
from lkap_api.agent_tests.service import tool_mocks_for_session
from lkap_api.agent_tests.transport import Reply
from lkap_api.datasets.service import lookup
from lkap_api.db.models import (
    Agent,
    AgentConfigVersion,
    AgentTestResult,
    Credential,
    Dataset,
    DatasetRow,
    KbDocument,
    LiveKitConnection,
    SessionEvent,
    Tool,
    WorkerInstance,
    utcnow,
)
from lkap_api.db.models import Session as SessionRow
from lkap_api.db.session import Database
from lkap_api.jobs.service import JobsService
from lkap_api.kb.embed import FakeEmbedder
from lkap_api.packs import clear_manifest_cache, discover_manifests
from lkap_api.settings import Settings
from lkap_api.templates.catalog import clear_catalog_cache, get_template, template_root
from lkap_api.vault import Vault

REPO = Path(__file__).resolve().parents[2]
WORKER_FIXTURE = REPO / "agent" / "tests" / "fixtures" / "claims_intake_config.json"
WEB_PRESET = REPO / "web" / "src" / "panels" / "notebook-preset.json"
LEGACY_PACKS = "packs.insurance_claim,packs.generic"
CHAT_URL = "https://api.openai.com/v1/chat/completions"
AGENT_IDENTITY = "agent-fake"
POLICY_TABLE = "Demo — Policy directory"
GOLDEN_CASES = ["fnol-golden", "fnol-safety", "fnol-evidence"]

#: D-V6-30's nine behaviours → where the golden cases carry each one (a judge reads the
#: expectation; a tool name is also checked on the recorded tool calls).
D_V6_30: dict[str, tuple[str, str]] = {
    "the safety question first": ("fnol-golden", "first message asks whether the caller and everyone"),
    "a policy found by number or name and read back": ("fnol-safety", "by the policyholder's name"),
    "the narrative and classification extracted": ("fnol-golden", "treats the loss as a home water damage"),
    "the document checklist driven by the claim type": ("fnol-golden", "asks about documents that fit it"),
    "a coverage caveat never confirmed": ("fnol-golden", "says it can't confirm coverage"),
    "a safety concern escalating": ("fnol-safety", "a person from the claims team will take over"),
    "a sketch generated on request": ("fnol-golden", "calls generate_image"),
    "the packet hand-off summary in the notebook": ("fnol-golden", "hand-off summary into the notebook"),
    "evidence photos pinned with notes": ("fnol-evidence", "pins the frame with pin_frame"),
}


# --------------------------------------------------------------------------- fixtures
def _use_packs(settings: Settings, packs: str | None) -> Settings:
    if packs is not None:
        settings.packs = packs
    clear_manifest_cache()
    clear_catalog_cache()
    return settings


@pytest.fixture
def default_packs(settings: Settings) -> Iterator[Settings]:
    """The api as a fresh install runs it: ``LKAP_PACKS`` unset (the real generic pack only)."""
    yield _use_packs(settings, None)
    clear_manifest_cache()
    clear_catalog_cache()


async def _app(settings: Settings, database: Database, monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    from lkap_api.main import create_app

    async def _fake_embedder(*_args: object, **_kwargs: object) -> FakeEmbedder:
        return FakeEmbedder()

    monkeypatch.setattr("lkap_api.routers.agents.resolve_embedder", _fake_embedder)
    monkeypatch.setattr("lkap_api.kb.ingest.resolve_embedder", _fake_embedder)
    application = create_app(settings)
    application.state.db = database
    return application


def _client(application: FastAPI, settings: Settings) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application),
        base_url="http://api.test",
        headers={"X-Admin-Token": settings.admin_token},
    )


@pytest.fixture
async def fresh_app(default_packs: Settings, database: Database, monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    return await _app(default_packs, database, monkeypatch)


@pytest.fixture
async def admin(fresh_app: FastAPI, default_packs: Settings) -> AsyncIterator[httpx.AsyncClient]:
    async with _client(fresh_app, default_packs) as client:
        yield client


async def _create_claims(admin: httpx.AsyncClient, name: str = "Demo — Claims intake") -> dict[str, Any]:
    response = await admin.post("/v1/agents", json={"name": name, "template_id": "claims_intake"})
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


# --------------------------------------------------------------------------- the default flip
def test_the_default_lkap_packs_is_the_generic_pack_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LKAP_PACKS", raising=False)
    fields = Settings.model_fields

    assert fields["packs"].default == "packs.generic"


async def test_default_lkap_packs_loads_only_generic_and_a_fresh_gallery_shows_the_claims_starter(
    admin: httpx.AsyncClient, default_packs: Settings
) -> None:
    packs = (await admin.get("/v1/packs")).json()
    templates = (await admin.get("/v1/templates")).json()

    assert default_packs.packs_list == ["packs.generic"]
    assert [pack["manifest"]["id"] for pack in packs["items"]] == ["generic"]
    ids = [item["template"]["id"] for item in templates["items"]]
    assert "claims_intake" in ids
    assert "insurance_claim" not in ids  # the legacy starter needs the legacy pack
    claims = next(item for item in templates["items"] if item["template"]["id"] == "claims_intake")
    assert claims["pack"]["id"] == "generic" and claims["derived"] is False


# --------------------------------------------------------------------------- the starter
async def test_claims_intake_creates_validates_and_seeds_its_table_kits_and_tests(
    admin: httpx.AsyncClient, database: Database
) -> None:
    agent = await _create_claims(admin)
    config = AgentConfig.model_validate(agent["config"])

    assert (agent["pack_id"], agent["ui_panel_id"]) == ("generic", "generic")
    validation = (await admin.post(f"/v1/agents/{agent['id']}/validate")).json()
    assert validation["ok"] is True, validation
    assert [issue for issue in validation["issues"] if issue["severity"] == "error"] == []
    # The Notebook preset (its notebook named for the intake kit) and what the kits add.
    assert [(block.id, str(block.type)) for block in config.panel.blocks] == [
        ("status", "status"),
        ("claim_notebook", "notebook"),
        ("sketch_board", "canvas"),
        ("gallery", "gallery"),
        ("policy_results", "table"),
        ("checklist", "checklist"),
        ("handoff", "handoff"),
    ]
    assert config.panel.layout == "wide"
    # The kits' instruction snippets, once each, after the starter's own instructions.
    for kit in ("record_lookup:policy", "structured_intake:claim", "notify_escalate:escalate"):
        assert config.instructions.count(f"<!-- kit:{kit} -->") == 1
    assert config.instructions.startswith("You are the live voice intake agent")
    # The starter's claim fields first, the kits' own after (a same-named field is kept).
    names = [field.name for field in config.extraction.fields]
    assert names[:4] == ["caller_name", "policy_number", "policy_status", "callback_number"]
    assert names[-2:] == ["preferred_time", "needs_person"]
    assert config.extraction.enabled and config.extraction.still_needed == "checklist"
    labels = {field.name: field.label for field in config.extraction.fields}
    assert labels["caller_name"] == "Policyholder"  # the starter's, not the kit's "Name"
    rule_ids = [rule.id for rule in config.rules]
    assert rule_ids[0] == "safety_first" and "packet_ready" in rule_ids
    assert rule_ids[-3:] == ["policy_found", "claim_complete", "escalate_hand_over"]
    assert [test.id for test in config.tests] == [
        *GOLDEN_CASES,
        "kit-structured_intake-claim",
        "kit-notify_escalate-escalate",
    ]
    async with database.session() as db:
        tools = (await db.execute(select(Tool).where(Tool.agent_id == agent["id"]))).scalars().all()
        table = await db.scalar(select(Dataset).where(Dataset.name == POLICY_TABLE))
        assert table is not None
        rows = await db.scalar(select(func.count(DatasetRow.id)).where(DatasetRow.dataset_id == table.id))
        [version] = (
            (await db.execute(select(AgentConfigVersion).where(AgentConfigVersion.agent_id == agent["id"])))
            .scalars()
            .all()
        )
        documents = (
            (await db.execute(select(KbDocument).where(KbDocument.kb_id.in_(config.knowledge.kb_ids))))
            .scalars()
            .all()
        )
    [tool] = tools
    assert (tool.name, tool.kind) == ("policy_lookup", "dataset")
    assert tool.definition["dataset_id"] == table.id
    assert tool.definition["key_columns"] == ["policy_number", "policyholder_name"]
    assert config.tools.tool_ids == [tool.id]
    assert (table.status, table.row_count, rows) == ("ready", 6, 6)
    assert version.config_version == 1 and version.note == "created from template claims_intake"
    assert sorted(document.filename for document in documents) == ["intake_playbook.md", "policy_lines.md"]
    # The golden cases' fixtures answer for the kit's lookup tool.
    golden = next(test for test in config.tests if test.id == "fnol-golden")
    assert golden.mocks["policy_lookup"][0]["policyholder_name"] == "Maya Singh"


async def test_creating_the_claims_starter_twice_keeps_one_policy_table(
    admin: httpx.AsyncClient, database: Database
) -> None:
    first = await _create_claims(admin, "Demo — Claims one")
    second = await _create_claims(admin, "Demo — Claims two")

    async with database.session() as db:
        tables = (await db.execute(select(Dataset).where(Dataset.name == POLICY_TABLE))).scalars().all()
        tools = (
            (await db.execute(select(Tool).where(Tool.agent_id.in_([first["id"], second["id"]]))))
            .scalars()
            .all()
        )
    [table] = tables
    assert len(tools) == 2  # agent-scoped rows, one per agent
    assert {tool.definition["dataset_id"] for tool in tools} == {table.id}


@pytest.mark.parametrize(
    ("keys", "holder"),
    [
        ({"policy_number": "H0-44721"}, "Maya Singh"),
        ({"policy_number": "  auto-90210 "}, "Jordan Lee"),
        ({"policyholder_name": "priya shah"}, "Priya Shah"),
        ({"policy_number": "AUTO-11111"}, "Chris Park"),
    ],
)
async def test_the_policy_table_finds_a_policy_by_number_or_by_name(
    admin: httpx.AsyncClient, database: Database, keys: dict[str, str], holder: str
) -> None:
    await _create_claims(admin)

    async with database.session() as db:
        table = await db.scalar(select(Dataset).where(Dataset.name == POLICY_TABLE))
        assert table is not None
        found = await lookup(db, table, DatasetLookupIn(keys=keys))

    [row] = found.rows
    assert row["policyholder_name"] == holder


def test_the_policy_table_file_holds_the_legacy_directory_rows() -> None:
    """The seeded rows are the legacy pack's ``POLICY_RECORDS``, flattened to text columns."""
    from packs.insurance_claim.policy_directory import POLICY_RECORDS  # noqa: PLC0415

    text = (
        template_root("claims_intake").joinpath("seeds", "policy_directory.csv").read_text(encoding="utf-8")
    )
    rows = list(csv.DictReader(io.StringIO(text)))

    assert [row["policy_number"] for row in rows] == [r["policy_number"] for r in POLICY_RECORDS.values()]
    for row, record in zip(rows, POLICY_RECORDS.values(), strict=True):
        assert row["policyholder_name"] == record["policyholder_name"]
        assert row["policy_line"] == record["policy_line"]
        assert row["status"] == record["status"]
        assert row["coverages"] == "; ".join(record["coverages"])
        assert row["notes"] == " ".join(record["notes"])


def test_the_document_rules_are_the_legacy_document_table() -> None:
    """``rules.py``'s ``TYPE_REQUIRED_DOCS``: each claim type's rule sets its documents, in order."""
    from packs.insurance_claim.rules import TYPE_REQUIRED_DOCS  # noqa: PLC0415

    template = get_template("claims_intake")
    assert template is not None and template.extraction is not None
    claim_type = next(field for field in template.extraction.fields if field.name == "claim_type")
    assert claim_type.options == list(TYPE_REQUIRED_DOCS)
    for kind, documents in TYPE_REQUIRED_DOCS.items():
        [rule] = [rule for rule in template.rules if rule.when == f'var.claim_type == "{kind}"']
        items = [(action.label, action.hint) for action in rule.then if action.do == "checklist.set_item"]
        assert items == documents
        caveat = [action.text for action in rule.then if action.do == "instruct"]
        assert len(caveat) == 1 and "I can't confirm coverage" in caveat[0]


def test_the_starter_carries_every_d_v6_30_behaviour() -> None:
    """Each behaviour has its expectation in a golden case, and its mechanism in the config."""
    template = get_template("claims_intake")
    assert template is not None and template.extraction is not None
    cases = {test.id: test for test in template.tests}
    for behaviour, (case_id, text) in D_V6_30.items():
        assert any(text in expectation for expectation in cases[case_id].expectations), behaviour
    assert template.greeting is not None and "safe" in template.greeting
    fields = {field.name: field for field in template.extraction.fields}
    # Within two turns: extraction runs after every caller turn, into the notebook's summary.
    assert [trigger.kind for trigger in template.extraction.triggers] == ["every_n_turns"]
    assert template.extraction.triggers[0].n == 1  # type: ignore[union-attr]
    for name in ("request_details", "claim_type"):
        assert fields[name].show_in == "notebook:claim_notebook.summary"
    whens = {rule.id: rule.when for rule in template.rules}
    assert whens["safety_first"] == "var.safety_concern == true"
    assert whens["coverage_caveat"] == "var.asked_about_coverage == true"
    assert template.capabilities is not None and template.capabilities.camera is True
    assert template.pipeline is not None and template.pipeline.image_gen is not None
    assert "generate_image" in (template.instructions or "")
    assert "pin_frame" in (template.instructions or "")
    assert "notebook_write" in (template.instructions or "")


def test_the_web_alias_preset_is_the_notebook_preset() -> None:
    """``web/src/panels/notebook-preset.json`` (the ``insurance_notebook`` alias) is ``NOTEBOOK_PRESET``."""
    assert json.loads(WEB_PRESET.read_text(encoding="utf-8")) == NOTEBOOK_PRESET.model_dump(mode="json")


# --------------------------------------------------------------------------- the legacy pack
async def test_an_insurance_pack_agent_with_its_notebook_panel_validates_unchanged_when_listed(
    settings: Settings, database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D-V6-31: listing the legacy pack explicitly keeps its agents exactly as they were."""
    _use_packs(settings, LEGACY_PACKS)
    try:
        application = await _app(settings, database, monkeypatch)
        async with _client(application, settings) as admin:
            response = await admin.post(
                "/v1/agents", json={"name": "Demo — Insurance", "pack_id": "insurance_claim"}
            )
            assert response.status_code == 201, response.text
            agent = response.json()
            validation = (await admin.post(f"/v1/agents/{agent['id']}/validate")).json()
            again = (await admin.get(f"/v1/agents/{agent['id']}")).json()
    finally:
        clear_manifest_cache()
        clear_catalog_cache()

    assert (agent["pack_id"], agent["ui_panel_id"]) == ("insurance_claim", "insurance_notebook")
    assert validation["ok"] is True, validation
    assert again["config"] == agent["config"] and again["config_version"] == 1
    manifest = {m.id: m for m in discover_manifests(LEGACY_PACKS.split(","))}["insurance_claim"]
    assert "legacy" in manifest.description.lower()


# --------------------------------------------------------------------------- the worker fixture
def _normalised(config: dict[str, Any], ids: dict[str, str]) -> dict[str, Any]:
    text = json.dumps(config, sort_keys=True, ensure_ascii=False)
    for real, placeholder in ids.items():
        text = text.replace(real, placeholder)
    loaded: dict[str, Any] = json.loads(text)
    return loaded


async def test_the_worker_fixture_is_the_seeded_config(admin: httpx.AsyncClient, database: Database) -> None:
    """``agent/tests/fixtures/claims_intake_config.json`` is what the starter seeds (ids normalised).

    The worker's golden test drives its real engines from that file; this keeps the two in step.
    Regenerate it by running this test with ``LKAP_WRITE_FIXTURES=1``.
    """
    agent = await _create_claims(admin)
    async with database.session() as db:
        [tool] = (await db.execute(select(Tool).where(Tool.agent_id == agent["id"]))).scalars().all()
    config = dict(agent["config"])
    ids = {tool.id: "tool-policy-lookup", tool.definition["dataset_id"]: "0" * 32}
    ids.update({kb_id: f"kb-{index}" for index, kb_id in enumerate(config["knowledge"]["kb_ids"])})
    expected = {
        "config": _normalised(config, ids),
        "tools": [_normalised(tool.definition, ids)],
    }
    import os  # noqa: PLC0415

    if os.environ.get("LKAP_WRITE_FIXTURES") == "1":
        WORKER_FIXTURE.write_text(json.dumps(expected, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    assert json.loads(WORKER_FIXTURE.read_text(encoding="utf-8")) == expected


# --------------------------------------------------------------------------- the golden chat (V5-29 runner)
class ScriptedClaimsWorker:
    """Stands in for the room and the worker behind it, answering each golden case from a script.

    Like the worker it reads the case's tool mocks through ``tool_mocks_for_session`` and posts
    the tool-call events, so the policy read back comes from the starter's own fixture row.
    """

    def __init__(self, database: Database, greeting: str) -> None:
        self.database = database
        self.greeting = greeting
        self.queue: asyncio.Queue[Reply | None] = asyncio.Queue()
        self.session_id = ""
        self.case_id = ""
        self.mocks: dict[str, Any] = {}
        self.turn = 0

    async def connect(self, server_url: str, token: str) -> None:
        async with self.database.session() as db:
            result = await db.scalar(select(AgentTestResult).where(AgentTestResult.status == "running"))
            assert result is not None and result.session_id is not None
            session = await db.scalar(select(SessionRow).where(SessionRow.id == result.session_id))
            assert session is not None
            self.session_id, self.case_id = session.id, result.case_id
            self.mocks = await tool_mocks_for_session(db, session)

    async def wait_for_agent(self, timeout_s: float) -> str:
        self.queue.put_nowait(Reply(text=self.greeting, final=True, participant=AGENT_IDENTITY))
        return AGENT_IDENTITY

    async def send_text(self, text: str) -> None:
        self.turn += 1
        reply, calls = self._script()
        for index, (tool, args, result) in enumerate(calls):
            call_id = f"{self.case_id}-{self.turn}-{index}"
            await self._events(
                ("tool_call_started", {"call_id": call_id, "tool": tool, "args_redacted": args}),
                (
                    "tool_call_ended",
                    {"call_id": call_id, "tool": tool, "status": "completed", "result_preview": result},
                ),
            )
        self.queue.put_nowait(Reply(text=reply, final=True, participant=AGENT_IDENTITY))

    def _script(self) -> tuple[str, list[tuple[str, dict[str, Any], str]]]:
        policy = (self.mocks.get("policy_lookup") or [{}])[0]
        lookup_call = ("policy_lookup", {"policy_number": "…"}, json.dumps(self.mocks.get("policy_lookup")))
        read_back = (
            f"I found the {policy.get('policy_line', '?')} policy for {policy.get('policyholder_name', '?')}."
        )
        match (self.case_id, self.turn):
            case ("fnol-golden", 1):
                return f"Let me check that policy. {read_back} What happened?", [lookup_call]
            case ("fnol-golden", 2):
                return (
                    "I'm sorry. I can't confirm coverage: an adjuster reviews the policy and the cause. "
                    "For a water damage claim, do you have photos from before the cleanup?",
                    [],
                )
            case ("fnol-golden", 3):
                sketch = (
                    "generate_image",
                    {"prompt": "A quick hand-drawn pen sketch … the kitchen sink"},
                    "shown",
                )
                return "Here is a quick sketch of the kitchen. Does it look right?", [sketch]
            case ("fnol-golden", _):
                note = ("notebook_write", {"section_id": "notes"}, "written")
                return "A burst pipe soaked your kitchen; photos help. An adjuster will be in touch.", [note]
            case ("fnol-safety", 1):
                escalate = ("escalate_to_human", {"reason": "The passenger is hurt."}, "escalated")
                return (
                    "Please contact emergency services first if anyone is in danger. A person from the "
                    f"claims team will take over now. {read_back}",
                    [lookup_call, escalate],
                )
            case ("fnol-evidence", _):
                pin = ("pin_frame", {"caption": "Swollen floorboards by the sink"}, "pinned")
                return "I can see swollen floorboards by the sink; I've pinned that photo.", [pin]
        return "Thank you.", []

    async def replies(self) -> AsyncIterator[Reply]:
        while True:
            item = await self.queue.get()
            if item is None:
                return
            yield item

    async def close(self) -> None:
        self.queue.put_nowait(None)
        if self.session_id:
            await self._events(("session_ended", {"reason": "participant_left"}))

    async def _events(self, *events: tuple[str, dict[str, Any]]) -> None:
        async with self.database.session() as db:
            for kind, payload in events:
                db.add(SessionEvent(session_id=self.session_id, ts=utcnow(), type=kind, payload=payload))


_PERSONA_LINES: dict[str, list[str]] = {
    "Maya Singh, a homeowner": [
        "Yes, we're safe. A pipe burst under my kitchen sink last night. Policy H0-44721.",
        "It soaked the floor. Is this covered?",
        "Can you sketch the kitchen for the adjuster?",
        "Yes, that's right. Thanks.",
    ],
    "You are Jordan Lee": ["Another car hit mine and my passenger's neck hurts. I'm Jordan Lee."],
    "camera on": ["I'm showing you the floor by the sink now."],
}


def _llm_router(judge_prompts: list[tuple[str, str]]) -> Callable[[httpx.Request], httpx.Response]:
    """The persona answers from ``_PERSONA_LINES``; every judge passes and its prompt is kept."""
    counters: dict[str, int] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        system, user = body["messages"][0]["content"], body["messages"][1]["content"]
        if "playing the CALLER" in system:
            key = next(marker for marker in _PERSONA_LINES if marker in system)
            index = counters.get(key, 0)
            counters[key] = index + 1
            lines = _PERSONA_LINES[key]
            content = json.dumps(
                {"message": lines[min(index, len(lines) - 1)], "done": index >= len(lines) - 1}
            )
        else:
            judge_prompts.append((system, user))
            content = json.dumps({"verdict": "pass", "score": 1, "reason": "fine"})
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    return handler


async def test_the_fnol_golden_chat_passes_offline_with_every_d_v6_30_behaviour_asserted(
    admin: httpx.AsyncClient,
    fresh_app: FastAPI,
    database: Database,
    default_packs: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        runner,
        "LIMITS",
        RunnerLimits(
            agent_join_timeout_s=0.3,
            greeting_timeout_s=0.3,
            reply_timeout_s=0.5,
            settle_s=0.05,
            events_wait_s=0.5,
            events_poll_s=0.05,
        ),
    )
    agent = await _create_claims(admin)
    async with database.session() as db:
        judge_key = Credential(
            provider_id="openai-llm",
            label="judge key",
            ciphertext=Vault(default_packs.master_key).encrypt({"api_key": "sk-test-placeholder"}),
            fingerprint="…test",
        )
        db.add(judge_key)
        connection = await db.scalar(select(LiveKitConnection).where(LiveKitConnection.is_default.is_(True)))
        assert connection is not None
        db.add(WorkerInstance(connection_id=connection.id, instance_key=f"w-{connection.id}", status="ready"))
        await db.flush()
        judge_id = judge_key.id
    # The persona and the judges need an OpenAI-compatible model with a key (V5-29).
    judge = ProviderRef(provider_id="openai-llm", credential_id=judge_id).model_dump()
    config = dict(agent["config"])
    config["qa"] = {**config["qa"], "model": judge}
    config["pipeline"] = {**config["pipeline"], "workflow_llm": judge}
    updated = await admin.put(f"/v1/agents/{agent['id']}", json={"config": config})
    assert updated.status_code == 200, updated.text
    greeting = updated.json()["config"]["voice"]["greeting"]
    workers: list[ScriptedClaimsWorker] = []

    def factory() -> ScriptedClaimsWorker:
        workers.append(ScriptedClaimsWorker(database, greeting))
        return workers[-1]

    monkeypatch.setattr(runner, "transport_factory", factory)
    judge_prompts: list[tuple[str, str]] = []
    async with httpx.AsyncClient(timeout=runner.LLM_HTTP_TIMEOUT_S) as http:
        fresh_app.state.jobs = JobsService(
            database=database, settings=default_packs, vault=Vault(default_packs.master_key), http_client=http
        )
        with respx.mock:
            respx.post(CHAT_URL).mock(side_effect=_llm_router(judge_prompts))
            started = await admin.post(f"/v1/agents/{agent['id']}/tests/run", json={"case_ids": GOLDEN_CASES})
        fresh_app.state.jobs = None
    assert started.status_code == 202, started.text
    run = (await admin.get(f"/v1/agents/{agent['id']}/tests/runs/{started.json()['id']}")).json()

    assert run["status"] == "passed", run
    verdicts = {verdict["case_id"]: verdict for verdict in run["verdicts"]}
    assert list(verdicts) == GOLDEN_CASES
    # The safety question first: the greeting opens every case.
    for verdict in verdicts.values():
        assert verdict["transcript"][0] == {"role": "assistant", "text": greeting}
        assert "safe place" in greeting
    # Tool-call checks: the policy read back from the starter's own fixture row, the sketch,
    # the hand-off note, the escalation and the pinned photo.
    golden_calls = verdicts["fnol-golden"]["tool_calls"]
    assert [call["tool"] for call in golden_calls] == ["policy_lookup", "generate_image", "notebook_write"]
    assert golden_calls[0]["mocked"] is True and "Maya Singh" in golden_calls[0]["result_preview"]
    assert "homeowners" in verdicts["fnol-golden"]["transcript"][2]["text"].lower()
    assert [call["tool"] for call in verdicts["fnol-safety"]["tool_calls"]] == [
        "policy_lookup",
        "escalate_to_human",
    ]
    assert "Jordan Lee" in verdicts["fnol-safety"]["tool_calls"][0]["result_preview"]
    assert [call["tool"] for call in verdicts["fnol-evidence"]["tool_calls"]] == ["pin_frame"]
    # Judge checks: every D-V6-30 expectation reached the accuracy and task-completion judges.
    for behaviour, (_case_id, text) in D_V6_30.items():
        for marker in ("Judge ACCURACY", "Judge TASK COMPLETION"):
            assert any(marker in system and text in user for system, user in judge_prompts), (
                behaviour,
                marker,
            )
    assert len(judge_prompts) == 5 * len(GOLDEN_CASES)
    assert all(re.search(r"<untrusted source=\"transcript\">", user) for _system, user in judge_prompts)
    async with database.session() as db:
        stored = await db.scalar(select(Agent).where(Agent.id == agent["id"]))
    assert stored is not None and stored.config_version == 2  # the judge model, nothing else
