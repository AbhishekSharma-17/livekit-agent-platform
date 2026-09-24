# LKAP v4 — Starter templates

Status: **decided** (Fable 5.1, 2026-09-24). Design plus the v1 catalogue. Packages and rulings are in `PLAN-V4.md`.

The user's words: "When I create a new agent it asks for a blank agent or the insurance claim agent. Insurance was just one use case we built; it shouldn't be the starter pack. We can offer different starters: blank, and others with particular capabilities."

Today `POST /v1/agents` seeds a config from a **pack manifest** (`lkap_contracts.packs.PackManifest`, `config_service.seed_config_from_manifest`) and the console's `/console/agents/new` page lists one card per installed pack (`GET /v1/packs`). Two packs exist: `generic` (shown as "Blank agent") and `insurance_claim`. A pack is a Python package with code tools, hooks and a custom React panel; that is the wrong unit for "a starter with particular capabilities", because almost every capability the platform has (knowledge, HTTP tools, forms, tables, flows, camera, screen share, telephony, DTMF, QA, webhooks) is **configuration**, not code.

## 1. Decisions

IDs `D-V4-n`. Each: what we do, why, what was rejected.

### D-V4-1 — A template is data layered on a pack; code packs stay for behaviour that needs code
- A **starter template** is a JSON document (`StarterTemplate`, §2) that names a pack (`pack_id`, default `generic`) and overlays configuration on it: instructions, greeting, pipeline mode and models, capabilities, built-in tool gating, panel blocks, voice settings, knowledge seeds, HTTP tool seeds, a flow, QA, telephony, recording. Everything a template sets is a field of `AgentConfig` or a row the api already knows how to create (knowledge bases, tool rows).
- Seeding **reuses** `seed_config_from_manifest`: the api builds an *effective manifest* (`manifest.model_copy(update=…)` with the template's overlay fields) and runs the one existing fallback rule (Inference substitution, `realtime`→`cascaded` without a key, `image_gen`/`avatar` dropped without a key). There is one seeding rule in the codebase, not two.
- Code packs remain for behaviour that needs Python: the insurance pack's policy lookup, claim packet sync, evidence pinning and sketch drawing, its `InsuranceState` and custom notebook panel. A code pack reaches the gallery **through a template** (`insurance_claim` in the catalogue, `category: example`), never as a bare pack card.
- Rejected: new code packs per starter (each would be a Python package with `manifest.py`/`pack.py`, a worker restart to install, and `LKAP_PACKS` churn, for zero behaviour); a "template = full `AgentConfig` JSON" (it would freeze provider ids and credential ids, bypass the Inference fallback and break the moment the registry default changes).

### D-V4-2 — The catalogue is JSON files in the api package; the contract model lives in `lkap_contracts`
- Model: `contracts/src/lkap_contracts/templates.py` (`StarterTemplate` and its parts, §2), exported like every other model (`EXPORTED_MODELS` in `export.py` → `generated/schemas/StarterTemplate.schema.json` → `web/src/contracts/lkap-contracts.d.ts` → `mcp/src/lkap_mcp/generated/`). `TemplateOut`, `TemplatesResponse` and the `AgentCreate.template_id` field go in `api_models.py`.
- Data: `api/src/lkap_api/templates/catalog/<id>/template.json`, a sibling `instructions.md` (long prose stays out of JSON string escapes), and `seeds/*.md` for knowledge seeds. The loader (`lkap_api/templates/catalog.py`) reads every directory with `importlib.resources`, merges `instructions.md` into `instructions`, validates with `StarterTemplate.model_validate`, and caches (`lru_cache`, cleared by tests like `packs.clear_manifest_cache`). A malformed template is a startup error, not a skipped entry: the catalogue ships with the api and is tested (§7); unlike packs there is no "being developed elsewhere" case.
- JSON, not YAML: no new dependency, and everything else the repo ships as data is JSON (`tools.snap.json`, `generated/*.json`). The MCP reads templates **live** from the api (`lkap://templates` → `GET /v1/templates`) exactly as `lkap://packs` does, so no export-script copy is needed.
- Rejected: templates in `contracts/` (a models package should not ship markdown seeds and prompts); templates in `packs/` (the worker never reads them); a `templates` DB table with a console editor (Phase 2, see §9 "Save as template").

### D-V4-3 — `POST /v1/agents` takes `template_id`; `pack_id` alone keeps today's behaviour
- `AgentCreate.template_id: str | None = None`. With `template_id`, the pack is the template's `pack_id` and the request's `pack_id` is **ignored** (it defaults to `"generic"` and the MCP tool always sends it, so a "differs → 422" rule would reject `agent_create(template_id="insurance_claim")` out of the box); `template_id` together with `config` → 422 ("send one of template_id, config"); an unknown `template_id` → 422 naming the known ids. Without `template_id`, `pack_id` (default `generic`) seeds from the manifest as today — which is by definition the pack's **derived template** (D-V4-4), so the two paths converge on one function.
- `GET /v1/templates` (admin, like `/v1/packs`) → `TemplatesResponse{items: TemplateOut[]}` in gallery order; `GET /v1/templates/{id}` → `TemplateOut`. `TemplateOut` is the merged `StarterTemplate` plus `pack: PackManifest` (so the console shows the pack's tool names and panel for code packs without a second request) and `derived: bool`.
- No new column and no migration (HANDOFF rule 4): the agent row does not remember its template. The first config version's `note` is `created from template <id>` (today: `created`), the `agent_created` log line carries `template_id`, and the console carries the id in its post-create navigation. A `template_id` column is Phase 2 if analytics ask for it.
- `create_agent` is **resequenced** because tool rows are `agent_id`-scoped (`routers/tools.py`): seed config → create the `Agent` row → `flush` → create the template's tool rows (`Tool(agent_id=row.id, …)`) → set `config.tools.tool_ids` → `validate_stored_config` → `_raise_if_invalid` → snapshot version 1. One transaction; a validation failure rolls the row and its tools back as today.

### D-V4-4 — Installed packs without a template get a derived one, so third-party packs still appear
- `GET /v1/templates` appends, after the catalogue, one derived entry per installed pack that no catalogue template references: `id = "pack:<pack_id>"`, `name`/`tagline`/`description` from the manifest, `category: example`, `derived: true`, chips computed from the manifest (`code_tools` if `tool_names`, `knowledge_seeds` if `kb_seeds`, `camera`/`screen_share`/`dtmf` from `capabilities`, `image_gen` if `recommended_pipeline.image_gen`), `requires.provider_keys` computed from the recommended pipeline (§4). Everything else is `None` — the manifest is the whole overlay.
- A catalogue template whose `pack_id` is not installed is **omitted** from the response (with a `template_pack_missing` warning log), like a pack that fails to import. `insurance_claim` therefore disappears from the gallery when `LKAP_PACKS` drops the pack.

### D-V4-5 — The "New agent" flow is a dialog; `/console/agents/new` becomes a deep link that opens it
- `UI_UX_SPEC.md` §4.2 said "a page, not a modal". R-V3-2 (dialogs only, no drawers or sheets) does not forbid a page, but the user asked for the New agent **dialog**, and a gallery with a preview pane is a modal task with one outcome. Ruling **R-V4-2** amends §4.2: the flow is a `Dialog` (`size="xl"`, the width `version-history.tsx` already uses) opened from every "New agent" trigger; `/console/agents/new` renders the agents list with the dialog open so bookmarks, the overview quick action and the breadcrumb trail keep working. Design in §6.

### D-V4-6 — Validity is a test, and the test derives, never trusts
- `api/tests/test_templates.py` (§7) loads the catalogue, pins the id list, validates every template against the contracts and the registry, seeds every template on a scratch workspace with no vendor keys and asserts the result validates with zero errors for every template that declares no required key, and checks that the **declared** `requires.provider_keys` equals the list **derived** from the seeded pipeline. The MCP's doc lint (`test_docs_lint.py`) resolves every template id a recipe mentions against `GET /v1/templates` on the in-process scratch api.

### D-V4-7 — The MCP exposes templates as a live resource, a describe kind, a tool argument and a recipe
- `lkap://templates` (live, like `lkap://packs`), `lkap_describe(kind="template", id=…)`, `agent_create(template_id=…)` (with `pack_id` kept, described as the fallback for a pack without a template), a new recipe `start-from-template`, and the two existing agent recipes switched to `template_id` (`generic-assistant` → `blank`, `insurance-intake-agent` → `insurance_claim`). `guide.md` and `concepts/agents.md` explain templates versus packs in one paragraph each. §8.

### D-V4-8 — Capability gates during seeding, so a starter never fails to create
- Some template settings are errors on a workspace that lacks the infrastructure: `capabilities.dtmf` is an **error** when the connection has no reachable SIP (`config_service.py`, "DTMF needs SIP"); a `TransferTarget` must pass the workspace dialing policy (R-V2-23). The seeder therefore applies gates the same way `seed_config_from_manifest` drops `image_gen` without a key: `dtmf` is set only when the connection's capabilities report `sip_enabled`; `recording.enabled` only when `egress_enabled` and a storage config exists; templates ship **no** transfer targets (the `transfer_call` tool registers only when `telephony.transfer_targets` is non-empty, R-V2-21), the next step tells the user to add one. The console tells the user what was gated by comparing the template's declared overlay with the created config (§6.4); the api response shape does not change.

## 2. Contracts

`contracts/src/lkap_contracts/templates.py` (new; imports `agent_config`, `flow`, `packs`, `telephony`, `tools` — nothing imports it back, so no cycle):

```python
"""Starter templates: data-only presets layered on a pack (docs/v4/TEMPLATES.md)."""

from typing import Any, Literal

from pydantic import BaseModel, Field

from lkap_contracts.agent_config import (
    CapabilitiesConfig, KnowledgeConfig, PanelLayout, PipelineConfig,
    QaConfig, RecordingConfig, TelephonyConfig, VoiceConfig,
)
from lkap_contracts.flow import FlowSpec
from lkap_contracts.packs import KbSeed
from lkap_contracts.tools import HttpToolDefinition

#: Template ids are slugs; derived ones are ``pack:<pack_id>``.
TEMPLATE_ID_PATTERN = r"^(pack:)?[a-z][a-z0-9_]{0,31}$"

TemplateCategory = Literal["blank", "support", "scheduling", "vision", "phone", "sales", "forms", "example"]

#: The closed chip vocabulary the gallery renders (label + icon per chip in the console).
TemplateChip = Literal[
    "rag", "citations", "http_tools", "forms", "table", "flow", "variables",
    "camera", "screen_share", "gallery", "telephony", "dtmf", "transfer",
    "webhook", "qa", "code_tools", "knowledge_seeds", "image_gen",
]

#: Editor sections a next step can deep-link to (``builtin-sections.tsx`` ids).
EditorSection = Literal["providers", "instructions", "flow", "panel", "tools", "knowledge", "recording", "limits"]


class RequiredKey(BaseModel):
    """A vendor key the template uses; ``optional`` keys only unlock an extra."""

    provider_id: str
    optional: bool = False
    purpose: str = ""


class TemplateRequirements(BaseModel):
    """What the workspace needs before the starter does everything it promises."""

    provider_keys: list[RequiredKey] = []
    telephony: bool = False          # a trunk, a number and a dispatch rule
    webhook_endpoint: bool = False   # a webhook endpoint subscribed to the named events
    storage: bool = False            # a storage config, for recording


class ToolSeed(BaseModel):
    """An HTTP tool row created for the new agent (``Tool(agent_id=…)``)."""

    definition: HttpToolDefinition
    enabled: bool = True


class NextStep(BaseModel):
    """One line of the post-create checklist; ``section`` deep-links into the editor, ``href`` elsewhere."""

    label: str = Field(max_length=120)
    section: EditorSection | None = None
    href: str | None = None


class StarterTemplate(BaseModel):
    """One starter: gallery metadata, the overlay on the pack manifest, and extras on the seeded config."""

    v: Literal[1] = 1
    id: str = Field(pattern=TEMPLATE_ID_PATTERN)
    name: str = Field(max_length=48)
    tagline: str = Field(max_length=90)
    description: str
    category: TemplateCategory
    chips: list[TemplateChip] = []
    requires: TemplateRequirements = TemplateRequirements()
    order: int = 100
    pack_id: str = "generic"

    # --- overlay on PackManifest (None = keep the pack's value) ---------------
    instructions: str | None = None
    greeting: str | None = None
    pipeline: PipelineConfig | None = None
    capabilities: CapabilitiesConfig | None = None
    builtin_tools_disabled: list[str] | None = None
    default_voice: dict[str, str] = {}
    panel: PanelLayout | None = None

    # --- extras applied to the seeded AgentConfig -----------------------------
    voice: VoiceConfig | None = None           # merged with exclude_unset; a validator rejects "greeting" in model_fields_set
    http_request_enabled: bool = False
    max_tool_steps: int | None = None
    tool_seeds: list[ToolSeed] = []
    kb_seeds: list[KbSeed] = []
    knowledge: KnowledgeConfig | None = None   # merged with exclude_unset; a validator rejects "kb_ids" in model_fields_set
    flow: FlowSpec | None = None
    qa: QaConfig | None = None
    telephony: TelephonyConfig | None = None
    recording: RecordingConfig | None = None
    pack_settings: dict[str, Any] = {}
    timezone: str | None = None

    # --- gallery ----------------------------------------------------------------
    sample_prompts: list[str] = []
    next_steps: list[NextStep] = []
```

`api_models.py` additions:

```python
class AgentCreate(BaseModel):
    name: str
    description: str = ""
    pack_id: str = "generic"
    template_id: str | None = None      # v4: wins over pack_id; 422 with config
    ui_panel_id: str | None = None
    config: AgentConfig | None = None
    connection_id: str | None = None
    mode: Literal["prompt", "flow"] = "prompt"


class TemplateOut(BaseModel):
    """One starter, merged (instructions.md folded in) plus the pack it layers on."""

    template: StarterTemplate
    pack: PackManifest
    derived: bool = False


class TemplatesResponse(BaseModel):
    """``GET /v1/templates``: catalogue order, then derived pack entries."""

    items: list[TemplateOut]
```

Export: `StarterTemplate`, `TemplateOut`, `TemplatesResponse` join `EXPORTED_MODELS`; the contracts gate (`PLAN-V3` definitions) regenerates the `.d.ts` and the MCP's copies.

## 3. Catalogue layout and loading

```
api/src/lkap_api/templates/
  __init__.py
  catalog.py        # load_catalog(), get_template(id), clear_catalog_cache(), derived_template(manifest)
  seed.py           # seed_from_template(...) → AgentConfig; apply_tool_seeds(...); requirements_from_pipeline(...)
  router.py         # GET /v1/templates, GET /v1/templates/{id}
  catalog/
    blank/template.json
    knowledge_assistant/{template.json, instructions.md, seeds/product_faq.md, seeds/support_playbook.md}
    receptionist/{template.json, instructions.md, seeds/practice_info.md}
    vision_assistant/{template.json, instructions.md}
    phone_agent/{template.json, instructions.md, seeds/phone_faq.md}
    lead_qualification/{template.json, instructions.md, seeds/offer_sheet.md}
    survey_intake/{template.json, instructions.md}
    insurance_claim/template.json
```

Loader rules:
- `template.json` must validate as `StarterTemplate`; its `id` must equal the directory name.
- If `instructions.md` exists, its text (stripped) becomes `instructions`; `template.json` may not set both (loader error).
- `kb_seeds[].files` resolve under `<dir>/seeds/`; a missing file is a loader error (packs log and skip; templates are ours and tested).
- Templates are ordered by `order`, then `id`. `blank` is `order: 0` and always first.
- `GET /v1/templates` filters by installed packs (D-V4-4) and appends derived entries.

## 4. Seeding algorithm (`lkap_api/templates/seed.py`)

```
seed_from_template(template, manifest, *, credentials_by_provider, connection) -> AgentConfig
```

1. **Effective manifest.** `manifest.model_copy(update=overlay)` where `overlay` contains, for each of `default_instructions ← instructions`, `default_greeting ← greeting`, `recommended_pipeline ← pipeline`, `capabilities ← capabilities`, `builtin_tools_disabled ← builtin_tools_disabled`, `default_panel ← panel`, `default_voice ← default_voice` (merged), only the fields the template sets. The pack's `id`, `tool_names`, `state_schema`, `ui_panel_id` and `instructions_by_mode` are untouched (the worker reads mode addenda from the real pack manifest, so a template cannot carry them; a template that needs a cascaded/realtime addendum writes it into `instructions`).
2. **Base config.** `config = seed_config_from_manifest(effective, credentials_by_provider=…, connection=…)` — unchanged function, so the Inference fallback, the realtime→cascaded downgrade, the `image_gen`/`avatar` drop and the default voices behave exactly as for packs. `config.panel` is set from `effective.default_panel` when the template set `panel` (the manifest path leaves `panel` at its default and lets `effective_layout` resolve it; templates that compose blocks need the blocks **stored**, so the block-tool gating in `allowed_tool_names` sees them).
3. **Extras.** In this order: `voice` **merged**, never replaced — `config.voice = config.voice.model_copy(update=template.voice.model_dump(exclude_unset=True))`, so the greeting written by step 2 survives a template that only sets `user_away_timeout_s` (the model validator rejects a template whose `voice` sets `greeting`; `greeting` is the overlay field); `tools.http_request_enabled`; `tools.max_tool_steps`; `knowledge` merged the same way (`auto_inject`, `top_k`; `kb_ids` rejected by the validator and filled by step 4); `flow`; `qa`; `telephony`; `recording`; `pack_settings`; `timezone`.
4. **Knowledge seeds.** `kb/seed.py` is generalised: `import_kb_seeds(db, store, embedder, *, root: Traversable, source_label, seeds, workspace_id)` reads files under `root/"seeds"`; `import_pack_kb_seeds` becomes a thin wrapper that resolves the pack's `importlib.resources.files(module_path)`; templates pass their catalogue directory. Reuse-by-name semantics are unchanged, which is why every template KB name is **prefixed with the template name** ("Knowledge assistant · Product FAQ") so two starters never merge into one KB.
5. **Gates (D-V4-8).** `capabilities.dtmf = template.dtmf and connection.capabilities.sip_enabled`; `recording.enabled = template.recording.enabled and connection.capabilities.egress_enabled and recording.storage_config_id is not None` (`ConnectionContext.capabilities` is the same `ConnectionCapabilities` the validator reads, so the seeder sees exactly what validation would reject; without a connection both gates close); `telephony.transfer_targets` pass through untouched (the catalogue ships none; a user-authored Phase 2 template would be validated at save).
6. **Tool seeds** happen in `create_agent` after the row exists (D-V4-3): each `ToolSeed.definition` becomes `Tool(workspace_id, agent_id=row.id, kind="http", name=definition.name, definition, enabled)`; the ids are appended to `config.tools.tool_ids`; the flow (if any) may already reference the tools by `name`, which `allowed_tool_names` resolves through `tool_names_by_id` (every workspace tool row, enabled or not). The `tools` table has no uniqueness constraint on `name` (`db/models.py`), and the rows are agent-scoped, so creating the same starter twice in a workspace yields two independent `check_availability` rows — the intended behaviour.
7. **Validate** with `validate_stored_config(pack_id=template.pack_id)`; errors → 422 exactly as today.

`requirements_from_pipeline(pipeline) -> list[RequiredKey]`: every `ProviderRef` in the recommended pipeline whose `ProviderSpec` needs a credential (`realtime`, `stt`, `llm`, `tts`, `avatar`, `image_gen`, `workflow_llm`), with `optional=True` for `avatar`/`image_gen` (the slots seeding drops rather than substitutes). The catalogue test asserts `template.requires.provider_keys == requirements_from_pipeline(template.pipeline or manifest.recommended_pipeline)` so the "Needs keys" badge can never lie.

## 5. The v1 catalogue

Eight starters. "Keys" = vendor keys beyond the LiveKit connection; every template but one runs on LiveKit Inference alone (`livekit-inference-stt` deepgram/nova-3, `livekit-inference-llm`, `livekit-inference-tts`), which is the platform's story, not a gap. All generic-pack templates use the composite panel; blocks are chosen so every block has a writer (`status`←`set_status`, `notes`←`push_note`, `activity`←automatic, `form`←`request_form`, `table`←`table_append`, `gallery`←`pin_frame`, `kb_citations`←`search_knowledge` and auto-inject, `transcript`←automatic). `checklist` is written only by pack code, so no generic template ships it except `blank` (whose four default blocks are what the v1→v2 migration writes); `custom{kind: flow_progress}` has no console renderer today, so no template ships it.

| # | id | Name | Category | Pack | Mode | Chips | Needs |
|---|---|---|---|---|---|---|---|
| 1 | `blank` | Blank agent | blank | generic | prompt | — | nothing |
| 2 | `knowledge_assistant` | Knowledge assistant | support | generic | prompt | rag, citations | nothing |
| 3 | `receptionist` | Receptionist | scheduling | generic | flow | flow, variables, forms, table, http_tools | nothing (tool URLs to edit) |
| 4 | `vision_assistant` | Vision assistant | vision | generic | prompt | camera, screen_share, gallery | nothing |
| 5 | `phone_agent` | Phone agent | phone | generic | prompt | telephony, dtmf, transfer, qa | a number + dispatch rule |
| 6 | `lead_qualification` | Lead qualification | sales | generic | flow | flow, variables, webhook, qa, rag | a webhook endpoint (optional) |
| 7 | `survey_intake` | Survey / intake form | forms | generic | prompt | forms, table | nothing |
| 8 | `insurance_claim` | Insurance claim intake | example | insurance_claim | cascaded (pack) | code_tools, knowledge_seeds, camera, image_gen | Google key (optional, sketches) |

Common to every generic-pack template unless stated: `pipeline` = cascaded LiveKit Inference (`stt` deepgram/nova-3, `llm` the registry default `google/gemma-4-31b-it`, `tts` inworld/inworld-tts-2 — written with the provider ids and the model ids the registry carries, checked by the test), `voice.language "en"`, `first_speaker "agent"`, `greeting_mode "say"`, `knowledge.auto_inject true`, `top_k 4`, `max_tool_steps 3`, `timezone "UTC"`, `qa` off, `recording` off, `telephony` empty, `http_request_enabled false` (the raw `http_request` built-in stays off; templates use declared tool rows).

### 5.1 `blank` — Blank agent
- **Story.** Exactly what `pack_id: generic` seeds today: a plain voice assistant on Inference, the four default blocks, no tools beyond the built-ins. The safety net and the fastest path to "make a test call".
- **Config.** `pack_id: generic`; every overlay field `None`; `chips: []`; `order: 0`. `next_steps`: "Write the instructions" (instructions), "Check providers" (providers), "Make a test call" (via the editor's test-call menu; `section: providers` with the label pointing at the header action).
- **Acceptance detail.** The config seeded from `template_id: "blank"` equals the config seeded from `pack_id: "generic"` field for field.

### 5.2 `knowledge_assistant` — Knowledge assistant (support with RAG)
- **Story.** Answers from the documents you upload, cites what it used, admits what it does not know, offers a human handoff. The RAG starter.
- **Overlay.** `instructions.md`: answer only from retrieved passages, quote the source title, say "I don't have that in my notes" instead of guessing, offer `escalate_to_human` when the caller asks for a person or the answer is not in the sources, keep answers to two or three sentences, call `set_status` with `researching`/`answered`. `greeting`: "Hi, I'm the support assistant. Ask me anything about the product and I'll answer from our documentation." `panel`: `status`, `kb_citations` ("Sources"), `notes` ("Follow-ups"), `activity`. `builtin_tools_disabled`: `["pin_frame", "describe_current_frame"]` (no vision).
- **Extras.** `kb_seeds`: `Knowledge assistant · Product FAQ` ← `seeds/product_faq.md` (a fictional product "Acme Meter": plans, limits, refund policy, support hours — ~40 Q&A lines); `Knowledge assistant · Support playbook` ← `seeds/support_playbook.md` (tone, escalation rules, what never to promise). `knowledge.top_k 4`, `auto_inject true`.
- **Gallery.** `sample_prompts`: "What's your refund policy?", "Which plan includes API access?", "I need to talk to a person." `next_steps`: "Replace the sample FAQ with your documents" (knowledge), "Tune the instructions" (instructions), "Make a test call".
- **Needs.** Nothing: `fastembed-embedding` is the seed embedder and Inference runs the pipeline.

### 5.3 `receptionist` — Receptionist / appointment booking
- **Story.** Greets, identifies the caller, collects a booking with a form, checks availability and books through two HTTP tools (placeholders you point at your system), logs the booking in a table, confirms and ends. The flow + forms + HTTP-tools starter.
- **Overlay.** `panel`: `status`, `form` ("Booking details"), `table` ("Bookings", columns `name`, `phone`, `service`, `time`), `notes`, `activity`. `builtin_tools_disabled`: `["pin_frame", "describe_current_frame"]`. `greeting` unset (the start node carries it; in flow mode the start node's greeting replaces `voice.greeting`).
- **Flow** (`FlowSpec v1`, node ids are tool-name safe):
  - `start` (kind `start`, greeting "Thanks for calling Acme Dental. I can book, move or cancel an appointment — how can I help?")
  - `global` (kind `global`): instructions "You are the receptionist of Acme Dental. Be brief and warm. Use `search_knowledge` for opening hours, location and services. If the caller asks for a person, call `escalate_to_human`. Never invent availability: use the tools."; `tools: ["search_knowledge", "escalate_to_human", "current_time", "push_note"]`.
  - `identify` (agent): "Get the caller's full name and phone number; confirm the phone number back digit by digit."; `extract: ["caller_name", "phone"]`.
  - `collect_booking` (agent): "Ask which service they need and when they would like to come in. Then call `request_form` with fields service (enum: cleaning, check-up, filling, consultation), preferred_date (date), preferred_time (string) so the caller can confirm the details on screen."; `tools: ["request_form", "current_time"]`; `extract: ["service", "preferred_time"]`.
  - `book` (agent): "Call `check_availability` with the requested slot. If free, call `book_appointment`, then `table_append` one row to the Bookings table and read the confirmation back. If not free, offer the two nearest alternatives the tool returned."; `tools: ["check_availability", "book_appointment", "table_append", "set_status"]`.
  - `done` (end): farewell "You're booked. We'll text a reminder the day before. Goodbye!", `disposition: "booked"`.
  - `no_booking` (end): farewell "No problem — call back any time and we'll find a slot. Goodbye!", `disposition: "not_booked"`.
  - Edges: `start→identify` ("the caller wants to book, move or cancel"), `identify→collect_booking` ("name and phone are confirmed"), `collect_booking→book` ("the form was submitted or the service and time are confirmed verbally"), `book→done` ("the booking is confirmed"), `book→no_booking` ("no slot works for the caller"), `identify→no_booking` ("the caller does not want to book").
  - `variables`: `caller_name` (string, required), `phone` (phone, required), `service` (enum: cleaning, check-up, filling, consultation), `preferred_time` (string).
- **Tool seeds** (both `enabled: true`, placeholder host `example.com`, `allowed_hosts: ["example.com"]`, `timeout_s 10`, `max_result_chars 2000`): `check_availability` (`GET https://example.com/appointments/availability?service={{ service }}&date={{ date }}`, parameters `service`, `date`; description "Check free slots for a service on a date; returns the nearest alternatives when the slot is taken") and `book_appointment` (`POST https://example.com/appointments`, `body_template` JSON with `name`, `phone`, `service`, `time`; description "Book the appointment; returns the confirmation id"). Until edited, the calls fail as ordinary tool errors that the model reports as "I couldn't reach the booking system"; the next step and the tools section's dry run make the fix obvious.
- **Extras.** `kb_seeds`: `Receptionist · Practice info` ← `seeds/practice_info.md` (hours, address, parking, services, cancellation policy). `voice.user_away_timeout_s 20`.
- **Gallery.** `sample_prompts`: "I'd like to book a cleaning next Tuesday morning.", "What are your opening hours?", "Can I cancel my appointment?" `next_steps`: "Point check_availability and book_appointment at your booking system" (tools), "Replace the practice info" (knowledge), "Review the flow" (flow), "Make a test call".
- **Needs.** Nothing to create; two tool URLs to edit.

### 5.4 `vision_assistant` — Vision assistant (camera and screen share)
- **Story.** Sees what the caller shows: describes a camera view, reads a shared screen, pins frames to a gallery and keeps notes. The multimodal starter.
- **Overlay.** `capabilities`: `camera true`, `screen_share true`, `chat_input true`, `vision_inject_per_turn true`. `pipeline`: cascaded Inference with `llm.model = "google/gemini-3.5-flash"` (the one Inference model flagged `supports_video`; the registry default is text-only and silently ignores frames — the test asserts `supports_video` whenever camera or screen share is on). `panel`: `status`, `gallery` ("Pinned frames"), `notes` ("Observations"), `activity`. `builtin_tools_disabled: []` (the vision built-ins `describe_current_frame`/`pin_frame` register because a capability is on). `instructions.md`: ask the caller to turn on the camera or share the screen when they mention something visual; describe concretely (objects, text, numbers, state), read text aloud when asked, call `pin_frame` with a caption when the caller says "keep this" or when something matters (an error message, a serial number), `push_note` for facts worth remembering, never claim to see what is not in frame. `greeting`: "Hi! Turn on your camera or share your screen and I'll tell you what I see — or just ask."
- **Extras.** none.
- **Gallery.** `sample_prompts`: "What's on my screen right now?", "Read the error message to me.", "Pin this and note the serial number." `next_steps`: "Optional: add a Google key and switch to Gemini Live for realtime video" (providers), "Tune what it should look for" (instructions), "Make a test call with the camera on".
- **Needs.** Nothing. (Gemini Live realtime video is an upgrade the next step names; the template itself does not require it, so the badge stays off.)

### 5.5 `phone_agent` — Phone agent (telephony, DTMF, transfer)
- **Story.** Answers a phone number: a keypad menu, answers from a small FAQ, takes a message, transfers to a human once you add a destination, and scores every call. The telephony starter.
- **Overlay.** `capabilities`: `dtmf true` (gated at seeding to the connection's SIP reachability, D-V4-8), `camera false`, `screen_share false`, `chat_input true` (it only gates the public session page's text box, and keeping it on lets the same agent be tried in the browser and the MCP text chat). `builtin_tools_disabled`: `["pin_frame", "describe_current_frame"]`. `panel`: `status`, `notes` ("Messages"), `transcript` (`show_tools false`), `activity`. `instructions.md`: phone etiquette (short sentences, confirm numbers by reading them back, no lists longer than three), the keypad menu ("press 1 for opening hours, 2 to leave a message, 0 for a person" — keypad entries arrive as user turns `[The caller pressed…]`), use `search_knowledge` for the FAQ, `push_note` for a message with the caller's name and number, `set_status` `message_taken`/`transferred`, call `transfer_call` only when a transfer target exists and the caller asks for a person, `end_call` after the goodbye. `greeting`: "Thanks for calling Acme. Press 1 for opening hours, 2 to leave a message, or just tell me what you need." `voice`: `user_away_timeout_s 20`, `allow_interruptions true` (merged over the seeded voice; the greeting comes from the overlay).
- **Extras.** `kb_seeds`: `Phone agent · FAQ` ← `seeds/phone_faq.md`. `qa`: `enabled true`, `rubric_prompt` a call-centre rubric (greeting given, need identified, resolved or routed, tone, closing). `telephony.transfer_targets: []` (see D-V4-8). `recording` off (needs a storage config; next step names it).
- **Gallery.** `sample_prompts`: "What time do you close today?", "I want to leave a message for the manager.", "Can I speak to someone?" `next_steps`: "Attach a number and a dispatch rule" (`href: /console/telephony`), "Add a transfer destination" (`href: /console/telephony`, the agent's telephony settings live in the editor's telephony extension when present, else the Telephony page), "Add a storage config to record calls" (recording), "Test it in a text chat before calling".
- **Needs.** `requires.telephony true` → "Needs a phone number" badge. No vendor key.

### 5.6 `lead_qualification` — Lead qualification (flow, variables, webhook, QA)
- **Story.** Qualifies an inbound lead with a fixed set of questions, extracts the answers as variables, routes to "book a demo" or "nurture", posts the result to your CRM through the `session.ended` webhook, and scores the call. The flow + variables + webhook starter.
- **Overlay.** `panel`: `status`, `notes` ("Qualification notes"), `kb_citations` ("Offer details"), `activity`. `builtin_tools_disabled`: `["pin_frame", "describe_current_frame"]`.
- **Flow.**
  - `start`: greeting "Hi, thanks for your interest in Acme. I'll ask a few quick questions so the right person can follow up. Sound good?"
  - `global`: "You qualify leads for Acme. One question at a time, acknowledge each answer, never pressure. Use `search_knowledge` when asked about pricing or features. `push_note` anything unusual."; `tools: ["search_knowledge", "push_note", "set_status"]`.
  - `company` (agent): "Ask for the company name and the caller's role."; `extract: ["company", "role"]`.
  - `needs` (agent): "Ask what they are trying to solve, how many people would use it, and when they want to start."; `extract: ["use_case", "team_size", "timeline"]`.
  - `budget` (agent): "Ask whether they have a budget range in mind; accept 'not sure'."; `extract: ["budget_range"]`.
  - `book_demo` (agent): "Offer a demo with a specialist; ask for the best email and a preferred day. Call `set_status` with qualified."; `extract: ["email"]`.
  - `qualified` (end): farewell "Perfect — a specialist will email you within one business day to confirm the demo.", `disposition: "qualified"`.
  - `nurture` (end): farewell "Thanks — I'll send over some material and we can pick this up when the timing is right.", `disposition: "nurture"`.
  - `qa` (kind `qa`): rubric "Score 1–5: were all qualification questions asked, was the routing decision consistent with the answers, was the tone consultative, did the agent avoid pressure."
  - Edges (amended by R-V4-30, 2026-09-25): `start→company` ("the caller agrees or starts answering" — the **only** edge from `start`, so the flow begins directly at `company` exactly as the receptionist begins at `identify`, and no model decision is needed to leave `start`), `company→needs`, `needs→budget`, `budget→book_demo` ("timeline is within 6 months and team_size is 5 or more"), `budget→nurture` ("timeline is later than 6 months, or team_size is under 5, or the caller is only researching"), `book_demo→qualified` ("email collected"), `company→nurture` ("the caller declines to answer questions or is only researching"; it was `start→nurture` before R-V4-30, which made `start` a router that `google/gemma-4-31b-it` never left — V4-06's B-6).
  - `variables`: `company` (string, required), `role` (string), `use_case` (string), `team_size` (number), `timeline` (enum: now, 1_3_months, 3_6_months, later), `budget_range` (enum: under_1k, 1k_10k, over_10k, not_sure), `email` (email).
- **Extras.** `kb_seeds`: `Lead qualification · Offer sheet` ← `seeds/offer_sheet.md` (plans, pricing bands, integrations). `qa` handled by the flow's `qa` node (R-V2-11: a `qa` node turns QA on).
- **Gallery.** `sample_prompts`: "We're a 20-person team looking at this for Q4.", "How much does it cost?", "I'm just researching for now." `next_steps`: "Add a webhook for session.ended to receive the variables and disposition" (`href: /console/settings?tab=webhooks`), "Edit the questions in the flow" (flow), "Replace the offer sheet" (knowledge), "Make a test call".
- **Needs.** `requires.webhook_endpoint true` → "Needs a webhook" badge (informational: the agent runs without one; the payload of `session.ended` carries `variables` and `disposition`).

### 5.7 `survey_intake` — Survey / intake form
- **Story.** Runs a fixed questionnaire by voice, shows each answer set as an on-screen form the respondent confirms, and appends one row per completed survey to a table. The forms starter, deliberately a prompt agent (contrast with the two flow starters).
- **Overlay.** `panel`: `status`, `form` ("Your answers"), `table` ("Responses", columns `name`, `satisfaction`, `recommend`, `comments`), `activity`. `builtin_tools_disabled`: `["pin_frame", "describe_current_frame", "escalate_to_human"]`. `instructions.md`: the five questions in order (name, satisfaction 1–5, would you recommend yes/no, what should we improve, anything else), one at a time, accept partial answers, after the last question call `request_form` with the answers prefilled so the respondent can correct them on screen, then `table_append` the confirmed row and `set_status` `completed`, thank them and `end_call`. `greeting`: "Hi! This is a two-minute feedback survey — five quick questions. Ready?" `voice`: `user_away_timeout_s 25`.
- **Extras.** none. `qa` off.
- **Gallery.** `sample_prompts`: "Sure, let's go.", "I'd say four out of five.", "The onboarding took too long." `next_steps`: "Change the questions" (instructions), "Adjust the response table's columns" (panel), "Make a test call".
- **Needs.** Nothing.

### 5.8 `insurance_claim` — Insurance claim intake (advanced example pack)
- **Story.** The full code pack: FNOL intake with policy lookup, claim extraction and classification, a document checklist, camera evidence and an incident sketch in a custom notebook panel, plus two seeded knowledge bases. Shown as the **advanced example**, last in the gallery.
- **Config.** `pack_id: insurance_claim`; every overlay field `None` (the manifest is the whole config: cascaded Inference with the vision LLM, `camera true`, the pack's five disabled built-ins, `google-image-gen` for sketches, two `kb_seeds`, the `insurance_notebook` panel). `category: example`, `order: 900`, `chips: ["code_tools", "knowledge_seeds", "camera", "image_gen"]`.
- **Gallery.** `sample_prompts`: "I had a small kitchen fire last night.", "My policy number is H0-44721.", "Can I show you the damage on camera?" `next_steps`: "Optional: add a Google key for incident sketches" (providers), "Read the pack's instructions" (instructions), "Make a test call with the camera on".
- **Needs.** `requires.provider_keys: [{provider_id: "google-image-gen", optional: true, purpose: "incident sketches"}]` → "Needs a key (optional)" badge; the test derives the same list from the manifest's pipeline.

Deferred candidates (not in v1, recorded so nobody re-argues them): `realtime_voice` (Gemini Live / OpenAI Realtime, needs a key — the first template that would show a **required** key badge), `avatar_presenter` (an avatar provider, full worker image), `outbound_reminder` (a `CallCreate`-driven flow with seed variables). See §9.

## 6. Console: the "New agent" dialog

Dialogs only (R-V3-2); the page becomes a deep link (R-V4-2). Files and acceptance in `PLAN-V4.md` V4-02.

### 6.1 Entry points
- `agents/page.tsx` header action, `agents-table.tsx` empty state, `overview/quick-actions.tsx`: all open `<CreateAgentDialog>` (a `Dialog` with `size="xl"`) in place of linking to `/console/agents/new`. `NewResourceButton` gains an `onClick` variant (or the callers switch to `Button` + the existing `useWriteAccess` check) — the write-access gate stays.
- `/console/agents/new` (`app/console/agents/new/page.tsx`) renders the agents page with the dialog open and `router.replace("/console/agents")` on close. `?template=<id>` preselects a starter (the MCP guide and the overview can deep-link "start from the receptionist starter").

### 6.2 Step 1 — Choose a starter
- Header: title "New agent", subtitle "Start from a starter and change anything afterwards." Body is two panes at `lg+` (gallery 7/12, preview 5/12) and stacked below `md` (preview collapses into an expandable card under the selected tile).
- **Gallery**: a `RadioGroup` (arrow keys move selection, as today) of tiles from `GET /v1/templates` in response order. Tile: name; tagline; a row of **capability chips** rendered from `chips` through a `TEMPLATE_CHIP_META` constant (label + icon per chip; the same `CAPABILITY_META` icons for camera/screen share/DTMF); a **badge row**: `Needs keys` (when any `requires.provider_keys[].optional === false`), `Optional key` (all optional), `Needs a phone number` (`telephony`), `Needs a webhook` (`webhook_endpoint`), `Example pack` (`pack_id !== "generic"`). When the workspace already holds a key for every required provider (the provider-keys list hook in `api-hooks.ts`), the key badge renders as a muted check "Keys present". Below the badges, one muted line: "Runs on LiveKit Inference — no vendor key" when `provider_keys` is empty. Selected tile: the same brand ring `PackCard` uses.
- **Preview pane** for the selected tile: description; a fact list — pipeline mode + vendor marks (from `template.pipeline ?? pack.recommended_pipeline`, the existing `pipelineProviderIds` logic), panel ("Blocks: Status, Sources, Notes, Activity" or the pack panel's `PANEL_META` label), tools ("2 HTTP tools: check_availability, book_appointment" / "4 code tools" for packs), knowledge ("Seeds 2 knowledge bases"), flow ("6 nodes, 4 variables"), QA on/off, capabilities icons; "Try saying" — the `sample_prompts` as quoted lines; "After creating" — the `next_steps` labels as a bullet list; a collapsed "Instructions" disclosure showing the first 600 characters of `instructions` (or the pack's `default_instructions`).
- Loading: skeleton tiles; error: `ErrorBanner` with retry (as today). Empty catalogue cannot happen (`blank` always ships), but a workspace whose api is older than the console (no `/v1/templates`) falls back to the pack list rendered as derived tiles — V4-02 handles a 404 from `templates` by calling `packs`, so the console can ship before the api is upgraded.
- Footer: "Cancel" and "Continue" (primary; disabled until a tile is selected — `blank` is preselected, so it is enabled at once).

### 6.3 Step 2 — Name it
- Same dialog, header "New agent · <starter name>" with a "Back" link. Name (required, prefilled with the starter name, e.g. "Receptionist"; inline error on blur), Description (optional, prefilled with the tagline, hint "Shown to callers on the call page"), Connection (only when the workspace has more than one connection; default preselected — mirrors `_workspace_connection_id`).
- Footer: "Back", "Create agent" (primary; pending label "Creating…"). Submit posts `{name, description, template_id, connection_id?, config: null}`. A 422 shows the api's `issues` under the form (the `IssueList` component the editor uses) with "Back" enabled; the dialog never closes on error.

### 6.4 After creating
- Toast: "Created from <starter>". If the seeded config differs from the starter's declared overlay in a gated field (`capabilities.dtmf` off while the template asked for it; `pipeline.mode` cascaded while the template asked for realtime; `image_gen` missing while the pack recommended one), the toast's second line names it: "DTMF is off until a SIP trunk is reachable on this connection." / "Running on LiveKit Inference — add a <vendor> key for <purpose>." The comparison is client-side (`template` vs `agent.config`); the api response does not change (D-V4-8).
- Navigate to `/console/agents/<id>?section=<first next_steps[].section ?? "providers">&from=<template_id>`.
- The editor's summary rail (`summary-rail.tsx`) shows a **"Next steps"** card when `?from=` is present: the starter's `next_steps` as a checklist (section items link to `?section=`, href items to their page), with "Dismiss" that removes `from` from the URL. This replaces the §4.2 "one-time hint" which was never built. No persistence: refreshing without `from` hides it; that is acceptable for a one-time hint.
- Accessibility: focus moves to the gallery on open and to the Name field on step 2; the dialog is labelled by its title; chips are `aria-label`led with the chip label; badges have `title` tooltips naming the vendor or requirement.

### 6.5 What the agents list shows
- Nothing new: the row's `pack_id` chip stays. A `template_id` column is Phase 2 (D-V4-3).

## 7. Validation and tests

`api/tests/test_templates.py` (V4-01):
- `EXPECTED_IDS = ("blank", "knowledge_assistant", "receptionist", "vision_assistant", "phone_agent", "lead_qualification", "survey_intake", "insurance_claim")` pinned; adding a starter is a deliberate edit.
- Per template, parametrised: `template.json` round-trips through `StarterTemplate`; the directory name equals `id`; `instructions.md` present whenever `pack_id == "generic"` and `id != "blank"`; the pack is installed under the test `LKAP_PACKS`; every name in `builtin_tools_disabled` ∈ `BUILTIN_TOOL_NAMES ∪ BLOCK_TOOL_NAMES ∪ {"send_dtmf", "transfer_call"}`; every provider id and model id in `pipeline` exists in the registry; `supports_video` holds for the LLM model when `camera` or `screen_share` is on; every `kb_seeds[].files` entry exists under `seeds/` and KB names start with `<name> · `; every `tool_seeds[].definition` validates, has `allowed_hosts` covering its `url` host, and its `name` is unique in the template; `flow` (when set) validates as `FlowSpec`, and its node tool references resolve through `allowed_tool_names(config, tool_names_by_id={seed names}, pack_tool_names=manifest.tool_names)` for the config the template seeds; `block_config_issues` is empty for `panel`; every `next_steps[]` has exactly one of `section`/`href`; `requires.provider_keys == requirements_from_pipeline(...)`.
- Seeding on a scratch workspace (SQLite, `FakeEmbedder`, no vendor keys, a connection whose caps report no SIP and no Egress): `POST /v1/agents {template_id}` → 201 for **every** template; `POST /v1/agents/{id}/validate` → `ok` with zero error-severity issues for every template with no non-optional required key; the created agent's `config.tools.tool_ids` has one row per tool seed and each row has `agent_id == agent.id`; creating `receptionist` twice yields two agents with disjoint tool rows; `config.knowledge.kb_ids` has one KB per seed; `config.voice.greeting == template.greeting` for `phone_agent` and `survey_intake` (the voice merge keeps the overlay greeting) and `config.voice.user_away_timeout_s` equals the template's; `phone_agent` gets `capabilities.dtmf == False` on that connection and `True` on a connection whose caps report SIP; `blank` equals `pack_id: generic` field for field; an MCP-shaped body (`template_id: "insurance_claim"` with the default `pack_id: "generic"`) → 201 with `pack_id == "insurance_claim"`; `template_id` with `config` → 422; unknown `template_id` → 422 naming the known ids; a loader fixture with `voice.greeting` or `knowledge.kb_ids` set fails validation.
- `GET /v1/templates` lists the catalogue in order, then a `pack:<id>` derived entry for a fake installed pack the test adds through `LKAP_PACKS`; a catalogue template whose pack is not installed is omitted; `GET /v1/templates/blank` returns it; both routes need admin.
- Existing tests unchanged and green: `test_create_without_a_config_seeds_from_the_pack`, `test_seeding_*`, `test_kb_seed.py` (the generalised importer keeps the pack wrapper's behaviour).

MCP (`mcp/tests`): `test_tools_agents.py` gains `agent_create(template_id="knowledge_assistant")` (plan and real) and the 422 relay; `tools.snap.json` regenerated for the new argument and the `template` describe kind; `test_docs_lint.py` resolves every `template_id` value in recipes against the scratch api's `/v1/templates`; `test_catalog_snapshot.py` unchanged in shape.

Web (`web/tests`): `console-create-agent.test.tsx` rewritten for the dialog: tiles render from a fixture `TemplatesResponse` (all eight), badges appear for `phone_agent`, `lead_qualification`, `insurance_claim` and for none of the Inference-only ones, "Keys present" when the provider-keys fixture holds a Google key, keyboard selection, step 2 prefill, the posted body carries `template_id` and no `pack_id`, the post-create navigation carries `section` and `from`, a 422 renders issues and keeps the dialog open, the 404 fallback to `packs`. `console-agents-list.test.tsx` and `console-overview.test.tsx`: the buttons open the dialog. A grep in the web gate: no V4 file imports `@/components/ui/sheet`.

## 8. MCP and docs

- **Resource** `lkap://templates` (live `GET /v1/templates`, cached like `packs`).
- **Describe** `lkap_describe(kind="template", id)` → the `TemplateOut` (with the catalogue ids in the not-found `details.known`).
- **Tool** `agent_create(name, template_id=None, pack_id="generic", …)`: `template_id` documented as "a starter id from lkap://templates (blank, knowledge_assistant, receptionist, vision_assistant, phone_agent, lead_qualification, survey_intake, insurance_claim)"; `pack_id` as "a pack without a starter (rare)". `plan=true` shows `template_id` in the planned body. `next_steps` in the result come from the template's `next_steps` labels when a template was used.
- **Recipes**: new `start-from-template.md` (pick a starter with `lkap://templates` or `lkap_describe`, create, follow its next steps, `test-and-publish`); `generic-assistant.md` uses `template_id: "blank"`; `insurance-intake-agent.md` uses `template_id: "insurance_claim"` and notes the optional Google key; `RECIPES` in `content.py` and the `guide.md` recipe list gain the new name. `concepts/agents.md` gets "Starters versus packs": a starter is configuration layered on a pack; a pack is code; use `template_id`; `pack_id` alone is the pack's derived starter.
- **Skill**: the recipe copy into `mcp/claude-plugin/skills/lkap/recipes/` happens through the export script (G4), nothing to edit; `SKILL.md` mentions `lkap://templates` in its "first calls" line.
- **Sanitised** (R-V3-8): the seeds and instructions name only fictional companies ("Acme Dental", "Acme Meter") and `example.com` hosts; the doc lint's hostname check runs over the catalogue's `instructions.md` and `seeds/*.md` too (V4-01 adds the catalogue directory to the lint's input list — that test file is V4-01's to edit, see the card).

## 9. Phase 2 (recorded, not planned)

- **Save as template**: a `templates` table, `POST /v1/templates` from an existing agent (strip credential ids and kb/tool ids into seeds), a console "Save as starter" action, and workspace-scoped templates listed after the catalogue. The `StarterTemplate` model is already the row shape; `derived` becomes `source: catalog|pack|workspace`.
- **`template_id` column** on agents for the list filter and analytics.
- **Realtime and avatar starters** once the "required key" badge has a real consumer.
- **Localised starters** (`voice.language`, `stt`/`tts` model language fields) — a per-template `locales` map.
