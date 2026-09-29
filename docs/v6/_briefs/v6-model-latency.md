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
