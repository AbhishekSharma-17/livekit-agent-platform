# LiveKit Inference: independence, billing, catalog, and what it means for LKAP

Research date: 2026-09-28. SDK: livekit-agents 1.8.3 / livekit-api 1.2.1 (installed in `agent/.venv`). Docs pages were fetched as `.md` and all show "rendered at 2026-09-27". Pricing pages were fetched 2026-09-28; the inference price payload is stamped `as_of 2026-09-21`. The repo was only read, and no paid calls were made, so nothing here was tested against the live gateway. Every auth claim below comes from reading SDK source plus the docs.

Abbreviations: `SDK/` = `agent/.venv/lib/python3.12/site-packages/livekit/agents/inference/`, `REPO/` = `livekit_agent_platform/`.

---

## 1. Can Inference be used on its own?

### How the SDK authenticates (from source)

| Item | Source | What it does |
|---|---|---|
| Base URL | `SDK/_utils.py:18` | `DEFAULT_INFERENCE_URL = "https://agent-gateway.livekit.cloud/v1"`. `get_default_inference_url()` (`_utils.py:58-74`) checks `LIVEKIT_INFERENCE_URL` first. If `LIVEKIT_URL` contains `.staging.livekit.cloud` it uses the staging gateway. Otherwise it uses the production gateway. It **never uses your `LIVEKIT_URL` host**, so a self-hosted server URL has no effect on where Inference traffic goes. |
| Key/secret | `SDK/_utils.py:77-98` (`resolve_credentials`), also inline in `stt.py:652+`, `tts.py:425+`, `interruption.py:309-326`, `eot/detector.py:69-81` | The order is: constructor `api_key`/`api_secret`, then **`LIVEKIT_INFERENCE_API_KEY` / `LIVEKIT_INFERENCE_API_SECRET`**, then `LIVEKIT_API_KEY` / `LIVEKIT_API_SECRET`. |
| Token | `SDK/_utils.py:160-168` (`create_access_token`) | The client mints the JWT itself from key and secret: `AccessToken(key, secret).with_identity("agent").with_inference_grants(InferenceGrants(perform=True))`, TTL 600 s. The token is signed with the project secret by `livekit-api`'s `AccessToken`, and the API key is the issuer. There is **no separate "inference key" product**. The token is signed with a LiveKit **Cloud project** API key pair. |
| Transport | `stt.py:1090-1101` (WS `…/stt?model=`), `tts.py:513-525` (WS `…/tts?model=`), `llm.py:252-256,353` (OpenAI-compatible HTTP client, `api_key=<JWT>`, refreshed per request) | Every request sends `Authorization: Bearer <JWT>`. |
| Room/job context | `SDK/_utils.py:101-145` (`get_inference_headers`) | Room ID, job ID, agent ID and session-ID headers are added only when a job context exists. With no job context, the `RuntimeError` is swallowed (`:143`). `X-LiveKit-Worker-Token` is added only when `LIVEKIT_WORKER_TOKEN` is set (Cloud-hosted agents, `:128`). **No room is required**, so `inference.LLM(...)` works from a plain Python script. LKAP already relies on this: the api mints the same JWT and calls the gateway with no room (`REPO/api/src/lkap_api/custom_models/service.py:174-186, 227-253`; base URL at `custom_models/probes/openai_like.py:62`). |
| Quota telemetry | `SDK/_utils.py:33-55` | The gateway returns `X-LiveKit-Inference-{RPM,TPM,Credits}-{Limit,Used}`. These are the per-project limits described in section 2. |

### What the docs say

- "LiveKit Inference is included in LiveKit Cloud" (docs.livekit.io/agents/models/inference, rendered 2026-09-27).
- The self-hosting comparison table has a "Built-in inference" row. For self-hosted it says "N/A, bring your own model provider API keys." For LiveKit Cloud it says "Included" (docs.livekit.io/transport/self-hosting, rendered 2026-09-27).
- The turn detector's default-selection table has a row for "Local development (`dev` mode) with LiveKit Cloud credentials → v1". This confirms that a worker running **outside** LiveKit Cloud hosting can use Inference as long as it holds Cloud project credentials (docs.livekit.io/agents/logic/turns/turn-detector).
- Launch blog (livekit.com/blog/introducing-livekit-inference, published 2025-10-01): "With just your LiveKit API key, you can use top-performing … models."
- A LiveKit-authored knowledge-base file in the agents repo says: "LiveKit Inference is a LiveKit Cloud feature only. If you self-host LiveKit, you must use model plugins instead" (github.com/livekit/agents `examples/homepage/knowledge_base/products/livekit-inference.md`, fetched 2026-09-28).
- **`LIVEKIT_INFERENCE_API_KEY/SECRET` and `LIVEKIT_INFERENCE_URL` are not documented** in the docs pages I checked or in any GitHub issue/PR I could find. They exist only in SDK source.

### Answer

| Scenario | Works? | Credentials |
|---|---|---|
| Agent hosted on LiveKit Cloud | Yes, officially | Uses the project's own `LIVEKIT_API_KEY/SECRET`, which Cloud injects, plus `LIVEKIT_WORKER_TOKEN`. |
| Agent on your own infrastructure, connected to a **LiveKit Cloud** project | Yes. The docs tie Inference to the project, not to where the agent runs (self-hosting comparison row; the turn-detector "dev mode with LiveKit Cloud credentials" row). No page states it outright. | That Cloud project's key/secret. |
| Plain script with no room (batch LLM, tests, QA judge) | Yes per the SDK (no job context needed). Not specifically documented, but the SDK clearly supports it. | Cloud project key/secret. |
| Agent connected to a **self-hosted** LiveKit server using that server's own keys | **No.** The gateway does not know those keys, so it returns 401. The earlier repo note `docs/research-v2/livekit-multi-deployment.md:115` reached the same conclusion. | — |
| Agent on a self-hosted server, with `LIVEKIT_INFERENCE_API_KEY/SECRET` set to a separate **Cloud project's** keys | **Probably works technically, but it is undocumented and officially "N/A".** From the SDK's point of view the room server and the Inference credentials are fully separate. Unknowns: whether the gateway checks the room/job headers against the signing project (they would carry a self-hosted room SID), and whether LiveKit's ToS allows it. A free Build project is enough to test this. | A LiveKit Cloud project key/secret. Usage is billed to, and rate-limited on, that project. |

---

## 2. Billing

Sources: livekit.com/pricing (fetched 2026-09-28); livekit.com/pricing/inference (fetched 2026-09-28, prices `as_of 2026-09-21`); docs.livekit.io/deploy/admin/billing, quotas-and-limits, agents/models/inference (all rendered 2026-09-27).

### Model and plans

- **Pay-as-you-go on top of monthly credits that come with each plan.** The docs say billing "is based on usage. Discounted rates are available on the Scale plan. Custom rates are available on the Enterprise plan." It appears on the one LiveKit Cloud invoice at month end.
- **Metering units** (billing doc): STT by seconds of connection time (1 s increments), LLM by input and output tokens (1 token), TTS by characters (1 character).

| Plan | Price | Inference credits/month | Inference concurrency (per model type: STT connections, TTS connections) | LLM RPM / TPM | After credits run out |
|---|---|---|---|---|---|
| Build | $0, "No credit card required" | $2.50 (~50 min) | 5 | 100 / 600,000 | **Hard cap.** "On the free Build plan, the included allowance is a hard cap and new requests fail after it's exceeded." |
| Ship | from $50/mo | $5 (~100 min) | 20 (the payload's `auto_approve_ceiling` field shows 40–150 depending on model; what it means is not documented) | 400 / 2,400,000 | Billed at list model prices. |
| Scale | from $500/mo | $50 (~1,000 min) | 50 ("request more via dashboard") | 1,000 / 6,000,000 | Billed at **discounted** STT/TTS prices. LLM prices were identical across plans in every LLM record I checked, which matches the repo's full-payload pass in `docs/v4/_sources/costs-livekit.md` (2026-09-25). Can request higher limits. |
| Enterprise | Custom | Custom | Custom | Custom | Volume pricing, including inference. |

- Unused credits **do not roll over** (quotas doc).
- Concurrency counts in aggregate per model type. For example, 10 sessions on Inference STT means 10 STT connections (pricing FAQ).
- The RPM/TPM figures for Ship and Scale come from the pricing payload's `quotas_current` field. The quotas doc gives only the Build numbers.
- **Not documented:** whether a card is needed to upgrade (paid plans obviously bill), any spend cap on Ship/Scale, and any documented billing API for Inference usage. The Analytics API does not expose tokens or credits.

### List prices for the models in LKAP's catalog (`as_of 2026-09-21`)

| Kind | Model | Build/Ship | Scale |
|---|---|---|---|
| STT | `deepgram/nova-3` | $0.0048/min | $0.0042/min |
| STT | `deepgram/flux-general-en` | $0.0065/min | $0.0057/min |
| STT | `assemblyai/universal-streaming` | $0.0025/min | $0.0025/min |
| STT | `cartesia/ink-whisper` | $0.0030/min | $0.0023/min |
| STT | `google/gemini-3.5-transcribe-live` | $0.0095/min | $0.0095/min |
| LLM (per 1M tokens: input / cached / output, same on all plans) | `google/gemma-4-31b-it` (LKAP default, served by LiveKit) | $0.40 / $0.20 / $1.20 | same |
| LLM | `google/gemini-3.5-flash` | $1.50 / $0.15 / $9.00 | same |
| LLM | `openai/gpt-4.1` (Azure or OpenAI) | $2.00 / $0.50 / $8.00 | same |
| LLM | `openai/gpt-4o-mini` | $0.15 / $0.075 / $0.60 | same |
| LLM | `openai/gpt-oss-120b` | Baseten $0.10 / – / $0.50; Groq $0.15 / $0.075 / $0.60 | same |
| TTS (per 1M characters) | `inworld/inworld-tts-2` (LKAP default) | $25 | $15 |
| TTS | `cartesia/sonic-3` | $50 | $37.50 |
| TTS | `deepgram/aura-2` | $30 | $27 |
| TTS | `rime/mistv3` | $30 regularly (**$0 during a promotion, "Free for the month of September 2026", ends 2026-10-01**) | $20 regularly |

The pricing page's calculator shows about $0.0479/min in total for a typical Build/Ship agent: session $0.01, telephony $0.01, LLM $0.0014, STT $0.0075, TTS $0.0090, observability $0.01. The same numbers match the repo's earlier research at `docs/v4/_sources/costs-livekit.md` (2026-09-25).

---

## 3. What models it offers today

Source: docs.livekit.io/agents/models/inference (rendered 2026-09-27). Every model streams: the SDK sets `STTCapabilities(streaming=True, …)` (`SDK/stt.py:640-648`) and `TTSCapabilities(streaming=True)` (`SDK/tts.py:416-420`). STT and TTS run over persistent WebSockets; LLM is a stateless, OpenAI-compatible streaming HTTP API.

- **STT:** Deepgram (Flux en/multi, Nova-3 in about 80 locales plus `multi`, Nova-3 Medical/Pharma, Nova-2 family); AssemblyAI (Universal-3.5 Pro, Universal-3.6 Pro Streaming, Universal-Streaming en, Universal-Streaming-Multilingual); Cartesia (Ink 2 en, Ink Whisper about 100 languages); Google (Gemini 3.5 Transcribe Live, 26 languages); Speechmatics (Linden-1; Enhanced and Standard are deprecated and retire 2026-10-05); xAI (`stt-1`, `stt-2`). The docs say every Inference STT model supports aligned transcripts.
- **LLM:** Gemma 4 31B (the recommended default, "latency-optimized … served on LiveKit's infrastructure"); OpenAI (GPT-4o/4o-mini, 4.1 family, 5 through 5.5, 5.6 Luna/Sol/Terra, `chat-latest`, gpt-oss-120b); Gemini (3/3.1/3.5 through 3.8 Flash, 3.1 Pro; the 2.5 family is deprecated and retires 2026-10-10/20); xAI Grok 4.20 through 4.7; DeepSeek V4.1 Flash; Kimi K2.6 (deprecated, retired 2026-09-26).
- **TTS:** Cartesia Sonic 3/3.5/3.6 (about 40 languages); Deepgram Aura-2 and Flux TTS; Fish Audio S2/S2.1 Pro; Gradium; Inworld TTS 1.5 Max/Mini, TTS 2.0 and 2.0 Flash (about 90 languages); Rime Coda and Mist v3; xAI `tts-1`. ElevenLabs models still appear in the pricing payload but **not** in the docs model table or the 1.8.3 `TTSModels` literal, so their status is unclear.
- **Realtime (speech-to-speech):** the SDK has `inference.realtime` for `openai/gpt-realtime*` and `xai/grok-voice*` (`SDK/_realtime_models.py`). **These are not in the docs Inference table or on the pricing page**, so treat them as undocumented.
- **Regions and latency:** requests go to any region by default. An opt-in **Inference region restriction** keeps traffic in the project's data region; it is on by default for new EU-hosted projects. Models marked "EU endpoint" support this. Latency: when agents are hosted on LiveKit Cloud they "run in the same data center as our inference service … across our private network backbone" (blog). A self-hosted worker reaches the gateway over the public internet. No per-region latency figures are published (**not documented**).
- **Zero data retention** applies by default on every plan (docs).

The 1.8.3 `Literal` lists are only type hints. The constructors accept any `str`, so the gateway's catalog decides what actually works. Every model in LKAP's registry (`REPO/contracts/src/lkap_contracts/providers.py:737-820`) is currently listed and not deprecated.

---

## 4. LKAP today

### How Inference is wired

1. **Registry.** `livekit-inference-stt/llm/tts` (`contracts/src/lkap_contracts/providers.py:737-820`) are `requires_credential=False` and `capabilities.cloud_only=True`. The same applies to `inference-vad` (`:1994-2003`) and `inference-turn-detector` (`:2020-2036`).
2. **Capability gate.** `api/src/lkap_api/connections/probe.py:66-67` sets `cloud = deployment_type == "cloud"` and `inference = cloud and use_inference`, and `:76` sets `turn_detector_mode = "hosted" if inference else "local"`. **A self-hosted connection is hard-wired to `inference_available=False`**, whatever the `use_inference` column says (`db/models.py:298`, default True).
3. **Validation.** `config_service.py:381-385` (`requires_inference`), `:2190-2208` (an error on any Inference slot: "self-hosted connections cannot use LiveKit Inference — use your own STT/LLM/TTS keys"), and `:2221` (a warning when the realtime/half-cascade workflow LLM would fall back to Inference). The UI mirrors this in `web/src/components/console/registry/provider-meta.ts:135` and `…/providers-section/connection-gate.ts:50`.
4. **Credentials into the worker.** The supervisor calls `GET /internal/v1/connections/{id}/worker-env` (`api/src/lkap_api/routers/internal.py:669-706`), which calls `worker_env()` (`api/src/lkap_api/connections/bundle.py:68-88`). That emits **only** `LIVEKIT_URL`, `LIVEKIT_API_KEY` and `LIVEKIT_API_SECRET` (`:81-85`) plus `LKAP_*`/`OTEL_*`, and **no `LIVEKIT_INFERENCE_*`**. The supervisor then merges it into the child env (`supervisor/src/lkap_supervisor/backends/base.py:70-81`; subprocess `:245`, docker `:203`).
5. **Worker.** `agent/src/lkap_agent/providers/factory.py:12-14, 117-124, 403-408` **strips** `api_key`/`api_secret` kwargs from any `livekit-inference-*` provider, so Inference always authenticates from process env. Two Inference objects are built outside the factory and also read env: `main.py:1638-1651` (`_workflow_model`, which falls back to `inference.LLM(WORKFLOW_FALLBACK_LLM_MODEL)`) and `main.py:436-448` (`default_turn_detector`, `v1-mini` when `turn_detector_mode=="local"`; see also `session_builder.py:290-300`).
6. **Api-side calls.** The model "Test" button mints a gateway JWT from the connection's LiveKit key (`api/src/lkap_api/custom_models/service.py:227-253`) without checking `deployment_type`. On a self-hosted connection that would get a 401.

**Conclusion:** a self-hosted connection **cannot use Inference today**. The validator rejects it, the capability is hard-wired off, and even if both were bypassed, the worker would sign with the self-hosted server's keys and the gateway would reject them. On a **Cloud** connection it works, and usage is billed to that connection's project.

### What would be needed (optional per-connection "LiveKit Cloud Inference credentials")

The design is simple because LKAP runs **one worker pool per connection**, so per-process env is per-connection. That keeps the factory invariant ("credentials are kwargs, never env", `factory.py:6-8`) intact.

1. **Data.** Add encrypted, nullable `inference_api_key` and `inference_api_secret` columns next to the connection's existing LiveKit secrets (`api/src/lkap_api/db/models.py` around `:288-313`, plus a migration). Expose them in `ConnectionCredentials` (`api/src/lkap_api/connections/clients.py:87`) and in the create/update models (`contracts/src/lkap_contracts/api_models.py:2116-2165`) as write-only secrets with a fingerprint. Optionally add `inference_url` for staging.
2. **Worker env.** In `bundle.py:81-85`, add `LIVEKIT_INFERENCE_API_KEY` and `LIVEKIT_INFERENCE_API_SECRET` when set, and placeholders in `redacted_env` (`:91-108`) and the external template. The SDK already reads these first (`SDK/_utils.py:80-94`). No worker code change is needed, and this also covers `main.py:1651`, the turn detector and adaptive interruption, which a kwarg-based approach would miss.
3. **Capability.** In `probe.py:66-67`, change to `inference = use_inference and (cloud or has_inference_creds)`. Keep `turn_detector_mode` as it is, or leave self-hosted on `local`, because the hosted `v1` turn detector is only free for Cloud-hosted agents. Change the `static_capabilities` and `effective_capabilities` signatures and their callers (`connections/service.py:189`, `config_service.py:232`, and the `ConnectionInfo` built with `capabilities_of(connection)` at `routers/internal.py:410-414`).
4. **Probe.** On "Test connection", mint an Inference JWT from the separate credentials and make a zero-cost authenticated call. There is **no documented free endpoint**, so one option is a 1-token Gemma chat, which costs a fraction of a cent. The existing `mint_inference_token` (`custom_models/service.py:174`) can be reused.
5. **Api signing.** `custom_models/service.py:246-248` should prefer the inference credentials when present, and refuse on self-hosted without them.
6. **Messages and UI.** Update `config_service.py:2190-2194` ("self-hosted connections cannot use LiveKit Inference" becomes "…unless LiveKit Cloud Inference credentials are set"), `provider-meta.ts:135` (gate on `inference_available`, not `deployment_type`), and the connection form (`docs/v2/UI_UX_SPEC-V2-AMENDMENTS.md:31`, where "Use LiveKit Inference (switch, Cloud only)" becomes a switch plus key/secret fields for self-hosted).
7. **Caveats to surface:** LiveKit calls this officially "N/A" for self-hosted. Traffic goes over the public internet. Usage is billed to, and rate-limited by, the separate Cloud project (Build caps at $2.50/month, then fails). A short live test on a free Build project should come first to confirm the gateway accepts JWTs whose room/job headers point at a foreign server.

Quick workaround with no code, not recommended: `LKAP_SUPERVISOR_PASSTHROUGH_ENV=LIVEKIT_INFERENCE_API_KEY,LIVEKIT_INFERENCE_API_SECRET` (`supervisor/src/lkap_supervisor/settings.py:60`, subprocess backend only) would inject the env. However, validation (`config_service.py:2200`) still blocks Inference slots on self-hosted, and the credentials would be shared across all pools.

---

## 5. Recommendation

| Use LiveKit Inference when | Use direct provider keys (plugins) when |
|---|---|
| The connection is LiveKit Cloud and you want one bill, no vendor accounts, ZDR by default, the co-located low-latency Gemma default, and built-in fallback. | The server is self-hosted (the official path), or you need a vendor or model feature Inference doesn't carry (ElevenLabs, OpenRouter models, custom endpoints, enterprise vendor contracts or discounts, vendor-side data residency beyond EU/global). |
| Demos, the Build plan, and low to medium volume: the price is the provider's pay-as-you-go rate, and LLM prices were the same on every plan in the records checked. | High volume where your own negotiated vendor rates beat list pay-as-you-go, or where Inference concurrency (5/20/50 per model type) or RPM/TPM would throttle you. |

**Self-hosted LiveKit options:**

1. **Bring your own keys** (the supported path; LKAP already enforces it). Use the plugins with per-provider credentials, `v1-mini` turn detection and Silero VAD.
2. **Hybrid:** create a (free or paid) LiveKit Cloud project only for its Inference credentials and implement the per-connection "Cloud Inference credentials" setting from section 4. This is technically plausible per the SDK but undocumented and not officially supported, so validate it with a live Build-plan test and flag the ToS question to LiveKit first.
3. **Move media to LiveKit Cloud** and keep workers self-hosted (external/supervised pool on a Cloud connection). This is fully supported: Inference works with the project's own key, and billing covers only Inference plus WebRTC minutes, not Cloud agent hosting.

## Sources (fetched 2026-09-28 unless noted)
- https://docs.livekit.io/agents/models/inference (rendered 2026-09-27)
- https://docs.livekit.io/agents/models (rendered 2026-09-27)
- https://docs.livekit.io/deploy/admin/billing (rendered 2026-09-27)
- https://docs.livekit.io/deploy/admin/quotas-and-limits (rendered 2026-09-27)
- https://docs.livekit.io/transport/self-hosting (rendered 2026-09-27)
- https://docs.livekit.io/deploy/custom/deployments (rendered 2026-09-27)
- https://docs.livekit.io/agents/logic/turns/turn-detector and …/adaptive-interruption-handling (rendered 2026-09-27)
- https://livekit.com/pricing
- https://livekit.com/pricing/inference (RSC payload `as_of 2026-09-21`)
- https://livekit.com/blog/introducing-livekit-inference (published 2025-10-01)
- https://github.com/livekit/agents/blob/main/examples/homepage/knowledge_base/products/livekit-inference.md
- https://github.com/livekit/agents/issues/6033 (self-hosted users asking for local replacements for Inference-hosted models)
