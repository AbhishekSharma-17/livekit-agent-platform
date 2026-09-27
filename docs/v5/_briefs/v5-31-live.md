# V5-31 live check — languages, the mid-call switch and captions

Status: **deferred** (not run by the implementing agent: it needs a dev-stack voice session plus an
api and worker restart, both outside the package's rules). The coordinator runs it after merge.
The captions renderer is V5-35's; until it lands, read the `lkap.captions` stream from the browser
console (step 6) or run this after V5-35.

**Inference `multi` (the card's open question).** LiveKit's documentation for LiveKit Inference's
Deepgram models lists `multi` among Nova-3's language codes ("set the language to `multi`"; billed
at the multilingual rate), and Deepgram's own overview gives Nova-3 `multi` as English, Spanish,
French, German, Hindi, Russian, Portuguese, Japanese, Italian and Dutch (both accessed
2026-09-27). The registry therefore records `language_detection: "multi"` for
`livekit-inference-stt`. **Documented, not yet confirmed live**: step 3 is the confirmation. If
the gateway refuses `multi`, record the error text here, set `auto_detect` off for the walk
(the tool path still works: Inference switches with `update_options(language=...)`), and mark
auto-detect "needs a Deepgram key".

## Steps

1. Scratch api on its own port and database (no migration in this package); a worker from this
   branch under a fresh agent name (never `lkap-agent`); a Builder key minted for the run and
   revoked at the end; `Demo — ` objects only.
2. Agent `Demo — Bilingual desk` (cascaded): STT `livekit-inference-stt` (`deepgram/nova-3`), LLM
   the Inference default, TTS `livekit-inference-tts`; `voice.languages = ["en", "hi"]`,
   `voice.auto_detect = true`, `voice.voices_by_language.hi` = a second voice that speaks Hindi
   (an Inference TTS voice with Hindi support, or OpenRouter TTS; record which). A panel with a
   `transcript` and a `captions` block (`position: "block"`). `agent_validate`: no error; record
   the warnings (expected: none for Hindi; a Tamil entry, if tried, draws the detection warning).
3. Browser session. Speak English first: English replies in the default voice. Then two Hindi
   sentences in a row: after the second, the `language_switched` event
   `{from_language: "en", to_language: "hi", source: "detected", stt_switched: false,
   voice_switched: true}`, replies in Devanagari with the Hindi voice. Record whether the worker
   log shows the STT built with `language=multi` and no gateway error (this answers the card's
   question).
4. Say "can we continue in English?": the model calls `switch_language("en")`
   (`source: "tool"`), the default voice returns. Keep talking for three more turns and record whether replies
   stay in English (a tool switch leaves only the tool's answer as the reminder; ask #198).
5. Mixed Hinglish for three turns ("mera claim status kya hai, it was filed last week"): the
   language should not flip on every turn (hysteresis). Record the per-turn languages.
6. Captions: in the browser console, `room.registerTextStreamHandler("lkap.captions", …)` (or
   V5-35's block) shows `{id, speaker, text, final, language}` segments: the caller's interims
   then the final with the same `id`; the agent's words arriving with its audio, not ahead of it.
   The `captions` block's state shows `language` changing on each switch.
7. After the call, `GET /v1/sessions/{id}`: each transcript turn has `language` (`hi` / `en`), and
   the session events include every `language_switched`.
8. Without auto-detect (`auto_detect = false`), repeat step 4 with "Hindi mein baat karein?": the
   tool switches and the worker log shows `update_options(language="hi")` reaching the Inference
   STT (the next Hindi turn is transcribed in Devanagari).
9. Compatibility: an existing `Demo — ` agent (no `languages`): no `switch_language` tool in its
   session, no captions stream, instructions unchanged.

## Results

Not run yet.
