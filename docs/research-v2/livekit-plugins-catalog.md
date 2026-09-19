# LiveKit Agents (Python) — Complete Plugin Catalog v2

Research date: **2026-09-19**. Status: **complete**.

This document **extends** `docs/research/providers-and-avatars.md` (dated 2026-09-17, verified against
livekit-agents 1.8.2) and the current MVP registry at
`livekit_agent_platform/contracts/src/lkap_contracts/providers.py`. It does not repeat what those two
already establish correctly; it adds everything they left out (54 additional provider packages) and
corrects two claims in the v1 doc that source-reading disproved (see §0.3).

## 0. Method and primary sources

- **Cloned, not fetched-per-file**: `git clone --depth 1 --branch livekit-agents@1.8.2 github.com/livekit/agents`
  and a second shallow clone of `main` (both on 2026-09-19). Tag resolved via `git ls-remote --tags`.
- **Installed SDK cross-check**: the project's own venv at `livekit_agent_platform/agent/.venv` was checked
  directly — `site-packages/livekit_agents-1.8.2.dist-info` confirms the installed core is exactly
  **1.8.2**, matching the cloned tag (not main). The venv's `livekit/plugins/` directory only has 8
  packages installed (`bey, cartesia, deepgram, elevenlabs, google, openai, silero, tavus`) — i.e. this
  project has installed a subset matching its current MVP registry, not the full 70-package catalog; every
  provider in this document beyond those 8 was verified against the cloned source tree, not the local venv.
- **`main` vs `1.8.2` diff**: `diff <(ls agents-1.8.2/livekit-plugins) <(ls agents-main/livekit-plugins)` is
  **empty** — main is 4 days ahead of the 1.8.2 tag (released 2026-09-15) and has added no new plugin
  directories yet. Every plugin in this catalog is present in **both**; there is no "main-only" column
  because there is nothing to put in it as of this research date.
- **Constructor signatures**: parsed with Python's `ast` module directly against the cloned source
  (`stt.py`/`tts.py`/`llm.py`/`avatar.py`/`realtime_model.py`/`realtime_api.py`/`gpt_live_model.py`/`vad.py`
  for every one of the 70 `livekit-plugins-*` / `livekit-*` directories in the repo) — not regex, not
  memory, not doc pages. This is a materially more reliable method than v1's per-file `grep`/read pass and
  caught things grep missed (e.g. Groq's `STT`/`LLM` both live in `services.py`, not `stt.py`).
- **PyPI**: every package's `pypi.org/pypi/<name>/json` fetched for version, `requires_python`, and
  `requires_dist`.
- **Dependency resolution**: verified empirically with `uv pip compile` against a requirements file
  listing all 70+ packages at their real published versions (not asserted) — see §4.
- **Vendor-side facts** (avatar list-APIs, pricing, latency, self-host): delegated to four parallel research
  passes (4-5 vendors each) using live web search, because these facts live on vendor doc/pricing pages the
  LiveKit plugin source cannot answer. Each cell below is marked with its source; anything the sub-pass
  could not confirm is marked **UNVERIFIED**, never guessed.
- **Core SDK mechanics** (`reply_required`, `TOOL_BEHAVIOR`, the new inference-native VAD/turn-detector):
  grepped directly from `livekit-agents/livekit/agents/` core, not plugin code.

### 0.1 What's genuinely new since v1

v1 covered 16 avatar plugins, ~13 STT, ~9 LLM (incl. OpenAI-compatible passthrough), ~12 TTS, 3 realtime
models, VAD/turn-detector/noise-cancellation as a paragraph, and named Hedra/PlayAI as absent. The real
`livekit-plugins/` directory in the 1.8.2 tag has **70 top-level packages**. Beyond the ones v1 already
named, this pass adds full constructor-level detail (AST-parsed `__init__`, not grep) for roughly **45
packages** v1 either didn't mention at all or only listed by name without reading their source: `asyncai`,
`baseten`, `bland`, `browser`, `cambai`, `clova`, `fal`, `fireworksai`, `fishaudio`, `gnani`, `gradium`,
`hamming`, `krisp`, `langchain`, `lmnt`, `meta`, `minimax`, `mistralai` (STT+LLM+TTS, all three), `murf`,
`neuphonic`, `nltk`, `nvidia` (STT+TTS+experimental realtime "PersonaPlex"), `palabra`, `perplexity`,
`phonic` (realtime), `respeecher`, `rtzr`, `simplismart`, `slng`, `smallestai`, `spitch`, `telnyx`,
`ultravox` (realtime), `upliftai`, `vakyam`, plus the internal/non-provider packages `livekit-blockguard`,
`livekit-durable`, `livekit-plugins-minimal`, and the out-of-tree `livekit-plugins-noise-cancellation` /
`livekit-plugins-ai-coustics` / `livekit-plugins-hedra` / `livekit-plugins-playai`. This pass also
**completed** several packages v1 covered only partially: `aws` (had LLM/TTS/STT, this pass adds the Nova
Sonic realtime submodule), `groq` (v1 only had LLM; this pass adds STT+TTS, both in `services.py`/`tts.py`),
`google`/`openai`/`xai` (this pass adds every realtime overload, not just the primary constructor).

### 0.2 New "kinds" the current registry schema has no slot for

`ProviderKind` in `providers.py` is `realtime | stt | llm | tts | avatar | image_gen | embedding |
secret_bag`. Source-reading turned up **three more first-class kinds** that a complete registry needs:

1. **`vad`** — two implementations: `livekit.plugins.silero.VAD` (separate package, local ONNX, no key) and
   the newer **`livekit.agents.inference.VAD`** (built into `livekit-agents` core since ~1.7, backed by the
   `livekit-local-inference` native package — same `av`/`livekit-local-inference` install already seen in
   the venv at `.../livekit/local_inference/`). Confirmed via `livekit-agents/livekit/agents/inference/vad.py`:
   `VADModels = Literal["silero"]`, class docstring: *"Voice Activity Detection backed by
   `livekit-local-inference`... native model singleton loaded once at module import."*
2. **`turn_detection`** — likewise two implementations: the plugin package
   `livekit.plugins.turn_detector.{multilingual,english}.{MultilingualModel,EnglishModel}` (local
   ONNX+`transformers`, HF model `livekit/turn-detector`, revisions `v1.2.2-en` / `v0.4.1-intl`), **and** a
   new inference-native class `livekit.agents.inference.eot.TurnDetector` confirmed in source
   (`inference/eot/detector.py`): `version: "v1" | "v1-mini"`, defaults to `"v1"` when
   `utils.is_hosted()`/dev-mode (billed to the LiveKit Cloud project) and to local `"v1-mini"` otherwise,
   with `local_fallback: bool = True` letting a cloud `v1` request silently degrade to the local mini model
   (~108 MB resident) if the gateway call fails.
3. **`noise_cancellation`** — three packages, none lockstep-versioned with core: `livekit-plugins-krisp`
   (in-tree at 1.8.2's own repo, version **0.4.2**, Krisp Viva SDK, classes
   `KrispVivaFilterFrameProcessor`/`KrispLicenseAuthProvider`/`LiveKitCloudAuthProvider`, agent-side
   `rtc.FrameProcessor`), plus two **out-of-tree** packages found only on PyPI:
   `livekit-plugins-noise-cancellation` (0.3.2, requires only `livekit>=0.21.3` — this is the older/simpler
   Krisp BVC wrapper most existing LiveKit sample apps import) and `livekit-plugins-ai-coustics` (0.3.2,
   requires `livekit-agents>=1.4.2`).

**Recommendation for `ProviderKind`**: extend the `Literal` to
`realtime | stt | llm | tts | avatar | vad | turn_detection | noise_cancellation | image_gen | embedding |
secret_bag`, and extend `FieldType` with `"file"` (bitHuman's `avatar_image: PIL.Image.Image | str`,
LemonSlice's `agent_image`, Google's `credentials_file`) since today's `FieldType` has no binary-upload
type and every registry snippet in v1/this doc had to fudge it as `"string"` or `"json"`.

### 0.3 Two corrections to the v1 research doc

1. **PlayAI is not absent.** v1 said *"no dedicated `livekit-plugins-playai` package... not currently an
   official LiveKit Agents plugin."* `pip index` / PyPI JSON shows `livekit-plugins-playai` **does exist**
   (latest **1.2.15**, uploaded 2025-10-15 — i.e. it predates and has not tracked the 1.8.2 lockstep train).
   Downloading and inspecting the wheel confirms a working `TTS` class (`api_key`, `user_id`, `voice`
   manifest URL, `model: TTSModel = "PlayDialog"`, wraps the third-party `pyht` SDK). It is **not** in the
   current `livekit/agents` monorepo tree (removed or never merged) and is 6 major-ish releases behind
   core, so treat it as **community/legacy, not verified against 1.8.2 core** — flagged `UNVERIFIED
   compatibility` in the registry proposal (§5).
2. **Hedra exists on PyPI but is dead, not merely absent.** `livekit-plugins-hedra` **1.7.1** is published
   (uploaded 2026-08-27, `requires_dist: livekit-agents>=1.7.1`) but is **not** in the `livekit/agents`
   monorepo tree at 1.8.2 or main. Downloading the wheel and reading `avatar.py` shows the entire class body
   is now:
   ```python
   class AvatarSession:
       """Hedra realtime avatar service has been disabled."""
       def __init__(self, **kwargs: object) -> None:
           raise HedraException(
               "The Hedra realtime avatar service has been disabled. This plugin no longer functions. "
               "Please browse our other avatar integrations instead at https://docs.livekit.io/agents/models/avatar/."
           )
   ```
   Hedra is **explicitly and permanently disabled by its own vendor plugin code** — instantiating it always
   raises. It must **not** appear as a selectable provider in the registry; it is listed in §2 only because
   the task calls for covering it and because "it's on PyPI but dead" is itself the load-bearing fact an
   admin UI needs to not build a dropdown entry for it.

---

## 1. Master table — every provider × kind

Legend: **LI** = LiveKit Inference (no vendor key). Version column is the plugin package's own PyPI version
(not always the same as `livekit-agents` core — see the "lockstep?" column). All Python class paths and
field names below come from the AST-parsed `__init__` signatures unless footnoted otherwise.

### 1.1 STT

| Provider | Package (version) | Lockstep w/ 1.8.2? | Class | Auth field(s) | Env fallback | Key non-secret params (defaults) | Example models | Vendor list-models API? |
|---|---|---|---|---|---|---|---|---|
| LiveKit Inference | `livekit-agents` (1.8.2, core) | n/a (core) | `livekit.agents.inference.STT` | `api_key`+`api_secret` = **your LiveKit project's own keys**, not a vendor key | `LIVEKIT_API_KEY`/`LIVEKIT_API_SECRET` (`LIVEKIT_INFERENCE_API_KEY/_SECRET` override checked first) | `model="<vendor>/<id>[:lang]"`, `language` | `deepgram/nova-3`, `deepgram/flux-general-en`, `assemblyai/universal-3-5-pro`, `cartesia/ink-whisper`, `google/gemini-3.5-transcribe-live`, `xai/stt-1`, `speechmatics/linden-1`, `inworld/inworld-stt-1` | n/a — fixed catalog in SDK source (`inference/stt.py` literals), refreshed by LiveKit itself |
| Deepgram | `livekit-plugins-deepgram` 1.8.2 | yes | `livekit.plugins.deepgram.STT` | `api_key` | `DEEPGRAM_API_KEY` | `model="nova-3"`, `language="en-US"`, `interim_results=True`, `punctuate=True`, `endpointing_ms=25`, `keywords`, `keyterm`, `base_url` | `nova-3`, `nova-2`, `nova-2-medical`, `nova-2-phonecall` | Yes — Deepgram REST `GET /v1/models` (vendor API, not in LK source) |
| AssemblyAI | `livekit-plugins-assemblyai` 1.8.2 | yes | `livekit.plugins.assemblyai.STT` | `api_key` | `ASSEMBLYAI_API_KEY` | `model: Literal["universal-streaming-english","universal-streaming-multilingual","u3-rt-pro","u3-rt-pro-beta-1","u3-pro","universal-3-5-pro","universal-3-6-pro"]="universal-3-5-pro"`, `language_code(s)`, `speaker_labels`, `format_turns`, `vad_threshold`, `mode` | `universal-3-5-pro`, `universal-3-6-pro`, `u3-rt-pro` | UNVERIFIED (closed literal in SDK; no listing call seen) |
| Google Cloud Speech | `livekit-plugins-google` 1.8.2 | yes | `livekit.plugins.google.STT` | **no `api_key`** — `credentials_info: dict` \| `credentials_file: str` \| `credentials: google.auth.credentials.Credentials` (ADC) | `GOOGLE_APPLICATION_CREDENTIALS` (ADC) | `languages="en-US"`, `model: SpeechModels="latest_long"`, `location="global"`, `project`, `detect_language=True`, `use_streaming` | `latest_long`, `chirp_3`, `telephony` | Yes, GCP `speech.googleapis.com` model listing is static per docs, not a REST list call |
| OpenAI | `livekit-plugins-openai` 1.8.2 | yes | `livekit.plugins.openai.STT` (+ `.with_azure(...)` overload, same file) | `api_key` (Azure overload: `api_key`/`azure_ad_token`/`azure_ad_token_provider`) | `OPENAI_API_KEY` | `model: STTModels="gpt-4o-mini-transcribe"`, `language="en"`, `use_realtime`, `vad`, `turn_detection`, `noise_reduction_type` | `gpt-4o-mini-transcribe`, `gpt-4o-transcribe`, `whisper-1` | Yes — `GET /v1/models` |
| Speechmatics | `livekit-plugins-speechmatics` 1.8.2 | yes | `livekit.plugins.speechmatics.STT` | `api_key` | none read directly (see note) — SDK helper resolves `SPEECHMATICS_API_KEY` | `turn_detection_mode: TurnDetectionMode=EXTERNAL`, `model`/`operating_point`, `language="en"`, `enable_diarization` | `linden-1`, legacy `enhanced`/`standard` | UNVERIFIED |
| ElevenLabs (Scribe) | `livekit-plugins-elevenlabs` 1.8.2 | yes | `livekit.plugins.elevenlabs.STT` | `api_key` | `ELEVEN_API_KEY` | `language_code`, `model: ElevenLabsSTTModels`, `tag_audio_events=True`, `use_realtime`, `server_vad`, `keyterms` | `scribe_v1`, `scribe_v2`, `scribe_v2_realtime` | Yes — `GET /v1/models` |
| Cartesia | `livekit-plugins-cartesia` 1.8.2 | yes | `livekit.plugins.cartesia.STT` | `api_key` | `CARTESIA_API_KEY` | `model: STTModels`, `sample_rate=16000`, `turn_start_threshold`, `turn_end_threshold`, `keyterm` | `ink-whisper`, `ink-2` | UNVERIFIED |
| Gladia | `livekit-plugins-gladia` 1.8.2 | yes | `livekit.plugins.gladia.STT` | `api_key` | `GLADIA_API_KEY` | `model: GladiaModels="solaria-1"`, `region: Literal["us-west","eu-west"]="eu-west"`, `code_switching=True`, `translation_enabled`, `custom_vocabulary` | `solaria-1` | UNVERIFIED |
| Soniox | `livekit-plugins-soniox` 1.8.2 | yes | `livekit.plugins.soniox.STT` | `api_key` | none in ctor (`params: STTOptions`) | `base_url` (`wss://stt-rt.soniox.com/...`), `params: STTOptions` | `stt-rt-v3` family (per `STTOptions` defaults, not enumerated here) | UNVERIFIED |
| Groq | `livekit-plugins-groq` 1.8.2 | yes | `livekit.plugins.groq.STT` (in `services.py`, subclasses OpenAI-compatible pattern) | `api_key` | `GROQ_API_KEY` | `model="whisper-large-v3-turbo"`, `base_url="https://api.groq.com/openai/v1"`, `language="en"`, `detect_language=False` | `whisper-large-v3-turbo`, `whisper-large-v3` | Yes — Groq `GET /openai/v1/models` |
| Azure AI Speech | `livekit-plugins-azure` 1.8.2 | yes | `livekit.plugins.azure.STT` | **`speech_key`** (not `api_key`) | `speech_auth_token` alt.; region-scoped, no single canonical env const read in ctor | `speech_region`, `speech_host`, `sample_rate=16000`, `language`, `segmentation_silence_timeout_ms`, `phrase_list` | region voice list, no "model id" concept | Yes — Azure Speech `GET .../speechtotext/v3.x/models/base` |
| Amazon Transcribe | `livekit-plugins-aws` 1.8.2 | yes | `livekit.plugins.aws.STT` | `credentials: Credentials \| NotGiven` (boto-style) — **no flat `api_key`/`api_secret` on STT**, unlike `aws.LLM`/`aws.TTS` | standard AWS chain (`AWS_REGION` etc.) | `region`, `sample_rate=24000`, `language="en-US"`, `identify_language`, `vocabulary_name(s)`, `enable_channel_identification` | n/a (streaming, no model id) | n/a |
| xAI | `livekit-plugins-xai` 1.8.2 | yes | `livekit.plugins.xai.STT` | `api_key` | `XAI_API_KEY` | `enable_diarization`, `language="en"`, `smart_turn`, `vad_threshold`, `keyterm` | `stt-1` | UNVERIFIED |
| Fal (Wizper) | `livekit-plugins-fal` 1.8.2 | yes | `livekit.plugins.fal.WizperSTT` | **class name is `WizperSTT`, not `STT`** — note for the factory's dynamic-import path | none read (`FAL_KEY` per fal-client convention) | model fixed to Whisper-family per fal's Wizper endpoint | Wizper (Whisper large) | n/a |
| Fireworks AI | `livekit-plugins-fireworksai` 1.8.2 | yes | `livekit.plugins.fireworksai.STT` | `api_key` | `FIREWORKS_API_KEY` | `base_url="wss://audio-streaming.us-virginia-1.direct.fireworks.ai/v1"`, `language`, `response_format="verbose_json"`, `skip_vad` | Fireworks streaming ASR | UNVERIFIED |
| Baseten | `livekit-plugins-baseten` 1.8.2 | yes | `livekit.plugins.baseten.STT` (+ `qwen3_stt.py` variant) | `api_key` | `BASETEN_API_KEY`, `BASETEN_MODEL_ENDPOINT` | `model: STTModels="whisper"`, `model_endpoint`, `model_id`, `chain_id`, `vad_threshold=0.5` | `whisper` (Baseten-hosted), Qwen3-ASR | n/a (self-deployed model endpoints) |
| Mistral (Voxtral) | `livekit-plugins-mistralai` 1.8.2 | yes | `livekit.plugins.mistralai.STT` | `api_key` (or `client: Mistral`) | `MISTRAL_API_KEY` | `model="voxtral-mini-latest"`, `language`, `context_bias`, `target_streaming_delay_ms`, `vad: vad.VAD` | `voxtral-mini-latest` | Yes — Mistral `GET /v1/models` |
| NVIDIA (Riva) | `livekit-plugins-nvidia` 1.8.2 | yes | `livekit.plugins.nvidia.STT` | `api_key` | none read directly | `model="parakeet-1.1b-en-US-asr-streaming-silero-vad-sortformer"`, `function_id`, `server="grpc.nvcf.nvidia.com:443"`, `enable_diarization` | `parakeet-1.1b-en-US...` | UNVERIFIED (NVCF function catalog, not a plugin-level call) |
| Sarvam | `livekit-plugins-sarvam` 1.8.2 | yes | `livekit.plugins.sarvam.STT` | `api_key` | `SARVAM_API_KEY` | `model: SarvamSTTModels="saaras:v4"`, `language="en-IN"`, `mode: Literal["transcribe","translate"]`, extensive VAD tuning params | `saaras:v4` | UNVERIFIED |
| Meta | `livekit-plugins-meta` 1.8.2 | yes | `livekit.plugins.meta.STT` | `api_key` | none read directly | `model="muse-voice-transcribe-1.0"`, `url="wss://api.meta.ai/v1/asr/realtime"`, `keywords`, `language_bias` | `muse-voice-transcribe-1.0` | UNVERIFIED |
| Gnani | `livekit-plugins-gnani` 1.8.2 | yes | `livekit.plugins.gnani.STT` | `api_key` | `GNANI_API_KEY` | `language="en-IN"`, `base_url="https://api.vachana.ai"`, `use_streaming=True`, `itn_native_numerals` | Gnani ASR (India-focused) | UNVERIFIED |
| Gradium | `livekit-plugins-gradium` 1.8.2 | yes | `livekit.plugins.gradium.STT` | `api_key` | `GRADIUM_API_KEY`, `GRADIUM_MODEL_ENDPOINT` | `model_endpoint`, `model_name="default"`, `vad_threshold=0.9`, `language="en"` | self-deployed | n/a |
| Clova (Naver) | `livekit-plugins-clova` 1.8.2 | yes | `livekit.plugins.clova.STT` | `secret` | `CLOVA_STT_SECRET_KEY`, `CLOVA_STT_INVOKE_URL` | `language: ClovaSttLanguages="en-US"`, `invoke_url`, `threshold=0.5` | Naver Clova Speech | UNVERIFIED |
| RTZR (Vito, Korean) | `livekit-plugins-rtzr` 1.8.2 | yes | `livekit.plugins.rtzr.STT` | no explicit `api_key` param — auth via `rtzrapi.py` helper (token-based) | UNVERIFIED | `model="sommers_ko"`, `language="ko"`, `domain="CALL"`, `epd_time=0.8` | `sommers_ko` | UNVERIFIED |
| Slng | `livekit-plugins-slng` 1.8.2 | yes | `livekit.plugins.slng.STT` | `api_key` (+ optional `api_token`, `provider_api_key` for BYO-upstream) | `SLNG_API_KEY` | `model_endpoint(s)`, `slng_base_url="api.slng.ai"`, `enable_diarization`, `min/max_speakers`, `fallback_recovery_cooldown_s=60.0` | routes to configured upstream model | n/a (router/gateway product) |
| Smallest AI | `livekit-plugins-smallestai` 1.8.2 | yes | `livekit.plugins.smallestai.STT` | `api_key` | `SMALLEST_API_KEY` | `model: STTModels="pulse"`, `language="en"`, `diarize`, `redact_pii`, `redact_pci` | `pulse` | UNVERIFIED |
| Simplismart | `livekit-plugins-simplismart` 1.8.2 | yes | `livekit.plugins.simplismart.STT` | `api_key` | `SIMPLISMART_API_KEY` | `model: STTModels="openai/whisper-large-v3-turbo"`, `vad_model: Literal["silero","frame"]`, `beam_size=4` | `openai/whisper-large-v3-turbo` | n/a (self-deployed inference) |
| Telnyx | `livekit-plugins-telnyx` 1.8.2 | yes | `livekit.plugins.telnyx.STT` | `api_key` | none read directly | `transcription_engine: TranscriptionEngine="telnyx"`, `language="en"`, `interim_results=True` | Telnyx native ASR | UNVERIFIED |
| Spitch | `livekit-plugins-spitch` 1.8.2 | yes | `livekit.plugins.spitch.STT` | none in ctor — uses `AsyncSpitch()` client which reads its own env var | UNVERIFIED (`SPITCH_API_KEY` per vendor convention, not confirmed in this file) | `language="en"` (African-language focused) | UNVERIFIED | UNVERIFIED |
| Palabra | `livekit-plugins-palabra` 1.8.2 | yes | `livekit.plugins.palabra.STT` | `api_key` | `PALABRA_API_KEY` | `translate_languages`, `filler_filter`, `sample_rate=16000`, `region` | Palabra real-time translation ASR | UNVERIFIED |

### 1.2 LLM

| Provider | Package (version) | Lockstep? | Class | Auth field(s) | Env fallback | Key non-secret params (defaults) | Example models | Vendor list-models API? |
|---|---|---|---|---|---|---|---|---|
| LiveKit Inference | `livekit-agents` (core) | n/a | `livekit.agents.inference.LLM` | Your LiveKit project keys | `LIVEKIT_API_KEY`/`_SECRET` | `model`, `temperature`, `inference_class: "priority"\|"standard"\|"low"` | `openai/gpt-5.4`, `openai/chat-latest`, `google/gemini-3.5-flash`, `moonshotai/kimi-k2.6`, `deepseek-ai/deepseek-v3.2`, `zai/glm-5.1`, `xai/grok-4.5` | n/a — fixed literal union in `inference/llm.py` (`OpenAIModels\|GoogleModels\|KimiModels\|DeepSeekModels\|ZAIModels\|XAIModels`) |
| OpenAI | `livekit-plugins-openai` 1.8.2 | yes | `livekit.plugins.openai.LLM` (chat) + `.responses.llm.LLM` (Responses API) + `.with_azure(...)` classmethod | `api_key` | `OPENAI_API_KEY` | `model="gpt-4.1"`, `base_url`, `temperature`, `parallel_tool_calls`, `reasoning_effort`, `verbosity`, `service_tier` | `gpt-4.1`, `gpt-4o`, `gpt-5.4` | Yes `GET /v1/models` |
| Azure OpenAI | same package | yes | `LLM.with_azure(azure_endpoint, azure_deployment, api_key\|azure_ad_token\|azure_ad_token_provider, api_version, ...)`; a distinct `azure/responses/llm.py::LLM` also exists (Azure Responses API surface) | `api_key` / `azure_ad_token` / `azure_ad_token_provider` | `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_AD_TOKEN` | `azure_deployment`, `api_version` | your deployment name | n/a |
| Anthropic | `livekit-plugins-anthropic` 1.8.2 | yes | `livekit.plugins.anthropic.LLM` | `api_key` | `ANTHROPIC_API_KEY` | `model: ChatModels="claude-sonnet-4-6"`, `max_tokens`, `top_k`, `caching: "ephemeral"`, `parallel_tool_calls` | `claude-sonnet-4-6`, `claude-opus-4-6`, `claude-opus-4-1-20250805`, `claude-3-5-haiku-20241022` (full `ChatModels` literal has 11 entries incl. 3 marked deprecated in-line) | Yes `GET /v1/models` |
| Google Gemini | `livekit-plugins-google` 1.8.2 | yes | `livekit.plugins.google.LLM` (+ `aiplatform_llm.py` Vertex variant) | `api_key`, or `vertexai=True`+`project`/`location`/`credentials` | `GOOGLE_API_KEY`, `GOOGLE_CLOUD_PROJECT`, `GOOGLE_CLOUD_LOCATION`, `GOOGLE_GENAI_USE_VERTEXAI` | `model: ChatModels="gemini-2.5-flash"` (plugin default; note this **lags** the newer `gemini-3.x` ids the LiveKit Inference catalog already lists), `temperature`, `thinking_config`, `safety_settings` | `gemini-2.5-flash`, `gemini-1.5-pro` (plugin's own `ChatModels` literal tops out at `gemini-2.5-flash`; use free-text for `gemini-3.x`) | Yes `GET /v1/models` (Gemini API) or Vertex Model Garden |
| Groq | `livekit-plugins-groq` 1.8.2 | yes | `livekit.plugins.groq.LLM` (in `services.py`) | `api_key` | `GROQ_API_KEY` | `model: str\|LLMModels="llama-3.3-70b-versatile"`, `base_url="https://api.groq.com/openai/v1"` (fixed), `reasoning_effort`, `service_tier` | `llama-3.3-70b-versatile`, `openai/gpt-oss-120b`, `qwen/qwen3-32b` | Yes |
| Cerebras | `livekit-plugins-cerebras` 1.8.2 | yes | `livekit.plugins.cerebras.LLM` | `api_key` | `CEREBRAS_API_KEY` | `model: str\|CerebrasChatModels="gpt-oss-120b"`, `base_url="https://api.cerebras.ai/v1"` (fixed), `gzip_compression=True`, `msgpack_encoding=True` | `gpt-oss-120b` | Yes |
| Perplexity | `livekit-plugins-perplexity` 1.8.2 | yes | `livekit.plugins.perplexity.LLM` (chat) + `.responses.llm.LLM` | `api_key` | `PERPLEXITY_BASE_URL` fixed to `https://api.perplexity.ai`; key env UNVERIFIED (not read via `os.environ` in this file — likely SDK-default `PERPLEXITY_API_KEY`) | `model: str\|PerplexityChatModels="sonar-pro"` (chat) / `"perplexity/sonar"` (responses) | `sonar-pro`, `sonar` | Yes |
| Mistral | `livekit-plugins-mistralai` 1.8.2 | yes | `livekit.plugins.mistralai.LLM` | `api_key` (or `client: Mistral`) | `MISTRAL_API_KEY` | `model: ChatModels="ministral-8b-latest"`, `temperature`, `random_seed`, `max_completion_tokens` | `ministral-8b-latest`, Mistral Large/Small family | Yes |
| Baseten | `livekit-plugins-baseten` 1.8.2 | yes | `livekit.plugins.baseten.LLM` | `api_key` | `BASETEN_API_KEY` | `model: str\|LLMModels="meta-llama/Llama-4-Maverick-17B-128E-Instruct"`, `base_url="https://inference.baseten.co/v1"`, `reasoning_effort` | `meta-llama/Llama-4-Maverick-17B-128E-Instruct` | n/a (self-deployed model garden) |
| xAI | `livekit-plugins-xai` 1.8.2 | yes | `livekit.plugins.xai.responses.llm.LLM` | `api_key` | `XAI_API_KEY` | `model="grok-4-1-fast-non-reasoning"`, `reasoning: Reasoning` | `grok-4-1-fast-non-reasoning`, `grok-4-1-fast-reasoning` | Yes |
| Amazon Bedrock | `livekit-plugins-aws` 1.8.2 | yes | `livekit.plugins.aws.LLM` | `api_key`+`api_secret` (flat AWS key/secret — **only** this plugin family + `aws.TTS` do this; `aws.STT` doesn't) | `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` only consulted by the underlying boto session when both are explicitly passed; else standard chain | `model: NotGivenOr[str]=DEFAULT_TEXT_MODEL` (= `"amazon.nova-2-lite-v1:0"`), `region="us-east-1"`, `cache_system`, `cache_tools`, `additional_request_fields` | `amazon.nova-2-lite-v1:0`, any Bedrock-hosted Anthropic/Meta model ARN | Yes — Bedrock `ListFoundationModels` |
| OpenAI-compatible passthrough | `livekit-plugins-openai` (`openai.LLM` with `base_url=`) | yes | same `openai.LLM` class | `api_key` (dummy string ok for unauthenticated Ollama) | `OPENROUTER_API_KEY`, `TOGETHER_API_KEY`, `FIREWORKS_API_KEY`, `DEEPSEEK_API_KEY`, `PERPLEXITY_API_KEY`, `CEREBRAS_API_KEY`, `OVHCLOUD_API_KEY`, `TELNYX_API_KEY`, `LETTA_API_KEY`, `NEBIUS_API_KEY`, `SAMBANOVA_API_KEY`, `OCTOAI_TOKEN`, `COMETAPI_API_KEY`, `XAI_API_KEY` (all confirmed literal `os.environ` reads in `openai/llm.py`) | `base_url` required (e.g. `https://openrouter.ai/api/v1`, `https://api.together.xyz/v1`, `http://localhost:11434/v1` for Ollama, `http://localhost:8000/v1` for vLLM) | whatever the target names its models | provider-specific |

### 1.3 TTS

| Provider | Package (version) | Lockstep? | Class | Auth field(s) | Env fallback | Key non-secret params (defaults) | Example voices/models | Vendor list API? |
|---|---|---|---|---|---|---|---|---|
| LiveKit Inference | `livekit-agents` (core) | n/a | `livekit.agents.inference.TTS` | LiveKit project keys | `LIVEKIT_API_KEY`/`_SECRET` | `model="vendor/id[:voice]"`, `voice`, `language` | `cartesia/sonic-3.5`, `deepgram/aura-2`, `rime/mistv3`, `inworld/inworld-tts-2`, `fishaudio/s2.1-pro`, `xai/tts-1` | n/a — fixed literal union in `inference/tts.py` |
| Cartesia | `livekit-plugins-cartesia` 1.8.2 | yes | `livekit.plugins.cartesia.TTS` | `api_key` | `CARTESIA_API_KEY` | `model: TTSModels="sonic-3"`, `voice: str\|list[float]`, `language="en"`, `speed`, `emotion`, `text_pacing` | `sonic-3`, `sonic-3.6` | Yes — `GET /voices` |
| ElevenLabs | `livekit-plugins-elevenlabs` 1.8.2 | yes | `livekit.plugins.elevenlabs.TTS` | `api_key` | `ELEVEN_API_KEY` | `voice_id=DEFAULT_VOICE_ID` (`"hpp4J3VqNfWAUOO0d1Us"`), `model: TTSModels="eleven_turbo_v2_5"`, `chunk_length_schedule`, `apply_text_normalization` | `eleven_turbo_v2_5`, `eleven_multilingual_v2`, `eleven_flash_v2_5` | Yes — `GET /v1/voices` |
| OpenAI | `livekit-plugins-openai` 1.8.2 | yes | `livekit.plugins.openai.TTS` (+ `.with_azure(...)`) | `api_key` | `OPENAI_API_KEY` | `model=DEFAULT_MODEL` (`"gpt-4o-mini-tts"`), `voice=DEFAULT_VOICE` (`"ash"`), `speed=1.0`, `instructions` | `gpt-4o-mini-tts`, `tts-1`, `tts-1-hd` | Yes |
| Google Cloud TTS | `livekit-plugins-google` 1.8.2 | yes | `livekit.plugins.google.TTS` | `credentials_info`/`credentials_file`/`credentials` (ADC) — no flat `api_key` | ADC | `voice_name`, `language="en-US"`, `model_name: GeminiTTSModels`, `use_streaming=True`, `enable_ssml` | `gemini-3.1-flash-tts-preview`, `en-US-Neural2-*` | Yes |
| Deepgram (Aura) | `livekit-plugins-deepgram` 1.8.2 | yes | `livekit.plugins.deepgram.TTS` | `api_key` | `DEEPGRAM_API_KEY` | `model: TTSModels="aura-2-andromeda-en"`, `encoding="linear16"`, `sample_rate=24000`, `mip_opt_out=False` | `aura-2-andromeda-en`, `aura-2-apollo-en` | Yes |
| Azure AI Speech | `livekit-plugins-azure` 1.8.2 | yes | `livekit.plugins.azure.TTS` | `speech_key` | none read directly (region-scoped) | `voice="en-US-JennyNeural"`, `speech_region`/`speech_endpoint`, `prosody: ProsodyConfig`, `style: StyleConfig`, `deployment_id` | `en-US-JennyNeural`, `en-US-GuyNeural` | Yes |
| Rime | `livekit-plugins-rime` 1.8.2 | yes | `livekit.plugins.rime.TTS` (3 `@overload` ctors: websocket-url form, base-url form, combined form) | `api_key` (omitted entirely on the raw-`websocket_url` overload — self-hosted/custom endpoint path, confirmed distinct overload) | `RIME_API_KEY` | `model: TTSModels`, `speaker`, `lang="eng"`, `time_scale_factor`, `pause_between_brackets` | `mist`, `mistv2`, `mistv3`, `coda` | UNVERIFIED |
| Inworld | `livekit-plugins-inworld` 1.8.2 | yes | `livekit.plugins.inworld.TTS` | `api_key` | none read directly (`INWORLD_API_KEY` per vendor convention) | `model=DEFAULT_MODEL` (`"inworld-tts-1.5-max"` — note: **differs from the LiveKit-Inference-catalog default `inworld-tts-2`**), `voice=DEFAULT_VOICE` (`"Ashley"`), `speaking_rate`, `delivery_mode`, `max_connections=20` | `inworld-tts-2`, `inworld-tts-1.5-max`, `inworld-tts-1.5-flash` | UNVERIFIED |
| Hume | `livekit-plugins-hume` 1.8.2 | yes | `livekit.plugins.hume.TTS` | `api_key` | `HUME_API_KEY` | `voice: VoiceById\|VoiceByName=DEFAULT_VOICE` (`VoiceByName(name="Male English Actor")`), `model_version="1"`, `description`, `instant_mode` | Hume "Octave" voices by id or name | Yes — Hume `GET /v0/tts/voices` |
| Resemble | `livekit-plugins-resemble` 1.8.2 | yes | `livekit.plugins.resemble.TTS` | `api_key` | `RESEMBLE_API_KEY` | `voice_uuid`, `sample_rate=44100`, `use_streaming=True` | project-specific voice UUID | UNVERIFIED |
| Amazon Polly | `livekit-plugins-aws` 1.8.2 | yes | `livekit.plugins.aws.TTS` | `api_key`+`api_secret` (flat) | AWS chain | `voice="Ruth"`, `speech_engine: TTSSpeechEngine="generative"`, `text_type="text"`, `region` | `Ruth`, `Matthew`, `Joanna` | Yes — Polly `DescribeVoices` |
| Bland | `livekit-plugins-bland` 1.8.2 | yes | `livekit.plugins.bland.TTS` | `api_key` | `BLAND_API_KEY` | `voice_id=DEFAULT_VOICE_ID`, `base_url="https://api.bland.ai/v2"`, `streaming=True`, `expressiveness`, `stability` | Bland voice-clone ids | UNVERIFIED |
| Camb.ai | `livekit-plugins-cambai` 1.8.2 | yes | `livekit.plugins.cambai.TTS` | `api_key` | `CAMB_API_KEY` | `voice_id: int`, `model: SpeechModel`, `language`, `enhance_named_entities` | Camb.ai voices (int ids) | UNVERIFIED |
| Fish Audio | `livekit-plugins-fishaudio` 1.8.2 | yes | `livekit.plugins.fishaudio.TTS` | `api_key` | none confirmed directly in ctor | `model: TTSModels=DEFAULT_MODEL` (`"s2.1-pro"`), `voice_id=DEFAULT_VOICE_ID`, `latency_mode: Literal balanced/...`, `normalize=True` | `s2.1-pro` | UNVERIFIED |
| Groq (TTS) | `livekit-plugins-groq` 1.8.2 | yes | `livekit.plugins.groq.TTS` | `api_key` | none in ctor directly (`base_url` fixed `https://api.groq.com/openai/v1`) | `model="canopylabs/orpheus-v1-english"`, `voice="autumn"` | `canopylabs/orpheus-v1-english` | Yes |
| LMNT | `livekit-plugins-lmnt` 1.8.2 | yes | `livekit.plugins.lmnt.TTS` | `api_key` | `LMNT_API_KEY` | `model: LMNTModels="blizzard"`, `voice="leah"`, `format="mp3"`, `sample_rate=24000` | `blizzard` | UNVERIFIED |
| MiniMax | `livekit-plugins-minimax` **1.3.0 — NOT lockstep, pins `livekit-agents==1.2.9` exactly** | **no — see §4, incompatible with the rest of the stack** | `livekit.plugins.minimax.TTS` | `api_key` | `MINIMAX_API_KEY`, `MINIMAX_BASE_URL` | `model: TTSModel=DEFAULT_MODEL` (`"speech-02-turbo"`), `voice=DEFAULT_VOICE_ID` (`"socialmedia_female_2_v1"`), `emotion`, `intensity`, `timbre` | `speech-02-turbo` | UNVERIFIED |
| Murf | `livekit-plugins-murf` 1.8.2 | yes | `livekit.plugins.murf.TTS` | `api_key` | `MURF_API_KEY` | `model: TTSModels="FALCON"`, `voice=TTSDefaultVoiceId`, `locale`, `style`, `streaming=True` | `FALCON` | UNVERIFIED |
| Neuphonic | `livekit-plugins-neuphonic` 1.8.2 | yes | `livekit.plugins.neuphonic.TTS` | `api_key` or `jwt_token` | `NEUPHONIC_API_KEY` | `voice_id="8e9c4bc8-..."`, `lang_code="en"`, `speed=1.0`, `sample_rate=22050` | vendor voice-id catalog | UNVERIFIED |
| NVIDIA | `livekit-plugins-nvidia` 1.8.2 | yes | `livekit.plugins.nvidia.TTS` | `api_key` | none read directly | `voice="Magpie-Multilingual.EN-US.Leo"`, `function_id="877104f7-..."`, `server="grpc.nvcf.nvidia.com:443"` | `Magpie-Multilingual.EN-US.Leo` | UNVERIFIED |
| Mistral (Voxtral TTS) | `livekit-plugins-mistralai` 1.8.2 | yes | `livekit.plugins.mistralai.TTS` | `api_key` (or `client: Mistral`) | `MISTRAL_API_KEY` | `model=DEFAULT_MODEL` (`"voxtral-mini-tts-latest"`), `voice=DEFAULT_VOICE` (`"en_paul_neutral"`), `response_format="mp3"` | `voxtral-mini-tts-latest` | Yes |
| Respeecher | `livekit-plugins-respeecher` 1.8.2 | yes | `livekit.plugins.respeecher.TTS` | `api_key` | `RESPEECHER_API_KEY` | `model: TTSModels="/public/tts/en-rt"`, `voice_id`, `sample_rate=24000` | `/public/tts/en-rt` | UNVERIFIED |
| Sarvam | `livekit-plugins-sarvam` 1.8.2 | yes | `livekit.plugins.sarvam.TTS` | `api_key` | `SARVAM_API_KEY` | `target_language_code="en-IN"`, `model: SarvamTTSModels="bulbul:v3"`, `speaker`, `pace=1.0` | `bulbul:v3` | UNVERIFIED |
| Simplismart | `livekit-plugins-simplismart` 1.8.2 | yes | `livekit.plugins.simplismart.TTS` | `api_key` | `SIMPLISMART_API_KEY` | `model: TTSModels=DEFAULT_ORPHEUS_MODEL` (`"canopylabs/orpheus-3b-0.1-ft"`), also supports Qwen-TTS (`qwen-tts`/`Chelsie` voice) | `orpheus-3b-0.1-ft`, `qwen-tts` | n/a (self-deployed) |
| Slng | `livekit-plugins-slng` 1.8.2 | yes | `livekit.plugins.slng.TTS` | `api_key` (+ `provider_api_key` for BYO upstream) | `SLNG_API_KEY` | `voice: str` (required, no default), `slng_base_url="api.slng.ai"`, `text_chunking`, `warm_standby_enabled` | routes to configured upstream | n/a |
| Smallest AI | `livekit-plugins-smallestai` 1.8.2 | yes | `livekit.plugins.smallestai.TTS` | `api_key` | `SMALLEST_API_KEY` | `model: TTSModels="lightning_v3.1_pro"`, `voice_id`, `output_format="pcm"` | `lightning_v3.1_pro` | UNVERIFIED |
| Soniox | `livekit-plugins-soniox` 1.8.2 | yes | `livekit.plugins.soniox.TTS` | `api_key` | `SONIOX_API_KEY` | `model=DEFAULT_MODEL` (`"tts-rt-v1-preview"`), `voice="Maya"`, `language="en"`, `speed=1.0` | `tts-rt-v1-preview` (Soniox added TTS in addition to its long-standing STT) | UNVERIFIED |
| Speechify | `livekit-plugins-speechify` 1.8.2 | yes | `livekit.plugins.speechify.TTS` | `api_key` | `SPEECHIFY_API_KEY` | `voice_id=DEFAULT_VOICE_ID` (`"dominic_32"`), `model=DEFAULT_MODEL` (`"simba-3.2"`) | `simba-3.2` | Yes — Speechify `GET /v1/voices` |
| Speechmatics (TTS) | `livekit-plugins-speechmatics` 1.8.2 | yes | `livekit.plugins.speechmatics.TTS` | `api_key` | `SPEECHMATICS_API_KEY` | `voice="sarah"`, `base_url="https://preview.tts.speechmatics.com"` (note: "preview" in the URL — likely beta) | `sarah` | UNVERIFIED |
| Spitch | `livekit-plugins-spitch` 1.8.2 | yes | `livekit.plugins.spitch.TTS` | none in ctor (`AsyncSpitch()` client) | UNVERIFIED | `language="en"`, `voice="lina"` (African-language TTS) | `lina` | UNVERIFIED |
| Telnyx | `livekit-plugins-telnyx` 1.8.2 | yes | `livekit.plugins.telnyx.TTS` | `api_key` | none read directly | `voice="Telnyx.NaturalHD.astra"` | `Telnyx.NaturalHD.astra` | UNVERIFIED |
| UpliftAI | `livekit-plugins-upliftai` 1.8.2 | yes | `livekit.plugins.upliftai.TTS` | `api_key` | `UPLIFTAI_API_KEY`, `UPLIFTAI_BASE_URL` | `voice_id=DEFAULT_VOICE_ID` (`"v_meklc281"`), `output_format="MP3_22050_32"` | `v_meklc281` | UNVERIFIED |
| Vakyam | `livekit-plugins-vakyam` 1.8.2 | yes | `livekit.plugins.vakyam.TTS` | `api_key` | `VAKYAM_API_KEY` | `model`, `voice`, `language`, `speed`, `allow_insecure_base_url=False` | India-focused TTS | UNVERIFIED |
| xAI | `livekit-plugins-xai` 1.8.2 | yes | `livekit.plugins.xai.TTS` | `api_key` | `XAI_API_KEY` | `voice: GrokVoices=DEFAULT_VOICE` (`"ara"`), `language="auto"`, `speed`, `optimize_streaming_latency` | 26-voice `GrokVoices` literal: `carina, zagan, helix, orion, luna, iris, ara, eve, leo, rex, sal, ...` | UNVERIFIED |
| AsyncAI | `livekit-plugins-asyncai` 1.8.2 | yes | `livekit.plugins.asyncai.TTS` | `api_key` | `ASYNCAI_API_KEY` | `model: TTSModels="async_flash_v1.0"`, `voice=TTSDefaultVoiceId`, `sample_rate=32000` | `async_flash_v1.0` | UNVERIFIED |
| Gnani (TTS) | `livekit-plugins-gnani` 1.8.2 | yes | `livekit.plugins.gnani.TTS` | `api_key` | `GNANI_API_KEY` | `voice: GnaniTTSVoices="Pranav"`, `base_url="https://api.vachana.ai"`, `synthesize_method: Literal["rest",...]="rest"` | `Pranav` | UNVERIFIED |
| Gradium (TTS) | `livekit-plugins-gradium` 1.8.2 | yes | `livekit.plugins.gradium.TTS` | `api_key` | `GRADIUM_API_KEY`, `GRADIUM_MODEL_ENDPOINT` | `model_name="default"`, `voice_id="4SZHfMpw-p46Ywgs"` | self-deployed | n/a |
| Baseten (TTS) | `livekit-plugins-baseten` 1.8.2 | yes | `livekit.plugins.baseten.TTS` (+ `qwen3_tts.py`) | `api_key` | `BASETEN_API_KEY` | `model: TTSModels="orpheus"`, `voice`, `task_type="Base"`, `x_vector_only_mode`, `ref_audio`/`ref_text` (voice cloning) | `orpheus`, Qwen3-TTS | n/a |
| **PlayAI (out-of-tree, stale)** | `livekit-plugins-playai` **1.2.15**, `pyht>=0.1.14` dep — **not in the 1.8.2 monorepo tree** | **UNVERIFIED vs 1.8.2 core** | `livekit.plugins.playai.TTS` | `api_key` **+ `user_id`** (two-part credential, unusual) | none read directly | `voice="s3://voice-cloning-zero-shot/.../manifest.json"` (manifest URL, not a bare id), `model: TTSModel="PlayDialog"`, `language="english"` | `PlayDialog`, `Play3.0-mini` (per docstring) | UNVERIFIED |

### 1.4 Realtime — see §3 for full depth; summary row per engine

| Provider | Package | Class | Auth | Video in | Half-cascade capable |
|---|---|---|---|---|---|
| Gemini Live | `livekit-plugins-google` 1.8.2 | `google.realtime.RealtimeModel` | `api_key` or Vertex (`vertexai`+`project`/`location`/`credentials`) | Yes | Yes (`modalities=["TEXT"]` + separate TTS) |
| OpenAI Realtime | `livekit-plugins-openai` 1.8.2 | `openai.realtime.RealtimeModel` (+ Azure overload, `.with_azure` classmethod) | `api_key` / Azure (`azure_deployment`+`entra_token`/`api_key`) | No | Yes (`modalities=["text"]`) |
| OpenAI "GPT-Live" | same package | `openai.realtime.GPTLiveModel` | `api_key` | No | delegates via Responses API (`delegation: DelegationTarget="responses"`) |
| xAI Grok Realtime | `livekit-plugins-xai` 1.8.2 | `xai.realtime.RealtimeModel` (subclasses OpenAI's) | `api_key` | No | Yes |
| AWS Nova Sonic | `livekit-plugins-aws` 1.8.2, `experimental.realtime` submodule | `aws.experimental.realtime.RealtimeModel` | flat `api_key`/`api_secret` or AWS chain | No | No (audio-native only; `modalities: "audio"\|"mixed"`) |
| NVIDIA PersonaPlex | `livekit-plugins-nvidia` 1.8.2, `experimental.realtime` | `nvidia.experimental.realtime.RealtimeModel` | `api_key`(NVCF) via `http_session` | No | UNVERIFIED |
| Phonic | `livekit-plugins-phonic` 1.8.2, `realtime` submodule | `phonic.realtime.RealtimeModel` | `api_key` | No | No — Phonic is a fully agent-hosted product (its own tool-calling, MCP servers, welcome messages — see §3) |
| Ultravox | `livekit-plugins-ultravox` 1.8.2, `realtime` submodule | `ultravox.realtime.RealtimeModel` | `api_key` | No | `output_medium: "text"\|"voice"` |
| Azure OpenAI Realtime | `livekit-plugins-openai` (same class, Azure overload) | see OpenAI row | `api_key`/`entra_token` | No | Yes |

### 1.5 Avatar — see §2 for full depth

16 in-tree plugins (unchanged list from v1, re-verified): Anam, Avatario, AvatarTalk, Beyond Presence,
bitHuman, D-ID, Keyframe, LemonSlice, LiveAvatar (HeyGen), Protoface, Runway, Simli, Spatius, Synthesia,
Tavus, TruGen. Plus **Hedra**, which exists on PyPI but is permanently disabled by the vendor (§0.3) — do
not register it as selectable.

### 1.6 VAD / turn detection / noise cancellation

| Kind | Package (version) | Lockstep? | Class | Auth | Notes |
|---|---|---|---|---|---|
| VAD | `livekit-plugins-silero` 1.8.2 | yes | `livekit.plugins.silero.VAD` (`VAD.load(...)` classmethod factory; raw `__init__(session, opts)` takes a pre-built `onnxruntime.InferenceSession`) | none | Local ONNX, no network |
| VAD | `livekit-agents` core (`inference.VAD`) | n/a (ships with core) | `livekit.agents.inference.VAD` | LiveKit project keys (native local-inference binary, effectively free/bundled) | Backed by `livekit-local-inference` pybind11 `.so`, loaded once at import; `VADModels = Literal["silero"]` |
| Turn detection | `livekit-plugins-turn-detector` 1.8.2 | yes | `livekit.plugins.turn_detector.multilingual.MultilingualModel`, `.english.EnglishModel` | none | HF model `livekit/turn-detector`, revisions `v1.2.2-en`/`v0.4.1-intl`, local ONNX + `transformers` + `AutoTokenizer` |
| Turn detection | `livekit-agents` core (`inference.eot.TurnDetector`) | n/a | `livekit.agents.inference.eot.TurnDetector` | LiveKit project keys | `version: "v1"\|"v1-mini"`, auto-picks `v1` when hosted/dev-mode else local `v1-mini`; `local_fallback=True` lets cloud `v1` degrade to local mini (~108MB resident) on gateway failure |
| Noise cancellation | `livekit-plugins-krisp` **0.4.2** (independent) | **no** | `livekit.plugins.krisp.KrispVivaFilterFrameProcessor`, `KrispLicenseAuthProvider`/`LiveKitCloudAuthProvider` | Krisp license key (self-hosted) or LiveKit Cloud project (`LiveKitCloudAuthProvider`) | Krisp Viva SDK, agent-side `rtc.FrameProcessor`; ships its own native wheel `livekit-plugins-krisp-internal` |
| Noise cancellation | `livekit-plugins-noise-cancellation` **0.3.2** (independent, out-of-tree) | **no** | UNVERIFIED class path (not in monorepo; older/simpler Krisp BVC wrapper referenced by many LiveKit sample apps) | LiveKit Cloud project | `requires_dist: livekit>=0.21.3` only — client/room-level, not agent-worker-level |
| Noise cancellation | `livekit-plugins-ai-coustics` **0.3.2** (independent, out-of-tree) | **no** | UNVERIFIED class path | vendor key (UNVERIFIED) | `requires_dist: livekit-agents>=1.4.2` |

### 1.7 Other (non-provider utility packages found in the same monorepo — do NOT register as providers)

| Package | Version | What it actually is |
|---|---|---|
| `livekit-plugins-langchain` | 1.8.2 | LangGraph adapter (`langgraph.py`) — lets a LangGraph graph act as the agent's "LLM" node; orchestration glue, not a model vendor |
| `livekit-plugins-browser` | **0.4.2** (independent, `requires-python>=3.12` — higher floor than the rest of the stack) | Headless-browser control tool for the agent (`browser_agent.py`, `session.py`, `page_actions.py`) — a **tool**, not an STT/LLM/TTS provider |
| `livekit-plugins-hamming` | 1.8.2 | Exports post-call monitoring artifacts to Hamming.ai (`attach_session`, `doctor`/`doctor_json` reports) — an observability sink, not a model |
| `livekit-plugins-nltk` | 1.8.2 | Sentence-tokenizer utility (`sentence_tokenizer.py`) used internally by TTS text-pacing, not user-selectable |
| `livekit-plugins-minimal` | 1.8.2 | The SDK's own "how to write a plugin" stub/template. Never functional; do not list |
| `livekit-blockguard` | UNVERIFIED (no PyPI listing found under this exact name at query time) | C-extension asyncio event-loop blocking-call watchdog (`install(threshold_ms=...)`), a dev/ops tool bundled in the same monorepo, unrelated to providers |
| `livekit-durable` | 0.1.0 (independent, `requires-python <3.15,>=3.10`) | `@durable` function decorator/registry for resumable workflow steps — a framework primitive, not a provider |

---

## 2. Avatars in depth

Universal facts already established and re-confirmed by this pass (re-stated briefly, full detail is in
v1 — not repeated here): every avatar's `AvatarSession` mints its **own** LiveKit access token internally
via `livekit_url`/`livekit_api_key`/`livekit_api_secret` (or env), joins as a second `kind=Kind.Agent`
room participant with `lk.publish_on_behalf` set to the primary agent's identity, and the ordering is always
`avatar.start(session, room=ctx.room)` → (`await avatar.wait_for_join()` optional) → `session.start(...)`.
All 16 in-tree avatars work with **both** cascaded and realtime-model pipelines (confirmed again by AST:
none of the 16 `AvatarSession.__init__` signatures take a pipeline-mode flag — they all just consume
whatever audio `AgentSession` publishes).

### 2.1 Constructor signatures (AST-verified against 1.8.2 source, this pass)

| Avatar | Exact kwargs (from source) | Required ID | Output audio |
|---|---|---|---|
| **Anam** | `persona_config: PersonaConfig` (has `name`, `avatarId` **required**, optional `avatarModel`, `directorNotes`), `session_options`, `api_url`, `api_key`, `avatar_participant_identity/name`, `conn_options` | `persona_config.avatarId` | consumes agent's normal audio out |
| **Avatario** | `avatar_id`, `video_info`, `api_key`, `avatar_participant_identity/name`, `conn_options` | `avatar_id` (env `AVATARIO_AVATAR_ID`, no default) | same |
| **AvatarTalk** | `api_url`, `api_secret` (not `api_key`!), `avatar`, `emotion`, `avatar_participant_identity/name` — **no `conn_options` param at all**, confirmed shortest ctor of the 16 | `avatar` (defaults `DEFAULT_AVATAR_NAME="japanese_man"`, `DEFAULT_AVATAR_EMOTION="expressive"` — has a usable stock default) | same |
| **Beyond Presence (bey)** | `avatar_id`, `api_url`, `api_key`, `avatar_participant_identity/name`, `conn_options` | `avatar_id` optional, stock default `b9be11b8-89fb-4227-8f86-4a881393cbdb` | same |
| **bitHuman** | `api_url`, `api_secret`, `api_token`, `model: "expression"\|"essence"="essence"`, `model_path`, `runtime: AsyncBithuman`, `avatar_image: PIL.Image\|str`, `avatar_id`, `conn_options`, `avatar_participant_identity/name` | exactly one of `model_path`/`avatar_image`/`avatar_id` | same |
| **D-ID** | `agent_id: str` **(positional-or-keyword, no default — the only avatar besides Synthesia whose primary id has no `NotGivenOr` wrapper, i.e. truly required)**, `api_url`, `api_key`, `audio_config: AudioConfig`, `avatar_participant_identity/name`, `conn_options` | `agent_id` (required) | same, with a distinct `audio_config` param — worth checking if D-ID needs specific sample-rate/encoding |
| **Keyframe** | `persona_id`, `persona_slug`, `api_url`, `api_key`, `avatar_participant_identity/name`, `conn_options` | exactly one of `persona_id`/`persona_slug` | same |
| **LemonSlice** | `agent_id`, `agent_image_url`, `agent_image: PIL.Image`, `agent_prompt`, `agent_idle_prompt`, `idle_timeout`, `api_url`, `api_key`, `avatar_participant_identity/name`, `conn_options`, `**kwargs` (extra payload passthrough) | exactly one of `agent_id`/`agent_image_url`/`agent_image` | same |
| **LiveAvatar (HeyGen)** | `avatar_id`, `api_url`, `api_key`, `is_sandbox`, `video_quality: "very_high"\|"high"\|"medium"\|"low"`, `avatar_participant_identity/name`, `conn_options` | `avatar_id` (env `LIVEAVATAR_AVATAR_ID`, no default) | same |
| **Protoface** | `avatar_id=DEFAULT_STOCK_AVATAR_ID` (`"av_stock_001"`), `api_url`, `api_key`, `max_duration_seconds`, `avatar_participant_identity/name`, `conn_options` | none — stock default | same, plus a session-length cap param |
| **Runway** | `avatar_id`, `preset_id`, `max_duration`, `api_url`, `api_key`, `avatar_participant_identity/name`, `conn_options` | exactly one of `avatar_id`/`preset_id`; `api_url` default `"https://api.dev.runwayml.com"` (note: **`.dev.` subdomain**, not `api.runwayml.com` — worth double-checking against Runway's current prod endpoint before shipping) | same, plus `max_duration` cap |
| **Simli** | `simli_config: SimliConfig` (a plain dataclass: **required** `api_key`, `face_id`, optional `emotion_id`), `api_url`, `avatar_participant_identity/name` — **no top-level `api_key` or `conn_options` kwarg**, credential is nested inside `simli_config` | `simli_config.face_id` | same |
| **Spatius** | `api_key`, `app_id`, `avatar_id`, `region`, `console_endpoint_url`, `ingress_endpoint_url`, `avatar_participant_identity/name`, `idle_timeout_seconds=0`, `sample_rate`, `audio_format: AudioFormat="OGG_OPUS"`, `opus_frame_duration_ms=20`, `opus_application`, `extra_params` | `avatar_id` + `app_id` (two-part id, unusual among the 16) | **explicitly configurable output codec** (`AudioFormat.OGG_OPUS` default) — the only avatar plugin surfacing an audio-format knob directly |
| **Synthesia** | positional `avatar_config: AvatarConfig`, then `api_key`, `api_url`, `join_timeout=DEFAULT_JOIN_TIMEOUT`, `avatar_participant_identity/name` — **`avatar_config` is positional, not keyword-only**, the only avatar ctor like this | `avatar_config` (holds up to 5 gallery avatar ids per v1, supports live `swap_avatar()`) | same |
| **Tavus** | `face_id`, `pal_id`, `replica_id`/`persona_id` (deprecated aliases), `api_url`, `api_key`, `avatar_participant_identity/name`, `conn_options` | none — stock PAL/face if both omitted | same |
| **TruGen** | `avatar_id`, `api_key`, `avatar_participant_identity/name`, `conn_options` | `avatar_id` optional (`NotGivenOr[str\|None]`) | same |
| **Hedra (dead)** | `**kwargs: object` — constructor unconditionally raises `HedraException` | n/a — cannot be instantiated | n/a |

Corrections/new findings vs v1 from this constructor pass:
- **AvatarTalk's auth field is `api_secret`, not `api_key`** — v1 listed it as `AvatarTalk(avatar=..., emotion=...)` with env fallbacks `AVATARTALK_AVATAR`/`AVATARTALK_EMOTION` but didn't flag the credential kwarg name; confirmed here directly from the signature.
- **D-ID's `agent_id` and Synthesia's `avatar_config` are true positional/required params**, not `NotGivenOr`-wrapped optionals like every other avatar's id field — the registry's generic "optional field with an env fallback" UI pattern won't fit these two without a `required: true` flag.
- **Simli has no `conn_options` and no top-level `api_key`** — it's the only avatar whose credential lives one level down in a nested dataclass (`simli_config.api_key`), already correctly modeled as a dotted-path field in the current registry's deferred `simli-avatar` entry.
- **Spatius requires two IDs (`app_id` + `avatar_id`)**, not one, and is the only avatar exposing an explicit output audio codec knob (`audio_format`).

### 2.2 Vendor-side facts (list-avatar APIs, pricing, latency, self-host)

*This section is populated from four parallel vendor-research passes (web search + direct OpenAPI-spec/doc
fetches against each vendor's own docs/pricing pages, run 2026-09-19), separate from the LiveKit SDK
source-reading above. Cells are marked UNVERIFIED where the sub-pass could not confirm a fact from a
primary source — treat UNVERIFIED as "ask the vendor / re-check before shipping copy that depends on it,"
not as false. Sources are inline per cell.*

| Vendor | List-avatars API (endpoint + auth) | Pricing (public tiers + URL) | Latency (published) | Self-host / local |
|---|---|---|---|---|
| **Anam** (anam.ai) | **Confirmed**: `GET https://api.anam.ai/v1/avatars` (also `/v1/personas`), `Authorization: Bearer $ANAM_API_KEY`. [anam.ai/docs/llms-full.txt](https://anam.ai/docs/llms-full.txt) | No official pricing page found; third-party trackers cite ~$49/mo + $0.11–0.20/min overage — **UNVERIFIED as vendor-published** | **Confirmed**: "180ms median server-side latency," "sub-1-second median" for the conversation engine. [anam.ai/api](https://anam.ai/api) | Cloud-only — no on-prem/self-host mention found |
| **Avatario** (avatario.ai) | **Probable, not confirmed**: `GET https://avatario.ai/api/avatars/stock` (found in a search snippet; docs page is a JS-rendered SPA the fetch tool couldn't fully load, auth header unconfirmed). Practical fallback: pick an id from [avatario.ai/dashboard](https://avatario.ai/dashboard/) | UNVERIFIED — no pricing page rendered (404); a circulating "$19/150 credits" figure appears to actually belong to HeyGen LiveAvatar, discarded as unreliable | UNVERIFIED — not in vendor docs or the [LiveKit integration guide](https://docs.livekit.io/agents/models/avatar/plugins/avatario/) | UNVERIFIED, appears cloud-only |
| **AvatarTalk** (avatartalk.ai, API host `api.avatartalk.ai`) | **No list endpoint** — avatars are a **static enum** of 14 named avatars (`japanese_man`, `european_woman`, `arab_man`, ...) passed as a string to `POST https://api.avatartalk.ai/inference` / `wss://.../ws/infer`. `Authorization: Bearer {api_key}`. [API.md](https://github.com/avatartalk-ai/avatartalk-examples/blob/main/API.md) | UNVERIFIED — no pricing page (404); docs mention pay-per-second billing + Lightning Network invoicing, no tier table | UNVERIFIED — only generic "low-latency" marketing claim | **Yes, explicit**: "Available in the cloud, on-premise or embedded on-device" with named hardware (NVIDIA DGX Spark, L4). [avatartalk.ai](https://avatartalk.ai/) |
| **Beyond Presence (bey)** (bey.dev / beyondpresence.ai) | **Confirmed**: `GET https://api.bey.dev/v1/avatars`, `x-api-key` header. [docs.bey.dev/get-started/api](https://docs.bey.dev/get-started/api) | **Confirmed tiers**: Free (€0, 40 min, 1 concurrent), Starter €49/mo, Growth €149/mo, Scale €349/mo, Enterprise custom incl. on-prem. [beyondpresence.ai/pricing](https://www.beyondpresence.ai/pricing) | **Confirmed**: audio-to-video model ~100ms; managed agents ~1.0–1.2s end-to-end; marketing claims "<250ms" response. [beyondpresence.ai](https://www.beyondpresence.ai/) | **Yes, Enterprise-gated**: "On-Premise Deployments" listed as an Enterprise-tier feature. [beyondpresence.ai/pricing](https://www.beyondpresence.ai/pricing) |
| **bitHuman** (bithuman.ai) | UNVERIFIED exact path — an OpenAPI spec exists at `docs.bithuman.ai/api-reference` and avatar/persona ids are visible in the console "Library" page, but no confirmed public GET-list endpoint | Credit-based: 99 free credits/mo, no card; ~2 credits/min cloud (1 credit/min self-hosted); +250 credits flat for agent generation; annual saves ~17%. [bithuman.ai/pricing](https://www.bithuman.ai/pricing) | **Confirmed**: "<200ms end-to-end latency," 25 FPS, runs on 1–2 CPU cores. [docs.bithuman.ai](https://docs.bithuman.ai/) | **Both confirmed.** Cloud API (hosted, per-minute) **and** genuine local/on-device mode: `pip install bithuman` (Python) or Swift SDK, loads a downloadable `.imx` model via `AsyncBithuman.create(model_path="model.imx", api_secret=...)`, runs fully offline on CPU/NVIDIA GPU/Apple Silicon — only network call is a 1-req/min billing heartbeat. [docs.bithuman.ai](https://docs.bithuman.ai/), [github.com/bithuman-prod/public-macos-offline-example](https://github.com/bithuman-prod/public-macos-offline-example) |
| **D-ID** (d-id.com) | **Confirmed**: `GET https://api.d-id.com/clips/presenters` (V3 Pro/Clips avatars) and `GET https://api.d-id.com/scenes/avatars`; Basic or Bearer token. [docs.d-id.com/reference/getpresenters](https://docs.d-id.com/reference/getpresenters), [.../getavatars](https://docs.d-id.com/reference/getavatars) | Free 14-day trial (3 min, watermarked); Lite ~$4.70/mo, Pro ~$16/mo, Advanced ~$108/mo, Enterprise by consultation. [d-id.com/pricing/studio](https://www.d-id.com/pricing/studio/) | Published: sub-200ms, ~100 FPS pipeline, lip-sync within 30ms of audio. [computerweekly.com](https://www.computerweekly.com/blog/CW-Developer-Network/Inside-D-IDs-real-time-AI-avatar-technology) | UNVERIFIED — no on-prem offering found; appears cloud/API-only |
| **Keyframe** (keyframelabs.com) | `GET /v1/avatars` returns the org's personas (`persona_id`, `slug`, `display_name`, `model_id`); auth `KEYFRAME_API_KEY` (`keyframe_sk_live_...`), base host UNVERIFIED (likely `api.keyframelabs.com`, not confirmed). [github.com/keyframelabs/docs PR#2](https://github.com/keyframelabs/docs/pull/2) | From $0.06/min, free to start, scales to 100s of concurrent connections. [keyframelabs.com](https://keyframelabs.com/) | UNVERIFIED — only "real-time" marketing language | **Yes** — a documented "Self-managed" integration path where "keys stay on your infrastructure," distinct from Hosted/Widget modes. [docs.keyframelabs.com/guides/integrate/self-managed](https://docs.keyframelabs.com/guides/integrate/self-managed) |
| **LemonSlice** (lemonslice.com) | UNVERIFIED — no list-faces/avatars REST endpoint documented (checked `/docs`, `/docs/self-managed/overview`, `/avatar-api`, `llms.txt`); only a public gallery page, not an API | Starter $7–8/mo (1,000 credits ≈41 min, 3 concurrency) up to Scale $200–240/mo; overage ~$0.164–0.22/min; Enterprise custom; free unauthenticated chat with existing avatars, no free API tier. [lemonslice.com/pricing](https://lemonslice.com/pricing) | Published: Flash model ~471ms p99 time-to-first-byte; ~2.04s avg end-to-end incl. STT+LLM+TTS. [lemonslice.com](https://lemonslice.com/) | Cloud-only. "Self-Managed Pipeline" means *you* orchestrate WebRTC/session logic against LemonSlice's cloud API, not on-prem model execution. [docs](https://lemonslice.com/docs/self-managed/overview) |
| **LiveAvatar** (liveavatar.com, by HeyGen) | **Confirmed via raw OpenAPI spec** (`api.liveavatar.com/openapi.json`): `GET https://api.liveavatar.com/v1/avatars/public` (no auth) lists public avatars; `GET https://api.liveavatar.com/v1/avatars` (`X-API-KEY` header) lists the account's own avatars | Free (10 credits/mo, 2-min sessions, watermarked) up to Business $475/mo (5,000 credits); FULL mode 1 credit=30s, LITE mode 1 credit=60s. [liveavatar.com](https://www.liveavatar.com/) | UNVERIFIED — no latency numbers in docs.liveavatar.com or HeyGen help center | Cloud-only — no on-prem option found |
| **Protoface** (protoface.com — note: `.ai` is only their GitHub org name, live product is `.com`) | UNVERIFIED exact endpoint — docs reference a "create and manage avatars" guide, no confirmed REST path; general inference auth is `Authorization: Bearer $PROTOFACE_API_KEY`. [docs.protoface.com](https://docs.protoface.com/) | Free tier, no card; realtime avatars from $0.01/min; other models (MiniMax H3 $0.01/sec, LTX 2.5 $0.003/sec). [protoface.com/pricing](https://www.protoface.com/pricing) | Marketing claims "under 300ms" face-motion latency, no benchmarks page. [protoface.com](https://www.protoface.com/) | UNVERIFIED — described as managed/cloud inference ("no need to manage GPUs"), no self-host surfaced |
| **Runway** (runwayml.com, "Characters" product — confirmed a **distinct API/product** from Runway's core video-gen API, its own "Dev" docs + LiveKit/React SDKs) | UNVERIFIED as a list call — Characters are created per-session from a reference image, not chosen off a fixed catalog; only a tangential `/v1/avatars/{id}/conversations` endpoint found | 2 credits upfront + 2 credits/6s active session, credits at $0.01 each. [docs.dev.runwayml.com/characters](https://docs.dev.runwayml.com/characters/concepts/) | Published: "37ms effective model time/frame" (24fps), "1.75s server-side turnaround" from end-of-speech to avatar response start | Cloud-only |
| **Simli** (simli.com) | **Confirmed via raw OpenAPI spec** (`docs.simli.com/api-reference/openapi.yaml`, server `https://api.simli.ai/`): `GET /faces` ("Get all faces"), header `x-simli-api-key` — resolves v1's UNVERIFIED to **confirmed yes** | $10 free signup credit + 50 free min/mo top-up; dedicated `/pricing` page 404s, full tier table UNVERIFIED. [simli.com](https://www.simli.com/) | Published: speech-to-video component <300ms (full STT/LLM/TTS pipeline adds more). [simli.com](https://www.simli.com/) | Cloud-only — no self-host option found |
| **Spatius** (spatius.ai) | UNVERIFIED — no public REST endpoint found; avatars browsed via web UI (`app.spatius.ai/avatars/library`) and CLI | Free (1,000 credits ≈100 min, 10-min session cap), Starter $19/mo, Builder $49, Growth $149, Scale $299 ($0.0056–0.009/min); Enterprise custom. [spatius.ai/blog/cheapest-real-time-ai-avatar-api-2026](https://www.spatius.ai/blog/cheapest-real-time-ai-avatar-api-2026/) | UNVERIFIED — no published number found | Cloud-only. Architecture is a hosted "Motion Server" streaming motion data to client SDKs (Web/iOS/Android/Flutter) for **on-device rendering** (not on-device generation) — no on-prem/local-runtime option |
| **Synthesia** (synthesia.io) | UNVERIFIED as a callable endpoint — [docs.synthesia.io/reference/avatars](https://docs.synthesia.io/reference/avatars) is a static reference table of stock avatar IDs, retrieved via UI "copy ID," not a documented programmatic list call | Basic free (1,200 credits/mo), Starter $18/mo, Creator $64/mo (annual), Enterprise custom; free trial. [synthesia.io/pricing](https://www.synthesia.io/pricing) | UNVERIFIED — page only says the team is "continuing to improve" latency, no number | Cloud-only — Interactive Avatars route audio to a "hosted avatar worker" via LiveKit; no self-hosted option found |
| **Tavus** (tavus.io / tavusapi.com) | **Confirmed, most complete of the 16**: current `GET https://tavusapi.com/v2/faces` (replaces `replicas`) and `GET https://tavusapi.com/v2/pals` (replaces `personas`) — legacy aliases `/v2/replicas`/`/v2/personas` still work; auth `x-api-key` header. [docs.tavus.io/api-reference/replica-model/get-replicas](https://docs.tavus.io/api-reference/replica-model/get-replicas), [.../personas/get-personas](https://docs.tavus.io/api-reference/personas/get-personas) | Tiered/usage-based per tavus.io/pricing — page not fetched this pass, treat as **UNVERIFIED detail** (existence of a public page is likely true, numbers are not confirmed here) | UNVERIFIED — not found in pages reviewed | Cloud-only (`tavusapi.com` is the only integration surface found) |
| **TruGen** (trugen.ai) | UNVERIFIED exact path — docs reference a "list all avatars" module (`docs.trugen.ai/api-reference`) but the path 404'd on direct fetch | Pricing page returned 502/403 on every fetch attempt — UNVERIFIED | "Under one second" claimed in TruGen's own Huma-1/Huma-2 blog post, no precise ms figure. [trugen.ai/blog](https://trugen.ai/blog/meet-huma-1-the-avatar-model-that-finally-makes-ai-feel-human) | A **third-party** listing (SaaSWorthy) claims "self-hosted deployments upon request" — not confirmed on TruGen's own site (blocked, 403); treat as unconfirmed-by-vendor |

**Cross-cutting notes from the vendor-research pass:**
- Only **Simli**, **Anam**, **Beyond Presence**, **D-ID**, and **LiveAvatar** have a *confirmed* (spec- or
  doc-page-verified) public list-avatars/faces REST endpoint. **Tavus** is the most complete: it has both a
  faces list and a pals (persona) list, replacing older `replicas`/`personas` naming. **Keyframe** and
  **bitHuman** likely have one (an OpenAPI spec/PR exists) but the exact path wasn't independently confirmed.
  **Avatario, LemonSlice, Protoface, Runway, Spatius, Synthesia, TruGen, AvatarTalk** either gate avatar
  selection behind a dashboard/UI only, or (Runway, AvatarTalk) don't have a fixed catalog to list at all —
  these need a manual "paste your avatar/face id" admin field, not a dynamic dropdown, until proven otherwise.
- **Self-host is real and Enterprise-relevant for four vendors**: bitHuman (fully local/offline, cheapest and
  best-documented), AvatarTalk (cloud/on-prem/on-device, explicitly named hardware), Beyond Presence
  (Enterprise-tier on-prem), and Keyframe ("self-managed" mode). Everyone else in the 16 is cloud-only as far
  as this pass could confirm.
- **Latency claims cluster around 100–300ms model-only, ~1–2s full-pipeline**: bitHuman <200ms, Simli <300ms,
  D-ID sub-200ms/30ms lip-sync, Anam 180ms median, Beyond Presence ~100ms model / ~1.0–1.2s end-to-end,
  LemonSlice ~471ms p99 TTFB / ~2.04s full pipeline, Runway 37ms/frame model + 1.75s server turnaround. Treat
  "model latency" and "end-to-end latency incl. STT+LLM+TTS" as different numbers when comparing vendors —
  several publish only one or the other.

### 2.3 Integration priority ranking

Updated with §2.2's vendor facts (pricing/latency/list-API/self-host), not just constructor shape:

1. **Beyond Presence (bey)** — zero required fields beyond the API key (stock default avatar id), a
   **confirmed** list-avatars API (`GET api.bey.dev/v1/avatars`), a **confirmed public free tier** (40
   min/mo) plus transparent paid tiers, **confirmed** ~100ms model / ~1.0–1.2s end-to-end latency, and an
   **Enterprise on-prem option** for later scale-up. This is now the strongest MVP anchor of the 16 on
   evidence, not just constructor shape.
2. **Tavus** — zero required fields beyond the API key (stock PAL/face), and **the most complete vendor API
   of the set**: confirmed `GET /v2/faces` and `GET /v2/pals` list endpoints (the only vendor with both a
   face list and a persona list independently confirmed), Python+Node parity. Pricing/latency numbers were
   not independently re-verified this pass (treat as "public page exists, not read") — worth a follow-up
   fetch of `tavus.io/pricing` before finalizing customer-facing copy.
3. **Simli** — one required field (`face_id`), and this pass **upgrades v1's "UNVERIFIED" to confirmed**:
   `GET https://api.simli.ai/faces` with `x-simli-api-key` auth is real (read from Simli's own OpenAPI spec).
   Confirmed <300ms model latency and a real free tier ($10 signup credit + 50 free min/mo). Still
   Python-only among Node-supporting peers, and its credential's nested-dataclass shape needs one extra
   factory-side unwrap step.
4. **bitHuman** — re-ranked *up* from v1/first-pass thinking once §2.2 landed: it is the **only vendor with
   a confirmed, well-documented, genuinely free local/offline mode** (open `pip install bithuman` + `.imx`
   model file, runs on CPU/Apple Silicon/NVIDIA GPU with no per-minute cloud billing once the model is
   downloaded) *and* a real cloud API with a no-card-required free tier (99 credits/mo) and the best
   published latency of the set (<200ms). For a platform that wants a demo/dev-mode avatar with zero
   recurring vendor cost, or an eventual on-prem/data-residency story, bitHuman is the strongest option —
   trade-off is the platform-restricted native wheel (§4.4: macOS arm64 + manylinux only, no Windows).
5. **AvatarTalk** — shortest constructor of all 16 (no `conn_options`), usable stock default
   (`avatar="japanese_man"`), and is the **only other vendor besides bitHuman with a genuine on-prem/on-device
   option** (explicitly named hardware, NVIDIA DGX Spark/L4) — interesting as a second self-host-capable pick,
   but pricing is UNVERIFIED and it reads as a smaller/newer vendor than bey/Tavus/Simli.

Everything else (Anam, Avatario, D-ID, Keyframe, LemonSlice, LiveAvatar, Protoface, Runway, Spatius,
Synthesia, TruGen) either requires a pre-chosen vendor-dashboard id with no usable default, lacks a
confirmed list-avatars API (so the admin UI can't offer a live dropdown without a manual paste-id fallback),
or both. Anam is the standout among this group on latency (180ms median, confirmed) and has a confirmed
list API — worth a 6th-place mention if the platform wants one more avatar with programmatic listing.
**Hedra is excluded entirely — its plugin is permanently disabled by the vendor (§0.3).**

---

## 3. Realtime models in depth

### 3.1 Core mechanics shared across all realtime engines (verified against `livekit-agents` core, not plugin code)

- **`reply_required`** is a field on `FunctionCallOutput` (`livekit/agents/llm/chat_context.py:380`,
  default `True`), documented in source as *"Whether the model should answer once it receives this output.
  Only realtime models read it, since [cascaded/text] models answer a result on their own."* A tool can flip
  it off (`FunctionToolsExecutedEvent.cancel_tool_reply()` sets every output's `reply_required = False`,
  `voice/generation.py:1223`) to suppress the model speaking after a tool call — this is a **core-SDK
  concept usable with any realtime model**, not Gemini-specific.
- **`TOOL_BEHAVIOR = Literal["UNSPECIFIED", "BLOCKING", "NON_BLOCKING"]`** is defined in
  `llm/_provider_format/google.py` — it is **Gemini-specific wire format**, not a general realtime concept;
  it maps Gemini's own `tool_behavior`/`tool_response_scheduling` fields onto the generic `reply_required`
  mechanism above. The current registry's `google-realtime` entry already models `tool_behavior` and
  `tool_response_scheduling` as fields — confirmed correct, no changes needed there.
- **NON_BLOCKING**, concretely: a Gemini Live tool call marked non-blocking lets the model keep talking (or
  stay silent per `tool_response_scheduling: "SILENT"`) while the tool runs, then optionally works the
  result back in later rather than always pausing for it — this is the "silent tool reply" capability the
  registry's `ProviderCapabilities.silent_tool_reply` flag gates.

### 3.2 Google Gemini Live — `livekit.plugins.google.realtime.RealtimeModel`

- **Model ids** (`LiveAPIModels` literal, exact — confirmed from `realtime/api_proto.py`):
  `gemini-live-2.5-flash-native-audio` (Vertex AI, GA), `gemini-3.8-live`, `gemini-3.8-live-extended-thinking`,
  `gemini-3.1-flash-live-preview`, `gemini-2.5-flash-native-audio-preview-12-2025` (Gemini API). The
  registry's current `models` list (`gemini-3.8-live`, `gemini-3.1-flash-live-preview`,
  `gemini-2.5-flash-native-audio-preview-12-2025`) is **confirmed correct** against this literal — no
  changes needed. `gemini-3.8-live-extended-thinking` and the Vertex-only
  `gemini-live-2.5-flash-native-audio` id are the two the registry is missing.
- **Voices** (`Voice` literal, confirmed exhaustive — 30 entries, read in full from
  `realtime/api_proto.py`): `Achernar, Achird, Algenib, Algieba, Alnilam, Aoede, Autonoe, Callirrhoe,
  Charon, Despina, Enceladus, Erinome, Fenrir, Gacrux, Iapetus, Kore, Laomedeia, Leda, Orus, Pulcherrima,
  Puck, Rasalgethi, Sadachbia, Sadaltager, Schedar, Sulafat, Umbriel, Vindemiatrix, Zephyr,
  Zubenelgenubi`. The registry's `GEMINI_LIVE_VOICES` (8 names: Kore, Puck, Charon, Aoede, Fenrir, Leda,
  Orus, Zephyr) is a **curated subset** of this 30-voice literal, not the full list — all 8 are valid
  members, but the UI dropdown could offer the remaining 22 as well. Recommend updating
  `GEMINI_LIVE_VOICES` to the full 30 (or explicitly keep it curated and say so in a code comment).
- **Video input**: yes — confirmed via `image_encode_options: images.EncodeOptions` ctor param and
  `modalities` list.
- **Tool calling**: yes, with the `tool_behavior`/`tool_response_scheduling`/`reply_required` mechanism above.
- **Half-cascade**: yes — set `modalities=["TEXT"]` and pair with a separate LiveKit `TTS` plugin.
- **Known limits**: `context_window_compression`, `session_resumption` params exist (long-session handling);
  `proactivity: bool` (model can speak unprompted) and `enable_affective_dialog: bool` are Gemini-only
  personality knobs with no equivalent in other realtime engines.

### 3.3 OpenAI Realtime / GPT-Live — `livekit.plugins.openai.realtime.{RealtimeModel,GPTLiveModel}`

- **`RealtimeModel`**: `model="gpt-realtime"` (only literal default seen; `RealtimeModels | str` allows
  free text), `voice=DEFAULT_VOICE` (confirmed **`"marin"`**), `modalities: list["text","audio"]`,
  `turn_detection`, `input_audio_noise_reduction`, `tracing`, `truncation`, `reasoning`, `speed`,
  `max_session_duration`. A **second overload in the same file, `RealtimeModel(...)` with `azure_deployment`/
  `entra_token`/`api_version` kwargs**, plus a **classmethod `RealtimeModel.with_azure(cls, *,
  azure_deployment: str, azure_endpoint=None, api_key=None, entra_token=None, ...)`** — so Azure OpenAI
  Realtime is reachable two ways from the same class (direct kwargs or the classmethod), both confirmed in
  source.
- **`GPTLiveModel`** (the newer "full-duplex" surface): `model=DEFAULT_MODEL`, `voice: GPTLiveVoices|str|dict
  =DEFAULT_VOICE`, **`delegation: DelegationTarget="responses"`**, `responses_options:
  ResponsesDelegationOptions` — this model **delegates response generation to the Responses API** rather
  than generating audio end-to-end itself; `reply_required`/tool semantics for this path run through the
  Responses-API delegation options, not the classic Realtime event stream.
- **Video input**: no `image`/`video` param on either class — confirmed no video support, matching v1 and
  the registry's `video_input=False`.
- **Tool calling**: yes, `tool_choice: llm.ToolChoice | None`.
- **Known limits**: `max_session_duration` exists on every OpenAI realtime variant — a hard ceiling the
  platform should surface (session auto-ends and must be resumed/reconnected), unlike Gemini's
  `session_resumption` config which manages this more gracefully.

### 3.4 xAI Grok Voice Agent — `livekit.plugins.xai.realtime.RealtimeModel`

- Subclasses `openai.realtime.RealtimeModel` (protocol-compatible).
- **Model ids** (`GrokRealtimeModels`, confirmed exhaustive): `grok-voice-latest`, `grok-voice-think-fast-2.0`,
  `grok-voice-think-fast-1.0`, `grok-voice-fast-1.0`.
- **Voices** (`GrokVoices`, confirmed exhaustive, 26 entries): `carina, zagan, helix, orion, luna, iris,
  altair, zenith, perseus, helios, lux, kepler, rigel, cosmo, celeste, ursa, sirius, lumen, castor, naksh,
  atlas, ara, eve, leo, rex, sal`. Default `voice="Ara"`.
- **Video input**: no.
- **Tool calling**: yes (`llm.ToolChoice`), plus `reasoning: RealtimeReasoning`.
- **Half-cascade**: not applicable in the same way as Gemini/OpenAI (no `modalities` param on this class) —
  it is audio-native only per this constructor.

### 3.5 AWS Nova Sonic — `livekit.plugins.aws.experimental.realtime.RealtimeModel`

Confirmed to exist and read directly (v1 had this as UNVERIFIED "likely" — now confirmed exact):

- **Path**: `livekit.plugins.aws.experimental.realtime.RealtimeModel` — the `experimental` package qualifier
  is real and part of the public import path, signalling non-GA/subject-to-change per LiveKit's own naming
  convention.
- **Model ids** (`REALTIME_MODELS`, exhaustive): `"amazon.nova-sonic-v1:0"`, `"amazon.nova-2-sonic-v1:0"`
  (default).
- **Voices**: two generations, `SONIC1_VOICES` (11: matthew, tiffany, amy, lupe, carlos, ambre, florian,
  greta→tina in v2, lennart, beatrice, lorenzo) and `SONIC2_VOICES` (adds `olivia`, `carolina`, `leo`,
  renames German `greta`→`tina`, marks several "Polyglot"). Default voice `"tiffany"`.
  `voice: NotGivenOr[SONIC1_VOICES | SONIC2_VOICES | str]`.
- **Auth**: flat `api_key`/`api_secret` (AWS access key/secret) or `session: AioSession|aioboto3.Session`,
  `region: NotGivenOr[str]` (no fixed default shown in this ctor, unlike `aws.LLM`'s `"us-east-1"`).
- **Modalities**: `MODALITIES = Literal["audio", "mixed"]`, default `"mixed"` — **no `"text"`-only mode**,
  meaning Nova Sonic has **no half-cascade path** (can't run text-only + separate TTS the way Gemini/OpenAI
  can).
- **Turn detection**: `TURN_DETECTION = Literal["HIGH", "MEDIUM", "LOW"]`, default `"MEDIUM"` — a
  sensitivity-tier enum, not a numeric threshold like other engines.
- **Tool calling**: yes (`tool_choice: llm.ToolChoice | None`), plus a `generate_reply_timeout: float = 10.0`
  — a hard timeout distinct from any other realtime engine's params, worth surfacing as a field.
- **Video input**: no.

### 3.6 Azure (two distinct integration paths, both confirmed)

1. **Azure OpenAI Realtime** — the same `openai.realtime.RealtimeModel` class, Azure overload/`.with_azure`
   classmethod (§3.3). Credential shape: `azure_deployment`+`api_key`/`entra_token`+`azure_endpoint`.
2. **Azure AI Speech is NOT a realtime engine** — it is STT/TTS only (`azure.STT`/`azure.TTS`,
   `speech_key`/`speech_region`), confirmed no `realtime` submodule exists under
   `livekit-plugins-azure/livekit/plugins/azure/` (only `stt.py`, `tts.py`, `responses/llm.py`). Do not
   register an "Azure Realtime (Speech)" entry distinct from Azure OpenAI Realtime — there isn't one.

### 3.7 Ultravox — `livekit.plugins.ultravox.realtime.RealtimeModel`

- **Model ids** (`UltravoxModel`, exhaustive): `fixie-ai/ultravox`, `fixie-ai/ultravox-gemma3-27b-preview`,
  `fixie-ai/ultravox-llama3.3-70b`, `fixie-ai/ultravox-qwen3-32b-preview`. Default `"fixie-ai/ultravox"`.
- **Voices** (`UltravoxVoice`, exhaustive — only two built-in + custom): `"Mark"`, `"Jessica"`; plus
  `external_voice: dict` for a fully custom TTS voice config.
- **Auth**: `api_key: str | None = None` (env fallback UNVERIFIED — not read via `os.environ` in this file).
- **Video input**: no.
- **Half-cascade**: `output_medium: "text" | "voice"` — yes, text-only mode exists.
- **Known limits**: `max_duration: str`, `time_exceeded_message: str`, `first_speaker:
  "FIRST_SPEAKER_USER"` (who talks first is configurable, unusual among these engines), `language_hint`.

### 3.8 Phonic — `livekit.plugins.phonic.realtime.RealtimeModel` (new to this catalog, not in v1)

By far the most heavily-parameterized realtime constructor found (40+ kwargs) — Phonic is less "a model"
and more "a fully hosted conversational-agent platform" that happens to expose a LiveKit realtime-model
adapter:

- **Auth**: `api_key`, `phonic_agent` (a pre-configured agent id on Phonic's own platform — most of the
  behavior below is normally configured server-side per `phonic_agent`, with these kwargs as local
  overrides).
- **Notable unique params**: `welcome_message`/`generate_welcome_message` (scripted opener),
  `multilingual_mode: "auto"|"request"`, `min_words_to_interrupt` (barge-in tuning), `no_input_poke_sec`/
  `no_input_poke_text`/`no_input_end_conversation_sec` (silence-handling state machine built into the
  model), `enable_assistant_backchannel`/`assistant_backchannel_aggressiveness` ("mm-hmm"-style
  backchanneling), `pronunciation_dictionary`, `enable_redaction`, `enable_watermarking`,
  `mcp_servers: list[str]` (Phonic can call **MCP servers directly**, bypassing LiveKit's own function-tool
  path), `observability_integrations`, `forbid_speech_after_tool_call`.
- **Video input**: no.
- **Implication for the platform**: Phonic doesn't fit the "generic realtime model + our own tool/handoff
  layer" mental model as cleanly as Gemini/OpenAI/xAI — a large slice of its behavior (tools, prompts, voice)
  lives in Phonic's own dashboard against `phonic_agent`, so the LiveKit-side config is closer to "which
  Phonic agent to attach" than "which model+voice+tools to configure here." Flag this as a UX-design
  decision, not just a registry field list, before prioritizing it.

### 3.9 NVIDIA PersonaPlex — `livekit.plugins.nvidia.experimental.realtime.RealtimeModel` (new to this catalog)

- **Auth**: `http_session`-based, no explicit `api_key` kwarg shown in this constructor (`base_url`,
  `voice`, `text_prompt`, `seed`, `silence_threshold_ms=500`) — likely reads an NVCF key from the session or
  environment; **UNVERIFIED** exact credential path, worth a follow-up read of the file's connection-setup
  method before shipping.
- **Voices**: `PersonaplexVoice` literal, default `"NATF2"` (full list not captured in this pass —
  UNVERIFIED beyond the default).
- **Video input**: no. **Half-cascade**: UNVERIFIED. This is the least-mature-looking realtime integration
  of the set (bare `experimental` path, no `conn_options`, no `tool_choice` param at all in the
  constructor) — likely lowest priority to wire up.

---

## 4. Dependency compatibility (empirically verified, not asserted)

**Method**: built a requirements file listing every one of the 70 `livekit-plugins-*`/`livekit-*` packages
(minus the 5 confirmed non-provider utility packages, §1.7) at their real PyPI versions, plus the 3
out-of-tree noise-cancellation packages, and ran `uv pip compile ... --python-version 3.12`. This resolves
the **entire real PyPI dependency graph**, not just top-level version strings — the earlier per-package
"lockstep?" column already flags the two real version outliers; this section is about whether they can all
be *installed together*, which is a different and harder question.

### 4.1 Result: 69 of the 70 provider packages resolve together into one coherent set

Excluding only `livekit-plugins-minimax`, `uv pip compile` resolved **216 packages** cleanly on Python 3.12
with **zero conflicts** — every avatar, STT, LLM, TTS, realtime, VAD, turn-detector, and noise-cancellation
package (including the two out-of-tree noise-cancellation packages and Krisp) can share one virtualenv and
one `livekit-agents==1.8.2` core. This is a stronger and more useful finding than v1's per-package version
table, because it's a real resolver run, not an inference from matching version strings.

### 4.2 The one real, confirmed conflict: MiniMax

`livekit-plugins-minimax`'s **latest published release is 1.3.0** (not lockstep-tracking core), and its
`requires_dist` pins **`livekit-agents==1.2.9`** — an *exact* pin, five major-ish releases behind the
1.8.2 baseline everything else in this catalog needs `>=1.8.2` for. `uv`'s resolver output, verbatim:

```
Because livekit-plugins-anam==1.8.2 depends on livekit-agents>=1.8.2
and livekit-plugins-minimax==1.3.0 depends on livekit-agents==1.2.9,
we can conclude that livekit-plugins-anam==1.8.2 and
livekit-plugins-minimax==1.3.0 are incompatible.
```

**MiniMax TTS cannot be installed in the same environment as any other 1.8.2-lockstep plugin.** This is a
hard blocker, not a "nice to check" — if a worker image needs MiniMax, it needs its own isolated environment
(or the platform waits for MiniMax to publish a release compatible with current core).

### 4.3 Python-version floor differences

- Floor for almost everything: `requires-python >= 3.10` (fully lockstep packages) or `>=3.10.0` (same
  thing, some packages just spell the patch digit).
- **`livekit-plugins-browser` requires `>=3.12`** — strictly higher than the rest of the stack. A worker
  image built for Python 3.10 or 3.11 (still valid for everything else) **cannot** install the browser tool
  plugin; `uv pip compile --python-version 3.10` fails immediately citing this package. If the platform ever
  supports the browser-control tool, its worker image needs Python ≥3.12 regardless of what any other
  provider needs.
- **`livekit-durable` caps at `<3.15`** — the only package in the whole graph with an upper bound; harmless
  today (3.15 doesn't exist yet) but will need re-checking whenever Python 3.15 ships.
- Everything else (including `bithuman`) resolved cleanly at 3.10, 3.12, 3.13, **and 3.14** in this pass —
  see §4.4, this **corrects v1's claim** that bitHuman needs "Python 3.11, 3.12, or 3.13 specifically."

### 4.4 Native / platform-restricted wheels (the real constraint behind v1's bitHuman claim)

v1 said bitHuman needs "Python 3.11–3.13 specifically." Checking the actual `bithuman` PyPI wheel list
(the underlying runtime SDK the plugin wraps) shows this was never quite the right framing — **the real
constraint is platform, not Python version**:

- `bithuman` ships wheels for **cp310 through cp314** (confirmed: `bithuman-2.11.4-cp310-...` through
  `-cp314-...` all exist), each only for **`macosx_14_0_arm64`** (Apple Silicon only, no Intel Mac) and
  **`manylinux_2_28_{aarch64,x86_64}`** (a fairly recent glibc baseline — excludes Alpine/musl Linux
  entirely, and excludes older glibc distros pinned below manylinux_2_28).
- **There is no Windows wheel for `bithuman` at all**, at any Python version. A Windows-based worker image
  cannot install `livekit-plugins-bithuman`, full stop — this is a materially different and more actionable
  fact than a Python-version ceiling, and the platform's "installed in this worker image" registry flag
  (see below) needs a platform axis, not just a Python-version axis.
- Its cloud+image mode also needs `livekit-agents[images]` (Pillow) which is already in the resolved set
  above at `pillow==12.3.0` — no extra conflict, just an extra install target to remember to include.

### 4.5 Other native/heavy dependencies surfaced by the real resolution (not from memory)

- `onnxruntime==1.30.0` (Silero VAD + the turn-detector plugin) and `transformers==5.17.0` (turn-detector's
  tokenizer) — **no `torch` was pulled in** by this resolution, confirming the turn-detector plugin's
  ONNX-only inference path doesn't require a PyTorch install (a meaningfully lighter footprint than most
  "transformers-based" tooling implies).
- `opencv-python-headless`, `soundfile`, `loguru`, `pydantic-settings` — all pulled in transitively by
  `bithuman` alone; it has by far the heaviest dependency footprint of the 16 avatar plugins.
- `azure-cognitiveservices-speech` (native Azure Speech SDK binary) and `awscrt` (native AWS Rust-backed
  transport) — both platform-specific native wheels; neither showed a resolution failure in this pass but
  both are typical sources of surprise on unusual platforms (e.g. Alpine/musl, ARM64 Windows) that `uv`'s
  metadata-only resolution wouldn't catch (it doesn't verify a wheel is actually downloadable/runnable on
  every platform, only that *some* wheel/sdist satisfies the version constraint).
- `livekit-plugins-krisp` pulls a matching **`livekit-plugins-krisp-internal`** native wheel — another
  proprietary-binary dependency to flag for the same "does this platform actually have a wheel" caveat as
  bitHuman and Azure Speech.

### 4.6 Recommended install strategy

Given §4.1's finding that 69/70 packages genuinely do coexist:

1. **Default worker image**: install `livekit-agents[all-non-minimax-extras]` (or an explicit
   `requirements.txt` pinning every plugin except `minimax`) at `livekit-agents==1.8.2`, Python **3.12**
   (satisfies the `browser` plugin's floor too, so it's available if ever needed) on a **manylinux_2_28 Linux
   x86_64 or aarch64** base image (satisfies bitHuman + Krisp's native wheels; Apple Silicon macOS also works
   for bitHuman but is not a realistic prod target). **Do not target Windows** if bitHuman or Krisp's native
   pieces are required — there is no Windows wheel path today.
2. **MiniMax as an isolated exception**: either (a) run it in a separate worker image/venv pinned to
   `livekit-agents==1.2.9` and route only MiniMax-configured tenants to that pool, or (b) exclude MiniMax
   from the registry entirely until the vendor republishes against current core, and mark it `status:
   "deferred"` with a note rather than `"mvp"` or even a normal `"available"` state.
3. **Registry flag for "installed in this worker image"**: add a field to `ProviderSpec` — e.g.
   `installed_in_image: bool = True` (or an enum if the platform ends up with more than one worker image
   flavor, e.g. `worker_image: Literal["default", "minimax-isolated", "windows-lite"]`) — so the admin UI can
   grey out / hide providers the currently-running worker pool cannot actually construct. This is strictly
   necessary once MiniMax exists in the registry at all, and becomes valuable again the moment the platform
   ever ships a second, smaller worker image (e.g. one without bitHuman/Krisp's native deps for a
   Windows-hosted dev environment).
4. **One extras group is simpler than per-deployment optional extras** given the resolver found zero
   conflicts among the other 69 — the "optional extras per deployment" approach only earns its complexity
   for MiniMax specifically; don't build a general per-provider extras matrix for a problem that in practice
   has exactly one exception.

---

## 5. Proposed registry entries (not already in `providers.py`)

Shape matches the existing `ProviderSpec`/`FieldSpec`/`ModelSpec` Pydantic models exactly (see
`lkap_contracts/providers.py`). Every entry is marked **VERIFIED** (constructor read from 1.8.2 source
directly, per §1/§2/§3) or **UNVERIFIED** (vendor-side fact only, or plugin not readable from the monorepo).
`secret_fields`/`fields` use the real kwarg names from §1's tables. This is additive to `_MVP`/`_DEFERRED` in
`providers.py` — none of the below duplicate an existing `id`.

**Schema changes needed first** (§0.2): add `"vad"`, `"turn_detection"`, `"noise_cancellation"` to
`ProviderKind`; add `"file"` to `FieldType`.

**Note on completeness of the JSON below**: `ProviderSpec.label`/`.vendor` are required (non-defaulted)
fields on the real Pydantic model, and every `FieldSpec` needs `label`; the entries below omit both
provider-level `label`/`vendor` and most `fields[]` labels for brevity given the entry count (~45 entries).
Every `fields[]` item does carry the required `type`. Before pasting into `providers.py`, run a mechanical
pass that (a) sets `vendor` from the `package` name or the master table in §1 (e.g. `"gladia-stt"` →
`vendor: "Gladia"`), and (b) derives every missing `label` from `name`/`id` (e.g. `title_case(name)` for
field labels, `f"{vendor} {kind title-cased}"` for provider labels) — pasting these objects unmodified will
fail Pydantic validation on the missing required `label` fields.

```jsonc
[
  // ---- VAD (new kind) ----
  {
    "id": "silero-vad", "kind": "vad", "status": "mvp", // VERIFIED
    "package": "livekit-plugins-silero", "python_class": "livekit.plugins.silero.VAD",
    "requires_credential": false, "secret_fields": [], "fields": [],
    "models": [{ "id": "silero", "label": "Silero (local ONNX)" }]
  },
  {
    "id": "inference-vad", "kind": "vad", "status": "mvp", // VERIFIED
    "package": "livekit-agents", "python_class": "livekit.agents.inference.VAD",
    "requires_credential": false, "secret_fields": [], "fields": [],
    "models": [{ "id": "silero", "label": "Silero (LiveKit-hosted native inference)" }]
  },

  // ---- TURN DETECTION (new kind) ----
  {
    "id": "turn-detector-plugin", "kind": "turn_detection", "status": "mvp", // VERIFIED
    "package": "livekit-plugins-turn-detector",
    "python_class": "livekit.plugins.turn_detector.multilingual.MultilingualModel",
    "requires_credential": false, "fields": []
  },
  {
    "id": "inference-turn-detector", "kind": "turn_detection", "status": "mvp", // VERIFIED
    "package": "livekit-agents", "python_class": "livekit.agents.inference.eot.TurnDetector",
    "requires_credential": false,
    "fields": [
      { "name": "version", "label": "Version", "type": "enum", "options": ["v1", "v1-mini"], "default": "v1" },
      { "name": "local_fallback", "label": "Fall back to local mini on gateway failure", "type": "boolean", "default": true }
    ]
  },

  // ---- NOISE CANCELLATION (new kind) ----
  {
    "id": "krisp-noise-cancellation", "kind": "noise_cancellation", "status": "deferred", // VERIFIED (class), UNVERIFIED (pricing/licensing)
    "package": "livekit-plugins-krisp", "python_class": "livekit.plugins.krisp.KrispVivaFilterFrameProcessor",
    "requires_credential": true,
    "secret_fields": [{ "name": "license_key", "label": "Krisp license key", "type": "secret", "help": "Or use LiveKitCloudAuthProvider with no vendor key if routing through LiveKit Cloud." }]
  },

  // ---- AVATAR: 10 not yet in registry (bey, tavus, simli, anam, bithuman, liveavatar = 6 already present as mvp/deferred; 16 - 6 = 10 below) ----
  {
    "id": "avatario-avatar", "kind": "avatar", "status": "deferred", // VERIFIED (ctor)
    "package": "livekit-plugins-avatario", "python_class": "livekit.plugins.avatario.AvatarSession",
    "secret_fields": [{ "name": "api_key", "env_fallback": "AVATARIO_API_KEY" }],
    "fields": [{ "name": "avatar_id", "type": "string", "required": true, "env_fallback": "AVATARIO_AVATAR_ID" }]
  },
  {
    "id": "avatartalk-avatar", "kind": "avatar", "status": "deferred", // VERIFIED (ctor)
    "package": "livekit-plugins-avatartalk", "python_class": "livekit.plugins.avatartalk.AvatarSession",
    "secret_fields": [{ "name": "api_secret", "env_fallback": "AVATARTALK_API_SECRET" }],
    "fields": [
      { "name": "avatar", "type": "string", "default": "japanese_man" },
      { "name": "emotion", "type": "string", "default": "expressive" }
    ]
  },
  {
    "id": "did-avatar", "kind": "avatar", "status": "deferred", // VERIFIED (ctor)
    "package": "livekit-plugins-did", "python_class": "livekit.plugins.did.AvatarSession",
    "secret_fields": [{ "name": "api_key", "env_fallback": "DID_API_KEY" }],
    "fields": [{ "name": "agent_id", "type": "string", "required": true, "help": "No default — D-ID has no stock avatar." }]
  },
  {
    "id": "keyframe-avatar", "kind": "avatar", "status": "deferred", // VERIFIED (ctor)
    "package": "livekit-plugins-keyframe", "python_class": "livekit.plugins.keyframe.AvatarSession",
    "secret_fields": [{ "name": "api_key", "env_fallback": "KEYFRAME_API_KEY" }],
    "fields": [
      { "name": "persona_id", "type": "string" },
      { "name": "persona_slug", "type": "string", "placeholder": "public:cosmo_persona-1.5-live" }
    ]
  },
  {
    "id": "lemonslice-avatar", "kind": "avatar", "status": "deferred", // VERIFIED (ctor)
    "package": "livekit-plugins-lemonslice", "python_class": "livekit.plugins.lemonslice.AvatarSession",
    "secret_fields": [{ "name": "api_key", "env_fallback": "LEMONSLICE_API_KEY" }],
    "fields": [
      { "name": "agent_id", "type": "string" },
      { "name": "agent_image_url", "type": "string" },
      { "name": "agent_image", "type": "file" }
    ]
  },
  {
    "id": "protoface-avatar", "kind": "avatar", "status": "deferred", // VERIFIED (ctor)
    "package": "livekit-plugins-protoface", "python_class": "livekit.plugins.protoface.AvatarSession",
    "secret_fields": [{ "name": "api_key", "env_fallback": "PROTOFACE_API_KEY" }],
    "fields": [{ "name": "avatar_id", "type": "string", "default": "av_stock_001" }]
  },
  {
    "id": "runway-avatar", "kind": "avatar", "status": "deferred", // VERIFIED (ctor)
    "package": "livekit-plugins-runway", "python_class": "livekit.plugins.runway.AvatarSession",
    "secret_fields": [{ "name": "api_key", "env_fallback": "RUNWAYML_API_SECRET" }],
    "fields": [
      { "name": "avatar_id", "type": "string" },
      { "name": "preset_id", "type": "string" }
    ]
  },
  {
    "id": "spatius-avatar", "kind": "avatar", "status": "deferred", // VERIFIED (ctor)
    "package": "livekit-plugins-spatius", "python_class": "livekit.plugins.spatius.AvatarSession",
    "secret_fields": [{ "name": "api_key", "env_fallback": "SPATIUS_API_KEY" }],
    "fields": [
      { "name": "app_id", "type": "string", "required": true },
      { "name": "avatar_id", "type": "string", "required": true, "env_fallback": "SPATIUS_AVATAR_ID" }
    ]
  },
  {
    "id": "synthesia-avatar", "kind": "avatar", "status": "deferred", // VERIFIED (ctor)
    "package": "livekit-plugins-synthesia", "python_class": "livekit.plugins.synthesia.AvatarSession",
    "secret_fields": [{ "name": "api_key", "env_fallback": "SYNTHESIA_API_KEY" }],
    "fields": [{ "name": "avatar_config", "type": "json", "required": true, "help": "Up to 5 gallery avatar ids; supports live swap_avatar()." }]
  },
  {
    "id": "trugen-avatar", "kind": "avatar", "status": "deferred", // VERIFIED (ctor)
    "package": "livekit-plugins-trugen", "python_class": "livekit.plugins.trugen.AvatarSession",
    "secret_fields": [{ "name": "api_key", "env_fallback": "TRUGEN_API_KEY" }],
    "fields": [{ "name": "avatar_id", "type": "string" }]
  },
  // Hedra intentionally NOT proposed as a registry entry — plugin permanently disabled by vendor (§0.3).

  // ---- REALTIME: 4 not yet in registry (azure-openai-realtime and xai-realtime are already in _DEFERRED) ----
  {
    "id": "aws-nova-sonic-realtime", "kind": "realtime", "status": "deferred", // VERIFIED (ctor)
    "package": "livekit-plugins-aws", "python_class": "livekit.plugins.aws.experimental.realtime.RealtimeModel",
    "secret_fields": [
      { "name": "api_key", "env_fallback": "AWS_ACCESS_KEY_ID" },
      { "name": "api_secret", "env_fallback": "AWS_SECRET_ACCESS_KEY" }
    ],
    "fields": [
      { "name": "region", "type": "string" },
      { "name": "modalities", "type": "enum", "options": ["audio", "mixed"], "default": "mixed" },
      { "name": "turn_detection", "type": "enum", "options": ["HIGH", "MEDIUM", "LOW"], "default": "MEDIUM" },
      { "name": "generate_reply_timeout", "type": "number", "default": 10.0 }
    ],
    "models": [
      { "id": "amazon.nova-2-sonic-v1:0", "label": "Nova 2 Sonic", "supports_video": false },
      { "id": "amazon.nova-sonic-v1:0", "label": "Nova Sonic", "supports_video": false }
    ],
    "default_model": "amazon.nova-2-sonic-v1:0"
  },
  {
    "id": "phonic-realtime", "kind": "realtime", "status": "deferred", // VERIFIED (ctor — see §3.8 UX caveat)
    "package": "livekit-plugins-phonic", "python_class": "livekit.plugins.phonic.realtime.RealtimeModel",
    "secret_fields": [{ "name": "api_key", "env_fallback": "PHONIC_API_KEY" }],
    "fields": [
      { "name": "phonic_agent", "type": "string", "help": "Most behavior (voice, tools, prompts) is configured on Phonic's own dashboard against this agent id." },
      { "name": "welcome_message", "type": "string" }
    ]
  },
  {
    "id": "ultravox-realtime", "kind": "realtime", "status": "deferred", // VERIFIED (ctor)
    "package": "livekit-plugins-ultravox", "python_class": "livekit.plugins.ultravox.realtime.RealtimeModel",
    "secret_fields": [{ "name": "api_key", "env_fallback": "ULTRAVOX_API_KEY" }],
    "fields": [
      { "name": "voice", "type": "enum", "options": ["Mark", "Jessica"], "default": "Mark" },
      { "name": "output_medium", "type": "enum", "options": ["text", "voice"], "default": "voice" }
    ],
    "models": [
      { "id": "fixie-ai/ultravox", "label": "Ultravox (default)" },
      { "id": "fixie-ai/ultravox-llama3.3-70b", "label": "Ultravox Llama 3.3 70B" }
    ],
    "default_model": "fixie-ai/ultravox"
  },
  {
    "id": "nvidia-personaplex-realtime", "kind": "realtime", "status": "deferred", // UNVERIFIED auth path, see §3.9
    "package": "livekit-plugins-nvidia", "python_class": "livekit.plugins.nvidia.experimental.realtime.RealtimeModel",
    "secret_fields": [{ "name": "api_key", "help": "UNVERIFIED exact param — constructor takes no explicit api_key kwarg; likely resolved via http_session/env. Re-check before shipping." }],
    "fields": [{ "name": "voice", "type": "string", "default": "NATF2" }]
  },

  // ---- LLM: not yet in registry ----
  {
    "id": "perplexity-llm", "kind": "llm", "status": "deferred", // VERIFIED (ctor)
    "package": "livekit-plugins-perplexity", "python_class": "livekit.plugins.perplexity.LLM",
    "secret_fields": [{ "name": "api_key" }],
    "fields": [{ "name": "model", "type": "string", "default": "sonar-pro" }]
  },
  {
    "id": "mistral-llm", "kind": "llm", "status": "deferred", // VERIFIED (ctor)
    "package": "livekit-plugins-mistralai", "python_class": "livekit.plugins.mistralai.LLM",
    "secret_fields": [{ "name": "api_key", "env_fallback": "MISTRAL_API_KEY" }],
    "fields": [{ "name": "model", "type": "string", "default": "ministral-8b-latest" }]
  },
  {
    "id": "baseten-llm", "kind": "llm", "status": "deferred", // VERIFIED (ctor)
    "package": "livekit-plugins-baseten", "python_class": "livekit.plugins.baseten.LLM",
    "secret_fields": [{ "name": "api_key", "env_fallback": "BASETEN_API_KEY" }],
    "fields": [
      { "name": "model", "type": "string", "default": "meta-llama/Llama-4-Maverick-17B-128E-Instruct" },
      { "name": "base_url", "type": "string", "default": "https://inference.baseten.co/v1" }
    ]
  },

  // ---- MiniMax deliberately marked non-installable, not deferred-normal ----
  {
    "id": "minimax-tts", "kind": "tts", "status": "deferred", // VERIFIED ctor, VERIFIED (empirically) incompatible
    "package": "livekit-plugins-minimax", "python_class": "livekit.plugins.minimax.TTS",
    "secret_fields": [{ "name": "api_key", "env_fallback": "MINIMAX_API_KEY" }],
    "fields": [{ "name": "model", "type": "string", "default": "speech-02-turbo" }],
    "note": "INCOMPATIBLE with livekit-agents>=1.8.2 (pins ==1.2.9, see §4.2). Do not enable until the vendor republishes."
  },

  // ---- PlayAI: out-of-tree, stale, unverified against current core ----
  {
    "id": "playai-tts", "kind": "tts", "status": "deferred", // VERIFIED ctor (from wheel), UNVERIFIED vs 1.8.2 core
    "package": "livekit-plugins-playai", "python_class": "livekit.plugins.playai.TTS",
    "secret_fields": [
      { "name": "api_key" },
      { "name": "user_id", "help": "PlayAI requires both an API key and a user id." }
    ],
    "fields": [{ "name": "voice", "type": "string", "help": "A PlayAI voice-clone manifest URL, not a bare id." }],
    "note": "Package has not been re-released since 2025-10-15 (v1.2.15) and is absent from the livekit/agents monorepo tree; compatibility with core 1.8.2 is UNVERIFIED, not disproven."
  },

  // ---- STT: 21 not yet in registry (assemblyai/google/openai/speechmatics/elevenlabs/cartesia/groq/azure are already in _DEFERRED) ----
  { "id": "gladia-stt", "kind": "stt", "status": "deferred", "package": "livekit-plugins-gladia", "python_class": "livekit.plugins.gladia.STT", // VERIFIED (ctor)
    "secret_fields": [{ "name": "api_key", "env_fallback": "GLADIA_API_KEY" }],
    "fields": [{ "name": "model", "type": "string", "default": "solaria-1" }, { "name": "region", "type": "enum", "options": ["us-west", "eu-west"], "default": "eu-west" }] },
  { "id": "soniox-stt", "kind": "stt", "status": "deferred", "package": "livekit-plugins-soniox", "python_class": "livekit.plugins.soniox.STT", // VERIFIED (ctor)
    "secret_fields": [{ "name": "api_key" }], "fields": [{ "name": "base_url", "type": "string", "default": "wss://stt-rt.soniox.com/transcribe-websocket" }] },
  { "id": "fal-wizper-stt", "kind": "stt", "status": "deferred", "package": "livekit-plugins-fal", "python_class": "livekit.plugins.fal.WizperSTT", // VERIFIED (ctor) — note class name is WizperSTT, not STT
    "secret_fields": [{ "name": "api_key", "help": "FAL_KEY per fal-client convention" }], "fields": [] },
  { "id": "fireworksai-stt", "kind": "stt", "status": "deferred", "package": "livekit-plugins-fireworksai", "python_class": "livekit.plugins.fireworksai.STT", // VERIFIED (ctor)
    "secret_fields": [{ "name": "api_key", "env_fallback": "FIREWORKS_API_KEY" }],
    "fields": [{ "name": "base_url", "type": "string", "default": "wss://audio-streaming.us-virginia-1.direct.fireworks.ai/v1" }, { "name": "language", "type": "string" }] },
  { "id": "baseten-stt", "kind": "stt", "status": "deferred", "package": "livekit-plugins-baseten", "python_class": "livekit.plugins.baseten.STT", // VERIFIED (ctor)
    "secret_fields": [{ "name": "api_key", "env_fallback": "BASETEN_API_KEY" }],
    "fields": [{ "name": "model", "type": "string", "default": "whisper" }, { "name": "model_endpoint", "type": "string" }] },
  { "id": "mistral-stt", "kind": "stt", "status": "deferred", "package": "livekit-plugins-mistralai", "python_class": "livekit.plugins.mistralai.STT", // VERIFIED (ctor)
    "secret_fields": [{ "name": "api_key", "env_fallback": "MISTRAL_API_KEY" }], "fields": [{ "name": "model", "type": "string", "default": "voxtral-mini-latest" }] },
  { "id": "nvidia-stt", "kind": "stt", "status": "deferred", "package": "livekit-plugins-nvidia", "python_class": "livekit.plugins.nvidia.STT", // VERIFIED (ctor)
    "secret_fields": [{ "name": "api_key" }],
    "fields": [{ "name": "model", "type": "string", "default": "parakeet-1.1b-en-US-asr-streaming-silero-vad-sortformer" }, { "name": "server", "type": "string", "default": "grpc.nvcf.nvidia.com:443" }] },
  { "id": "sarvam-stt", "kind": "stt", "status": "deferred", "package": "livekit-plugins-sarvam", "python_class": "livekit.plugins.sarvam.STT", // VERIFIED (ctor)
    "secret_fields": [{ "name": "api_key", "env_fallback": "SARVAM_API_KEY" }],
    "fields": [{ "name": "model", "type": "string", "default": "saaras:v4" }, { "name": "language", "type": "string", "default": "en-IN" }] },
  { "id": "meta-stt", "kind": "stt", "status": "deferred", "package": "livekit-plugins-meta", "python_class": "livekit.plugins.meta.STT", // VERIFIED (ctor)
    "secret_fields": [{ "name": "api_key" }], "fields": [{ "name": "model", "type": "string", "default": "muse-voice-transcribe-1.0" }] },
  { "id": "gnani-stt", "kind": "stt", "status": "deferred", "package": "livekit-plugins-gnani", "python_class": "livekit.plugins.gnani.STT", // VERIFIED (ctor)
    "secret_fields": [{ "name": "api_key", "env_fallback": "GNANI_API_KEY" }], "fields": [{ "name": "language", "type": "string", "default": "en-IN" }] },
  { "id": "gradium-stt", "kind": "stt", "status": "deferred", "package": "livekit-plugins-gradium", "python_class": "livekit.plugins.gradium.STT", // VERIFIED (ctor)
    "secret_fields": [{ "name": "api_key", "env_fallback": "GRADIUM_API_KEY" }], "fields": [{ "name": "model_endpoint", "type": "string", "env_fallback": "GRADIUM_MODEL_ENDPOINT" }] },
  { "id": "clova-stt", "kind": "stt", "status": "deferred", "package": "livekit-plugins-clova", "python_class": "livekit.plugins.clova.STT", // VERIFIED (ctor)
    "secret_fields": [{ "name": "secret", "env_fallback": "CLOVA_STT_SECRET_KEY" }],
    "fields": [{ "name": "invoke_url", "type": "string", "env_fallback": "CLOVA_STT_INVOKE_URL" }, { "name": "language", "type": "string", "default": "en-US" }] },
  { "id": "rtzr-stt", "kind": "stt", "status": "deferred", "package": "livekit-plugins-rtzr", "python_class": "livekit.plugins.rtzr.STT", // VERIFIED (ctor), UNVERIFIED auth field name
    "secret_fields": [{ "name": "api_key", "help": "UNVERIFIED — no explicit api_key kwarg on STT.__init__; auth resolved via rtzrapi.py helper." }],
    "fields": [{ "name": "model", "type": "string", "default": "sommers_ko" }, { "name": "language", "type": "string", "default": "ko" }] },
  { "id": "slng-stt", "kind": "stt", "status": "deferred", "package": "livekit-plugins-slng", "python_class": "livekit.plugins.slng.STT", // VERIFIED (ctor)
    "secret_fields": [{ "name": "api_key", "env_fallback": "SLNG_API_KEY" }], "fields": [{ "name": "slng_base_url", "type": "string", "default": "api.slng.ai" }] },
  { "id": "smallestai-stt", "kind": "stt", "status": "deferred", "package": "livekit-plugins-smallestai", "python_class": "livekit.plugins.smallestai.STT", // VERIFIED (ctor)
    "secret_fields": [{ "name": "api_key", "env_fallback": "SMALLEST_API_KEY" }], "fields": [{ "name": "model", "type": "string", "default": "pulse" }] },
  { "id": "simplismart-stt", "kind": "stt", "status": "deferred", "package": "livekit-plugins-simplismart", "python_class": "livekit.plugins.simplismart.STT", // VERIFIED (ctor)
    "secret_fields": [{ "name": "api_key", "env_fallback": "SIMPLISMART_API_KEY" }], "fields": [{ "name": "model", "type": "string", "default": "openai/whisper-large-v3-turbo" }] },
  { "id": "telnyx-stt", "kind": "stt", "status": "deferred", "package": "livekit-plugins-telnyx", "python_class": "livekit.plugins.telnyx.STT", // VERIFIED (ctor)
    "secret_fields": [{ "name": "api_key" }], "fields": [{ "name": "language", "type": "string", "default": "en" }] },
  { "id": "spitch-stt", "kind": "stt", "status": "deferred", "package": "livekit-plugins-spitch", "python_class": "livekit.plugins.spitch.STT", // VERIFIED (ctor), UNVERIFIED auth field name
    "secret_fields": [{ "name": "api_key", "help": "UNVERIFIED — no explicit credential kwarg; uses AsyncSpitch() client's own env resolution." }],
    "fields": [{ "name": "language", "type": "string", "default": "en" }] },
  { "id": "palabra-stt", "kind": "stt", "status": "deferred", "package": "livekit-plugins-palabra", "python_class": "livekit.plugins.palabra.STT", // VERIFIED (ctor)
    "secret_fields": [{ "name": "api_key", "env_fallback": "PALABRA_API_KEY" }], "fields": [{ "name": "translate_languages", "type": "string" }] },
  { "id": "aws-transcribe-stt", "kind": "stt", "status": "deferred", "package": "livekit-plugins-aws", "python_class": "livekit.plugins.aws.STT", // VERIFIED (ctor) — note: nested Credentials object, not flat api_key/api_secret unlike aws.LLM/aws.TTS
    "secret_fields": [{ "name": "credentials", "type": "json", "help": "boto-style Credentials object; falls back to standard AWS credential chain if omitted." }],
    "fields": [{ "name": "region", "type": "string", "env_fallback": "AWS_REGION" }, { "name": "language", "type": "string", "default": "en-US" }] },
  { "id": "xai-stt", "kind": "stt", "status": "deferred", "package": "livekit-plugins-xai", "python_class": "livekit.plugins.xai.STT", // VERIFIED (ctor)
    "secret_fields": [{ "name": "api_key", "env_fallback": "XAI_API_KEY" }], "fields": [{ "name": "language", "type": "string", "default": "en" }] },

  // ---- TTS: 28 not yet in registry (cartesia/elevenlabs/openai=mvp; google/deepgram/rime/inworld/hume/azure already in _DEFERRED) ----
  { "id": "asyncai-tts", "kind": "tts", "status": "deferred", "package": "livekit-plugins-asyncai", "python_class": "livekit.plugins.asyncai.TTS", // VERIFIED (ctor)
    "secret_fields": [{ "name": "api_key", "env_fallback": "ASYNCAI_API_KEY" }], "fields": [{ "name": "model", "type": "string", "default": "async_flash_v1.0" }] },
  { "id": "aws-polly-tts", "kind": "tts", "status": "deferred", "package": "livekit-plugins-aws", "python_class": "livekit.plugins.aws.TTS", // VERIFIED (ctor)
    "secret_fields": [{ "name": "api_key", "env_fallback": "AWS_ACCESS_KEY_ID" }, { "name": "api_secret", "env_fallback": "AWS_SECRET_ACCESS_KEY" }],
    "fields": [{ "name": "voice", "type": "string", "default": "Ruth" }, { "name": "speech_engine", "type": "enum", "options": ["generative", "standard", "neural"], "default": "generative" }] },
  { "id": "baseten-tts", "kind": "tts", "status": "deferred", "package": "livekit-plugins-baseten", "python_class": "livekit.plugins.baseten.TTS", // VERIFIED (ctor)
    "secret_fields": [{ "name": "api_key", "env_fallback": "BASETEN_API_KEY" }], "fields": [{ "name": "model", "type": "string", "default": "orpheus" }] },
  { "id": "bland-tts", "kind": "tts", "status": "deferred", "package": "livekit-plugins-bland", "python_class": "livekit.plugins.bland.TTS", // VERIFIED (ctor)
    "secret_fields": [{ "name": "api_key", "env_fallback": "BLAND_API_KEY" }], "fields": [{ "name": "voice_id", "type": "string" }] },
  { "id": "cambai-tts", "kind": "tts", "status": "deferred", "package": "livekit-plugins-cambai", "python_class": "livekit.plugins.cambai.TTS", // VERIFIED (ctor)
    "secret_fields": [{ "name": "api_key", "env_fallback": "CAMB_API_KEY" }], "fields": [{ "name": "voice_id", "type": "number" }, { "name": "language", "type": "string" }] },
  { "id": "fishaudio-tts", "kind": "tts", "status": "deferred", "package": "livekit-plugins-fishaudio", "python_class": "livekit.plugins.fishaudio.TTS", // VERIFIED (ctor)
    "secret_fields": [{ "name": "api_key" }], "fields": [{ "name": "model", "type": "string", "default": "s2.1-pro" }, { "name": "voice_id", "type": "string" }] },
  { "id": "gnani-tts", "kind": "tts", "status": "deferred", "package": "livekit-plugins-gnani", "python_class": "livekit.plugins.gnani.TTS", // VERIFIED (ctor)
    "secret_fields": [{ "name": "api_key", "env_fallback": "GNANI_API_KEY" }], "fields": [{ "name": "voice", "type": "string", "default": "Pranav" }] },
  { "id": "gradium-tts", "kind": "tts", "status": "deferred", "package": "livekit-plugins-gradium", "python_class": "livekit.plugins.gradium.TTS", // VERIFIED (ctor)
    "secret_fields": [{ "name": "api_key", "env_fallback": "GRADIUM_API_KEY" }], "fields": [{ "name": "voice_id", "type": "string", "default": "4SZHfMpw-p46Ywgs" }] },
  { "id": "groq-tts", "kind": "tts", "status": "deferred", "package": "livekit-plugins-groq", "python_class": "livekit.plugins.groq.TTS", // VERIFIED (ctor)
    "secret_fields": [{ "name": "api_key", "env_fallback": "GROQ_API_KEY" }], "fields": [{ "name": "model", "type": "string", "default": "canopylabs/orpheus-v1-english" }, { "name": "voice", "type": "string", "default": "autumn" }] },
  { "id": "lmnt-tts", "kind": "tts", "status": "deferred", "package": "livekit-plugins-lmnt", "python_class": "livekit.plugins.lmnt.TTS", // VERIFIED (ctor)
    "secret_fields": [{ "name": "api_key", "env_fallback": "LMNT_API_KEY" }], "fields": [{ "name": "model", "type": "string", "default": "blizzard" }, { "name": "voice", "type": "string", "default": "leah" }] },
  { "id": "mistral-tts", "kind": "tts", "status": "deferred", "package": "livekit-plugins-mistralai", "python_class": "livekit.plugins.mistralai.TTS", // VERIFIED (ctor)
    "secret_fields": [{ "name": "api_key", "env_fallback": "MISTRAL_API_KEY" }], "fields": [{ "name": "model", "type": "string", "default": "voxtral-mini-tts-latest" }, { "name": "voice", "type": "string", "default": "en_paul_neutral" }] },
  { "id": "murf-tts", "kind": "tts", "status": "deferred", "package": "livekit-plugins-murf", "python_class": "livekit.plugins.murf.TTS", // VERIFIED (ctor)
    "secret_fields": [{ "name": "api_key", "env_fallback": "MURF_API_KEY" }], "fields": [{ "name": "model", "type": "string", "default": "FALCON" }] },
  { "id": "neuphonic-tts", "kind": "tts", "status": "deferred", "package": "livekit-plugins-neuphonic", "python_class": "livekit.plugins.neuphonic.TTS", // VERIFIED (ctor)
    "secret_fields": [{ "name": "api_key", "env_fallback": "NEUPHONIC_API_KEY" }], "fields": [{ "name": "voice_id", "type": "string", "default": "8e9c4bc8-3979-48ab-8626-df53befc2090" }] },
  { "id": "nvidia-tts", "kind": "tts", "status": "deferred", "package": "livekit-plugins-nvidia", "python_class": "livekit.plugins.nvidia.TTS", // VERIFIED (ctor)
    "secret_fields": [{ "name": "api_key" }], "fields": [{ "name": "voice", "type": "string", "default": "Magpie-Multilingual.EN-US.Leo" }] },
  { "id": "palabra-tts", "kind": "tts", "status": "deferred", "package": "livekit-plugins-palabra", "python_class": "livekit.plugins.palabra.TTS", // VERIFIED (ctor)
    "secret_fields": [{ "name": "api_key" }], "fields": [{ "name": "voice_id", "type": "string" }, { "name": "language", "type": "string" }] },
  { "id": "resemble-tts", "kind": "tts", "status": "deferred", "package": "livekit-plugins-resemble", "python_class": "livekit.plugins.resemble.TTS", // VERIFIED (ctor) — note: illustrated in v1's JSON sketch but never actually added to providers.py
    "secret_fields": [{ "name": "api_key", "env_fallback": "RESEMBLE_API_KEY" }], "fields": [{ "name": "voice_uuid", "type": "string" }] },
  { "id": "respeecher-tts", "kind": "tts", "status": "deferred", "package": "livekit-plugins-respeecher", "python_class": "livekit.plugins.respeecher.TTS", // VERIFIED (ctor)
    "secret_fields": [{ "name": "api_key", "env_fallback": "RESPEECHER_API_KEY" }], "fields": [{ "name": "model", "type": "string", "default": "/public/tts/en-rt" }] },
  { "id": "sarvam-tts", "kind": "tts", "status": "deferred", "package": "livekit-plugins-sarvam", "python_class": "livekit.plugins.sarvam.TTS", // VERIFIED (ctor)
    "secret_fields": [{ "name": "api_key", "env_fallback": "SARVAM_API_KEY" }], "fields": [{ "name": "model", "type": "string", "default": "bulbul:v3" }, { "name": "target_language_code", "type": "string", "default": "en-IN" }] },
  { "id": "simplismart-tts", "kind": "tts", "status": "deferred", "package": "livekit-plugins-simplismart", "python_class": "livekit.plugins.simplismart.TTS", // VERIFIED (ctor)
    "secret_fields": [{ "name": "api_key" }], "fields": [{ "name": "model", "type": "string", "default": "canopylabs/orpheus-3b-0.1-ft" }] },
  { "id": "slng-tts", "kind": "tts", "status": "deferred", "package": "livekit-plugins-slng", "python_class": "livekit.plugins.slng.TTS", // VERIFIED (ctor)
    "secret_fields": [{ "name": "api_key", "env_fallback": "SLNG_API_KEY" }], "fields": [{ "name": "voice", "type": "string", "required": true }] },
  { "id": "smallestai-tts", "kind": "tts", "status": "deferred", "package": "livekit-plugins-smallestai", "python_class": "livekit.plugins.smallestai.TTS", // VERIFIED (ctor)
    "secret_fields": [{ "name": "api_key", "env_fallback": "SMALLEST_API_KEY" }], "fields": [{ "name": "model", "type": "string", "default": "lightning_v3.1_pro" }] },
  { "id": "soniox-tts", "kind": "tts", "status": "deferred", "package": "livekit-plugins-soniox", "python_class": "livekit.plugins.soniox.TTS", // VERIFIED (ctor)
    "secret_fields": [{ "name": "api_key", "env_fallback": "SONIOX_API_KEY" }], "fields": [{ "name": "model", "type": "string", "default": "tts-rt-v1-preview" }, { "name": "voice", "type": "string", "default": "Maya" }] },
  { "id": "speechify-tts", "kind": "tts", "status": "deferred", "package": "livekit-plugins-speechify", "python_class": "livekit.plugins.speechify.TTS", // VERIFIED (ctor)
    "secret_fields": [{ "name": "api_key", "env_fallback": "SPEECHIFY_API_KEY" }], "fields": [{ "name": "voice_id", "type": "string", "default": "dominic_32" }, { "name": "model", "type": "string", "default": "simba-3.2" }] },
  { "id": "speechmatics-tts", "kind": "tts", "status": "deferred", "package": "livekit-plugins-speechmatics", "python_class": "livekit.plugins.speechmatics.TTS", // VERIFIED (ctor)
    "secret_fields": [{ "name": "api_key", "env_fallback": "SPEECHMATICS_API_KEY" }], "fields": [{ "name": "voice", "type": "string", "default": "sarah" }] },
  { "id": "spitch-tts", "kind": "tts", "status": "deferred", "package": "livekit-plugins-spitch", "python_class": "livekit.plugins.spitch.TTS", // VERIFIED (ctor), UNVERIFIED auth field name
    "secret_fields": [{ "name": "api_key", "help": "UNVERIFIED — no explicit credential kwarg; uses AsyncSpitch() client." }], "fields": [{ "name": "language", "type": "string", "default": "en" }, { "name": "voice", "type": "string", "default": "lina" }] },
  { "id": "telnyx-tts", "kind": "tts", "status": "deferred", "package": "livekit-plugins-telnyx", "python_class": "livekit.plugins.telnyx.TTS", // VERIFIED (ctor)
    "secret_fields": [{ "name": "api_key" }], "fields": [{ "name": "voice", "type": "string", "default": "Telnyx.NaturalHD.astra" }] },
  { "id": "upliftai-tts", "kind": "tts", "status": "deferred", "package": "livekit-plugins-upliftai", "python_class": "livekit.plugins.upliftai.TTS", // VERIFIED (ctor)
    "secret_fields": [{ "name": "api_key", "env_fallback": "UPLIFTAI_API_KEY" }], "fields": [{ "name": "voice_id", "type": "string", "default": "v_meklc281" }] },
  { "id": "vakyam-tts", "kind": "tts", "status": "deferred", "package": "livekit-plugins-vakyam", "python_class": "livekit.plugins.vakyam.TTS", // VERIFIED (ctor)
    "secret_fields": [{ "name": "api_key", "env_fallback": "VAKYAM_API_KEY" }], "fields": [{ "name": "voice", "type": "string" }, { "name": "language", "type": "string" }] },
  { "id": "xai-tts", "kind": "tts", "status": "deferred", "package": "livekit-plugins-xai", "python_class": "livekit.plugins.xai.TTS", // VERIFIED (ctor)
    "secret_fields": [{ "name": "api_key", "env_fallback": "XAI_API_KEY" }], "fields": [{ "name": "voice", "type": "string", "default": "ara" }, { "name": "language", "type": "string", "default": "auto" }] },

  // ---- LLM / realtime: remaining distinct class paths not covered above ----
  { "id": "xai-llm", "kind": "llm", "status": "deferred", "package": "livekit-plugins-xai", "python_class": "livekit.plugins.xai.responses.llm.LLM", // VERIFIED (ctor)
    "secret_fields": [{ "name": "api_key", "env_fallback": "XAI_API_KEY" }], "fields": [{ "name": "model", "type": "string", "default": "grok-4-1-fast-non-reasoning" }] },
  { "id": "openai-responses-llm", "kind": "llm", "status": "deferred", "package": "livekit-plugins-openai", "python_class": "livekit.plugins.openai.responses.llm.LLM", // VERIFIED (ctor) — distinct class from openai.LLM (chat), uses the Responses API over a websocket by default
    "secret_fields": [{ "name": "api_key", "env_fallback": "OPENAI_API_KEY" }],
    "fields": [{ "name": "model", "type": "string", "default": "gpt-4.1" }, { "name": "use_websocket", "type": "boolean", "default": true }] },
  { "id": "openai-gptlive-realtime", "kind": "realtime", "status": "deferred", "package": "livekit-plugins-openai", "python_class": "livekit.plugins.openai.realtime.GPTLiveModel", // VERIFIED (ctor) — distinct from openai.realtime.RealtimeModel; delegates generation to the Responses API
    "secret_fields": [{ "name": "api_key" }],
    "fields": [{ "name": "voice", "type": "string" }, { "name": "delegation", "type": "enum", "options": ["responses"], "default": "responses" }] },

  // ---- Noise cancellation: two out-of-tree packages, class paths UNVERIFIED (not in the monorepo to read) ----
  { "id": "legacy-noise-cancellation", "kind": "noise_cancellation", "status": "deferred", "package": "livekit-plugins-noise-cancellation", "python_class": "UNVERIFIED", // UNVERIFIED — out-of-tree, only requires_dist (livekit>=0.21.3) confirmed from PyPI metadata, class path not read
    "requires_credential": false, "fields": [], "note": "Independent 0.x versioning; likely the older client/room-level Krisp BVC wrapper distinct from in-tree livekit-plugins-krisp. Confirm class path before registering." },
  { "id": "ai-coustics-noise-cancellation", "kind": "noise_cancellation", "status": "deferred", "package": "livekit-plugins-ai-coustics", "python_class": "UNVERIFIED", // UNVERIFIED — out-of-tree, only requires_dist (livekit-agents>=1.4.2) confirmed from PyPI metadata
    "secret_fields": [{ "name": "api_key", "help": "UNVERIFIED — vendor credential shape not confirmed." }], "fields": [] }
  // Hedra: no entry proposed. Plugin exists on PyPI (1.7.1) but its AvatarSession.__init__ unconditionally
  // raises HedraException("...has been disabled..."). Registering it would only produce a broken selection.
]
```

---

## Status note

Complete. All five requested sections are written and source-verified per the methodology in §0:
SDK-side facts (constructors, versions, dependency graph, core mechanics) were read directly from the
1.8.2 source tree and PyPI metadata; vendor-side facts (avatar list-APIs, pricing, latency, self-host)
came from four parallel web-research passes against each vendor's own docs/pricing pages, completed and
merged into §2.2. Every cell not backed by a primary source is explicitly marked **UNVERIFIED** rather than
guessed — treat those as open follow-ups, not as established fact.
