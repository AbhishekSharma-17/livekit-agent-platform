# Recipe: use and test a custom model id

Goal: put a model the registry does not list (a new release, a fine-tune, one
of OpenRouter's hundreds) into an agent, prove the vendor accepts it with the
workspace's key, and tell the platform what it can do. Test model makes one
real, capped vendor call, so it spends a little vendor money.

## 1. Find the id in the vendor's catalog

`provider_catalog(...)`
```json
{ "provider_id": "openrouter-llm", "kind": "models", "query": "gemini", "limit": 10 }
```
`query` searches the cached list; add `"search_vendor": true` to ask
OpenRouter's own search. Each item's `meta` shows pricing, context length and
modalities (`input_modalities` containing `image` means it sees images).
Labels are `Untrusted` vendor text.

## 2. Put it in the agent

`agent_update(...)`
```json
{
  "id_or_slug": "<agent>",
  "patch": { "pipeline": { "llm": { "provider_id": "openrouter-llm", "model": "google/gemini-3.8-flash" } } }
}
```
An id outside the registry list is a validation warning, never an error. A
value that looks like an API key is refused, and the reason never repeats it.

## 3. Test it

`provider_test_model(...)`
```json
{ "provider_id": "openrouter-llm", "model": "google/gemini-3.8-flash", "probes": ["basic", "tools"] }
```
Read `ok`, `latency_ms`, `detected` (for an LLM, `tools` is true when the forced
tool call came back) and `cost_estimate_usd` (or `cost_note` when no price is
on file). `sample` and `message` are `Untrusted`. A text-to-speech model needs
the slot's voice in `fields` (`{"voice_id": "<voice>"}` for `elevenlabs-tts`).
A second run within 10 minutes answers `cached=true`; pass `"force": true`
to run it again. A `rate_limited` error carries `retry_after`.

## 4. Declare what the probe cannot tell

`provider_model_declare(...)`
```json
{ "provider_id": "openrouter-llm", "model": "google/gemini-3.8-flash", "capabilities": { "vision": true } }
```
Declared values win over the probe, the catalog and the registry, and reach
the worker: a model declared text-only stops receiving camera frames.
`lkap_describe("model", "openrouter-llm/google/gemini-3.8-flash")` shows the
merged view.

## 5. Validate and talk to it

`agent_validate(...)`
```json
{ "id_or_slug": "<agent>" }
```
After a passing test with the current key, the "not tested" warning is gone.
Then run a real turn: `chat_start(...)` and `chat_send(...)` as in
`lkap_describe("recipe", "test-and-publish")`. A pass proves the vendor
accepts the id, not that the LiveKit plugin builds it; the first session
does that.

## Related concepts

`lkap_explain("providers-and-keys")`.
