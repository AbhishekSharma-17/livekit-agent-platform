# Lowest-latency cascaded voice stack for LKAP (STT + LLM + TTS + turn handling)

Research date: 2026-09-30. SDK: livekit-agents 1.8.3 (source read in `agent/.venv`). Speech-to-speech realtime APIs are out of scope. No code, agent config or keys were touched and no paid API was called. Every latency figure below is either **our own measurement** (from the brief), a **vendor claim**, or an **independent measurement**, and is labelled as such.

Abbreviations: `SDK/` = `agent/.venv/lib/python3.12/site-packages/livekit/`. "TTFT" = LLM time to first token. "TTFA/TTFB" = TTS time to first audio byte. "EOT" = end of turn. "mouth-to-ear" = caller stops speaking → first agent audio heard.

## 1. Executive summary

**Where the time goes today.** Our best stack right now is Flux + OpenRouter `openai/gpt-6-luna` (effort `none`) + Aura-2. It measures 3.0 to 3.4 s on Cloud and 2.4 to 2.9 s on the DGX with the worker still on the Mac. The main contributors:

- **LLM first token: 1.0 to 2.0 s.** This is the largest single stage. Artificial Analysis measures the same model at 0.78 s direct from OpenAI.
- **Turn handling: about 0.25 to 0.5 s we don't need to spend.** Flux decides the turn, and the `balanced` preset then still waits out `min_delay` (F1). No preemptive TTS runs (F3).
- **Aura-2 first byte: 0.33 to 0.38 s.**
- **Network: about 1.2 s** from the Tailscale relay on the DGX path.

Every recommendation below attacks those four.

**Cost assumptions for every $/min figure.** One call-minute. STT is billed for the whole minute. The agent speaks about 600 characters. There are 4 LLM turns of about 3,000 input and 60 output tokens each. Eager end-of-turn with preemptive generation adds about 60% more LLM calls (Deepgram says "50 to 70%"), and preemptive TTS wastes about 20% extra characters. Prices are list pay-as-you-go. LiveKit platform fees (WebRTC, agent-session minutes, a $50/month Ship plan) are excluded.

### Stack A: fastest on LiveKit Cloud with LiveKit Inference

| Slot | LKAP value |
|---|---|
| `pipeline.stt` | `{"provider_id": "livekit-inference-stt", "model": "deepgram/nova-3", "fields": {"language": "en"}}` |
| `pipeline.llm` | `{"provider_id": "livekit-inference-llm", "model": "google/gemma-4-31b-it", "fields": {"temperature": 0.4}}` |
| `pipeline.tts` | `{"provider_id": "livekit-inference-tts", "model": "inworld/inworld-tts-2-flash", "fields": {"voice": "Ashley", "language": "en"}}`. This id is valid (model ids are free-form under `model_id_rules`) but is not in the registry's suggested list. Fall back to `deepgram/aura-2` if it sounds worse. |
| `pipeline.vad` | unset (the prewarmed Silero) |
| `pipeline.turn_detection` | unset. LKAP synthesizes `inference-turn-detector`. |
| `pipeline.turn_detector` | `{"mode": "hosted"}` (hosted `v1`, `local_fallback` stays true) |
| `pipeline.conversation_preset` | `"custom"` |
| `pipeline.turn_handling` | the turn-detector JSON in §4.4: `min_delay` 0.3, `max_delay` 1.5, adaptive interruption, `preemptive_tts: true` |

- **Why this combination:**
  - Nova-3 has the fastest finals among the mainstream vendors (Pipecat: 247 ms median time from end of speech to final).
  - The hosted `v1` turn detector has the best cutoff-versus-latency trade-off in LiveKit's own eot-bench (543 ms at 5% false cutoffs, against Flux's 1,151 ms). That is a vendor benchmark.
  - Gemma 4 31B is LiveKit's "latency-optimized" default: 192 ms TTFT per the vendor, 350 to 500 ms floor per a community measurement, and 96.6% tool-call pass rate on Pipecat.
  - Inworld TTS-2 Flash has the lowest independent TTFA (Coval: 72 to 75 ms P50) and ranks #9 on the Artificial Analysis TTS quality leaderboard.
- **Expected latency:** 1.2 to 1.8 s mouth-to-ear once Silero `min_silence_duration` can be lowered to 0.3 (F4), or 1.5 to 2.1 s until then. This is about 1.5 s below what we measured on a similar Inference path (2.5 to 4.5 s). Most of that assumed saving comes from two swaps that M6 must confirm: Gemma TTFT against OpenRouter (about 1 s) and TTS-2 Flash against TTS-2 (about 0.5 s).
- **Expected cost:** STT $0.0048 + LLM ≈ $0.008 + TTS ≈ $0.011 ≈ **$0.024/min**.
- **Runs on:** Cloud only.
- **Caveats:**
  - It cannot be measured until the Build plan's Inference credits reset, or the project moves to Ship.
  - Once F5 is fixed, try Inference `deepgram/flux-general-en` with STT turn-taking as a variant.

### Stack B: fastest with direct keys, identical on Cloud and on the self-hosted DGX

| Slot | LKAP value |
|---|---|
| `pipeline.stt` | `{"provider_id": "deepgram-flux-stt", "model": "flux-general-en", "fields": {"eot_threshold": 0.75, "eager_eot_threshold": 0.4, "eot_timeout_ms": 3000}}` |
| `pipeline.llm` | `{"provider_id": "openrouter-llm", "model": "anthropic/claude-haiku-4.5", "fields": {"temperature": 0.4, "provider": "{\"order\": [\"anthropic\"], \"allow_fallbacks\": true, \"require_parameters\": true}", "fallback_models": "[\"openai/gpt-4.1-mini\"]"}}`. Check the exact slug in the OpenRouter catalog. The fallback must accept the same parameters: a Luna fallback would get `temperature` and hit the 404 that V6-31 fixed, because LKAP filters parameters by the primary model. |
| `pipeline.tts` | `{"provider_id": "deepgram-tts", "model": "aura-2-andromeda-en"}` |
| `pipeline.vad` / `turn_detection` | unset. The Flux entry has `end_of_turn`, so the worker runs `turn_detection="stt"` (F1). |
| `pipeline.conversation_preset` | `"custom"` |
| `pipeline.turn_handling` | the Flux JSON in §4.4: `min_delay` 0.1, `preemptive_tts: true`, with `interruption.mode` left unset, so the same config works on Cloud and the DGX |

- **Why this combination:**
  - Flux's end-of-turn (vendor: about 260 ms) with eager EOT feeds preemptive LLM and TTS (F3). Telnyx measured about 150 ms saved at the median and about 350 ms at the 95th percentile in production.
  - Claude Haiku 4.5 is the fastest *reliable* tool caller among the independent numbers: Artificial Analysis 0.62 s TTFT (direct from Anthropic) and Pipecat 637 ms P50 with 98.0% tool pass.
  - Aura-2 is already paid for, and its measured TTFB of 0.33 to 0.38 s is our best.
  - Only the Deepgram and OpenRouter keys are needed, and all plugins are in the `slim` image.
- **Speed challenger (measure it):** `openrouter-llm` with `openai/gpt-oss-120b`, `reasoning_effort: "low"`, `provider: {"order": ["groq", "cerebras"], "allow_fallbacks": false}`.
  - Pipecat: 98 ms P50 on Groq.
  - Tool pass rate only 86.3%, and no parallel tool calls on Groq.
  - Our 3.4 s measurement for it used OpenRouter's default routing, which is weighted by price, not speed.
- **Expected latency:**
  - About 1.4 to 2.0 s on the DGX with the worker running on the DGX (dgx-worker-deploy brief).
  - About 1.6 to 2.3 s on Cloud with the worker on the Mac.
  - The lower end needs OpenRouter overhead to stay small (see M3 vs M4 in §7).
- **Expected cost:** Flux $0.0077 + Haiku 4.5 ≈ $0.021 (about $1/$5 per 1M tokens, ×1.6) + Aura-2 ≈ $0.022 ≈ **$0.05/min**.
- **Upgrades that need new keys or the `full` image (not today):**
  - `inworld-tts` with `inworld-tts-2-flash`: Coval 72 ms, $10 to $15 per 1M characters.
  - `anthropic-llm` direct: removes the OpenRouter hop.
  - `groq-llm` / `cerebras-llm` direct.

### Stack C: cheapest with good latency (runs today on existing keys, Cloud and DGX)

This is Stack B's STT, TTS and turn handling with the current cheap LLM.

| Slot | LKAP value |
|---|---|
| `pipeline.stt` | same as B (Flux, `eot_threshold` 0.75, `eager_eot_threshold` 0.4, `eot_timeout_ms` 3000) |
| `pipeline.llm` | `{"provider_id": "openrouter-llm", "model": "openai/gpt-6-luna", "fields": {"reasoning_effort": "none"}}`. OpenAI is Luna's only upstream, so `provider.sort` changes nothing. |
| `pipeline.tts` | `{"provider_id": "deepgram-tts", "model": "aura-2-andromeda-en"}` |
| `pipeline.conversation_preset` / `turn_handling` | `"custom"`, Flux JSON (§4.4) |

- **Expected latency:** about 1.8 to 2.6 s. That is today's 3.0 to 3.4 s on Cloud, minus about 0.25 to 0.4 s from the `min_delay` fix, minus about 0.15 to 0.35 s from eager EOT, minus about 0.1 to 0.3 s from preemptive TTS. Worker co-location on the DGX gives more.
- **Expected cost:**
  - Flux $0.0077 + Luna ≈ $0.002 (at $0.05/$0.25 per 1M, OpenRouter's page now shows $0.10/$0.50, which would make it about $0.004) + Aura-2 ≈ $0.022 ≈ **$0.032/min**.
  - Aura-2 is two-thirds of that. Swapping in `inworld-tts` / `inworld-tts-2-flash` (new key, `full` image) brings it to about **$0.02/min** and is also faster.
- **Risk:** GPT-6 Luna has no independent tool-call score yet. Its predecessor, GPT-5.6 Luna at `none`, had an 88.3% pass rate on Pipecat's voice-readiness benchmark, with a P95 TTFT of 2.3 s. M2 must count wrong tool calls.

### Top findings

1. **Flux agents lose 0.25 to 0.5 s to `min_delay`.** The fix is config only (F1).
2. **Eager EOT plus preemptive TTS works end to end in 1.8.3 (F3), but no agent uses it.**
3. **The LLM hop through OpenRouter is the biggest stage.** Pick hosts with `provider.order` / `sort: "latency"`. The default routing is weighted by price.
4. **The turn-detector path has a hidden 0.55 s Silero floor, and LKAP's presets override LiveKit's tighter defaults (F4).** Inference Flux does not get STT turn-taking in LKAP (F5).
5. **Inworld TTS-2 Flash is the fastest independently measured TTS.** Our Inworld TTS-2 numbers (0.66 to 0.83 s) are 4× the independent P50, so pipeline overhead (region, hops, first-sentence buffering) matters as much as the model.

## 2. Latency budget and what we measured

| Stage | Our measurement | Best independent figure (source) | Lever |
|---|---|---|---|
| End-of-turn decision | not isolated, includes `min_delay` 0.5 on `balanced` | Flux about 260 ms median (vendor). LiveKit v1 detector about 295 to 543 ms at 10%/5% false cutoffs (LiveKit eot-bench, vendor) | F1, F3, F4 |
| Final transcript after end of speech | n/a | Nova-3 247 ms, Soniox 249 to 260 ms, AssemblyAI Universal-Streaming 256 ms (Pipecat stt-benchmark) | STT choice |
| LLM TTFT | OpenRouter 1.0 to 2.0 s (Luna `none`, 4.1-mini) | gpt-oss-120b/Groq 98 ms, Gemma 4 31B 489 ms, Haiku 4.5 637 ms, GPT-5.6 Luna `none` 671 ms (Pipecat Voice Readiness, P50), GPT-6 Luna 0.78 s direct (AA, 10k input) | host, routing, effort |
| TTS TTFA | Aura-2 0.33 to 0.38 s, Inworld TTS-2 0.66 to 0.83 s (Inference) | Inworld Flash 72 ms, Inworld TTS-2 170 ms, ElevenLabs Flash 182 ms, Rime Mist v3 256 ms, Aura-2 290 ms, Sonic 3.6 440 ms (Coval, perceived, P50) | vendor, co-location |
| Network / worker placement | Tailscale relay about 1.2 s (DGX path) | n/a | §6 |
| End to end | Cloud 3.0 to 3.4 s, DGX 2.4 to 2.9 s, best single run 1.9 s | Industry P50 1.4 to 1.7 s over 4M+ production calls (Hamming, secondary source), Daily targets <1.5 s voice-to-voice with about 700 ms LLM TTFT | all of the above |

## 3. Per-component comparison

Legend: **V** = vendor claim, **I** = independent measurement, **L** = LiveKit (vendor of the framework). LKAP support is read from `contracts/generated/providers.json` and `agent/requirements/{slim,full}.txt`.

### 3.1 LLM (time to first token, with tool calling)

Artificial Analysis (AA) measures TTFT with a **10k-token input** and counts thinking time, so our 1.5 to 3k-token prompts should come in a little lower. All AA pages were fetched 2026-09-30.

| Model / host | TTFT | Source | Tool-call quality | Price per 1M in/out | LKAP support |
|---|---|---|---|---|---|
| gpt-oss-120b / Groq | 98 ms P50, 217 ms P95 | I: Pipecat Voice Readiness (2026-08-18) | 86.3% pass, **no parallel tool calls on Groq** (Groq docs) | Groq $0.15/$0.60 | `groq-llm` (full image, new key) or `openrouter-llm` with `provider.order: ["groq"]` |
| gpt-oss-120b (low) / Baseten, Crusoe, Cerebras | 0.20 / 0.38 / 0.44 s | I: AA | AA: function calling yes | Baseten $0.10/$0.50 | `baseten-llm`, `cerebras-llm` (full), OpenRouter `order` |
| Gemma 4 31B / LiveKit Inference | 192 ms TTFT, 354 ms to first sentence | V/L: LiveKit blog (dated "07.02.2026") | serving-layer fix for tool calls written as text | $0.40/$1.20 (Inference) | `livekit-inference-llm` (Cloud) |
| Gemma 4 31B on the Inference gateway | 350 to 400 ms floor, about 500 ms on a fresh 1K prompt | I: community.livekit.io thread 1704 (2026-07-23) | n/a | n/a | n/a |
| Gemma 4 31B (thinking off) / Baseten, Modular | 489 ms P50 (Pipecat), 0.42 s (AA) | I | 96.6% pass | n/a | `baseten-llm` (full, model id free-form) |
| Gemma 4 31B / OpenRouter | 1,876 ms | L: LiveKit blog | n/a | n/a | `openrouter-llm` (routing matters) |
| Claude Haiku 4.5 / Anthropic | 0.62 s (AA), 637 ms P50, 1,615 ms P95 (Pipecat) | I | 98.0% pass | about $1/$5 | `openrouter-llm` (`anthropic/claude-haiku-4.5`), or `anthropic-llm` (key, registry lists only 3.5 Haiku, id is free-form) |
| Qwen3.8-27B FP8 / Baseten | 649 ms P50, 801 ms P95 | I: Pipecat | 98.2% pass | n/a | `baseten-llm` (full) |
| GPT-6 Luna (non-reasoning) / OpenAI | 0.78 s | I: AA (released 2026-09-22, OpenAI is the only host) | not in Pipecat | OpenRouter page $0.10/$0.50 (our brief says $0.05/$0.25) | `openrouter-llm` (current), `openai-llm` with an OpenAI key |
| GPT-5.6 Luna (`none`) | 671 ms P50, 2,304 ms P95 | I: Pipecat | 88.3% pass | n/a | Inference `openai/gpt-5.6-luna` |
| GPT-4.1 mini / nano / OpenAI | 0.74 / 0.71 s (AA), 4.1-mini 851 ms P50 (Pipecat) | I | 4.1-mini 85.3% pass, AA marks both deprecated | $0.40/$1.60, $0.10/$0.40 | all three providers |
| Gemini 2.5 Flash (thinking off) | 550 ms P50 | I: Pipecat | 89.9% | n/a | `google-llm` |
| Gemini 3.5 Flash-Lite (minimal) | 591 ms P50 (Pipecat), AA 9.3 s (probably counts thinking) | I | **68.6% pass** | n/a | avoid for tool-heavy agents |
| Llama 3.3 70B / Groq | 0.86 s | I: AA | n/a | n/a | `groq-llm` default |

**OpenRouter specifics** (https://openrouter.ai/docs/features/provider-routing and https://openrouter.ai/docs/guides/best-practices/latency-and-performance, fetched 2026-09-30):

- **Default routing is weighted by inverse-square of price** among stable providers. For multi-host models such as gpt-oss-120b or Gemma, the default therefore favours cheap and often slow hosts.
- **`sort: "latency"`** (or `{by, partition}`) turns off load balancing and tries providers fastest-first. `order` pins a list. `allow_fallbacks` defaults to true. `require_parameters` defaults to false upstream, but LKAP forces true.
- **`preferred_max_latency` and `preferred_min_throughput`** are soft preferences.
- **Shortcuts:** `:nitro` sorts by throughput, not latency. `:floor` sorts by price.
- **Overhead:** the current page gives no millisecond figure. An older copy said about 40 ms, and third parties report 50 to 200 ms. Neither is verified. Cold edge caches (the first 1 to 2 minutes in a region) and low credit balances add latency.
- **Sticky routing and caching:** a stable `session_id` keeps the provider's KV cache warm ("up to 80 to 90%" lower latency on multi-turn, vendor claim). Prompt caching for OpenAI (from 1,024 tokens) and Gemini is automatic. Sticky provider routing expires after 10 minutes idle.
- **What LKAP sends:** livekit-plugins-openai 1.8.3 `with_openrouter` accepts `user` and `prompt_cache_key` (`SDK/plugins/openai/llm.py:~452-501`). LKAP sets neither. This is a follow-up.

**Does prompt size matter?** Not much for TTFT. OpenAI: "Cutting 50% of your prompt may only result in a 1 to 5% latency improvement". `max_tokens` shortens generation but not TTFT (https://developers.openai.com/api/docs/guides/latency-optimization). Prefix caching matters for long prefixes. A repeated 9K prefix went from 864 ms to 67 ms on self-hosted vLLM (LiveKit community). Host choice and reasoning `none`/`minimal` dominate.

### 3.2 STT (streaming, end-of-turn)

| Vendor / model | Protocol | End-of-turn / final latency | Source | Key params (defaults) | Price/min | LKAP support |
|---|---|---|---|---|---|---|
| Deepgram Flux `flux-general-en` / `-multi` | WS `/v2/listen` | EOT about 260 ms median (V). Eager mode saves "hundreds of ms" (V), Telnyx production about −150 ms median, −350 ms P95 (2026-01-14) | V + third-party production | `eot_threshold` 0.5 to 0.9 (0.7), `eager_eot_threshold` 0.3 to 0.9 (off, gateway default 0.5), `eot_timeout_ms` (API 5000, plugin doc 3000). Deepgram low-latency preset: eager 0.4 / eot 0.7. Eager costs "50 to 70% more LLM calls" | $0.0077 PAYG ($0.0065 promo), Inference $0.0065 | `deepgram-flux-stt` (slim ✓, all 3 fields), Inference model ✓ but no STT turn-taking (F5) |
| Deepgram Nova-3 | WS `/v1/listen` | 247 ms median / 298 P95 time to final after VAD stop | I: Pipecat stt-benchmark | plugin `endpointing` 25 ms, `no_delay` true, interim on | $0.0077 ($0.0048 promo), Inference $0.0048 | `deepgram-stt`, Inference ✓ |
| AssemblyAI Universal-Streaming (en) | WS | 256 / 362 / 417 ms | I: Pipecat | `end_of_turn_confidence_threshold` 0.4, `max_turn_silence` 1280 ms | $0.15/hr = $0.0025, **billed per session duration** | `assemblyai-stt` (full), Inference ✓ |
| AssemblyAI Universal-3.6 Pro realtime | WS | 307 ms P50 (V), Pipecat 307/401/498 ms | V + I | `min_turn_silence` 100, LiveKit example `max_turn_silence` 1000, `vad_threshold` 0.3, **LiveKit: "set `endpointing.min_delay` to 0"** | $0.45/hr = $0.0075 | `assemblyai-stt` (full), Inference ✓ |
| Cartesia Ink-2 | WS | 0.1 s time-to-final (V), AA 0.21 s semantic endpoints, Pipecat 299/328/**1,584** ms (long tail) | V + I | turn events incl. `turn.eager_end`, thresholds via Inference `extra_kwargs` (eager 0.3 to 0.6, default 0.4) | Inference $0.003 (Ink-Whisper), Ink-2 price not found | `cartesia-stt` (slim ✓), Inference ✓ |
| Soniox `stt-rt-v5` | WS | 260 / 305 / 313 ms | I: Pipecat | endpoint detection off by default, voice preset level 2, sensitivity 0.3, max 1500 ms | $0.12/hr = $0.002 | `soniox-stt` (full) |
| Speechmatics `linden-1` | WS | 369 / 438 / 690 ms | I: Pipecat | `end_of_utterance_silence_trigger` 0.5 to 0.8 s recommended | price unclear | `speechmatics-stt` (full), Inference ✓ |
| Gladia Solaria-1 | WS | about 270 ms final (secondary, unverified) | n/a | `endpointing` 0.05 s | $0.75/hr Starter | `gladia-stt` |
| Google Gemini 3.5 Transcribe Live | WS | 458 / 532 / 599 ms | I: Pipecat | 10-minute session cap | about $0.0095 | Inference ✓ |
| NVIDIA Nemotron 3.0 ASR | n/a | 221 / 238 / 252 ms (fastest in the benchmark) | I: Pipecat | n/a | n/a | `nvidia-stt` (NIM, a DGX self-host candidate, not evaluated) |

**How to read the benchmarks.**

- **Pipecat** (https://github.com/pipecat-ai/stt-benchmark, fetched 2026-09-30) measures time from VAD end-of-speech to final transcript on 1,000 smart-turn samples. It tests how fast a finalisation arrives, not how good native turn detection is, and it excludes Flux.
- **LiveKit eot-bench** (https://github.com/livekit/eot-bench) is LiveKit's own and measures turn *quality*. The latency needed to keep false cutoffs at 5% / 10%:
  - LiveKit v1: 543 / 295 ms
  - Flux: 1,151 / 548 ms
  - AssemblyAI: 1,049 / 713 ms
  - VAD only: 1,600 / 1,000 ms
- **Cartesia** claims turn-detection F1 of 93% for Ink-2, against 88% for Flux (vendor).
- **Coval** (2026-06-04) measures time to first *partial*, with Nova-3 fastest.

**Which component should decide the turn?**

- **On Cloud with Inference:** the hosted `v1` detector with Nova-3 (Stack A). By LiveKit's benchmark it cuts off callers less at equal latency. Tune `max_delay` and the Silero silence (F4).
- **On the DGX or with direct keys:** Flux with `turn_detection="stt"` (Stacks B/C), with `min_delay` near 0 as LiveKit advises for AssemblyAI, and eager EOT on. The DGX has no hosted `v1`, and the local `v1-mini` is the weaker model. Measure Nova-3 + `v1-mini` on the DGX as a control (M7).
- **In every mode, VAD still handles barge-in.**

### 3.3 TTS (time to first audio, streaming)

Coval's "perceived TTFA" is network round trip plus leading silence, measured since 2026-06. Sources: https://www.coval.ai/blog/time-to-first-audio-ttfa-benchmark/ (2026-09-08), and the same data with interquartile ranges reproduced by the vendor Gradium at https://gradium.ai/content/tts-latency-benchmark-2026. Runner region and connection reuse are not stated. Quality is Artificial Analysis Speech Arena Elo (https://artificialanalysis.ai/text-to-speech/leaderboard/provider-voice, fetched 2026-09-30). **AA publishes no TTS latency.**

| Model | TTFA | Source | Quality (AA Elo, rank) | Protocol | Price per 1M chars | LKAP support |
|---|---|---|---|---|---|---|
| **Inworld TTS-2 Flash** | 72 to 75 ms P50, IQR 17 (I: Coval), 20 ms P90 server-side (V) | I + V | 1210, #9 | WS / streaming REST | $15 (Inference $15, $9 on Scale, Inworld Creator $10) | Inference: id valid, not suggested. `inworld-tts` lists `inworld-tts-2-flash` (**full image**, Inworld key) |
| Inworld TTS-2 | 170 to 176 ms (I), ours via Inference 0.66 to 0.83 s | I + ours | 1247, #5 | WS | $25 | Inference default, `inworld-tts` (full) |
| ElevenLabs Flash v2.5 | 182 to 202 ms (I), about 75 ms model-side (V) | I + V | 1076, #45 | WS (multi-context) | $40 to $50 | `elevenlabs-tts` (slim ✓, key), **not on Inference** |
| Rime Mist v3 | 256 ms P50, IQR 21 (very steady) (I) | I | not listed (Mist v2 909) | WS `/ws3`, US-East/West endpoints | $30 (Inference, **free promo ends 2026-10-01**) | Inference ✓, `rime-tts` needs `use_websocket: true` (full) |
| Deepgram Aura-2 | 290 ms P50, IQR 231 (P25 186 / P75 417) (I), ours 0.33 to 0.38 s direct, "sub-200 ms" (V) | I + ours | not listed | WS (linear16) | $30 | `deepgram-tts` (slim ✓), Inference ✓ |
| Deepgram Flux TTS (launched 2026-08-13) | "as little as 80 ms" (V only) | V | not listed | WS `/v2/speak`, knows what was spoken on interrupt | $45 | Inference `deepgram/flux-tts` ✓, plugin `deepgram.TTSv2` exists, but LKAP has no direct entry |
| Gradium | 214 ms (I) | I | 1149, #21 | WS | about $47 | Inference ✓, `gradium-tts` |
| Cartesia Sonic 3.5 / 3.6 | 270 / 437 to 440 ms (I), "sub-90 ms" (V) | I + V | 3.6: 1275, #2 | WS with continuations | $49 to $50 | `cartesia-tts` (slim ✓), Inference ✓. **Sonic Turbo / Sonic 2 stop working after 2026-10-20** |
| Fish S2.1 Pro | 283 to 286 ms (I) | I | 1139, #22 | WS | $15 | Inference ✓, `fishaudio-tts` (full) |
| xAI TTS | 354 ms (Voice Arena, secondary) | secondary | n/a | WS | $15 | Inference ✓, `xai-tts` (full) |
| Speechify Simba 3.2 | 123 ms (Voice Arena, secondary) | secondary | 1241, #6 | n/a | about $6.6 | `speechify-tts` (full), not evaluated |
| OpenAI gpt-4o-mini-tts | 812 ms (secondary) | secondary | n/a | HTTP, **not streaming in LKAP** | n/a | `openai-tts` (avoid for live calls) |

**Protocol and region notes.**

- **Deepgram:** the latency docs show a TLS handshake of about 339 ms before a 277 ms first byte, so a reused websocket matters. The SDK prewarms it (F6). Deepgram serves from the US. An EU endpoint exists.
- **ElevenLabs:** `api.us.elevenlabs.io` pins US servers. Regional TTFB is 100 to 150 ms in North America and the EU.
- **Rime:** choosing the right US coast saves 25 to 60 ms.
- **Cartesia:** websockets amortise connection setup. Regional endpoints are Enterprise-only.

**Our Inworld TTS-2 result is 4× Coval's P50.** That points at the hop from the Mac worker to the gateway and on to Inworld, and at waiting for the first sentence, not at the model.

## 4. Turn handling in LiveKit Agents 1.8.3, what actually decides latency (verified in source)

This section is the part of the budget LKAP controls with config alone, and it is where the biggest cheap win is.

### 4.1 How the turn ends, per path

| Path | Who ends the turn | What LKAP does today | Where the time goes |
|---|---|---|---|
| **STT-native** (Deepgram Flux via `deepgram-flux-stt`) | Flux `EndOfTurn` → `END_OF_SPEECH` → `_run_eou_detection(trigger="stt")` | `session_builder.stt_decides_turns()` → `turn_detection="stt"` when the STT entry has `capabilities.end_of_turn` and no `turn_detection` slot is set | Flux EOT latency (vendor: median ~260 ms after speech end) **plus** `endpointing.min_delay − (now − last_word_end)` (finding F1) |
| **Turn detector** (Nova-3 or any STT, hosted `v1` on Cloud, local `v1-mini` on DGX) | Silero VAD end-of-speech → turn-detector prediction → `min_delay` (or `max_delay` if the model says "unlikely") | `prepare_resolved()` synthesizes an `inference-turn-detector` slot, `v1-mini` on a `local` connection | Silero `min_silence_duration` (0.55 s default, F4) → detector inference (hosted: network round trip, "commits after ~1 s if no prediction") → `min_delay`/`max_delay` |
| **LiveKit Inference Flux** (`livekit-inference-stt` + `deepgram/flux-general-en`) | Today: the turn detector path above, **not** Flux (F5) | provider-level `end_of_turn: false` | Same as the turn-detector row, so Flux's own EOT is wasted |

### 4.2 Verified source findings (F1 to F10)

- **F1. Flux's end-of-turn still pays `endpointing.min_delay`.** In `turn_detection="stt"` mode, `SDK/agents/voice/audio_recognition.py:1319-1360` sets `_last_speaking_time` to Flux's last-word end time (`speech_end_time`, or the word timestamps via `stt_last_speaking_time`, else *now*) and calls `_run_eou_detection`, whose `_bounce_eou_task` (`:1531`, `:1716-1722`) sleeps `min_delay − (now − last_speaking_time)`. With LKAP's `balanced` preset (0.5 s) and Flux EOT arriving ~0.26 s after the last word, a Flux agent waits another ~0.25 s after Flux has already decided. If the word timestamps are missing the full 0.5 s is added. `max_delay` is **dead in stt mode**: it is used only when a turn-detector model returns "unlikely" (`:1603-1608`), and stt mode runs none. Also, with a plain `"stt"` turn detection the SDK's default is the legacy 0.5/3.0, not the 0.3/2.5 streaming defaults (`SDK/agents/voice/turn.py:135-146, 300-315`). → **Flux agents should run `conversation_preset: "custom"` with `endpointing.min_delay` 0.0 to 0.15.** VAD still handles barge-in, and a false EOT is corrected by VAD interruption (`audio_recognition.py:1323-1343` flushes VAD so an immediate SOS interrupts).
- **F2. Dynamic endpointing is not a speed lever in 1.8.3.** `DynamicEndpointing` (`SDK/agents/voice/endpointing.py:49-120`) learns `min_delay` from the caller's pauses with `ExpFilter(min_val=min_delay, max_val=max_delay)`. It can only *raise* the delay above the configured floor. It protects slow talkers. It does not make anything faster than `fixed` at the same `min_delay`. (LiveKit staff did recommend `dynamic` 0.25/0.8/α 0.9 to one user whose short utterances kept hitting a 1.0 s `max_delay`: https://community.livekit.io/t/high-response-latency-after-short-user-utterances-despite-preemptive-generation-and-aggressive-endpointing-agents-1-6-4/1658, posted 2026-07-17, fetched 2026-09-30. The saving there came from the lower `max_delay`, which is the lever that matters on the turn-detector path.)
- **F3. Flux eager EOT feeds preemptive generation end to end.** `SDK/plugins/deepgram/stt_v2.py:698-705` maps `EagerEndOfTurn` → `PREFLIGHT_TRANSCRIPT`. `audio_recognition.py:1270-1310` calls `on_preemptive_generation` in every non-manual mode, including `"stt"`. `agent_activity.py:2567-2620` starts the LLM with `schedule_speech=False`. With `preemptive_tts: true` synthesis also starts before the turn is confirmed (`agent_activity.py:~3600-3612`). `TurnResumed` sends an interim transcript that cancels the attempt. At `EndOfTurn` the final transcript is compared with the preflight one (`:1238`, `transcript_changed`) and the speculative reply is kept when unchanged. So with eager EOT, the LLM TTFT and TTS TTFA overlap the gap between `EagerEndOfTurn` and `EndOfTurn`. The plugin enforces `eager_eot_threshold ≤ eot_threshold` (`stt_v2.py:139-146`, `eot_threshold` default 0.7, max 0.9). LKAP's `deepgram-flux-stt` entry already exposes all three fields. Caveat: knowledge auto-inject forces preemptive generation **off** unless the config sets `preemptive_generation.enabled` explicitly (`agent/src/lkap_agent/session_builder.py:552-555, 640-653`), and a turn with a knowledge hit discards the speculative reply anyway.
- **F4. On the turn-detector path, Silero's 0.55 s silence is a hidden floor.** LKAP prewarms `silero.VAD.load()` with defaults (`agent/src/lkap_agent/main.py:2064`), i.e. `min_silence_duration=0.55` (`SDK/plugins/silero/vad.py:64`). The turn detector is only evaluated after VAD end-of-speech (`audio_recognition.py:1429-1450`), so no turn can close in under ~0.55 s + detector inference, whatever `min_delay` says (`min_delay` is anchored at the real speech end, `speech_end_time = now − silence_duration − inference_duration`, so it overlaps the silence rather than adding to it). The SDK accepts down to 0.25 s with the audio turn detector (`_check_vad_silence_requirement`, `MIN_SILENCE_DURATION_MS=200` + 50 ms, `SDK/agents/inference/eot/base.py:32`). LiveKit's turn-detector page recommends `min_delay` 0.3 / `max_delay` 2.5 with the audio detector (https://docs.livekit.io/agents/logic/turns/turn-detector/, page dated 2026-09-30). The SDK applies those streaming defaults only when the caller sets nothing, but every LKAP named preset pins its own values (`balanced` 0.5/3.0). LKAP's `silero-vad` entry exposes only `min_speech_duration` → **follow-up: expose `min_silence_duration`**.
- **F5. LiveKit Inference Flux does not get STT-native turns in LKAP.** `livekit-inference-stt` has one provider-wide `capabilities.end_of_turn: false`, so an agent on `deepgram/flux-general-en` through Inference runs the hosted turn detector + VAD. LiveKit documents `turn_detection="stt"` with Inference Flux (https://docs.livekit.io/agents/models/stt/deepgram/, dated 2026-09-30). The SDK's `DeepgramFluxOptions` (`SDK/agents/inference/stt.py:117-126`) notes the gateway default `eager_eot_threshold` 0.5, but LKAP exposes no `extra_kwargs` on that entry. → **follow-up: per-model `end_of_turn` for Inference STT (Flux models, Cartesia `ink-2`, AssemblyAI) and Flux options (`eot_threshold`, `eager_eot_threshold`, `eot_timeout_ms`) passed as `extra_kwargs`.**
- **F6. Connection warm-up is already done by the SDK.** `AgentSession.__init__` calls `llm.prewarm()` (`SDK/agents/voice/agent_session.py:636-639`). `AgentActivity` prewarms STT, LLM and TTS on start (`agent_activity.py:891-931, 1017-1023`). Implementations exist for the openai plugin (covers OpenRouter and OpenAI direct, `plugins/openai/llm.py:178`), Inference LLM/TTS, Deepgram TTS v1/v2, Cartesia TTS. The worker also prewarms Silero per process. Nothing needs building. What the SDK cannot fix is a cold *process* (keep `num_idle_processes` ≥ 1, which is the SDK default).
- **F7. Plugin availability limits the direct-key stacks.** Mac dev venv: cartesia, deepgram, elevenlabs, google, openai, silero (+ bey/simli/tavus). `agent/requirements/slim.txt` (the image the DGX brief builds) adds only `turn-detector`. Groq, Cerebras, Inworld, Rime, AssemblyAI, Soniox, Speechmatics, xAI and Baseten need the **`full`** image (`agent/requirements/full.txt`). `rime-tts` streams only with `use_websocket: true` (registry `streaming=False` otherwise).
- **F8. OpenRouter routing knobs are already in the registry.** `openrouter-llm` has a `provider` JSON field (`order`, `only`, `ignore`, `sort`, `allow_fallbacks`, `require_parameters`, `preferred_max_latency`, …) with `require_parameters` defaulted to `true` by the worker (`agent/src/lkap_agent/providers/factory.py:550-570`), `fallback_models`, and `reasoning_effort` (V6-31 sends the lowest effort when empty). `ProviderRef.fields` values are scalars, so `provider` is stored as a JSON string.
- **F9. Plans.** Build $0 (1,000 agent-session minutes, $2.50 Inference credit, **hard cap**, 5 concurrent). Ship $50/month (5,000 minutes, $5 credit, then list prices, 20 concurrent). Scale $500 (discounted STT/TTS). The hosted `v1` turn detector is "available at no cost to agents deployed to LiveKit Cloud". Billing for a self-hosted worker using Cloud credentials (our Mac worker) is not documented. Sources: https://livekit.com/pricing, https://docs.livekit.io/deploy/admin/billing/, https://docs.livekit.io/agents/logic/turns/turn-detector/ (all fetched 2026-09-30). Prices per model in `docs/research-v6/livekit-inference.md` §2.
- **F10. Per-stage metrics exist in 1.8.3** (`SDK/agents/metrics/base.py`): EOU `end_of_utterance_delay`, `transcription_delay`, `on_user_turn_completed_delay`. LLM `ttft`. TTS `ttfb`. The measurement plan logs them per turn. One LiveKit community case with a "missing" 2 s turned out to be client-side playout buffering, not the agent (https://community.livekit.io/t/high-end-to-end-latency-asr-llm-tts-total-1s-but-speech-end-to-reply-takes-3-4s-livekit-agents/1277, resolved 2026-09-08, fetched 2026-09-30). Our scripted caller measures the first voiced frame received, which excludes a browser's playout buffer but includes WebRTC jitter buffering.

### 4.3 What LiveKit recommends (docs dated 2026-09-30)

- Turn detector model on top of VAD is the recommended default. STT endpointing (AssemblyAI, Deepgram Flux) is the alternative, "the bundled VAD continues handling interruptions while STT determines turn boundaries". VAD-only "when you need minimal latency" (https://docs.livekit.io/agents/logic/turns/).
- With the audio turn detector: `min_delay` 0.3, `max_delay` 2.5. VAD `min_silence_duration` ≥ 0.25 s (https://docs.livekit.io/agents/logic/turns/turn-detector/).
- Tuning page: defaults 0.5/3.0. Lower `min_delay` for faster replies. Preemptive generation on by default. `preemptive_tts` "cuts more latency at the cost of wasted compute on cancellations". `max_speech_duration` 10 s, `max_retries` 3. Interruption `mode: "adaptive"`, `min_duration` 0.5, `false_interruption_timeout` 2.0, `resume_false_interruption` true (https://docs.livekit.io/agents/logic/turns/tuning/).
- STT turn-taking: the tuning page says `min_delay` "in STT mode … adds to the provider's endpoint signal" (consistent with F1). The AssemblyAI plugin page says "Set `endpointing.min_delay` to `0` … to avoid extra latency". The Deepgram Flux page gives no `min_delay` advice and says only "set `turn_detection="stt"` … the session's bundled VAD continues to handle interruption detection" (https://docs.livekit.io/agents/models/stt/deepgram/, https://docs.livekit.io/agents/models/stt/assemblyai/, fetched 2026-09-30).
- Hosted `v1` vs local `v1-mini`: `v1` is served on Inference in every region, "if the model doesn't return a prediction within about a second, the agent commits the turn". `v1-mini` runs on the worker CPU. Default is `v1` on Cloud or dev mode, `v1-mini` elsewhere (`SDK/agents/inference/eot/detector.py:56-63`). The older text `MultilingualModel` is deprecated (~50 to 160 ms per turn, 396 MB).

### 4.4 Recommended `turn_handling` values

Stored as `pipeline.conversation_preset: "custom"` + `pipeline.turn_handling` (the api refuses `turn_detection` there, LKAP picks it).

**Flux (STT decides the turn)** for Stacks B and C:
```json
{
  "endpointing": {"mode": "fixed", "min_delay": 0.1},
  "interruption": {"min_duration": 0.4, "min_words": 0,
                    "false_interruption_timeout": 1.5, "resume_false_interruption": true},
  "preemptive_generation": {"enabled": true, "preemptive_tts": true,
                             "max_speech_duration": 10.0, "max_retries": 3}
}
```
`max_delay` is omitted on purpose (dead in stt mode, F1). If callers get cut off, raise Flux's `eot_threshold` first (0.75 → 0.8), not `min_delay`. `interruption.mode` is left out on purpose. When it is absent, the SDK picks the best strategy available (`turn.py` `InterruptionOptions`), which is adaptive where the hosted model exists and VAD otherwise. So one config serves Cloud and the DGX. M0 checks which mode the SDK actually chose.

**Turn detector (Nova-3 or Inference Flux today)** for Stack A:
```json
{
  "endpointing": {"mode": "fixed", "min_delay": 0.3, "max_delay": 1.5},
  "interruption": {"mode": "adaptive", "min_duration": 0.4, "false_interruption_timeout": 1.5,
                    "resume_false_interruption": true},
  "preemptive_generation": {"enabled": true, "preemptive_tts": true}
}
```
plus Silero `min_silence_duration` 0.3 (**needs the F4 follow-up**, until then the effective floor is ~0.55 s + detector). `max_delay` 1.5 rather than 2.5 because short answers ("yes", a policy number) are what the turn detector most often scores "unlikely".

**Realistic floor.** With streaming everything, the budget is roughly: EOT decision 0.25 to 0.4 s (Flux vendor median 0.26 s) + LLM TTFT 0.2 to 0.5 s (fast host) + TTS TTFA 0.1 to 0.35 s + WebRTC/network/jitter 0.1 to 0.2 s ≈ **0.7 to 1.4 s mouth-to-ear**, less when eager EOT + `preemptive_tts` overlap the LLM and TTS with the EOT wait. Our best observed run is 1.9 s (Receptionist, Cloud). A sustained 1.2 to 1.6 s median is a realistic target for LKAP with OpenRouter-class LLM latency, and ~1.0 s needs a sub-300 ms TTFT host.

## 5. LiveKit Inference: why it felt fast, and whether Ship is worth it

**Why it felt fast.** These are the likely reasons. Only the co-location claim is documented, and it is LiveKit's own claim.

1. **Persistent websockets to one gateway.** STT and TTS each run over one websocket to `agent-gateway.livekit.cloud` (`SDK/agents/inference/stt.py`, `tts.py`), and the SDK prewarms them (F6). Direct plugins also keep sockets open, so on its own this is not an advantage.
2. **Co-location.** Agents *hosted on LiveKit Cloud* "run in the same data center as our inference service … across our private network backbone" (launch blog, see `livekit-inference.md` §3). Our worker runs on a Mac, so we get only part of this benefit. Gateway to vendor happens on LiveKit's backbone, but Mac to gateway crosses the public internet.
3. **The hosted `v1` turn detector.** It is the strongest detector in LiveKit's benchmark and costs nothing for Cloud-hosted agents. LKAP picks it explicitly, not through the SDK default. The `inference-turn-detector` entry defaults `version` to `v1`, and `prepare_resolved` changes it to `v1-mini` only on a `local` connection. So a Mac worker on a Cloud connection uses hosted `v1`. Whether that is billed for a worker not hosted on LiveKit Cloud is undocumented (F9).
4. **Gemma 4 31B served by LiveKit.** Its prefix cache and affinity are keyed on the room ID, so the fast path applies only inside LiveKit rooms (community thread 1704).

**Fastest models on Inference** (from §3):

- STT: `deepgram/nova-3`, which finalises fastest. Or `deepgram/flux-general-en` once F5 lands.
- LLM: `google/gemma-4-31b-it`.
- TTS: `inworld/inworld-tts-2-flash` (not in LKAP's suggestions), then `rime/mistv3` and `deepgram/aura-2`.
- Avoid `cartesia/sonic-3.6` when latency is the goal (Coval ~440 ms perceived).

**Build versus Ship.**

- Build is a hard cap at $2.50 a month. Stack A uses about $0.024 per minute, so the credit covers roughly 100 minutes a month.
- Ship costs $50 a month, includes $5 of credit (~200 Stack-A minutes), and then bills at list prices.
- Direct keys reach the same vendors at similar list prices. For example, Deepgram Flux is $0.0077 direct against $0.0065 on Inference. Aura-2 is $30 on both.
- The latency difference comes from where the worker sits, not from Inference itself.

**Recommendation.** Do not buy Ship for latency alone. Buy it only if you want:

- the hosted `v1` turn detector,
- Gemma on LiveKit's own serving,
- adaptive interruption, and
- one bill,

**and** you also plan to run the worker near the Cloud region (ideally Cloud agent hosting). With a worker on a Mac, the direct-key Stack B is as fast and needs no plan.

## 6. Network and topology

- **Put the worker where the media and the vendors are.** Each hop from the worker to LiveKit, STT, LLM or TTS is paid on every turn.
  - Deepgram serves from the US. There is an EU endpoint.
  - OpenRouter runs on Cloudflare's edge, and the upstream (OpenAI, Anthropic) is mostly in the US.
  - Inworld, Rime and ElevenLabs have US endpoints.
  - A worker in a US-East or US-Central cloud region, in the same region as the LiveKit Cloud project, is the textbook placement.
  - **We do not know** the LiveKit Cloud project's region, the Mac's network location, or the DGX's physical location (§8).
- **DGX case.**
  - Move the worker onto the DGX, as the `dgx-worker-deploy.md` brief describes. Today the Mac-to-DGX path goes through a Tailscale DERP relay and costs about 1.2 s per reply.
  - Also fix direct UDP (41641) so callers on the Mac do not relay.
  - All of Stack B/C's plugins are in the `slim` image.
  - The local `v1-mini` detector is baked into the image (`download-files`), so no GPU is needed.
- **Cloud case.**
  - If the Cloud project is far from the Mac, a Mac worker pays two long legs: caller to Cloud, and Cloud to worker to vendors.
  - That may explain why the DGX path (2.4 to 2.9 s) beat Cloud (3.0 to 3.4 s) even with the relay.
  - Running the worker in the Cloud region, or on LiveKit Cloud agent hosting, is the structural fix. LKAP does not support Cloud agent hosting today. This is a follow-up.
- **Warm-up.** Already handled by the SDK (F6). Keep at least one idle job process (the SDK default), so the Silero, turn-detector and plugin imports are paid before the call. Do not add a second warm-up layer.
- **Keep sockets open.** Each STT/TTS plugin keeps one websocket per session, and the LLM uses a pooled HTTP/2 client.
  - OpenRouter's edge cache is cold for the first 1 to 2 minutes in a region.
  - Low credit balances trigger extra checks.
  - **Keep the OpenRouter balance topped up.**

## 7. Measurement plan (for the scripted caller)

**Protocol.**

- Measure caller-stops-speaking to first voiced agent frame, as in V6-31.
- Run **5 sessions per configuration, 3 scripted questions per session (15 turns)**. Report the median, P90 and range. Our noise is ±0.5 s, so fewer runs cannot separate configurations less than about 0.3 s apart.
- Include one question that should trigger a tool call.
- Include one short answer (a bare "yes" or a policy number) to catch early cutoffs.
- Log the SDK metrics per turn (F10): `end_of_utterance_delay`, `transcription_delay`, LLM `ttft`, TTS `ttfb`. That tells us which stage moved.
- Count cutoffs, meaning the agent spoke while the script still had words left, and wrong tool calls.
- Discard each session's first turn as a warm-up sample, and report it separately.

| # | Deployment | Configuration | Question it answers |
|---|---|---|---|
| M0 | Cloud + DGX (worker on the DGX) | today: Flux + Luna `none` + Aura-2, current preset | baseline, also note from the worker log which interruption mode the SDK chose on each connection |
| M1 | both | M0 + `custom` with `min_delay` 0.1 (F1) | savings from the `min_delay` fix |
| M2 | both | M1 + `eager_eot_threshold` 0.4, `eot_threshold` 0.75, `preemptive_tts: true` (= Stack C) | savings from eager EOT and preemptive TTS, and any cutoffs |
| M3 | DGX | M2 with LLM Haiku 4.5 via OpenRouter, `order: ["anthropic"]` (= Stack B) | LLM TTFT through OpenRouter |
| M4 | DGX | M2 with `openai/gpt-oss-120b`, `reasoning_effort: low`, `order: ["groq","cerebras"]`, `allow_fallbacks: false` | fastest host, tool-call pass rate |
| M5 | DGX | M2 with `openai/gpt-6-luna` and a `provider` of `{"sort":"latency"}` (control, should equal M2) | confirms `sort` is a no-op on single-host models |
| M6 | Cloud, **after Inference credits reset or on Ship** | Stack A | the Inference path |
| M7 | DGX | Nova-3 (`deepgram-stt`) + local `v1-mini` + M2's LLM/TTS, `min_delay` 0.3, `max_delay` 1.5 | Flux against a local turn detector on a self-hosted server |

- Optional, and cheap: a text-only TTFT probe that streams the agent's real system prompt and tools to each OpenRouter model/provider 20 times. It isolates the LLM stage for well under $0.10 and needs no audio. This is for the coordinator to run with the OpenRouter key. I did not run it.
- **Cost estimate.**
  - A 1-minute session costs about $0.008 for Flux and about $0.018 to $0.022 for Aura-2 (3 replies, about 600 characters, plus preemptive waste), so **about $0.03 of Deepgram credit per session**.
  - M0 to M5 and M7 on the DGX, with M0 to M2 also on Cloud, is about 50 sessions, or **about $1.50 of Deepgram credit**.
  - OpenRouter spend is a few cents. Haiku is the priciest, at about $0.02 per session.
  - M6 costs about $0.12 of Inference credit (5 sessions) and fits in a reset $2.50.
  - Any Deepgram promotional pricing makes all of this lower.

## 8. Open questions and risks

- **Regions (unknown):**
  - Which region is the LiveKit Cloud project in?
  - Where is the DGX physically?
  - Where is the Mac's network egress?
  - Those three decide whether worker placement is worth more than any vendor swap.
- **Eager EOT cost and cutoffs.**
  - Deepgram expects 50 to 70% more LLM calls.
  - No published false-eager rate exists.
  - With `preemptive_tts`, discarded audio is billed Aura-2 characters.
  - If cutoffs appear, raise `eot_threshold` (0.75 → 0.8) before touching `min_delay`.
- **Flux versus the turn detector.** LiveKit's own eot-bench ranks `v1` ahead of Flux. That benchmark is by LiveKit, not independent, and M7 checks it on our audio.
- **Adaptive interruption needs Inference.** On the DGX, leave `interruption.mode` unset. Confirm the worker does not try the hosted model on a `local` connection.
- **Hosted `v1` billing for a Mac worker using Cloud credentials:** undocumented. With Build credits exhausted, does `v1` fail over to `v1-mini` (`local_fallback: true`)? Check the worker logs during M6.
- **Model facts that need checking:**
  - Luna's price: our brief says $0.05/$0.25. OpenRouter's page on 2026-09-30 says $0.10/$0.50.
  - The exact OpenRouter slug for Haiku 4.5.
  - Whether GPT-4.1 mini/nano are being deprecated (AA marks them so).
- **Tool reliability of the fastest models:**
  - gpt-oss-120b passed 86.3% on Pipecat and has no parallel tool calls on Groq.
  - GPT-5.6 Luna `none` (GPT-6 Luna's predecessor) passed 88.3%.
  - GPT-4.1 mini passed 85.3%.
  - Gemini 3.5 Flash-Lite passed 68.6%.
  - Insurance claim flows use tools, so M3/M4 must count wrong tool calls.
- **Deadlines:**
  - Rime Mist v3 is free on Inference until 2026-10-01.
  - Cartesia Sonic Turbo and Sonic 2 stop 2026-10-20.
  - Gemini 2.5 retires on Inference 2026-10-10/20.
- **Knowledge auto-inject** turns preemptive generation off (F3). Agents with a knowledge base need `preemptive_generation.enabled: true` set explicitly, and still lose the speculative reply on turns with a knowledge hit.
- **Benchmark caveats:**
  - Several numbers come from summarised pages (Coval's board and Voice Arena are JavaScript-rendered and were read second-hand).
  - Artificial Analysis LLM TTFT uses 10k-token inputs.
  - Pipecat's STT benchmark excludes Flux.
  - Treat everything as ranking evidence and let M0 to M7 decide.

## 9. Proposed follow-ups (not implemented)

1. **A Flux-aware preset.** Add a `"stt_turns"` or `"flux"` conversation preset (`min_delay` 0.1, `preemptive_tts` on). Or make `snappy` drop `min_delay` to about 0.1 when `stt_decides_turns()` is true. This removes F1 without asking builders to write custom JSON.
2. **Expose Silero `min_silence_duration`** on `silero-vad`, and default it to 0.3 when the audio turn detector is used (F4). Revisit `balanced` against the SDK's 0.3/2.5 streaming defaults.
3. **Inference STT turn-taking:**
   - A per-model `end_of_turn` capability for `livekit-inference-stt` (Flux models, `cartesia/ink-2`, AssemblyAI).
   - Pass Flux `eot_threshold`, `eager_eot_threshold` and `eot_timeout_ms` as `extra_kwargs` (F5).
4. **Update registry suggestions:**
   - Inference TTS: add `inworld/inworld-tts-2-flash` and `deepgram/flux-tts`.
   - `anthropic-llm`: add `claude-haiku-4-5`.
   - Mark Sonic Turbo / Sonic 2 as deprecated.
   - Refresh the console's "Fast for voice" LLM list with the §3.1 evidence: Gemma 4 31B, Haiku 4.5, gpt-oss-120b on Groq/Cerebras (with the tool-call caveat).
5. **OpenRouter:**
   - Send `user` / `prompt_cache_key` (a session or agent id) for sticky routing and cache warmth.
   - In the console, suggest `{"sort":"latency"}` for multi-host models and explain that default routing is weighted by price.
6. **DGX image.** Either add `inworld`, `groq`, `cerebras` and `baseten` to `slim.txt`, or build the DGX worker from `full`, so Stack B's upgrades are possible there (F7).
7. **Worker placement.** Consider a documented "worker in the Cloud region" or LiveKit Cloud agent-hosting option for Cloud connections (§6).
8. **Measurement harness.** Commit the scripted caller with per-stage metric capture (F10), so M0 to M7 can be repeated after each change.

## 10. Sources (fetched 2026-09-30 unless noted)

**LiveKit**
- https://docs.livekit.io/agents/logic/turns/
- https://docs.livekit.io/agents/logic/turns/turn-detector/
- https://docs.livekit.io/agents/logic/turns/tuning/
- https://docs.livekit.io/agents/models/stt/deepgram/
- https://docs.livekit.io/agents/models/stt/assemblyai/
- https://docs.livekit.io/agents/models/llm/
- https://docs.livekit.io/agents/models/tts/
- https://docs.livekit.io/deploy/admin/billing/
- https://livekit.com/pricing
- https://livekit.com/blog/latency-optimized-inference-gemma-4-on-livekit (dated "07.02.2026")
- https://github.com/livekit/eot-bench
- community.livekit.io threads: 1277 (resolved 2026-09-08), 1658 (2026-07-17), 1704 (2026-07-23)

**Deepgram**
- https://developers.deepgram.com/docs/flux/quickstart
- https://developers.deepgram.com/docs/flux/configuration
- https://developers.deepgram.com/docs/flux/voice-agent-eager-eot
- https://developers.deepgram.com/docs/endpointing
- https://deepgram.com/pricing
- https://deepgram.com/learn/aura-2-leads-coval-real-time-tts-benchmarks
- Telnyx: https://telnyx.com/release-notes/automatic-eager-end-of-turn-deepgram-flux (2026-01-14)

**Other STT**
- https://www.assemblyai.com/pricing
- https://www.assemblyai.com/docs/streaming/universal-3-pro
- https://cartesia.ai/blog/ink-2 (2026-07-09)
- https://soniox.com/docs/stt/rt/endpoint-detection
- https://docs.speechmatics.com/speech-to-text/realtime/end-of-turn
- https://docs.gladia.io/chapters/live-stt/features/endpointing
- https://x.ai/news/grok-stt-and-tts-apis

**Independent benchmarks**
- https://github.com/pipecat-ai/stt-benchmark
- https://artificialanalysis.ai/speech-to-text/streaming
- https://pipecat.ai/benchmarks/voice-readiness (2026-08-18)
- https://pipecat.ai/benchmarks/phonebench-alpha-1 (2026-08-27)
- https://www.daily.co/blog/benchmarking-llms-for-voice-agent-use-cases (2026-02-02)
- https://www.coval.ai/blog/time-to-first-audio-ttfa-benchmark/ (2026-09-08)
- https://www.coval.ai/blog/tts-latency-benchmark-2026/ (2026-07-23)
- https://gradium.ai/content/tts-latency-benchmark-2026 (vendor reproduction of Coval data)
- https://artificialanalysis.ai/text-to-speech/leaderboard/provider-voice
- Artificial Analysis LLM provider pages under https://artificialanalysis.ai/models/…/providers (gpt-6-luna-non-reasoning, gpt-4-1-mini, gpt-4-1-nano, claude-4-5-haiku, gpt-oss-120b-low, gemma-4-31b-non-reasoning, gemini-2-5-flash-lite, llama-3-3-instruct-70b)

**LLM hosts and APIs**
- https://openrouter.ai/docs/features/provider-routing
- https://openrouter.ai/docs/guides/best-practices/latency-and-performance
- https://openrouter.ai/docs/features/prompt-caching
- https://openrouter.ai/openai/gpt-6-luna/providers
- https://developers.openai.com/api/docs/guides/latency-optimization
- https://console.groq.com/docs/tool-use/overview
- https://console.groq.com/docs/model/openai/gpt-oss-120b
- https://inference-docs.cerebras.ai/capabilities/tool-use
- https://www.cerebras.ai/blog/openai-gpt-oss-120b-runs-fastest-on-cerebras (2025-08-06)

**TTS vendors**
- https://docs.inworld.ai/docs/tts/tts
- https://inworld.ai/pricing
- https://elevenlabs.io/docs/best-practices/latency-optimization
- https://elevenlabs.io/pricing/api
- https://docs.cartesia.ai/use-the-api/compare-tts-endpoints
- https://docs.cartesia.ai/build-with-cartesia/tts-models/older-models
- https://docs.rime.ai/docs/latency
- https://rime.ai/pricing

**Repo (read only)**
- `contracts/src/lkap_contracts/turn_handling.py`, `agent_config.py`, `common.py`, `contracts/generated/providers.json`
- `agent/src/lkap_agent/session_builder.py`, `main.py`, `providers/factory.py`
- `agent/requirements/{slim,full}.txt`
- `docs/research-v6/{livekit-inference,speech-vendors,tts-vendors,gateways-audio,openrouter-audio}.md`
- `docs/v6/ARCHITECTURE-V6.md` §21
- `docs/v6/_briefs/{v6-model-latency,dgx-worker-deploy}.md`
- livekit-agents 1.8.3 and plugin source in `agent/.venv`
