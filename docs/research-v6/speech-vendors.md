# Direct speech vendors in LiveKit Agents 1.8.3 — streaming facts (researched 2026-09-28)

Streaming flags from the plugin source at tag livekit-agents@1.8.3 (STTCapabilities/TTSCapabilities).

| Vendor | STT streams in LiveKit? | TTS streams in LiveKit? | Notes |
|---|---|---|---|
| Deepgram | Yes: `deepgram.STT` (Nova-3, interim results), `deepgram.STTv2` (Flux, built-in end-of-turn ~260 ms, eager EOT) | Yes: `deepgram.TTS` (Aura-2, websocket text-in), `deepgram.TTSv2` (Flux TTS) | Nova-3 $0.0077/min list; Flux EN $0.0077/min; Aura-2 $0.030/1k chars |
| AssemblyAI | Yes: `assemblyai.STT` (Universal-Streaming; `end_of_turn`) | n/a | $0.15/hr EN/multilingual; billed by websocket session time incl. idle |
| Speechmatics | Yes: `speechmatics.STT` (partials <500 ms) | **No** (`streaming=False`; English only, 4 voices) | Linden-1 $0.16/hr |
| OpenAI | **Only with `use_realtime=True`** or `gpt-realtime-whisper` / `gpt-live-transcribe`; default `gpt-4o-mini-transcribe` is batch | **No** (`openai.TTS` ChunkedStream; needs full text) | set use_realtime for pipelines |
| Google | Yes by default (`use_streaming=True`, Chirp 3) | Yes by default, **Chirp 3 HD voices only** | pin a Chirp 3 HD voice; 5-min stream limit (reconnect unverified) |
| Azure | Yes (`azure.STT`) | **No** in the plugin (`streaming=False`) although Azure supports text-in streaming | $1/hr STT; Neural TTS $15/1M chars |
| Cartesia, ElevenLabs (earlier report) | Cartesia Ink streams; ElevenLabs Scribe with use_realtime | Cartesia Sonic streams; ElevenLabs streams | installed in dev venv |

Takeaway: for an end-to-end streaming pipeline with direct keys, TTS should be Deepgram, Cartesia, ElevenLabs or Google Chirp 3 HD (or LiveKit Inference); OpenAI/Azure/Speechmatics TTS add whole-sentence latency in LiveKit. LKAP should (a) default `openai-stt` to realtime transcription for live calls, (b) mark non-streaming TTS entries in the registry/console, (c) consider Deepgram Flux (STTv2) with its own turn detection.

Sources: plugin source at https://github.com/livekit/agents/tree/livekit-agents@1.8.3/livekit-plugins ; vendor docs/pricing (Deepgram, AssemblyAI, Speechmatics, OpenAI, Google Cloud, Azure) accessed 2026-09-28.
