# Background tools: the agent keeps talking while the work runs

Status: **design, no implementation** (Fable 5.1, 2026-09-25). Packages V4-12, V4-13 and V4-14 in `PLAN-V4.md` §2; rulings R-V4-34 … R-V4-40 in §5. Every SDK claim below was read in the installed `livekit-agents==1.8.2` (`agent/.venv/lib/python3.12/site-packages/livekit/agents/…`, file and line quoted) or on a LiveKit page fetched today (§11). Nothing here is guessed from `main`.

The user's ask, in one sentence: *a toggle so that data-retrieval or action tools run in the background, in parallel, while the agent keeps talking — the agent says it is fetching, knows a call is in progress, and reports the result when it lands; for any tool, retrieval or action.*

---

## 0. Summary

**The pinned SDK already has the engine.** `livekit-agents 1.8.2` ships an async-tool executor that LKAP does not use: a tool that calls `ctx.update("Looking that up…")` releases the LLM at once with that text as its (first) result, keeps running as its own `asyncio.Task`, and its later updates and final return are coalesced into one deferred reply that is generated **when the session is next idle**, never over the user's or the agent's speech. The same executor gives verbatim filler speech on an idle timer (`ctx.with_filler`), an in-progress placeholder so the model does not re-issue a running call, LLM-visible cancellation (`ToolFlag.CANCELLABLE` auto-exposes `lk_agents_get_running_tasks` / `lk_agents_cancel_task`), duplicate-call policy (`on_duplicate` × `duplicate_scope`), per-tool options for MCP tools (`MCPToolset(tool_options=…)`, including forwarding MCP progress notifications as updates), configurable prompt templates (`tool_handling.async_options`), a flat lifecycle event (`ToolExecutionUpdatedEvent`: started → updated → ended → reply) — and tool calls in one LLM turn already run in parallel. LKAP's `BackgroundToolRunner` (`tools/background.py`) predates all of it; its docstring's first line ("LiveKit has no non-blocking tool flag") is no longer true.

**So LKAP builds the policy, not the scheduler.** What we add:

1. **A per-tool execution policy** (`ToolExecution` in `lkap_contracts.tools`): `mode` = `blocking` (today) | `background` (announce at once, run on, report at idle) | `auto` (run inline up to a threshold, then announce), an `announce` line, optional verbatim `fillers` on an idle timer, `cancellable`, and a duplicate policy. Fillers are orthogonal to the mode: a blocking tool may say "still checking…" after four idle seconds without a second LLM call (that is knowledge-and-memory P1-2).
2. **An agent-level default** (`ToolsConfig.execution_default`) that only ever reaches **read tools** (GET HTTP tools, `search_knowledge`, `http_request` GETs, `describe_current_frame`); writes, telephony, forms, `end_call` and flow edges are never backgrounded by a default, and non-GET HTTP tools need an explicit per-tool opt-in with `on_duplicate="confirm"`.
3. **One wrapper** (`tools/execution.py::run_with_policy`) that every tool source (built-in, declarative HTTP, MCP via `MCPToolset`, pack tools that declare it) goes through; a bounded `max_duration_s`, cancellation on hangup, handoff and text-channel rewind, errors surfaced as a spoken tool error.
4. **The conversation glue**: voice-safe `async_options` templates per pipeline mode, a pipeline-note sentence so the model knows what "in progress" means, a `thinking_sound` for the blocking wait, and the activity block fed from `ToolExecutionUpdatedEvent` (running → done/error/cancelled, with the update text as `detail`), plus two new session events.
5. **Console and MCP**: the per-tool fields in the HTTP tool editor beside `silent_reply`, a per-tool options table in the MCP editor, the agent default and the thinking sound in *Instructions & voice*; `tool_create_http(execution=…)`, `tool_create_mcp(tool_options=…)`. Dialogs only (R-V3-2).

Two SDK limits shape the design and are ruled on rather than worked around: fillers need a TTS (neither realtime plugin sets `supports_say` in 1.8.2, and `_FillerScheduler` calls `say()` unguarded), so fillers are skipped on realtime-without-TTS and on the text channel; and per-node tool scoping (flows) is activity-scoped in this SDK, so a `go_to_*` transition cancels a node's cancellable background tools. Flow-node background tools are additionally gated on the `livekit-agents 1.8.3` bump (V4-14), because 1.8.2 drops a handoff when a sibling tool in the same batch finishes after the edge tool (#7321).

---

## 1. What `livekit-agents 1.8.2` supports natively (verified)

Paths are under `agent/.venv/lib/python3.12/site-packages/livekit/agents/`.

### 1.1 Tool calls in one turn run in parallel

`voice/generation.py:821–1080` (`_execute_tools_task`): each `FunctionCall` from the stream becomes `asyncio.create_task(_traceable_fnc_tool(...))` immediately (line ~1057), and the batch is awaited with `asyncio.shield(asyncio.gather(*tasks, return_exceptions=True))` at the end. Two calls in one LLM turn overlap; the reply waits for the slowest **blocking** one. Per-call errors become `ToolError` outputs, not batch failures.

### 1.2 `RunContext.update()`: the first update releases the LLM, the rest are queued

`voice/events.py:173–248` and `voice/tool_executor.py:299–472`:

- `_ToolExecutor.execute()` runs the tool as `asyncio.create_task(_execute_tool(), name=f"tool_exec_{fnc_name}")` and returns `await first_update_fut` — i.e. **when the first `ctx.update()` lands or the tool returns** (docstring, line 306). A tool that never updates is a classic sync tool.
- The first `ctx.update(message)` sets the future with the message rendered through `update_template` and marks the call `extra["__livekit_agents_tool_non_blocking"] = True` (`events.py:240–243`). The LLM then answers this turn with that text as the tool's output, e.g. "Sure, let me look up flights…".
- Later updates and the final return are appended as synthetic `(FunctionCall, FunctionCallOutput)` pairs with call ids `{call_id}_update_N` / `{call_id}_final` (`events.py:262–275`, `tool_executor.py:405–409`), inserted eagerly into the agent's `chat_ctx` and `session.history` (`_enqueue_reply`, lines 519–540), and delivered by `_deliver_reply` (542–647): it **awaits `session.wait_for_idle()`** (or the owning activity's), then `session.generate_reply(instructions=<template>, tool_choice="none", chat_ctx=…)`. Two templates: `reply_at_tail` when nothing was said since the update, `reply_maybe_covered` otherwise (the model may answer with nothing if it already said it; that reply is reported `skipped`). One deferred reply covers every update that arrived while waiting.
- `session.wait_for_idle()` (`agent_session.py:1735–1758`, `agent_activity.py:2070–2130`) waits for no current speech, an empty speech queue, no in-flight end-of-turn and no user speech — so a background result **never interrupts**; it is spoken at the next turn boundary. An interrupted deferred reply is not rescheduled (`TODO(long)` at 641).
- Templates (`tool_executor.py:57–70, 97–133`): `update_template` (default: "The tool `{function_name}` has updated, message: {message} … DON'T make up …"), `duplicate_reject_template`, `duplicate_confirm_template`, `reply_at_tail_template`, `reply_maybe_covered_template`; settable on `AgentSession(tool_handling={"async_options": {...}})`, `Agent(tool_handling=…)` or `AsyncToolset(tool_handling=…)`, most specific wins (`ToolHandlingOptions` docstring). The defaults speak tool names and call ids — fine for a chat model, not for a voice.
- The first update **counts as a tool step** (`speech_handle._num_steps += 1` in the pipeline reply loop, `agent_activity.py:3944`), so `max_tool_steps` (LKAP default 3) still bounds the chain.

### 1.3 Filler speech and the floor

- `ctx.with_filler(source, *, delay=0, interval=None, max_steps=None)` (`events.py:112–155`, `voice/filler_scheduler.py`): a background scheduler waits for the session to be **continuously idle** for `delay` seconds (any `agent_state` of `speaking`/`thinking` or user speech resets the dwell), then `session.say(text)`; `interval` repeats it, `max_steps` caps it; a callable source `(step) -> str | SpeechHandle | None` rotates phrases. `ctx.update()` resets the dwell so a filler does not race a real update. **`say()` is called with no guard** (`filler_scheduler.py:83–88`): on a session without a TTS whose realtime model lacks `capabilities.supports_say`, `AgentActivity.say` raises `RuntimeError` (`agent_activity.py:1703–1707`); in 1.8.2 that error is swallowed inside the scheduler's task (1.8.3's #7334 "report the error that stops a filler" makes it visible). Neither `livekit-plugins-google` nor `livekit-plugins-openai` sets `supports_say` (`llm/realtime.py:88` default `False`; the plugins pass no override, as `platform_agent.resolve_greeting_mode` already records).
- `ctx.foreground()` (`events.py:156–171`): wait for idle, then hold the floor (`_wait_for_idle_and_hold`) so an inline `AgentTask` or a direct `say`/`generate_reply` inside a tool does not collide with a queued deferred reply. Not needed for v1 (no inline tasks inside declarative tools).
- `ctx.wait_for_playout()` waits only for the spoken part of the turn that preceded the tool (`events.py:103–110`); `SpeechHandle.wait_for_playout()` from inside a *blocking* tool raises with an explicit message (`speech_handle.py:229–255`) — a non-blocking tool (after its first update) is allowed to.
- Thinking sound: `BackgroundAudioPlayer(thinking_sound=…)` plays only while `agent_state == "thinking"` (docs, §11), i.e. during a **blocking** wait; after a first update the state is `speaking`/`listening` and the sound stops. Verified importable in 1.8.2 with `BuiltinAudioClip.KEYBOARD_TYPING`, `KEYBOARD_TYPING2`, `OFFICE_AMBIENCE` (research-v4 panels §C25).

### 1.4 The model knows what is running: placeholder and companion tools

- **Placeholder** (`generation.py:66–104, 132–145`; used at `agent_activity.py:3496–3502`): before every LLM inference the activity inserts, for each still-running call not already in this turn's context, the call plus a `FunctionCallOutput("The tool call is still in progress.")`, flagged so it is stripped before the context is persisted or forwarded. The model therefore sees that `check_order(...)` is pending and does not re-issue it. This is the "pending tasks note" the ask describes — it exists.
- **Companion tools** (`agent_activity.py:697–705`, `tool_executor.py:170–189`): when any registered tool carries `ToolFlag.CANCELLABLE`, the activity appends `lk_agents_get_running_tasks` (returns the cancellable running calls' `FunctionCall` dumps) and `lk_agents_cancel_task(call_id)` to the LLM-visible tool list — always-on so the schema, and the prompt cache, stay stable. `cancel()` refuses when the owning speech disallows interruptions (`tool_executor.py:474–487`).
- **Duplicates** (`llm/tool_context.py:158–177`, `tool_executor.py:649–706`): `on_duplicate` = `allow` (default) | `reject` (the LLM gets `duplicate_reject_template`) | `replace` (cancel the running one; requires it to be cancellable) | `confirm` (the schema gains a `lk_agents_confirm_duplicate: bool` parameter and the LLM must re-call with it); `duplicate_scope` = `name` (default) | `name_and_args` (validated arguments compared after canonical JSON). Keys are name-scoped; a mode is per tool.

### 1.5 Scoping and handoffs

`agent_activity.py:1163–1176` and `llm/async_toolset.py`: toolsets in **`session.tools`** are attached with `activity=None` (session-scoped: a running tool survives a handoff, its reply goes to whichever agent is current), toolsets and tools in **`agent.tools`** are activity-scoped: on activity close (`agent_activity.py:1618–1650`) `_tool_executor.drain()` **cancels cancellable tools and awaits the rest**, then `aclose()` sweeps. Session-scoped and per-agent scoping are mutually exclusive in this SDK: `FlowRuntime.tools_for` builds per-node `Agent(tools=…)`, so a node's background tool is cancelled by a `go_to_*` transition unless it is non-cancellable (then the transition waits for it). An agent handoff **after** a first update is unsupported (`tool_executor.py:376–384`).

### 1.6 MCP

`llm/mcp.py:54–74, 206–293, 535–560`: `MCPToolset(id, mcp_server, tool_options={name: MCPToolOptions(flags, on_duplicate, duplicate_scope, report_progress)})`; `report_progress=True` forwards the server's progress notifications (`progress_callback`) as `ctx.update(message)` (empty messages are dropped). `Agent(mcp_servers=[…])` is **deprecated** in 1.8.2 with a warning "Use `MCPToolset` instead" (`voice/agent.py:136–139`) and builds one `MCPToolset` per server with **no** `tool_options` (`agent_activity.py:1153–1160`). Per-tool policy for MCP tools therefore requires LKAP to construct the toolsets itself; `GuardedMCPServerHTTP` (`tools/mcp_client.py`) stays the server.

### 1.7 Events and observability

`voice/events.py:478–530`: `ToolExecutionUpdatedEvent.update` is one of `ToolCallStarted(function_call)`, `ToolCallUpdated(id, call_id, message)`, `ToolCallEnded(id, call_id, message, status ∈ done|error|cancelled)`, `ToolReplyUpdated(update_ids, status ∈ scheduled|completed|interrupted|skipped, speech_id)`. `lkap_agent/observability.py:268, 295–330` already subscribes and records `tool_call_started` / `tool_call_ended` session events; `tool_call_updated` and `tool_reply_updated` are ignored today. `FunctionToolsExecutedEvent` (`events.py:423–437`) still fires per batch with `has_tool_reply` / `cancel_tool_reply()`, which `PlatformAgent.on_function_tools_executed` uses for `silent_reply`.

### 1.8 Realtime models

- **Gemini Live** (`livekit/plugins/google/realtime/realtime_api.py:185–186, 305–306, 342–353, 706–731`): constructor options `tool_behavior` (`BLOCKING` | `NON_BLOCKING`; Gemini's own default is BLOCK) and `tool_response_scheduling` (`WHEN_IDLE` | `INTERRUPT` | `SILENT`, default WHEN_IDLE; Vertex ignores it). LKAP's registry entry already exposes both fields with defaults **`NON_BLOCKING` / `WHEN_IDLE`** (`contracts/…/providers.py:771–786`). `SILENT` is claimed only for outputs with `reply_required=False` on a NON_BLOCKING session (the `silent_reply` path). Capabilities: `auto_tool_reply_generation=True`, `mutable_chat_context=True`, `per_response_tool_choice=False`. So a first update is sent as a function response the model answers when idle, and the deferred reply's `generate_reply(instructions=…, tool_choice="none")` works (the `tool_choice` is ignored with a warning — a live-check observation).
- **OpenAI Realtime** (`plugins/openai/realtime/realtime_model.py:486`): `auto_tool_reply_generation=False`, so the SDK itself creates the tool reply (`agent_activity.py:4723–4740`) after waiting for current speeches to finish ("most realtime models don't support generating multiple responses at the same time", 4667–4676). Same executor path; no async-function-calling flag is needed on the wire.
- Both: no `supports_say` → `with_filler` cannot speak unless the pipeline is `half_cascade` (a TTS exists).

### 1.9 What 1.8.3 changes (from the tag compare, 66 commits)

- #7321 "don't let a parallel tool cancel an agent handoff" (merged 2026-09-18): in 1.8.2, when a handoff tool and an ordinary tool are emitted in the same batch and the ordinary one finishes **after** the edge tool, `new_agent_task` is overwritten with `None` and the transition is silently dropped (pipeline and realtime paths). This is pre-existing for LKAP flows with any slow sibling tool; background tools make the "finishes later" case the common one only when the sibling is *blocking*, since a background tool's dispatch returns at its first update. Gate: §5.
- #7334 "report the error that stops a filler": a `say()` failure inside `with_filler` becomes visible.
- 1.8.3 pins `livekit==1.1.18` exactly (research-v4 `_sources/livekit-surface.md` §1).

### 1.10 What LKAP has today

| Piece | Where | Relation to this design |
|---|---|---|
| `BackgroundToolRunner` (`submit`, urgent `generate_reply(allow_interruptions=False)` vs routine `update_chat_ctx` note; activity events; `workflow_run` session event; `cancel_all` at shutdown) | `agent/src/lkap_agent/tools/background.py`, `packs/src/packs/base.py::BackgroundRunner` | Pack-only API for the insurance workflow and image generation. **Kept unchanged** in v1 (R-V4-34); its urgent-interrupt path is the one thing the SDK executor does not offer. |
| `silent_reply` (`HttpToolDefinition`, `ToolMeta`), `PlatformAgent.on_function_tools_executed` → `cancel_tool_reply()` on realtime | `platform_agent.py:543–560` | Mutually exclusive with `background` (a cancelled reply would eat the announce). |
| `PIPELINE_NOTES` | `platform_agent.py:79–98` | Gain one sentence on in-progress tools per mode. |
| Session events `tool_call_started/ended` | `observability.py`, `docs/CONTRACTS.md` §7 | Gain `tool_call_updated`, `tool_reply`. |
| Activity block (`ActivityEvent`, `phase=running` renders a `StateMeter`) | `contracts/…/ui_protocol.py:77–90`, `web/src/panels/generic/blocks.tsx:324–354` | Fed from `ToolExecutionUpdatedEvent`; no contract change (`detail` exists). |
| `Agent(mcp_servers=…)` from `build_mcp_servers` | `tools/declarative.py`, `flow/runtime.py::mcp_servers_for` | Becomes `MCPToolset(mcp_server=GuardedMCPServerHTTP, tool_options=…)`. |
| `max_tool_steps` | `ToolsConfig`, `session_builder.py` | Unchanged; the first update spends one step. |

---

## 2. Decisions

### D-V4-29 — Native first: the SDK executor is the runtime; LKAP adds policy

No second scheduler. `BackgroundToolRunner` is neither extended nor migrated in v1 (packs keep it for urgent-interrupt results; its docstring's first line is corrected as part of V4-12 — a comment, no behaviour). Everything a declarative or built-in tool needs comes from `RunContext.update`, `with_filler`, `ToolFlag`, `on_duplicate`, `MCPToolset` and `ToolExecutionUpdatedEvent`. If a future SDK removes one of these the wrapper degrades to blocking with one warning per process (the `_claim_user_turn` pattern, D-W2-9p), and a tripwire test imports every symbol.

### D-V4-30 — Three mechanisms, named apart

1. **Announce** (`ctx.update`): the *LLM* says something in its own words at once; costs one extra generation per tool (the first update is a tool step) and works on every pipeline, TTS or not.
2. **Fillers** (`ctx.with_filler`): verbatim phrases spoken after `filler_delay_s` of continuous idle, repeated every `filler_interval_s` up to `len(fillers)` times; **no LLM call**; needs a TTS (`session.tts is not None`); skipped otherwise.
3. **Thinking sound** (`BackgroundAudioPlayer(thinking_sound=…)`): the blocking wait only.

A tool combines them: `mode=blocking` + fillers is "let me check… (4 s) still checking…" with one generation at the end; `mode=background` + fillers is "let me look that up" now, "still on it" if the search drags, the answer at idle.

### D-V4-31 — Modes: `blocking` | `background` | `auto`; `auto` is the recommended default for reads

`auto` starts the work, waits inline up to `auto_threshold_ms` (default **700**), and returns inline when the work finished — a warm `search_knowledge` (20–40 ms, knowledge-and-memory §1.2) never pays the second generation — otherwise it calls `ctx.update(announce)` and carries on as `background`. The threshold is per tool with the agent default as fallback. `background` announces immediately (for tools known to be slow: a workflow, an image, a remote CRM). `blocking` is today.

### D-V4-32 — The safety rule is mechanical

- `ToolsConfig.execution_default` (`blocking` default) reaches only **read tools**: HTTP tools with `method == "GET"`, `search_knowledge`, `describe_current_frame`, and `http_request` when its call is a GET. It never reaches MCP tools (no annotation is read today; per-tool opt-in only), non-GET HTTP tools, block tools, telephony tools, `end_call`, `escalate_to_human`, `request_form`, flow edge tools or pack tools.
- **Hard never** (a per-tool setting is refused by the api validator and ignored by the worker with a warning): `end_call`, `transfer_call`, `send_dtmf`, `request_form` (it already returns immediately and delivers later), `escalate_to_human`, every `go_to_*` edge tool, `update_block`, `show_document`, `table_append`, `push_note`, `set_status`, `pin_frame`, `current_time` (sub-100 ms UI writes: backgrounding is pure cost).
- A non-GET HTTP tool may set `mode` explicitly; its duplicate policy then defaults to `confirm` (the schema gains `lk_agents_confirm_duplicate`) and `cancellable` defaults to `false` (a POST in flight is not torn down by a handoff or a "never mind"). GET tools default `on_duplicate="reject"`, `duplicate_scope="name_and_args"`, `cancellable=true`.
- `silent_reply` and a non-blocking mode on the same tool is a validation **error**.

### D-V4-33 — Results are queued at idle, never interrupt; pending state is native

The SDK's `_deliver_reply` waits for idle. LKAP adds no interrupting path for declarative or built-in tools; a result that must cut in is a pack concern (`BackgroundRunner.urgent`), unchanged. The model learns what is pending from the SDK placeholder and, when a cancellable tool exists, the two companion tools; no LKAP `check_task_status` tool. The pipeline note tells the model what "still in progress" means and not to promise a result it has not received. One bound to state plainly: the deferred reply is generated with `tool_choice="none"`, so a background result can be *spoken* but cannot itself *trigger* the next tool call — "look it up, then book it" runs the booking on the user's next turn (or as a blocking follow-up the model issues in the announce turn, which spends a step). "Several tool calls while talking" therefore means several calls the model issues in one turn (already parallel) or across turns, not an autonomous chain fed by results.

### D-V4-34 — Bounds: `max_duration_s`, cancellation, errors

Every non-blocking run is bounded by `max_duration_s` (default 60, ≤ 600) on top of the tool's own transport timeout (`timeout_s` for HTTP); a timeout ends the call as `ToolError("<label> took longer than N s")`, which the SDK voices as an error result at idle. Cancellation: hangup (activity close → `drain()`/`aclose()`), a `go_to_*` handoff (cancellable tools only), `lk_agents_cancel_task`, `on_duplicate="replace"`, and a text-channel rewind (§5) all cancel the **inner work task** the wrapper owns; an HTTP request in flight is dropped with the `httpx` client. Errors surface once (the SDK's `ToolCallEnded(status="error")` → activity `error` + `tool_call_ended` event) and are never retried by the platform.

### D-V4-35 — Voice-safe templates per pipeline mode, set in `session_builder`

`AgentSession(tool_handling={"async_options": …})` with LKAP text: `update_template` = "Background work for `{function_name}` reports: {message}. Tell the user briefly, in one clause, and continue; do not invent anything the message does not say."; `reply_at_tail` = "A background task just finished. Say what it found in one or two sentences, naturally, then continue."; `reply_maybe_covered` = the SDK's meaning ("if you already said all of it, answer with nothing") in the same voice; `duplicate_reject` = "That is already being looked up; tell the user it is on its way."; `duplicate_confirm` = "…re-call with `lk_agents_confirm_duplicate=true` only if the user asks for it again." No tool name is spoken: the templates instruct, the model paraphrases.

### D-V4-36 — Realtime and text-channel behaviour is degraded, not disabled

| Pipeline | Announce (`ctx.update`) | Fillers | Deferred reply | Note |
|---|---|---|---|---|
| cascaded | yes | yes | yes | reference behaviour |
| half_cascade | yes | yes (TTS exists) | yes | |
| realtime (Gemini) | yes; `tool_behavior=NON_BLOCKING`, `tool_response_scheduling=WHEN_IDLE` (registry defaults) mean the model voices it when idle | **skipped** (no TTS, no `supports_say`) | yes (`generate_reply(instructions)`); `tool_choice="none"` is ignored with a warning | `INTERRUPT` scheduling stays an admin choice on the provider slot |
| realtime (OpenAI) | yes (SDK-generated reply) | skipped | yes | |
| text channel | yes (a text reply) | skipped | yes, as a new assistant message | rewind cancels running work |

### D-V4-37 — Flows: activity-scoped in v1; the 1.8.3 bump gates flow-node background tools

A node's background tools live in the node's `Agent(tools=…)`: **activity-scoped; cancellations and late results are carried into the node the caller is on** (R-V4-69, V4-20). A transition cancels the cancellable ones (feed: `cancelled`) and waits for the non-cancellable ones; both happen in the old node's drain, before the next node starts. For a cancelled call the flow runtime adds a short output to the next node's context ("Cancelled: the conversation moved to the next step before this lookup finished. Call it again if the caller still needs it."), so the next node never promises a result that will not come. A non-cancellable call's final result, which the SDK would deliver to the closed node and drop, is carried into the next node's context and used on its next reply (no reply is forced; one `info` event `flow_late_result`). Session-scoped `AsyncToolset` (results delivered to the next node) is deferred: it would require one shared tool list per session, which breaks per-node scoping and edge-tool rebuilding. Until the worker runs `livekit-agents ≥ 1.8.3`, the worker **downgrades** flow-node tools with a non-blocking mode to `blocking` (one warning per session, an `info` event) because of #7321; V4-14 bumps the pin and lifts the downgrade.

### D-V4-38 — What the panel shows and what the timeline records

Every `ToolExecutionUpdatedEvent` becomes an `ActivityEvent` upserted by `call_id` for tools with a non-blocking mode or any fillers, and for blocking tools whose run exceeds 1 s (so a slow blocking tool is also visible): `running` on start (headline "<label> started"), `running` again on each update with `detail={"message": …}` and headline = the update text, `done`/`error`/`cancelled` on end with `duration_ms`. Session events: existing `tool_call_started`/`tool_call_ended` unchanged; new `tool_call_updated {call_id, tool, message_preview}` and `tool_reply {call_ids, status, speech_id}` (worker-only types, `docs/CONTRACTS.md` §7). Pack `BackgroundRunner` jobs keep their own events.

---

## 3. Contracts

`contracts/src/lkap_contracts/tools.py` (V4-12, first commit):

```python
ToolExecutionMode = Literal["blocking", "background", "auto"]
DuplicatePolicy = Literal["allow", "reject", "replace", "confirm"]
DuplicateScope = Literal["name", "name_and_args"]


class ToolExecution(BaseModel):
    """How one tool runs relative to the conversation (BACKGROUND-TOOLS.md §2)."""

    mode: ToolExecutionMode | None = None
    """None = the agent's `tools.execution_default` when the tool is a read tool, else `blocking`."""
    announce: str | None = None
    """What the model is told on the first update; None = "Working on <label>." Spoken in the model's words."""
    auto_threshold_ms: int = Field(default=700, ge=0, le=5000)
    fillers: list[str] = Field(default=[], max_length=5)
    """Spoken verbatim after `filler_delay_s` of idle, one per interval; needs a TTS."""
    filler_delay_s: float = Field(default=4.0, ge=0.5, le=30)
    filler_interval_s: float = Field(default=8.0, ge=1, le=60)
    cancellable: bool | None = None
    """None = true for read tools, false otherwise."""
    on_duplicate: DuplicatePolicy | None = None
    """None = "reject" for read tools, "confirm" otherwise."""
    duplicate_scope: DuplicateScope = "name_and_args"
    max_duration_s: float = Field(default=60, gt=0, le=600)
    """Not applied to MCP tools: their bound is the server's `timeout_s`."""
    report_progress: bool = False
    """MCP tools only: forward the server's progress notifications as updates (ignored elsewhere)."""


#: Built-ins the agent-level default may background.
BACKGROUNDABLE_BUILTINS: Final[frozenset[str]] = frozenset({"search_knowledge", "http_request", "describe_current_frame"})
#: Tools that are never non-blocking (validator error; the worker ignores it with a warning).
NEVER_BACKGROUND_TOOLS: Final[frozenset[str]] = frozenset({
    "end_call", "transfer_call", "send_dtmf", "request_form", "escalate_to_human",
    "update_block", "show_document", "table_append", "push_note", "set_status", "pin_frame", "current_time",
})
```

- `HttpToolDefinition.execution: ToolExecution = ToolExecution()`; a validator: `silent_reply and execution.mode in ("background", "auto")` → error.
- `McpServerDefinition.tool_options: dict[str, ToolExecution] = {}` keyed by MCP tool name (validated ⊆ `allowed_tools` when that is set); `report_progress` is read from these entries only.
- `ToolsConfig.execution_default: ToolExecutionMode = "blocking"` and `ToolsConfig.builtin_execution: dict[str, ToolExecution] = {}` (keys ⊆ `BACKGROUNDABLE_BUILTINS`).
- `ToolMeta.execution: ToolExecution | None = None` (`lkap_contracts.packs`) so a pack tool can opt in without touching the runner.
- `VoiceConfig.thinking_sound: Literal["none", "keyboard_typing", "keyboard_typing2", "office_ambience"] = "none"` (the three `BuiltinAudioClip`s verified in 1.8.2; an uploaded clip is the conversation-tuning screen's job later).
- `docs/CONTRACTS.md` §7: the two event types.
- Exported: `ToolExecution`, `ToolExecutionMode`, `DuplicatePolicy`; the contracts gate regenerates `contracts/generated/**`, `web/src/contracts/lkap-contracts.d.ts`, `mcp/src/lkap_mcp/generated/**`. No migration: tool definitions and configs are JSON columns (HANDOFF rule 4 holds).

---

## 4. Runtime

### 4.1 `agent/src/lkap_agent/tools/execution.py` (new)

```python
@dataclass(frozen=True, slots=True)
class ResolvedExecution:
    mode: ToolExecutionMode; announce: str; auto_threshold_ms: int
    fillers: tuple[str, ...]; filler_delay_s: float; filler_interval_s: float
    cancellable: bool; on_duplicate: DuplicatePolicy; duplicate_scope: DuplicateScope
    max_duration_s: float; label: str

def resolve_execution(
    *, name: str, kind: Literal["builtin", "http", "mcp", "pack"], is_read: bool,
    declared: ToolExecution | None, agent_default: ToolExecutionMode, flow_node: bool, sdk_version: str,
) -> ResolvedExecution: ...
    # NEVER_BACKGROUND_TOOLS -> blocking (warn if declared otherwise)
    # declared.mode or (agent_default if is_read else "blocking")
    # flow_node and sdk_version < 1.8.3 and mode != blocking -> blocking (D-V4-37, one warning)
    # sdk_version = livekit.agents.__version__ (verified: `version.py:15`, re-exported by the package;
    # compared with packaging.version.Version, not string order)

def tool_flags(resolved) -> tuple[ToolFlag, DuplicatePolicy, DuplicateScope]: ...

async def run_with_policy(context: RunContext[Any], resolved: ResolvedExecution, work: Callable[[], Awaitable[Any]]) -> Any:
    task = asyncio.create_task(work(), name=f"lkap_tool_{resolved.label}")
    _register(context.session, context.function_call.call_id, task)   # per-session WeakKeyDictionary, no SDK privates
    try:
        if resolved.mode == "auto":
            done, _ = await asyncio.wait({task}, timeout=resolved.auto_threshold_ms / 1000)
            if done:
                return task.result()                       # inline: a classic tool, one generation
        if resolved.mode != "blocking":
            await context.update(resolved.announce)         # releases the LLM; the rest is deferred
        fillers = resolved.fillers if context.session.tts is not None else ()
        async with _filler(context, fillers, resolved):     # nullcontext() when empty
            return await asyncio.wait_for(task, resolved.max_duration_s)
    except TimeoutError as exc:
        raise ToolError(f"{resolved.label} took longer than {resolved.max_duration_s:g} seconds") from exc
    except asyncio.CancelledError:
        task.cancel(); raise
    finally:
        _unregister(context.session, context.function_call.call_id)

def cancel_running(session: AgentSession[Any]) -> int: ...   # text rewind, tests
```

`work()` is the tool's existing body (the HTTP request, the KB search, the vision call, the MCP call), unchanged. `context.update` is only ever called once by the wrapper; MCP `report_progress` may add more through the SDK.

### 4.2 Where the policy is applied

| Source | Change |
|---|---|
| Built-ins (`tools/builtin/__init__.py`, `search_knowledge.py`, `http_request.py`, `describe_current_frame.py`) | `build_builtin_tools(..., execution=config.tools.builtin_execution, execution_default=config.tools.execution_default)`; the three builders take a `ResolvedExecution`, declare `flags`/`on_duplicate`/`duplicate_scope` on `@function_tool`, and wrap their body in `run_with_policy`. `http_request` resolves `is_read` per call (`method == "GET"`); a non-GET call under a non-blocking policy runs blocking. Other built-ins untouched. |
| HTTP tools (`tools/declarative.py::build_http_tools`) | `_handler_for` becomes `run_with_policy(context, resolved, lambda: _request(...))`; `function_tool(raw_schema=..., flags=..., on_duplicate=..., duplicate_scope=...)`. `is_read = definition.method == "GET"`. |
| MCP (`tools/declarative.py::build_mcp_servers` → `build_mcp_toolsets`, `tools/mcp_client.py`) | Returns `MCPToolset(id=f"mcp_{definition.name}", mcp_server=GuardedMCPServerHTTP(...), tool_options={name: MCPToolOptions(flags, on_duplicate, duplicate_scope, report_progress)})` and the worker passes them in `Agent(tools=[*tools, *toolsets])`, dropping `mcp_servers=` (deprecated). The wrapper is not applied to MCP tools (the toolset owns the call); `announce` for an MCP tool is delivered through `report_progress` only, so an MCP tool with `mode="background"` and `report_progress=False` gets an api **warning** ("no progress messages: the model will not announce this tool"). `max_duration_s` is **not** applied to MCP tools: their bound stays the server's `GuardedMCPServerHTTP(timeout=definition.timeout_s)`, which `McpServerDefinition.timeout_s` already feeds (`MCPToolset` itself takes no timeout). The SDK sets the toolsets up itself: `AgentActivity._setup_toolsets` (`agent_activity.py:1150–1182`) attaches every `AsyncToolset` found in `agent.tools`/`session.tools` and awaits `toolset.setup()` (connect + list tools) for each under the activity lock — no explicit `setup()` call in LKAP. |
| Pack tools (`packs/base.py::ToolMeta.execution`) | `PlatformAgent` wraps a pack tool only when its `ToolMeta.execution` is set, through the same `run_with_policy` around the pack's coroutine (a `FunctionTool` re-created with the same schema via `function_tool(raw_schema=…)`). Insurance pack: no change. |
| Flow nodes (`flow/runtime.py::tools_for`, `mcp_servers_for` → `mcp_toolsets_for`, `node_agent.py`) | Tools are built once per session with `flow_node=True`; `mcp_toolsets_for` returns fresh toolsets per node (today's pattern). Edge tools untouched. |
| Session (`session_builder.py`) | `AgentSession(..., tool_handling={"async_options": ASYNC_TOOL_OPTIONS[mode]})`; `SessionPlan.thinking_sound`. |
| Agent (`platform_agent.py`) | `PIPELINE_NOTES` gain: cascaded/half_cascade — "Some tools report progress first and finish later; when a tool output says it is still in progress, tell the user it is on its way and never invent the result."; realtime — the same sentence appended. `on_function_tools_executed` unchanged (a background tool never has `silent_reply`). New `on_tool_execution(ev)` → `ctx.ui.activity(...)` per D-V4-38, registered by the worker as a synchronous handler like the others. |
| Worker (`main.py`, two hunks, granted) | Register `agent.on_tool_execution`; start `BackgroundAudioPlayer(thinking_sound=AudioConfig(BuiltinAudioClip.<x>, volume=0.6))` after `session.start` when `voice.thinking_sound != "none"` and the session has audio out (never on the text channel), stopped in the shutdown path. |
| Observability (`observability.py`) | Record `tool_call_updated` and `tool_reply`. |
| Text channel (`text_mode.py::rewind`) | Call `cancel_running(session)` before `truncate_to_turn`. |
| Pack runner (`tools/background.py`) | Docstring only. |

### 4.3 Sequence (cascaded, `auto`, a slow GET)

1. User: "What's the balance on policy H0-44721?" → LLM emits `lookup_policy(id=…)` (and possibly a second call; both start).
2. Wrapper: work starts; 700 ms pass without a result → `ctx.update("Looking up policy H0-44721.")` → executor returns that as the tool output → the LLM's reply this turn: "Let me pull that up for you." (one tool step spent). `ToolCallUpdated` → activity `running` "Looking up policy H0-44721."
3. Fillers (if a TTS): after 4 s of idle, "Still checking…" via `say()`; the user may talk over it; a user turn resets the dwell. Meanwhile the model, if asked something else, sees the placeholder pair and does not re-call `lookup_policy`.
4. Work returns → `{call_id}_final` pair inserted into `chat_ctx`; `_deliver_reply` waits for idle → `generate_reply(reply_at_tail | reply_maybe_covered)` → "Your balance is …". `ToolCallEnded(done)` → activity `done`; `ToolReplyUpdated(completed)` → `tool_reply` event.
5. Hangup at any point → activity close cancels the work (cancellable) → `cancelled`.

---

## 5. Composition

| With | Effect | Ruling |
|---|---|---|
| Knowledge auto-inject | Independent: auto-inject runs in `on_user_turn_completed`, before the LLM; `search_knowledge` in `auto` mode returns inline on a warm local store and announces only on a slow remote store. knowledge-and-memory P1-2 is satisfied by fillers on the tool path. | D-V4-31 |
| Preemptive generation | Unchanged for the turn that calls the tool. A deferred insert into `Agent.chat_ctx` while a speculative reply for the *next* user turn is in flight invalidates it (the SDK regenerates); cost only, no wrong answer. | note in §10 |
| `silent_reply` | Mutually exclusive with non-blocking modes (validator error). Honoured on realtime models, and in cascaded mode from livekit-agents 1.8.3 (the pipeline path reads `has_tool_reply`); below 1.8.3 a cascaded LLM answers every tool output. | D-V4-32; R-V4-68 |
| Flows | Activity-scoped; transitions cancel cancellable background tools and await the rest; cancellations and late results are carried into the node the caller is on; 1.8.3 gate. A batch that hands off gets no reply from the draining node (below). | D-V4-37; R-V4-64; R-V4-69 |
| Text channel | Announce and deferred reply as messages; no fillers; rewind cancels. | D-V4-36 |
| Gemini Live | Registry defaults `NON_BLOCKING`/`WHEN_IDLE` are what this design assumes; `INTERRUPT` makes the first update cut in (admin's choice on the slot, documented in the field help). | D-V4-36 |
| OpenAI Realtime | SDK-generated tool reply after current speech; no fillers. | D-V4-36 |
| `max_tool_steps` | One batch of parallel calls is one step (`_num_steps += 1` per batch, `agent_activity.py:3944`); what spends steps is sequential chaining: the announce reply is generated with `tool_choice="auto"` in the pipeline path, so it may call another tool whose announce spends another step. Three chained announces exhaust LKAP's default 3 → the console warns when `execution_default != "blocking"` and `max_tool_steps < 4`. The **deferred** reply is generated with `tool_choice="none"` (`tool_executor.py` `_deliver_reply`), so a background result never triggers a follow-up tool call by itself; the model can chain from it on the next user turn. | V4-12 validator; D-V4-33 |
| Pack `BackgroundRunner` | Unchanged; both feeds coexist in the activity block (different `source`s). | D-V4-29 |
| Telephony | `transfer_call`, `send_dtmf`, `end_call` never non-blocking; the thinking sound plays on SIP legs too (a `BackgroundAudioPlayer` publishes to the room). | D-V4-32 |

**Handoffs and sibling replies (R-V4-64, V4-19).** livekit-agents 1.8.3 waits for a whole batch of parallel calls (`generation.py` `_execute_tools_task` gathers them). It then emits `function_tools_executed` and, when a tool returned an `Agent`, calls `update_agent`. It still generates the tool reply on the **draining** activity, with the old node's instructions and `tool_choice="none"`, whenever any output of the batch has `reply_required` (`agent_activity.py` `_pipeline_reply_task_impl`). An edge tool's own output never asks for a reply. A sibling's output does: a blocking result, or a background or `auto` tool's first `ctx.update()` announce. So the caller heard the router before the target node (ask #126, 2a and 2b). Completion order inside the batch does not change this, because the decision is made over all the outputs at once. The flow runtime therefore registers a synchronous `function_tools_executed` handler (`FlowRuntime.on_function_tools_executed`). When the batch hands off, it calls `cancel_tool_reply()`, which marks every output `reply_required=False`. A `ToolResult(..., reply_required=False)` returned by the tool would not reach a background tool's announce, which the SDK builds from `ctx.update()`. The handler also carries the batch's call/output pairs into the target node, filtered to the target's tools the way `Agent.__init__` filters its copy. This is needed because the target copied the old context when the edge tool ran, before the SDK committed the batch, and the SDK merges nothing on a handoff. The target's `on_enter` `generate_reply()` answers from those pairs. A background sibling that is still running is cancelled by the drain, or awaited if it is not cancellable, as D-V4-37 says. Either way the target hears about it (R-V4-69, V4-20): the runtime also watches `tool_execution_updated`, remembers which node each call started in, and when a call from another node ends while the caller is on the target, it carries the outcome there. A cancellation becomes a synthetic call/output pair under the SDK's terminal id `{call_id}_final` (never a second output for the announce's call id), whose output starts with "Cancelled:". A late `_final` pair is copied from `session.history` (the SDK inserted it into the closed node and dropped its reply on `ActivityClosedError`) and recorded once as a `flow_late_result` info event. Both carries run before the target's activity starts, and its `on_enter` `settle()` awaits them, so the target's first reply already sees them. A batch without an edge is untouched. Realtime models that generate the tool reply server-side (`auto_tool_reply_generation`: Gemini Live) honour `reply_required=False` only where the plugin can send the result silently. For Gemini that is `SILENT` scheduling, which needs `tool_behavior=NON_BLOCKING` and is not available on Vertex. Everywhere else the model still answers the sibling, the plugin logs a warning, and nothing on this path has been tested live (ask #150).

---

## 6. UX

- **Filler speech**: per tool, verbatim, ≤ 5 phrases, rotated in order, `delay`/`interval` from the policy. Generated fillers are the `announce` (the model's words). The conversation-tuning screen (panels-and-capabilities #1) later adds agent-wide defaults; this design keeps them per tool.
- **Thinking sound**: `voice.thinking_sound`, one of three built-in clips; plays only while the agent is `thinking` (blocking waits, and the inline part of an `auto` run).
- **Activity block**: rows per D-V4-38; the row text shows the latest update message, the meter shows `running`; `detail.message` renders in the existing collapsible row (E2 in panels-and-capabilities).
- **Session detail timeline** (console): the two new events render like `tool_call_*` rows; `tool_reply.status=skipped` reads "already covered".

---

## 7. Console and MCP

**HTTP tool editor** (`web/src/components/console/tools/http-tool-editor-dialog.tsx`, Sonnet): a "Runs" select beside the `silent_reply` switch — *Agent default* (initial; posts `mode: null`, so the tool follows `tools.execution_default`; R-V4-57), *Blocking*, *In the background* (`background`), *Automatic: background if slower than N ms* (`auto`, N field, default 700); "What the agent says first" (`announce`); "Fillers while waiting" (up to five lines; helper: "spoken as written, needs a voice"); delay/interval; "Can be cancelled"; "Repeated calls" (`reject` / `confirm` / `replace` / `allow`, with the explanation from D-V4-32); "Give up after" (`max_duration_s`). The `silent_reply` switch and a non-blocking mode disable each other with the validator's message. Non-GET methods show the confirm default and a one-line note ("this tool changes something; the agent asks before running it twice").

**MCP editor** (`mcp-tool-editor-dialog.tsx`): a per-tool options table under `allowed_tools` (rows = the allowed tool names, or free text when unset): mode, announce via progress (`report_progress` switch), cancellable, repeated calls.

**Agent editor**: *Instructions & voice* (`agents/tabs/instructions-tab.tsx`) gains a "Conversation" card with "Read tools run" (`tools.execution_default`: blocking / automatic / background; helper naming which tools it affects), "Thinking sound" (`voice.thinking_sound`), and the `max_tool_steps` warning inline. The *Tools* section's built-in rows (`tools-tab.tsx`) show an "Execution" chip for `search_knowledge`, `http_request`, `describe_current_frame` opening the same fields in a `Dialog` (writes `tools.builtin_execution[name]`). No sheets (R-V3-2).

**MCP server** (`mcp/src/lkap_mcp/tools/tools.py`, `agents.py` docs): `tool_create_http(..., execution: ToolExecution | None = None)`, `tool_update(..., execution=…)`, `tool_create_mcp(..., tool_options: dict[str, ToolExecution] | None = None)`; `agent_update(patch={"tools": {"execution_default": "auto"}})` documented; `docs/concepts/tools.md` gains "Background tools" (modes, the safety rule, fillers need a voice); `docs/recipes/add-http-tool.md` one step; `tools.snap.json` regenerated.

---

## 8. Testing

**Unit (agent, `FakeLLM`/`ScriptedLLM` — offline).** `agent/tests/unit/test_tool_execution.py` (create):
- `resolve_execution`: read GET + default `auto` → `auto`; POST + default `auto` → `blocking`; POST declared `background` → `on_duplicate="confirm"`, `cancellable=False`; every `NEVER_BACKGROUND_TOOLS` name → `blocking` with a warning; `flow_node=True` + SDK `1.8.2` → `blocking`; `1.8.3` → declared.
- `run_with_policy` with a fake `RunContext` (records `update()` calls, exposes `session.tts`): `auto` with a 10 ms work → inline result, no `update`; `auto` with a 2 s work → one `update(announce)` then the result; `background` → `update` first; timeout → `ToolError`; cancellation propagates and cancels the inner task; fillers skipped when `session.tts is None`; `with_filler` entered exactly once when fillers exist.
- Real SDK path: an `AgentSession` with `FakeLLM` scripted to call a wrapped HTTP tool against a `respx` route with a 1.5 s delay; assert the turn's `ToolExecutionUpdatedEvent` sequence `started → updated → ended(done) → reply(scheduled|completed)`, that the placeholder pair appears in the next inference's `chat_ctx` while the tool runs (the fake LLM records its `chat_ctx`), and that `session.history` ends with the `_final` output. Duplicate: two scripted calls with the same args → the second returns the reject template text. Cancellable: the companion tools are in the fake LLM's recorded tool list only when a cancellable tool is registered. Text channel: `rewind` cancels the running task.
- Tripwire: imports of `RunContext.update`, `with_filler`, `ToolFlag.CANCELLABLE`, `MCPToolset`, `MCPToolOptions`, `ToolExecutionUpdatedEvent`, `AgentSession(tool_handling=)`.
- `test_declarative.py`: `build_http_tools` yields tools whose `info.flags`/`on_duplicate`/`duplicate_scope` match the policy; `build_mcp_toolsets` yields `MCPToolset`s with the `tool_options`; `test_platform_agent.py`: the pipeline-note sentence; `on_tool_execution` → activity events; `test_observability.py`: the two events; `test_session_builder`: `tool_handling` present per mode.

**api.** `test_config_service.py`: `silent_reply` + `background` → error at `tools[i].definition`; `builtin_execution` key outside `BACKGROUNDABLE_BUILTINS` → error; MCP `tool_options` key outside `allowed_tools` → error; `background` MCP tool without `report_progress` → warning; `execution_default != blocking` with `max_tool_steps < 4` → warning.

**Web.** `console-http-tool-editor.test.tsx`: the fields, the mutual disable, the posted `definition.execution`; `console-mcp-tool-editor.test.tsx`: the table; `console-instructions-tab.test.tsx`: the card; axe green; mobile 375 px.

**MCP.** `test_tools_tools.py`: `tool_create_http(execution=…)` plan body; `tools.snap.json` diff = the new arguments only; `test_docs_lint.py` green.

---

## 9. Live check (V4-12 for non-flow agents, V4-14 for flows)

Rules: R-V4-17 (a scratch api on its own port and DB, a worker under a fresh agent name, a Builder key minted and revoked, `Demo — ` naming, no telephony). Recorded in `docs/v4/_briefs/v4-12-live.md`.

1. **HTTP `auto`**: a GET tool `slow_echo` → `https://httpbin.org/delay/4` (on R-V4-18's allowlist; `allowed_hosts=["httpbin.org"]`, `timeout_s=10`), `execution={"mode":"auto","announce":"Fetching the echo now.","fillers":["Still fetching."],"filler_delay_s":3}`, on the generic template with `google/gemma-4-31b-it` cascaded. `chat_start`/`chat_send("Fetch the echo.")` → the first assistant message acknowledges within one turn, the activity block shows `running` with the announce text, a `tool_call_updated` event exists, and a later assistant message carries the result; `tool_reply.status == completed`. Repeat over voice in the browser: the filler is heard around 3 s after the acknowledgement; `EOU→first-audio` for the acknowledgement turn is under the agent's blocking-run figure (the session detail's latency columns).
2. **Fast path**: the same tool at `/delay/0` → no `tool_call_updated`, one assistant message (inline).
3. **Duplicate**: "Fetch it again" while running → the reply says it is on its way; no second `tool_call_started` for the same args.
4. **Cancel**: with `cancellable=true`, "Never mind, stop that" → `lk_agents_cancel_task` called, `tool_call_ended.status == cancelled`, activity `cancelled`.
5. **Hangup mid-tool**: disconnect during the delay → `cancelled` events, no error in the worker log, the job ends within the grace.
6. **Realtime (Gemini Live)**: the same agent switched to `realtime`; the announce is voiced by the model; no filler (expected); the deferred reply lands after idle; note the `tool_choice` warning count.
7. **Text channel**: `chat_send` on the text channel shows the two messages; `chat_rewind` during the delay → `cancelled`.
8. **Thinking sound**: a blocking `/delay/2` tool with `voice.thinking_sound="keyboard_typing"` → audible during the wait, silent after.
9. **Interrupted during the announce** (the behaviour the feature rests on, inferred from `agent_activity.py:3886–3906` — an interruption cancels the batch task while each tool task is shielded and the executor's `_on_done`/`_enqueue_reply` run independently — but not yet observed): the user talks over "Let me pull that up" → the acknowledgement is cut, the tool keeps running (`tool_call_ended.status == done`, not `cancelled`), and the result is still spoken at the next idle. If the result is lost instead, that is an ask against D-V4-33 before V4-13 ships the console fields.

Each step: session id, the event rows (`tool_call_*`, `tool_reply`), cost; no transcript beyond the quoted turns. A failing step is an ask with the log line, never a patch during the run (R-V4-20).

---

## 10. Risks and open items

- **Double generation on `background`** — by design; `auto` is the default recommendation, and the console helper says so.
- **Fillers on realtime** are silent until a plugin sets `supports_say`; the console helper text says "needs a voice (a TTS)"; the worker logs once per session when fillers are dropped.
- **#7321 (1.8.2)** — a slow blocking sibling can drop a flow handoff today; background tools do not make it worse (their dispatch returns at the first update) but V4-14's bump is the fix. The downgrade in D-V4-37 is conservative until then.
- **Deferred reply interrupted** is not rescheduled by the SDK (`TODO(long)`); the activity row still shows `done`, the `tool_reply` event `interrupted`; the model sees the result in its context next turn. Acceptable; noted in the concept doc.
- **No autonomous chaining from a background result**: the deferred reply runs with `tool_choice="none"`; a task that needs "fetch, then act on what came back" either acts on the next user turn or is one tool that does both steps inside its own work (with updates between them, as the LiveKit sample does). The concept doc says so; the console helper does not (too much for a field).
- **Interruption during the announce** is inferred from the reply loop, not observed; live step 9 settles it.
- **`chat_ctx` growth**: each update adds a call/output pair; with `max_length` five fillers and one announce this is bounded per call.
- **Pack runner drift**: two mechanisms (SDK executor, `BackgroundToolRunner`) for two audiences; a later package can move the pack's routine (non-urgent) path onto `ToolMeta.execution` — filed as an ask, not done.
- **Asks to file at V4-12 time**: (a) `background.py` docstring correction (done in-card); (b) `StartNode` and edge tools with background siblings once V4-14 lands; (c) an uploaded thinking/ambient clip (conversation-tuning screen).

---

## 11. Sources

- Installed `livekit-agents 1.8.2`: `voice/tool_executor.py`, `voice/events.py` (`RunContext`, `ToolExecutionUpdatedEvent`), `voice/filler_scheduler.py`, `voice/generation.py` (`_execute_tools_task`, `_inject_running_tool_calls`), `voice/agent_activity.py` (tools property 690–712, close 1618–1650, reply loop 3880–3990, realtime reply 4655–4740), `voice/agent_session.py` (`wait_for_idle` 1735, `generate_reply` 1558, `tool_handling` 400/453), `voice/agent.py` (136–139 deprecation), `llm/tool_context.py` (`ToolFlag`, `DuplicateMode`, `function_tool`), `llm/async_toolset.py`, `llm/mcp.py` (`MCPToolOptions`, `MCPToolset`), `llm/realtime.py` (`RealtimeCapabilities`), `livekit/plugins/google/realtime/realtime_api.py`, `livekit/plugins/openai/realtime/realtime_model.py`.
- LiveKit docs "Async tools" (https://docs.livekit.io/agents/logic/tools/async/, accessed 2026-09-25): the progress-update, filler, combined, foreground (`GetEmailTask`), cancellation, duplicate, `AsyncToolset` and prompt-template samples; the sentence "To keep a tool running across handoffs … bundle it into an `AsyncToolset`". Tools overview (https://docs.livekit.io/agents/build/tools/): "synchronously or in the background, letting the agent keep talking while long-running work completes".
- GitHub `livekit/agents` (via `gh`, 2026-09-25): code search `ctx.update(` → `tests/test_tools.py`, `tests/test_filler.py`, `tests/test_foreground_run_tracking.py`, `tests/test_realtime_agent_state_during_tool.py` (its docstring: "`ctx.with_filler()` speaks through `session.say()`, and a realtime model that advertises `supports_say` …"); PR #7321 (merged 2026-09-18; body quoted in §1.9); compare `livekit-agents@1.8.2...1.8.3` (#7321, #7334). No `examples/` file uses the API yet (the `examples/voice_agents` listing has none).
- Repo: `agent/src/lkap_agent/{platform_agent,session_builder,observability,text_mode}.py`, `tools/{background,declarative,mcp_client}.py`, `tools/builtin/**`, `flow/runtime.py`, `packs/src/packs/base.py`, `contracts/src/lkap_contracts/{tools,agent_config,packs,ui_protocol,providers}.py`, `web/src/panels/generic/blocks.tsx`, `web/src/components/console/tools/*.tsx`, `docs/research-v4/knowledge-and-memory.md` (§1.2–1.3, P0-0, P0-2, P1-2), `docs/research-v4/panels-and-capabilities.md` (C25, E2, roadmap #1), `docs/research-v4/_sources/{livekit-surface,capability-providers}.md`.
