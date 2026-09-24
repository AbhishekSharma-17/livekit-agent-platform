# V4-03 live check: OpenRouter (pending the user's key)

Status: **not run.** V4-03 landed offline (every OpenRouter call in the tests is mocked). The steps below are `OPENROUTER.md` §3 and run once the user has added an OpenRouter key in the console (Providers → LLM → OpenRouter → Add key). Nothing here has touched OpenRouter with a real key.

Live rules (HANDOFF rule 3, PLAN-V4 "Live rules"): a scratch api on its own port and DB, and a worker under a fresh agent name. Never `lkap-agent`, never `other-project-agent`. A `uv run` dev worker sees the five OpenRouter ids at once; an already-built image reports them only after a rebuild.

Record per step: pass or fail, the evidence (status codes, ids and counts, never a key value), and the date.

| # | Step | Expect | Result |
|---|---|---|---|
| 1 | Provider-key test for the OpenRouter key (console "Test", `POST /v1/credentials/{id}/test`) | `ok: true`; the message counts the tool-capable models; the preview shows five ids. Then a deliberately wrong key gives `ok: false` (proves the `/key` probe, R-V4-9). | pending |
| 2 | `GET /v1/providers/openrouter-{llm,stt,tts,embedding,image-gen}/catalog`, plus `openrouter-tts?kind=voices` | Vendor lists, `source: "vendor"`, all five authenticated by the one row stored under `openrouter-llm`; voices labelled like `Kore · Gemini 3.8 Flash TTS`. | pending |
| 3 | A cascaded agent from `blank`: `llm=openrouter-llm/openai/gpt-4.1-mini`, Inference STT/TTS; MCP `chat_start`/`chat_send` with an HTTP tool | The tool is called. Repeat with `anthropic/claude-sonnet-4.6` and `google/gemini-3.5-flash` (the strict tool-schema check). Record TTFT from `SessionLatency`. | pending |
| 4 | Same agent with `stt=openrouter-stt`, `tts=openrouter-tts/google/gemini-3.8-flash-tts/Kore`: a 60-second audio session | Works. Record end-of-speech to first audio, compared with step 3. | pending |
| 5 | QA with `qa.model=openrouter-llm` | A `session_qa` row scored by the api-process judge. | pending |
| 6 | Scratch api with `LKAP_EMBEDDER=openrouter-embedding:<credential_id>` | A KB import and a search hit. | pending |
| 7 | `image_gen=openrouter-image-gen`, the image block, one generation | One image rendered. | pending |

If step 3 fails on a non-OpenAI model because of the strict tool schema, file an ask for a `_strict_tool_schema` pass-through; do not monkey-patch the plugin (`OPENROUTER.md` §2.7). Only the ids that passed go into `VERIFIED_IDS`.

V4-04 adds one screenshot of the shared-key line in the credential dialog here.
