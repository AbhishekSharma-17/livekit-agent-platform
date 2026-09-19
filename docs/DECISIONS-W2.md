# LKAP — Wave 2 decisions (D-W2-1 … D-W2-13)

Status: **binding.** Written by the architect after Wave 0/1 landed and Wave 2 started (2026-09-18); D-W2-9p and D-W2-10 … D-W2-13 were added the same day after W2-AGENT-INTEGRATION reported Stages 0–8 green. For every item below this file wins over `ARCHITECTURE.md`, `CONTRACTS.md`, `INSURANCE_PACK_MAPPING.md`, `IMPLEMENTATION_PLAN.md` and `LIVE_TEST_PLAN.md`; §D-W2-9 lists the exact sections it supersedes, and each later decision names its own. Implementers apply these without re-deciding; if a decision is empirically wrong (the SDK refuses it), stop and report with the error text.

Precedence of documents from now on: `DECISIONS-W2.md` > `CONTRACTS.md` > `ARCHITECTURE.md` > `INSURANCE_PACK_MAPPING.md` > `IMPLEMENTATION_PLAN.md` > code comments. Research fact sheets (`../docs/research/*.md`, moved to the repo root) are evidence, not contracts.

Verified inputs these decisions rely on (checked against the installed `livekit-agents==1.8.2` in `agent/.venv` on 2026-09-18):

- `RoomOptions` (`voice/room_io/types.py`) has `participant_identity`, `video_input`, `text_input`, `close_on_disconnect`, `participant_kinds`; RoomIO reads `room.local_participant` at start, so `ctx.connect()` must precede `session.start()`.
- RoomIO's single video input (`_ParticipantVideoInputStream`) accepts `SOURCE_CAMERA` and `SOURCE_SCREENSHARE`; `_on_track_available` **closes the previous stream and switches to the newest track** ("last available track wins"); on unsubscribe/unpublish it falls back to any remaining publication of the linked participant.
- `llm.ImageContent.image: str | rtc.VideoFrame` — a JPEG data URL is accepted; the in-source example encodes with `EncodeOptions(format="JPEG", resize_options=ResizeOptions(width=512, height=512, strategy="scale_aspect_fit"))`.
- `AgentSession` emits `"close"` with `CloseEvent(reason=...)`; `JobContext.add_shutdown_callback` accepts `Callable[[str], Coroutine]`.
- The worker's dispatch name is resolved once, inside `AgentServer.rtc_session()` (`worker.py:511-521`), with precedence **`LIVEKIT_AGENT_NAME_OVERRIDE` env → explicit `agent_name=` argument → `LIVEKIT_AGENT_NAME` env → `""`** (automatic dispatch). `Settings.livekit_agent_name` is **not** consulted by the SDK. The SDK comment describes the override as "a platform-injected force, e.g. from the lk simulation launcher"; whether LiveKit Cloud sets it for deployed agents is not decidable from the SDK. (Corrected by D-W2-11; the earlier "env only" wording was incomplete.)
- Typed chat: `RoomOptions.text_input` is `NotGivenOr[TextInputOptions | bool]`; not given → enabled with `_default_text_input_cb` (`room_io/types.py:46`), which does `async with sess._claim_user_turn(): await sess.interrupt(); sess.generate_reply(user_input=ev.text)` — it never calls `Agent.on_user_turn_completed`. `_claim_user_turn`'s docstring says "Use in custom `text_input_cb` or any flow that drives a user turn across awaits". `generate_reply(user_input=ChatMessage, chat_ctx=...)` is public; `_pipeline_reply_task` inserts `new_message` into the given context and, once the speech is scheduled, into the agent's persisted `_chat_ctx` — the same persistence the audio path gets. RoomIO wraps the callback in `try/except Exception` and only logs (`room_io.py:520-529`).
- `JobRequest.agent_name` (`job.py:1074`) is public; `rtc_session(on_request=...)` replaces `_default_request_fnc` (which just `accept()`s), and a request function that neither accepts nor rejects makes the SDK reject the job.
- `ModelSpec.supports_video` already exists in the registry (set on the Gemini Live models) and is exported to `web/src/contracts/lkap-contracts.d.ts`; `ModelCombobox` already renders it.
- `cli.run_app`: the first SIGINT/SIGTERM only schedules the exit; `dev` mode then calls `server.aclose()` directly, `start` mode runs `server.drain()` (up to `drain_timeout`) first; a second signal calls `os._exit(1)`. Hot reload exists only through the `lk` CLI dev channel (`--cli-addr` → `WatchClient`), not in `python -m lkap_agent.main dev`.
- `ErrorEvent(error=..., source=LLM | STT | TTS | RealtimeModel | ...)` identifies which component failed.
- The avatar worker participant carries attribute `lk.publish_on_behalf=<agent identity>` (`types.ATTRIBUTE_PUBLISH_ON_BEHALF`).
- Realtime plugins (google, openai) do not set `capabilities.supports_say`; `say()` without a TTS raises.
- `web/src/app/api/console/[...path]/route.ts` forwards any `/api/console/<path>` to `/v1/<path>` with `X-Admin-Token` from server env; `POST /v1/agents/{id_or_slug}/connect` already accepts an admin token for unpublished agents.

---

## D-W2-1 — Test call on an unpublished agent: console-proxied test mode (no new token type)

**Decision.** The session page gets a `?mode=test` variant that routes its two API calls through the web app's existing admin proxy instead of the public API. No new endpoint, no cookie, no signed token, no auto-publish.

**Rationale.** The web app is already the trust boundary under D14 (anyone who can reach `/s/…` can reach `/api/console/*`), so test mode adds zero exposure while reusing code that exists; a signed token adds an api endpoint, a contracts model and a URL-borne secret for no security gain in the MVP; auto-publish would silently expose a draft agent to the public route.

**Ownership.** New package **W2-TEST-CONNECT** (Sonnet), see IMPLEMENTATION_PLAN.

**Implementation.**

1. `web/src/lib/livekit.ts`
   - Add `export interface SessionAccess { viaConsole: boolean }` (default `{ viaConsole: false }`).
   - `fetchConnect(slug, request, access?)`: when `access.viaConsole` the URL is the **relative** `/api/console/agents/${encodeURIComponent(slug)}/connect` (same origin; the proxy attaches `X-Admin-Token`); otherwise unchanged (`${publicApiBaseUrl()}/v1/agents/${slug}/connect`).
   - `createConnectTokenSource(slug, handlers, access?)` threads `access` into `fetchConnect`.
   - Add `export function toPublicAgent(agent: AgentOut): AgentPublicOut` = `{ id, slug, name, description, ui_panel_id, capabilities: agent.config.capabilities, pipeline_mode: agent.config.pipeline.mode }`. (`ConnectResponse.agent` is already `AgentPublicOut`; the mapper is only for the pre-connect card.)
   - Add `export async function fetchAdminAgentServerSide(slug): Promise<AgentPublicOut>` used **only from server components**: `fetch(`${publicApiBaseUrl()}/v1/agents/${slug}`, { headers: { "X-Admin-Token": process.env.LKAP_ADMIN_TOKEN ?? "" }, cache: "no-store" })` → `toPublicAgent`. It lives in a separate module `web/src/lib/livekit-server.ts` whose first line is `import "server-only";` (a helper that reads `LKAP_ADMIN_TOKEN` must be impossible to import client-side); the public helpers stay in `livekit.ts`.
2. `web/src/app/(session)/s/[slug]/page.tsx`: accept `searchParams: Promise<{ mode?: string }>`; `const testMode = mode === "test"`; `loadAgent(slug, testMode)` calls `fetchAdminAgentServerSide` in test mode, else `fetchPublicAgent`; pass `testMode` down.
3. `SessionExperience` → `LiveSession` accept `testMode: boolean`; `LiveSession` calls `createConnectTokenSource(slug, handlers, { viaConsole: testMode })`. `PreCallCard` shows a small "Test mode — draft agents allowed" badge when `testMode`.
4. Console links: `web/src/components/console/agents/agents-table.tsx` and `web/src/components/console/agents/agent-editor.tsx` link to `/s/${agent.slug}?mode=test`.
5. Errors: `describeConnectError` unchanged; in test mode a 403 can only mean the proxy's `LKAP_ADMIN_TOKEN` is wrong — map `status === 401 || 403` with `viaConsole` to "The console's admin token is not accepted by the API." in `LiveSession`.
6. Token lifetime: the participant JWT stays the api-minted 2 h token from `mint_participant_token`; nothing else is minted. Nothing is sent by the console except the same `ConnectRequest`.
7. Tests (vitest): `toPublicAgent` mapping; `createConnectTokenSource(..., { viaConsole: true })` posts to `/api/console/agents/<slug>/connect` (mock `fetch`); page renders the badge. No api changes, no contracts changes.

**Supersedes** ARCHITECTURE §11 ("`test-connect`" in the admin list) and §12 ("opens `/s/[slug]` with admin token cookie").

---

## D-W2-2 — Orphan session rows: freeze after the first connect + api-side stale sweep

**Decision.** (a) Client: a `TokenSource` created by `createConnectTokenSource` performs **exactly one** `POST /connect` in its lifetime — it freezes itself as soon as the first connect resolves, not only on unmount. (b) API: a background sweep marks stale rows `failed` so rows that still slip through (browser closed before joining, worker crash) never stay `created`/`active` forever.

**Rationale.** The extra row comes from `useSession`'s unexpected-disconnect handler force-refetching the token *while the component is still mounted* — the existing `freeze()` in the unmount cleanup runs too late for a server-initiated hangup; freezing on first success closes that path with two lines. A sweep is still needed because a token can be minted and never used.

**Implementation.**

1. `web/src/lib/livekit.ts` (`createConnectTokenSource`): inside the custom callback, after `last = await request;` set `frozen = true;`. Keep the exported `freeze()` (harmless, still called on unmount). Add a vitest: calling the token source twice (second with `force`) performs one fetch and returns identical credentials. Owner: **W2-TEST-CONNECT** (it already edits this file). **Amended by D-W3-2:** `freeze()` is a no-op until a connect has been attempted.
   - Consequence to document in the file header: after a *full* reconnect the same token/room is replayed; if the agent job has already closed (`close_on_disconnect`), the room is gone and the page shows "Call ended" — the visitor starts a new call (new `sessionId`). This is the intended MVP behaviour ("one attempt == one session").
2. `api/src/lkap_api/main.py` lifespan: start `asyncio.create_task(sweep_loop(app.state.db, settings))`, cancelled on shutdown. `api/src/lkap_api/sessions_sweep.py` (new): every `LKAP_SESSION_SWEEP_INTERVAL_S` (default 60) run one UPDATE per rule:
   - `status='created' AND created_at < now - LKAP_SESSION_STALE_CREATED_S` (default 600) → `status='failed', error='never started', ended_at=now`.
   - `status='active' AND started_at < now - LKAP_SESSION_STALE_ACTIVE_S` (default 21600) → `status='failed', error='summary never received', ended_at=now`.
   - A later `PUT /internal/v1/sessions/{id}/summary` for a swept row still overwrites status/usage/transcript (the worker is the authority when it does show up). `GET /internal/…/resolved` on a swept row keeps returning 409 (already "ended/failed"); that is correct — a dispatch older than 10 min is a replay.
   - Settings: three new optional `LKAP_SESSION_*` ints in `api/src/lkap_api/settings.py`; CONTRACTS §3 table gains them (api, opt).
   - Tests: `api/tests/test_sessions_sweep.py` with injected `now`. Owner: **W3-POLISH** (already owns `sessions.py`).
3. Console: no filtering; `failed` rows with those two error strings render with a muted "never started" chip (W3-POLISH).

---

## D-W2-3 — Generated TS for `Any` fields: fix the exporter (`tsType: "unknown"`), not the consumers

**Decision.** `lkap_contracts.export.prepare_for_typescript` injects `"tsType": "unknown"` into every property schema that is *empty* (no `type`, `$ref`, `anyOf`/`oneOf`/`allOf`, `properties`, `items`, `enum`, `const`). `json-schema-to-typescript` honours `tsType`, so `UiPatchOp.value` becomes `value?: unknown`; `ErrorBody.details` (`object | None`) is fixed by the same rule. Web's local widening (`UiPatchOpAny`, `UiPatchAny` in `web/src/lib/ui-state.ts`) stays as a deprecated alias equal to the generated type until W3-POLISH deletes it.

**Rationale.** The contract says `Any`; the generated type must say `unknown`. Fixing it at the generator keeps "TS types are generated, never hand-written" true and removes the same defect from every other `Any`/`object` field at once.

**Ownership.** New package **W2-CONTRACTS-FIX** (Sonnet).

**Implementation.**

1. `contracts/src/lkap_contracts/export.py`: add `_mark_untyped_as_unknown(node)` applied inside `prepare_for_typescript` after `_pure_refs`: walk `definitions[*].properties[*]` (and nested `properties`/`items`) and set `tsType = "unknown"` on schemas whose key set, after removing `default`, `description`, `title`, `examples`, is empty. Do **not** touch the committed JSON Schemas (`generated/schemas/*.schema.json`), only the TS input.
2. Regenerate: `cd contracts && uv run python -m lkap_contracts.export && ../scripts/export_contracts.sh` (copies to `web/src/contracts/`). Commit both generated files.
3. `contracts/tests/test_export.py`: assert the generated `.d.ts` contains `value?: unknown;` inside `interface UiPatchOp` and `details?: unknown;` inside `interface ErrorBody`.
4. `contracts/src/lkap_contracts/py.typed`: add the empty PEP 561 marker (the agent pyproject carries a `follow_untyped_imports` override because it is missing). W2-AGENT-INTEGRATION removes the override from `agent/pyproject.toml` afterwards; api/packs remove theirs if present.
5. `contracts/src/lkap_contracts/api_models.py`: `InternalKbSearchRequest.k: int = Field(4, ge=1, le=20)` and `KbSearchRequest.k` likewise (supports D-W2-5; schema regen included in step 2).
6. `web/src/lib/ui-state.ts`: after the regenerated file is copied, change the aliases to `export type UiPatchOpAny = UiPatchOp; export type UiPatchAny = UiPatch;` with a `@deprecated` JSDoc (W2-CONTRACTS-FIX may edit this one file for that purpose only); `pnpm typecheck` must stay green.

**Supersedes** nothing; CONTRACTS §4 "Export" paragraph gains one sentence about `tsType`.

---

## D-W2-4 — Video source preference: the FrameBuffer honours the UI's selection; realtime relies on RoomIO's last-track-wins

**Decision.** `FrameBuffer` gains an explicit *preferred source*. `set_video_source` (UI → agent RPC) sets it; `latest()`/`latest_jpeg()` return the preferred source's frame when one is fresh enough and fall back to the freshest frame of any source otherwise. In realtime mode nothing else changes: RoomIO already switches the model's video input to the newest published track and falls back when it stops (verified), so the UI's one-video-source rule plus the RPC keeps both modes deterministic.

**Rationale.** The buffer is the single frame source for `pin_frame`, `describe_current_frame` and cascaded injection, so the preference must live there rather than in `userdata`; the fallback keeps the ≤ 12 s / ≤ 8 s freshness contract meaningful during the second or two when a switch has unsubscribed one track and the other has not produced a frame yet.

**Ownership.** W2-AGENT-INTEGRATION (`agent/src/lkap_agent/vision.py`, `agent/src/lkap_agent/main.py`, `agent/tests/unit/test_vision.py`).

**Implementation.**

1. `vision.py`: `FrameBuffer.__init__` adds `self._preferred: FrameSource | None = None`; new method `set_preferred_source(self, source: FrameSource | None) -> None` (logs at DEBUG). `latest(max_age_s)`: compute fresh candidates as today; if `self._preferred` has a fresh candidate return it; else return the freshest candidate; `None` when there are none. `latest_jpeg` unchanged (delegates). Not added to `packs.base.FrameBufferProto` — packs never need it; `main.py` reaches it via `getattr(frames, "set_preferred_source", None)` exactly like `start`/`stop`.
2. `main.py` `_assemble._on_set_video_source(source)`: `mapped = source if source in ("camera", "screen") else None`; call `set_preferred_source(mapped)` when the frame buffer has it; keep `cell[0].userdata["video_source"] = source` for packs.
3. Mid-call switching, both modes: the browser already disables the other track before enabling the new one and sends `set_video_source` after the local track state changes (`session-room.tsx`). Realtime: RoomIO switches the model's input on the new `track_subscribed` (last-track-wins) and, when screen share stops, falls back to the camera publication if still published. Cascaded: the next user turn picks the preferred source per rule 1; `describe_current_frame` and `pin_frame` do the same immediately.
4. Tests: preferred source returned when fresh; fallback to the other source when the preferred one is stale/absent; `None` preference restores freshest-wins.

**Supersedes** ARCHITECTURE §8 bullet 1 ("Active source = screen share if published, else camera") — the rule is now "UI-selected source, else freshest".

---

## D-W2-5 — KB `top_k` precedence: explicit `k` wins; platform callers always pass the agent's `knowledge.top_k`; the api clamps

**Decision.** Precedence for the number of hits is: explicit `k` given by the caller → the contract default `4` in `KbClient.search`; the platform's own callers (`search_knowledge` tool, `PlatformAgent._inject_knowledge`) always pass `k=ctx.config.knowledge.top_k`; the api enforces `1 ≤ k ≤ 20` on both `/internal/v1/kb/search` and `/v1/knowledge-bases/{id}/search`. `ApiKbClient` loses its dead `default_k` fallback.

**Rationale.** The reported gap is stale — `search_knowledge.py` already passes `top_k` — the real defect is `ApiKbClient.search(query, k=4, …)` doing `k or self._default_k`, where the truthy default `4` makes the config value unreachable for any caller that omits `k`. Changing the `KbClient` Protocol signature would break `fake_ctx.py` and the in-flight pack package under `mypy --strict`, so the fix stays inside the implementation and the api.

**Implementation.**

1. `agent/src/lkap_agent/config_client.py` `ApiKbClient`: drop `default_k`; `search(query, k=4, kb_ids=None)` passes `k` through unchanged. `main.py` stops passing `default_k=…`. Packs that want the agent setting pass `k=ctx.config.knowledge.top_k` (document in `packs/base.py` docstring of `KbClient.search`, one line). Owner: W2-AGENT-INTEGRATION.
2. `contracts` `InternalKbSearchRequest.k` / `KbSearchRequest.k`: `Field(4, ge=1, le=20)` (D-W2-3 step 5). `api/src/lkap_api/routers/knowledge.py` and `routers/internal.py` rely on Pydantic for the clamp — no extra code and nothing for W2-API-KB to do; W2-CONTRACTS-FIX adds a contracts model test that `InternalKbSearchRequest(k=0)` and `(k=50)` raise `ValidationError`.
3. `search_knowledge` and `_inject_knowledge` unchanged (they already pass `top_k`).

---

## D-W2-6 — LiveKit Inference credentials come from the process environment, everywhere

**Decision.** Every `livekit.agents.inference.*` object (`STT`, `LLM`, `TTS`, `TurnDetector`) is constructed **without** `api_key`/`api_secret`; the SDK reads `LIVEKIT_API_KEY`/`LIVEKIT_API_SECRET` from env. `_workflow_model` and `tests/live/test_inference_smoke.py` are changed to match `ProviderFactory` and `speak_fixed_line`, which already follow this rule.

**Rationale.** The factory already strips `api_key`/`api_secret` from Inference providers (`_INFERENCE_FORBIDDEN_KWARGS`) because Inference is billed to the worker's own project, and `Settings` fails fast at startup when the two env vars are missing — so "env" is the convention the codebase has already voted for; explicit creds in two places just create a second way to be wrong (e.g. an override that differs from what the worker registered with).

**Implementation (W2-AGENT-INTEGRATION).**

1. `agent/src/lkap_agent/main.py` `_workflow_model(providers, settings)` → `_workflow_model(providers)` returning `inference.LLM(WORKFLOW_FALLBACK_LLM_MODEL)` in the fallback branch; update the call site and docstring.
2. `agent/tests/live/test_inference_smoke.py` `_inference_llm()` → `inference.LLM(LLM_MODEL)` (the `skipif` already requires the env vars).
3. Unit tests that patch `inference.LLM` keep working; `test_main` asserts the fallback is constructed with the model only (no `api_key` kwarg).
4. Vendor (non-Inference) providers are unchanged: explicit constructor kwargs from the resolved config, never env (ARCHITECTURE §6 factory rule stands).

---

## D-W2-7 — Avatar rooms: the worker targets the browser by the api-minted identity; the browser targets the agent by excluding the on-behalf participant

**Decision.** The browser participant's identity is decided by the api at connect time and already travels in `DispatchMetadata.participant_identity` → `ResolvedAgentConfig.participant_identity`. The worker passes it as `UiChannel(ui_identity=…)` (the constructor parameter exists and is currently unused) so `request_ui` always RPCs the browser, never the avatar worker participant. The `remote_participants` fallback in `_remote_identity()` skips any participant that has the `lk.publish_on_behalf` attribute or `kind == AGENT`. Symmetrically, the web `useAgentRpc` must resolve the **agent** identity as the remote participant of kind `AGENT` that does **not** carry `lk.publish_on_behalf`.

**Rationale.** Identity-by-construction beats identity-by-discovery: the api already owns the identity, the same value already drives `RoomOptions(participant_identity=…)` and `FrameBuffer(participant_identity=…)`, so using it for RPC removes the only place where an avatar's second participant could be picked up. The web-side rule is the mirror image; whether `useVoiceAssistant().agent` already excludes the on-behalf participant is unverified, so the ladder's avatar stage checks it first.

**Implementation.**

1. `agent/src/lkap_agent/main.py` `_wire_optional_modules._make_ui(...)`: accept `ui_identity: str | None = None` and pass it to `UiChannelImpl(...)`; `_assemble` passes `ui_identity=resolved.participant_identity`. Broadcast topics (`lkap.ui.state/activity/asset`) stay room-wide (no `destination_identities`) — the avatar participant ignores them. Owner: W2-AGENT-INTEGRATION.
2. `agent/src/lkap_agent/ui/channel.py` `_remote_identity()`: when `_ui_identity` is `None`, iterate `self._room.remote_participants.values()` and return the first whose `attributes.get("lk.publish_on_behalf")` is falsy and whose `kind != rtc.ParticipantKind.PARTICIPANT_KIND_AGENT`; raise the existing `RuntimeError` otherwise. Unit test with `FakeRoom` holding an avatar-like participant first. Owner: W2-AGENT-INTEGRATION (trivial edit outside its file list; note it in the report).
3. `web/src/hooks/useAgentRpc.ts`: compute `agentIdentity` from `room.remoteParticipants` (via `useRemoteParticipants()`): `p.kind === ParticipantKind.Agent && !p.attributes["lk.publish_on_behalf"]`; fall back to `useVoiceAssistant().agent?.identity`. Owner: W3-E2E-INSURANCE, done at ladder stage 10 (avatar) only if the check there fails; otherwise record "verified: `useVoiceAssistant` resolves the agent" in RUNBOOK and leave the hook alone.

---

## D-W2-8 — Vision without a realtime model (cascaded injection rules)

**Decision.** Cascaded pipelines see the camera/screen through exactly one path per user turn plus one explicit tool, under these rules:

| # | Rule |
|---|---|
| R1 | Applies only when `pipeline.mode == "cascaded"`, `capabilities.camera or capabilities.screen_share`, and `capabilities.vision_inject_per_turn` (default `true`). Realtime mode is unchanged: `RoomOptions(video_input=True)` + default `VoiceActivityVideoSampler(speaking_fps=1.0, silent_fps=0.3)`. |
| R2 | At most **one** frame per completed user turn, taken from `frames.latest(max_age_s=LKAP_VISION_MAX_FRAME_AGE_S)` (default 8 s), honouring the D-W2-4 source preference. No frame → no injection, no note. |
| R3 | The frame is **encoded before it is attached**: `encode(frame, EncodeOptions(format="JPEG", resize_options=ResizeOptions(width=512, height=512, strategy="scale_aspect_fit")))` in `asyncio.to_thread`, wrapped as `ImageContent(image="data:image/jpeg;base64,…", inference_detail="low")`. Never attach a raw `rtc.VideoFrame` to a persisted message (each keeps megabytes of pixel buffer alive for the whole session). |
| R4 | Context bound: before appending, strip every `ImageContent` from earlier `ChatMessage`s in `turn_ctx.items` (the per-turn copy), so an LLM call carries **at most one image**. `new_message` (persisted) keeps its data URL (~20–40 KB); transcript export already drops images (`text_content`). |
| R5 | Auto-degrade: `PlatformAgent` remembers `_last_turn_had_frame`; a synchronous `session.on("error")` handler that sees `isinstance(ev.source, llm.LLM)` (or `ev.error` an `LLMError`) while that flag is set sets `_vision_disabled = True`, logs a warning with the model id, and records an `error` event `{"message": "vision injection disabled: <error>"}` via the observer. `describe_current_frame` stays available (it is explicit and may still fail loudly). |
| R6 | Source switches need nothing extra: the next turn takes the preferred source (D-W2-4). Between turns the model asks with `describe_current_frame(question)`. No text marker is added to the user message (it would leak into `text_content`, the transcript and `user_turn` events). |
| R7 | Cost/latency guardrails: `inference_detail="low"` and ≤ 512 px keep each image at a few hundred input tokens; one image per call (R4) bounds growth; `capabilities.vision_inject_per_turn=false` turns the per-turn path off entirely while keeping `pin_frame`/`describe_current_frame`. Encoding runs off the event loop (R3) so it does not delay end-of-turn detection. |
| R8 | **Amended by D-W2-10.** Stage 7b showed `google/gemma-4-31b-it` *silently ignores* images on LiveKit Inference (no error, so R5 never fires) and `google/gemini-3.5-flash` answers correctly. Vision capability is now registry data (`ModelSpec.supports_video`) and R1 gains a tri-state model gate: known vision-capable → inject; known text-only → skip injection (and `describe_current_frame` refuses); unknown model id → inject and rely on R5. |

**Rationale.** R3/R4 are what stop a 30-minute call from either exhausting memory (raw frames in history) or re-sending every past frame on every LLM call; R5 is what keeps one text-only model from failing every turn; the rest pins the behaviour that W1 implemented so the pack and the tests can rely on it.

**Implementation (W2-AGENT-INTEGRATION).** `agent/src/lkap_agent/platform_agent.py`: make `_inject_vision` async (`await self._inject_vision(turn_ctx, new_message)`), implement R2–R5; add `agent/src/lkap_agent/vision.py: encode_jpeg_data_url(frame, max_px=512) -> str` (reuse `_resize_options`); register the error handler in `run_session` next to `function_tools_executed`. Tests in `test_platform_agent.py`: injection uses a data URL, at most one image in `turn_ctx`, disabled after an LLM error, untouched in realtime mode.

**Supersedes** ARCHITECTURE §8 bullet "Cascaded" (raw `ImageContent(image=frame)`) and INSURANCE_PACK_MAPPING #6's cascaded column.

---

## D-W2-9 — Reconciliation with Wave 1 (what the docs now mean)

Each item names the doc section it supersedes. `DECISIONS-W2.md` is authoritative for all of them; the older docs get a one-line pointer where the text is misleading (done by the package that touches the doc next, or by W3-E2E-INSURANCE at the latest).

| # | Topic | Wave 1 reality (authoritative) | Superseded text |
|---|---|---|---|
| 9a | **Initial `UiSnapshot`** | The **platform guarantees** an initial snapshot, and it sends it **after** the pack's hook: `PlatformAgent.on_enter` sets `ctx.ui.state.custom = pack.initial_state(ctx)`, awaits `pack.on_session_start(ctx)`, *then* awaits `ctx.ui.snapshot()`. Order matters: `UiChannel.snapshot()` reuses the current `seq` (only 0 → 1 bumps) and the browser reducer drops a snapshot whose `seq <= last`, so a platform-first empty snapshot would make the insurance pack's own seq-1 snapshot (`build_ui_state(blank)`) be discarded. Pack-first is always safe: if the pack snapshotted, the platform's later snapshot carries the same full state at the current seq; if it sent nothing, the platform supplies seq 1. Today nobody sends it for the generic pack and the browser survives only through the 600 ms `get_snapshot` fallback. Owner: W2-AGENT-INTEGRATION; W2-PACK-INSURANCE-TOOLS keeps its snapshot in `on_session_start` unchanged. | CONTRACTS §10 "seq starts at 1 with a snapshot on session start" (now says who sends it); `packs/generic/pack.py` docstring. |
| 9b | **Start order** | `resolve → build → ctx.connect() → UiChannel.start()/FrameBuffer.start() → avatar.start() + wait_for_join() → session.start(room_options=RoomOptions(participant_identity=…, video_input=…, close_on_disconnect=True))`. Config is still fetched before connecting. | ARCHITECTURE §4 sequence diagram rows 6–7 and the "Rules" bullet 1 (`connect()` "after fetching config" still true; "then session.start then connect" is not). IMPLEMENTATION_PLAN W1-AGENT-CORE text. |
| 9c | **Observability** | Usage from `session_usage_updated` (`AgentSessionUsage`); tool timing from `tool_execution_updated`; `metrics_collected`/`UsageCollector` are deprecated and unused. Transcript from `session.history`. | ARCHITECTURE §14 "Worker subscribes to `metrics_collected` … `metrics.UsageCollector`"; IMPLEMENTATION_PLAN W1-AGENT-CORE `observability.py` line. |
| 9d | **Greeting** | `greeting_mode="say"` is honoured only when the session has a TTS; realtime-without-TTS greets via `generate_reply(instructions="Greet the user. Say exactly this and nothing more: …")` (`resolve_greeting_mode`). `end_call` mirrors this. | ARCHITECTURE §15.9 (now verified: needs TTS); INSURANCE_PACK_MAPPING #3 (`session.say(greeting)` → mode-dependent). |
| 9e | **Job shutdown after hangup** | `close_on_disconnect=True` closes the `AgentSession` when the browser leaves, but the *job* (and therefore the shutdown callback that posts the summary) must be ended explicitly: `run_session` registers `plan.session.on("close", lambda ev: ctx.shutdown(reason=f"session closed: {ev.reason}"))`. Pass criterion in the ladder: summary stored ≤ 10 s after End call. Owner: W2-AGENT-INTEGRATION. | ARCHITECTURE §4 last two rows (implicit). |
| 9f | **Agent-name guard** | **Superseded by D-W2-11.** The name is now fixed by construction (`@server.rtc_session(agent_name="lkap-agent", on_request=...)`); the env guard only refuses a *set* source that disagrees, and every job whose `JobRequest.agent_name != "lkap-agent"` is rejected. `LIVEKIT_AGENT_NAME` becomes optional. | CONTRACTS §3 row `LIVEKIT_AGENT_NAME`; LIVE_TEST_PLAN A1 step 2 and Stage 12 `secrets.env`; `agent/README.md`. |
| 9g | **Dependencies** | `openai>=2,<3` (livekit-agents 1.8.2 requires it); `livekit-agents[...,mcp]` extra is required for `lkap_agent.tools.declarative`; `contracts` ships `py.typed` (D-W2-3) and the mypy overrides go away. | CONTRACTS §2 agent deps block. |
| 9h | **Asset attribute name** | Byte-stream attribute and `AssetRef` field are `caption` (CONTRACTS §10), not `caption_ref`. | ARCHITECTURE §9 table row `lkap.ui.asset`. |
| 9i | **Silent tool replies** | Control point is the synchronous `function_tools_executed` handler calling `cancel_tool_reply()` for tools whose `ToolMeta.silent_reply` is true, realtime mode only; `FunctionCallOutput.reply_required` is what realtime models read afterwards. Cascaded mode relies on the pipeline note + short acknowledgement. Already implemented in `PlatformAgent.on_function_tools_executed`. | ARCHITECTURE §7.2 wording ("`reply_required` control") — same mechanism, now precise. |
| 9j | **Video source preference** | D-W2-4. | ARCHITECTURE §8 bullet 1. |
| 9k | **Cascaded vision encoding** | D-W2-8. | ARCHITECTURE §8 "Cascaded" bullet; MAPPING #6. |
| 9l | **Test call** | D-W2-1. | ARCHITECTURE §11, §12. |
| 9m | **KB `k`** | D-W2-5. | ARCHITECTURE §7.4 "default 4" (still the contract default; agents use `top_k`). |
| 9n | **W2-AGENT-INTEGRATION scope** | The wiring listed in the plan (`UiChannel`, `FrameBuffer`, `BackgroundToolRunner`, tools, image gen, pack loader, RPC registration, shutdown → summary) already landed in W1 via `Deps.from_env`/`_wire_optional_modules`. The package is now: apply D-W2-4/5/6/7/8/9a/9e/9f, then run `LIVE_TEST_PLAN.md` Stages 0–8 and fix what breaks. | IMPLEMENTATION_PLAN W2-AGENT-INTEGRATION "Deliver" paragraph (rewritten). |
| 9o | **`inference.TurnDetector()`** | Constructed per session by `Deps.turn_detector_factory` in cascaded mode only; realtime sessions get no detector and no VAD. Needs network at construction; the worker must have LiveKit creds in env (D-W2-6). | none (confirmation). |
| 9p | **Typed chat runs the per-turn hook** | See §D-W2-9p below: `platform_text_input_cb` replaces the SDK default so `lk.chat` turns get KB auto-inject, the per-turn frame and the pack hook, and `capabilities.chat_input=false` disables typed input at the worker (`RoomOptions.text_input=False`). | ARCHITECTURE §9 row `lk.transcription`, `lk.chat` ("default `text_input` handler → `generate_reply`"); LIVE_TEST_PLAN Stage 3 "Proves" line. |

Not changed by Wave 1 and still binding: D1–D14, dispatch metadata is IDs-only, `RoomAgentDispatch` minted only by the api, secrets never in the browser/dispatch/logs, `agent → packs → contracts` dependency direction, the one-video-source UI rule.

---

## D-W2-9p — Typed chat goes through `on_user_turn_completed`; the SDK-private `_claim_user_turn` is accepted with a tripwire and a runtime fallback

**Decision.** `platform_text_input_cb` (`agent/src/lkap_agent/platform_agent.py`) stays as implemented: claim the turn, `interrupt()`, run `Agent.on_user_turn_completed(turn_ctx_copy, new_message)`, then `generate_reply(user_input=new_message, chat_ctx=turn_ctx_copy)`. The one private call, `sess._claim_user_turn()`, is **accepted** — it is the documented extension point for custom `text_input_cb`s (its own docstring says so) and the SDK's default callback uses nothing else — but it is guarded three ways: an exact SDK pin (already `livekit-agents==1.8.2`), an offline tripwire test that reads the SDK source, and a runtime fallback so a future rename degrades to "typed chat without the user-state pin" instead of "typed chat silently dead". `capabilities.chat_input=false` is now honoured by the worker.

**Rationale.** The alternative public-API shapes are worse: `generate_reply(user_input=text)` after mutating `agent.chat_ctx` persists the KB note and the image into history (violates D-W2-8 R4 and pollutes the transcript), and skipping the claim leaves `user_state` at `"listening"` during the turn so `wait_for_idle`/away-timeout logic sees no user activity. Mirroring the audio path (`_user_turn_completed_impl`) with the SDK's own claim primitive is the smallest faithful implementation; the risk is only an SDK upgrade, and that is what the pin + tripwire cover.

**Known gap (recorded, not fixed).** In realtime mode `AgentActivity._generate_reply` forwards only `user_message.raw_text_content` and ignores `chat_ctx`, so a typed turn still runs the hook (pack side effects, KB note added to the per-turn copy) but the realtime model never sees the per-turn edits. This is the SDK's realtime behaviour for every custom callback, not a platform defect; pack hooks must not rely on `turn_ctx` edits reaching a realtime model.

**Implementation.**

1. `agent/src/lkap_agent/platform_agent.py` `platform_text_input_cb`: replace `async with sess._claim_user_turn():` by
   `claim = getattr(sess, "_claim_user_turn", None); async with (claim() if claim is not None else contextlib.nullcontext()):` and log **one** `warning("AgentSession._claim_user_turn is missing; typed turns run without the user-state pin (SDK upgrade?)")` per process (module-level flag). Catch `RuntimeError` from `await sess.interrupt()` (an uninterruptible speech) the way the audio path does: log at INFO "skipping typed turn: current speech cannot be interrupted" and return. Everything else unchanged. Owner: **W3-E2E-INSURANCE** (Opus).
2. Tripwire, `agent/tests/unit/test_platform_agent.py::test_sdk_default_text_input_cb_still_matches_our_assumptions`: `src = inspect.getsource(livekit.agents.voice.room_io.types._default_text_input_cb)`; assert `"_claim_user_turn" in src` and `"generate_reply(user_input=" in src`; assert `inspect.signature(AgentSession.generate_reply).parameters` contains `user_input` and `chat_ctx`; assert `hasattr(AgentSession, "_claim_user_turn")`. Failure message: "livekit-agents changed the typed-chat path; re-read DECISIONS-W2 §D-W2-9p before bumping the pin". Owner: W3-E2E-INSURANCE.
3. Fallback test: a `_TypedTurnSession` without `_claim_user_turn` still gets the hook and the reply, and the warning is logged once. Owner: W3-E2E-INSURANCE.
4. `capabilities.chat_input`: `agent/src/lkap_agent/session_builder.py` `SessionBuilder.build` sets `RoomOptions(..., text_input=False if not config.capabilities.chat_input else NOT_GIVEN)`; `_assemble` already skips the callback when `text_input is False`. Unit test in `test_session_builder.py`: `chat_input=False` → `room_options.text_input is False`; default → `NOT_GIVEN`/callback wired. The browser already hides the chat control (`session-room.tsx` `chat: capabilities.chat_input`); this closes the server side so a scripted participant cannot type to a voice-only agent. Owner: W3-E2E-INSURANCE.
5. Pin policy: `agent/pyproject.toml` keeps the exact `==1.8.2` pins; any bump must re-run the tripwire and re-read `room_io/types.py` and `agent_session.py` for the four symbols above. Add that sentence to CONTRACTS §2 next to the pin.
6. Docs: ARCHITECTURE §9 row `lk.transcription`, `lk.chat` gets "typed chat → `platform_text_input_cb` → `on_user_turn_completed` → `generate_reply` (D-W2-9p)"; LIVE_TEST_PLAN Stage 3 "Proves" becomes "`lk.chat` → `platform_text_input_cb` → hook → `generate_reply`" and its pass criterion adds "a typed question on `smoke-kb` gets a KB-grounded answer (Stage 8 already does this typed)". Owner: W3-E2E-INSURANCE (it owns the D-W2-9 pointers).

**Supersedes** ARCHITECTURE §9 row `lk.transcription`, `lk.chat`; LIVE_TEST_PLAN Stage 3 "Proves".

---

## D-W2-10 — Vision capability is registry data; gemma stays the text default, vision-enabled agents seed and run on a known vision model

**Decision.** (a) `ModelSpec.supports_video` is redefined as "the model accepts visual input" (realtime: video frames; cascaded LLM: image parts) and is set **only on models verified through this platform's own path**: today `google/gemini-3.5-flash` under `livekit-inference-llm` (Stage 7b) plus the three Gemini Live models already flagged. `google/gemma-4-31b-it` gets `note="text-only on LiveKit Inference: ignores image parts silently"` and **stays** the registry `default_model` (D8: cheapest model that runs on LiveKit creds alone; correct for every agent without camera/screen share). (b) A shared helper `lkap_contracts.providers.vision_support(provider_id, model) -> bool | None` returns `True`/`False` for a model in the suggestion list and `None` for a free-text id. (c) The worker's per-turn injection (D-W2-8 R1) is gated on it tri-state; `describe_current_frame` refuses with a `ToolError` on a known text-only model. (d) The api emits a validation **warning** (never an error — the registry is a suggestion list) when camera/screen share is on and the cascaded LLM is known text-only, mirroring the existing realtime warning. (e) The insurance pack's manifest pins its LLM model to the provider's first `supports_video` model, so the seeded agent (camera on) runs on `google/gemini-3.5-flash`; the generic pack keeps gemma. (f) `CapabilitiesConfig.vision_inject_per_turn` keeps its contract default `true`; the gate in (c) is what makes that safe, no schema change.

**Rationale.** A model that ignores images without an error defeats auto-degrade, wastes a few hundred tokens per turn and, worse, makes the agent confidently describe a red "CAM 42" card as "white" — a wrong answer is a worse failure than a missing feature. The registry already carries a per-model vision flag for realtime; extending its meaning costs no schema regeneration (it is exported and rendered already), while a boolean-only gate would either block admins' free-text vision models or keep wasting tokens on known text-only ones — hence tri-state.

**Implementation.**

1. `contracts/src/lkap_contracts/providers.py`: `ModelSpec.supports_video` docstring → "Accepts visual input: video frames for realtime models, image content parts for LLMs. Set only after the platform has verified it." Set `supports_video=True` on `ModelSpec(id="google/gemini-3.5-flash")` in `livekit-inference-llm`; add the `note` above to `google/gemma-4-31b-it`. Do **not** flag `openai/gpt-4.1`, `openai/gpt-4o-mini` or the `google-llm`/`openai-llm` vendor models until a live check records it (RUNBOOK). Add:
   ```python
   def vision_support(provider_id: str, model: str | None) -> bool | None:
       """True/False for a model in the provider's suggestion list, None for unknown ids/providers."""
   ```
   (resolve `model or spec.default_model`; unknown provider → `None`). Unit tests in `contracts/tests/test_providers.py`: gemini-3.5-flash → True, gemma → False, `"anything/else"` → None, unknown provider → None. Regenerate contracts (`export.py` + `scripts/export_contracts.sh`) — only the JSON `description` strings change. Owner: **W3-E2E-INSURANCE** (Opus) — this step is on the critical path of step 3 and lands **first**, in one small contracts commit (this file, its test, generated output); W3-POLISH's step 2 depends on it and starts after that commit exists.
2. `api/src/lkap_api/config_service.py` `validate_agent_config`: next to the existing realtime `video_input` warning add, for `mode == "cascaded"` with `pipeline.llm` set and `(config.capabilities.camera or config.capabilities.screen_share)`: if `vision_support(ref.provider_id, ref.model) is False` → `warnings.append(f"pipeline.llm: '{model}' cannot see images; camera/screen share still reach the UI and pin_frame, but per-turn vision and describe_current_frame are disabled — pick a model marked 'supports video' (e.g. google/gemini-3.5-flash)")`. `None` → no warning. Test in `api/tests/test_config_service.py` (three cases). `ValidationBanner` already shows warnings on save and on `/validate`; no new web work is required. Optional polish in the same package: `web/src/components/console/registry/model-combobox.tsx` label "supports video" → "vision"; `panel-tab.tsx` shows a one-line hint under the camera/screen-share toggles when the selected LLM model's `supports_video` is false. CONTRACTS §7 event list gains `info` (`{message}`) (doc line only, see step 3). Owner: **W3-POLISH** (Sonnet), after step 1 has landed.
3. Worker gate, `agent/src/lkap_agent/platform_agent.py`: in `PlatformAgent.__init__` compute `self._model_vision = vision_support(llm_ref.provider_id, llm_ref.model)` from `ctx.config.pipeline.llm` (cascaded only; `None` when the slot is absent). `_inject_vision`: after the R1 checks, `if self._model_vision is False: return` — log **once per session** at INFO `"per-turn vision skipped: model is text-only"` with `provider_id`, `model`, and record one `info` session event `{"message": "vision injection skipped: <model> is text-only"}` via the observer (so the console session detail explains the silence). `SessionEventIn.type` is a plain `str` in contracts and the api stores it as-is (verified), so no contracts change is needed for the new type; CONTRACTS §7's event list is documentation only. `True`/`None` → inject as today (R5 still covers `None`). `describe_current_frame` (`tools/builtin/describe_current_frame.py`): same lookup via `ctx.config.pipeline.llm`; `False` → `raise ToolError("The configured language model cannot see images; ask an admin to switch it to a vision-capable model.")` before any encoding. Tests in `test_platform_agent.py` and `test_builtin_tools.py`: gemma config → no `ImageContent` appended and the event recorded once across two turns; gemini config → injected; free-text model → injected. Owner: **W3-E2E-INSURANCE** (Opus), right after step 1.
4. Seed: `packs/src/packs/insurance_claim/manifest.py` — `llm=ProviderRef(provider_id=_LLM.id, model=_vision_model(_LLM))` where `_vision_model(spec)` returns the first `m.id for m in spec.models if m.supports_video` and falls back to `spec.default_model` (a pack must still seed if the registry loses its vision model). Assert in `packs/tests/insurance_claim/test_manifest.py` (or the existing manifest test) that the seeded llm model is `google/gemini-3.5-flash` and that `seed_config_from_manifest` keeps it. Generic pack unchanged. `WORKFLOW_FALLBACK_LLM_MODEL` in `main.py` stays gemma (text-only workflow, deliberately cheapest); the `test_main.py:512`, `test_factory.py`, `fake_api.py` and `test_inference_smoke.py` gemma references are correct and stay. Owner: W3-E2E-INSURANCE.
5. Docs: INSURANCE_PACK_MAPPING #1 and #6 cascaded column → `google/gemini-3.5-flash` for the LLM slot; LIVE_TEST_PLAN §E.4 "gemma everywhere" → "gemma for text-only agents; the first `supports_video` model for camera/screen-share agents"; Part C row "7b on gemma" → "closed: gemma is text-only on Inference (silent), gemini-3.5-flash verified"; ARCHITECTURE §8 cascaded bullet gets the gate sentence. RUNBOOK records the 7b evidence ("CAM 42" card → "white" on gemma; correct on gemini-3.5-flash). Owner: W3-E2E-INSURANCE.

**Supersedes** D-W2-8 R8 (amended above); INSURANCE_PACK_MAPPING #1/#6 model ids; LIVE_TEST_PLAN §E.4 and Part C "7b on gemma".

---

## D-W2-11 — Agent name by construction: explicit `agent_name="lkap-agent"` at registration, env guard only against a contradicting *set* value, and a job-request filter

**Decision.** The worker registers with `@server.rtc_session(agent_name=REQUIRED_AGENT_NAME, on_request=_only_lkap_jobs)`. With the explicit argument the SDK's precedence (`LIVEKIT_AGENT_NAME_OVERRIDE` → argument → `LIVEKIT_AGENT_NAME` → `""`) can no longer yield an unnamed worker, locally or on LiveKit Cloud, whatever the platform injects. `require_agent_name` changes from "env must equal" to "**no set source may disagree**": it raises iff `LIVEKIT_AGENT_NAME_OVERRIDE` is set and ≠ `lkap-agent`, or `LIVEKIT_AGENT_NAME` is set and ≠ `lkap-agent` (ignored by the SDK once the argument is passed, but a mismatch means the process was launched from another agent's env), or `settings.livekit_agent_name` ≠ `lkap-agent`. Unset env is fine — `LIVEKIT_AGENT_NAME` becomes **optional** everywhere. As a runtime belt on public API, `_only_lkap_jobs(req: JobRequest)` rejects any job whose `req.agent_name != "lkap-agent"` (automatic-dispatch jobs carry `""`) and `accept()`s the rest.

**Rationale.** The 9f guard protected the shared project by demanding an env var the SDK does not even need once the name is passed explicitly, and it would refuse to start on any host that only injects the override — which the SDK explicitly describes as a platform-injected force. Fixing the name at the registration call makes the safe state the default state; the env checks then only catch operator mistakes, and the request filter catches the one remaining failure mode (a mis-registered worker being offered automatic-dispatch jobs) loudly instead of silently. Whether Cloud sets `LIVEKIT_AGENT_NAME_OVERRIDE` remains unverified; this rule is correct in both cases.

**Implementation (W3-E2E-INSURANCE, Opus; `agent/src/lkap_agent/main.py`, `agent/tests/unit/test_main.py`).**

1. `main.py`: `@server.rtc_session(agent_name=REQUIRED_AGENT_NAME, on_request=only_lkap_jobs)` where
   ```python
   async def only_lkap_jobs(req: JobRequest) -> None:
       if req.agent_name != REQUIRED_AGENT_NAME:
           logger.error("rejecting job dispatched to the wrong agent name", job_agent_name=req.agent_name, room=req.room.name)
           await req.reject()
           return
       logger.info("accepting job", agent_name=req.agent_name, room=req.room.name)
       await req.accept()
   ```
   Keep `identity`/`name` arguments to `accept()` as today (none are passed now; do not add any).
2. `require_agent_name(environ, settings)`: implement the "no set source disagrees" rule above; keep the `RuntimeError` messages naming the offending source; return `REQUIRED_AGENT_NAME`. Also compute `effective_agent_name(environ) -> str` = `environ.get("LIVEKIT_AGENT_NAME_OVERRIDE") or REQUIRED_AGENT_NAME` (mirrors the SDK precedence given the explicit argument) and log it from `prewarm` instead of `os.environ.get("LIVEKIT_AGENT_NAME")`.
3. Tests: replace the `missing`/`empty` cases at `test_main.py:722-741` (they now **accept**); keep `wrong` and `override` as refusals; add `{"LIVEKIT_AGENT_NAME_OVERRIDE": "lkap-agent"}` with no `LIVEKIT_AGENT_NAME` → accepted; settings mismatch still refuses. `only_lkap_jobs`: a fake `JobRequest` with `agent_name` `""` and `"other-project-agent"` is rejected (and never accepted), `"lkap-agent"` is accepted. Registration check: `AgentServer` has no public getter for the resolved name, so — consistent with D-W2-9p — the unit test reads `server._agent_name == "lkap-agent"` (with `LIVEKIT_AGENT_NAME_OVERRIDE` unset in the test env) and a tripwire test asserts `inspect.getsource(AgentServer.rtc_session)` still contains `LIVEKIT_AGENT_NAME_OVERRIDE` and `elif agent_name:` in that order (the precedence this decision relies on).
4. Live pass criterion (LIVE_TEST_PLAN Stage 1, add): the worker log shows `accepting job agent_name=lkap-agent` for the dispatched room — this verifies that Cloud populates `JobRequest.agent_name` on explicit dispatch well before Stage 12; if every job is rejected there, stop and report (the belt would be wrong, not the guard). Stage 12: `lk agent list` shows `lkap-agent` **and** `other-project-agent`; the cloud worker's first job also logs `accepting job agent_name=lkap-agent`.
5. Docs/config: CONTRACTS §3 row `LIVEKIT_AGENT_NAME` → "opt; the worker registers as `lkap-agent` by code; if set it must equal `lkap-agent` (`LIVEKIT_AGENT_NAME_OVERRIDE` likewise)"; LIVE_TEST_PLAN A1 step 2 check line → "`worker process prewarmed agent_name=lkap-agent` and the SDK's `registered worker` line with `agent_name=lkap-agent`; any job log without `accepting job agent_name=lkap-agent` is a stop"; Stage 12 `secrets.env` no longer needs `LIVEKIT_AGENT_NAME`; `agent/README.md` run line drops the export; `scripts/dev.sh` may keep its harmless default export. `agent/livekit.toml` `[agent] name = "lkap-agent"` stays the deploy-time source of truth and must equal `REQUIRED_AGENT_NAME` (add a one-line offline test that parses the toml). The other half of the pair is the api: `RoomAgentDispatch(agent_name=...)` is minted from `lkap_api` `Settings.agent_name` (`LKAP_AGENT_NAME`, default `lkap-agent`); if it drifts, dispatches target a name nobody serves and the reject filter sees nothing. `api/tests/test_settings.py` gains `assert Settings().agent_name == "lkap-agent"` and CONTRACTS §3's `LKAP_AGENT_NAME` row says "must equal the worker's `REQUIRED_AGENT_NAME`" (W3-POLISH owns the api test file; one assertion).

**Supersedes** D-W2-9f; CONTRACTS §3 row `LIVEKIT_AGENT_NAME`; LIVE_TEST_PLAN A1 step 2 and Stage 12 secrets list.

---

## D-W2-12 — `search_knowledge` drops the `kb` parameter

**Decision.** The built-in tool's signature becomes `search_knowledge(query: str) -> str`; it always searches the agent's attached KBs (`kb_ids=None` → api default of the agent's `knowledge.kb_ids`) with `k=ctx.config.knowledge.top_k`. The `kb` filter is removed rather than kept-but-ignored.

**Rationale.** After W2-AGENT-INTEGRATION's fix an out-of-scope `kb` id is silently ignored, so the schema advertises a filter that does nothing — models invent ids (the reason for the fix) and then trust a narrowing that never happened. An agent can only ever search its own KBs, and with typically one or two attached the filter has no use in the MVP; removing it is the honest schema.

**Implementation (W3-E2E-INSURANCE).** `agent/src/lkap_agent/tools/builtin/search_knowledge.py`: remove the `kb` argument and the `kb_ids` computation (`ctx.kb.search(query, k=ctx.config.knowledge.top_k)`); update the docstring. `agent/tests/unit/test_builtin_tools.py`: the "kb outside the agent's KBs" test becomes "the tool schema has exactly one parameter, `query`". ARCHITECTURE §7 table row → `search_knowledge(query)`. `KbClient.search(..., kb_ids=...)` itself stays (packs and `_inject_knowledge` may still pass it).

**Supersedes** ARCHITECTURE §7 row `search_knowledge(query, kb?)`.

---

## D-W2-13 — Worker lifecycle rule for dev and deploy (no hot reload; SIGINT with grace, never SIGKILL first)

**Decision.** Recorded from `cli.py` (1.8.2), replacing LIVE_TEST_PLAN A1's "dev mode hot-reloads — verify": (1) `python -m lkap_agent.main dev` does **not** reload on file change; code edits need a restart, config edits never do (config is fetched per job). Hot reload exists only when the process is driven by the `lk` CLI dev channel (`--cli-addr`), which the platform does not use. (2) The first SIGINT/SIGTERM schedules the exit; in `dev` mode the server closes immediately (`aclose()`), in `start` mode it **drains** first — stays registered, finishes running jobs, accepts none — for up to `drain_timeout`; a second signal `os._exit(1)`s. (3) Therefore every launcher of the worker (`.claude/launch.json` `lkap-agent` entry, `scripts/dev.sh`, the Dockerfile/compose stop policy) sends **SIGINT, waits ≥ 15 s in dev / `drain_timeout` in start mode, then SIGKILL** — never SIGKILL first. A killed worker leaves `active` session rows behind; the D-W2-2(b) sweep is the safety net, not a substitute for the grace period.

**Rationale.** The implementer's Stage 0–8 run hit exactly this (SIGTERM appeared to "only drain", and a dev worker kept its old code); pinning the observed SDK behaviour as the rule stops the next person from re-discovering it and keeps the "exactly one session row per call, closed ≤ 10 s" criterion meaningful during restarts.

**Implementation.** `scripts/dev.sh` `cleanup()`: `kill -INT`, then wait up to 15 s, then `kill -KILL` (Owner: W3-POLISH, one function; it does not own the file today — note it in the report). `.claude/launch.json` `lkap-agent` entry: no change needed unless it specifies a stop signal (W2-DEPLOY/W3-E2E-INSURANCE check). `agent/Dockerfile`: `STOPSIGNAL SIGINT` and document `stop_grace_period` ≥ `drain_timeout` in the compose file (W3-E2E-INSURANCE, alongside Stage 12). LIVE_TEST_PLAN A1 "Restart rule" paragraph → this text; RUNBOOK gets a "restarting the worker" subsection and records whether the observed drain matched (Owner: W3-E2E-INSURANCE).

**Supersedes** LIVE_TEST_PLAN A1 "Restart rule".

---

## Wave 3 decisions (D-W3-1 … D-W3-2)

Status: **binding**, added by the architect's final pre-handoff review (`docs/REVIEW-FINAL.md` §1, 2026-09-19) after that review read the code as it stood at the end of Wave 2. Same precedence rule as the rest of this file: this document wins over `CONTRACTS.md`, `ARCHITECTURE.md` and everything else.

### D-W3-1 — Session events from packs and tools: a `record_event` callback on the pack session context

**Decision.** `PackSessionContext` (packs/base.py:169–186) gains one Protocol **method**, `def record_event(self, event_type: str, payload: dict[str, Any]) -> None: ...` (declared like `UiChannel.patch`, not as an attribute: under `mypy --strict` a non-ClassVar attribute on a Protocol is a *settable* member, and a fake implementing it as a method would fail with "expected settable variable, got read-only attribute"). Its shape matches what `PlatformAgent` (platform_agent.py:169) and `BackgroundToolRunner` (background.py:65) already accept as a `Callable`. The worker's `SessionContext` dataclass keeps a `Callable`-typed field named `record_event` (a callable attribute satisfies a Protocol method) bound to `SessionObserver.record` in `_assemble` (main.py:538–558). Packs and built-in tools call `ctx.record_event(type, payload)`; delivery stays buffered/best-effort in the observer, so a pack can never slow or break a call by recording.

**Emission rules (also written into CONTRACTS §7 next to the event list).**
- Platform-owned types are emitted only by the worker: `session_started`, `agent_state`, `user_turn`, `agent_turn`, `tool_call_started/ended`, `workflow_run`, `metrics`, `error`, `info`, `session_ended`. A pack must not emit them.
- Packs and tools may emit `escalation` (`{reason: str, urgency: "low"|"normal"|"high"}`) and `info` (`{message: str}`). Any other type is stored as-is by the api (`SessionEventIn.type` is a plain `str`, internal.py:158–166) and rendered generically by the console; packs should prefix custom types with their pack id (`insurance_claim.route_changed`) so they never collide with platform types.
- `escalate_to_human` (tools/builtin/escalate_to_human.py:22–51) emits `escalation{reason, urgency}` right after `set_status("Escalated", "warning")`.
- The insurance pack emits `escalation` on the **route transition into `emergency_escalation`**, in `render_and_patch` (packs/insurance_claim/tools.py:104–125), which already knows `intake.previous_route` and is the single choke point for both the tool-triggered run and the passive debounced run. Payload: `{"reason": "claim routed to emergency_escalation", "urgency": "high", "route": <route>}`. Not on the urgent-reply branch of `submit_workflow_run`: the passive run never speaks but still escalates, and a repeated run must not re-emit.

**Why a callback and not a richer API.** The observer is the only writer to the api's event endpoint and already owns buffering, redaction and shutdown ordering; exposing a `Callable` keeps packs free of any `lkap_agent` import (dependency direction `agent → packs → contracts` holds), needs no contracts model change (no schema regen, no TS regen), and matches the two existing call sites so there is one idiom, not two.

**Contracts implications.** `packs/base.py` is "CONTRACTS §8 verbatim": §8's `PackSessionContext` listing gains the one line. `SessionContext` (platform_agent.py:136–154, `@dataclass(slots=True)`) gets the field with a no-op default so every existing constructor call keeps compiling (`tests/live/test_e2e_insurance.py:104`, `test_main.py`). Both fake contexts must gain the attribute or `mypy --strict` fails wherever a fake is passed as `PackSessionContext`: `agent/tests/fakes/fake_ctx.py` and `packs/tests/insurance_claim/fake_ctx.py` (they are divergent copies — see REVIEW-FINAL F-18).

**Does it block the MVP?** No. The console already shows the escalation as `workflow_run` + the urgent `done` activity + the danger stamp, and the user's manual test does not depend on the event. It is in the fix plan anyway because it is ~40 lines, it is the last unimplemented CONTRACTS §7 type, LIVE_TEST_PLAN Stage 9's pass line names it, and without it a second pack has no way to put anything of its own on the session timeline.

**Implementation.** WP-B owns the code; WP-A wires `record_event=record_event` into `SessionContext(...)` in `_assemble`; WP-D writes the CONTRACTS §7/§8 lines and this decision.

### D-W3-2 — Start-call freeze: confirmed as implemented (freeze is a no-op until a connect has been attempted)

**Decision.** Confirm the refinement in `web/src/lib/livekit.ts:268–276`: `freeze()` sets `frozen = true` only when `last !== null || inflight !== null`. This amends D-W2-2(a), whose text ("keep the exported `freeze()`, harmless, still called on unmount") assumed the cleanup could never run before the first fetch — React StrictMode in `next dev` runs the session effect's cleanup before re-running it on the same memoised source, so the unrefined freeze killed every first call in dev.

**Rationale.** The purpose of freezing is to prevent a *second* `POST /connect` on the same source; a source that has never fetched has nothing to replay and nothing to protect. The refinement preserves D-W2-2(a)'s guarantee (one source, at most one row) in every path: first success → `frozen` (line 260); unmount after success → `freeze()` no-op; unmount during the in-flight first fetch → freezes, and the second `start()` receives the same in-flight promise (line 230), still one row.

**One nuance to record.** Freezing while the first fetch is in flight and that fetch then *rejects* leaves the source dead (`last === null`, `frozen === true` → `session_closed` on any later call). This is acceptable only because `SessionExperience` (session-experience.tsx:43–53) remounts a fresh `LiveSession`, and therefore a fresh source, on every Start/Try-again (the `attempt` key). If anyone ever reuses a source across attempts, this nuance becomes a bug. The vitest at `web/tests/livekit-connect.test.ts` ("freeze() before any connect is a no-op") covers the StrictMode case.

**Implementation.** Already implemented (verified against the code as of 2026-09-19); this decision records and confirms it. Recorded as **D-W3-2**.
