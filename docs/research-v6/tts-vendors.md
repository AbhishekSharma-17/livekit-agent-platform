# TTS-first vendors in LiveKit Agents 1.8.x (researched 2026-09-28, flags from plugin source on main)

| Vendor | Streams in LiveKit? | Plugin default to override | Price (list, per 1M chars) | STT? | Notes |
|---|---|---|---|---|---|
| Cartesia Sonic-3.x | Yes (WS, pcm_s16le 24k) | default `sonic-3` → use `sonic-3.6` | ~$37 to $50 | Ink-2 streaming WS | sub-90 ms TTFA |
| ElevenLabs Flash v2.5 / v3 | Yes (multi-stream-input WS) | default `eleven_turbo_v2_5` + mp3 → use `eleven_flash_v2_5` + PCM | $50 (Flash), $100 (v2/v3) | Scribe v2 Realtime ($0.39/hr) | **Retired from LiveKit Inference 2026-08-31**, own key only |
| Rime Coda / Mist v3 | **Only with `use_websocket=True`** (HTTP by default) | set websocket on | $30 (Mist v3), $50 (Coda) | none | sub-100 ms models |
| Inworld TTS-2 / TTS-2 Flash | Yes (WS) | default `inworld-tts-1.5-max` is **deprecated** → `inworld-tts-2-flash` / `inworld-tts-2` | $25 / $15 | inworld-stt-1 streaming ($0.15/hr) | |
| Hume Octave | **No** (HTTP stream json, streaming=False) | none | $50 to $150 | no (EVI is S2S) | |
| Fish Audio s2.1-pro | Yes (WS) | none | $15 per 1M UTF-8 **bytes** | transcribe-1 | |
| MiniMax speech-2.8 | Yes (WS) | package is **`livekit-plugins-minimax-ai`** (not `-minimax`, stale 1.3.0), default `speech-02-turbo` → `speech-2.8-turbo` + PCM | $60 (turbo), $100 (hd) | ASR (streaming undocumented) | |
| xAI Grok voice | Yes (WS) | none | $15 | grok-voice-transcribe-2.0 streaming ($0.20/hr) | session cap 1,800 s in plugin |

Implications for LKAP: check the registry's default models/formats for these providers and override deprecated plugin defaults. The Rime entry should enable websocket, Hume should be marked as non-streaming, ElevenLabs should not go via Inference, and the MiniMax package name in agent/requirements/full.txt should be verified.
Sources: livekit/agents plugin source (main), PyPI JSON for each plugin, vendor pricing/docs pages (Cartesia, ElevenLabs, Rime, Inworld, Hume, Fish Audio, MiniMax, xAI), all accessed 2026-09-28.
