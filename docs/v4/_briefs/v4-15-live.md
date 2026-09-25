# V4-15 live brief: costs (pricing sources, the per-minute estimate, estimate vs actual)

Status: **offline part done, live steps pending the merge.** The package ran in a
worktree; the dev api (port 8000) runs the main checkout, so the live steps below
run after the coordinator merges V4-15, applies `v4_003_session_estimates` (with a
database backup) and reloads the api. Nothing in this brief needs a worker restart.

## 1. Price rows re-read on 2026-09-25

Every row of `COSTS.md` §1.3 was read off its official page on 2026-09-25 (raw page
source; Google via its `.md.txt` variant). `as_of = 2026-09-25`,
`PRICE_VERSION = 2026-09-25`.

| Provider | Rows | Source | Result |
|---|---|---|---|
| `openai-llm` | gpt-4.1 $2.00 / cached $0.50 / $8.00; gpt-4.1-mini $0.40 / $0.10 / $1.60; gpt-4o $2.50 / cached $1.25 / $10.00; gpt-4o-mini $0.15 / $0.075 / $0.60 (per 1M) | developers.openai.com/api/docs/pricing | matches |
| `openai-realtime` | gpt-realtime text $4 / cached $0.40 / $16, audio $32 / $64; gpt-realtime-mini text $0.60 / $0.06 / $2.40, audio $10 / $20 | same | matches (the page now leads with gpt-realtime-2.1, ask #105) |
| `openai-tts` | tts-1 $15/1M chars; gpt-4o-mini-tts $0.60 in / $12.00 out per 1M tokens | same | matches (no per-minute figure on the page) |
| `openai-stt` | gpt-4o-transcribe audio in $2.50, out $10.00 (+ $0.006/min estimate); mini $1.25 / $5.00 ($0.003/min) | same + model page | the input price is "Audio tokens" (ask #92) |
| `anthropic-llm` | claude-sonnet-4-6 $3 / cached $0.30 / $15; claude-haiku-4-5 $1 / $0.10 / $5 | platform.claude.com/docs/en/about-claude/pricing | matches |
| `google-llm` | gemini-2.5-flash cached $0.03 (+ existing $0.30 / $2.50); gemini-3.5-flash $1.50 / $0.15 / $9.00 | ai.google.dev/gemini-api/docs/pricing | matches |
| `google-realtime` | 2.5-flash-native-audio-preview-12-2025 text $0.50 / audio $3.00 in, text $2.00 / audio $12.00 out; 3.8-live, 3.8-live-extended-thinking, 3.1-flash-live-preview $0.75 / $3.00 / $4.50 / $12.00 | same | matches; 25 tokens/s is a footnote under the TTS models |
| `google-tts` | gemini-3.8-flash-tts $0.50 in / $9.00 out per 1M | same | matches; rises to $1.00 / $18.00 on 2027-01-01 (in `tier_note`) |
| `groq-llm`, `groq-stt`, `groq-tts` | gpt-oss-120b $0.15 / $0.60; gpt-oss-20b $0.075 / $0.30; whisper-large-v3-turbo $0.04/h; whisper-large-v3 $0.111/h; canopylabs/orpheus-v1-english $22/1M chars | console.groq.com/docs/models | matches |
| `deepgram-stt`, `deepgram-tts` | nova-3 $0.0048/min; flux-general-en $0.0065/min; Aura-2 $0.030/1k; Aura-1 $0.015/1k | deepgram.com/pricing | streaming figures are a **limited-time promotion** (regular $0.0077/min, ask #103); Nova-2 no longer in the rate table (left out) |
| `assemblyai-stt` | universal-streaming $0.15/h, billed on WebSocket session time | assemblyai.com/pricing | matches |
| `rime-tts` | mistv3 $0.03/1k chars (Starter) | rime.ai/pricing | matches |
| `inworld-tts` | inworld-tts-2 $25/1M; inworld-tts-2-flash $15/1M (on-demand) | inworld.ai/pricing | matches |
| `livekit-inference-*` | STT nova-3 $0.0048, flux $0.0065, universal-streaming $0.0025, ink-whisper $0.0030, gemini-3.5-transcribe-live $0.0095 (per min); LLM gpt-4.1 $2.00 / cached $0.50 / $8.00, gpt-4o-mini $0.15 / $0.075 / $0.60, gemini-3.5-flash $1.50 / $0.15 / $9.00, gemma-4-31b-it $0.40 / $0.20 / $1.20; TTS sonic-3 $50, aura-2 $30, mistv3 $30, inworld-tts-2 $25 (per 1M chars), Build tier | livekit.com/pricing/inference | matches; gpt-oss-120b stays unpriced (two routes, ask #93) |
| LiveKit Cloud pseudo ids | agent minutes $0.01; participant minutes $0.0005; SIP local inbound $0.01, toll-free $0.02, trunk $0.004; egress audio $0.005, video $0.02 (per min) | livekit.com/pricing, docs.livekit.io/deploy/admin/billing | matches; 10-second minimum per agent session |
| `fastembed-embedding` | known zero | github.com/qdrant/fastembed | runs on the worker |

Not added: turn-detector / VAD "included" rows (the page shows an icon, not the word, ask #106); `nova-3-multi` (no registry id).

## 2. Live steps (COSTS.md §8, steps 3–7 minus the console) — pending

Run through the MCP tools and `api_request` with a Builder key minted and revoked
for the run; record every figure, source and date here.

1. **OpenRouter speech units first** (step 6's check): `pricing_quote(provider_id="openrouter-stt", model="deepgram/nova-3")` should quote `audio_s_in` ≈ $0.0000717/s ($0.0043/min) and `pricing_quote(provider_id="openrouter-tts", model="deepgram/aura-2")` `chars` ≈ $0.00003. After an `openrouter-stt`/`openrouter-tts` session, a line ~60× off the vendor's figure means the unit map is wrong: file an ask, do not patch.
2. `pricing_quote(provider_id="livekit-inference-llm", model="openai/gpt-4o-mini")` shows a per-minute figure; `openai/gpt-oss-120b` shows "no price".
3. `cost_estimate(agent_id_or_slug="<Demo — Receptionist>")` and `cost_estimate(agent_id_or_slug="<Demo — Vision assistant>")`: per-minute band, lines, unpriced list, sources and dates. With `workspace_averages=true` at least one assumption reads `source: "workspace"` (the dev workspace has more than 10 sessions).
4. Set a workspace price: `api_request(method="PUT", path="/v1/workspace/prices", body={"prices": [{"provider_id": "bey-avatar", "unit": "minutes", "usd_per_unit": 0.10, "note": "Starter plan", "as_of": ""}]})`; the vision agent's estimate gains the avatar line with `quote.source: "workspace"`.
5. One 3-minute browser session and one text chat (`chat_start` … `chat_end`): `session_get` shows `cost.estimated_usd`, the actual lines with call and participant minutes, `drivers`; the text chat has call minutes in both columns and no audio lines in either.
6. On `Demo — Vision assistant` (OpenRouter LLM): the LLM lines carry `price_source: "live"` at OpenRouter's listed price.
7. `cost_summary(range="7d")`: `estimated_usd`, `accuracy_pct`, `top_drivers`.
