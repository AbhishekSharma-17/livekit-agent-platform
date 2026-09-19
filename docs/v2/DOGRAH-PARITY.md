# Dograh parity — feature by feature

Status: **decided** (Fable 5.1, 2026-09-19). Source for the Dograh column: `../research-v2/dograh.md` (verified as of 2026-09-19; 8 carriers + Asterisk, plaintext credentials, no RBAC, no avatars/vision, alpha Helm, open bugs on campaigns/KB/Telnyx). Legend: ✓ shipped · ◐ partial · ✗ absent · **P1** Phase 1 core · **P1s** Phase 1 stretch · **P2/P3** later.

| Area | Feature | Dograh | LKAP v1 | LKAP v2 | Where we differ or beat it |
|---|---|---|---|---|---|
| Runtime | Media/transport | Pipecat fork, asyncio tasks in uvicorn, nginx least_conn, coturn | LiveKit rooms + `AgentSession` | same | SFU rooms give per-call isolation, NAT traversal, multi-participant and horizontal scale without WebSocket affinity or a custom worker-sync layer. |
| Runtime | Multi-deployment (many LiveKit servers/projects) | n/a (no LiveKit) | ✗ (env-bound) | **P1** connections + pools + supervisor | New capability class; Dograh has one media stack per install. |
| Providers | STT/LLM/TTS breadth | 34 enum entries | ~17 (4 live-verified: inference STT/LLM/TTS, fastembed) | **P1** ~90 registry entries (32 STT, 15 LLM, 48 TTS), `verified` grows only via live stages | Registry-driven forms and factory; no per-vendor code paths. |
| Providers | Realtime / S2S | 7 | 2 | **P1** 9 (Gemini, OpenAI, GPT-Live, Azure, xAI, Nova Sonic, Ultravox, Phonic, PersonaPlex deferred) | Half-cascade mode with capability gating; Dograh has no half-cascade. |
| Providers | Managed inference without vendor keys | Dograh service key | LiveKit Inference | **P1** per-connection Inference flag | Equivalent; ours is gated per connection. |
| Providers | OpenAI-compatible / self-hosted LLM | ✓ (base URL) | ✓ (openai.LLM base_url) | **P1** explicit entries (Ollama, vLLM, OpenRouter…) | Parity. |
| Providers | Credential storage | **plaintext JSON** | Fernet vault | Fernet vault + per-connection secrets + key rotation CLI | **Beat**: encrypted at rest, decrypt only in the api, blast radius per connection. |
| Providers | Credential test + model/voice lists | ◐ (masking only) | test only | **P1** test + vendor catalogs cached | Beat. |
| Providers | Per-workspace enable/disable | ✗ | ✗ | **P1** | Beat. |
| Providers | VAD / turn detection / NC selection | three-tier, per-node interruption | fixed defaults | **P1** slots + connection-aware turn detector | Parity+ (Krisp on Cloud). |
| Avatars & vision | Avatars | ✗ | 2 (bey, tavus; never live-tested) | **P1** 15 plugins, pickers, options; bey/tavus flip to `verified` in V2-20 with keys | **Beat** (Dograh has none). |
| Avatars & vision | Camera / screen share vision | ✗ | ✓ | ✓ + `video` block | Beat. |
| Building | Visual flow builder | ✓ (React Flow, LLM-evaluated edges, global node, versions, diff) | ✗ | **P1s** FlowSpec + canvas; versions/diff for prompt and flow agents | Same mental model, mapped onto LiveKit handoffs; edges-as-tools like Dograh. |
| Building | Single-prompt agent | ✓ (one agent node) | ✓ | ✓ default | Parity. |
| Building | Per-node provider overrides | ✓ | ✗ | **P1s** cascaded only | Explicit limitation; realtime flows share one model. |
| Building | Variables / extraction | ✓ | pack code | **P1s** `VariableSpec` + structured extraction | Parity. |
| Building | Create from natural language | ✓ | ✗ | P2 | Deferred. |
| Building | One spec driving UI + REST + MCP | ✓ | ✗ | **P1s** schema, P2 MCP server | Adopted. |
| Building | Packs (code-first vertical logic) | ✗ | ✓ | ✓ + `default_panel` | Beat. |
| Panels | Live side panel / canvas | ✗ (live context vars only) | 2 custom panels | **P1** 12 block types, composer, forms with RPC | Beat; Dograh has no in-call UI surface. |
| Tools | HTTP tools with auth types, test dialog | ✓ | ✓ dry-run | ✓ + F-15/F-14 hardening | Parity+ (private-range deny). |
| Tools | MCP client | ✓ streamable HTTP | ✓ | ✓ | Parity. |
| Tools | MCP server (self-editing) | ✓ | ✗ | P2 | Deferred. |
| Tools | Built-ins | calculator, time, timezone, transfer resolver | 9 | 9 + panel tools + DTMF/transfer (P1s) | Beat. |
| Knowledge | KB (full-doc / chunked, pgvector) | ✓ (open recall bug on small corpora) | LanceDB + fastembed | ✓ + citations block + upload cap | Parity, no ivfflat issue. |
| Telephony | Carriers | 8 REST integrations + Asterisk | ✗ | **P1s** LiveKit SIP trunks (any SIP carrier) | One integration instead of eight; carriers = Twilio/Telnyx/… as SIP. |
| Telephony | Inbound number → agent | ✓ | ✗ | **P1s** dispatch rules | Parity. |
| Telephony | Outbound single call | ✓ | ✗ | **P1s** | Parity. |
| Telephony | Campaigns (CSV, pacing, retries, schedules, circuit breaker) | ✓ (concurrency-leak bug open) | ✗ | P2 | Deferred. |
| Telephony | DTMF | ◐ (ARI logs only; tool "future") | ✗ | **P1s** send + receive | **Beat** (LiveKit native). |
| Telephony | Voicemail / AMD | ✓ (answer supervisor; reliability issues) | ✗ | P2 (carrier + transcript fallback) | Deferred. |
| Telephony | Transfer | blind only; warm agent-to-agent | ✗ | **P1s** cold (REFER); P2 warm via conference | Parity in P2. |
| Web/widget | Browser test call | ✓ SmallWebRTC + coturn | ✓ LiveKit | ✓ | Parity, no TURN to run. |
| Web/widget | Embeddable widget (voice/chat; floating/inline/headless) | ✓ | ✗ | **P1s** floating/inline; P2 headless | Origins allowlist + rate limits before launch. |
| Testing | Text chat test with edit/replay | ✓ | ✗ | **P1s** rewind | Parity. |
| Testing | AI-vs-AI simulator | placeholder / outsourced | ✗ | P2 (second persona worker + judge) | Beat when shipped. |
| Testing | Post-call QA / LLM judge | ✓ `qa` node (truncation bug on Gemini) | ✗ | **P1** QA job with JSON repair | Beat on robustness. |
| Testing | Regression suites | ✗ | ✗ | P2 | — |
| Observability | Runs/sessions with transcript, tool calls, node transitions | ✓ timeline | ✓ | ✓ + handoff/block/dtmf rows | Parity+. |
| Observability | Recordings | ✓ MinIO/S3 | ✗ | **P1** Egress → S3 | Parity. |
| Observability | Cost | credits, flat | usage only | **P1** price table × real usage per provider | Beat. |
| Observability | Latency | ✗ explicit | ✗ | **P1** EOU→audio, TTFT, TTFB p50/p95 | Beat. |
| Observability | Tracing | own viewer + Langfuse per org + OTel (new) + Prometheus | structlog | **P1** OTel (LiveKit GenAI conventions) + Prometheus; P2 Langfuse presets | Parity+ with PII redaction available in the SDK. |
| Observability | Webhooks | ✓ (unsigned outbound, plaintext secret issue) | ✗ | **P1** signed, durable, redeliverable | **Beat**. |
| Platform | Multi-tenancy | org-scoped by convention | ✗ | **P1** workspaces + query guard | Parity+ (test-enforced scoping). |
| Platform | RBAC | ✗ (superuser + impersonation only) | ✗ | **P1** owner/admin/builder/viewer | **Beat**. |
| Platform | Auth | local JWT or Stack Auth | static tokens | **P1** email/password + invites; P2 OIDC | Parity in P2 for SSO. |
| Platform | API keys | ✓ hashed | ✗ | **P1** hashed, scoped | Parity+. |
| Platform | Public API + SDKs | ✓ generated Python/TS SDKs | ✗ | **P1** public OpenAPI subset; SDKs P2 | Deferred SDK codegen. |
| Platform | Jobs | ARQ | BackgroundTasks | **P1** inline or arq | Parity, optional Redis. |
| Platform | Storage abstraction | local/s3/minio/null | local | **P1** local/s3 | Parity. |
| Platform | Deployment | compose (multi-process container), alpha Helm | compose partial, `lk agent deploy` | **P1** compose dev/prod, supervisor, images, CI; K8s P2 | Parity for compose; both defer K8s. |
| Platform | Voice prompting guide | ✓ | ✗ | P3 | Nice-to-have. |
| Platform | Native RNNoise, gender detection | ✓ | ✗ | Krisp/ai-coustics NC (P1); gender ✗ | Different approach. |
| Platform | i18n | English-centric | English | English; language capability flags | Parity. |

## Deliberate differences

1. **LiveKit-native everything**: SIP, Egress, avatars, Inference, handoffs and OTel come from the SDK/Cloud; we spend effort on product surface, not on re-solving transport or telephony per carrier.
2. **Connections as a first-class entity**: Dograh has no equivalent; a workspace can run agents across several LiveKit projects/servers with isolated secrets and pools.
3. **Panels and avatars**: a video-first surface Dograh does not have.
4. **Security posture as a claim**: encrypted credentials, RBAC, signed webhooks, scoped API keys, rate-limited public connect — each verifiable in code and tests.
5. **We defer campaigns and AMD** to Phase 2 rather than ship a fragile dialer; Dograh's own issues (#745, #737, #777) show where that path leads.
6. **We do not fork the runtime**: the SDK is pinned (`livekit-agents==1.8.2`) with tripwire tests; Dograh maintains a diverged Pipecat fork.
