# Generative-UI protocols for agents — state as of 2026-09-24

Sources fetched today unless marked UNVERIFIED. Note on dates: WebFetch's epoch-to-date conversions were unreliable, so dates below come only from literal GitHub API `published_at` strings or page text; npm versions are given without publish dates.

---

## 1. AG-UI (Agent–User Interaction Protocol) — CopilotKit

**Direct answer:** yes, `STATE_DELTA` is an array of RFC 6902 JSON Patch operations. Confirmed on two spec pages.

**Version:** spec **1.0** (docs at `docs.ag-ui.com/spec/1.0/…`, with a 0.x→1.0 migration guide and changelog). `@ag-ui/core` **1.0.0** on npm. GitHub releases are date-tagged; latest `release/2026-09-23` (published `2026-09-23T12:45:54Z`). The exact 1.0 cut date is UNVERIFIED (PR #2774 "AG-UI 1.0" not fetched). Repo: ~16k stars, MIT.

**Data model (event stream):**
- Lifecycle: `RUN_STARTED` (threadId, runId, `parentRunId`, `protocolVersion` in-band), `RUN_FINISHED` (optional `outcome`: absent = success, `interrupt`, `cancelled`; `interrupts[]` with id/reason/message/toolCallId/responseSchema/expiresAt), `RUN_ERROR`, `STEP_STARTED/FINISHED` (named, must balance).
- Text: `TEXT_MESSAGE_START/CONTENT/END` (+ `TEXT_MESSAGE_CHUNK`).
- Tools: `TOOL_CALL_START/ARGS/END/RESULT` (+ `TOOL_CALL_CHUNK`); tool results now accept content parts (images/documents), not just strings.
- State: `STATE_SNAPSHOT` ("replaces the agent state wholesale"), `STATE_DELTA` ("amends the current state with an RFC 6902 patch. The baseline is the consumer's current state" — ops `add/remove/replace/move/copy/test`; malformed patches are fatal, well-formed-but-failing patches are skipped with a warning), `MESSAGES_SNAPSHOT` (in-place replace by id, append new, drop absent — except activity/reasoning messages).
- 1.0 additions: `ACTIVITY_SNAPSHOT/DELTA` (delta also RFC 6902), `REASONING_*` family (replaces `THINKING_*`), `REASONING_ENCRYPTED_VALUE`, `SUBAGENT_STARTED/FINISHED/ERROR`, `RAW`, `CUSTOM` (name/value), draft `MetaEvent`.
- Ordering: no per-event seq field; ordering is a transport-binding contract ("ordered, complete delivery… in the order the producer emitted them"). Capabilities page lists "Resumable: stream recovery via sequence numbers" as a declared capability.

**Transports (1.0):** HTTP POST + SSE is the required binding (one JSON event per frame). HTTP + Protobuf is an optional binding: media type `application/vnd.ag-ui.event+proto`, negotiated via `Accept`, **4-byte big-endian length-prefixed frames**; unknown fields and JSON-Patch extension members are dropped on the binary wire. WebSockets/message buses are allowed as custom transports but not standard bindings; capabilities discovery lists SSE, HTTP binary, WebSocket, push/webhooks.

**Generative UI:** AG-UI positions itself as the runtime channel, not a UI spec. It supports "static" generative UI (tool call name → frontend-registered component) and "declarative" specs carried as payloads — it names **A2UI (Google)**, **Open-JSON-UI ("OpenAI")** and **MCP-UI** — plus custom specs. A separate draft proposes a `generateUserInterface` tool (description/data/output schema) handled by a secondary generator.

**HITL:** interrupt-aware run lifecycle — `RUN_FINISHED{outcome:interrupt}` then client re-submits `RunAgentInput` with a `resume[]` (interruptId, status resolved/cancelled, payload validated against `responseSchema`). LangGraph and AWS Strands have native interrupt support.

**Predictive state updates:** not present in the AG-UI 1.0 spec pages. It is a CopilotKit/LangGraph-layer feature (`copilotkit_emit_state(config, state)` mid-node; final node state is the source of truth). Wire mapping (STATE_DELTA vs CUSTOM) — UNVERIFIED.

**Integrations (docs):** LangGraph/LangChain, CrewAI (partners); Microsoft Agent Framework, Google ADK, AWS Strands, AWS Bedrock AgentCore, Mastra, Pydantic AI, Agno, LlamaIndex, AG2 (first-party); Claude Agent SDK, Claude Managed Agents, Langroid (community). The GitHub README table still shows "AWS Bedrock Agents: In Progress" — minor inconsistency.

- https://docs.ag-ui.com/spec/1.0/events/state.md (accessed 2026-09-24)
- https://docs.ag-ui.com/concepts/state (accessed 2026-09-24)
- https://docs.ag-ui.com/concepts/events (accessed 2026-09-24)
- https://docs.ag-ui.com/spec/1.0/basic/transports/http-protobuf.md (accessed 2026-09-24)
- https://docs.ag-ui.com/concepts/interrupts.md (accessed 2026-09-24)
- https://docs.ag-ui.com/concepts/generative-ui-specs.md (accessed 2026-09-24)
- https://github.com/ag-ui-protocol/ag-ui (accessed 2026-09-24)

---

## 2. CopilotKit generative UI

**Version:** `@copilotkit/react-core` **1.73.3** on npm. Docs distinguish a deprecated **v1** API and current **v2** entry points (`@copilotkit/react-core/v2`, `@copilotkit/runtime/v2`).

**Registration model — fixed catalogue on the client, yes.** Components are registered in React via hooks and exposed to the agent as named tools with Zod schemas; the agent references them by tool name, and CopilotKit "renders your component directly in the chat with the tool's arguments as props."

- v2 hooks: `useFrontendTool` (client tool with handler), `useComponent` (component-as-tool: name, description, Zod `parameters`, `render`, optional `agentId`), `useRenderTool` / `useDefaultRenderTool` / `useRenderToolCall` (render backend tool calls), `useHumanInTheLoop` (render + `respond()`; status `InProgress → Executing → Complete`), `useInterrupt` (LangGraph `interrupt()`), `useAgent` / `useAgentContext` (state: `agent.state`, `agent.setState()`).
- Legacy v1 (deprecated but supported): `useCopilotAction` (name, description, parameters, handler, `render`, `renderAndWaitForResponse`, `available`, `followUp`), `useCoAgent`, `useCoAgentStateRender`, `useCopilotReadable`. Docs: "useCopilotAction is still supported, but we recommend migrating to useFrontendTool from the v2 API"; `renderAndWaitForResponse` → `useHumanInTheLoop` with `render`.
- Shared state rides on AG-UI `STATE_SNAPSHOT`/`STATE_DELTA` over SSE (built-in agent has `AGUISendStateSnapshot`/`AGUISendStateDelta` tools).
- Declarative specs: enabling `a2ui: {}` in `CopilotRuntime` auto-renders A2UI JSONL output; docs also reference JSON Render and Hashbrown.
- `CopilotTextarea`: AI-autocomplete textarea; per search snippets, in 2026 its autosuggestions "no longer connect to a backend" and the v1 backend was removed in 1.50.0 (snippet-level, UNVERIFIED).
- "CoAgents": the docs index now labels the section "Sub-agents"; original marketing definition UNVERIFIED from a direct page.

- https://docs.copilotkit.ai/concepts/which-hook (accessed 2026-09-24)
- https://docs.copilotkit.ai/generative-ui (accessed 2026-09-24)
- https://docs.copilotkit.ai/human-in-the-loop (accessed 2026-09-24)
- https://docs.copilotkit.ai/reference/v1/hooks/useCopilotAction (accessed 2026-09-24)
- https://docs.copilotkit.ai/langgraph/shared-state/predictive-state-updates (accessed 2026-09-24)

---

## 3. Vercel AI SDK

**Direct answers:** current major is **7.x** (`ai@7.0.113`; 7.0 released 2026-06-25), not 5/6. RSC `streamUI` is **experimental, not deprecated** — exact warning: "AI SDK RSC is currently experimental. We recommend using AI SDK UI for production." A migration guide RSC→UI exists.

**Generative UI model (AI SDK UI):** `useChat` messages carry a `parts[]` array; tool invocations appear as typed parts `tool-<toolName>` (or `dynamic-tool` for runtime-typed tools). Part `state` machine: `input-streaming`, `input-available`, `output-available`, `output-error`, plus 6.0's `approval-requested`, `approval-responded`, `output-denied`. The client switches on `part.type`/`part.state` and renders its own component with `part.output` — i.e. a **client-side fixed mapping of tool name → component**. Client-executed tools via `onToolCall`, results via `addToolOutput`, auto-continue with `sendAutomaticallyWhen`.

**Agents:** 6.0 replaced `Experimental_Agent` with **`ToolLoopAgent`** (`instructions`, default `stopWhen` 20 steps). 7.0 added **`WorkflowAgent`** (durable execution across restarts/approvals), kept `ToolLoopAgent`, HMAC-signed tool approvals, `DirectChatTransport`, `@ai-sdk/otel`, stable `generateSpeech`/`transcribe`, experimental browser-to-provider realtime WebSocket sessions. 7.0 renames: `system→instructions`, `onFinish→onEnd`, `stepCountIs→isStepCount`; ESM-only, Node 22+. No new generative-UI/RSC features in 7.

- https://ai-sdk.dev/docs/ai-sdk-ui/generative-user-interfaces (accessed 2026-09-24)
- https://ai-sdk.dev/docs/ai-sdk-ui/chatbot-tool-usage (accessed 2026-09-24)
- https://ai-sdk.dev/docs/ai-sdk-rsc/overview (accessed 2026-09-24)
- https://ai-sdk.dev/docs/migration-guides/migration-guide-6-0 (accessed 2026-09-24)
- https://vercel.com/changelog/ai-sdk-7 (accessed 2026-09-24)

---

## 4. MCP-UI

**Status:** now an SDK **implementing the MCP Apps standard** ("fully compliant with the MCP Apps specification"), with legacy support for pre-standard hosts via `UIResourceRenderer`. Repo moved to `MCP-UI-Org/mcp-ui` (~5.2k stars). `@mcp-ui/client` **7.1.1**, depending on `@modelcontextprotocol/ext-apps ^1.2.0`. Server SDKs: TS, Ruby gem, PyPI.

**Data model:** `UIResource` = `{type:'resource', resource:{uri:'ui://…', mimeType, text|blob}}`. Current primary MIME is `text/html;profile=mcp-app`; external URLs via iframe (`text/uri-list` per snippet). **Remote DOM (Shopify) has been removed** — mcpui.dev page snippet: "Remote DOM support has been removed from MCP-UI. This documentation is preserved for historical reference only" (MIME was `application/vnd.mcp-ui.remote-dom+javascript; framework=react|webcomponents`). Direct page fetch 404'd — mark removal as snippet-verified only.

**Sandboxing:** "In all content types, the remote code is executed in a sandboxed iframe."

**UI actions:** iframe → host `postMessage`. Confirmed shape: `{type:'tool', messageId?, payload:{toolName, params}}`; async acks `ui-message-received` / `ui-message-response` (success/error). The `prompt`, `intent`, `notify`, `link` action types are listed by name but their payload shapes were not on the pages fetched (UNVERIFIED). Host→iframe now uses MCP Apps notifications (`ui/notifications/tool-input`, `tool-input-partial`, `tool-result`, `tool-cancelled`, `size-changed`, `host-context-changed`, `ui/resource-teardown`).

- https://mcpui.dev/guide/introduction (accessed 2026-09-24)
- https://mcpui.dev/guide/protocol-details (accessed 2026-09-24)
- https://github.com/idosal/mcp-ui (redirects to MCP-UI-Org; accessed 2026-09-24)

---

## 5. MCP Apps (official MCP extension, SEP-1865)

**Status:** **Stable, spec version 2026-01-26**; extension id `io.modelcontextprotocol/ui`; a draft is open. Co-developed by Anthropic, OpenAI and the MCP-UI authors (MCP blog, 2026-01-26). Extensions framework formalized under core protocol version **2026-07-28** (negotiated via `_meta["io.modelcontextprotocol/clientCapabilities"].extensions` and `server/discover`). SDK `@modelcontextprotocol/ext-apps` **2.0.1** (GitHub `published_at` `2026-09-24T10:01:20Z`; 2.0.0 moved to MCP TS SDK 2.0 split packages + zod 4; wire protocol unchanged from 1.x).

**Data model:** tool `_meta.ui.resourceUri` → `ui://` resource with MIME `text/html;profile=mcp-app` (mandatory in this version). Resource `_meta.ui`: `csp{connectDomains, resourceDomains, frameDomains, baseUriDomains}`, `permissions` (camera/mic/geolocation/clipboard-write), `domain` (dedicated sandbox origin), `prefersBorder`. Tool `_meta.ui.visibility`: `["model","app"]` default, `["app"]`, `["model"]`.

**Bridge (JSON-RPC over postMessage):** View→Host: `ui/initialize`, `tools/call` (there is no `ui/request-tool-call`), `resources/read`, `ui/message`, `ui/open-link`, `ui/update-model-context`, `ui/request-display-mode` (inline/fullscreen/pip), `notifications/message`. Host→View: `ui/notifications/tool-input`, `tool-input-partial` (streamed args), `tool-result`, `tool-cancelled`, `size-changed`, `host-context-changed`, `ui/resource-teardown`.

**Sandboxing:** sandboxed iframe; web hosts use a **double-iframe** pattern (outer sandbox proxy on a different origin loads the inner iframe with CSP headers built from declared domains; default `default-src 'none'`). Host controls capabilities (can restrict callable tools, disable open-link).

**Hosts (client matrix):** Claude web/desktop, VS Code GitHub Copilot, Microsoft 365 Copilot, Goose, Postman, MCPJam, **ChatGPT**, Cursor, Archestra.AI, PostHog Code. (ext-apps README also lists mcp-use inspector, Alpic Playground.) OpenAI's plugin changelog: "2026-02-22: ChatGPT is now fully compatible with the MCP Apps spec."

**Relationship:** standardizes patterns from MCP-UI and OpenAI's Apps SDK; `@mcp-ui/client` is the recommended React host renderer, `App Bridge` module the low-level host option.

- https://modelcontextprotocol.io/extensions/apps (accessed 2026-09-24)
- https://github.com/modelcontextprotocol/ext-apps/blob/main/specification/2026-01-26/apps.mdx (accessed 2026-09-24)
- https://modelcontextprotocol.io/extensions/client-matrix (accessed 2026-09-24)
- https://modelcontextprotocol.io/extensions/overview (accessed 2026-09-24)
- https://blog.modelcontextprotocol.io/posts/2026-01-26-mcp-apps/ (accessed 2026-09-24)

---

## 6. OpenAI Apps SDK → "Plugins" in ChatGPT

**Rename:** OpenAI docs now live under `developers.openai.com/plugins/…`; Wikipedia: "In July 2026, ChatGPT apps were renamed to plugins" (a search snippet gives 9 July 2026 — day UNVERIFIED). A plugin = MCP server + skills + optional UI; public directory submission via OpenAI Platform.

**UI model today:** "ChatGPT implements the open MCP Apps standard" — `text/html;profile=mcp-app`, `_meta.ui.resourceUri`, JSON-RPC over postMessage; "new UI should use shared bridge methods." `window.openai` remains as ChatGPT-specific extensions + compatibility aliases: `toolInput`, `toolOutput`, `widgetState`/`setWidgetState` (sync, whole-blob), `callTool`, `sendFollowUpMessage({prompt})`, `requestDisplayMode({mode: inline|pip|fullscreen})`, `openExternal({href, redirectUrl})`, `requestClose`, `requestModal`, `requestCheckout` (private beta), `uploadFile`/`selectFiles`; env: `theme, displayMode, maxHeight, safeArea, locale`. Legacy `_meta["openai/outputTemplate"]` is an alias for `_meta.ui.resourceUri`; `openai/widgetCSP` legacy vs `_meta.ui.csp`; `openai/visibility` deprecated 2026-07-21 in favor of `_meta.ui.visibility`. `text/html+skybridge` is the legacy MIME — deprecation evidence is third-party (GitHub issues, fast-agent docs); OpenAI's changelog fetch showed no skybridge entry (UNVERIFIED from OpenAI).

**CSP/sandbox:** iframe; `_meta.ui.csp` allowlists; "Nested frames are blocked by default."

**2026 changelog highlights:** MCP Apps compatibility (02-22), file helpers (03-09/03-24), `hostContext.styles.variables` (05-28), granular app permissions (06-12), stable OAuth callbacks/CIMD (08-21).

- https://developers.openai.com/plugins/build/chatgpt-ui (accessed 2026-09-24)
- https://developers.openai.com/plugins/reference (accessed 2026-09-24)
- https://developers.openai.com/plugins/changelog (accessed 2026-09-24)
- https://developers.openai.com/apps-sdk/build/custom-ux (accessed 2026-09-24)

---

## 7. A2UI (Google) — exists, verified

Apache-2.0, "created by Google with contributions from CopilotKit"; repo now `a2ui-project/a2ui` (~16.5k stars). **Not Gemini-specific** — transport-agnostic (A2A, AG-UI, MCP, WebSockets, REST); announced 2025-12-15; v0.9 released 2026-07-03 (InfoQ). **Current production: v0.9.1** (spec closed); **v1.0 is a release candidate** (stable target "Q4 2026" per search snippet — UNVERIFIED).

**Data model (JSONL envelopes, each with `version`):** server→client `createSurface{surfaceId, catalogId, theme, sendDataModel}`, `updateComponents{surfaceId, components[]}` (flat adjacency list: `{id, component, …props, children:[ids] | {path, componentId} template}`; one `id:"root"`), `updateDataModel{surfaceId, path (JSON Pointer RFC 6901), value}` (omit path = replace whole model), `deleteSurface`. Client→server: `action{name, surfaceId, sourceComponentId, timestamp, context}`, `error`. v1.0 adds `callRendererFunction`/`rendererFunctionResponse`, `callAgentFunction`/`agentFunctionResponse`, components embedded in `createSurface`, UAX#31 naming, `$id` catalog schemas. Basic catalog: Text, Image, Icon, Video, AudioPlayer, Row, Column, List, Card, Tabs, Modal, Divider, Button, CheckBox, TextField, DateTimeInput, ChoicePicker, Slider.

**Trust:** data-not-code; "Agents can only use pre-approved components from your catalog"; client functions registered by name; multi-agent attribution validation; `sendDataModel` privacy scoping. Renderers: Lit, Flutter, Angular, React (Compose/SwiftUI on roadmap).

- https://a2ui.org/specification/v0.9.1-a2ui/ (accessed 2026-09-24)
- https://a2ui.org/specification/v1.0-a2ui/ (accessed 2026-09-24)
- https://github.com/google/A2UI (accessed 2026-09-24)
- https://www.infoq.com/news/2026/07/google-a2ui-genui/ (accessed 2026-09-24)

---

## 8. Constrained-JSON-tree approaches and Adaptive Cards

**json-render (Vercel Labs)** — `@json-render/core` **0.21.0**, ~18.2k stars, Apache-2.0. `defineCatalog(schema, {components:{Name:{props: zod, description}}, actions})`; spec is flat `{root, elements:{id:{type, props, children[], visible, watch}}}` with `$state`/`$cond`/`$template`/`$computed` expressions. **SpecStream = JSONL where each line is an RFC 6902 JSON Patch op**, compiled progressively (`createSpecStreamCompiler`, `useUIStream`). Renderers for React/Vue/Svelte/Solid/RN/Remotion/PDF; `@json-render/mcp` for MCP Apps hosts.

**Thesys C1 / Crayon → OpenUI** — `docs.thesys.dev` now 307-redirects to `openui.com`. OpenUI (`thesysdev/openui`, ~9.8k stars, MIT; `@openuidev/lang-core` 0.3.0) uses **"OpenUI Lang", a compact streaming DSL (not JSON)**, claims up to 67% fewer tokens than JSON; components are constrained by a prompt generated from the allowed component library; Gateway + "Autofix" for malformed output. Crayon repo fetch returned OpenUI content (likely redirected) — Crayon status UNVERIFIED. No sandboxing story documented.

**Open-JSON-UI ("OpenAI's declarative schema")** — named by AG-UI/CopilotKit docs and search snippets only; both CopilotKit URLs I fetched served A2UI content and no OpenAI source was found. UNVERIFIED.

**Microsoft Adaptive Cards** — fixed JSON schema (`type: AdaptiveCard`, `version`, `body[]`, `actions[]`), "purely declarative — no code is needed or allowed", host renders natively. Copilot Studio doc (updated 2026-08-03): supports **schema 1.6 and earlier**; Bot Framework Web Chat 1.6 (no `Action.Execute`), Teams and live-chat widget limited to 1.5. Whether anything newer than 1.6 exists is UNVERIFIED (the explorer is JS-rendered; the 1.6.0 schema JSON fetch 404'd).

- https://github.com/vercel-labs/json-render (accessed 2026-09-24)
- https://json-render.dev/docs/streaming (accessed 2026-09-24)
- https://github.com/thesysdev/openui (accessed 2026-09-24)
- https://learn.microsoft.com/en-us/microsoft-copilot-studio/adaptive-cards-overview (accessed 2026-09-24)
- https://learn.microsoft.com/en-us/adaptive-cards/ (accessed 2026-09-24)

---

## 9. Pipecat RTVI and "Pipecat UI"

**RTVI standard:** docs page says v1.0 (June 2025); Pipecat **1.2.0** (GitHub `published_at` `2026-05-14T21:50:46Z`) "bumps the RTVI PROTOCOL_VERSION from 1.2.0 to 1.3.0" — version-labelling discrepancy noted. Envelope: `{label:"rtvi-ai", type, id?, data?}` over WebSocket / WebRTC data channel / Daily. `@pipecat-ai/client-js` 1.13.1.

**UI alongside voice ("UI Agent Protocol", 1.2.0):**
- Client→server: `ui-snapshot` (accessibility tree with stable refs + selection; server injects as `<ui_state>` before each LLM turn), `ui-event{event, payload}` (routed to `@ui_event(name)` handlers), `ui-cancel-task`; plus generic `client-message{t, d}` / `server-response`.
- Server→client: `ui-command` with a **fixed imperative vocabulary** — `scroll_to`, `highlight`, `select_text`, `click`, `set_input_value`, `toast`, `navigate`, `focus` — plus custom `send_command(name, payload)`; `ui-task`; `ui-job-group` lifecycle (`group_started → job_update* → job_completed×N → group_completed`); generic `server-message` (arbitrary JSON).
- Architecture: `UIWorker` runs as a **separate parallel worker with its own LLM context** next to the voice pipeline; voice agent delegates screen turns (`job("ui", name="respond")`) and `respond_to_job()` chooses verbatim TTS / text-for-voice-LLM / silent.
- Transcription/TTS events: `user-transcription`, `bot-tts-text`, `bot-output`, `user-started/stopped-speaking`.

**Client UI:** `@pipecat-ai/voice-ui-kit` (0.14.0, ~417 stars) is **superseded by "Pipecat UI"**, a shadcn registry (`npx shadcn@latest registry add @pipecat`; React 19, Tailwind 4) with mic/camera/connection controls, transcripts, visualizers. Whether it ships default `ui-command` handlers is not on the page fetched (a Pipecat release note says defaults live in `@pipecat-ai/client-react`).

- https://docs.pipecat.ai/client/rtvi-standard (accessed 2026-09-24)
- https://docs.pipecat.ai/pipecat/learn/ui-worker.md (accessed 2026-09-24)
- https://docs.pipecat.ai/client/pipecat-ui.md (accessed 2026-09-24)
- https://github.com/pipecat-ai/pipecat/releases/tag/v1.2.0 (accessed 2026-09-24)

---

## Analysis

### (a) Fixed client-side catalogue vs arbitrary agent-generated markup

| Fixed catalogue (agent picks from client-owned set) | Arbitrary markup (server ships HTML/JS) | Carrier / both |
|---|---|---|
| CopilotKit (`useComponent`/tool-name mapping), Vercel `useChat` tool parts, A2UI (Basic catalog + catalogId), json-render (`defineCatalog` + Zod), OpenUI Lang (allowed-component prompt), Adaptive Cards (fixed schema, host-rendered), RTVI `ui-command` (fixed verb vocabulary) | MCP-UI, MCP Apps, ChatGPT plugins (`text/html;profile=mcp-app` in iframe) | AG-UI — transport that carries tool-mapped static UI *or* A2UI/Open-JSON-UI/MCP-UI payloads |

Your current design (typed Pydantic block state models, no arbitrary HTML) sits squarely in the left column with A2UI, json-render and Adaptive Cards.

### (b) How each sandboxes untrusted UI

- **Sandboxed by construction (data, not code):** A2UI (catalog-only, functions registered by name, attribution checks), json-render (Zod validation against catalog), Adaptive Cards ("no code is needed or allowed"), CopilotKit/Vercel (agent only supplies args to developer-authored components), RTVI (client decides which `ui-command`s to honor; `ui-snapshot` exposes the a11y tree to the agent, so the client controls what is snapshotted — the equivalent of your "strict public config").
- **Sandboxed at runtime:** MCP Apps / MCP-UI / ChatGPT — iframe `sandbox`, JSON-RPC over `postMessage`, host-built CSP from `_meta.ui.csp` (`default-src 'none'` default), double-iframe origin isolation on web hosts, host-gated tool calls, explicit `permissions` for mic/camera. OpenUI: no sandbox story documented.

### (c) JSON-patch-style state deltas comparable to a `set/append/remove/upsert` envelope

- **AG-UI `STATE_DELTA` / `ACTIVITY_DELTA`:** RFC 6902 array, applied in order against consumer state; snapshot for resync. Closest analogue to yours.
- **json-render SpecStream:** JSONL of RFC 6902 ops for the *UI spec itself*.
- **A2UI `updateDataModel`:** JSON Pointer `path` + `value` = set/replace; no path = whole-model replace. No remove/append op seen in the pages fetched.
- **MCP Apps / ChatGPT:** no state-delta protocol — `tool-input-partial`/`tool-result` notifications plus `widgetState` as a whole blob.
- **Vercel:** tool-part state machine with streamed input; no state patches.
- **RTVI:** imperative commands, not patches; `server-message` is free-form JSON.
- **Adaptive Cards:** whole-card replace.

Practical takeaway: your `set/append/remove/upsert` maps onto RFC 6902 `replace/add/remove` (append = `add` to `/-`; upsert = `add` on an object path). Either adopt RFC 6902 outright or keep your envelope and document the one-to-one mapping so your patches are drop-in for AG-UI consumers and json-render renderers without a translation layer. AG-UI's protobuf binding shows the same JSON Patch ops can go over a compact binary channel — relevant to a LiveKit data channel.

### (d) What a voice-first platform gains from each in a live call

- **AG-UI:** the only one with a spec'd compact binary framing (4-byte length prefix, `application/vnd.ag-ui.event+proto`) and a formal ordered-delivery/termination contract; interrupt/resume and `cancelled` outcomes map cleanly to barge-in. Cost: it is a chat-run model (RUN_STARTED…RUN_FINISHED), so a continuous voice session needs run boundaries defined per turn.
- **A2UI / json-render:** JSONL progressive rendering lets UI appear while TTS is still speaking; catalog validation keeps the LLM from emitting anything your renderer cannot draw. Token cost of emitting component trees per turn is the latency risk (OpenUI's DSL exists precisely to cut that).
- **MCP Apps / ChatGPT / MCP-UI:** richest UI, but iframe boot + resource fetch per tool call is a visible delay in a call; mitigations are `_meta.ui.resourceUri` preloading and `tool-input-partial` streaming; `ui/update-model-context` gives voice↔screen coupling; `tool-cancelled` supports barge-in. Requires hosting sandbox origins and CSP infra you don't have today.
- **RTVI / Pipecat:** the only one designed around a voice pipeline — `ui-snapshot` grounds the LLM in screen state, `ui-command` is a small fixed verb set, and the `UIWorker` runs as a separate parallel LLM context so UI work never sits on the speech path. Directly validates your architecture: keep a block catalogue, add a snapshot-to-agent channel and a cancel primitive.
- **CopilotKit / Vercel:** React-side ergonomics for tool→component mapping and HITL (`respond()` / approval states); nothing voice-specific.

Net: nothing fetched argues for moving a voice platform to arbitrary HTML/iframes; the industry's own voice stack (Pipecat) and the two newest Google/Vercel specs (A2UI, json-render) all chose fixed catalogues with patch/pointer-based state streaming. The one thing worth adopting from the "dynamic" camp is MCP Apps-style host capability gating and cancellation semantics.

### (e) Could not verify

- Open-JSON-UI: no OpenAI primary source; CopilotKit pages served A2UI content.
- MCP-UI `prompt`/`intent`/`notify`/`link` payload shapes (only `tool` confirmed); Remote DOM removal (search snippet; page 404).
- AG-UI 1.0 exact release date; PredictState wire mapping; Bedrock status inconsistency.
- A2UI 0.9.1 release date and "v1.0 stable Q4 2026".
- `text/html+skybridge` deprecation from an OpenAI source; "9 July 2026" plugins rename day.
- Adaptive Cards: whether anything beyond 1.6 exists (explorer/schema fetches failed).
- RTVI version labelling (docs "1.0 June 2025" vs Pipecat `PROTOCOL_VERSION 1.3.0`); whether Pipecat UI ships default `ui-command` handlers.
- Crayon's current status; exact C1→OpenUI relationship (only the redirect and a Product Hunt snippet, launch 2026-03-11).
- MCP Apps draft-spec deltas vs 2026-01-26 (fetch returned a 2025-11-21 document).
- CopilotKit "CoAgents" original definition; CopilotTextarea 2026 changes (snippets only).
- All npm publish dates (versions verified; dates not).