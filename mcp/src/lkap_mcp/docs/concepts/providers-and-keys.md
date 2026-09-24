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
vendor. `provider_catalog(provider_id, kind, key_id, query, limit, offset,
model, search_vendor, refresh)` (`kind` is
`"models"|"voices"|"avatars"|"personas"`) returns a `CatalogResponse` whose
labels are `Untrusted` (vendor-supplied) and whose `meta` is trimmed to
pricing, context length, modalities, supported parameters and deprecation.
`query` searches the cached list; `search_vendor=true` forwards it to
OpenRouter's own search (OpenRouter entries only); `model` keeps one model's
voices; `total` counts the matches before paging.

## Custom model ids and Test model

The registry's `models` list is a suggestion list, never an allowlist: any id
the vendor accepts can go in a slot. One rule checks every id (the console,
the api and these tools share it): 1–200 printable characters, no spaces,
no URL, and nothing that looks like an API key — a key-shaped value is
refused with a reason that never repeats it. The tools below check the id
locally and refuse before sending anything. An unknown id is only a warning
until it is tested.

`provider_test_model(provider_id, model, key_id, fields, probes, force)`
makes one real, capped vendor call from the api and records the result on
the workspace (`ModelTestResult`): an LLM answers one word within 4 tokens
(`probes=["basic","tools"]` also forces one tool call; `"vision"` sends a
1x1 image), speech-to-text transcribes a bundled 1 s clip, text-to-speech
says "Hello.", a realtime model completes its handshake, an avatar id is
read. Image models are never generated against. It spends vendor money: at
most 10 tests per workspace per minute and 2 at once (a `rate_limited` error
carries `retry_after`); a repeat within 10 minutes with the same key answers
`cached=true` unless `force=true`. `sample` and every `message` are
`Untrusted`. A pass proves the vendor accepts the id with that key, not that
the LiveKit plugin builds it — the first session still checks that. For
`livekit-inference-llm`, pass `connection_id` to pick the connection that
signs the request (default: the workspace's default connection).

`provider_model_declare(provider_id, model, capabilities)` records what a
model can do (`ModelCapabilities`: vision, tools, audio in/out). Declared
values win over the probe, the live catalog and the registry, and the worker
uses the result: a custom LLM declared or detected text-only stops
receiving camera frames. `lkap_describe("model", "openrouter-llm/openai/gpt-4.1-mini")`
shows the merged view: registry entry, workspace record, catalog item and
capabilities.

## Related tools

`provider_list`, `provider_catalog`, `provider_settings`,
`provider_key_create`, `provider_key_list`, `provider_key_test`,
`provider_test_model`, `provider_model_declare`.

## Related schemas

`ProviderSpec`, `ProviderOut`, `ProviderSettingsIn`, `CatalogItem`,
`CatalogResponse`, `CredentialCreate`, `CredentialOut`, `CredentialTestResult`,
`ModelTestRequest`, `ModelTestResult`, `ProviderModelOut`, `ModelCapabilities`.
