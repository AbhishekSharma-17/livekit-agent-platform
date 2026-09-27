# LKAP v6 — cross-package asks

Format (v3/v4/v5): **owner package** — file — the exact edit — who asked — status. The coordinator's decisions are binding; design questions go to Fable and land as `R-V6-n` in `PLAN-V6.md` §7. Never a key value, a hostname from the environment, a user name or a machine path.

## Coordinator decisions

(none yet)

## Open

| # | Owner | File | Edit | Asked by | Status |
|---|---|---|---|---|---|
| 1 | V6-02 (`agent/providers/**`) | `agent/src/lkap_agent/providers/openrouter.py` (a new OpenRouter STT class in place of the stock `openai.STT` for `openrouter-stt`) | Read the transcription response's `x-generation-id` header (the OpenAI SDK's `with_raw_response`) and return it as the `SpeechEvent.request_id`, so `STTMetrics.request_id` carries OpenRouter's `gen-…` id through the `StreamAdapter` into `ProviderRequestCollector`. Today the stock plugin's batch `_recognize_impl` builds its event with no request id, so no STT id is ever reported and the V6-01 STT reconcile path has no data. Verify first that `/audio/transcriptions` sends the header (live, cheapest model); if it does not, close this as "not reportable" | V6-01 | Open |
| 2 | coordinator | `docs/v6/PLAN-V6.md` §5.1 | Add V6-01's deferred live checks: (a) with the stored OpenRouter key, the LLM, STT and TTS pickers show a live price (`deepgram/nova-3` ≈ $0.0043/min, `openai/gpt-4o-mini-transcribe` ≈ $0.0035/min, `google/gemini-3.8-flash-tts` ≈ $0.0061/min, `microsoft/mai-transcribe-2` "no price"); (b) one OpenRouter TTS sentence: does the response carry `x-generation-id`, and does `GET /generation?id=` answer `total_cost` for it (if not, the TTS reconcile path stays dormant, harmlessly: non-`gen-` ids are never looked up) | V6-01 | Open |
| 3 | Fable (`PLAN-V6.md` D-V6-9) | `docs/v6/PLAN-V6.md` D-V6-9, the V6-01 card | Record two facts V6-01 found against the live catalogue (2026-09-28): Gemini TTS lists its audio-out price as `completion`, not `audio_output` (V6-01 reads `audio_output` when present, else `completion`); and OpenRouter's per-token OpenAI transcription is priced at 40 audio tokens a second, derived from OpenAI's own per-minute estimates ($0.003/min at $1.25 per 1M for gpt-4o-mini-transcribe, $0.006 at $2.50 for gpt-4o-transcribe), not the realtime guide's 10/s — a ruling can change the rate in `api/src/lkap_api/costs/assumptions.py` (`OPENROUTER_AUDIO_TOKENS_PER_S`) | V6-01 | Open |
| 4 | V6-02 (`agent/providers/**`) | `agent/src/lkap_agent/providers/openrouter.py` (`OpenRouterTTS`, and the STT class of ask #1) | Report token usage for OpenRouter's token-billed speech so an ended session's cost line is priced: the TTS stream calls `_set_token_usage(input_tokens=…, output_tokens=…)` and the STT event carries `recognition_usage`, from the response's `usage` when OpenRouter returns one (verify live). Today (livekit-agents 1.8.3) the batch STT path reports `audio_duration` only and `OpenRouterTTS` reports characters only, so after V6-01 an actual line for `openai/gpt-4o-mini-transcribe` or `google/gemini-3.8-flash-tts` reads "no price" (before V6-01 it was a figure 16–40× too low); the estimate and the picker are priced | V6-01 | Open |
