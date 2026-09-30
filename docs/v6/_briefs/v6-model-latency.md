# V6 — voice reply latency by LLM (live, 2026-09-29)

Measured after V6-31 on `demo-blank-agent`, LiveKit Cloud, LiveKit Inference streaming STT (`deepgram/nova-3`) and TTS (`inworld/inworld-tts-2`), LLM through OpenRouter. A scripted caller joins through `/v1/agents/{slug}/connect`, plays a synthesized question into the room and times the gap between the end of its speech and the first voiced frame of the agent's reply. One run per model; treat ±0.5 s as noise.

| LLM (OpenRouter) | Reasoning effort sent | Reply audio starts after the caller stops | Price per 1M tokens (in / out) |
|---|---|---|---|
| `openai/gpt-oss-120b` | automatic (lowest) | 3.4 s | $0.04 / $0.17 |
| `openai/gpt-4.1-mini` | — (not a reasoning model) | 3.4–3.6 s | $0.40 / $1.60 |
| `openai/gpt-4.1-nano` | — | 3.6 s | $0.10 / $0.40 |
| `google/gemini-3.1-flash-lite` | automatic (lowest) | 3.7 s | $0.125 / $0.75 |
| `openai/gpt-6-luna` | automatic (`none`, V6-31) | 3.8 s | $0.05 / $0.25 |
| `openai/gpt-6-luna` | `low` | 6.4 s | same |
| `openai/gpt-6-luna` | provider default (`medium`, before V6-31) | 8.2 s | same |
| `openai/gpt-6-luna` on the self-hosted server with OpenRouter STT/TTS (not streaming) | `medium` | 11.4 s | same |

Findings:
- With a non-reasoning model or the lowest effort, every model lands at about 3.4–3.8 s: the floor is end-of-turn detection plus streaming STT finalisation and TTS time-to-first-byte, not the LLM.
- Reasoning effort is the dominant lever for reasoning models: `none` vs `medium` on Luna is 3.8 s vs 8.2 s.
- Before V6-31, `openai/gpt-6-luna` failed every turn (OpenRouter 404, `temperature` not supported under `require_parameters`); V6-31 drops unsupported parameters.
- Self-hosted LiveKit 1.13.7 (protocol 17) on the DGX works end to end: dispatch, the agent join (about 1.5 s), audio both ways, STT and TTS through OpenRouter.

## 2026-09-30 — Deepgram speech, new turn-taking settings, three LLMs

Scripted caller with real-time audio pacing (the earlier 1 s send buffer removed). All agents: Deepgram Flux STT (`flux-general-en`, `eot_threshold` 0.75, `eager_eot_threshold` 0.4, `eot_timeout_ms` 3000) and Deepgram Aura-2 TTS; `turn_handling` custom with `endpointing.min_delay` 0.1 and preemptive generation + preemptive TTS (the "new turns" settings, from `docs/research-v6/low-latency-stack.md`). Two runs per cell; LLM first-token and TTS first-byte from the session's own metrics.

| Config | Cloud (`demo-phone-agent`) reply start | DGX (`demo-blank-agent`, worker on the Mac over Tailscale) | LLM first token | TTS first byte |
|---|---|---|---|---|
| Baseline (old turns, GPT-6 Luna) | 3.45, 3.99 s | 5.04, 8.60 s | 0.97–2.19 s | 0.33–0.42 s |
| M1: new turns, GPT-6 Luna (effort none) | 2.58, 2.72 s | 5.41, 6.43 s | 0.97–1.78 s | 0.31–0.34 s |
| M2: new turns, Claude Haiku 4.5 (OpenRouter → Anthropic) | 2.38, 3.04 s | 2.92, 6.56 s | 1.09–4.05 s | 0.30–0.38 s |
| M3: new turns, GPT-OSS 120B effort low (OpenRouter → Groq/Cerebras) | **1.42, 2.91 s** | **2.28, 3.41 s** | **0.29–0.61 s** | 0.30–0.32 s |

Findings: the new turn settings cut about 1 s on Cloud; the LLM hop is the remaining lever — GPT-OSS on Groq/Cerebras starts in ~0.3 s vs ~1–1.8 s for GPT-6 Luna and ~1.1 s for Haiku (one 4 s outlier). GPT-OSS answered more tersely (dropped "can you hear me") and has a lower published tool-call pass rate (86%, Pipecat benchmark), so prefer it for simple conversational agents and keep Luna/Haiku for tool-heavy ones. The DGX path is noisy and slower because the worker runs on the Mac across a relayed Tailscale path; `docs/v6/_briefs/dgx-worker-deploy.md` moves it onto the DGX.

## 2026-09-30 — after V6-34 (`fast` preset + OpenRouter `sticky_routing`)

All demo agents except `demo-knowledge-assistant` now use `conversation_preset: "fast"` (Flux → `min_delay` 0.1 + preemptive generation and TTS) and `sticky_routing: true`; the knowledge assistant keeps its custom turn handling (auto-inject) and gets `sticky_routing`. Live check for ask #327: `sticky_routing` with `require_parameters: true` caused no OpenRouter 404 on `openai/gpt-oss-120b` (Groq/Cerebras) or `openai/gpt-6-luna` (0 404s in the worker log).

| Agent | Where | LLM | Reply start |
|---|---|---|---|
| `demo-phone-agent` | Cloud | GPT-OSS 120B @ Groq/Cerebras | 1.98, 2.50 s |
| `demo-claims-intake` | Cloud | GPT-6 Luna | 2.23 s (was 3.12 s on 2026-09-29) |
| `demo-order-checkout` | DGX (worker on the Mac) | GPT-6 Luna | 2.21 s (was 2.51 s) |
