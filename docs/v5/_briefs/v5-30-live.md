# V5-30 live check — privacy and post-call fields

Status: **deferred** (not run by the implementing agent: it needs an api and a worker from this branch,
which the package's rules leave to the coordinator). No migration: the scrub records a
`privacy_scrubbed` session event instead of a column. The automatic scrub at the end of a call needs
ask #174 (the one-line enqueue in `routers/internal.py::put_summary`); until it lands, step 4 uses
`POST /v1/sessions/{id}/scrub`. The console half (the Privacy card, the fields editor, the fields on
the session detail and in the table) is V5-34; steps 3–5 read the api directly until then.
**STT redaction: deferred** (needs a Deepgram key, or LiveKit Inference confirmed to pass `redact`
through; the registry lists redaction on `deepgram-stt` only).

## Steps

1. Scratch api on its own port and a scratch database at head (`v5_002_session_uploads` or later);
   worker from this branch under a fresh agent name (never `lkap-agent`); a Builder key minted for the
   run and revoked at the end; `Demo — ` objects only, cleaned up.
2. `Demo — Insurance claim intake`: `agent_update(patch={"qa": {"enabled": true, "fields":
   [{"name": "claim_type", "type": "select", "options": ["auto", "home", "travel"], "description":
   "The kind of claim the caller reports"}, {"name": "injury", "type": "boolean", "description":
   "Whether anyone was hurt"}]}, "privacy": {"storage_tier": "redacted"}})`. `agent_validate`: no
   privacy warning. Also try `privacy.stt_redact=["pci"]` on the Inference STT: one warning at
   `privacy.stt_redact` ("… cannot mask pci while transcribing; it is ignored"); put it back to `[]`.
3. One text chat (`chat_start` / `chat_send` / `chat_end`) as a fictional caller: "My kitchen flooded
   last night, nobody was hurt. My card is 4111 1111 1111 1111 and my email is jane.roe@example.com."
   After `chat_end`, `session_get(session_id)`: `qa.fields == {"claim_type": "home", "injury": false}`
   (record the judge model and how long the fields took; `raw.fields_error` if they failed).
4. Scrub: with ask #174 applied, `scrubbed_at` is already set; otherwise `POST
   /v1/sessions/{id}/scrub` → 202 `queued`, then `scrubbed_at` is set. The transcript reads "My card is
   [card number] and my email is [email]"; `session_events` shows the `user_turn` texts masked and a
   `privacy_scrubbed` event `{tier: "redacted", replaced: {...}, model_pass: "none"}`. A second `POST
   …/scrub` → `already_scrubbed`.
5. `GET /v1/sessions/export.csv?agent_id=<id>` → the file's last columns are `claim_type, injury`
   with `home, false` for the session.
6. Optional, with an OpenRouter key: `privacy.scrub_model = {provider_id: "openrouter-llm",
   credential_id: <key>, model: <a small chat model>}`; one more chat naming the caller "Jane Roe" and a
   street address; after the scrub the name reads `[name]`, the address `[address]`,
   `model_pass: "done"`. Record the cost line of that call.
7. `storage_tier: "basic"` on a session that calls a tool (`current_time` is enough): after the scrub
   the `tool_call_started` event has no `args_redacted` and `tool_call_ended` no `result_preview`.
8. Delete one session from step 3 (`DELETE /v1/sessions/{id}`): its uploaded files (if any) and its
   recording object are gone at once (S5-36).
9. Knowledge (S5-28/S5-30): upload `notes.exe` → 415; a url import named `x.docx` over a text page is
   stored as `x.docx.txt`; two `POST …/reindex` in a row on a KB with a large document → the second is
   409 while the first runs.
10. Telemetry (optional, needs an OTLP collector): with `OTEL_EXPORTER_OTLP_ENDPOINT` set on the worker
    and ask #175 applied, `privacy.telemetry_pii=false` → the worker logs "third-party telemetry
    personal data setting applied" (`allow_pii=false`) and the collector's spans carry no conversation
    text. LiveKit Cloud Insights is not affected.

## Known limits recorded here

- The worker scores the call from its own copy of the transcript after the summary: a QA summary
  posted after the scrub may quote what the scrub masked (the scrub masks the QA summary only when the
  verdict already exists). The post-call field values are kept as extracted, by design.
- The recording is not touched by the tiers (`recording.retention_days` owns it); `sessions.variables`
  and `caller` are kept (flow and webhook data the builder asked for).
- `telemetry_pii` is applied once per worker process (the SDK installs its filter once per tracer
  provider); with process reuse the first session's setting holds.
