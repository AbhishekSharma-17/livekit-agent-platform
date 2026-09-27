# OpenRouter STT/TTS for voice agents — findings (2026-09-28)

## OpenRouter audio
- TTS `POST /api/v1/audio/speech`: raw audio byte stream per request; no websocket, no incremental text input; pcm (default) or mp3. Whether bytes are forwarded as generated is undocumented. Billing "per character".
- STT `POST /api/v1/audio/transcriptions`: whole file only; no streaming/realtime/partials; 60 s upstream timeout; per second or per token depending on model.
- Chat audio: input base64 whole clip; audio output via SSE delta chunks (e.g. gpt-4o-audio-preview). No duplex.
- Catalog (live /models, 2026-09-28): 24 transcription models, 21 speech models. No latency figures published (latency_last_30m null).
- Sources: openrouter.ai/docs/guides/overview/multimodal/{tts,stt,audio}; /docs/guides/overview/models; blog announcing audio APIs 2026-05-01.

## How LKAP calls it
- STT: `openrouter-stt` = stock `livekit.plugins.openai.STT` (non-streaming) → AgentSession wraps in `stt.StreamAdapter` + Silero VAD → one WAV per utterance, one batch request, no interim results (plugin stt.py:612-644; providers.py:1363-1391).
- TTS: LKAP `OpenRouterTTS` (agent/src/lkap_agent/providers/openrouter.py:169-258), streaming=False → `tts.StreamAdapter` sentence split → one POST per sentence.
- Measured (docs/v5/_briefs/openrouter-voice-diagnosis.md): OpenRouter aura-2 TTS TTFB 2378 ms (+~2 s to playback) vs LiveKit Inference ~0.7 s p50. End-of-speech → first audio ~2–4 s on OpenRouter.

## Verdict
Not suitable for real-time voice STT/TTS. Use OpenRouter for the LLM family (llm, workflow_llm, QA judge, embeddings, image gen). OpenRouter audio OK for text tests, batch/offline, voice comparison.

## Why prices don't show (bugs)
- Defect A (all OpenRouter models, incl. LLM): picker calls POST /v1/pricing/quotes with {provider_id, model}; `load_live_sheets` (`api/src/lkap_api/costs/prices.py:190-219`) only selects catalog cache rows with credential_id IS NULL, but the OpenRouter catalog is keyed (every row has a credential_id) → falls back to list table (no openrouter rows) → "no price". Reproduced in-memory. Fix: resolve the workspace's default/only OpenRouter credential when a ref has none (reuse `costs/vendors/openrouter.py` `openrouter_api_key`), or cache /models as a public row.
- Defect B: `contracts/src/lkap_contracts/pricing.py:699-700` maps STT audio seconds and TTS chars to `pricing.prompt`, but OpenRouter's unit varies per model (per second: deepgram/nova-3 correct; per token: gpt-4o-mini-transcribe ~40× too low; mai-transcribe-2 prompt 0.1 → $6/min nonsense; gemini TTS per token, audio-out cost missed at estimate.py:219). Fix: classify unit by tokenizer, sanity bound (> ~$1/audio-min rejected), token-priced TTS estimate, or curated prices; extend reconcile (jobs/reconcile.py:99-126, LLM only) to STT/TTS generation ids.

## Alternatives (one key, many models, real streaming)
- LiveKit Inference is the only verified one-key gateway with websocket streaming STT+TTS (already in LKAP). STT: Deepgram flux/nova-3, AssemblyAI universal-streaming/3.x-pro, Cartesia ink-2/ink-whisper, Gemini transcribe live, Speechmatics linden-1, xAI stt. TTS: Cartesia sonic-3.x, Deepgram aura-2/flux-tts, Fish s2/s2.1-pro, Gradium, Inworld tts-1.5/2/2-flash, Rime coda/mistv3, xAI tts-1. No ElevenLabs. LKAP registry model lists are behind the current catalog → refresh.
- Direct streaming vendors already supported: Deepgram, Cartesia, ElevenLabs (TTS), Google (use_streaming), Azure (pinned in full.txt; not installed in dev venv), AssemblyAI, Speechmatics, Rime, Inworld.
- Generic gateways (fal, Together, DeepInfra, Replicate, AI/ML API, Eden AI): mostly batch HTTP audio — no better than OpenRouter for live voice (prior knowledge, unverified).

## Work items
1. Pricing defect A fix (prices.py) + test.
2. Pricing defect B (per-token audio units, sanity bounds, Gemini TTS audio-out).
3. Optional: reconcile STT/TTS OpenRouter generations.
4. Optional UX: label OpenRouter STT/TTS "batch — not for live calls".
5. Refresh LiveKit Inference model/voice lists.
