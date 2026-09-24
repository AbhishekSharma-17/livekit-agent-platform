# LKAP v4 — OpenRouter as a provider

Status: **decided** (Fable 5.1, 2026-09-25). Findings first, then the design. Package cards V4-03/V4-04 and rulings R-V4-7…9 are in `PLAN-V4.md`.

The user's words: "if you do some research you'll find that OpenRouter provides STT, LLM, TTS and all those things as well, so have an option that everywhere we can use OpenRouter, so that from that one key we can get all of the voice providers and everything."

Short answer: mostly true, with one important exception. OpenRouter today sells **LLM chat, speech-to-text, text-to-speech, embeddings and image generation** behind one key and one base URL. It sells **no realtime (speech-to-speech) model** and **no streaming STT**: its audio endpoints are request/response. So an OpenRouter key can fill every slot of a *cascaded* pipeline plus the QA judge, the workflow model, the KB embedder and image generation; it cannot fill the `realtime` slot, and its STT is a batch transcriber, slower per turn than the streaming STT of LiveKit Inference or Deepgram. The design offers all five OpenRouter entries honestly labelled, and keeps LiveKit Inference (already "one key" through the user's LiveKit project) as the recommended low-latency STT/TTS.

## 1. Findings (accessed 2026-09-25)

Everything below was read from the vendor pages or from the installed plugin source (`agent/.venv`, `livekit-plugins-openai 1.8.2`), or probed live with `curl` against public endpoints with no key. Pages that returned 404 are not cited.

### 1.1 LLM — real, first-class, and LiveKit has a constructor for it

- `POST /api/v1/chat/completions`, OpenAI-shaped; streaming via SSE (`stream: true`); every stream ends with a `usage` chunk before `[DONE]`; keep-alive comment lines `: OPENROUTER PROCESSING` must be skipped by hand-rolled parsers (the OpenAI SDK does). Mid-stream errors arrive as an SSE event with `finish_reason: "error"` under a 200. Cancelling a streaming request stops billing on supported providers. — [API overview](https://openrouter.ai/docs/api-reference/overview), [Streaming](https://openrouter.ai/docs/api-reference/streaming).
- Tool calling: `tools`/`tool_choice`, streaming tool-call deltas, `parallel_tool_calls`. Models are filterable with `supported_parameters=tools`; OpenRouter publishes a per-provider "Tool Call Error Rate" because routed providers differ. The `tools` array must be sent on every request in the loop. — [Tool calling](https://openrouter.ai/docs/guides/features/tool-calling).
- Routing: the `provider` object (`order`, `only`, `ignore`, `allow_fallbacks`, `sort: price|throughput|latency`, `require_parameters`, `data_collection`, `zdr`, `quantizations`, `max_price`, `preferred_min_throughput`, `preferred_max_latency`); model fallbacks via the `models` array; `:nitro` / `:floor` suffixes. Default is price-weighted load balancing avoiding recently failed providers. — [Provider routing](https://openrouter.ai/docs/features/provider-routing), [Latency and performance](https://openrouter.ai/docs/guides/best-practices/latency-and-performance).
- Model ids are `author/slug` (`openai/gpt-4.1-mini`); `:free` is a distinct catalogue entry, `:nitro`/`:floor`/`:online` are routing variants. — [Models](https://openrouter.ai/docs/guides/overview/models).
- Latency: OpenRouter documents its knobs (`preferred_max_latency: {p90: 2.5}`, `sort: "latency"`) and describes TTFT/TPS as the metrics, but publishes no first-party voice-agent numbers. It is an extra HTTP hop in front of the vendor; for a cascaded voice agent that is the same shape as any hosted LLM and acceptable, provided a low-latency model is chosen and `sort: latency` or a concrete `order` is set.
- Attribution headers: `HTTP-Referer` (app URL, the primary identifier) and `X-OpenRouter-Title` (display name; `X-Title` still supported). The title only counts when paired with `HTTP-Referer`. Optional. — [App attribution](https://openrouter.ai/docs/app-attribution).
- **LiveKit Agents 1.8.2**: `livekit.plugins.openai.LLM.with_openrouter(*, model="auto", api_key, base_url="https://openrouter.ai/api/v1", site_url, app_name, fallback_models, provider: OpenRouterProviderPreferences, plugins, temperature, parallel_tool_calls, tool_choice, reasoning_effort, top_p, timeout, …)` — a `@staticmethod` in the installed `llm.py` (lines 443–510). It maps `site_url→HTTP-Referer`, `app_name→X-Title`, `provider→extra_body.provider`, `fallback_models→extra_body.models=[model, *fallback_models]`, and falls back to `OPENROUTER_API_KEY` only when no `api_key` kwarg is given (our factory always passes one). `OpenRouterProviderPreferences` is a `TypedDict(total=False)` with `order, allow_fallbacks, require_parameters, data_collection, only, ignore, quantizations, sort, max_price` (`models.py:303`); it is not validated at runtime, so newer keys such as `preferred_max_latency` pass through. LiveKit's doc page confirms the same surface. — [LiveKit: OpenRouter LLM plugin](https://docs.livekit.io/agents/models/llm/openrouter/), [OpenRouter: LiveKit integration](https://openrouter.ai/docs/guides/community/livekit) (both pages are LLM-only; neither mentions STT or TTS through OpenRouter).
- Two plugin details that matter for routed non-OpenAI models: `with_openrouter` leaves `_strict_tool_schema=True` (the plugin's `with_cerebras`/`with_x_ai` constructors set it `False`, `llm.py:306,351`), so tool definitions go out with OpenAI's `strict` flag; and `openrouter/auto` is **not** in the `supported_parameters=tools` list (probe below), so `auto` is not a safe default for an agent with tools.

### 1.2 Speech-to-text — real, but request/response only

- `POST /api/v1/audio/transcriptions`, announced 2026-05-01, launched 2026-07-22. Accepts OpenAI-style `multipart/form-data` (`file`, ≤ 25 MB) or JSON `input_audio{data,format}`; fields `model`, `language` (ISO-639-1), `temperature`, `response_format: json|verbose_json`, `timestamp_granularities`, `provider.options`. Returns `{text, usage{seconds,tokens,cost}}`. **"Streaming: not supported. The endpoint operates as request/response only — no SSE, WebSocket, or chunked streaming."** Routing preferences `order/only/ignore` are *not* applied to transcription. ~60 s upstream processing timeout. "If you already have a client built for OpenAI's `/v1/audio/transcriptions`, you can point its base URL at `https://openrouter.ai/api/v1` and it works unchanged." — [STT guide](https://openrouter.ai/docs/guides/overview/multimodal/stt), [Audio APIs announcement](https://openrouter.ai/blog/announcements/announcing-audio-apis/), [Transcription tutorial](https://openrouter.ai/blog/tutorials/transcription-on-openrouter/).
- Probe: `GET /api/v1/models?output_modalities=transcription` → 22 models, including `openai/gpt-4o-mini-transcribe`, `openai/gpt-4o-transcribe`, `openai/whisper-1`, `openai/whisper-large-v3-turbo`, `deepgram/nova-3`, `assemblyai/universal-3-5-pro`, `google/chirp-3`, `mistralai/voxtral-mini-transcribe`, `microsoft/mai-transcribe-2`. Note that `deepgram/nova-3` here is Deepgram's model behind a batch HTTP call, not Deepgram's streaming websocket.
- Audio *input* on chat models (`input_audio` content parts, base64 only) exists but is a chat-completion call, not a transcriber, and the LiveKit LLM plugin never sends audio. — [Audio](https://openrouter.ai/docs/features/multimodal/audio).
- **LiveKit fit**: `livekit.plugins.openai.STT(model, base_url, api_key, language, use_realtime=…)` has two paths. `use_realtime=True` opens a websocket at `{base_url}/realtime?…` (`stt.py:586`) — OpenRouter has no `/realtime` path (its `openapi.json` path list has none). `use_realtime=False` is a batch `client.audio.transcriptions.create(file=…, model=…, language=…, response_format="json"|"verbose_json")` (`stt.py:633`) that the framework wraps in a VAD-driven `StreamAdapter`: audio is buffered until Silero's end-of-speech, uploaded, transcribed, and the final transcript arrives with **no interim results**. `use_realtime` defaults to `_is_realtime_only(model)` (prefixes `gpt-realtime-whisper`, `gpt-live-transcribe`), so any `author/slug` OpenRouter id silently selects batch mode. The plugin already ships exactly this shape for OVHcloud (`STT.with_ovhcloud`: Whisper over a `base_url`, `use_realtime=False`, `stt.py:403`). So OpenRouter STT **works in LiveKit** and is offered, with the latency cost stated plainly: every turn pays upload + model time after end-of-speech, and the turn detector sees only final transcripts.

### 1.3 Text-to-speech — real, parity with the existing `openai-tts` entry

- `POST /api/v1/audio/speech`, "fully compatible with the OpenAI SDK" via `base_url=https://openrouter.ai/api/v1`; fields `model`, `input`, `voice` (provider-dependent), `response_format: mp3|pcm` (default `pcm`), `speed`, `input_references` (voice cloning), `provider.options`. Returns raw audio bytes (`audio/mpeg` or `audio/pcm`) with an `X-Generation-Id` header; the tutorial reads it through `with_streaming_response` for progressive playback. — [TTS guide](https://openrouter.ai/docs/guides/overview/multimodal/tts), [TTS tutorial](https://openrouter.ai/blog/tutorials/text-to-speech/) (2026-09-11).
- Probe: `GET /api/v1/models?output_modalities=speech` → 20 models: `google/gemini-3.8-flash-tts`, `google/gemini-3.8-flash-lite-tts`, `google/gemini-3.1-flash-tts-preview`, `deepgram/aura-2`, `deepgram/flux-tts:free`, `microsoft/mai-voice-2`, `microsoft/mai-voice-2-flash`, `x-ai/grok-voice-tts-1.0`, `mistralai/voxtral-mini-tts-2603`, `fish-audio/s2-pro`, `minimax/speech-2.8-turbo`, `hexgrad/kokoro-82m`, `canopylabs/orpheus-3b-0.1-ft`, `sesame/csm-1b`, … Each item carries a top-level `supported_voices` list (e.g. Gemini: `Zephyr, Puck, Charon, Kore, Fenrir, …`; Grok: `eve, ara, rex, sal, leo`; MAI: `en-US-Harper:MAI-Voice-2`). **`openai/gpt-4o-mini-tts` is not in the list today**, so the LiveKit plugin's default model and default voice (`ash`) are meaningless on OpenRouter and the registry must set its own.
- Audio *output* from chat models (`modalities: ["text","audio"]`, SSE `delta.audio` chunks) exists but the LiveKit LLM plugin ignores audio deltas; not applicable.
- **LiveKit fit**: `livekit.plugins.openai.TTS(model, voice, speed, instructions, base_url, api_key, response_format)` declares `TTSCapabilities(streaming=False)` and calls `client.audio.speech.with_streaming_response.create(model, voice, input, response_format, speed, instructions=<omitted when unset>)` (`tts.py:261`). Default `response_format="mp3"` is one of OpenRouter's two formats. So `openai.TTS(base_url=…)` against OpenRouter is the same non-streaming, sentence-at-a-time shape the platform already offers as `openai-tts`. Offered.

### 1.4 Realtime / speech-to-speech — none

- The `openapi.json` path list (probed) contains `/chat/completions`, `/audio/speech`, `/audio/transcriptions`, `/embeddings`, `/images`, `/videos`, `/responses`, `/messages`, `/rerank`, `/models`, `/key`, … and **no websocket or `/realtime` path**. OpenRouter's audio docs describe SSE-streamed audio *output* only; there is no full-duplex session. The `openai-realtime` registry entry's `base_url` field ("override for OpenAI-compatible realtime endpoints") must therefore never be pointed at OpenRouter; the `realtime` slot stays with Gemini Live / OpenAI Realtime / xAI / Nova Sonic on their own keys.

### 1.5 Embeddings — real, OpenAI-shaped

- `POST /api/v1/embeddings` with `model`, `input` (text, list, or multimodal `content` arrays), `encoding_format`; response `data[].embedding`. — [Embeddings API](https://openrouter.ai/docs/api-reference/embeddings).
- Probe: `output_modalities=embeddings` → 37 models, including `openai/text-embedding-3-small`, `openai/text-embedding-3-large`, `google/gemini-embedding-2`, `voyageai/voyage-4-*`, `qwen/*`.
- **LKAP fit**: `lkap_api.kb.embed.OpenAIEmbedder(api_key, *, model, base_url, …)` already takes `base_url` and `model`; its `dimension` is the class constant 1536, so only `openai/text-embedding-3-small` (1536 dims via OpenRouter as via OpenAI) is listed. `resolve_embedder` today only understands `LKAP_EMBEDDER=fastembed|openai:<credential_id>` (`embed.py:203`) — a small generalisation is needed. Note that the embedder is **platform-level configuration** (one setting for the api process), not a per-agent slot; that is unchanged.

### 1.6 Image generation — real, but not OpenAI-compatible

- `POST /api/v1/images` (announced 2026-06-23) with `model`, `prompt`, `n`, `resolution: 512|1K|2K|4K`, `aspect_ratio`, `size` (a tier or explicit pixels), `quality`, `output_format`, `background`, `input_references`, `stream`; response `{data: [{b64_json, media_type}], usage{cost}}`. Model list at `/api/v1/images/models`. The docs make no OpenAI Images API compatibility claim and the path is `/images`, not `/images/generations`. — [Image generation guide](https://openrouter.ai/docs/guides/overview/multimodal/image-generation), [Unified Image API announcement](https://openrouter.ai/blog/announcements/image-api/).
- Probe: `output_modalities=image` → 57 models, including `openai/gpt-image-1`, `openai/gpt-image-1-mini`, `openai/gpt-image-2`, `google/gemini-3.1-flash-image`, `google/gemini-2.5-flash-image`.
- **LKAP fit**: `lkap_agent.providers.image_gen.OpenAIImageGen` builds `AsyncOpenAI(api_key=api_key)` with no `base_url` and calls `images.generate` (`/images/generations`), so it cannot be pointed at OpenRouter. A small `OpenRouterImageGen` class is required (§2.6).

### 1.7 Key and catalogue endpoints (the credential test)

- `GET /api/v1/key` returns the key's `label`, `limit`, `limit_remaining`, `limit_reset`, `usage`, `usage_daily/weekly/monthly`, `is_free_tier`, `free_model_daily_requests`. — [Limits](https://openrouter.ai/docs/api-reference/limits). Probe: no key → 401; `Bearer sk-or-v1-invalid` → 401 `{"error":{"message":"User not found.","code":401}}`.
- `GET /api/v1/models` with `category`, `supported_parameters`, `input_modalities`, `output_modalities` (`text, image, embeddings, audio, video, rerank, decisions, speech, transcription`, or `all`); per-model `id`, `name`, `architecture.{input_modalities,output_modalities}`, `supported_parameters`, `pricing`, `context_length`, `top_provider`, plus (probed) `supported_voices`, `per_request_limits`, `description`. — [List models](https://openrouter.ai/docs/api-reference/models/get-models). **Probe: `/models` is public — a bogus bearer token still gets 200.** It therefore cannot be the credential test on its own (OpenAI's `/v1/models` 401s on a bad key, which is why `openai_models` works as a test today).

### 1.8 What is real versus what was assumed

| Assumption | Reality |
|---|---|
| "OpenRouter provides STT" | Yes, batch transcription (`/audio/transcriptions`), 22 models. Not streaming. Usable in LiveKit as a VAD-chunked batch STT with no interim results. |
| "OpenRouter provides TTS" | Yes (`/audio/speech`), 20 models, OpenAI-SDK compatible. Same non-streaming shape as the platform's `openai-tts`. |
| "OpenRouter provides LLM" | Yes; first-class LiveKit support (`LLM.with_openrouter`). |
| "…and all those things" | Embeddings and image generation: yes. Realtime speech-to-speech: **no**. Avatars, VAD, turn detection, noise cancellation: not OpenRouter's business. |
| "one key for everything" | One OpenRouter key covers `llm`, `workflow_llm`, `qa_llm`, `stt`, `tts`, the KB embedder and `image_gen`. With the platform's per-provider credential rows it would have to be pasted five times — that is the platform gap R-V4-7 closes. The `realtime` slot and every vendor-specific avatar keep their own keys. |

Recommendation for "one key": **OpenRouter for the LLM family (llm, workflow, QA judge, flow node overrides), embeddings and images; LiveKit Inference for STT and TTS** — the latter is already one key through the user's LiveKit project and is streaming. OpenRouter STT/TTS are offered for people who want every vendor bill on one invoice and accept the per-turn latency.

## 2. Design

Decisions continue `TEMPLATES.md`'s numbering at D-V4-9.

### D-V4-9 — Five registry entries on the slim image, all built from what the slim image already carries

New entries in `contracts/src/lkap_contracts/providers.py`, defined in a `_OPENROUTER_AVAILABLE` list and shipped through the existing `_shipped()` helper (so `worker_image="slim"`, `verification="unverified"`), then placed in `REGISTRY = [*_MVP, *_OPENROUTER, *_FULL, *_NEW, *_DEFERRED]` so the ordered `EXPECTED_MVP_IDS` equality in `contracts/tests/test_registry.py` extends by appending. `slim.txt` already lists `livekit-plugins-openai==1.8.2` and the `openai` SDK is a base dependency, so `scripts/gen_plugin_requirements.py` regenerates identical requirement files and `check_installed_providers.py` imports the five `python_class` paths on the slim build (its `_resolve` already walks classmethod paths).

| id | kind | `python_class` | `credential_provider` | default model | catalog / test adapter |
|---|---|---|---|---|---|
| `openrouter-llm` | llm | `livekit.plugins.openai.LLM.with_openrouter` | — (the home) | `openai/gpt-4.1-mini` | `openrouter_llm_models` |
| `openrouter-stt` | stt | `livekit.plugins.openai.STT` | `openrouter-llm` | `openai/gpt-4o-mini-transcribe` | `openrouter_stt_models` |
| `openrouter-tts` | tts | `livekit.plugins.openai.TTS` | `openrouter-llm` | `google/gemini-3.8-flash-tts` | `openrouter_tts_models` (kinds `models`, `voices`) |
| `openrouter-embedding` | embedding | `lkap_api.kb.embed.OpenAIEmbedder` | `openrouter-llm` | `openai/text-embedding-3-small` | `openrouter_embedding_models` |
| `openrouter-image-gen` | image_gen | `lkap_agent.providers.image_gen.OpenRouterImageGen` | `openrouter-llm` | `openai/gpt-image-1` | `openrouter_image_models` |

Common: `vendor="OpenRouter"`, `secret_fields=[_api_key("OpenRouter API key", env="OPENROUTER_API_KEY")]`, `get_key_url="https://openrouter.ai/settings/keys"` (200 on HEAD), `price_ref=None` (pricing is per routed model; the catalog `meta.pricing` carries it).

Fields per entry (every name checked against the 1.8.2 signatures in §1; the `test_field_names_are_accepted_by_the_real_constructor` gate re-checks them):

- **`openrouter-llm`** — `temperature` (number, 0.7); `fallback_models` (json, help: "Model ids tried in order when the primary is unavailable; sent as OpenRouter's `models` array"); `provider` (json, help: "OpenRouter provider preferences: `order`, `only`, `ignore`, `sort` (price/throughput/latency), `allow_fallbacks`, `require_parameters`, `data_collection`, `preferred_max_latency`…; `require_parameters` defaults to true so a request with tools never lands on an endpoint that cannot call them"); `site_url` (string, help: "Sent as `HTTP-Referer` for OpenRouter's app rankings; the app name below only counts when this is set"); `app_name` (string, default `LKAP`, sent as `X-Title`). `models`: `openai/gpt-4.1-mini` (default), `openai/gpt-4.1`, `openai/gpt-4o-mini`, `google/gemini-3.5-flash`, `anthropic/claude-sonnet-4.6` — all in the `supported_parameters=tools` probe. `supports_video` stays `False` on every entry until verified (the rule on `ModelSpec`). `notes`: "Routes to hundreds of models on one key; `openrouter/auto` is not tool-safe and is deliberately not the default. Tool schemas go out with OpenAI's `strict` flag; if a routed non-OpenAI model rejects a tool call, pick an OpenAI model or an `order` of providers known to support strict tools." `docs_url` = the LiveKit OpenRouter page. `capabilities` default (`tool_calling=True`).
- **`openrouter-stt`** — `base_url` (string, default `https://openrouter.ai/api/v1`), `language` (string, `en`). `use_realtime` is **not** exposed: the plugin derives `False` for every `author/slug` id, and exposing it would let an admin request a websocket OpenRouter does not have. `models`: `openai/gpt-4o-mini-transcribe` (default), `openai/whisper-large-v3-turbo`, `openai/gpt-4o-transcribe`, `deepgram/nova-3`, `mistralai/voxtral-mini-transcribe`. `notes`: "Batch transcription over HTTP: no interim results; each turn is transcribed after end-of-speech, so expect roughly half a second to two seconds more per turn than a streaming STT. For low latency prefer LiveKit Inference STT or Deepgram." `docs_url` = the OpenAI STT LiveKit page (the class it uses).
- **`openrouter-tts`** — `base_url` (default as above), `voice` (`type="catalog"`, `catalog_kind="voices"`, default `Kore`), `speed` (number, 1.0). `models`: `google/gemini-3.8-flash-tts` (default), `google/gemini-3.8-flash-lite-tts`, `deepgram/aura-2`, `mistralai/voxtral-mini-tts-2603`, `x-ai/grok-voice-tts-1.0`. `capabilities=ProviderCapabilities(voices_dynamic=True)`. `notes`: "Voices are per model — pick the model first, then a voice it lists. Non-streaming, like OpenAI TTS: one request per sentence."
- **`openrouter-embedding`** — `base_url` (default as above). `models`: `openai/text-embedding-3-small` only (the embedder's dimension is fixed at 1536; another model means re-embedding every KB). `capabilities=ProviderCapabilities(tool_calling=False)`. `notes` says how to select it (`LKAP_EMBEDDER=openrouter-embedding:<credential_id>`, D-V4-13).
- **`openrouter-image-gen`** — `resolution` (enum `512|1K|2K|4K`, default `1K`), `aspect_ratio` (string, `1:1`). `models`: `openai/gpt-image-1` (default), `openai/gpt-image-1-mini`, `google/gemini-3.1-flash-image`. `capabilities=ProviderCapabilities(tool_calling=False)`, `package="openai"` (the class uses the SDK's generic `post`, no new dependency).

Rejected: a single `openrouter` entry whose kind switches per slot (the registry, factory and pickers are keyed by `kind`; one id per kind is the platform's shape); `worker_image="full"` to dodge the MVP pin (dishonest — slim builds them); marking STT/TTS `deferred` because they are batch (they work; the note and R-V4-8 carry the caveat).

### D-V4-10 — One credential row serves all five: `ProviderSpec.credential_provider`

The platform scopes every credential lookup by `provider_id`: `Credential.provider_id`, `config_service._validate_credential` ("credential … belongs to provider X, not Y"), `_unambiguous_credential` in seeding, `routers/providers.py::_credential_for`/`_resolve_credential` (catalog fetch, default-credential setting), `routers/provider_keys.py::list_credentials(provider_id=)`, `templates/seed.py::requirements_from_pipeline`, and the console's `credential-picker.tsx`. Without a rule, "one key" means pasting the same key five times.

- `ProviderSpec.credential_provider: str | None = None` (additive, exported). Set on the four non-LLM OpenRouter entries to `"openrouter-llm"`, the **credential home**. A `model_validator` on the registry (in a `contracts/tests/test_registry.py` test, since `REGISTRY` is a list) checks: the home exists, has no `credential_provider` of its own, and declares the same `secret_fields` names.
- `lkap_contracts.providers.credential_home(spec_or_id) -> str` returns `spec.credential_provider or spec.id`. Every site above compares `Credential.provider_id == credential_home(spec)` instead of `== spec.id`; `requirements_from_pipeline` keys its dict by the home so a template with three OpenRouter slots reports one required key; `list_credentials(provider_id=X)` returns rows stored under `credential_home(X)`.
- A provider-key create (`routers/provider_keys.py::create_credential`) with `provider_id: "openrouter-stt"` **stores the row under the home** (`provider_id="openrouter-llm"`), after `_check_secrets` against the alias's own (identical) `secret_fields`. So "Add key" from any OpenRouter row lands in one place and the console's LLM tab shows it. `CredentialOut.provider_id` is the stored (home) id.
- The worker is untouched: it receives resolved kwargs, never credential ids.
- Existing vendors with several entries on one key (OpenAI ×6, Google, Deepgram, ElevenLabs, Cartesia) are **not** migrated here; adopting `credential_provider` for them is an ask (a data migration would have to merge existing rows). R-V4-7 records the rule.

Rejected: a `secret_bag`-style "OpenRouter key" entry with no kind (the credential dialog and pickers are per kind; users would not find it); resolving aliases in the console only (validation would still reject the slot).

### D-V4-11 — Catalog and credential test: one adapter class that probes `/key` and then lists filtered models

`get_catalog` looks up the adapter by `spec.test` and the docstring rule is `test == catalog.adapter`, so the credential test *is* the catalog fetch. Because `/models` is public (§1.7), a plain `HttpCatalogAdapter` on `/models` would report a wrong key as "OK". New in `api/src/lkap_api/catalogs/adapters.py` (or a sibling `catalogs/openrouter.py`, Opus's choice):

```python
@dataclass(frozen=True, slots=True)
class OpenRouterCatalogAdapter:
    vendor: str = "OpenRouter"
    filter: str = ""                 # e.g. "supported_parameters=tools"
    voices: bool = False             # flatten supported_voices for kind="voices"
    async def fetch(self, *, client, secrets, kind) -> list[CatalogItem]:
        # 1. GET https://openrouter.ai/api/v1/key with Bearer <api_key>; non-2xx → CatalogAdapterError("OpenRouter rejected the key")
        # 2. GET https://openrouter.ai/api/v1/models?<filter>; parse_items(body, label_keys=("name",)) — meta keeps pricing, context_length, architecture, supported_parameters, supported_voices
        # 3. kind == "voices": one CatalogItem per (model, voice): id=voice, label=f"{voice} · {model name}", meta={"model": model_id}
```

Five registrations: `openrouter_llm_models` (`supported_parameters=tools`), `openrouter_stt_models` (`output_modalities=transcription`), `openrouter_tts_models` (`output_modalities=speech`, `voices=True`), `openrouter_embedding_models` (`output_modalities=embeddings`), `openrouter_image_models` (`output_modalities=image`). The `_resolve_credential` change of D-V4-10 means the STT entry's catalog authenticates with the home's key. Requests go through `HttpClientDep`, the net-guarded client; `openrouter.ai` resolves to public addresses and needs no policy change. Cache: the existing `provider_catalog_cache` keyed by provider id, credential id and kind (1 h). The credential test message stays the generic "OpenRouter responded with N item(s)" with the first five as `catalog_preview`.

Limitation accepted for v1: the TTS voice picker lists every voice of every speech model with a model-qualified label, because the registry-form combobox cannot filter a `voices` catalog by the chosen model. A follow-up can pass the selected model as `meta` context.

### D-V4-12 — "Everywhere": which slots OpenRouter fills, and the two code paths that need a line

| Slot / consumer | How it reaches OpenRouter | Change |
|---|---|---|
| `pipeline.llm` (cascaded) | factory → `LLM.with_openrouter(api_key, model, temperature, fallback_models, provider, site_url, app_name)` | registry only, plus a factory `setdefault` (below) |
| `pipeline.workflow_llm` (`PromptJsonStructuredLLM`) | same factory build, `SLOT_KINDS["workflow_llm"]="llm"` | none |
| `qa_llm` in the worker (`qa.build_judge`) | same factory build | none |
| QA judge in the api process (`qa/resolve.py`) | `OpenAiCompatibleJudgeLLM(base_url="https://openrouter.ai/api/v1")` | add `"openrouter-llm"` to `_OPENAI_COMPATIBLE_DEFAULT_BASE_URL`; `net_guard.check_url` passes |
| Flow node overrides (`flow/providers.py`) | rule 1: same provider and credential as `llm`/`workflow_llm`/`qa_llm` → cheaper OpenRouter model per node | none |
| `pipeline.stt`, `pipeline.tts` (cascaded, half-cascade `tts`) | factory → `openai.STT`/`openai.TTS` with `base_url` | registry only |
| `pipeline.realtime` | not possible (§1.4) | none; `notes` on `openai-realtime` gains "not OpenRouter" |
| KB embedder | `resolve_embedder` | D-V4-13 |
| `pipeline.image_gen` | `OpenRouterImageGen` | §2.6 |
| Templates' "Needs keys" | `requirements_from_pipeline` via `credential_home` | D-V4-10 |
| Console pickers, Providers catalog, MCP `provider_list`/`lkap_describe("provider")` | registry-driven | V4-04 for the picker alias only |

Factory special case (3 lines next to the `livekit-inference-llm` one in `ProviderFactory._constructor_kwargs`): for `spec.id == "openrouter-llm"`, `provider = dict(kwargs.get("provider") or {}); provider.setdefault("require_parameters", True); kwargs["provider"] = provider`. Failure mode this prevents: a tool-bearing request routed to an endpoint that ignores `tools`, which surfaces as the agent "answering" instead of calling the tool. An admin who wants the cheaper routing sets `provider: {"require_parameters": false}` explicitly.

### D-V4-13 — Embedder selection generalises to `<provider_id>:<credential_id>`

`resolve_embedder` accepts `fastembed`, the legacy `openai:<credential_id>` (kept, maps to `openai-embedding`), and `<provider_id>:<credential_id>` for any registry entry of kind `embedding` whose `python_class` is `lkap_api.kb.embed.OpenAIEmbedder`; it builds `OpenAIEmbedder(api_key, model=spec.default_model, base_url=<the entry's base_url field default>)`. The credential lookup stays cross-workspace by id (it is platform configuration) but now also checks `Credential.provider_id == credential_home(spec)`. Documented in `docs/RUNBOOK.md` next to `LKAP_EMBEDDER`; `deploy/api.env.example` is left alone.

### 2.6 `OpenRouterImageGen`

In `agent/src/lkap_agent/providers/image_gen.py`, beside `OpenAIImageGen`: `__init__(api_key, model, resolution="1K", aspect_ratio="1:1", site_url=None, app_name="LKAP")`; `generate(prompt, *, timeout_s)` posts `{"model", "prompt", "resolution", "aspect_ratio", "n": 1}` to `/images` through `AsyncOpenAI(api_key=…, base_url="https://openrouter.ai/api/v1", default_headers=<attribution>).post("/images", body=…, cast_to=httpx.Response)` (the SDK's generic request method; no new dependency), and returns `(base64.b64decode(data[0].b64_json), data[0].media_type)`. Same timeout/log/`ImageGenError` discipline as the OpenAI class. Registry fields `resolution` and `aspect_ratio` are its kwargs; `model` comes from the slot.

### 2.7 Attribution, tools and other caveats, in one place

- Attribution is opt-in: `app_name` defaults to `LKAP`, `site_url` is empty until an admin sets it; the console help says the title only counts with a referer. Nothing in the platform's own prose or defaults names a real hostname besides OpenRouter's (`get_key_url`, `base_url` defaults — the same as every other vendor entry).
- Tool calling: the LLM catalog is pre-filtered to `supported_parameters=tools`; `require_parameters` defaults on; the static model list is tool-capable; `openrouter/auto` is not offered. The strict-schema flag is a live-check item (§3), not a code change: if it bites, the fix is a `_strict_tool_schema=False` pass-through (the plugin has no public knob; Opus files an ask rather than monkey-patching).
- Free-tier keys: `:free` model variants are rate-limited (20 req/min, 50 or 1000 req/day); the catalog `meta.pricing` shows `0`; nothing in LKAP treats them specially.
- Slim image: no new package anywhere (contracts, api, agent). Existing built workers report the ids baked into their `installed_providers.json` at build time, so an already-running slim worker shows the OpenRouter entries as "not installed on this connection" until its image is rebuilt; a `uv run` dev worker discovers installed ids by import and sees them immediately.
- Realtime STT websocket, `input_audio`, chat audio output: exist on OpenRouter or in the plugin, deliberately unused (§1.2–1.4).

## 3. Live check (with the user's key, after V4-03 lands)

The user adds the key once, in the console: Providers → LLM → OpenRouter → Add key (`get_key_url` opens OpenRouter's keys page). Then, on a scratch api/worker per the live rules:

1. The provider-key test route (`routers/provider_keys.py::test_credential`, the console's "Test" button) → `ok`, message names the tool-capable model count, preview shows five ids. A deliberately wrong key → `ok: false` (proves the `/key` probe).
2. Catalogs: `GET /v1/providers/openrouter-{llm,stt,tts,embedding,image-gen}/catalog` return vendor lists; `openrouter-tts?kind=voices` lists `Kore · Gemini 3.8 Flash TTS`. The STT/TTS/embedding/image entries authenticate with the one home row.
3. Cascaded agent from `blank`: `llm=openrouter-llm/openai/gpt-4.1-mini` with Inference STT/TTS; MCP `chat_start`/`chat_send` with an HTTP tool → the tool is called. Repeat with `anthropic/claude-sonnet-4.6` and `google/gemini-3.5-flash` (the strict-schema check). Record TTFT from `SessionLatency`.
4. Same agent with `stt=openrouter-stt` and `tts=openrouter-tts/google/gemini-3.8-flash-tts/Kore`: a 60-second audio session; record end-of-speech-to-first-audio versus step 3.
5. QA: enable QA with `qa.model=openrouter-llm` → `session_qa` row scored by the api-process judge.
6. Embeddings: scratch api with `LKAP_EMBEDDER=openrouter-embedding:<credential_id>` → a KB import and a search hit.
7. Image: `image_gen=openrouter-image-gen`, the image block, one generation.

Recorded in `docs/v4/_briefs/v4-03-live.md`; `VERIFIED_IDS` gains only what passed.

## 4. Sources

OpenRouter (all accessed 2026-09-25): [API overview](https://openrouter.ai/docs/api-reference/overview) · [API reference index](https://openrouter.ai/docs/api/reference/overview) · [Streaming](https://openrouter.ai/docs/api-reference/streaming) · [Provider routing](https://openrouter.ai/docs/features/provider-routing) · [Latency and performance](https://openrouter.ai/docs/guides/best-practices/latency-and-performance) · [Tool calling](https://openrouter.ai/docs/guides/features/tool-calling) · [App attribution](https://openrouter.ai/docs/app-attribution) · [Models](https://openrouter.ai/docs/guides/overview/models) · [List models](https://openrouter.ai/docs/api-reference/models/get-models) · [Limits / `GET /key`](https://openrouter.ai/docs/api-reference/limits) · [Audio (chat input/output)](https://openrouter.ai/docs/features/multimodal/audio) · [Speech-to-text](https://openrouter.ai/docs/guides/overview/multimodal/stt) · [Text-to-speech](https://openrouter.ai/docs/guides/overview/multimodal/tts) · [Audio APIs announcement, 2026-05-01](https://openrouter.ai/blog/announcements/announcing-audio-apis/) · [Transcription tutorial, 2026-07-22](https://openrouter.ai/blog/tutorials/transcription-on-openrouter/) · [TTS tutorial, 2026-09-11](https://openrouter.ai/blog/tutorials/text-to-speech/) · [Embeddings](https://openrouter.ai/docs/api-reference/embeddings) · [Image generation](https://openrouter.ai/docs/guides/overview/multimodal/image-generation) · [Unified Image API, 2026-06-23](https://openrouter.ai/blog/announcements/image-api/) · [LiveKit integration](https://openrouter.ai/docs/guides/community/livekit) · `https://openrouter.ai/openapi.json` (path list, curl).

LiveKit: [OpenRouter LLM plugin](https://docs.livekit.io/agents/models/llm/openrouter/) (accessed 2026-09-25); installed `livekit-plugins-openai==1.8.2` source in `agent/.venv` (`llm.py`, `stt.py`, `tts.py`, `models.py`, `realtime/realtime_model.py`).

Live probes (curl, 2026-09-25, no key or a bogus key): `GET /api/v1/models?output_modalities=transcription|speech|embeddings|image`, `?supported_parameters=tools`, unfiltered (`458` models, `openrouter/auto` present, absent under the tools filter); `GET /api/v1/key` → 401 without and with a bogus key; `GET /api/v1/models` → 200 with a bogus key; `GET https://api.openai.com/v1/models` → 401 with a bogus key; `HEAD https://openrouter.ai/settings/keys` → 200.
