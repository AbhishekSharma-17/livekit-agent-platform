# LKAP v4 research — Panels, blocks and agent capabilities

Status: **research, complete** (Fable 5.1, 2026-09-24). Research only: no decision here is binding until it lands in a v4 architecture doc. Facts about third-party products are dated with an access date; anything a fetch could not confirm is marked **UNVERIFIED** and must not be planned against.

Companion docs (written in parallel by other Fable agents; this document cross-references and does not duplicate them):

- `tools-and-integrations.md` — HTTP/MCP tools, browser and computer use, SMS/WhatsApp and other outbound integrations.
- `knowledge-and-memory.md` — knowledge bases, retrieval, long-term and cross-session memory.

Sections: 1 executive summary · 2 inventory of what exists · 3 proposed blocks · 4 proposed capabilities · 5 generative-UI recommendation · 6 prioritised roadmap · 7 open questions · 8 sources.

---

## 1. Executive summary

LKAP's panel system (12 typed blocks over a seq-ordered patch envelope, strict public config, validated state) is the architecture the rest of the field arrived at in 2026: Google's A2UI, Vercel's json-render, AG-UI 1.0 and Pipecat's RTVI all chose a fixed client catalogue with pointer/patch state streaming; only MCP Apps ships arbitrary HTML, and it does so inside a CSP-governed sandbox that costs iframe boot time mid-call. **D-V2-12 stands.** The gaps are in *which* blocks exist and in *what the agent can do*, not in the mechanism.

What is missing on the panel side is the set of elements every surveyed product renders beside a live agent: quick-reply **choices**, a key-value **details** card, rich **markdown**, a **steps** timeline (today a JSON dump for flows), a **consent**/disclosure surface, an **upload** request (there is no client→agent bytes path at all today), a **handoff** status, a **slots** picker, **cards**, an outbound **link** (payment, e-sign, identity verification), **captions**, and an **annotation** layer over a pinned frame. Two small protocol additions make most of them cheap: a generic blocking `request_block` (generalising `request_form`) and an `lkap.ui.upload` byte stream. Nineteen block types and five enhancements are specified in §3 with config, state, tools and a voice-UX note each.

On capabilities, the decisive finding is that the pinned SDK already contains more than the v2 plan assumed. Verified by import against the agent venv's `livekit-agents 1.8.2`: native **answering-machine detection** (`AMD`), a prebuilt **warm transfer** with hold music and a private briefing (`WarmTransferTask`), **background/thinking audio**, **LLM judges** for evals (`evals.JudgeGroup`), **video input for realtime models** (`RoomOptions(video_input=True)`), `AgentTask` sub-flows and a full-duplex model class. That revises three v2 deferrals (AMD, warm transfer, text simulations move from Phase 2 to P1) and makes the two P0 capabilities configuration work: a **conversation-tuning screen** over the `turn_handling` options the contract already passes, and **recording consent + AI disclosure**, which the EU AI Act's Art. 50 has required since 2 August 2026. The P1 set is telephony noise cancellation, PII redaction with storage tiers, supervisor listen-in/takeover (primitives exist; no official example, needs a live test), multilingual switching (Deepgram `multi` covers Hindi/English and the plugin exposes `update_options(language=)`), document/ID extraction through the upload block, screen-share co-pilot, structured post-call fields and guardrails. Backchanneling, prosody emotion, voice cloning from the console, campaigns and MCP Apps embeds are deferred with reasons.

The roadmap in §6 orders the next ten packages: conversation tuning; consent and disclosure; the block quartet plus the request generalisation; upload and ID extraction; AMD; warm transfer with the handoff block; supervisor listen-in; text simulations with judges; privacy and analysis fields; multilingual switching with captions. Twelve questions for the user are in §7; the largest are Cloud-only `MoveParticipant` for warm transfer, jurisdiction defaults for disclosure, and whether human agents become a principal in the role matrix.

---

## 2. Inventory — what LKAP has today

Baseline: the tree at HEAD `8840d02` (v2 Phase 1 complete, v3 agent-access layer live-tested). Everything below is read from the code and the v2/v3 docs, not from memory.

### 2.1 Panel and block system

- **Panel layout** is configuration: `AgentConfig.panel: PanelLayout {panel_id: "composite" | <custom id>, layout: side|wide, blocks: BlockSpec[]}` (`contracts/src/lkap_contracts/agent_config.py`). The api resolves the effective layout (agent → pack `default_panel` → empty composite) and ships it on `ConnectResponse.agent.panel` (R-V2-7). The block list is public by definition.
- **Block state** is runtime: `UiState.blocks[<block id>]`, patched over the seq-ordered `lkap.ui.state` envelope with `set / append / remove / upsert` ops on JSON-pointer paths; a full snapshot every 50 patches or on gap (`contracts/src/lkap_contracts/ui_protocol.py`, `agent/src/lkap_agent/ui/channel.py`).
- **Config vs state split (R-V2-17)**: every block type has a strict, `extra="forbid"` config model in `contracts/src/lkap_contracts/blocks.py` and (for stateful types) a state model in `ui_protocol.py`. `initial_block_state` seeds state from any config key that names a state field. The console composer generates its per-block form from `web/src/panels/blocks/catalog.ts::configFields`, kept equal to the config schemas by a parity test.
- **The 12 block types** (`BlockType` literal):

| Type | State model | Config keys | Driven by |
|---|---|---|---|
| `status` | envelope `status` + `progress` | none | `set_status` |
| `notes` | envelope `notes[]` | none | `push_note` |
| `checklist` | envelope `checklist[]` | none | pack code (`set_checklist`) |
| `activity` | envelope `activity[]` (ring of 30) | none | every tool call |
| `form` | `FormBlockState {schema, values, status: idle\|requested\|submitted, submitted_at}` | none | `request_form` (blocking RPC with timeout; realtime mode returns `None` and delivers as a background result) |
| `table` | `TableBlockState {columns, rows, selected_row}` | `columns` | `table_append`, `update_block` |
| `document` | `DocumentBlockState {asset_id\|url, page, highlights[{page,bbox,note}]}` | `url` (https only), `page` | `show_document`, `update_block` |
| `gallery` | `GalleryBlockState {asset_ids, selected}` | none | `pin_frame` (bytes on the `lkap.ui.asset` byte stream), `update_block` |
| `kb_citations` | `KbCitationsBlockState {items[{chunk_id, filename, score, text}]}` | none | `search_knowledge` → `cite` (implicit) |
| `transcript` | `TranscriptBlockState {show_tools}` | `show_tools` | the room's transcription streams |
| `video` | `VideoBlockState {source: agent_avatar\|user_camera\|user_screen\|track:<sid>, muted}` | `source`, `muted` | `update_block` |
| `custom` | `{}` (pack-defined) | `kind` + any pack JSON | the pack; `kind == "flow_progress"` mirrors `FlowState` (R-V2-14) and renders as JSON today |

- **Block tools** (`contracts/src/lkap_contracts/tools.py::BLOCK_TOOL_NAMES`): `update_block` (for `UPDATABLE_BLOCK_TYPES` = document, gallery, table, transcript, video, kb_citations, custom), `show_document`, `table_append`, `request_form`. Each registers only when the panel has a block of its type; `pick_block` resolves an unnamed target when exactly one block of the type exists.
- **UI RPCs** (`UiRequest.method`): `open_dialog`, `focus`, `request_video_source`, `toast`, `form`, `show_block`, `navigate` (new tab, confirmed by the UI). **Agent actions** (`AgentAction.action`): `get_snapshot`, `set_video_source`, `ui_action`, `form_submit`, `block_action {block_id, name, data}` → `Pack.on_block_action`, `rewind`, `inject_user_text`.
- **Custom React panels** keep the v1 `PanelDefinition` (`web/src/panels/registry.ts`); the insurance notebook is the one shipped example and may render `<Block>` components inside. Panels are on the session-bundle path (≤ 400 kB budget, D-V2-19).
- **Already decided but not built** (ARCHITECTURE-V2 §3 deferred list): `whiteboard`, `map`, `chart` and a typed `flow_progress` renderer are Phase 2; "ingress sources as video blocks" Phase 3. D-V2-12 explicitly rejected "a fully dynamic JSON-rendered UI".

### 2.2 Agent capabilities

- **`CapabilitiesConfig`** (`agent_config.py`): `camera`, `screen_share`, `chat_input`, `vision_inject_per_turn`, `dtmf`. Camera/screen gate the vision built-ins `describe_current_frame` and `pin_frame` (`agent/src/lkap_agent/vision.py`).
- **Built-in tools** (`BUILTIN_TOOL_NAMES`): `end_call`, `search_knowledge`, `http_request`, `describe_current_frame`, `pin_frame`, `push_note`, `set_status`, `escalate_to_human` (a stub: sets status "Escalated" and records an event, no routing), `current_time`; plus the four block tools and the telephony tools `send_dtmf`, `transfer_call` (cold REFER, targets from `TelephonyConfig.transfer_targets`, R-V2-21).
- **Pipeline** (`PipelineConfig`): modes `cascaded | realtime | half_cascade`; slots `stt, llm, tts, realtime, avatar (+AvatarOptions), image_gen, workflow_llm, vad, turn_detection, noise_cancellation`; `turn_handling` passes through `turn_detection, endpointing, interruption, preemptive_generation, user_turn_limit` to `AgentSession` (`agent/src/lkap_agent/session_builder.py::_TURN_HANDLING_KEYS`). The console exposes `turn_handling` only as a raw JSON schema field (`web/src/components/console/lib/schemas.ts`); there is no tuned UI for endpointing or preemptive generation yet.
- **Voice**: `VoiceConfig {greeting, greeting_mode, language, allow_interruptions, user_away_timeout_s, first_speaker}`. `language` is a single string; no per-session language switching or translation.
- **Providers**: ~90 registry entries across 11 kinds (`ProviderKind` incl. `vad`, `turn_detection`, `noise_cancellation`); Krisp NC on Cloud connections (`noise_cancellation_tier`), `inference.TurnDetector` hosted or local per `turn_detector_mode`; 15 avatar plugins with vendor catalogs (bey, tavus, simli, anam, d-id, liveavatar pickers). Hume is registered as a TTS provider; Palabra (translation) and the browser plugin are catalogued but not registered (ARCHITECTURE-V2 §3: Phase 3).
- **Channels**: `web, test, text, sip_in, sip_out, widget, api`. Telephony core: SIP trunks, dispatch rules, outbound single call with `wait_until_answered`, DTMF send/receive, cold transfer. Deferred to Phase 2 by D-V2-14: campaigns, AMD/voicemail, warm transfer, voicemail drop.
- **Testing/QA**: post-call QA judge in the worker (`QaConfig`, `session_qa`), text test mode with `rewind`/`inject_user_text`, config versions with restore. Deferred (D-V2-15): simulated caller (second persona worker) + judge, scenario suites, CI regression.
- **Observability**: Egress audio recordings to S3, cost lines from real usage × `pricing.py`, latency p50/p95 (EOU→first audio, TTFT, TTFB), OTel with LiveKit GenAI conventions, signed durable webhooks, `usage_daily` rollups. No intent/topic extraction beyond the QA judge's `tags`.
- **Flows**: `FlowSpec` with `start/agent/end/global/transfer/qa` nodes, edges as `go_to_<node>` tools, LiveKit handoffs carrying `chat_ctx`, variable extraction on node exit, per-node LLM/TTS override in cascaded mode. `AgentTask` sub-flows and loops are Phase 2.
- **Agent access (v3)**: the `lkap` MCP server (stdio + streamable HTTP) with `chat_start/send/rewind/end` test chat, a Claude Code skill, `AGENTS.md`.
- **Not present anywhere**: multilingual switching or live translation, voice cloning, emotion/sentiment in-call, backchanneling, voicemail detection, recording consent prompts, PII redaction, warm transfer/whisper, supervisor listen-in or human takeover, campaigns, SMS/WhatsApp follow-up, document/ID OCR, screen-share co-pilot beyond frame description, browsing/computer use, long-term memory, simulated callers, prompt A/B tests, guardrails/moderation, filler phrases.

---

## 3. Proposed blocks

### 3.0 Two protocol additions every new block leans on

Before any new type, two small, generic additions to the envelope make most of the blocks below cheap. They are proposals, not decisions.

1. **A generic blocking request (`request_block`).** `request_form` today is a one-off: `UiRequest.method="form"` + `AgentAction.action="form_submit"` + `UiChannel.request_form(...)` awaiting a future keyed by block id. Five of the blocks below (choices, slots, upload, signature, consent) need exactly the same "ask the browser, await the user's answer or a timeout, realtime mode gets it as an urgent background result" shape. Generalise once: `UiRequest.method="request" {block_id, timeout_s}` and `AgentAction.action="block_submit" {block_id, values} | {block_id, cancelled: true}`, with `UiChannel.request_block(block_id, *, timeout_s) -> dict | None`. `form` keeps its method name for one release as an alias. The state model of each requestable block carries `status: idle | requested | submitted | cancelled` so a reconnecting browser renders the pending request from the snapshot, exactly as `form` does today.
2. **Client → agent bytes (`lkap.ui.upload`).** `lkap.ui.asset` only carries bytes from the agent to the browser (`push_asset`). Upload, signature and the camera-annotation block need the reverse direction. LiveKit byte streams are symmetric: the browser calls `room.localParticipant.sendFile(file, {topic: "lkap.ui.upload", attributes: {block_id, name}})` and the worker registers `room.register_byte_stream_handler("lkap.ui.upload", ...)`; the worker stores the bytes as a session asset (same `AssetRef` shape) and posts it to the api's existing session asset store. Limits belong in the block config (public) and are enforced worker-side too.

Both additions reuse the ordering guarantees D-V2-12 chose the single envelope for; neither adds a data channel.

### 3.1 Summary table

Legend: **Kind** — `new` = a new `BlockType` with its own config and state models; `custom.kind` = a typed renderer for an existing `custom` block kind (no contract change beyond the renderer); `enh` = an enhancement of an existing type. **Effort** S/M/L for contracts + worker tool + renderer + composer form + fixtures. **Pri** P0/P1/P2 for LKAP's next phase (insurance first, then generic).

| # | Block | Kind | Purpose | Driving tool(s) | Needs §3.0 | Effort | Pri |
|---|---|---|---|---|---|---|---|
| B1 | `choices` | new | Quick replies, single/multi select, yes/no, quiz/poll | `request_choice` | request | S | **P0** |
| B2 | `details` | new | Key-value summary card (claim no., policy, dates, amounts) | `set_details`, `update_block` | — | S | **P0** |
| B3 | `markdown` | new | Rich text: recap, instructions, a quoted policy clause, a generated letter | `show_text`, `update_block` | — | S | **P0** |
| B4 | `steps` | new (promotes `custom.kind=flow_progress`) | Step progress / timeline of the call or the flow | `set_steps`; auto from `FlowState` | — | S | **P0** |
| B5 | `consent` | new | Recording consent, AI disclosure, terms; gates recording | `request_consent` | request | S | **P0** |
| B6 | `upload` | new | Ask the caller for files/photos (ID, invoice, PDF) | `request_upload` | request + upload | M | **P1** |
| B7 | `handoff` | new | Human handoff / transfer status, queue, who joined | `escalate_to_human` (upgraded), `transfer_call`, the takeover capability | — | S (M with the capability) | **P1** |
| B8 | `slots` | new | Calendar slot picker | `request_slot` | request | M | **P1** |
| B9 | `annotation` | new (was "whiteboard", ARCHITECTURE-V2 §3) | Boxes/arrows/labels on a pinned camera or screen frame; user can draw too | `annotate_frame`, `pin_frame` | upload (user drawing) | M | **P1** |
| B10 | `cards` | new | Rich cards, product carousel, offer comparison | `show_cards`, `update_block` | — | M | P1 |
| B11 | `link` | new | Checkout / payment / e-sign / deep link with completion status | `send_link`, completion via webhook → `link_completed` event | — | S | P1 |
| B12 | `captions` | new | Live captions, optionally translated into the viewer's language | none (reads transcription streams) + the translation capability | — | M | P1 |
| B13 | `chart` | new (ARCHITECTURE-V2 §3 P2) | Small fixed chart schema: number, bar, line, pie, gauge | `show_chart`, `update_block` | — | M | P2 |
| B14 | `map` | new (ARCHITECTURE-V2 §3 P2) | Location, markers, a route (repair shop, branch, tow ETA) | `show_map`, `update_block` | — | M (tile provider) | P2 |
| B15 | `signature` | new | Capture a signature image + intent record | `request_signature` | request + upload | M | P2 |
| B16 | `timer` | new | Countdown / elapsed (OTP expiry, hold, session limit) | `start_timer`, `update_block` | — | S | P2 |
| B17 | `embed` | new | Sandboxed iframe / video player from an allowlisted host | `show_embed`, `update_block` | — | M (security review) | P2 |
| B18 | `code` | new | Code or diff view | `show_code` | — | S | P2 (generic packs only) |
| B19 | `cart` | new | Order lines and totals | `cart_add`, `update_block` | — | S | P2 |
| E1 | `kb_citations` + source preview | enh | Click a citation → open the page in the `document` block | `cite` carries `asset_id|url, page, bbox` | — | S | **P0** |
| E2 | `activity` "working on" line | enh | Show `agent_state == thinking` and the running tool's headline as one live line; render `detail` | none (room `agent_state` events) | — | S | P1 |
| E3 | `form` field types | enh | `date`, `phone`, `email`, `select`, `file` (via B6) in `request_form` | `request_form` | upload for `file` | S | P1 |
| E4 | `video` PiP + annotation overlay | enh | Show `annotation` shapes over the live `user_camera` source | B9 | — | S | P1 |
| E5 | `transcript` translation toggle | enh | When B12 exists, the transcript can show the translated line under the original | B12 | — | S | P2 |

Evaluated and **not** proposed as a block: a separate *quiz/poll* type (B1 with `reveal`), a separate *thinking feed* (E2), a *cart* separate from cards is kept only as a small P2 (B19) because insurance has no basket; *rich cards* and *product carousel* are one type (B10).

### 3.2 Block details

Each entry: purpose · example · **config** (public, strict, `extra="forbid"`) · **state** (validated by a contracts model, exported to TS) · **tools** and registration rule · **voice UX**.

#### B1 `choices` — quick replies and option pickers

- **Purpose.** The single most common in-call UI element in every product surveyed (§8): a small set of buttons the caller can tap *or* answer by voice. Covers yes/no confirmations, "which policy?", NPS, and a quiz/poll when `reveal` is set.
- **Example.** "Was anyone injured?" → `[No] [Yes, minor] [Yes, serious]`. The caller says "no" or taps; either way the tool returns `{"selected": ["no"]}`.
- **Config.** `{ multi: bool = false, layout: "buttons" | "list" | "chips" = "buttons", max_options: int = 8 }`.
- **State.** `ChoicesBlockState { prompt: str, options: [{id, label, hint?, tone?, image_asset_id?}], multi: bool, selected: list[str], status: idle|requested|submitted|cancelled, reveal: {correct: list[str], explanation?} | None, submitted_at }`.
- **Tools.** `request_choice(block_id?, prompt, options: [{id,label,hint?}], multi=false, timeout_s=60) -> {selected} | None` — registered when the panel has a `choices` block (`BLOCK_TOOL_TYPES` rule). Uses `request_block` (§3.0). **Voice answers**: the tool description tells the model that if the caller answers by voice, it must call `resolve_choice(block_id, selected)` so the block flips to `submitted` and the pending request resolves — the same trick `request_form` needs in realtime mode, made explicit.
- **Voice UX.** The agent reads at most three options aloud and says "or tap one on your screen"; on phone channels (`sip_*`) the block is invisible, so the tool returns immediately with `{"channel": "voice_only"}` and the model relies on the spoken answer. The composer shows a "voice-only fallback" hint.

#### B2 `details` — key-value summary card

- **Purpose.** The "what we have so far" card: claim number, policyholder, incident date, estimated amount, next step. The insurance notebook's "Claim details" summary, generalised. Cheap and used in nearly every vertical.
- **Config.** `{ columns: 1 | 2 = 1, fields: [{key, label, type: string|number|date|money|phone|email|badge}] }` (starting fields; the agent may add).
- **State.** `DetailsBlockState { items: [{key, label, value: str|number|null, type, tone?, updated_at}] }`. `upsert` by `key` maps straight onto the existing tree-op semantics.
- **Tools.** `set_details(block_id?, items: [{key, label?, value}])` upserts; `update_block` may remove. Registered when a `details` block exists.
- **Voice UX.** None required; the agent should not read the card aloud. The tool description says "update silently as facts are confirmed".

#### B3 `markdown` — rich text

- **Purpose.** Anything longer than a note: a recap before hangup, step-by-step instructions, a quoted policy clause with emphasis, a drafted email or letter the caller can read while the agent speaks a shorter version.
- **Config.** `{ max_chars: int = 8000, allow_links: bool = false }`.
- **State.** `MarkdownBlockState { markdown: str, title?: str, updated_at }`. Rendered with a strict Markdown subset (no raw HTML, links only when `allow_links`, images only from session assets) so the "config is public, state is validated" rule holds.
- **Tools.** `show_text(block_id?, markdown, title?)` replaces; `update_block` may append. Registered when a `markdown` block exists.
- **Voice UX.** The tool description instructs: "speak a one-sentence summary; the full text is on screen". On voice-only channels the model should not call it (the tool returns `{"visible": false}` so the model knows).

#### B4 `steps` — progress timeline

- **Purpose.** Where the caller is in the process. For flow agents it mirrors `FlowState` automatically (today the `custom.kind=flow_progress` block dumps JSON, R-V2-14); for prompt agents the pack or the model sets steps. Also the natural "checklist timeline" the task mentions.
- **Config.** `{ steps: [{id, label}], source: "flow" | "manual" = "manual", show_notes: bool = true }`.
- **State.** `StepsBlockState { steps: [{id, label, status: pending|active|done|skipped|failed, note?, at?}], current: str|null }`.
- **Tools.** `set_steps(block_id?, steps)` and `update_block`; with `source="flow"` the worker writes it from the `handoff` event and no tool is registered. Migration: existing `custom` blocks with `kind == "flow_progress"` are rendered by the `steps` renderer with `source="flow"`.
- **Voice UX.** Silent. The agent may say "that's step two of four done".

#### B5 `consent` — recording consent and AI disclosure

- **Purpose.** Show the disclosure text, capture acceptance by tap or by voice, and **gate recording** (the worker starts Egress only after `accepted` when `recording.require_consent` is set — see C8). Also the always-on "You are talking to an AI" banner that the EU AI Act Art. 50 and several US state laws require (§4, C8).
- **Config.** `{ kind: "recording" | "ai_disclosure" | "terms" | "custom", text: str, required: bool = true, decline_action: "continue" | "end_call" = "continue", show_banner: bool = true }`. The text is public config by design (it must be auditable).
- **State.** `ConsentBlockState { status: pending|accepted|declined|not_applicable, method: tap|voice|null, at, text_hash }`. `text_hash` ties the acceptance to the exact wording for the audit row.
- **Tools.** `request_consent(block_id?)` (blocking, `request_block`); `record_consent(block_id, accepted: bool, method="voice")` for spoken answers on any channel. Both write a `consent` session event `{kind, accepted, method, text_hash}` and, for `recording`, call the api's recording start/stop.
- **Voice UX.** The agent must read the disclosure in full on voice-only channels (the pack's greeting template includes it); on web the banner shows while the agent asks. Declining a required recording consent with `decline_action="end_call"` says goodbye politely.

#### B6 `upload` — request files or photos from the caller

- **Purpose.** "Please upload a photo of the damage / your driver's licence / the invoice." Feeds document OCR and ID checks (C14) and the gallery. The caller picks a file, takes a photo (mobile `capture="environment"`), or drags a PDF.
- **Config.** `{ accept: "image/*,application/pdf", max_files: int = 5, max_bytes: int = 10_000_000, camera_capture: bool = true }`.
- **State.** `UploadBlockState { prompt, status: idle|requested|uploading|submitted|cancelled, files: [{asset_id, name, mime, size, ts, preview_asset_id?}], progress: {name, pct}|null }`.
- **Tools.** `request_upload(block_id?, prompt, accept?, max_files?) -> {files: [{asset_id, name, mime, size}]} | None` (blocking). The worker then hands each asset to vision (`describe_asset(asset_id)`) or to a pack handler; uploaded images are also appended to the gallery when one exists. Needs the `lkap.ui.upload` byte stream (§3.0).
- **Voice UX.** "Tap upload or take a photo — I'll wait." The agent keeps listening; an `uploading` progress patch lets it say "got it, one moment" without a tool call.

#### B7 `handoff` — human handoff status

- **Purpose.** Replace today's stub `escalate_to_human` (sets a status stamp, records an event) with a visible state machine the caller and the console both see: requested → queued → connecting → connected (with the human's display name) → ended, or declined/timeout, plus `mode`.
- **Config.** `{ show_queue: bool = true, show_agent_name: bool = true }`.
- **State.** `HandoffBlockState { status: none|requested|queued|connecting|connected|declined|timeout|ended, mode: transfer|takeover|listen_in|callback, reason?, urgency, queue_position?, eta_s?, human: {identity, name}|null, at }`.
- **Tools.** `escalate_to_human` gains `mode` and drives this block; `transfer_call` writes `mode="transfer"`. The worker updates `connected` when a participant with attribute `lkap.role=human` joins (C10). On `sip_*` channels the state is still written (the console session view shows it).
- **Voice UX.** "I'm bringing in a colleague; stay on the line." While `connecting`, the agent stops taking turns (C10) and the block shows the wait.

#### B8 `slots` — calendar slot picker

- **Purpose.** Book an inspection, a callback, an appointment. The agent fetches availability through a tool (calendar integrations belong to `tools-and-integrations.md`), shows slots, the caller taps or says one.
- **Config.** `{ timezone_mode: "caller" | "agent" = "caller", days_visible: int = 7, allow_custom: bool = false }`.
- **State.** `SlotsBlockState { prompt, timezone, slots: [{id, start, end, label?, capacity?}], selected: str|null, status: idle|requested|submitted|cancelled, grouped_by_day: bool }`.
- **Tools.** `request_slot(block_id?, prompt, slots: [{id, start, end}], timeout_s=120) -> {selected, start, end} | None`; `resolve_slot` for spoken answers (same rule as B1).
- **Voice UX.** Read the first two or three slots; on voice-only channels the block is skipped and the model negotiates verbally. Timezone display follows the caller's browser; the tool always returns ISO 8601 with offset.

#### B9 `annotation` — boxes and arrows on a pinned frame

- **Purpose.** The "whiteboard" of ARCHITECTURE-V2 §3, scoped down to what the vision pipeline can actually drive: the agent (via a VLM returning bounding boxes) or the caller draws rectangles, arrows, circles and labels over a pinned camera or screen frame. Insurance: "circle the dent"; screen-share co-pilot: "click the button I've highlighted" (C15).
- **Config.** `{ user_can_draw: bool = true, tools: ["box","arrow","circle","text"], source: "gallery_selected" | "live_camera" = "gallery_selected" }`.
- **State.** `AnnotationBlockState { asset_id: str|null, width, height, shapes: [{id, kind, coords: [x0,y0,x1,y1] normalised 0..1, label?, tone?, author: agent|user}], selected: str|null }`. Normalised coordinates survive any display size.
- **Tools.** `annotate_frame(block_id?, asset_id?, shapes)` upserts by id; `clear_annotations`. The worker's `describe_current_frame` can request boxes from the VLM (`vision.py` gains a "locate" prompt) and call `annotate_frame` itself. User drawings arrive as `block_action {name: "draw", data: shape}` → `Pack.on_block_action` and, for the model, as a background user message "the caller circled an area at …".
- **Voice UX.** "I've marked the two areas I can see damage in — is that right?" E4 overlays the same shapes on the live `video` block when `source="live_camera"`.

#### B10 `cards` — rich cards and carousels

- **Purpose.** Compare two or three options (coverage plans, repair shops, products), each with image, title, subtitle, a few facts and actions. Selecting a card is a `block_action` the model receives.
- **Config.** `{ layout: "carousel" | "grid" | "list" = "carousel", selectable: bool = true, max_cards: int = 10 }`.
- **State.** `CardsBlockState { cards: [{id, title, subtitle?, image_asset_id?|image_url? (https, allowlisted), facts: [{label, value}], badges: [str], actions: [{name, label, tone?}]}], selected: str|null }`.
- **Tools.** `show_cards(block_id?, cards)`, `update_block`. Card action taps → `block_action {block_id, name, data:{card_id}}`; the worker turns it into a background user message so the model reacts ("You picked the Gold plan").
- **Voice UX.** The agent describes the differentiator of each card in one clause; never reads all facts.

#### B11 `link` — checkout, payment, e-sign and deep links

- **Purpose.** Hand the caller to an external, already-trusted flow (Stripe Checkout / Payment Link, DocuSign, a portal deep link) and show its completion state. Payments themselves are out of scope for LKAP (PCI); the platform only carries the link and the webhook result (`tools-and-integrations.md` owns the Stripe/DocuSign integrations).
- **Config.** `{ allowed_hosts: [str] (https only, required), open_in: "new_tab" | "dialog" = "new_tab", show_qr: bool = true }`. `show_qr` matters on desktop web when the caller wants to pay on their phone.
- **State.** `LinkBlockState { url, label, kind: checkout|esign|portal|other, status: pending|opened|completed|failed|expired, expires_at?, reference?, completed_at? }`.
- **Tools.** `send_link(block_id?, url, label, kind, expires_at?)` — the url must match `allowed_hosts` (validated worker-side too). Completion arrives through the api's inbound webhook (`link.completed` event → worker over a reliable data packet, the same path as `lkap.telephony.dtmf`) and becomes a background user message.
- **Voice UX.** "I've put a secure payment link on your screen; tell me when you're done, or I'll see it complete." On voice-only channels the tool sends the link by SMS instead when the SMS capability exists (C12), else returns `{"channel": "voice_only"}`.

#### B12 `captions` — live captions and translation

- **Purpose.** Accessibility captions of both sides, and, with the translation capability (C1), a second line in the viewer's language. Distinct from `transcript`: captions are the *current* utterance, large, ephemeral.
- **Config.** `{ show_user: bool = true, show_agent: bool = true, target_language: str|null = null, position: "bottom" | "block" = "block" }`.
- **State.** `CaptionsBlockState { source_language, target_language, current: {speaker: user|agent, text, translated?, final: bool} | null }`.
- **Tools.** None for the caller-side rendering: the browser reads the `lk.transcription` text streams (the agent's `transcription` output and the user's STT) and, when translation is on, a second stream topic `lkap.translation` the worker publishes per segment. `set_caption_language(target)` lets the caller ask "can you show this in Hindi?".
- **Voice UX.** Silent. On avatar layouts `position="bottom"` overlays the video.

#### B13 `chart` — small fixed chart schema

- **Purpose.** A number with a delta, a bar/line of a few series, a pie of shares, a gauge. Fixed schema, not Vega-Lite (a full grammar in public config is both a bundle and a validation problem).
- **Config.** `{ kind: number|bar|line|pie|gauge, unit?: str, max_points: int = 200 }`.
- **State.** `ChartBlockState { kind, title?, unit?, series: [{name, points: [{x: str|number, y: number}]}], value?: number, delta?: number, min?, max? }`.
- **Tools.** `show_chart(block_id?, ...)`, `update_block` (append points). Renderer: a tiny SVG chart component (no chart library on the session bundle path; ≤ 400 kB budget).
- **Voice UX.** The agent states the one number that matters.

#### B14 `map` — location and route

- **Purpose.** Nearest repair shop, branch, tow-truck ETA, "confirm the incident location". The composer must pick a tile/maps provider (MapLibre + a tile key, or Google Maps JS); this is a workspace-level provider setting, not per block.
- **Config.** `{ provider: "maplibre" | "google", interactive: bool = true, default_zoom: int = 13 }`.
- **State.** `MapBlockState { center: {lat, lng}, zoom, markers: [{id, lat, lng, label?, tone?}], route: {polyline: str, eta_s?}|null, selected: str|null }`.
- **Tools.** `show_map(block_id?, center, markers, route?)`, `update_block`. Tapping a marker → `block_action {name: "select_marker"}`. Geocoding is a tool (`tools-and-integrations.md`).
- **Voice UX.** "The closest approved shop is 2 km away — I've marked it." Map libraries are lazy-loaded like pdf.js (`blocks/pdf-page.tsx` pattern) so they stay off the base bundle.

#### B15 `signature` — signature capture

- **Purpose.** A drawn signature plus an intent record for low-stakes acknowledgements (proof of statement, delivery acceptance). Not a qualified e-signature: anything legally weighty goes through B11 to DocuSign/Adobe Sign.
- **Config.** `{ disclosure_text: str, require_name: bool = true }`.
- **State.** `SignatureBlockState { status: idle|requested|signed|cancelled, name?, asset_id?, signed_at?, text_hash }`.
- **Tools.** `request_signature(block_id?, disclosure_text?)` (blocking); the PNG arrives on `lkap.ui.upload`. The session event stores the hash of the disclosure text as consent does.
- **Voice UX.** "Please sign in the box on your screen." Skipped on voice-only channels.

#### B16 `timer` — countdown or elapsed

- **Config.** `{ mode: countdown|elapsed, tone_warning_s: int = 30 }`. **State.** `TimerBlockState { label, ends_at?: float, started_at?: float, running: bool }`. **Tools.** `start_timer(block_id?, label, seconds)`, `stop_timer`. **Voice UX.** "You have three minutes to enter the code." Rendered client-side from timestamps, so no patches per second.

#### B17 `embed` — sandboxed iframe or video player

- **Purpose.** Play an explainer video, show a hosted 3D viewer, or mount a third-party mini-app. This is the only block that shows content LKAP does not render itself, so it is also the natural host for MCP-UI / MCP Apps resources (§5).
- **Config.** `{ allowed_hosts: [str] (required, https), sandbox: "video" | "app" = "video", allow_messages: bool = false }`.
- **State.** `EmbedBlockState { url, kind: youtube|vimeo|video_file|iframe, playing?, position_s?, title? }`.
- **Tools.** `show_embed(block_id?, url, kind)`, `update_block` (seek/pause for video). `sandbox="app"` renders `<iframe sandbox="allow-scripts" ...>` with a CSP and a postMessage bridge limited to `{intent, notify}` (MCP-UI's action vocabulary) mapped onto `block_action`.
- **Voice UX.** "I'm playing the 40-second guide now; say pause any time."

#### B18 `code` and B19 `cart`

- `code`: `{language}` config; state `{language, code, filename?, diff?: {before, after}}`; tool `show_code`. Only meaningful for developer-assistant packs; P2.
- `cart`: state `{currency, lines: [{sku, title, qty, unit_price, total}], subtotal, tax, total}`; tools `cart_add/cart_remove`; pairs with B10 and B11. P2 (no insurance use).

#### E1 — citations with a source preview

`KbCitation` gains `asset_id?`, `url?`, `page?`, `bbox?`. Tapping a citation sends `block_action {name: "open_citation"}`; the worker answers with `show_document(asset_id, page, note)` on the `document` block when one exists (else `navigate`). Requires the KB ingest to keep page numbers per chunk (`knowledge-and-memory.md`). Cheap and makes `kb_citations` genuinely useful.

#### E2 — the activity feed as a "thinking / working on" line

No new block: the `activity` renderer subscribes to the room's `agent_state` (`listening | thinking | speaking`, already exposed by `useVoiceAssistant`) and shows one live line ("Looking up your policy…" from the running tool's `headline`) above the ring. Also render `ActivityEvent.detail` in a collapsible row. Matches the tool-activity pattern every agent product now shows (§8).

#### E3 — richer `request_form` field types

`request_form(fields: [{name, label, type, required}])` today accepts a small type set; add `date`, `phone`, `email`, `select {options}`, `textarea`, and `file` (delegating to the upload stream). The JSON schema already carries `format`, so the contract change is only in the tool signature and the renderer.

---

## 4. Proposed capabilities

### 4.0 How to read the table

- **Support now** names the exact LiveKit API or vendor parameter the research found. Anything a page could not confirm stays **UNVERIFIED**. Items marked **(1.8.2 ✓)** were additionally verified by importing them from the agent venv's pinned `livekit-agents 1.8.2` on 2026-09-24 (`AMD`, `livekit.agents.beta.workflows.WarmTransferTask`, `BackgroundAudioPlayer` + `BuiltinAudioClip.HOLD_MUSIC`, `livekit.agents.evals.JudgeGroup`/`accuracy_judge`, `AgentTask`, `mock_tools`, `AgentSession.run/say/interrupt`, `voice.room_io.RoomOptions(video_input=…)`/`AudioInputOptions`, `llm.DuplexModel`, `AgentServer`, `inference.TurnDetector`). None of the Palabra, Gladia, noise-cancellation, Krisp, Hamming or Hume plugins are installed in that slim dev venv today; Gladia, Krisp, Hamming and Hume are `full`-image registry entries, while Palabra and the browser plugin are catalogued but not registered at all (ARCHITECTURE-V2 §3, Phase 3).
- **Effort**: S ≤ one PLAN-V2-sized package, M = one to three, L = more or blocked on an external dependency. **Value** is for LKAP's next phase (insurance pack first, generic platform second). **Pri** P0 = next wave, P1 = the wave after, P2 = later.
- Where ARCHITECTURE-V2 §3 or D-V2-14/15 already assigned a phase, the row says whether this research **confirms** or **revises** it.

### 4.1 Summary table

| # | Capability | Support now | Effort | Value | Pri | v2 decision |
|---|---|---|---|---|---|---|
| C1 | Multilingual + mid-call language switching | Deepgram `language="multi"` (10 languages incl. Hindi) and `update_options(language=)` in the plugin; Gladia `code_switching`; Google `detect_language`; OpenAI STT in-place `update_options`; turn detector 14 languages; Gemini Live auto-switches (97/99 languages per Google) | M | High (India: Hindi/English code-switching) | **P1** | new |
| C2 | Live translation (captions, then speech) | `livekit-plugins-palabra` STT emits translations with `source_texts`; Gladia `translation_target_languages`; `livekit-examples/live-translated-captioning` (Deepgram → GPT → captions) | M captions / L speech | Medium | P2 | ARCHITECTURE-V2 §3 Phase 3 → **revise to P2 for captions** |
| C3 | Voice cloning / design | LiveKit Inference custom voices (dashboard only, consent checkbox, paid plan, one `v_…` id across Cartesia/Inworld/Fish/Gradium); ElevenLabs `POST /v1/voices/add` (voice-captcha), Voice Design; Cartesia `POST /voices/clone`; Hume Octave | S (list cloned voices) / M (clone from console) | Low–Medium | P2 | new |
| C4 | Emotion / sentiment in-call | Hume EVI returns expression scores but the LiveKit Hume plugin is TTS-only; Deepgram `sentiment/intents/topics` stream on Nova (English) but are **not** in the LiveKit plugin; practical path = per-turn text classifier on `user_input_transcribed` | S (text) / L (audio prosody) | Medium (distress → escalate) | P2 | new |
| C5 | Noise cancellation / voice isolation on telephony | `noise_cancellation.BVCTelephony()`, `krisp.voice_isolation_telephony()`, SIP trunk `krisp_enabled`; ai-coustics self-hosted; Cloud pricing 1,000 min then $0.0012/min (pricing page) | S | Medium | **P1** | extends D-V2-10 |
| C6 | Barge-in and turn-taking tuning | `turn_handling` already reaches `AgentSession` (`_TURN_HANDLING_KEYS`); `TurnHandlingOptions` defaults: endpointing `fixed/0.5/3.0`, interruption `adaptive`, `false_interruption_timeout=2.0`, `resume_false_interruption`, `preemptive_generation.enabled=True`; flat params deprecated on `main`; `TurnDetector(unlikely_threshold=…)` per language | S | High | **P0** | new (console only) |
| C7 | Backchanneling | Not in LiveKit; Retell `enable_backchannel`; native in full-duplex models (`DuplexModel`/GPT-Live in 1.8.1+, NVIDIA PersonaPlex); `say(add_to_chat_ctx=False)` on `user_state=speaking` is a hack | M | Low | P2 | new |
| C8 | Voicemail / AMD for outbound | **`livekit.agents.AMD` (1.8.2 ✓)**: `human / machine-ivr / machine-vm / machine-unavailable / uncertain`, `interrupt_on_machine`, `ivr_detection`; start before `create_sip_participant(wait_until_answered=True)`; carrier AMD (Twilio `MachineDetection`, Telnyx `answering_machine_detection=premium`) as alternatives | S–M | High for outbound | **P1** | D-V2-14 P2 "carrier flag + transcript classifier" → **revise: native AMD, P1** |
| C9 | Recording consent + AI disclosure | EU AI Act Art. 50 applies since 2 Aug 2026; CA SB 243 (companion bots; customer-service bots excluded) and B&P §17941; India DPDP Rules 2025 phased to 2027; US two-party list UNVERIFIED. Vendors: Synthflow transparency messages, ElevenLabs widget terms, Vapi widget consent | S | High (compliance) | **P0** | new |
| C10 | PII redaction and storage tiers | Deepgram `redact=` on Nova streaming (English; entity groups) and on `update_options`; Flux only `numbers`; AssemblyAI streaming redaction not in the LiveKit plugin; `LIVEKIT_TELEMETRY_ALLOW_PII=0` for external exporters only; Twilio pause-recording API; Retell "everything / except PII / basic" storage tiers | M | High (insurance) | **P1** | new |
| C11 | Warm transfer with a private briefing ("whisper") | **`WarmTransferTask` (1.8.2 ✓)**: hold audio `HOLD_MUSIC`, consultation room via `CreateSIPParticipant`, agent briefs the human privately, `MoveParticipant` (**Cloud only**), `ToolError` on timeout resumes the caller; Retell `private_handoff_option`, Vapi `warm-transfer-say-summary` | M | High | **P1** | D-V2-14 P2 → **revise to P1** (prebuilt task exists) |
| C12 | Human takeover / supervisor listen-in | Primitives on `main` and 1.8.2: `session.interrupt(force=)`, `session.input/output.set_audio_enabled`, `RoomIO.set_participant/unset_participant`, `participant_kinds`; LiveKit HITL blog pattern; **no official listen-in example (UNVERIFIED)**; Vapi `monitorPlan.listenEnabled/controlEnabled` as the reference | M listen-in / L full audio takeover | High | **P1** | new |
| C13 | Scheduled / outbound campaigns | Needs arq + Redis slots (D-V2-18), C8, DNC list, pacing, time windows; ElevenLabs batch calling, Vapi/Retell as references | L | Medium | P2 | D-V2-14 P2 **confirmed** |
| C14 | SMS / WhatsApp follow-up | `send_sms` P0, `send_whatsapp` P2 in `tools-and-integrations.md` §3.4; ElevenLabs WhatsApp voice notes; Bland SMS/RCS/iMessage | S | High | **P1** | cross-ref |
| C15 | Video avatars (depth) | 15 plugins registered; bey/tavus still `unverified` (D-V2-11); Tavus Raven perception and Sparrow turn-taking are vendor-side | S | Medium | P1 (verify) | D-V2-11 |
| C16 | Vision: document/ID OCR, object counting | Any vision LLM slot + B6 upload; Azure `prebuilt-idDocument` (incl. India DL/PAN/Aadhaar), AWS Textract `AnalyzeID` (US only), Google Document AI Identity, Mistral OCR 3; embeddable IDV (Stripe Identity modal, Persona, Veriff, Entrust iframe with `allow="camera"`) | M | High (FNOL, KYC) | **P1** | new |
| C17 | Screen-share co-pilot | **`RoomOptions(video_input=True)` (1.8.2 ✓)** for Gemini Live / OpenAI realtime: ~1 fps while speaking, 1 frame/3 s idle, 1024² JPEG; Gemini audio+video sessions **2 min without session management**; `gpt-realtime` takes images, not video; Tavus `perception_model: raven-1` | M | Medium | **P1** | cascaded path exists (`vision_inject_per_turn`) |
| C18 | Web browsing / computer use | `livekit-plugins-browser` 0.4.2 catalogued, Phase 3 (ARCHITECTURE-V2 §3); see `tools-and-integrations.md` | L | Low | P2 | confirmed |
| C19 | Multi-agent beyond flows | `AgentTask` (1.8.2 ✓) sub-flows, task groups, LLM supervisor pattern; realtime flows still share one model | M | Medium | P2 | D-V2-13 Phase 2 **confirmed** |
| C20 | Long-term memory | Bland, Tavus, Sesame, Gemini all ship per-caller memory; see `knowledge-and-memory.md` | — | — | per that doc | cross-ref |
| C21 | Evals and simulated callers | **`session.run(...)` + `result.expect…` + `JudgeGroup`/built-in judges (1.8.2 ✓)**; `lk agent simulate text|audio` with `scenarios.yaml` (Cloud); Hamming/Cekura/Roark join rooms over WebRTC; ElevenLabs/Vapi/Retell test suites as references; v3 `chat_start` transport already drives text sessions | M text / L audio | High | **P1** | D-V2-15 P2 → **revise: text sims P1** |
| C22 | A/B testing of prompts | Retell weighted agents per number; ElevenLabs Experiments (deterministic routing, CSAT/containment/latency); LKAP already pins `config_version` per session | M | Medium | P2 | new |
| C23 | Analytics: intent/topic/disposition fields | QA judge exists (`tags`, `sentiment`); Retell post-call fields (Text/Selector/Boolean/Number), ElevenLabs data collection + success criteria; Deepgram intents/topics are English streaming and not in the plugin | S | High | **P1** | extends D-V2-15 |
| C24 | Guardrails and moderation | No audio classifier exists (OpenAI omni-moderation is text+image); pattern = parallel transcript classifier + `session.interrupt(force=True)`; Doheny "observer" via `conversation_item_added` + `update_chat_ctx`; OpenAI Agents SDK realtime debounced output guardrails; Azure Prompt Shields for tool responses; Llama Guard 4 | M | High | **P1** | new |
| C25 | Latency: preemptive generation, fillers, thinking sounds | `preemptive_generation` on by default in `turn_handling`; **`BackgroundAudioPlayer(ambient_sound=, thinking_sound=)` (1.8.2 ✓)**; filler = `say(text, add_to_chat_ctx=False)` before slow tools; Vapi `backgroundSound` defaults `office` on phone | S | High | **P0** | new |
| C26 | LiveKit platform features | Byte streams both ways (B6), RPC 15 KiB, reliable data 15 KiB; Egress incl. **auto-egress on `CreateRoom`** and RTMP/HLS; Agent Insights (Cloud, opt-in, 30-day, `record=` per category); `session.usage`/`SessionReport`; `DuplexModel` (GPT-Live) | S each | Medium | P1 | extends D-V2-16 |

### 4.2 Capability details

#### C1 Multilingual and mid-call switching (P1, M)

- **What.** Detect the caller's language, transcribe code-switched speech (Hindi/English), answer in the same language with a matching TTS voice, and let the caller switch mid-call ("can we do this in Hindi?").
- **Support now.** Deepgram Nova-3 / Flux `language=multi` covers EN, ES, FR, DE, HI, RU, PT, JA, IT, NL and the LiveKit plugin exposes `language` on the constructor and on `SpeechStream.update_options` (plugin source); Gladia `languages=[…], code_switching=True`; Google `detect_language=True`; OpenAI STT `detect_language`, in-place `update_options`. `update_options(language=)` mid-session: Deepgram, Google, OpenAI, Gladia yes; Palabra new streams only; AssemblyAI `language_codes` only; Speechmatics none. The turn detector supports 14 languages and takes per-language `unlikely_threshold`. Realtime: Gemini Live auto-detects (97 vs 99 languages across two Google pages). ElevenLabs `language_code` enforces TTS language except on `multilingual_v2`.
- **LKAP change.** `VoiceConfig.language: str` → `languages: list[str]` (first = default) + `auto_detect: bool` + `voices_by_language: dict[str, ProviderRef|voice id]`; a `switch_language(lang)` built-in that calls `stt.update_options`, swaps the TTS voice and updates the greeting; `user_input_transcribed.language` recorded per turn; captions block B12 shows the detected language. Registry `capabilities.languages` already exists for validation.
- **Risk.** TTS voice swap mid-utterance needs a new `tts` instance in cascaded mode (`Agent(tts=…)` handoff, as flows already do per node).

#### C2 Live translation (P2 captions M, speech L)

- Captions-only translation is a text-stream problem: the worker runs the configured `workflow_llm` (or Palabra/Gladia's own translation) per final segment and publishes on a `lkap.translation` text stream; B12 renders it. Speech-to-speech ("agent speaks Hindi, caller hears English") needs a second TTS voice per target and either Palabra's STT→translation→TTS pair or the LiveKit `live-translated-captioning` pattern extended with TTS; that is a second audio publisher per language and belongs with the avatar layout work. ARCHITECTURE-V2 §3 put "Palabra translation pipelines" in Phase 3; captions can move earlier because they reuse the transcript path.

#### C3 Voice cloning (P2)

- The cheapest, safest version is **listing** cloned voices: `voices_dynamic` catalogs already fetch ElevenLabs/Cartesia voices, and LiveKit Inference custom voices route one `v_…` id across four vendors (dashboard-only creation, consent checkbox, paid plan). Cloning *from the console* means uploading a sample through a vendor adapter (ElevenLabs IVC returns `requires_verification` and uses voice-captcha; Cartesia has no consent text on the endpoint page) and storing a consent attestation. Given the legal exposure (EU AI Act Art. 50(2) marking; vendor terms), recommend catalog-only in P2 and console cloning only with a recorded consent record.

#### C4 Emotion and sentiment (P2)

- Prosody-based emotion needs Hume EVI as the speech engine (no LiveKit realtime plugin; the Hume plugin is TTS-only) or a parallel raw Deepgram socket (`sentiment=true` streams on Nova, English only). The practical S-sized version is a per-turn text classifier on `user_input_transcribed` (workflow_llm, cheap model) that writes `tone` on the `status` stamp and can trigger `escalate_to_human` on sustained distress. Post-call sentiment already exists in `session_qa`.

#### C5 Noise cancellation on telephony (P1, S)

- The registry has the Cloud-only Krisp path; add the telephony-tuned variants (`BVCTelephony()`, `krisp.voice_isolation_telephony()`) and choose them automatically for `sip_*` channels; expose SIP trunk `krisp_enabled`; show the per-minute cost from the price table. ai-coustics is the self-hosted option (vendor key).

#### C6 + C25 Conversation tuning: turn taking, interruptions, latency (P0, S)

- Everything is already accepted by the contract (`turn_handling` passes `turn_detection, endpointing, interruption, preemptive_generation, user_turn_limit`), but the console shows it as raw JSON. Build one "Conversation" screen with the `TurnHandlingOptions` fields and their documented defaults, three presets (`patient`, `balanced`, `snappy`, plus `telephony` which raises `min_duration` and enables BVCTelephony), the detector choice (`v1` hosted vs `v1-mini` local per `turn_detector_mode`), `unlikely_threshold`, and the latency helpers: `VoiceConfig.ambient_sound` / `thinking_sound` (`BuiltinAudioClip` or an uploaded asset), `filler_phrases` (spoken with `add_to_chat_ctx=False` before any tool whose definition sets `announce: "Let me check that"`), and `preemptive_generation.enabled`. The session detail already records EOU→first-audio p50/p95, so the effect is measurable.
- Note: the flat `AgentSession` parameters are deprecated on `main`; LKAP already uses `turn_handling`, so nothing breaks on upgrade.

#### C7 Backchanneling (P2, M)

- Retell ships `enable_backchannel/backchannel_frequency/backchannel_words`; LiveKit has nothing native. A cascaded hack (`say("mm-hmm", add_to_chat_ctx=False)` while `user_state == speaking` for > N s, probability p) risks stepping on the caller and confuses the turn detector. Recommend exposing it only through models that do it natively (`DuplexModel` GPT-Live in 1.8.1+, PersonaPlex), i.e. a registry capability flag, not a feature.

#### C8 Voicemail and AMD (P1, S–M)

- `livekit.agents.AMD` (verified in 1.8.2) classifies within seconds of answer. The docs give Inference-style defaults (`llm="google/gemini-3.1-flash-lite"`, `stt="cartesia/ink-whisper"`); `ink-whisper` is on the fetched Inference STT list, the Gemini model is only implied by an ellipsis there (UNVERIFIED as an Inference model), so LKAP should pass its own resolved `llm`/`stt` slots explicitly and gate the feature on `inference_available` only when the defaults are used. LKAP: `TelephonyConfig.amd: {enabled, on_machine: hangup|leave_message, message: str|None, ivr_detection: bool}`, `calls.amd_result`, a `voicemail` session event, and a webhook `call.voicemail`. This revises D-V2-14's "carrier flag + transcript classifier" plan: the native detector replaces both; carrier AMD stays as an optional attribute passthrough (`sip.twilio.*` attributes exist; Telnyx passthrough UNVERIFIED).

#### C9 Recording consent and AI disclosure (P0, S)

- **Law, primary sources.** EU AI Act Art. 50 (deployers must inform people they interact with AI; emotion-recognition deployers must inform) applies since 2 August 2026 with no deferral for 50(1)/(3); California SB 243 (companion chatbots, private right of action) exempts bots used solely for customer service, but B&P §17941 (the 2019 BOT Act) still requires online disclosure for commercial bots; India's DPDP Rules 2025 phase in notice-and-consent obligations through 14 May 2027. The US two-party-consent state list and TRAI specifics are UNVERIFIED here and need counsel.
- **LKAP change.** `RecordingConfig.require_consent: bool` (Egress starts only after a `consent` event with `kind=recording, accepted=true`), `AgentConfig.disclosure: {enabled, text, position: greeting|banner|both}`, the B5 block, the `consent` session event with `text_hash`, and a workspace default per jurisdiction. On SIP channels the greeting template carries the disclosure; on web the banner is always visible while `show_banner`.

#### C10 PII redaction and storage tiers (P1, M)

- **In the transcript path.** Deepgram `redact` on Nova streaming supports entity groups (`pci|pii|phi|numbers`) in English and is on the plugin's constructor and `update_options` (issue #5683 → PR #5692); Flux only redacts numbers; AssemblyAI streaming redaction is vendor-supported but not in the LiveKit plugin. **In storage.** Copy Retell's three tiers: `full | redacted | basic` for transcripts and events, a post-call scrub job (regex + LLM) for tiers below `full`, and `LIVEKIT_TELEMETRY_ALLOW_PII=0` when an OTLP exporter is set (it does not change what LiveKit Cloud Insights receives — say so in the console). **For payments.** No LiveKit "pause recording" primitive was found (UNVERIFIED); the PCI pattern is `session.input.set_audio_enabled(False)` + DTMF capture with `send_dtmf` masked, or hand off to B11 (Stripe link) — `tools-and-integrations.md` §3.5 lists `collect_card_by_dtmf` as an open question.
- **LKAP change.** `PrivacyConfig {stt_redact: list[str], storage_tier, telemetry_pii: bool, scrub_model: ProviderRef|None}` on `AgentConfig`; the STT factory maps `stt_redact` onto the plugin kwarg when the provider supports it (registry capability `redaction: list[str]`).

#### C11 Warm transfer with a private briefing (P1, M)

- `WarmTransferTask` (verified in 1.8.2, `livekit.agents.beta.workflows`) does the whole recipe: caller on `HOLD_MUSIC`, human dialled into a private consultation room, the agent briefs the human from `chat_ctx` (this **is** the "whisper": only the human hears it), then `MoveParticipant` joins them; timeout resumes the caller. Constraint: `MoveParticipant` is **LiveKit Cloud only**, so self-hosted connections fall back to cold transfer (`sip_enabled` + `cloud_hosting` flags gate it). LKAP: `TelephonyConfig.transfer_targets[].mode: cold|warm`, `transfer_call(mode=)`, B7 shows `connecting → connected`, `calls.status=transferred` plus a `transfer` event with the summary. Beta module: pin the import behind a registry capability and a tripwire test.

#### C12 Human takeover and supervisor listen-in (P1, M/L)

- **Listen-in (M).** The console mints a token for a hidden participant (`canPublish=false`, `hidden=true`) into the session room; the worker's `RoomIO` keeps consuming the caller only (`participant_identity` already set), so a listener never triggers turns. A "Live" tab in the session detail plays the room audio and shows the panel state from the same envelope. **Whisper to the agent (S).** The supervisor types instructions that reach the worker on `lk.chat` with attribute `lkap.role=supervisor`, applied via `update_chat_ctx`/`generate_reply(instructions=…)` (Vapi's `controlEnabled`). **Takeover (L).** A human joins with audio; the worker calls `session.output.set_audio_enabled(False)`, `session.input.set_audio_enabled(False)`, keeps transcribing for the record, and B7 flips to `connected`; on the human leaving it re-enables I/O (the HITL blog's pattern). No official LiveKit example exists (UNVERIFIED), so this needs its own live test. Needs a `human_agent` role or a per-session grant in the role matrix.

#### C13 Campaigns (P2, L) — D-V2-14 confirmed

- Everything Dograh got wrong (issues #745/#737/#777) lives here; do not start before C8, arq/Redis slots and a DNC list exist. Model on ElevenLabs batch calling (CSV, per-recipient variables, scheduling) with pacing, retries, time windows per timezone and a circuit breaker.

#### C14 SMS / WhatsApp (P1) — cross-reference

- Owned by `tools-and-integrations.md` §3.4 (`send_sms` P0 via Twilio/Telnyx, default destination `sip.phoneNumber`; WhatsApp P2). The panel dependency is B11: on voice-only channels a link is sent by SMS instead of shown.

#### C15 Avatars (P1 verify only)

- No new capability; finish D-V2-11's verification (bey/tavus keys), add the avatar layout presets and let B12 captions overlay the video. Tavus-style perception (Raven) is C17 in LiveKit terms.

#### C16 Vision: document and ID scanning (P1, M)

- B6 delivers the bytes; the first implementation is the vision LLM already in the pipeline (`describe_asset(asset_id, task="extract_id_fields", schema=…)` with a JSON schema), which is S. Vendor processors come next as provider-kind `document_ai` entries: Azure `prebuilt-idDocument` covers India DL/PAN/Aadhaar as well as worldwide passports (the best fit for the user's market), AWS `AnalyzeID` is US-only, Google Document AI Identity is US/FR. Identity *verification* (liveness, face match) is not something to build: Stripe Identity's modal (`stripe.verifyIdentity`), Persona's embedded flow and Entrust's iframe (`allow="camera *"`) all fit B11/B17 with a webhook back. Object counting is a plain VLM prompt on a pinned frame.

#### C17 Screen-share co-pilot (P1, M)

- Realtime models see video natively in 1.8.2 through `RoomOptions(video_input=True)` (1 fps while speaking, 1 per 3 s idle, 1024² JPEG, camera or screen track chosen automatically); OpenAI `gpt-realtime` accepts images only; Gemini Live limits audio+video sessions to 2 minutes unless session management/compression is used (Firebase limits page) — a hard constraint to surface in the console. Cascaded agents keep `vision_inject_per_turn`. The co-pilot experience is B9 (`annotation`) over the `user_screen` source plus a "locate" prompt that returns normalised boxes. Pipecat's `ui-snapshot` (send the a11y tree of the app to the LLM) is the right idea for guiding users through *LKAP's own* panel, not through arbitrary screens.

#### C18, C19, C20 — cross-references and confirmations

- Browser/computer use stays Phase 3 (`livekit-plugins-browser` 0.4.2, Python ≥ 3.12) — `tools-and-integrations.md`. Multi-agent beyond flows: `AgentTask` sub-flows and task groups are in 1.8.2; D-V2-13's Phase 2 stands. Long-term memory: `knowledge-and-memory.md`; the panel hook is a `details` block prefilled from memory at `on_enter`.

#### C21 Evals and simulated callers (P1 text, P2 audio)

- Text simulations are nearly free: the v3 MCP chat transport already runs `channel=text` sessions against a real worker, and `livekit.agents.evals.JudgeGroup` with the built-in judges (`task_completion`, `handoff`, `accuracy`, `tool_use`, `safety`, `relevancy`, `coherence`, `conciseness`) is importable in 1.8.2. LKAP: `agents.tests: [{name, persona_instructions, scenario, expectations, mocks}]`, a job that plays the persona with the `workflow_llm` through a text session and judges the transcript, results in `session_qa`-like rows, and a "run tests before publish" gate. Audio simulations (a second persona worker with TTS, interruptions) remain D-V2-15's Phase 2 design; `lk agent simulate audio` exists for Cloud-hosted agents and Hamming/Cekura/Roark join rooms over WebRTC as external runners (register them as "eval provider" connections later).

#### C22 A/B testing (P2, M)

- Sessions already pin `config_version`; add `agents.experiment: {variants: [{config_version, weight}], metric: qa_score|disposition|duration|latency, bucket_by: caller|session}` chosen at `connect`/dispatch (Retell weights per number, ElevenLabs deterministic routing), and an analytics view comparing `session_qa` and latency per version.

#### C23 Post-call analysis fields (P1, S)

- Extend `QaConfig` with `fields: [{name, type: text|number|boolean|select, options?, description}]` (Retell/ElevenLabs shape); the existing worker judge fills them with a JSON schema and one repair retry; results go to `session_qa.raw`, the session detail, webhooks and the CSV export. Deepgram's streaming intents/topics are English-only and outside the plugin, so post-call LLM extraction is the right first step.

#### C24 Guardrails (P1, M)

- Realtime audio cannot be pre-filtered; every vendor runs a parallel classifier on the transcript. LKAP: `GuardrailsConfig {input: [rule], output: [rule], tool_output: [rule], on_trip: interrupt|end_call|escalate, model: ProviderRef|None}` where a rule is a prompt-based classifier (cheap model), a regex, or a provider (OpenAI omni-moderation for text, Azure Prompt Shields for tool responses); the worker hooks `on_user_turn_completed` (input; `StopResponse()` to refuse), `conversation_item_added` (output, debounced like the OpenAI Agents SDK), and the tool result path; on trip it calls `session.interrupt(force=True)`, speaks the configured safe reply and records a `guardrail` event that E2 renders as a chip (the `openai-realtime-agents` pattern).

#### C26 LiveKit platform features worth exposing (P1, S each)

- **Agent Insights** (Cloud): link out from the session detail when `observability_dashboard`; honour `record=` per category to match the storage tier of C10. **Auto-egress** on `CreateRoom` removes the worker-triggered recording start for web sessions (D-V2-16 keeps it for SIP rooms the worker creates). **`DuplexModel`** (GPT-Live, 1.8.1+): a new realtime registry entry with `capabilities.full_duplex=true` (C7). **`session.usage` / `SessionReport`**: replace the deprecated session-level `metrics_collected` consumer before the SDK removes it. **RTMP/HLS egress**: Phase 3 unless a broadcast use case appears.

---

## 5. Generative UI: dynamic component trees vs a fixed catalogue

### 5.1 What the field settled on (2026-09-24)

| Approach | Who | Trust model | State delta |
|---|---|---|---|
| **Fixed client catalogue**, agent supplies data/args | CopilotKit v2 (`useComponent`, tool name → component), Vercel AI SDK 7 (`tool-<name>` message parts with `input-streaming → output-available` states), Google **A2UI** v0.9.1/1.0-rc (catalogue-only components, flat adjacency list, JSON-Pointer `updateDataModel`), Vercel **json-render** (`defineCatalog` + Zod, JSONL of RFC 6902 ops), Microsoft Adaptive Cards 1.6, Pipecat **RTVI** `ui-command` (fixed verbs `scroll_to, highlight, click, set_input_value, toast, navigate, focus`) | Data, not code: the LLM can only name things the client already renders | JSON Patch (json-render, AG-UI), JSON Pointer set (A2UI), whole replace (Adaptive Cards) |
| **Arbitrary markup in a sandbox** | **MCP Apps** (stable spec 2026-01-26, ext id `io.modelcontextprotocol/ui`, `text/html;profile=mcp-app`, JSON-RPC over postMessage, host-built CSP from `_meta.ui.csp`, double-iframe on web, hosts: Claude, ChatGPT, VS Code, Cursor, Goose, Postman…), MCP-UI 7.x (now an MCP Apps SDK; Remote DOM removed — snippet-verified), ChatGPT plugins (renamed from Apps, July 2026; `window.openai` aliases) | Runtime sandbox: iframe + CSP + host-gated tool calls + declared permissions | None; `tool-input-partial`/`tool-result` notifications and a whole-blob `widgetState` |
| **Transport that carries either** | **AG-UI 1.0** (`STATE_SNAPSHOT` + `STATE_DELTA` as RFC 6902, `ACTIVITY_*`, `REASONING_*`, interrupt/resume run outcomes, SSE required + optional length-prefixed protobuf) | Depends on payload spec | RFC 6902 |

Nothing fetched argues for a voice platform to ship arbitrary HTML: the only voice-native protocol (Pipecat RTVI, with a separate `UIWorker` LLM context so screen work never sits on the speech path) and the two newest specs (A2UI, json-render) chose fixed catalogues with pointer/patch state streaming. The dynamic camp's real contribution is **host capability gating and cancellation semantics**, not markup.

### 5.2 Recommendation

**Uphold D-V2-12: the block catalogue stays fixed and typed.** LKAP's envelope (`set/append/remove/upsert` on JSON-pointer paths, seq-ordered, snapshot every 50 patches) is already the shape A2UI and AG-UI converged on. Do three things, in this order, and explicitly do not do a fourth.

1. **Document and test the RFC 6902 mapping (S, P1).** `set` ≙ `replace`/`add`, `append` ≙ `add` to `/-`, `remove` ≙ `remove`, `upsert` ≙ `add` on a keyed object path. Add an `AgentAction`-side and reducer-side adapter that accepts AG-UI `STATE_DELTA` arrays for `/blocks/...` paths. Payoff: an external AG-UI agent (LangGraph, Pydantic AI, ADK, Mastra) can drive LKAP blocks through the worker without a bespoke bridge, and LKAP panels can be rendered by json-render-style hosts. No wire change for existing clients.
2. **Add a composition primitive from the catalogue, not from the model (S, P2).** A `layout` block (`{kind: columns|tabs|accordion, children: [block ids]}`) lets a pack or the agent arrange existing blocks; the children are still typed blocks. This gives most of what "generative layout" buys without letting the LLM emit trees.
3. **Host MCP Apps resources in the `embed` block, `sandbox="app"` (M, P2, gated).** When a tool the agent already uses (via the MCP client path in `tools-and-integrations.md`) ships a `ui://` resource with `text/html;profile=mcp-app`, LKAP can render it in a sandboxed iframe with a CSP built from `_meta.ui.csp`, a dedicated sandbox origin, and a bridge subset: host→view `ui/notifications/tool-input|tool-result|tool-cancelled|size-changed|host-context-changed|resource-teardown`; view→host `ui/initialize`, `ui/message` (→ background user message), `ui/open-link` (→ the existing `navigate` confirm), `ui/update-model-context`, and `tools/call` **only for tools the agent's config already allows**. `tool-cancelled` maps onto barge-in. Costs: a second origin to host, CSP infrastructure, iframe boot latency mid-call, and a review of the "config is public" rule (the resource URI is public, the rendered HTML is the tool server's). Gate behind a workspace flag and per-server allowlist; do not build until a concrete tool with a `ui://` resource is wanted.
4. **Do not adopt LLM-emitted component trees (A2UI/json-render/OpenUI) as a block source.** Per-turn token cost of emitting trees is the latency risk in a call (OpenUI's DSL exists to fight exactly that), it breaks the "state validated by a Pydantic model" rule, and every use case in §3 is covered by a typed block. Revisit only if packs repeatedly need ad-hoc layouts that `layout` (item 2) cannot express.

Two ideas to borrow regardless: Pipecat's **`ui-snapshot`** (the browser sends a compact a11y-style snapshot of the panel so the LLM knows what the caller sees — a `describe_panel` built-in that returns block ids, titles and status fields costs nothing and improves voice/screen coupling), and AG-UI's **interrupt/resume outcomes** for the blocking requests of §3.0 (`cancelled` on barge-in, `interrupt` with a `responseSchema` for the pending block).

---

## 6. Prioritised roadmap — the next 10 things

Ordering rule: compliance and perceived call quality first (they touch every session and are S-sized because the contracts exist), then the blocks that make the insurance pack complete, then the telephony features whose SDK primitives are verified in 1.8.2, then quality and safety tooling. Each item is one or two PLAN-V2-sized packages.

| # | Build | Refs | Effort | Why now |
|---|---|---|---|---|
| 1 | **Conversation tuning screen**: `turn_handling` form with presets, detector choice, thresholds; ambient/thinking sounds; filler phrases per slow tool; preemptive generation toggle | C6, C25 | S | The contract already passes every field; it is the biggest lever on how the agent *feels*, and every competitor exposes it. Measurable with the latency columns that exist. |
| 2 | **Consent and disclosure**: `consent` block, `require_consent` recording gate, disclosure greeting/banner, `consent` event with text hash, jurisdiction defaults | B5, C9 | S | EU AI Act Art. 50 has applied since 2 Aug 2026; insurance calls are recorded; it is cheap and it blocks nothing else. |
| 3 | **Block quartet + generic request**: `request_block`/`block_submit` generalisation, then `choices`, `details`, `markdown`, `steps` (typed `flow_progress`), citation → document preview | §3.0, B1–B4, E1 | M | The most-used in-call elements across all twelve products; the request generalisation makes B6/B8/B15 trivial later; the notebook's "claim details" becomes a generic block. |
| 4 | **Upload block + document/ID extraction**: `lkap.ui.upload` byte stream, `upload` block, `request_upload`, `describe_asset(schema=…)` on the vision LLM, gallery integration | B6, C16 | M | FNOL needs photos and IDs; this is the platform's first client→agent bytes path and unlocks every vision task; Azure `prebuilt-idDocument` covers Indian documents when a vendor processor is added later. |
| 5 | **AMD and voicemail drop** for outbound: `TelephonyConfig.amd`, `calls.amd_result`, `voicemail` event/webhook | C8 | S–M | `livekit.agents.AMD` is in 1.8.2 and runs on LiveKit Inference with no vendor keys; revises D-V2-14 and is the prerequisite for campaigns. |
| 6 | **Warm transfer + handoff block**: `transfer_call(mode=warm)` on `WarmTransferTask`, `HOLD_MUSIC`, private briefing, `handoff` block, Cloud-only gating with cold fallback | C11, B7 | M | The prebuilt task exists in 1.8.2; warm transfer with a briefing is the feature every phone-agent vendor advertises; the block also serves item 7. |
| 7 | **Supervisor listen-in and typed whisper** (takeover as a follow-up): hidden-participant token, "Live" tab in session detail, `lk.chat` supervisor instructions, `handoff` block states | C12, B7 | M (L for audio takeover) | Human-in-the-loop is table stakes for a contact-centre buyer; the primitives exist but need a live test because no official example does. |
| 8 | **Text simulations and judges**: `agents.tests`, persona runner on the v3 text-session transport, `JudgeGroup` verdicts, pre-publish gate, results table | C21 | M | 1.8.2 ships the judges; v3 already drives text sessions; this turns the QA rubric into regression tests. Audio sims stay Phase 2. |
| 9 | **Privacy and analysis**: `PrivacyConfig` (Deepgram `redact`, storage tiers, telemetry PII flag, post-call scrub), `QaConfig.fields` structured extraction | C10, C23 | M | Insurance transcripts are PII-dense; both are configuration over existing paths (STT factory, QA judge). |
| 10 | **Multilingual switching + captions**: `languages[]`, auto-detect, `switch_language`, per-language voices, `captions` block (translation later) | C1, B12 | M | Hindi/English code-switching is the user's market; Deepgram `multi` and `update_options(language=)` are in the plugin; captions reuse the transcript stream. |

Item 4 also carries E3 (the `file` and richer field types of `request_form`), since they ride on the same upload stream.

**Next seven after these (P1/P2):** `annotation` over pinned and live frames plus the screen-share co-pilot path with `RoomOptions(video_input=True)` (B9, E4, C17 — P1, kept out of the ten only because item 4 must land the bytes path first); guardrails with the `interrupt` pattern and a transcript chip (C24, E2); `cards`, `link` (payment/e-sign/IDV) and `slots` blocks with the calendar and Stripe tools from `tools-and-integrations.md` (B8, B10, B11); telephony noise-cancellation variants and Agent Insights link-out (C5, C26); avatar verification and layout presets (C15); the AG-UI `STATE_DELTA` adapter and `layout` block (§5.2 items 1–2); campaigns (C13) once 5 and Redis job slots exist.

**Explicitly deferred with reasons:** backchanneling (no native path outside duplex models), prosody-based emotion (needs Hume as the engine), voice cloning from the console (consent burden; catalog listing suffices), MCP Apps embeds (wait for a concrete `ui://` tool), speech-to-speech translation (second audio publisher), map/chart (no insurance pull yet; tile-provider keys), LLM-emitted component trees (§5.2 item 4).

---

## 7. Open questions for the user

1. **Cloud vs self-hosted for warm transfer.** `MoveParticipant` is LiveKit Cloud only. Is cold-transfer fallback acceptable on self-hosted connections, or should warm transfer be Cloud-only in the UI?
2. **Jurisdiction defaults for disclosure and consent.** Which markets ship first (India, EU, US states)? The doc has primary sources for the EU AI Act, California SB 243/§17941 and India's DPDP Rules, but the US two-party-consent list and TRAI specifics are UNVERIFIED and need counsel before defaults are baked in.
3. **Languages for the insurance pack.** Is Hindi/English code-switching the target (Deepgram `multi` covers it), and are Marathi/Tamil/Telugu required (not in Deepgram's multi set; Gladia/Google would be the path)?
4. **Human agents as principals.** Listen-in and takeover need a role or per-session grant. Add a `human_agent` role to the matrix, or treat supervisors as `builder`+ console users?
5. **Voice cloning appetite.** Catalog-only (list vendor-cloned voices) or console cloning with a stored consent attestation? Which vendor (LiveKit Inference custom voices is dashboard-only today)?
6. **Payments and identity verification.** Should LKAP stay link-only (Stripe Checkout/Identity, Persona, Veriff via B11) and never handle card data in-call (`collect_card_by_dtmf` is an open question in `tools-and-integrations.md`)?
7. **MCP Apps embeds.** Is there a concrete third-party MCP tool with a `ui://` resource the agent should render? If not, item §5.2-3 stays parked.
8. **Map provider.** If `map` is wanted, MapLibre with a tile key or Google Maps JS (billing account)? Workspace-level setting either way.
9. **Simulation runner.** Own persona runner on the text transport (recommended, works on any connection) vs Cloud `lk agent simulate` (cloud-hosted agents only) vs an external vendor (Hamming/Cekura/Roark as a connection)?
10. **Noise-cancellation spend.** Cloud NC/voice isolation is metered after 1,000 minutes ($0.0012/min per the pricing page). Enable by default on SIP channels, or opt-in per agent?
11. **Avatar recording.** Should avatar sessions record video (room composite) rather than audio-only? Affects egress cost and storage.
12. **Release upgrade.** 1.8.3 (2026-09-23) pins `livekit==1.1.18`; flat `AgentSession` params are deprecated on `main`. Upgrade in the same wave as item 1, or hold at 1.8.2 until the tripwire suite is extended?

---

## 8. Sources

Full per-fact citations, including everything marked UNVERIFIED, are in the four verbatim research reports under `docs/research-v4/_sources/` (`livekit-surface.md`, `competitor-in-call-ui.md`, `generative-ui-protocols.md`, `capability-providers.md`). The entries below are the ones this document's claims rest on; all were accessed 2026-09-24.

**Local verification.** `agent/` venv, `livekit-agents 1.8.2`: imports of `AMD`, `beta.workflows.WarmTransferTask`, `BackgroundAudioPlayer`, `BuiltinAudioClip` (incl. `HOLD_MUSIC`), `evals.JudgeGroup`/`accuracy_judge`, `AgentTask`, `mock_tools`, `AgentSession.run/say/interrupt`, `voice.room_io.RoomOptions`/`AudioInputOptions`, `llm.DuplexModel`, `AgentServer`, `inference.TurnDetector` (2026-09-24). Repo files: `contracts/src/lkap_contracts/{blocks,ui_protocol,agent_config,tools}.py`, `agent/src/lkap_agent/{ui/blocks.py,ui/channel.py,session_builder.py,telephony.py,tools/builtin/*}`, `web/src/panels/**`, `docs/v2/{ARCHITECTURE-V2,CONTRACTS-V2,DOGRAH-PARITY}.md`, `docs/research-v2/livekit-plugins-catalog.md`, `docs/research-v4/tools-and-integrations.md`.

**LiveKit.**
- livekit-agents on PyPI (1.8.3, 2026-09-23; `livekit==1.1.18`): https://pypi.org/project/livekit-agents/
- Frontend starters and Agents UI registry: https://docs.livekit.io/agents/start/frontend/ · https://docs.livekit.io/frontends/agents-ui/ · https://github.com/livekit-examples/agent-starter-react · https://docs.livekit.io/agents/start/embed/
- Text streams, data packets, byte streams, RPC: https://docs.livekit.io/home/client/data/text-streams/ · https://docs.livekit.io/home/client/data/packets/ · https://docs.livekit.io/home/client/data/byte-streams/ · https://docs.livekit.io/home/client/data/rpc/ · https://docs.livekit.io/agents/build/text/
- SIP transfers, AMD, attributes: https://docs.livekit.io/sip/transfer-cold/ · https://docs.livekit.io/sip/transfer-warm/ · https://docs.livekit.io/agents/prebuilt/tasks/warm-transfer/ · https://docs.livekit.io/home/server/managing-participants/ · https://docs.livekit.io/telephony/features/answering-machine-detection/ · https://livekit.com/blog/answering-machine-detection · https://docs.livekit.io/sip/outbound-calls/ · https://docs.livekit.io/reference/telephony/sip-participant/
- Egress: https://docs.livekit.io/home/egress/overview/ · https://docs.livekit.io/home/egress/autoegress/ · https://docs.livekit.io/home/egress/outputs/
- Noise cancellation: https://docs.livekit.io/home/cloud/noise-cancellation/ · https://docs.livekit.io/transport/media/noise-cancellation/ · https://pypi.org/project/livekit-plugins-noise-cancellation/ · https://pypi.org/pypi/livekit-plugins-krisp/json · https://livekit.com/pricing
- Turn handling: https://docs.livekit.io/agents/build/turns/turn-detector/ · https://docs.livekit.io/reference/agents/turn-handling-options/ · https://docs.livekit.io/agents/build/turns/ · https://raw.githubusercontent.com/livekit/agents/main/livekit-agents/livekit/agents/voice/agent_session.py
- Background audio, events, workflows, nodes: https://docs.livekit.io/agents/multimodality/audio/background-audio/ · https://docs.livekit.io/agents/build/events/ · https://docs.livekit.io/agents/build/workflows/ · https://docs.livekit.io/agents/logic/supervisor-pattern/ · https://docs.livekit.io/agents/build/nodes/ · https://docs.livekit.io/testing/observability/data/
- Inference models, custom voices, Cartesia emotion: https://docs.livekit.io/agents/models/inference/ · https://docs.livekit.io/agents/models/tts/custom-voices/ · https://livekit.com/blog/voice-cloning-livekit-inference · https://docs.livekit.io/agents/integrations/cartesia/
- Testing, simulations, judges, Insights, Hamming: https://docs.livekit.io/testing/unit-tests/ · https://docs.livekit.io/testing/simulations/ · https://raw.githubusercontent.com/livekit/agents/main/livekit-agents/livekit/agents/evals/judge.py · https://docs.livekit.io/testing/observability/insights/ · https://docs.livekit.io/testing/observability/tracing/ · https://pypi.org/pypi/livekit-plugins-hamming/json
- Translation and multilingual: https://github.com/livekit/agents/tree/main/livekit-plugins/livekit-plugins-palabra · https://docs.livekit.io/agents/models/stt/gladia/ · https://docs.livekit.io/agents/models/stt/ · https://github.com/livekit-examples/live-translated-captioning · https://raw.githubusercontent.com/livekit/agents/main/livekit-plugins/livekit-plugins-deepgram/livekit/plugins/deepgram/stt.py · https://raw.githubusercontent.com/livekit/agents/main/livekit-plugins/livekit-plugins-openai/livekit/plugins/openai/stt.py
- Vision: https://docs.livekit.io/agents/multimodality/vision/video/ · https://developers.openai.com/api/docs/models/gpt-realtime · https://ai.google.dev/gemini-api/docs/live-api/capabilities · https://firebase.google.com/docs/ai-logic/live-api/limits-and-specs
- Human-in-the-loop and guardrail patterns: https://livekit.com/blog/human-in-the-loop-voice-agents · https://livekit.com/blog/observer-pattern-voice-agent-guardrails · https://github.com/livekit-examples/python-agents-examples · https://raw.githubusercontent.com/livekit/agents/main/livekit-agents/livekit/agents/voice/room_io/room_io.py

**Competitor in-call surfaces.**
- OpenAI: https://raw.githubusercontent.com/openai/openai-realtime-agents/main/README.md · https://raw.githubusercontent.com/openai/openai-realtime-agents/main/src/app/components/GuardrailChip.tsx · https://raw.githubusercontent.com/openai/openai-realtime-console/main/client/components/ToolPanel.jsx
- Vapi: https://docs.vapi.ai/chat/web-widget · https://docs.vapi.ai/api-reference/assistants/create · https://docs.vapi.ai/customization/speech-configuration · https://docs.vapi.ai/calls/voicemail-detection · https://docs.vapi.ai/tools/transfer-call · https://docs.vapi.ai/observability/evals-quickstart · https://docs.vapi.ai/observability/simulations-quickstart · https://docs.vapi.ai/security-and-privacy/pci · https://docs.vapi.ai/workflows/overview
- Retell: https://docs.retellai.com/api-references/create-agent · https://docs.retellai.com/build/interaction-configuration · https://docs.retellai.com/build/single-multi-prompt/transfer-call · https://docs.retellai.com/api-references/create-retell-llm · https://docs.retellai.com/test/llm-simulation-testing · https://docs.retellai.com/features/post-call-analysis · https://docs.retellai.com/accounts/privacy-disable · https://docs.retellai.com/deploy/ab-testing · https://docs.retellai.com/deploy/chat-widget
- Bland: https://docs.bland.ai/tutorials/chat-widget · https://docs.bland.ai/tutorials/warm-transfer · https://www.bland.ai/changelog
- Synthflow: https://docs.synthflow.ai/ai-transparency · https://docs.synthflow.ai/call-transfers · https://docs.synthflow.ai/create-a-real-time-booking-action
- Hume: https://dev.hume.ai/docs/speech-to-speech-evi/overview · https://dev.hume.ai/docs/speech-to-speech-evi/faq · https://raw.githubusercontent.com/HumeAI/hume-evi-next-js-starter/main/components/Expressions.tsx · https://dev.hume.ai/docs/text-to-speech-tts/overview
- Tavus: https://docs.tavus.io/sections/conversational-video-interface/overview-cvi · https://docs.tavus.io/sections/conversational-video-interface/pal/screen-share · https://docs.tavus.io/sections/conversational-video-interface/persona/perception · https://docs.tavus.io/sections/conversational-video-interface/interactions-protocols/overview
- Sesame: https://www.sesame.com/blog/voice-your-curiosity · https://techcrunch.com/2025/10/21/sesame-the-conversational-ai-startup-from-oculus-founders-raises-250m-and-launches-beta/
- Intercom: https://fin.ai/voice · https://www.intercom.com/help/en/articles/10697275-deploy-fin-voice
- Google: https://support.google.com/gemini/answer/15274899 · https://blog.google/products-and-platforms/products/gemini/gemini-live-updates-august-2025/ · https://ai.google.dev/gemini-api/docs/live-guide · https://ai.google.dev/gemini-api/docs/live-session · https://blog.google/innovation-and-ai/models-and-research/gemini-models/gemini-3-8-live-gemini-3-8-live-extended-thinking/
- ElevenLabs: https://elevenlabs.io/docs/agents-platform/customization/widget · https://elevenlabs.io/docs/agents-platform/customization/tools/system-tools · https://elevenlabs.io/docs/eleven-agents/customization/agent-testing · https://elevenlabs.io/docs/eleven-agents/operate/experiments · https://elevenlabs.io/docs/eleven-agents/customization/agent-analysis/data-collection · https://elevenlabs.io/docs/eleven-agents/phone-numbers/batch-calls · https://elevenlabs.io/docs/eleven-agents/whatsapp
- Others: https://developers.deepgram.com/docs/voice-agent-feature-overview · https://docs.pipecat.ai/client/rtvi-standard · https://docs.pipecat.ai/pipecat/learn/ui-worker.md · https://cresta.com/ai-agent · https://www.parloa.com/knowledge-hub/pii-redaction-ai/ · https://docs.poly.ai/call-handoff/introduction · https://docs.poly.ai/integrations/pci-pal

**Generative-UI protocols.**
- AG-UI 1.0: https://docs.ag-ui.com/spec/1.0/events/state.md · https://docs.ag-ui.com/concepts/interrupts.md · https://docs.ag-ui.com/concepts/generative-ui-specs.md · https://docs.ag-ui.com/spec/1.0/basic/transports/http-protobuf.md · https://github.com/ag-ui-protocol/ag-ui
- CopilotKit: https://docs.copilotkit.ai/concepts/which-hook · https://docs.copilotkit.ai/generative-ui · https://docs.copilotkit.ai/human-in-the-loop
- Vercel AI SDK 7: https://ai-sdk.dev/docs/ai-sdk-ui/generative-user-interfaces · https://ai-sdk.dev/docs/ai-sdk-ui/chatbot-tool-usage · https://vercel.com/changelog/ai-sdk-7
- MCP Apps / MCP-UI / ChatGPT plugins: https://modelcontextprotocol.io/extensions/apps · https://github.com/modelcontextprotocol/ext-apps/blob/main/specification/2026-01-26/apps.mdx · https://modelcontextprotocol.io/extensions/client-matrix · https://blog.modelcontextprotocol.io/posts/2026-01-26-mcp-apps/ · https://mcpui.dev/guide/protocol-details · https://developers.openai.com/plugins/build/chatgpt-ui · https://developers.openai.com/plugins/changelog
- A2UI, json-render, OpenUI, Adaptive Cards: https://a2ui.org/specification/v0.9.1-a2ui/ · https://a2ui.org/specification/v1.0-a2ui/ · https://github.com/vercel-labs/json-render · https://json-render.dev/docs/streaming · https://github.com/thesysdev/openui · https://learn.microsoft.com/en-us/microsoft-copilot-studio/adaptive-cards-overview

**Capability providers and law.**
- Multilingual/translation: https://developers.deepgram.com/docs/multilingual-code-switching · https://docs.gladia.io/chapters/language/code-switching · https://support.gladia.io/article/how-to-combine-real-time-transcription-with-translation · https://docs.speechmatics.com/speech-to-text/features/translation · https://docs.cloud.google.com/speech-to-text/docs/models/chirp-3 · https://elevenlabs.io/docs/overview/models
- Voice cloning: https://elevenlabs.io/docs/api-reference/voices/ivc/create · https://elevenlabs.io/docs/api-reference/text-to-voice/design · https://docs.cartesia.ai/api-reference/voices/clone
- Emotion/sentiment: https://developers.deepgram.com/docs/sentiment-analysis · https://developers.deepgram.com/docs/intent-recognition · https://www.assemblyai.com/docs/audio-intelligence/sentiment-analysis · https://www.hume.ai/expression-measurement-api
- NC: https://raw.githubusercontent.com/livekit/agents/main/livekit-plugins/livekit-plugins-krisp/README.md · https://elevenlabs.io/docs/overview/capabilities/voice-isolator
- Backchannel / duplex: https://github.com/NVIDIA/personaplex
- AMD: https://www.twilio.com/docs/voice/answering-machine-detection · https://developers.telnyx.com/docs/voice/programmable-voice/answering-machine-detection
- Law: https://artificialintelligenceact.eu/article/50/ · https://www.goodwinlaw.com/en/insights/publications/2026/08/alerts-technology-dpc-eu-ai-act-transparency-obligations-now-in-force · https://leginfo.legislature.ca.gov/faces/billTextClient.xhtml?bill_id=202520260SB243 · https://leginfo.legislature.ca.gov/faces/codes_displaySection.xhtml?lawCode=BPC&sectionNum=17941 · https://static.pib.gov.in/WriteReadData/specificdocs/documents/2025/nov/doc20251117695301.pdf
- PII/PCI: https://developers.deepgram.com/docs/redaction · https://github.com/livekit/agents/issues/5683 · https://www.assemblyai.com/docs/streaming/pii-redaction · https://www.twilio.com/docs/voice/api/recording · https://www.twilio.com/docs/voice/pci-workflows
- Transfer/whisper: https://www.twilio.com/docs/voice/twiml/number · https://docs.livekit.io/telephony/features/transfers/warm/
- Evals: https://hamming.ai/integrations/livekit · https://docs.cekura.ai/documentation/integrations/livekit-integration · https://docs.roark.ai/ · https://docs.coval.ai/
- Guardrails: https://developers.openai.com/api/docs/guides/moderation · https://openai.github.io/openai-agents-python/realtime/guide/ · https://learn.microsoft.com/en-us/azure/ai-services/content-safety/concepts/jailbreak-detection · https://huggingface.co/meta-llama/Llama-Guard-4-12B · https://github.com/NVIDIA-NeMo/Guardrails
- Latency: https://docs.livekit.io/agents/logic-structure/sessions/ · https://elevenlabs.io/docs/eleven-api/guides/how-to/best-practices/latency-optimization · https://docs.cartesia.ai/build-with-cartesia/tts-models/latest
- Vision/ID: https://docs.aws.amazon.com/textract/latest/dg/how-it-works-identity.html · https://learn.microsoft.com/en-us/azure/ai-services/document-intelligence/prebuilt/id-document?view=doc-intel-4.0.0 · https://cloud.google.com/solutions/identity-doc-ai · https://docs.mistral.ai/models/ocr-3-25-12 · https://docs.stripe.com/identity/verify-identity-documents?platform=web&type=modal · https://docs.withpersona.com/docs/embedded-flow · https://documentation.identity.entrust.com/sdk/sdk-iframe-guide

**Still UNVERIFIED after this pass** (kept from the source reports; do not plan against): release notes of livekit-agents 1.8.3; a LiveKit-native "whisper" audio path and any official listen-in example; `sip.h.*` attributes; runtime `update_options(language=)` for Speechmatics; Hume's "48 emotions" figure; ElevenLabs backchannel; Vapi A/B and filler injection; Bland voice cloning and "Citrus"; Retell voicemail field names; the US two-party-consent list; Utah SB 226 text; TRAI rules; Google Media Translation deprecation; MCP-UI `prompt/intent/notify/link` payload shapes; Open-JSON-UI; A2UI 1.0 stable date; whether Egress can be paused (no page found).
