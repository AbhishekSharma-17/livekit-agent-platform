# V4-08 live check: custom model ids, live catalogs, "Test model", catalog drift

Status: **done where a key exists (V4-10, 2026-09-25, 05:07–05:11 IST).** The protocol is `CUSTOM-MODELS.md` §4. Every step that needed a vendor key the workspace does not hold is marked **not run** and routed to an ask. Every failure is an ask, not a patch (#64–#69 in `_asks.md`).

The repo is public. This file holds no token, key, host or fingerprint; ids of agents and avatars are fine.

## Setup

- **Target.** The user's dev api on `:8080` (`/v1/health`: `ok`, `db: ok`), not a scratch api. The card asked for the real stack because the stored keys live there. The run changed no agent config and restarted no server. It left behind the `provider_models` rows that "Test model" writes by design, one per tested id (see "Side effects").
- **Stored keys** (vault listing: ids and fingerprints only). OpenRouter (`openrouter-llm` home), Beyond Presence and Simli. There is no OpenAI, Google, Anthropic, Deepgram, ElevenLabs, Cartesia, Hume, xAI or Tavus key.
- **Access.** One Builder key: `agents:read, sessions:read, connections:read, providers:read, audit:read, agents:write, sessions:write`. It was named `V4-10 live check (temporary)`, minted with the admin token, and given a 1-day expiry. It was held only in a 0600 scratchpad file and never printed, and was **revoked at 05:11:08 IST** (`DELETE /v1/api-keys/{id}` → 204; the listing shows `revoked_at` set). The file was then deleted.
- **Calls.** One call at a time, about 7 s apart, under the 10-per-minute cap. None was answered from the cache (`cached=false` on all).

## Results: `POST /v1/providers/{id}/test-model`

Latency is the probe's own latency (`latency_ms`, the `basic` call). "Wall" is the whole request as the client saw it, which includes the `tools` call for LLMs. Cost is the route's `cost_estimate_usd`; the vendor's real charge is noted where the two differ.

| Provider | Model / id | ok | Latency | Wall | Probes | Detected | Cost |
|---|---|---|---|---|---|---|---|
| `openrouter-llm` | `openai/gpt-4.1-mini` | ✅ true | 1258 ms | 2460 ms | basic ✓ (`ok`), tools ✓ 1115 ms | `tools=true` | $0.0000308 (catalog pricing) |
| `openrouter-llm` | `openai/this-model-does-not-exist-v410` | ❌ false (expected) | 325 ms | 490 ms | basic ✗ `HTTP 400: … is not a valid model ID` (no key fragment) | — | none |
| `openrouter-llm` | a key-shaped `sk-or-v1-000…` value | 422 (expected) | — | 59 ms | `the model id looks like an API key, not a model id`, and the value is not echoed | — | none |
| `openrouter-tts` | `google/gemini-3.8-flash-tts`, voice `Kore` (the entry's default) | ❌ false | 1203 ms | 1487 ms | basic ✗ `HTTP 400: Gemini TTS only supports response_format="pcm". Got "mp3".` | — | none (rejected) · **ask #64** |
| `openrouter-tts` | `deepgram/aura-2`, voice `aura-2-thalia-en` | ✅ true | 1186 ms (TTFB) | 1491 ms | basic ✓ 4608 bytes `audio/mpeg` | `audio_out=true` | reported $0.00000; about $0.00018 expected (6 characters × $0.00003) · **ask #66** |
| `openrouter-stt` | `openai/gpt-4o-mini-transcribe` (the bundled 1 s clip) | ✅ true | 1724 ms | 1841 ms | basic ✓ transcript `OK.` | `audio_in=true` | none on file (1 s of audio, about $0.00005) |
| `livekit-inference-llm` (the default connection's JWT) | `google/gemma-4-31b-it` (the demo agents' LLM) | ✅ true | 1019 ms | 2198 ms | basic ✓ (`ok` plus a raw `<turn\|>` marker), tools **inconclusive** (no call within 4 tokens) | `tools=null` | $0.0000380 (platform price table) |
| `livekit-inference-llm` | `openai/gpt-4o-mini` | ✅ true | 1164 ms | 3942 ms | basic ✓ (`Ok.`), tools ✗ `HTTP 400: … max_tokens or model output limit was reached` | **`tools=false` (wrong)** · **ask #65** | $0.0000033 |
| `bey-avatar` | `b9be11b8-89fb-4227-8f86-4a881393cbdb` (the `Demo — Vision assistant` avatar) | ✅ true | 1225 ms | 1293 ms | `GET /v1/avatars/{id}` ✓ "the vendor knows this id" | — | none (session-less GET) |
| `bey-avatar` | `00000000-0000-4000-8000-000000000000` (a nonsense id) | ❌ false (expected) | 951 ms | 995 ms | `HTTP 404: Avatar not found.` | — | none |
| `simli-avatar` | `cace3ef7-a4c4-425d-a8cf-a5358eb0c427` (the `Demo — Survey intake form` face, which played live in the recheck) | ❌ false (**false negative**) | 1176 ms | 1230 ms | the face list answered 2xx, but "not in this key's face list (0 faces listed)" | — | none (a list GET; no session and no minutes) · **ask #67** |

**Vendor spend:** about $0.0003 in all, well under the $0.10 budget. The two rejected calls (the Gemini TTS 400 and the invalid id) are not billed by OpenRouter. The Simli probe is a `GET /faces`: no session, no minutes. The Bey probe is a GET by id.

## Re-run after the fixes (asks #64–#67, 2026-09-25, 05:37 IST)

The four failing readings were fixed in the api (asks #64–#67, plus #76 and #77 from the user) and re-tested on the same dev api on `:8080`. That api runs `uvicorn --reload`, so it picked up the edits without a restart; the new messages in the answers show the new code ran. One Builder key was used, with the same scopes and handling as above: named `V4-10 re-run after fixes (temporary)`, minted with the admin token with a 1-day expiry, held only in a 0600 scratchpad file and never printed. It was **revoked at 05:37:52 IST** (`DELETE /v1/api-keys/{id}` → 204; the listing shows `revoked_at` set), and the file was then deleted. Every call used `force=true` and ran about 7 s apart; none was cached.

| Provider | Model / id | ok | Latency | Wall | Probes | Detected | Cost |
|---|---|---|---|---|---|---|---|
| `openrouter-tts` | `google/gemini-3.8-flash-tts`, voice `Kore` | ✅ true (was ❌) | 2239 ms (TTFB of the pcm retry) | 3152 ms | basic ✓ 59520 bytes `audio/pcm`, "retried with pcm after the vendor refused mp3" | `audio_out=true` | $0.0000030 for the input side (6 characters × `prompt`); output audio is not counted, and the note says so. OpenRouter bills the output audio per audio token, which a speech answer does not report (1.24 s of 24 kHz PCM; well under a cent). For a token-priced model the input side is approximate: 6 characters are priced as 6 prompt units |
| `livekit-inference-llm` | `openai/gpt-4o-mini` | ✅ true | 1893 ms | 4534 ms | basic ✓ (`Ok.`), tools **inconclusive**: `HTTP 400: … max_tokens or model output limit was reached … (inconclusive)` | `tools=null` (was `false`); the stored row is corrected | $0.0000033 (platform price table) |
| `openrouter-tts` | `deepgram/aura-2`, voice `aura-2-thalia-en` | ✅ true | 1322 ms (TTFB) | 1366 ms | basic ✓ 3744 bytes `audio/mpeg` | `audio_out=true` | **$0.00018** (was $0.00000): 6 characters × $0.00003 |
| `simli-avatar` | `cace3ef7-a4c4-425d-a8cf-a5358eb0c427` (the Survey agent's face, Simli's preset "Tina") | ➖ null (was ❌ false) | 1255 ms | 1314 ms | basic: "the key works, but the id is not among this account's own faces (0 listed); Simli's preset faces are not listed and can't be verified without starting a session" | — | none (a list GET; no session, no minutes) |

- **Vendor spend:** under $0.01 in all. The priced parts come to about $0.0002; the Gemini TTS output audio is billed on top of that per audio token, a count the answer does not report. The rejected mp3 request is not billed. That is under the $0.01 budget. No Simli session was started and no minutes were used.
- **Simli, checked against the vendor's docs:** `GET /faces` in `https://api.simli.ai/openapi.yaml` returns the account's own faces (each with an `owner_id`). The preset faces are a static docs page (21 ids), and the OpenAPI has no preset or get-by-id endpoint. So "0 listed" is what `GET /faces` returns for an account with no faces of its own. The console's Simli catalog is unchanged and lists the same thing; whether it should also show the 21 documented presets is the coordinator's call.
- **Validation of `Demo — Vision assistant`** (`7ccd189876a74adca561cf88e2bf7e64`; `openrouter-llm` `google/gemini-3.5-flash`, camera and screen share on). `POST /v1/agents/{id}/validate` (read-only) → `ok=true`, no errors. The only warning left is the auto-inject tip ("Tip: auto-inject turns off preemptive generation … Nothing is broken; …", ask #77). The false "cannot see images" warning is gone (ask #76): the cached OpenRouter catalog says the model takes `image` input.
- **Still open:** a definite `tools` reading for models that cannot finish a forced tool call in 4 tokens needs a ruling on the budget (ask #78). The `openrouter-llm` registry flags are ask #79.
- **Side effects:** the four `provider_models` rows were overwritten by the route (as designed); `livekit-inference-llm`/`openai/gpt-4o-mini` now stores `detected.tools=null`. Audit rows `provider.test_model` were written. No agent config, connection, credential or worker was touched.

## Vision probes for #79 (R-V4-42, with the R-V4-41 re-tests, 2026-09-25, 06:00 IST)

The five `openrouter-llm` ids were probed with `probes: ["basic","tools","vision"]`, and the two LiveKit Inference ids of R-V4-41 were re-tested with `["basic","tools"]`, all with `force: true`, on the same dev api on `:8080`. R-V4-41's fix (`TOOLS_MAX_TOKENS = 16` on the `tools` call) was already on disk, and `uvicorn --reload` had picked it up: the Gemini `tools` row below reads "within the 16-token budget", a message only the new code produces.

- **Access.** One Builder key: scope `agents:write` (what `test-model` needs besides the builder role), named `R-V4-42 vision probes (temporary)`, minted with the admin token with a 1-day expiry. It was held only in a 0600 scratchpad file and never printed, and was **revoked at 06:01:35 IST** (`DELETE /v1/api-keys/{id}` → 204; the listing shows `revoked_at` set). The file was then deleted.
- **Calls.** Seven, one at a time, about 7 s after the previous one returned (under the 10-per-minute cap). None was answered from the cache (`cached=false` on all).

| Provider | Model / id | ok | Latency | Wall | Probes | Detected | Cost |
|---|---|---|---|---|---|---|---|
| `openrouter-llm` | `openai/gpt-4.1-mini` | ✅ true | 1604 ms | 4780 ms | basic ✓ (`ok`), tools ✓ 1182 ms, **vision ✓** 1941 ms "accepted an image" | `vision=true`, `tools=true` | $0.0000408 (catalog pricing) |
| `openrouter-llm` | `openai/gpt-4.1` | ✅ true | 1643 ms | 4080 ms | basic ✓ (`ok`), tools ✓ 1105 ms, **vision ✓** 1286 ms | `vision=true`, `tools=true` | $0.000708 (catalog pricing) |
| `openrouter-llm` | `openai/gpt-4o-mini` | ✅ true | 1326 ms | 3341 ms | basic ✓ (`Ok.`), tools ✓ 937 ms, **vision ✓** 1018 ms | `vision=true`, `tools=true` | $0.0012942 (catalog pricing; most of it is the image input) |
| `openrouter-llm` | `google/gemini-3.5-flash` | ✅ true | 1540 ms | 4830 ms | basic ✓ ("answered, with no text within the 4-token budget"), tools **inconclusive** 2031 ms ("no tool call within the 16-token budget"), **vision ✓** 1222 ms | `vision=true`, `tools=null` | $0.000903 (catalog pricing) |
| `openrouter-llm` | `anthropic/claude-sonnet-4.6` | ✅ true | 2390 ms | 5075 ms | basic ✓ (`ok`), tools ✓ 1088 ms, **vision ✓** 1545 ms | `vision=true`, `tools=true` | $0.002379 (catalog pricing) |
| `livekit-inference-llm` | `openai/gpt-4o-mini` | ✅ true | 1697 ms | 3601 ms | basic ✓ (`Ok.`), tools ✓ 1864 ms "called the tool" | **`tools=true`** (was `null`, ask #65) | $0.0000159 (platform price table) |
| `livekit-inference-llm` | `google/gemma-4-31b-it` | ✅ true | 1378 ms | 2903 ms | basic ✓ (`ok` plus a raw `<turn\|>` marker), tools ✓ 1485 ms "called the tool" | **`tools=true`** (was `null`) | $0.0000488 (platform price table) |

- **Vision (#79): all five pass.** Each accepted the 1×1 PNG with a 2xx. The contracts owner flips `supports_video=True` on all five `openrouter-llm` `ModelSpec` lines. None failed and none was inconclusive.
- **Tools (#78): both LiveKit Inference ids now read definite `tools=true`** at the 16-token budget. The stored rows are corrected by the route. `google/gemini-3.5-flash` on OpenRouter still reads `tools=null`: it spent the budget without a call (its `basic` answer also had no text within 4 tokens, which is typical of a thinking model). Per R-V4-41 that stays inconclusive, with no retry at a larger budget.
- **Vendor spend:** about $0.0054 in all, from the route's own estimates (catalog pricing for OpenRouter, the platform price table for LiveKit Inference). That is under the $0.01 budget. OpenRouter's image-input pricing accounts for most of the gpt-4o-mini and Claude figures.
- **Side effects:** the seven `provider_models` rows were overwritten by the route (as designed), and audit rows `provider.test_model` were written. No agent config, connection, credential or worker was touched.

## §4 step by step

1. **OpenRouter LLM: pass.** `openai/gpt-4.1-mini` was used instead of `google/gemini-3.8-flash` (the card asked for a cheap model). `tools` ✓, cost from `meta.pricing`. The nonsense id gives `ok=false` with the vendor's reason and no key fragment. A key-shaped id gives 422 with no echo.
2. **Other LLMs: LiveKit Inference passes (both models); the rest were not run (no keys).** `openai-llm`, `anthropic-llm` and `google-llm` have no stored key. The gateway JWT path works for `openai/gpt-4o-mini` and `google/gemma-4-31b-it` (the tools reading on gpt-4o-mini is ask #65). **Whether the gateway lists models with a JWT: still unverified.** The listing needs the connection's secret, which this run does not read (ask #68).
3. **TTS: fails on the entry's default; `deepgram/aura-2` passes.** `openrouter-tts` with `google/gemini-3.8-flash-tts`/`Kore` gets a 400 because the probe sends mp3 (ask #64); `deepgram/aura-2` passes (4608 bytes, TTFB 1186 ms). `deepgram-tts` was **not run** (no Deepgram key). The **Cartesia header variants** were **not run** (no Cartesia key), so asks #43 (c) and #59 (c) stay open.
4. **STT: pass.** `openrouter-stt` with `openai/gpt-4o-mini-transcribe` returns the transcript `OK.`, which contains "ok". `deepgram-stt` `nova-3` was **not run** (no key).
5. **Realtime: not run.** There is no OpenAI or Google key, so the `gpt-realtime`/`gemini-3.8-live` handshakes (and #59 (a), xAI) remain unverified.
6. **Catalogs, with the Builder key: pass where the check applies.**
   - `deepgram-tts/catalog?kind=models` returns **102** Aura ids with `source="vendor"` and **no Deepgram key stored** (the public-adapter path).
   - `openrouter-llm/catalog?q=claude&limit=5` returns 5 items of `total=28`.
   - `openrouter-tts/catalog?kind=voices&model=google/gemini-3.8-flash-tts` returns **30** voices, each listed once (`deepgram/aura-2` has 90).
   - `deepgram-stt/catalog?q=nova-3` returns `nova-3-general`, `nova-3-medical` and `nova-3-pharma`; `q=flux` returns **0** (ask #42).
   - `bey-avatar/catalog?kind=avatars` returns 10 avatars. `simli-avatar/catalog?kind=avatars` returns **0** (ask #67).
   - `google-realtime` (the `bidiGenerateContent` filter) was **not run** (no Google key).
7. **Validation walk: not run.** It needs the V4-09 console and a key rotation; the card leaves that walk to V4-09. The validator's disappearance/deprecation warning is covered offline by `api/tests/test_catalog_deprecation.py`.
8. **Drift: pass (run locally; the workflow is not dispatched).** The repo is not pushed, so the local equivalent was run: `python -m lkap_api.catalogs.drift --only keyless` with no keys in the environment. It checked **8** entries keyless (`deepgram-stt`, `deepgram-tts`, `rime-tts` and the five OpenRouter entries) and skipped 26 as "not selected by --only". The four sections:
   - `registry_not_upstream`: only `deepgram-stt` `flux-general-en`, marked expected (`KNOWN_UNLISTED`: Deepgram's `/v1/models` has no Flux entry).
   - `upstream_new`: `openrouter-llm` 366 (the 25 newest listed), `openrouter-image-gen` 54, `deepgram-tts` 101, `deepgram-stt` 40 (the `nova-3`/`nova-2` aliases hold), `openrouter-embedding` 36, `openrouter-stt` 18, `openrouter-tts` 15, `rime-tts` 3 (`coda`, `mist`, `mistv2`).
   - `deprecation_notices`: none. No registry id carries a vendor retirement signal; OpenRouter lists `expiration_date` on other models, for example `google/gemini-2.5-flash-lite` on 2026-10-20.

   Every registry id of the five OpenRouter entries is listed upstream. The raw public lists: Deepgram 450 STT and 102 TTS entries, Rime 4 models.

## Asks settled (only where a stored key exists)

- **#42 (Deepgram names): settled for the drift job.** It uses an alias map (short name ↔ `-general`), and `flux-general-en` is confirmed absent from `/v1/models` both live and through the api catalog. Whether the registry should also list the canonical ids is the coordinator's call.
- **#43 (ElevenLabs v2, Hume `provider`, Cartesia headers): still unverified.** No key.
- **#50: done.** The drift job's registry-level comparison is the backstop.
- **#59 (xAI realtime, Tavus replicas, Cartesia probes, ElevenLabs STT, Gemini tool): still unverified.** No key. **Bey's** `GET /v1/avatars/{id}` is now verified (200 for a real id, 404 for a nonsense one). **Simli's** membership probe fails on a working face (ask #67).
- **#60: partly exercised.** See the table: the `tools` inconclusive reading holds for a budget cut-off, but not for a 400 naming `max_tokens` (ask #65).

## Side effects

- `provider_models` rows were written by the route (as designed) for the ten `test-model` calls that passed id validation: two each for `openrouter-llm`, `openrouter-tts`, `livekit-inference-llm` and `bey-avatar`, one each for `openrouter-stt` and `simli-avatar`. The key-shaped id was refused with 422 before anything was written. **One stored reading is wrong:** `livekit-inference-llm` / `openai/gpt-4o-mini` has `detected.tools=false` (ask #65). No agent uses that pair; re-test with `force=true` after the fix. The avatar rows do not reach validation, because avatar slots carry their id in a field, not in `model`.
- Audit rows `provider.test_model` were written, as designed.
- No agent config, connection, credential or worker was touched.

## V4-09 console walk (screenshots)

Reserved for V4-09 (`PLAN-V4.md` V4-09 "Live"): pick `openrouter-llm`, type `google/gemini-3.8-flash`, Test, see the chip, save, validate; then an id the vendor rejects. Append one screenshot each here.

## Run log

- 05:07 Vault listing (ids and fingerprints only); the Builder key minted (expires in 1 day).
- 05:07 Free catalog reads: `bey-avatar`, `simli-avatar`, `deepgram-stt` (`q=flux`, `q=nova-3`), `deepgram-tts`, `openrouter-llm` (`q=claude`) and `openrouter-tts` voices.
- 05:08–05:10 The eleven `test-model` calls in the table, sequential.
- 05:11 Key revoked (204), and its scratchpad file deleted.
- 05:37 Re-run after the fixes (section above): the key minted, the four `force=true` calls, the Vision assistant validated, the key revoked (204) and its file deleted.
- 05:03 (before the api calls) `python -m lkap_api.catalogs.drift --only keyless`, with no keys: the §4 step 8 result above. It was re-run at 05:13 through `scripts/catalog_drift.py` with the final module: exit 0, the same sections, and `flux-general-en` marked expected.
- 06:00 Vision probes for #79 (section above): the Builder key minted (expires in 1 day); the seven `force=true` `test-model` calls, sequential, 06:00:25–06:01:32.
- 06:01 Key revoked at 06:01:35 (204; `revoked_at` set in the listing), and its scratchpad file deleted.
