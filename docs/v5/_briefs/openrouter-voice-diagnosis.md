# OpenRouter voice sessions: diagnosis (2026-09-26)

**Report.** With OpenRouter in the STT, LLM and TTS slots, voice sessions sit on "listening" and nothing
happens. The one time something worked, the transcript was slow and the voice sounded distorted
("zombie-ish").

**Short answer.** Two LKAP bugs meant OpenRouter speech could not work at all, and one config choice
explains the odd voice. OpenRouter itself is not broken, but its speech endpoints are request/response
only (no streaming), so even after these fixes they will always be slower than a streaming STT/TTS.

## Evidence

The evidence was read-only: the dev DB `api/data/lkap.db` (sessions, `session_events`, agent config
versions, the catalog cache) and the worker log. Eight sessions used `openrouter-stt`/`openrouter-tts`
(2026-09-24 to 2026-09-26). Four ran; four never started (no worker was running; unrelated).

| Session | STT (language) | TTS (voice) | What happened |
|---|---|---|---|
| `ef86f22a` | openrouter-stt `openai/gpt-4o-mini-transcribe` (`multi`) | `google/gemini-3.8-flash-tts` (Fenrir) | TTS 400 on the greeting; 8 STT 400s; no user turn |
| `b02bac9f` | same | same | TTS 400 twice (greeting and reply: silent); 8 STT 400s; the one user turn ("hello") was typed |
| `a7b481a6` | `microsoft/mai-transcribe-2` (`multi`) | `google/gemini-3.8-flash-lite-tts` | TTS 400; 8 STT 400s; no user turn |
| `0a179844` | `microsoft/mai-transcribe-2` (`multi`) | `deepgram/aura-2` (`aura-2-agathe-fr`) | Greeting spoken (TTS TTFB 2378 ms); 8 STT 400s; no user turn |

Both errors are the same in every session:

- TTS: `APIStatusError 400: Gemini TTS only supports response_format="pcm". Got "mp3".`
- STT: `APIStatusError 400: Provider returned 400`. The SDK tries each utterance 4 times over about 6 s,
  then logs `STT stream ended on an unrecoverable error, recreating` and starts over. The UI shows
  "listening" the whole time. VAD and `StreamAdapter` are set up correctly (the traceback goes through
  `stt/stream_adapter.py`), so a missing VAD is not the cause.

For comparison, the LiveKit Inference session `6c383a80` (same day, same connection) ran 9 turns with
end-of-utterance to first audio at p50 2.6 s and p95 6.8 s.

## Root causes

### 1. TTS always asked for MP3, and Gemini TTS only does PCM (LKAP bug, fixed)

`openrouter-tts` used the stock `livekit.plugins.openai.TTS` (1.8.3). That class sends
`response_format="mp3"` unless told otherwise (`livekit/plugins/openai/tts.py:113`), and LKAP never
told it otherwise. OpenRouter's Gemini TTS models, including the registry default
`google/gemini-3.8-flash-tts`, reject MP3, so every sentence failed. The agent was silent.
`agent/tests/unit/test_factory_openrouter.py` asserted `response_format == "mp3"`, which locked the bug
in. OpenRouter's docs say the default format is PCM; Voxtral is the exception, which takes MP3 only.

There was a second, hidden problem. OpenRouter sends PCM with `Content-Type:
audio/pcm;rate=<hz>;channels=<n>`. The plugin drops those parameters (`tts.py:275`) and labels all audio
24 kHz mono (`tts.py:39,284`). If a model returns PCM at any other rate, it plays at the wrong speed and
pitch. For example, 16 kHz audio labelled 24 kHz plays 1.5x fast. No session hit this, because only MP3
was ever requested, but switching to PCM would expose it.

### 2. STT sent `language="multi"`, which OpenRouter/OpenAI transcription rejects (LKAP bug, fixed)

Every OpenRouter STT config had `fields.language = "multi"`. That value comes from the console's shared
language picker (`web/src/components/console/registry/registry-form.tsx:143`). `multi` is Deepgram's
code for "multilingual". OpenRouter's `/audio/transcriptions` takes one ISO-639-1 code (`en`, `ja`) and
auto-detects the language when none is given. So `multi` fails on every utterance. Two different STT
models (`gpt-4o-mini-transcribe`, `mai-transcribe-2`) failed the same way, which points at the shared
request, not at a model.

The plugin sends nothing else unusual (a 24 kHz WAV, `response_format=json`, no prompt), so `language` is
the only candidate. This could not be confirmed with a live call: no key was used and no paid calls were
made. It is the most likely cause, and the fix is correct either way. Codes like `en-US` would fail the
same way.

### 3. "Zombie" voice in the one session that spoke: a French voice reading English (config)

Session `0a179844` used `deepgram/aura-2` with voice `aura-2-agathe-fr`, a French Aura-2 voice, to read
an English greeting. That is the most likely cause of the odd, robotic sound. A sample-rate mix-up is
unlikely. The request was MP3, which the decoder resamples to the right rate. The recorded audio was
7.1 s for the 90-character greeting, a normal speaking pace; 1.5x too fast would be about 4.7 s and 1.5x
too slow about 10.6 s. What content type OpenRouter actually returned was not logged. The slow start
(TTS TTFB 2.4 s, and 2.0 s more before playback) comes from the non-streaming, one-request-per-sentence
design (see below). The "transcript" the user saw was the agent's greeting. No user speech was ever
transcribed in any OpenRouter session.

### Side finding: flow variable extraction fails on OpenRouter (not changed)

In `0a179844` the worker log also shows `workflow LLM call failed: 404 No endpoints found that can
handle the requested parameters` from the flow's variable extractor. `_openrouter_llm_kwargs` defaults
`provider.require_parameters=true` (D-V4-12, so tool calls never land on an endpoint that ignores
them). With the chosen model, no endpoint supports every parameter the request sends. The workaround is
a different workflow model, or `{"require_parameters": false}` in that slot's provider preferences. This
was left as it is because it is a design decision, not part of this report.

## What was fixed

- **`agent/src/lkap_agent/providers/openrouter.py`, new `OpenRouterTTS`.** The registry's
  `openrouter-tts.python_class` now points here (`contracts/src/lkap_contracts/providers.py:1354`). It
  keeps the OpenAI plugin's constructor, and:
  - asks for `pcm` by default and `mp3` for `mistralai/voxtral*` (`response_format_for`);
  - reads `rate`/`channels` from the response `Content-Type` and labels frames with the rate the vendor
    actually sent (`parse_audio_content_type`, falling back to 24 kHz). The voice pipeline then
    resamples to the room's rate (livekit-agents `voice/generation.py:631`). The pipeline does not
    remix channels, so multi-channel PCM is downmixed to mono (`PcmDownmixer`);
  - drops `stream_format` (OpenRouter returns raw bytes, never SSE) and uses `X-Generation-Id` as the
    request id.
- **`agent/src/lkap_agent/providers/factory.py`: `openai_transcription_language_kwargs`.** This runs for
  every `livekit.plugins.openai.STT` provider (`openrouter-stt`, `openai-stt`). `multi`, `auto` or empty
  becomes `detect_language=True` (the language is left out), and `en-US` or `pt_BR` becomes `en` or `pt`.
  It runs in the worker, so configs already saved with `multi` work without being saved again.
- **The factory prefers the worker registry's `lkap_agent.*` class** when an older api still resolves
  the previous vendor class. This covers a restart order where the worker comes up before the api.
- **`api/src/lkap_api/config_service.py`: `speech_latency_issues`.** Validator warnings (not errors):
  - `pipeline.stt` on `openrouter-stt`: each turn is transcribed in one request after the user stops
    speaking, with no live transcript, adding about 0.5 to 2 s; use LiveKit Inference or Deepgram.
  - `pipeline.tts` on `openrouter-tts`: speech is not streamed, which adds about 1 to 2.5 s before the
    agent speaks; use LiveKit Inference or Cartesia.
  - `pipeline.stt.fields.language` on an OpenAI-style transcriber, when the value is not a two-letter
    code: says whether the language will be auto-detected or which code will be sent instead.
- The registry note for `openrouter-tts` now describes the format/rate handling and the latency.

Tests: `agent/tests/unit/test_openrouter_tts.py` (format per model, Content-Type parsing, a real
`ChunkedStream` against a mocked `/audio/speech`: 16 kHz PCM stays 0.5 s and is labelled 16 kHz, no
`stream_format`, a 400 surfaces as `APIStatusError`), `agent/tests/unit/test_factory_openrouter.py`
(PCM default, Voxtral MP3, version-skew fallback, language normalisation table), and
`api/tests/test_speech_latency_issues.py`.

**Check after restart.** `deepgram/aura-2`, the only model that ever spoke, used MP3 and now gets PCM.
OpenRouter documents PCM as the endpoint default, so this should work, but it is the first thing to try
on a real session. The console has no `response_format` setting. If another model turns out to be MP3
only, add it to `_MP3_ONLY_MODEL_PREFIXES` in `openrouter.py`.

## What is inherent to OpenRouter (not fixable in LKAP)

- **STT is batch only.** OpenRouter has no streaming transcription (no websocket, no `/realtime`). Each
  turn means: wait for Silero to detect end of speech, upload the WAV, wait for the full transcript. There
  are no interim results, so the turn detector and preemptive generation get nothing early. Expect 0.5 to
  2 s more per turn than a streaming STT.
- **TTS is one request per sentence.** Nothing plays until the first sentence has been sent and the audio
  starts coming back. The one measured TTFB was 2.4 s (Aura-2 through OpenRouter), against 0.7 s p50 for
  LiveKit Inference TTS in `6c383a80`.
- **Every call makes an extra HTTP hop.**

## Recommendation

- **Keep OpenRouter for the LLM family**: llm, workflow_llm, QA judge, embeddings and image
  generation. That is what it is good at.
- **For speech, use a streaming provider**: LiveKit Inference STT/TTS (no extra key; it bills through
  the LiveKit project), or Deepgram STT with Cartesia or ElevenLabs TTS. This is also the recommendation
  in V4 (`docs/v4/OPENROUTER.md` §3).
- **If OpenRouter speech is required** (for example, one invoice):
  - set the STT language to a two-letter code (`en`), or leave it blank for auto-detect;
  - pick a TTS voice in the same language as the agent (an `-en` Aura-2 voice, or any Gemini voice);
  - accept roughly 2 to 4 s from end of speech to first audio.
- **Restart the worker and the api** to pick up the fixes. The worker holds the new class and the
  language mapping; the api holds the new registry `python_class` and the warnings. The factory fallback
  still covers a worker restarted before the api.
