# Providers and keys

The registry (`providers.json`, 126 entries) is the single source of every
vendor plugin the worker can construct: `stt`, `llm`, `tts`, `realtime`,
`avatar`, `image_gen`, `embedding`, `vad`, `turn_detection`,
`noise_cancellation`, plus one `secret_bag` kind (`http-tool-secret`, for
HTTP tool headers/URLs/bodies — see `lkap_explain("tools-http")`). Each entry
(`ProviderSpec`) carries `id`, `label`, `vendor`, `kind`, `models`,
`capabilities`, `requires_credential`, `secret_fields`, `availability`
(`available`/preview/deprecated), `verification` and `worker_image`.

`provider_list(kind=, enabled=, installed_on=, query=)` returns compact rows;
`lkap_describe("provider", id)` returns the full spec. The console's word
for a stored vendor credential is "key"; the api's path (and the tool
family) is `credentials`.

## LiveKit Inference needs no vendor key

`livekit-inference-stt`, `livekit-inference-llm` and `livekit-inference-tts`
run on the LiveKit connection's own credentials — a cascaded agent can be
fully built and tested with zero `provider_key_create` calls. Every other
provider (`deepgram-stt`, `openai-llm`, `elevenlabs-tts`, `google-realtime`,
`bey-avatar`, …) needs a credential first.

## One OpenRouter key

OpenRouter sells LLM, speech-to-text, text-to-speech, embeddings and image
generation behind one key, so five entries share it: `openrouter-llm`,
`openrouter-stt`, `openrouter-tts`, `openrouter-embedding` and
`openrouter-image-gen`. `openrouter-llm` is the key's **home**: the other
four name it in `credential_provider`, a key created for any of them is
stored under `openrouter-llm`, and `provider_key_list(provider_id=
"openrouter-stt")` lists that same row. One `provider_key_create` call covers
the LLM, the workflow and QA judge models, STT, TTS, the knowledge-base
embedder and image generation. It never fills the `realtime` slot: OpenRouter
has no speech-to-speech model.

`openrouter-stt` is batch transcription (no interim results; each turn is
uploaded after end-of-speech, adding roughly half a second to two seconds),
and `openrouter-tts` is non-streaming like `openai-tts`. For the lowest
latency keep LiveKit Inference for STT and TTS and use OpenRouter for the
LLM. `openrouter/auto` is not tool-safe, so the default model is
`openai/gpt-4.1-mini`.

## Creating a key

`provider_key_create(provider_id, label, secrets={NAME: SecretInput},
test=true)` — `secrets`' keys must match the provider's `secret_fields`
exactly (`lkap_describe("provider", id)` lists them, e.g. `api_key` for
`deepgram-stt`, or `api_key`/`project_id` for a Google provider). Each value
is a `SecretInput`: a reference (`env:NAME`, `file:/path#KEY`) or the value
pasted inline — either way it goes to the vault and the response
(`CredentialOut`) carries only a `fingerprint`, never the value.
`test=true` runs a live probe (`CredentialTestResult`) before you rely on it.

`provider_key_list(provider_id=)` and `provider_key_test(key_id)` manage
existing keys; `provider_settings(provider_id, enabled=, default_key_id=)`
turns a provider off workspace-wide or sets which key an agent uses when it
doesn't pick one explicitly.

## Catalogs

Some providers have a dynamic catalog beyond their static `models` list —
voices, avatar personas, or a larger model list fetched live from the
vendor. `provider_catalog(provider_id, kind, key_id, refresh)` (`kind` is
`"models"|"voices"|"avatars"|"personas"`) returns a `CatalogResponse` whose
entries are `Untrusted` (vendor-supplied labels).

## Related tools

`provider_list`, `provider_catalog`, `provider_settings`,
`provider_key_create`, `provider_key_list`, `provider_key_test`.

## Related schemas

`ProviderSpec`, `ProviderOut`, `ProviderSettingsIn`, `CatalogItem`,
`CatalogResponse`, `CredentialCreate`, `CredentialOut`, `CredentialTestResult`.
