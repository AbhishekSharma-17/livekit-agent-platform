# Multi-model gateways as STT/TTS sources for LiveKit Agents 1.8.x (researched 2026-09-28)

How I checked: vendor docs, PyPI JSON, and the source of the `livekit-plugins-{fal,groq,fireworksai,openai}` 1.8.3 wheels (the capability flags below come from the code, because docs.livekit.io does not list streaming support). I made no calls to any vendor's inference endpoint.

| Vendor | STT models + streaming? | TTS models + streaming transport | Latency (documented) | Pricing model (example) | LiveKit plugin (PyPI latest, 1.8.x?) | Notes |
|---|---|---|---|---|---|---|
| **fal.ai** | Wizper (Whisper v3 L), `fal-ai/speech-to-text` (Canary-based), `.../stream`. **Batch only.** Queue/HTTP with `audio_url`. The `/stream` endpoint is SSE progress over a file, not live-audio input. The fal realtime WebSocket (`wss://fal.run/{id}/realtime`) is documented only for 2 image models. | Kokoro, ElevenLabs, MiniMax, Dia, Chatterbox, Maya and others. Most return a **file URL** (wav/mp3). A few `/stream` endpoints use fal SSE streaming: `fal-ai/maya/stream` offers mp3/wav/**pcm** at 24 or 48 kHz. No WebSocket TTS. | Not documented for audio. Wizper is described as "2x faster" than Whisper v3 L (marketing). | Per model/output. STT `speech-to-text/stream` $0.0008/s. Wizper is not clearly documented (its page shows $0/compute-s). TTS Kokoro $0.02/1K chars, ElevenLabs Turbo v2.5 $0.05/1K. | `livekit-plugins-fal` **1.8.3 ✓**. STT only: `fal.WizperSTT`, `streaming=False` (`interim_results=True` flag), calls `fal-ai/wizper`. No TTS class. | No OpenAI-compatible audio API, so `livekit-plugins-openai` does not apply. Wizper needs VAD + StreamAdapter. |
| **Together AI** | Whisper L v3, Parakeet TDT 0.6B v3, Nemotron ASR, Deepgram Nova-3/Flux. Batch: `POST /v1/audio/transcriptions`. **Streaming ✓**: `wss://api.together.ai/v1/realtime?model=…&input_audio_format=pcm_s16le_16000`, OpenAI-Realtime-style events with `…transcription.delta` (interim) and `.completed`, plus server VAD. The docs show only `openai/whisper-large-v3` for streaming, and the only format is **16 kHz** s16le. | Orpheus, Kokoro, Cartesia Sonic-2/3, Rime Mist/Arcana, MiniMax. `POST /v1/audio/speech` (wav/mp3/raw/mulaw, configurable `sample_rate`, e.g. 24000). **HTTP SSE** (`stream:true`, base64 deltas, **raw PCM only**). **WebSocket** `wss://api.together.ai/v1/audio/speech/websocket`, text-in / audio-delta-out with word timestamps (mp3/opus/aac/flac/wav/pcm). | Not documented (only "low TTFB" / "lowest latency" wording). | STT per audio minute: Whisper L v3 $0.0015, Whisper streaming $0.0035. TTS per 1M chars: Kokoro $4, Orpheus $15, Cartesia Sonic-3 $65. | **None** (`livekit-plugins-together` is not on PyPI). | OpenAI-compatible. Batch STT and TTS should work through `openai.STT/TTS(base_url="https://api.together.xyz/v1")`, but TTS is whole-file there (plugin `streaming=False`). The realtime STT path probably needs a **custom plugin**, because the openai plugin sends 24 kHz PCM and Together requires 16 kHz plus the model in the query string and the `OpenAI-Beta: realtime=v1` header. Best streaming candidate of the eight. |
| **Groq** | `whisper-large-v3`, `whisper-large-v3-turbo`. **Batch only** (file-based). `POST https://api.groq.com/openai/v1/audio/transcriptions`. | `canopylabs/orpheus-v1-english` (autumn, diana, hannah, austin, daniel, troy) and `orpheus-arabic-saudi`. `POST /openai/v1/audio/speech`, **wav only**, **200-char max per request**. Streaming not documented. Sample rate not documented (plugin assumes 48 kHz). | STT speed factor: turbo 216x real time, v3 189x. TTS: "low-latency", no figures. | STT per hour: turbo $0.04, v3 $0.111, 10 s minimum bill. TTS: Orpheus EN $22/1M chars, AR $40/1M. | `livekit-plugins-groq` **1.8.3 ✓**. `groq.STT` subclasses the OpenAI STT with `use_realtime=False`, so it is **batch**. `groq.TTS` is `streaming=False` (ChunkedStream, wav, 48k). | OpenAI-compatible base URL. The 200-char cap can break long LLM sentences. docs.livekit.io still says the default model is `playai-tts`, but the 1.8.3 wheel defaults to `canopylabs/orpheus-v1-english`. |
| **DeepInfra** | Whisper L v3 / turbo, Qwen3-ASR, Voxtral, Nemotron-3.5-ASR-*Streaming*. The model pages say "streaming", but **only HTTP file POST is documented** (`/v1/inference/{model}`, OpenAI-compatible `/v1/audio/transcriptions`). No WebSocket documented. | Kokoro, Orpheus, Chatterbox, Qwen3-TTS, Inworld realtime-tts-1.5/2, Sesame CSM and others. OpenAI-compatible `POST /v1/audio/speech` (mp3/opus/flac/wav/pcm, default wav). An ElevenLabs-style `POST /v1/text-to-speech/{voice_id}/stream` exists, but its response transport is not clearly documented (the OpenAPI spec declares JSON). Sample rate not documented. | Not documented. | STT per minute: whisper-turbo $0.0002, v3 $0.00045. TTS per 1M chars: Kokoro $0.62, Orpheus $7, Inworld 1.5-max $50. | **None** (not on PyPI). | OpenAI-compatible (host `api.deepinfra.com`, spec paths `/v1/audio/*`), so the openai plugin works for batch STT and non-streaming TTS. |
| **Replicate** | openai/whisper, incredibly-fast-whisper, whisperx, gpt-4o(-mini)-transcribe, parakeet-rnnt, granite-speech. **Batch only**: prediction API (sync `Prefer: wait` / poll / webhook). Replicate's SSE streaming covers language models only. | inworld realtime-tts-1.5/2, minimax speech-2.8 hd/turbo, elevenlabs v3/v2, gemini-3.1-flash-tts, chatterbox and others. The output is a **file**. Audio streaming is not documented. | Model-vendor claims shown on Replicate pages, not measured by Replicate: Inworld 1.5 mini ~120 ms, 1.5 max <200 ms, MiniMax 2.8 turbo <250 ms. | Per-second hardware or per-output, varying by model. Example: incredibly-fast-whisper costs about $0.0047 per run (212 runs/$1). | **None**. | Not OpenAI-compatible for audio. Prediction plus file-URL round trips make it a poor fit for real-time use. |
| **Fireworks AI** | Previously whisper-v3/turbo and fireworks-asr streaming over WS. **Audio inference deprecated 2026-06-10** (official changelog), and the ASR doc pages now return 404. | None documented (a "Voice Agent Platform" beta mentions TTS, but there is no public API docs page). | Voice platform: "sub-500ms responses" (marketing). | Audio pricing is no longer listed on /pricing. | `livekit-plugins-fireworksai` **1.8.3 ✓**. STT `streaming=True` against `wss://audio-streaming.us-virginia-1.direct.fireworks.ai/v1/audio/transcriptions/streaming`, **but the backend is deprecated**, so treat it as dead. | Do not use it for STT/TTS. The openai plugin's `with_fireworks` covers LLM only. |
| **AI/ML API** (aimlapi.com) | Deepgram Nova-3/Nova-2, AssemblyAI Universal/Slam-1, Whisper tiny to large, gpt-4o(-mini)-transcribe, MAI Transcribe 1.5. **Async batch only**: `POST /v1/stt/create`, then poll `GET /v1/stt/{generation_id}`. No WebSocket documented. | Deepgram Aura/Aura-2, ElevenLabs (multilingual v2, turbo v2.5), OpenAI tts-1/-hd/gpt-4o-mini-tts, Inworld TTS-1/1.5, Hume Octave 2, Qwen3-TTS, MiniMax speech 2.x and others. `POST /v1/tts`. **HTTP chunked streaming** (`stream` defaults to true for Aura-2 and ElevenLabs, OpenAI models are `stream:false` only). Formats: Aura-2 linear16/mulaw/mp3/opus/…, ElevenLabs `pcm_8000` to `pcm_48000`, mp3, opus. | Aura-2 "sub-200 ms TTFB" (Deepgram's claim relayed in AIML docs). | Per second / per character. Nova-3 $0.0001668/s (about $0.01/min). Aura-2 $39/1M chars. | **None**. | Proprietary `/v1/tts` and `/v1/stt` schema. OpenAI-compatible `/v1/audio/*` is **not documented**, so the openai plugin does not apply and a custom plugin would be needed. |
| **Eden AI** | Amazon, AssemblyAI, Deepgram (incl. nova-3), Gladia, Google, Microsoft, OpenAI. **Async batch**: `POST /v3/universal-ai/async` (`audio/speech_to_text_async/{provider}`), then poll or webhook. Also OpenAI-compatible `POST /v3/audio/transcriptions`. No streaming or WebSocket documented. | Amazon, Deepgram aura/aura-2, ElevenLabs (flash/turbo/v3), Google (Chirp3-HD, Gemini TTS), OpenAI and others. Sync `POST /v3/universal-ai` or OpenAI-compatible `POST /v3/audio/speech`, which returns raw audio (mpeg/wav/pcm/ogg/flac/aac). Streaming not documented. | Not documented. | Provider price plus a 5.5% platform fee. STT: Deepgram nova-3 $0.0052/min, OpenAI $0.006/min. TTS: Aura-2 $0.03/1K chars, ElevenLabs $0.30/1K. | **None**. | OpenAI-compatible audio at `https://api.edenai.run/v3`, so the openai plugin could work with `base_url` for batch STT and whole-file TTS. This is an inference from the documented endpoint shape and has not been tested. |

## PyPI check (JSON API, 2026-09-28)
- `livekit-plugins-fal`: last 5 releases 1.7.1, 1.8.0, 1.8.1, 1.8.2, 1.8.3. Latest is 1.8.3 and it requires `livekit-agents>=1.8.3`.
- `livekit-plugins-groq`: latest 1.8.3.
- `livekit-plugins-fireworksai`: latest 1.8.3.
- `livekit-plugins-openai`: latest 1.8.3.
- Returned 404 (no such package): `together`, `deepinfra`, `replicate`, `aimlapi`, `edenai`.

## Key takeaways for a real-time LiveKit agent
- **Together** is the only one of the eight with documented streaming for both STT and TTS, namely a realtime WebSocket for STT with interim deltas and a WebSocket or SSE-PCM path for TTS. It has no LiveKit plugin, so it needs a custom STT/TTS plugin. The openai plugin covers only batch STT and whole-file TTS.
- **AI/ML API** has HTTP-chunked streaming TTS (Aura-2 and ElevenLabs PCM), but STT is async/poll only.
- **Groq and fal** have official 1.8.3 plugins, but both are batch STT. Groq TTS is non-streaming, wav-only and capped at 200 characters per request.
- **Fireworks.** Its plugin is the only one that streams (1.8.3 STT), but it points at an audio backend Fireworks deprecated on 2026-06-10.
- **DeepInfra, Replicate and Eden AI** have no documented streaming for audio input or output.
- `livekit-plugins-openai` 1.8.3. The TTS class declares `TTSCapabilities(streaming=False)`, but it reads the HTTP response incrementally through `with_streaming_response`. OpenAI-compatible servers that return plain bytes are handled. STT streams only when `use_realtime=True`, which uses the OpenAI Realtime protocol at 24 kHz.

## Sources (accessed 2026-09-28)
- https://pypi.org/pypi/livekit-plugins-{fal,together,groq,deepinfra,replicate,fireworksai,aimlapi,edenai,openai}/json, plus 1.8.3 wheel source (stt.py / tts.py / services.py / models.py)
- https://docs.livekit.io/agents/models/stt/
- https://docs.livekit.io/agents/models/tts/
- https://docs.livekit.io/agents/models/stt/plugins/fal/
- https://docs.livekit.io/agents/models/stt/plugins/groq/
- https://docs.livekit.io/agents/models/tts/plugins/groq/
- https://fal.ai/models/fal-ai/wizper
- https://fal.ai/models/fal-ai/wizper/api
- https://fal.ai/models/fal-ai/speech-to-text/stream
- https://fal.ai/models/fal-ai/speech-to-text/stream/api
- https://fal.ai/docs/documentation/model-apis/inference/real-time
- https://fal.ai/docs/documentation/model-apis/inference/streaming
- https://fal.ai/models/fal-ai/maya/stream/api
- https://fal.ai/models/fal-ai/kokoro/american-english
- https://fal.ai/models/fal-ai/kokoro/american-english/api
- https://fal.ai/models/fal-ai/elevenlabs/tts/turbo-v2.5/api
- https://fal.ai/explore/text-to-speech-apis
- https://docs.together.ai/docs/speech-to-text
- https://docs.together.ai/docs/inference/transcription/streaming
- https://docs.together.ai/docs/text-to-speech
- https://docs.together.ai/docs/inference/text-to-speech/streaming
- https://docs.together.ai/docs/inference/text-to-speech/websocket
- https://www.together.ai/pricing
- https://console.groq.com/docs/speech-to-text
- https://console.groq.com/docs/text-to-speech
- https://console.groq.com/docs/text-to-speech/orpheus
- https://console.groq.com/docs/model/canopylabs/orpheus-v1-english
- https://docs.deepinfra.com/llms.txt
- https://docs.deepinfra.com/apis/speech
- https://docs.deepinfra.com/apis/text-to-speech
- https://api.deepinfra.com/openapi.json
- https://deepinfra.com/models/automatic-speech-recognition
- https://deepinfra.com/models/text-to-speech
- https://deepinfra.com/nvidia/Nemotron-3.5-ASR-Streaming-Multilingual-0.6b/api
- https://deepinfra.com/hexgrad/Kokoro-82M/api
- https://replicate.com/docs/topics/predictions/streaming
- https://replicate.com/docs/topics/predictions/create-a-prediction
- https://replicate.com/pricing
- https://replicate.com/collections/speech-to-text
- https://replicate.com/collections/text-to-speech
- https://replicate.com/vaibhavs10/incredibly-fast-whisper
- https://replicate.com/inworld/realtime-tts-1.5-mini
- https://replicate.com/minimax/speech-2.8-turbo
- https://docs.fireworks.ai/updates/changelog (entry dated 2026-06-10: "Audio inference and image generation deprecation")
- https://docs.fireworks.ai/llms.txt
- https://fireworks.ai/platform/voice-agent-platform
- https://docs.aimlapi.com/llms.txt
- https://docs.aimlapi.com/api-references/speech-models/speech-to-text
- https://docs.aimlapi.com/api-references/speech-models/speech-to-text/deepgram/nova-3
- https://docs.aimlapi.com/api-references/speech-models/text-to-speech/deepgram/aura-2
- https://docs.aimlapi.com/api-references/speech-models/text-to-speech/elevenlabs/eleven_turbo_v2_5
- https://docs.aimlapi.com/api-references/speech-models/text-to-speech/openai/gpt-4o-mini-tts
- https://docs.aimlapi.com/api-references/speech-models/speech-to-text/stt-legacy
- https://aimlapi.com/models/deepgram-nova-3
- https://aimlapi.com/models/aura-2
- https://www.edenai.co/docs/v3/expert-models/features/audio/tts
- https://www.edenai.co/docs/v3/expert-models/features/audio/speech-to-text-async
- https://www.edenai.co/docs/v3/overview/plans-prices
- https://www.edenai.co/docs/api-reference/audio/audio-speech
- https://www.edenai.co/docs/api-reference/audio/audio-transcriptions
