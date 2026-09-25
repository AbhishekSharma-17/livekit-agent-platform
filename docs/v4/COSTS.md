# LKAP v4 — Pricing sources, the per-minute estimate, and actual cost

Status: **decided** (Fable 5.1, 2026-09-25). Findings first (§1, from the four research reports in `_sources/costs-{llm,livekit,speech,avatars}.md`, every row read from a vendor page that day), then the design (§2 decisions D-V4-39 … D-V4-48, continuing `BACKGROUND-TOOLS.md`'s numbering), the estimator (§3), actual cost (§4), UX (§5), MCP (§6), tests (§7) and the live check (§8). Package cards V4-15 … V4-17 and rulings R-V4-44 … R-V4-51 are in `PLAN-V4.md`. Migration **`v4_003_session_estimates`** (PLAN-V5 §0.3 chains every V5 migration after it).

The user's words, paraphrased: *"For the providers we configure (LLM, TTS, STT, realtime, avatar, image and other services), can we get their official pricing from the API? When we configure an agent, can we see, even before using it, how much it will cost per minute based on that pricing? Show the estimate for a chosen STT/LLM/TTS configuration, then compare estimate versus actual after usage, and track actual cost properly in observability and analytics."*

Short answer. **One vendor publishes its price list through an API: OpenRouter** (`GET /api/v1/models`, no key needed, per-token/per-request/per-audio-token prices for every routed model, STT and TTS included; and the exact charge per request from `GET /api/v1/generation?id=` or in-band `usage.cost`). LiveKit Inference publishes a price *page* (a machine-readable copy exists only as an undocumented Next.js payload; livekit/agents#7420 asks for a real API). OpenAI, Anthropic, Google, Groq, Deepgram, ElevenLabs, Cartesia, AssemblyAI and every avatar vendor publish **pages**; some expose *usage* after the fact — mostly behind admin keys LKAP does not hold, and never prices. So the design has three price sources behind one interface — a **workspace-entered price** (for plan-based vendors: "I pay $0.10/min for my avatar"), a **live quote** where a vendor offers one (OpenRouter), and the **maintained table** `lkap_contracts.pricing.PRICES` (every row with `source_url` + `as_of`) — plus a weekly drift check for table rows that have a live twin. On top: a **usage model per minute** (talk ratios, turns, tokens per turn, session length; defaults with sources, overridden by the workspace's own session averages, editable per request) that turns quotes into a **$/min estimate with a range and a breakdown**, snapshotted on every session at creation so **estimate vs actual** is a fair comparison at the pinned config version; **actual cost** made complete (realtime audio tokens, cached tokens, token-billed TTS/STT, LiveKit agent/SIP/egress minutes, turn-detector requests) and, where the vendor allows, **reconciled** with the vendor's own charge (OpenRouter per generation; Deepgram per request designed for). Everything is labelled "estimate" with its source and date; an unknown price is still never a zero.

## 1. Findings (accessed 2026-09-25)

### 1.1 What exists in the repo today (read from HEAD)

- **The price table.** `contracts/src/lkap_contracts/pricing.py`: `Price{provider_id, model|None, unit, usd_per_unit, source_url, as_of}`, `Unit = tokens_in | tokens_out | audio_s_in | audio_s_out | chars | minutes | images`, `PRICE_VERSION = "2026-09-23"`, `lookup(provider_id, model, unit)` (model-specific row wins), 30 rows: OpenAI `gpt-4.1`/`gpt-4o`/`gpt-4o-mini` tokens, `tts-1` chars, Gemini `gemini-2.5-flash` tokens, Deepgram `nova-3` audio seconds and Aura chars, and the LiveKit Inference "Build"-tier rows (V2-20F). The module's rule: a price is only added when read off the vendor's page that day; an unknown price is `None`, never `0`. `gpt-4o-mini-tts` is unpriced because it is billed per audio **token** and `Unit` has no slot; `openai/gpt-oss-120b` and `inworld/inworld-tts-2` because the LiveKit page was ambiguous (§1.2 settles both).
- **`ProviderSpec.price_ref: str | None`** exists on the registry (8 entries set it, e.g. `deepgram-stt` → `"deepgram-stt"`, `openai-llm` → `"openai-llm"`) and **nothing reads it** (`grep -rn price_ref api/src web/src mcp/src` → only the generated `.d.ts`). A dangling alias.
- **Actual cost.** `api/src/lkap_api/costs/service.py::compute_usage_lines` prices `sessions.usage` (`dataclasses.asdict(AgentSessionUsage)`, `{"model_usage": [...]}`) by **slot** (`costs/mapping.py`: `llm_usage` → `pipeline.llm` else `pipeline.realtime`, `tts_usage` → `tts`, `stt_usage` → `stt`; the entry's own `provider` string is a display name and is not trusted), using only `input_tokens`/`output_tokens`, `characters_count` else `audio_duration` (TTS), `audio_duration` (STT). Avatar minutes are wall-clock `ended_at − started_at`; egress minutes come from `recording_duration_s` at finalisation (`recordings/finalize.py`) under the pseudo id `livekit-egress` (no row today). `interruption_usage`/`eot_usage` are skipped. Only priced lines are persisted (`session_costs`: `provider_id, model, unit String(16), quantity, unit_price_usd, cost_usd, price_version`); unpriced ones are recomputed on read by `render_cost` (`note="no price"`). `sessions.cost_usd` is the sum of persisted lines or `None`. `cost_session` is idempotent, called once from the worker's summary (`routers/internal.py`) before the `session.ended` webhook. `SessionMetricsIn.usage_lines` exists and always arrives empty (`internal.py:515`).
- **What the worker reports** (`agent/src/lkap_agent/observability.py`, livekit-agents 1.8.2): it subscribes to `session_usage_updated` and posts the latest `AgentSessionUsage` with the summary (and a `metrics {kind: "session_usage"}` event per update). It does **not** subscribe to `metrics_collected` (the SDK logs a deprecation warning per session), so it never sees `LLMMetrics.request_id` / `STTMetrics.request_id` / `TTSMetrics.request_id`. The SDK's usage records (`livekit/agents/metrics/usage.py`) carry more than the api prices: `LLMModelUsage.input_cached_tokens`, `input_cache_creation_tokens`, `input_audio_tokens`, `input_text_tokens`, `input_image_tokens`, `output_audio_tokens`, `output_text_tokens`, `output_reasoning_tokens` (inside `output_tokens`), `session_duration`; `TTSModelUsage.input_tokens/output_tokens` beside `characters_count`/`audio_duration`; `STTModelUsage.input_tokens/output_tokens` (1.8.3 adds `input_audio_tokens`, PR #7303); `InterruptionModelUsage.total_requests`, `EOTModelUsage.total_requests`. **For realtime models the collector folds audio and text tokens into `input_tokens`/`output_tokens`** (`ModelUsageCollector.collect`, the `RealtimeModelMetrics` branch), so a plain `tokens_in` price on a realtime entry would bill audio tokens at the text rate (8× too low for OpenAI). `AvatarMetrics` exists (latencies) but is never aggregated: avatar minutes stay wall-clock. The SDK has **no** cost helper (grep-verified across `metrics/`, `voice/report.py`).
- **Per-request ids.** For OpenAI-compatible LLMs (`livekit-plugins-openai`, which OpenRouter uses) `LLMMetrics.request_id` is the completion id (`livekit/agents/inference/llm.py:481` sets `ChatChunk(id=chunk.id)`; `llm/llm.py:392` copies it into the metrics) — for OpenRouter the `gen-…` id its `/generation` endpoint takes. The same stream parser builds `CompletionUsage` from `prompt_tokens`/`completion_tokens`/cached/reasoning only and **drops OpenRouter's in-band `usage.cost`** (`inference/llm.py:474–491`). Deepgram's STT plugin carries the vendor `request_id` from the stream `Metadata`; ElevenLabs/Cartesia generate local ids.
- **Probe cost** (V4-08, `custom_models/service.py::estimate_cost`): `pricing.lookup` × the probe's usage, else OpenRouter's `CatalogItem.meta["pricing"]` (`prompt`/`completion` per token), else `None` — the only reader of a live price today.
- **Analytics.** `GET /v1/analytics/summary` (`routers/analytics.py`) scans the workspace's sessions live into `AnalyticsSummary{sessions, minutes, cost_usd, failed, by_day[], by_agent[]}`; `usage_daily` (`jobs/rollup.py`) holds the same four numbers nightly. The console shows four tiles, a by-day chart, a by-agent table; the session Cost tab renders `SessionCost.lines` with a literal "no price", never "$0".
- **Templates.** `StarterTemplate.pipeline: PipelineConfig | None` (`None` keeps the pack default); the gallery tile shows name, tagline, chips, requirement badges — no cost.
- **Ownership.** V4-12 is merged: `observability.py`, `agent_config.py`, `mcp/tools/{tools,agents}.py` are free again (`agent_config.py` is still left to V4-17, the only package that needs it); V4-13 (console tool files) and V4-14 (`agent/pyproject.toml`, requirement files) are still running; V5 wave 1 runs in worktrees with `v5_001` chained after `v4_003` (PLAN-V5 §0.3 reserves this design's files). Nothing here touches V4-13's or V4-14's files.

### 1.2 Provider pricing and usage availability (sources: `_sources/costs-*.md`)

Legend — **price list**: *live* = an API returns prices; *page* = a web page only, so the price is a **table** row with `source_url` + `as_of`. **Usage after the fact**: what an API returns, at what granularity, with which key.

| Provider (registry ids) | Price list | Usage / cost after the fact | Notes for this design |
|---|---|---|---|
| **OpenRouter** (`openrouter-{llm,stt,tts,embedding,image-gen}`) | **Live.** `GET https://openrouter.ai/api/v1/models` (no key; `?output_modalities=all` to include STT/TTS): `pricing{prompt, completion, request, image, image_output, audio, audio_output, input_cache_read, input_cache_write, internal_reasoning, web_search, …}` as **USD per single unit** (decimal strings); `/models/{author}/{slug}/endpoints` per routed provider. | **Yes, three ways, regular key.** In-band `usage.cost` (+ `cost_details.upstream_inference_cost`, cached/reasoning details) on every response, last SSE chunk when streaming (`usage:{include:true}` now deprecated as always-on); `GET /api/v1/generation?id=` → `total_cost`, native token counts, `cache_discount`, latency (availability delay undocumented); `GET /api/v1/key` → `usage`, `usage_daily/weekly/monthly`, limits. Management key only: `/credits`, `/activity` (30 days, per date × model), `/analytics/query`. | The live source. STT/TTS `prompt` unit is **not documented**: arithmetic says per audio **second** for `deepgram/nova-3` (0.0000716667 × 60 = $0.0043/min, Deepgram's pre-recorded rate) and per **character** for `deepgram/aura-2` ($0.030/1k) — the unit map in D-V4-39 says so and the live check confirms it. |
| **LiveKit Inference** (`livekit-inference-{stt,llm,tts}`, `inference-turn-detector`, `inference-vad`) | **Page** (https://www.livekit.com/pricing/inference). An undocumented Next.js RSC payload (`curl -H "RSC: 1" …/pricing/inference/`) carries 137 records with `pricing_current.as_of` (2026-09-21) and `unit_price{build,ship,scale}`; livekit/agents#7420 (2026-09-23) requests a real API, unanswered. Tiers Build $0 / Ship $50 / Scale $500 per month with $2.50 / $5 / $50 Inference credit included; LLM prices identical across tiers, STT/TTS cheaper on Scale. Metering (https://docs.livekit.io/deploy/admin/billing/): STT **seconds of connection time** (1-s increments), LLM tokens, TTS characters. | **No API** for Inference usage per session or project; the Cloud "Agent Insights" UI shows token counts (30-day retention, no export). | Table rows (entry tier = Build, as today). The RSC payload is used by the **drift job only** as an unofficial twin (D-V4-40), never as the price source. STT metering on connection time settles `stt_billing=stream` (§3). `openai/gpt-oss-120b` now resolvable (Groq route $0.15/$0.60, Baseten $0.10/$0.50 — default route unknown → stays unpriced, ask #93); Inworld TTS-2 is listed at $25/1M chars → priceable. |
| **LiveKit Cloud** (pseudo ids `livekit-agent`, `livekit-sip`, `livekit-egress`, `livekit-participant`) | **Page** (https://www.livekit.com/pricing). Agent session minutes: Build 1,000 incl (no overage listed), Ship/Scale $0.01/min after 5,000 / 50,000; 1-s increments, 10-s minimum. Participant minutes $0.0005/min (Ship) after 150,000. SIP: LiveKit US local inbound $0.01/min, toll-free $0.02/min, third-party trunk in+out $0.004/min (Ship). Egress: composite/participant $0.02/min video, $0.005/min audio-only (Ship); track egress $0.001/min. Agent audio recordings (observability) $0.005/min. Krisp NC included; voice isolation $0.0012/min. | **Analytics API** (`https://cloud-api.livekit.io/api/project/{PROJECT_ID}/sessions[/{id}]`, JWT with `roomList`, **Scale plan or higher**, start ≤ 7 days back) → per session `bandwidth`, `connectionMinutes`, participants, egress status. No agent-minute, SIP-minute, egress-minute or Inference figures via any API (livekit/livekit#2803 unanswered). | Table rows at the Ship/Scale overage rate with `tier_note` ("Build includes 1,000 agent minutes/month; overage rate shown"). Actual quantities come from LKAP's own clocks (session, recording duration, channel), not from LiveKit. The Analytics API is not used (plan-gated, 7-day window). |
| **OpenAI** (`openai-{llm,tts,stt,realtime,image-gen,embedding,responses-llm}`) | **Page** (https://developers.openai.com/api/docs/pricing). No pricing API. gpt-4.1 $2.00/$0.50 cached/$8.00; gpt-4.1-mini $0.40/$0.10/$1.60; gpt-4o-mini $0.15/$0.075/$0.60; **gpt-realtime** text $4/$0.40/$16, **audio $32/$0.40/$64** per 1M; gpt-realtime-mini text $0.60/$0.06/$2.40, audio $10/$0.30/$20; **gpt-4o-mini-tts** text in $0.60, audio out $12 per 1M tokens; gpt-4o-transcribe $2.50 in/$10 out per 1M (page's estimate $0.006/min), gpt-4o-mini-transcribe $1.25/$5 ($0.003/min); whisper-1 no longer on the page. | **Admin key only**: `GET /v1/organization/usage/{completions,audio_speeches,audio_transcriptions,…}` (tokens, `1m|1h|1d` buckets, group by project/key/model; audio/text/cached splits) and `GET /v1/organization/costs` (USD, **daily only**, `line_item`); delay undocumented. Per response: `usage.prompt_tokens_details{cached_tokens, audio_tokens}`, `completion_tokens_details{reasoning_tokens, audio_tokens}`; Realtime `response.done.usage.input_token_details{text,audio,image,cached}` / `output_token_details{text,audio}` ("will correspond to billing"). | Table rows incl. the realtime audio/text splits and the token-billed TTS/STT. No reconciliation (admin key). The SDK already delivers the realtime splits. |
| **Anthropic** (`anthropic-llm`) | **Page** (https://platform.claude.com/docs/en/about-claude/pricing). Sonnet 4.6 $3 / cache write $3.75 / cache read $0.30 / out $15 per MTok; Haiku 4.5 $1 / $0.10 read / $5; Sonnet 5 $2/$10. | **Admin key, OAuth `org:admin`, or an unscoped key**: `GET /v1/organizations/usage_report/messages` (`1m|1h|1d`, cached/creation/output splits) and `GET /v1/organizations/cost_report` (**daily**, cents, "typically appears within 5 minutes"). Per response: `usage{input_tokens, cache_creation_input_tokens, cache_read_input_tokens, output_tokens}`. | Table rows with `cached_tokens_in`. The SDK's `input_cached_tokens`/`input_cache_creation_tokens` map onto them. No reconciliation. |
| **Google Gemini API** (`google-{llm,realtime,stt,tts,image-gen}`) | **Page** (https://ai.google.dev/gemini-api/docs/pricing). gemini-2.5-flash $0.30 in (text) / **$1.00 audio in** / $2.50 out / cache $0.03; gemini-3.5-flash $1.50 / $9.00 / cache $0.15; Live models (`gemini-2.5-flash-native-audio-preview-12-2025`: text in $0.50, audio in $3.00, text out $2.00, audio out $12.00; `gemini-3.8-live`, `gemini-3.1-flash-live-preview`: text $0.75 / audio in $3.00 / text out $4.50 / audio out $12.00); TTS `gemini-3.8-flash-tts` $0.50 in / $9.00 audio out. **Audio = 25 tokens per second** (documented). Free tier "free of charge" with data used for product improvement. | **None from the Gemini API.** Per response `usageMetadata{promptTokenCount, cachedContentTokenCount, candidatesTokenCount, thoughtsTokenCount, promptTokensDetails[{modality, tokenCount}], …}`; Live API messages carry the same. After the fact: AI Studio/Cloud Billing pages (up to 24 h), BigQuery export. | Table rows incl. Live audio splits; `audio_tokens_*_per_s = 25` is a sourced assumption. No reconciliation. |
| **Groq** (`groq-{llm,stt,tts}`) | **Page** (https://console.groq.com/docs/models; `groq.com/pricing` now redirects). gpt-oss-120b $0.15/$0.60; whisper-large-v3-turbo $0.04/hour; Orpheus TTS $22/1M chars; Llama models "contact sales". `GET /openai/v1/models` has no pricing. | **None documented.** Per response `usage` tokens and timings only. | Table rows. No reconciliation. |
| **Deepgram** (`deepgram-{stt,tts}`) | **Page** (https://deepgram.com/pricing). Nova-3 streaming $0.0048/min (multilingual $0.0058), pre-recorded $0.0043; Flux $0.0065 ($0.0078 multi); Aura-2 $0.030/1k chars; Aura-1 $0.015. | **Yes, per request in USD, regular key**: `GET /v1/projects/{project_id}/requests/{request_id}` → `response.details.usd`, `duration`, `models`; `/usage/breakdown` (hours, tts_characters, no USD); `/balances`. The streaming `Metadata.request_id` is the id the logs guide looks up (implied, not stated). | Table rows (the two `nova-3` rows already there). Reconciliation designed (D-V4-45) — needs the project id, which the credential form does not collect (ask #94). |
| **ElevenLabs** (`elevenlabs-{tts,stt}`) | **Page** (https://elevenlabs.io/pricing/api): per-1k-character rates depend on the selected plan ($0.10 v3/multilingual, $0.05 Flash on the state captured); plans $6–$990/month with included credits; no plan-independent pay-as-you-go rate. Scribe STT $0.22/hour. | Subscription: `GET /v1/user/subscription` (character_count/limit); workspace analytics `POST /v1/workspace/analytics/query/usage-by-product-over-time` (credits and fiat units); per request: `GET /v1/history` items with `request_id` and character deltas. `character-cost` header documented generically, not for streaming. | **Stays unpriced in the table** (plan-dependent); the workspace-entered price (D-V4-39) is the way to price it. |
| **Cartesia** (`cartesia-{tts,stt}`) | **Page** (credits: ~1 credit/char TTS, Ink-Whisper 1 credit/s; plans Free 20k → Scale $299/8M credits; derived ≈ $0.037–$0.05 per 1k chars; overage $/credit not published). | Admin key: `GET /usage/credits` (credits, daily), `GET /usage/agents` (cents, for Cartesia's own agents). `request_id` in every response body. | Stays unpriced (credits, no USD unit rate); workspace-entered price. Through LiveKit Inference it **is** priced (sonic-3 $50/1M chars, ink-whisper $0.0030/min). |
| **AssemblyAI** (`assemblyai-stt`) | **Page** (https://www.assemblyai.com/pricing): Universal-Streaming $0.15/hour **billed on WebSocket session time**; Universal-3.5 Pro Realtime $0.45/hour. | **None until Q4 2026** ("Usage and Spend API" on the roadmap). Streaming `Termination` message carries `audio_duration_seconds`, `session_duration_seconds`. | Table row (per second, from $/hour ÷ 3600) with `tier_note="billed on session time"`. |
| **Rime, Inworld, Hume, Speechmatics** | Pages: Mist v3 $0.03/1k chars; Inworld TTS-2 $25/1M chars on-demand (TTS-2 Flash $15); Hume Octave overage $0.15/1k (plan-tiered), EVI $0.06/min; Speechmatics "$0.129" (unit not rendered), volume discount above 500 h. | Rime/Inworld/Hume: dashboard only. Speechmatics: `GET /v2/usage` per UTC day, current day excluded. | Rime and Inworld: table rows. Hume: plan-tiered → unpriced (workspace price). Speechmatics: unit unconfirmed → unpriced. |
| **Beyond Presence** (`bey-avatar`) | **Page** (https://www.beyondpresence.ai/pricing): plans in **EUR** with included minutes and overage (Starter €49/280 min/€0.175 per min … Enterprise "€0.03/min at scale"); Free 40 min, 3-min session cap. No PAYG rate, no pricing API. | `GET /v1/sessions[/{id}]` (`x-api-key`) → `status.started_at/ended_at` (duration derived; no minutes/credits/cost). The plugin discards the session id; correlation is by `avatar_id` + time. | Unpriced (EUR plans) → workspace-entered price in USD. Actual = LKAP wall-clock. |
| **Simli** (`simli-avatar`) | **No reachable pricing page** (all `/pricing` paths 404 on 2026-09-25); homepage: "$10 on signup", "50 minutes monthly top-up", "pay-as-you-go" without a rate. | **Best of the set**: `GET /history/sessions` (`x-simli-api-key`, `startTime`/`endTime`) → `sessionTotalTime` (s), `startTime`, `endTime`, `apiKeyName`; no cost. | Unpriced → workspace price. Vendor minutes reconcilable by time window (designed, not built). |
| **Tavus** (`tavus-avatar`) | **Page** (https://www.tavus.io/pricing): Basic $0/25 min; Starter $59/100 min/**$0.37 per min** overage; Growth $397/1,250 min/$0.32; recording $0.03/min. Credits count from conversation creation. | `GET /v2/conversations[/{id}?verbose=true]` → `created_at`, `events[]` (`system.shutdown.timestamp`); duration derived. Webhook `system.shutdown` has no duration. The plugin exposes `conversation_id`. | Unpriced (plan overage) → workspace price. |
| **Anam, bitHuman, HeyGen LiveAvatar, D-ID** | Pages/plans only (Anam Starter $12/50 min/$0.16 per min; bitHuman credits ≈ $0.02–0.04/min; LiveAvatar 1–2 credits/min; D-ID page not retrievable). | Anam `GET /v1/sessions` (`sessionLengthMs`); LiveAvatar `GET /v1/sessions?type=historic` (`duration`, **`credits_consumed`**); D-ID `GET /v2/agents/sessions` (`duration_minutes`); bitHuman `/api/billing` (balance). | Unpriced → workspace price. |

**Summary.** Live prices: OpenRouter only. Live per-request cost: OpenRouter (regular key), Deepgram (regular key + project id). Everything else is a maintained table row, and for the plan-based vendors (ElevenLabs, Cartesia direct, Hume, every avatar vendor) the honest per-unit price is the one the workspace admin types in from their own plan.

### 1.3 Rows to add to `pricing.PRICES` (each re-read from `source_url` on the implementation day; `as_of` = that day; `PRICE_VERSION` bumped)

From §1.2, entry tier, USD, with the `Unit` each row needs (new units in bold):

- `openai-llm`: `gpt-4.1-mini` tokens_in/out ($0.40/$1.60 per 1M) and **`cached_tokens_in`** for `gpt-4.1` ($0.50), `gpt-4.1-mini` ($0.10), `gpt-4o-mini` ($0.075).
- `openai-realtime`: `gpt-realtime` **`text_tokens_in`** $4.00, **`text_tokens_out`** $16.00, **`audio_tokens_in`** $32.00, **`audio_tokens_out`** $64.00, **`cached_tokens_in`** $0.40 (per 1M); `gpt-realtime-mini` $0.60/$2.40/$10/$20/$0.06. `tier_note="Standard tier"`.
- `openai-tts`: `gpt-4o-mini-tts` `tokens_in` $0.60, `tokens_out` $12.00 per 1M (token-billed; the SDK reports `TTSModelUsage.input_tokens/output_tokens`).
- `openai-stt`: `gpt-4o-transcribe` `tokens_in` $2.50 (text) — **hold**: the page prices audio-in tokens separately from text-in and the report captured only "$2.50 in"; the implementer reads the audio-in figure off the page (ask #92 records the check) and adds `audio_tokens_in` + `tokens_out` $10.00; `gpt-4o-mini-transcribe` likewise ($1.25 / $5.00).
- `anthropic-llm`: `claude-sonnet-4.6` $3/$15, `cached_tokens_in` $0.30; `claude-haiku-4.5` $1/$5, `cached_tokens_in` $0.10 (model ids as the registry lists them).
- `google-llm`: `gemini-3.5-flash` $1.50/$9.00, `cached_tokens_in` $0.15; `gemini-2.5-flash` `cached_tokens_in` $0.03 (the tokens rows exist).
- `google-realtime`: `gemini-2.5-flash-native-audio-preview-12-2025` `text_tokens_in` $0.50, `audio_tokens_in` $3.00, `text_tokens_out` $2.00, `audio_tokens_out` $12.00; `gemini-3.8-live`, `gemini-3.8-live-extended-thinking`, `gemini-3.1-flash-live-preview` $0.75 / $3.00 / $4.50 / $12.00 (per 1M).
- `google-tts`: `gemini-3.8-flash-tts` `tokens_in` $0.50, `tokens_out` $9.00 per 1M (token-billed TTS; `tier_note` "introductory rate through 2026-12-31").
- `groq-llm`: `openai/gpt-oss-120b` $0.15/$0.60, `openai/gpt-oss-20b` $0.075/$0.30; `groq-stt`: `whisper-large-v3-turbo` `audio_s_in` $0.04/3600, `whisper-large-v3` $0.111/3600; `groq-tts`: `orpheus-v1-english` (id as the registry lists it) `chars` $22/1M.
- `deepgram-stt`: `nova-3` already; add `flux-general-en` $0.0065/60 per s, `nova-2` if the page still lists it; `deepgram-tts`: `aura-2` model-specific $0.030/1k (the model-agnostic row stays), `aura-1` $0.015/1k.
- `assemblyai-stt`: `universal-streaming` `audio_s_in` $0.15/3600, `tier_note="billed on WebSocket session time"`.
- `rime-tts`: `mistv3` `chars` $0.03/1k; `inworld-tts`: `inworld-tts-2` $25/1M, `inworld-tts-2-flash` $15/1M.
- `livekit-inference-tts`: `inworld/inworld-tts-2` $25/1M chars (the page now lists it). `livekit-inference-llm`: `openai/gpt-oss-120b` stays unpriced (route ambiguity, ask #93). `livekit-inference-stt`: `deepgram/nova-3-multi` $0.0058/min if the registry lists it.
- **Infrastructure** (pseudo ids, documented in the module docstring): `livekit-agent` `minutes` $0.01 (`tier_note="Ship/Scale overage rate; Build includes 1,000 agent minutes/month"`), `livekit-participant` `minutes` $0.0005 (`tier_note` likewise; quantity = 2 × session minutes for a two-party call), `livekit-sip` model `local-inbound` $0.01, `toll-free-inbound` $0.02, `trunk` $0.004 (`minutes`), `livekit-egress` model `audio` $0.005, `video` $0.02 (`minutes`), `livekit-recording` `minutes` $0.005 (only if LKAP ever turns on agent audio recordings; not today — no row), `inference-turn-detector` / `inference-vad` `requests` — **not priced by either LiveKit page** (they are Inference-hosted models with no listed rate; the Krisp NC row says "included") → known-zero rows with `tier_note="included with LiveKit Cloud"` only if the page states inclusion on the implementation day; else unpriced.
- **Known zeros**: `fastembed-embedding` `tokens_in` 0, `tier_note="runs on the worker; no vendor charge"`.

## 2. Decisions

### D-V4-39 — One price interface, three sources: `PriceQuote` resolved workspace → live → table → unknown; `price_ref` becomes the alias it was meant to be

**Contract** (`lkap_contracts/pricing.py`, additive):

```python
PriceSource = Literal["workspace", "live", "table"]

class PriceQuote(BaseModel):
    provider_id: str            # the registry entry priced (after price_ref aliasing)
    model: str | None
    unit: Unit
    usd_per_unit: Decimal
    source: PriceSource
    source_url: str | None      # vendor page (table), API endpoint (live), None (workspace)
    as_of: str                  # ISO date: the row's as_of (table), the fetch date (live), the edit date (workspace)
    fetched_at: datetime | None = None   # live: the catalog cache row's fetch time
    tier_note: str | None = None         # "LiveKit Build tier list price", "Standard tier", …
    free_tier_note: str | None = None    # "free within Google's daily limits; priced at the paid rate here"
    stale: bool = False                  # table row older than PRICE_STALE_DAYS (90), or a live row past its TTL
    currency: Literal["USD"] = "USD"

class WorkspacePrice(BaseModel):        # what an admin types in (D-V4-39, "workspace" source)
    provider_id: str; model: str | None = None; unit: Unit
    usd_per_unit: Decimal = Field(ge=0, le=1000); note: str | None = Field(default=None, max_length=200)
    as_of: str
```

`Price` (the table row) keeps its shape and gains optional `tier_note` and `free_tier_note`. `PRICE_STALE_DAYS = 90`. The `PRICE_VERSION` rule is written into the docstring and a test: **any row edit bumps `PRICE_VERSION` to that day; the contracts test fails when a row's `as_of` is newer than `PRICE_VERSION`.**

**Units grow** (`Unit`), all ≤ 16 characters (`session_costs.unit` is `String(16)`): `text_tokens_in`, `text_tokens_out`, `audio_tokens_in`, `audio_tokens_out`, `cached_tokens_in`, `requests`. Not added: a session-seconds unit (xAI-style `session_duration` stays unpriced; no registry entry needs it). A contracts test asserts every literal's length.

**Resolution** — `pricing.quote(provider_id, model, unit, *, workspace_prices=(), catalog_meta=None, catalog_fetched_at=None, now) -> PriceQuote | None`, in `lkap_contracts` so the api, MCP and any later worker use agree. `catalog_meta` is the raw `CatalogItem.meta` mapping (`Mapping[str, Any] | None`), not the `CatalogItem` model: `api_models.py` imports `pricing.Unit`, so `pricing.py` must not import `api_models` back.

1. **Workspace** (`workspaces.settings["cost"]["prices"]`, the telephony-policy precedent — no migration): an admin-entered `WorkspacePrice` for this `(provider_id|alias, model|None, unit)`; model-specific beats model-agnostic. This is how ElevenLabs, Cartesia direct, Hume and every avatar vendor get a real number ("my Beyond Presence plan works out to $0.10/min"). USD only; the console field says so. `source="workspace"`, `as_of` = the edit date.
2. **Live**: when `catalog_meta` is given and its `"pricing"` carries the unit — OpenRouter's keys map `prompt` → `tokens_in` (LLM), `audio_s_in` (an STT entry: the price is per audio second, §1.2) or `chars` (a TTS entry: per character), `completion` → `tokens_out`, `input_cache_read` → `cached_tokens_in`, `audio` → `audio_tokens_in`, `audio_output` → `audio_tokens_out`, `image` → `images`, `request` → `requests`; all **USD per single unit** — return `source="live"`, `source_url="https://openrouter.ai/api/v1/models"`, `as_of` = the cache row's fetch date, `stale` when past the catalog TTL. A `"0"` on a `:free` model is a **known** zero with `free_tier_note` (the "never zero" rule is about unknown prices).
3. **Table**: `lookup(alias, model, unit)` where `alias = spec.price_ref or provider_id`. `price_ref` is thereby defined as **"price this entry with that entry's rows"** (`openai-responses-llm` → `openai-llm`, `azure-openai-realtime` → `openai-realtime`); the self-references (`deepgram-stt` → `"deepgram-stt"`, …) are removed from the registry, and a contracts test asserts every `price_ref` names another registry id. The quote carries the row's `source_url`, `as_of`, notes, `stale` when older than 90 days.
4. **Unknown**: `None`; callers keep today's contract (`cost_usd=None`, `note="no price"`).

`lookup` stays (V4-08's `estimate_cost` keeps working) as the table half of `quote`.

**Tiered and volume pricing: the list price of the entry tier, said out loud.** LiveKit's "Build" rows are the precedent; the rule is generalised: `tier_note` is mandatory on a row whose page shows more than one tier, a volume discount or an included quota, and the estimate footer repeats it ("list prices at the entry tier; included minutes, volume discounts and enterprise contracts not modelled"). Plan-included minutes are **never** converted into a rate by LKAP (Cartesia's ≈ $0.04/1k chars, Tavus's $0.37/min overage are not entry-tier list prices); that conversion is the admin's, through a workspace price.

**Currency: USD only.** Every priced vendor lists USD; Beyond Presence lists EUR and therefore has no table row (the admin converts when entering a workspace price). A display currency is out of scope (ask #95).

**Free tiers: a note, never a zero line.** Gemini's free tier, OpenRouter's free daily requests and Deepgram's signup credit are `free_tier_note`s on the paid-rate row. The only zero-priced lines are OpenRouter `:free` models (live, known) and local models (`fastembed-embedding`, `tier_note="runs on the worker; no vendor charge"`).

### D-V4-40 — Freshness: the weekly drift job gains `price_drift` and `price_stale`; every surface prints the date and the source

- `catalogs/drift.py` (V4-10's job; R-V4-27: never fails the build) gains **`price_drift`**: (a) for every table row whose `provider_id` has an OpenRouter twin (`pricing.OPENROUTER_TWINS`: `openai-llm gpt-4.1` ↔ `openai/gpt-4.1`, `anthropic-llm claude-sonnet-4.6` ↔ `anthropic/claude-sonnet-4.6`, `google-llm gemini-3.5-flash` ↔ `google/gemini-3.5-flash`, `deepgram-stt nova-3` ↔ `deepgram/nova-3`, `deepgram-tts aura-2` ↔ `deepgram/aura-2`, …), compare with OpenRouter's public `/models?output_modalities=all` (the drift job already reads it keyless); (b) for every `livekit-inference-*` row, compare with the RSC payload (`curl -H "RSC: 1" https://www.livekit.com/pricing/inference/`, parsed leniently; any failure → the section says "LiveKit payload unavailable" and nothing else). A difference above **2 %** is a report row ("table $2.000/1M (as_of 2026-09-23) vs live $2.500/1M") — a prompt to re-read the vendor page, never an automatic edit. **`price_stale`** lists rows older than `PRICE_STALE_DAYS`. Vendor-page HTML diffing is out of scope (unstructured pages; weekly false alarms teach people to ignore the issue).
- Every estimate and cost line carries `as_of` and `source`; the console prints "List prices as of <date>" (table), "OpenRouter prices, fetched <time>" (live) or "Your price, set <date>" (workspace) under every figure; a `stale` quote adds "price may be out of date". `price_version` is stored on every persisted cost line (today) and on the estimate snapshot (D-V4-43), which makes "price changed since the estimate" a detectable reason in the variance explanation.

### D-V4-41 — The usage model per minute: assumptions with a source, overridden by the workspace's own averages, editable per request

An estimate is `Σ_lines quantity_per_minute × usd_per_unit`; quantities come from an explicit **usage model**. Every assumption is a named number with `low`/`high` bounds and `source ∈ {default, workspace, request}`:

| Key | Default (source) | Low–high | Feeds |
|---|---|---|---|
| `session_minutes` | 5 (default) or the workspace's median ended-session length | 2–15 | LLM context growth; per-session lines |
| `caller_talk_ratio` | 0.45 (two parties, roughly half each, pauses) | 0.30–0.60 | STT when `stt_billing="segments"`; realtime audio-in tokens |
| `agent_talk_ratio` | 0.45 | 0.30–0.60 | TTS characters/seconds; realtime audio-out tokens |
| `stt_billing` | `stream` — LiveKit Inference meters STT on **seconds of connection time** (billing doc), AssemblyAI on WebSocket session time, Deepgram streaming on audio sent (the plugins stream continuously); `segments` only for batch/VAD-gated STT | — | STT s/min = 60 (stream) or 60 × caller ratio (segments) |
| `speech_wpm` | 150 words/minute | 130–170 | chars/min = wpm × `chars_per_word` × talk ratio |
| `chars_per_word` | 6 (5 letters + a space) | 5.5–6.5 | TTS characters |
| `agent_turns_per_min` | 3 | 2–5 | LLM calls/min; TTS requests |
| `prompt_tokens` | measured from the config: `len(instructions)/4` + Σ tools (≈ 120 per HTTP/MCP tool schema + built-ins) + `knowledge.top_k × 180` when `auto_inject` + panel/block schema (≈ 200) | ×0.8–×1.3 | LLM input per turn (fixed part) |
| `history_tokens_per_turn` | 140 (≈ 60 user + 80 agent words) | 100–200 | LLM input growth per turn (linear per turn; quadratic over a session) |
| `output_tokens_per_turn` | 60 | 40–120 | LLM output (reasoning tokens are inside `output_tokens`, so a thinking model's high bound is ×3) |
| `tool_calls_per_session` | 1 | 0–4 | one extra LLM round-trip each (input = current context, output ≈ 40) |
| `audio_tokens_in_per_s`, `audio_tokens_out_per_s` | **Gemini: 25 tokens/s** (documented on the pricing page). **OpenAI: read off the pricing page's per-minute column at implementation** ($/min ÷ $/1M audio tokens × 1M / 60), ask #92 | vendor ±0 | realtime audio tokens = seconds of talk × rate |
| `kb_queries_per_turn` | 1 when `knowledge.auto_inject` (≈ 30 tokens each) | 0–2 | embedding tokens |
| `recording` | from `config.recording.enabled` / `audio_only` | — | egress minutes = session minutes |
| `avatar` | from `pipeline.avatar` | — | avatar minutes = session minutes |
| `channel` | `web` (request may say `phone` or `text`) | — | SIP minutes when `phone` (model `local-inbound` for LiveKit-hosted numbers, `trunk` otherwise); no audio lines when `text` |
| `participants` | 2 | — | LiveKit participant minutes |
| `qa_judge` | from `config.qa.enabled`: one call per session (input ≈ prompt + `history_tokens_per_turn × turns × 1.2`, output ≈ 200) | — | one per-session LLM line on the judge model |

**Workspace averages** (`costs/assumptions.py::workspace_assumptions(db, workspace_id)`): over the last 30 days' ended sessions with `usage` (newest 500 at most), when at least **10** qualify, derive `session_minutes` (median), `agent_turns_per_min` (`SessionLatency.turns` ÷ minutes), `history_tokens_per_turn` and `output_tokens_per_turn` (`llm_usage` totals ÷ turns, prompt part removed with the agent's measured `prompt_tokens`), `agent_talk_ratio` (back-solved from TTS chars/min), `stt_billing` (from STT s/min ≥ 50 → stream), `tool_calls_per_session` (from `tool_call_started` events). Each derived value replaces the default with `source="workspace"` and its p25–p75 as low/high. Computed per request (the analytics route already scans sessions live; the same trade-off), no cache table.

**Range.** `low`/`high` = the estimate recomputed with every assumption at the bound that lowers/raises cost. A band from stated assumptions, not a confidence interval; the UI says "typically $a–$b".

**Where it runs.** `costs/estimate.py` (pure functions over `AgentConfig` + assumptions + a `quote` callable; no I/O) serves the agent route, the template gallery, the picker figures and the session snapshot.

### D-V4-42 — One estimate route with three sources, one quotes route for pickers, one prices route for admins; nothing new on `AgentConfig`

- `POST /v1/cost-estimates` (`agents:read`; new `routers/costs.py`) with `CostEstimateRequest{agent_id | template_id | config (exactly one), assumptions: dict[str, float | str] | None, channel: "web" | "phone" | "text" = "web"}`. `config` is the editor's unsaved draft (validated as `AgentConfig`, no side effects); `template_id` uses the starter's pipeline or the pack default. Response `CostEstimate` (§3.1). `POST /v1/agents/{id}/cost-estimate` is the same handler on the ask's path (what the MCP tool calls).
- `POST /v1/pricing/quotes` (`agents:read`) with `{items: [{provider_id, model}]}` (≤ 100) → per item `{provider_id, model, kind, quotes: PriceQuote[], per_minute_usd: Decimal | None, note}`, `per_minute_usd` being **that slot's own share** at default assumptions (STT: 60 s × price; LLM: the LLM lines at the default prompt; TTS: chars × price; realtime: audio in+out; avatar: 1 min). Batched so the combobox asks once per open; the response carries `price_version` and `as_of` and the console caches it with react-query `staleTime` (one hour, the catalog TTL) — no ETag on a POST.
- `GET /v1/cost-estimates/assumptions` (`agents:read`) → the workspace's effective assumptions with sources.
- `GET /v1/workspace/prices` (`agents:read`: builders see why a line is priced) and `PUT /v1/workspace/prices` (`providers:write`, admin) → `{prices: WorkspacePrice[]}` stored in `workspaces.settings["cost"]["prices"]` (≤ 100 rows, validated: `provider_id` a registry id or a pseudo id, `unit` a `Unit`, USD), audit row `workspace.prices_updated`.
- `GET /v1/templates` items gain `estimate: TemplateEstimate{per_minute_usd_mid, per_minute_usd_low, per_minute_usd_high, as_of, unpriced: int} | None` (additive on `TemplateOut`; default assumptions, no workspace averages; cached in-process per `PRICE_VERSION`).
- **Assumptions are not a config field**: an assumption is a property of the person asking, not of the agent. Overrides live in the request; the console remembers the last-used overrides per agent in `localStorage`.

### D-V4-43 — Every session carries its estimate from creation, at the pinned config version and that day's prices; the comparison is per minute

- Migration **`v4_003_session_estimates`** (pre-authorised by R-V4-47): `sessions.estimate JSON NULL` (the `CostEstimate` at creation, trimmed: per-minute figures, lines, assumptions, `price_version`, `as_of`), `sessions.estimated_usd NUMERIC(12,6) NULL`, `sessions.reconciled_usd NUMERIC(12,6) NULL`, `usage_daily.estimated_usd NUMERIC(14,6) NULL`, `session_costs.price_source VARCHAR(16) NOT NULL DEFAULT 'table'`, `session_costs.vendor_usd NUMERIC(12,6) NULL`, `session_costs.vendor_ref VARCHAR(64) NULL`. No index changes.
- `costs/snapshot.py::attach_estimate(database, session_id)` runs as a **background task after the creation response is committed** at the three creation sites (`routers/connect.py`, `routers/text_sessions.py`, `routers/internal.py` for worker-registered sessions): it re-reads the row, resolves the config the row pins (`config_version`, like `config_for_session`), and stores the estimate with workspace averages and prices. The token endpoints stay fast (the > 500 ms rule): workspace averages are cached in-process per workspace for 10 minutes (`costs/assumptions.py`, a small TTL dict), and the estimator itself is pure. A failure never fails session creation (logged, `estimate=None`); a summary that arrives before the task ran (a sub-second session) finds no snapshot and reports "no estimate".
- At summary time (`cost_session`), `estimated_usd = per_minute_usd.mid × actual minutes + per-session lines`; `render_cost` returns the comparison (§4.3). A session with no snapshot (created before the migration) shows "no estimate" — never a back-filled one (prices and config may have changed).
- Text-channel sessions (`chat_start`) are estimated with `channel="text"`: **a text chat is a real LiveKit room with the agent dispatched** (`text_sessions.py` mints a participant token for `room_name_for(session_id)`), so the call-minute and participant-minute lines stay; only the audio lines (STT, TTS, realtime audio tokens, avatar, phone) drop. The actual-cost side does the same (`_infra_lines` reads `session.channel`), so a test chat never shows a systematic variance.

### D-V4-44 — Actual cost is complete before it is reconciled

`compute_usage_lines` prices every billable field the SDK reports, by slot as today:

- **LLM** (`llm_usage` on `llm`/`realtime`/`workflow_llm`): if the model has a `text_tokens_in` or `audio_tokens_in` quote (a realtime model), price `input_text_tokens`, `input_audio_tokens`, `output_text_tokens`, `output_audio_tokens` under those units and **never** the folded totals; else `input_tokens` under `tokens_in`, `output_tokens` under `tokens_out`. When a `cached_tokens_in` quote exists, the `tokens_in` (or `text_tokens_in`) quantity is `input_tokens − input_cached_tokens` and a `cached_tokens_in` line carries the cached part; without one, the full count is priced at the uncached rate (conservative; `note="cache discount not modelled"`). `input_image_tokens` stay inside `input_tokens` for chat models; `output_reasoning_tokens` stay inside `output_tokens` (the SDK says so).
- **TTS** (`tts_usage`): `characters_count` under `chars` when > 0; else `input_tokens`/`output_tokens` under `tokens_in`/`tokens_out` (`gpt-4o-mini-tts`, Gemini TTS become priceable); else `audio_duration` under `audio_s_out`.
- **STT** (`stt_usage`): `audio_duration` under `audio_s_in`; token-billed STT (`input_tokens`/`output_tokens` > 0, or 1.8.3's `input_audio_tokens`) under `tokens_in`/`audio_tokens_in`/`tokens_out`.
- **Turn detection and VAD** (`eot_usage`/`interruption_usage`): `total_requests` under `requests` on `pipeline.turn_detection`/`pipeline.vad` — priced only when the table has a row (§1.3: expected "included", a known zero, if the page says so); else shown unpriced.
- **Infrastructure** (pseudo ids): `livekit-agent` `minutes` (wall-clock; the 10-s minimum applied), `livekit-participant` `minutes` × 2, `livekit-sip` `minutes` when `channel="phone"` (model `local-inbound`/`toll-free-inbound` for a LiveKit-hosted number per its `phone_numbers` row, `trunk` for a third-party trunk), `livekit-egress` model `audio`/`video` from `config.recording.audio_only` (the existing `recording_duration_s` path, model added).
- **Avatar**: wall-clock minutes, unchanged.
- **Image generation** (`pipeline.image_gen`): the worker reports no usage; the count of `asset {kind: "image"}` events is priced under `images` at summary time (the api owns the events) — a stated approximation.
- **Embeddings** (knowledge): not metered per session by the worker; an estimate line only (`note="not metered per session"`) until ask #96 (the worker reporting `search_knowledge` query counts) is decided.

Persisted lines keep `price_source` so a line priced from OpenRouter's live sheet or from a workspace price is distinguishable from a table one; `unit_price_usd` remains the price at costing time. An OpenRouter session is costed **live** at the price the cached catalog listed when the session ended (`price_source="live"`).

### D-V4-45 — Reconciliation is opt-in, vendor by vendor: OpenRouter first, by generation id, from the worker's per-request metrics; Deepgram designed in the same shape

- **What is possible** (§1.2): OpenRouter returns the exact charge per generation (`GET /api/v1/generation?id=`, regular key) and in-band (`usage.cost`); Deepgram returns USD per request (`/v1/projects/{project_id}/requests/{request_id}`, regular key, needs the project id). OpenAI and Anthropic report only behind admin keys and only per day; ElevenLabs per request in characters; Simli/LiveAvatar/Anam/D-ID per session in minutes (LiveAvatar in credits); Cartesia, AssemblyAI, Google, Groq, LiveKit Cloud nothing per session.
- **Design.** The workspace opts in (`settings["cost"]["reconcile"]: list[str]`, values `openrouter`, `deepgram`), delivered to the worker as `ResolvedAgentConfig.cost_reconcile: list[str] = []` (additive; **V4-17's own contracts step** — `agent_config.py` is not touched by V4-15, so V5 wave 1 is unaffected). Only then `observability.py` subscribes to `metrics_collected` — accepting the SDK's one deprecation warning per session, logged once at info with the reason — and collects `(request_id, provider, model)` from `LLMMetrics`/`STTMetrics`/`TTSMetrics` into one `metrics {kind: "provider_requests", data: {llm: [...], stt: [...], tts: [...]}}` event posted just before the summary (ids only, never a payload; ≤ 2,000 ids). The api job `cost_reconcile` (`COST_RECONCILE`, enqueued by `cost_session` when the event exists and the workspace opted in) resolves the session's OpenRouter credential like the catalog adapter does, calls `/generation?id=` per id through `net_guard` (≤ 10/s; a 404 is retried once after 30 s — the generation record lands within seconds but the delay is undocumented), sums `total_cost` per (provider, model), writes `session_costs.vendor_usd` + `vendor_ref` (`"12 generations"`) on the matching LLM lines and `sessions.reconciled_usd`, and records a `cost_reconciled` audit row. The in-band `usage.cost` is **not** used: the SDK's stream parser drops it (§1.1) and a plugin patch is not worth the drift risk.
- **Deepgram** is the same shape with the vendor client in `costs/vendors/deepgram.py`, gated on the credential carrying a project id (ask #94: a `project_id` field on `deepgram-stt`/`deepgram-tts`); built in V4-17 only if that ask is decided by then, else designed.
- **Never** a vendor admin key: OpenAI/Anthropic organisation reports stay a manual export, said in the analytics help text.

### D-V4-46 — Estimate vs actual: per session, per agent, per day, with a driver-level explanation

`SessionCost` (`GET /v1/sessions/{id}`) grows: `estimated_usd`, `estimate_per_minute_usd`, `variance_usd` (= `total_usd − estimated_usd` when both exist), `variance_pct`, `reconciled_usd`, `price_version`, `estimate_as_of`, and `drivers: list[CostDriver]` with `CostDriver{slot, provider_id, model, unit, estimated_quantity, actual_quantity, estimated_usd, actual_usd, delta_usd, reason}`, `reason ∈ {"more minutes", "fewer minutes", "more talk", "less talk", "longer prompts", "more turns", "unpriced line", "price changed", "not estimated", "as estimated"}` chosen by comparing per-minute quantities with the snapshot's assumption bands (outside → the matching reason; inside → "as estimated"); sorted by `|delta_usd|`; unpriced on either side → `"unpriced line"`, `delta_usd=None`. `SessionOut` (list rows) gains `estimated_usd`. `AnalyticsSummary` and `AnalyticsBucket` gain `estimated_usd`, `sessions_estimated`, `accuracy_pct` (`actual / estimated × 100` over sessions with both); `AnalyticsSummary.top_drivers: list[AnalyticsDriver{provider_id, model, unit, cost_usd, share_pct, estimated_usd}]` (grouped over `session_costs` in the range, top 8). `usage_daily` gains `estimated_usd`.

### D-V4-47 — The console shows the estimate where the choice is made and the comparison where the money went; dialogs only; "estimate" always labelled; no jargon

§5. Every figure that is not a persisted cost line is prefixed "≈" and carries the word "estimate" in the same component, with the source and date one line or one hover away. No drawers (R-V3-2). "Tokens", "egress", "SIP", "LLM/STT/TTS" never appear in a summary sentence — the breakdown uses plain labels ("Caller's speech → text", "Agent's thinking", "Agent's voice", "Video avatar", "Call minutes", "Phone minutes", "Recording", "Knowledge lookups", "Quality review"); the technical unit shows only in the expandable line detail.

### D-V4-48 — MCP: `cost_estimate`, `pricing_quote`, `cost_summary`; cost fields ride on the session tools; `activity` stays audit

`activity()` lists audit rows (configuration changes); cost belongs to sessions, so `session_list`/`session_get` carry the new fields through the api models (no tool code), and a new read tool `cost_summary(range)` wraps `/v1/analytics/summary`. `cost_estimate(...)` and `pricing_quote(...)` are new read tools in a new `mcp/tools/costs.py`. Workspace prices are written through `api_request` (no dedicated write tool in v1).

## 3. The per-minute estimate

### 3.1 Contract (`lkap_contracts/api_models.py`, additive; exported for the `.d.ts`)

```python
AssumptionSource = Literal["default", "workspace", "request"]
EstimateSlot = Literal["stt","llm","tts","realtime","avatar","workflow_llm","image_gen","embedding",
                       "turn_detection","vad","livekit_agent","livekit_participant","livekit_sip",
                       "livekit_egress","qa_judge"]

class Assumption(BaseModel):
    key: str; value: float | str; low: float | None = None; high: float | None = None
    unit: str | None = None; source: AssumptionSource; label: str; source_url: str | None = None

class EstimateLine(BaseModel):
    slot: EstimateSlot; label: str                  # the plain-language label of D-V4-47
    provider_id: str; model: str | None; unit: Unit
    quantity_per_min: Decimal | None                # None for per-session lines
    quantity_per_session: Decimal | None
    quote: PriceQuote | None
    usd_per_min: Decimal | None                     # None when unpriced
    usd_per_session: Decimal | None
    note: str | None = None                         # "no price", "not metered per session", "included with LiveKit Cloud"

class MoneyRange(BaseModel):
    low: Decimal; mid: Decimal; high: Decimal

class CostEstimate(BaseModel):
    per_minute_usd: MoneyRange | None               # None when every line is unpriced
    per_session_usd: MoneyRange | None
    session_minutes: float
    channel: Literal["web","phone","text"]
    lines: list[EstimateLine]
    assumptions: list[Assumption]
    unpriced: list[str]                             # "Agent's voice — ElevenLabs eleven_flash_v2_5 (characters)"
    priced_share: float                             # 0–1, by line count
    price_version: str; as_of: str                  # the oldest as_of among the quotes used
    sources: list[PriceSource]
    caveats: list[str]                              # tier notes, free-tier notes, "streaming STT bills silence"

class TemplateEstimate(BaseModel):
    per_minute_usd_mid: Decimal; per_minute_usd_low: Decimal; per_minute_usd_high: Decimal
    as_of: str; unpriced: int
```

### 3.2 Lines per pipeline mode

- **cascaded**: `stt` (`audio_s_in` = 60 or 60 × caller ratio per `stt_billing`), `llm` (`tokens_in` = prompt × turns/min + history growth; `tokens_out`), `tts` (`chars` = wpm × chars_per_word × agent ratio, or `audio_s_out`/`tokens_out` when the quote has that unit), `turn_detection`/`vad` (`requests` ≈ user turns/min, when a quote exists), plus the shared lines.
- **realtime**: `realtime` (`audio_tokens_in` = 60 × caller ratio × rate, `audio_tokens_out` = 60 × agent ratio × rate, `text_tokens_in` = prompt + history at the text rate, `text_tokens_out` ≈ the transcript of the spoken output); no STT/TTS lines; if the model has only `tokens_in/out` quotes the line is unpriced with `note="audio-token price unknown"` — never priced at the text rate.
- **half_cascade**: the realtime model's `audio_tokens_in`/`text_tokens_*` lines plus a `tts` line (its audio output is unused).
- **Shared**: `avatar` (`minutes`), `livekit_agent` (`minutes`), `livekit_participant` (`minutes` × 2), `livekit_sip` (phone), `livekit_egress` (recording), `embedding` (`tokens_in` = `kb_queries_per_turn × user turns × 30`; fastembed → known zero), `workflow_llm` (flows: one extra call per transition, sized like a tool call), `qa_judge` (per session), `image_gen` (`images` per session, default 0, editable). `channel="text"` drops `stt`, `tts`, the `realtime` audio-token lines, `avatar` and `livekit_sip`; `livekit_agent` and `livekit_participant` stay (a text chat is a LiveKit room).
- **LLM context growth**: with `T = agent_turns_per_min × session_minutes` turns, `Σ input = T × prompt_tokens + history_tokens_per_turn × T(T+1)/2`; per minute ÷ `session_minutes`. Tool calls add `tool_calls_per_session × (prompt + mid-session history)` input and 40 output each. This is why `session_minutes` is an assumption and why a long call costs more per minute; the dialog shows per-minute and per-session at the assumed length.

### 3.3 Worked example (today's table, LiveKit Inference "Build" rows, as_of 2026-09-23; Cloud rows from §1.3 pending their `as_of`)

Cascaded, `deepgram/nova-3` (STT $0.0048/min), `openai/gpt-4o-mini` ($0.150/$0.600 per 1M), `cartesia/sonic-3` ($50/1M chars), defaults, 5-minute web session, no avatar, no recording:

| Line | Quantity per minute | Price | $/min |
|---|---|---|---|
| Caller's speech → text (`deepgram/nova-3`) | 60 s (`stt_billing=stream`) | $0.00008/s | 0.0048 |
| Agent's thinking, input (`openai/gpt-4o-mini`) | 1,500 × 3 + 140 × (15·16/2) / 5 = 4,500 + 3,360 = 7,860 tokens | $0.15/1M | 0.0012 |
| Agent's thinking, output | 3 × 60 = 180 tokens | $0.60/1M | 0.0001 |
| Agent's voice (`cartesia/sonic-3`) | 150 × 6 × 0.45 = 405 chars | $50/1M | 0.0203 |
| Call minutes (`livekit-agent`) | 1 | $0.01 (Ship/Scale overage; Build includes 1,000/month) | 0.0100 |
| Participant minutes (`livekit-participant`) | 2 | $0.0005 | 0.0010 |
| **Total** | | | **≈ $0.037/min**, typically $0.028–$0.053 (talk ratios 0.30–0.60, tokens ×0.8–×1.3) |

The agent's voice dominates a cascaded pipeline at list prices; a realtime pipeline is dominated by audio-out tokens (Gemini Live: 25 tok/s × 60 × 0.45 = 675 tokens/min × $12/1M ≈ $0.008/min out, $0.002/min in). The breakdown makes that visible, which is the point.

## 4. Actual cost

### 4.1 Costing (D-V4-44) — `costs/service.py`

`compute_usage_lines(pipeline, usage, *, quote)` takes the `quote` callable (bound to the workspace prices and, for an OpenRouter slot, the cached catalog item). New helpers `_llm_lines`, `_tts_lines`, `_stt_lines`, `_requests_lines`, `_infra_lines(session, config, number_row)`, `_image_lines(events)`. `cost_session` persists priced lines with `price_source`, computes `estimated_usd` from the snapshot, writes `sessions.cost_usd` as today, and enqueues `COST_RECONCILE` when a `provider_requests` event exists and the workspace opted in.

### 4.2 Snapshot (D-V4-43) — `costs/snapshot.py`

`attach_estimate(database, session_id)`: a background task after creation; `CostEstimate` at the pinned config with the (10-minute-cached) workspace averages and the workspace prices, stored trimmed on `sessions.estimate`. Idempotent.

### 4.3 Read path (D-V4-46) — `render_cost`

Returns the enriched `SessionCost` with `drivers` from joining estimate lines to actual lines on `(slot, unit)`; per-minute quantities are `actual_quantity / actual_minutes`. `reason` by the first matching rule: no actual line → "not estimated" / "unpriced line"; `price_version` differs and the unit price differs → "price changed"; minutes outside the `session_minutes` band → "more/fewer minutes"; talk-driven units (`chars`, `audio_s_*`, `audio_tokens_*`) outside the ratio band → "more/less talk"; `tokens_in` per turn above the prompt band → "longer prompts"; turns/min above the band → "more turns"; else "as estimated".

### 4.4 Analytics — `routers/analytics.py`, `jobs/rollup.py`

The live scan adds `estimated_usd` per session to totals and buckets, counts `sessions_estimated`, computes `accuracy_pct`, and runs one grouped query over `session_costs` for `top_drivers`. The rollup writes `usage_daily.estimated_usd`.

### 4.5 Reconciliation (D-V4-45) — V4-17

Worker: `observability.py` gains `_on_metrics(ev)` under the opt-in (`ResolvedAgentConfig.cost_reconcile`), collecting ids into `self._requests` and posting one `provider_requests` metrics event before the summary; `docs/CONTRACTS.md` §7 notes the new `metrics` kind. api: `jobs/reconcile.py` (`COST_RECONCILE`), `costs/vendors/openrouter.py` (`GET /api/v1/generation`), `costs/vendors/deepgram.py` (designed; built if ask #94 is decided), `vendor_usd`/`vendor_ref` on the lines, `sessions.reconciled_usd`, the audit row.

## 5. UX

Every surface is labelled "estimate" and shows source + date (D-V4-47). Dialogs only.

1. **Agent editor summary rail** (`agents/editor/summary-rail.tsx`): `RailRow label="Cost"` → "≈ $0.04/min · estimate", muted "typically $0.03–$0.05"; click opens the **Cost estimate dialog** (`agents/editor/cost-estimate-dialog.tsx`, new): per-minute and per-session figures; the breakdown table (plain label, what it counts, quantity/min, price with source + date, $/min; the technical unit under a disclosure); "Assumptions" with fields for the editable ones (call length, how much the caller talks, how much the agent talks, replies per minute, words per reply, tool calls per call, web/phone); "Use my workspace's averages" (`GET …/assumptions`); "Not included (no published price): …" with a **"Set a price"** link per line that opens the **Your prices dialog** (`settings/workspace-prices-dialog.tsx`, new; admin only; a table of provider · model · unit · USD · note, saved with `PUT /v1/workspace/prices`); footer "List prices at the entry tier as of <date>; OpenRouter prices live; your own prices where set. Estimates are not bills." The dialog posts the draft `config`, debounced 500 ms, keyed by the pipeline+knowledge+tools+qa+recording subset of the form.
2. **Providers section** (`agents/tabs/providers-tab.tsx`, `registry/provider-slot-card.tsx`): the section header shows the same "≈ $/min · estimate" (one query shared through the editor context); each slot card summary a small "≈ $0.020/min" chip (that slot's `usd_per_min`) or a muted "no price" with a "Set a price" affordance for admins; a stale quote gets "prices from <date>" as its title.
3. **Model combobox rows** (`registry/model-combobox.tsx`): a trailing "≈ $0.004/min" on suggested and catalog rows from one `POST /v1/pricing/quotes` for the visible ids per open (≤ 100; more rows on scroll), muted "no price" otherwise; the custom-id row shows nothing.
4. **Template gallery** (`agents/create/template-tile.tsx`): a "≈ $0.04/min" pill from `TemplateOut.estimate`, `title="Estimate at list prices as of <date>, before your own usage"`; no pill when nothing is priced.
5. **Session detail Cost tab** (`sessions-v2/cost-tab.tsx`): tiles "Estimated", "Actual", "Difference" (with %; "no estimate" for older sessions; "Vendor charged" when reconciled); the lines table gains "Estimated" and, when present, "Vendor charged" columns; a "Why it differs" list (one sentence per driver: "The agent spoke more than expected: 620 characters/min vs 405 → +$0.011"); footer "Prices as of <date>". Unpriced lines stay "no price".
6. **Analytics** (`analytics/analytics-view.tsx`): a fifth tile "Estimated" with "accuracy 96 %" beneath; the by-day chart with two series (actual, estimated); a "Top cost drivers" table (plain label · provider · cost · share · estimated). Help text: "Actual cost is computed from usage at list prices; OpenRouter sessions use live prices and can be reconciled with OpenRouter's charge. Vendor invoices may differ (included minutes, volume tiers, taxes)."
7. **Sessions list**: the Cost column shows "$0.12 (≈ $0.10)" with the estimate muted.

Mobile (375 px): the estimate dialog stacks the breakdown as cards; the Cost tab tiles wrap; no horizontal page scroll; axe green.

## 6. MCP

`mcp/tools/costs.py` (new; added to `server.TOOL_MODULES`):

- `cost_estimate(agent_id_or_slug: str | None = None, template_id: str | None = None, config: AgentConfig | None = None, session_minutes: float | None = None, assumptions: dict[str, float | str] | None = None, channel: "web" | "phone" | "text" = "web")` → `CostEstimate`; `scopes={"agents:read"}`, `annotations=READ`; the docstring: "an estimate at list prices, not a bill", the assumption keys named.
- `pricing_quote(provider_id: str, model: str | None = None)` → `{quotes: PriceQuote[], per_minute_usd, note}`; `agents:read`.
- `cost_summary(range: "today" | "7d" | "30d" | "all" = "30d")` → `AnalyticsSummary`; `sessions:read`.
- `cost_estimate` calls `POST /v1/agents/{id}/cost-estimate` for an agent and `POST /v1/cost-estimates` for a template or a config. `session_list`/`session_get` carry `estimated_usd`, `variance_usd`, `reconciled_usd`, `drivers` through the api models. `docs/concepts/recordings-and-cost.md` gains "Estimates" and "Your prices"; `docs/recipes/estimate-agent-cost.md` (new): `cost_estimate` before `agent_publish`, a test chat, then `session_get` to compare. `tests/tools.snap.json` regenerated (three new tools).

## 7. Tests

- **contracts** (`contracts/tests/test_pricing.py`, create): `Unit` literal lengths ≤ 16; `PriceQuote`/`WorkspacePrice` round-trips; `quote()` precedence (workspace over live over table over none; model-specific over agnostic; `price_ref` alias; no self-alias; every `price_ref` names a registry id); `PRICE_VERSION ≥ max(as_of)`; every row has `source_url` + `as_of`; tiered rows have `tier_note`; an OpenRouter `:free` price is a known zero; the OpenRouter unit map per entry kind.
- **api** `test_cost_estimate.py` (create): §3.3 reproduces to 4 decimals; realtime with only `tokens_in` quotes → unpriced with the note; `half_cascade` has TTS and no STT; `phone` adds SIP with the right model from the number row; recording adds egress `audio`/`video`; QA adds the judge line; `text` drops the audio lines and keeps the call-minute lines; `template_id` with a `None` pipeline uses the pack default; a draft `config` with a validation error → 422; workspace averages at 10 sessions, not 9; workspace prices win over the table; low ≤ mid ≤ high; `unpriced` names every unpriced line; the quotes route returns `price_version`/`as_of`; the snapshot task runs after the creation response and a failing estimate still returns 200; `PUT /v1/workspace/prices` validates ids/units and audits. `test_costs.py` additions: realtime split pricing never uses folded totals; cached tokens subtracted only with a cached quote; token-billed TTS/STT; `requests` lines; agent/participant/SIP/egress minutes by channel and recording; live OpenRouter costing → `price_source="live"`; snapshot idempotent; `estimated_usd` = mid × minutes + per-session lines; `drivers` reasons in rule order; older sessions "no estimate". `test_analytics.py`: `estimated_usd`, `accuracy_pct`, `top_drivers`. `test_catalog_drift.py`: `price_drift` (OpenRouter twin and the LiveKit payload, tolerance 2 %, payload failure → one skipped row) and `price_stale`. `test_migrations`: `v4_003` up/down.
- **web**: rail row and dialog (figure, edits → refetch, workspace averages, unpriced list, "Set a price" opens the prices dialog, mobile), slot chips, combobox trailing figures from one batched request, template pill, Cost tab tiles/columns/drivers, analytics tile/series/table; axe on each dialog; no `sheet` import.
- **mcp**: the three tools' snapshot rows; `cost_estimate` is read-only; doc lint.
- **worker (V4-17)**: `metrics_collected` subscribed only under the opt-in; the `provider_requests` event shape; no ids in logs; the `cost_reconcile` job against a `respx` OpenRouter `/generation` fake (sum, 404 retry, the credential resolved, no secret in logs).

## 8. Live check

On the dev stack (R-V4-17 rules; a Builder key minted and revoked), recorded in `docs/v4/_briefs/v4-15-live.md`:

1. `Demo — Receptionist` in the editor: the rail shows "≈ $/min · estimate"; open the dialog; switch the TTS to `deepgram/aura-2` without saving → the figure changes; save → unchanged; "Use my workspace's averages" changes at least one assumption's source to workspace (the dev workspace has > 10 sessions).
2. The gallery shows pills for starters with a priced pipeline; record `blank`'s.
3. The LLM combobox on `livekit-inference-llm` shows "≈ $/min" on `openai/gpt-4o-mini` and "no price" on `openai/gpt-oss-120b`.
4. Set a workspace price for `bey-avatar` `minutes` ($0.10, note "Starter plan"); the vision agent's estimate gains the avatar line with "Your price, set <date>".
5. One 3-minute browser session and one text chat: the Cost tab shows the snapshot, the actual lines include call minutes and participant minutes, the drivers read sensibly; the text chat has call minutes in both columns and no audio lines in either.
6. On `Demo — Vision assistant` (OpenRouter): LLM lines show `price_source="live"` at OpenRouter's listed price; with the opt-in (V4-17) `reconciled_usd` appears within a minute and is within 5 % of the actual line — the STT/TTS OpenRouter unit inference of §1.2 is confirmed here on an `openrouter-stt`/`openrouter-tts` session (a line whose live cost is off by ~60× means the unit map is wrong: file an ask, do not patch).
7. Analytics (7d): the "Estimated" tile and accuracy, the two-series chart, the drivers table; every figure and date shown recorded.
