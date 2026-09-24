# Pipeline modes

`AgentConfig.pipeline` (`PipelineConfig`) has a `mode`: `cascaded`,
`realtime` or `half_cascade`. Each mode requires a different set of provider
slots to be filled before the agent can run; `agent_validate` (and, offline,
`agent_flow_validate`) reports a missing slot as an `error` `Issue` at
`pipeline.<slot>`.

| Mode | Required slots | What it means |
|---|---|---|
| `cascaded` | `stt`, `llm`, `tts` | Separate speech-to-text, a text LLM, text-to-speech. Works with LiveKit Inference alone (no vendor key needed): `livekit-inference-stt`, `livekit-inference-llm`, `livekit-inference-tts`. |
| `realtime` | `realtime` | One speech-to-speech model handles the whole turn, e.g. `google-realtime` (Gemini Live) or `openai-realtime`. Needs a vendor credential. |
| `half_cascade` | `realtime`, `tts` | A realtime model generates text only (no native audio out); a separate TTS provider speaks it. Rare; check the provider's `capabilities.text_modality` before picking one for this mode. |

Every mode may also fill optional slots: `avatar` (a talking-head vendor,
plus `avatar_options`), `image_gen` (for packs that produce images, e.g. the
insurance pack's incident sketches), `workflow_llm` (a second LLM for
background work, separate from the conversational one), `vad`,
`turn_detection`, `noise_cancellation`. A `qa_llm` slot is resolved by the
api only when `qa.enabled` is true, walking `qa.model` → `workflow_llm` →
`llm` → the Inference default — you never set it directly
(`lkap_explain("qa-and-evals")`).

Each provider entry (`provider_list`) declares which `kind` it fills (`stt`,
`llm`, `tts`, `realtime`, `avatar`, `image_gen`, `embedding`, `vad`,
`turn_detection`, `noise_cancellation`, or `secret_bag` for HTTP tool
secrets) and a `default_model`. `agent_create`/`agent_update` reference a
provider with a `ProviderRef{provider_id, model}`; `provider_list(kind=...)`
or `lkap_describe("provider", id)` shows the exact model list and whether a
credential is required (`requires_credential`, `secret_fields`).

## Vision

A pack that turns the camera or screen share on (`config.capabilities`)
should pick an LLM or realtime model whose `ModelSpec.supports_video` is
true; a text-only model paired with camera input silently ignores frames.
`lkap_describe("provider", id)` lists each model's `supports_video`.

## Switching modes

`agent_update(patch={"pipeline": {"mode": "realtime", "realtime":
{"provider_id": "google-realtime", "model": "gemini-3.8-live"}}})`
merges over the existing pipeline; slots from the old mode that the new mode
doesn't need are simply unused, not removed. Always `agent_validate`
afterwards.

## Related tools

`agent_create`, `agent_update`, `agent_validate`, `provider_list`,
`provider_catalog`, `lkap_describe`.

## Related schemas

`AgentConfig`, `ProviderSpec`, `ResolvedProvider`, `QaConfig`, `AvatarOptions`.
