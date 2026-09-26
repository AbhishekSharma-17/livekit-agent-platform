# V5-51 live check — the caller's date and time (R-V5-10)

Status: **deferred** (not run by the implementing agent: it needs a dev-stack session plus an api and
worker restart, both outside the package's rules). The coordinator runs it after merge. Phone
inference stays deferred (telephony offline); it is unit-tested on fixture numbers
(`agent/tests/unit/test_locale.py`).

Until V5-52 lands, the console does not send the browser's zone yet. Use the MCP test chat or a
direct call for steps 3–4: `POST /v1/agents/{id}/text-sessions` with `{"timezone": "<zone>"}` (or
`participant_metadata.timezone` on `…/connect`).

## Steps

1. Scratch api on its own port and database (migrated to head — no new migration); worker from
   this branch under a fresh agent name (never `lkap-agent`), a Builder key minted for the run.
2. Workspace default: `PUT /v1/workspaces/default {"settings": {"locale": {"timezone":
   "Asia/Kolkata"}}}` → 200; `{"locale": {"timezone": "Mars/Base"}}` → 422. Create an agent from
   the Receptionist starter: its `config.timezone` is `Asia/Kolkata`. Then set that agent's
   `timezone` to a zone that differs from the test machine's (for example `America/New_York`).
3. Text chat with `timezone` = the machine's zone (for example `Europe/London`): ask
   "what day is it tomorrow?" and "are you open now?". Expect the answer in the caller's zone, the
   opening hours converted from the business zone, and a `current_time` call (or none — the stamp
   already carries the date). Record the replies.
4. Session events: exactly one `locale` event `{caller_timezone: <machine zone>, source: "browser",
   business_timezone: "America/New_York"}`. After the chat ends, `GET /v1/sessions/{id}` (and MCP
   `session_get`) shows `caller_timezone`.
5. Same agent, text chat with no `timezone`: the `locale` event says `source: "business"` and the
   agent answers in the business zone (today's behaviour).
6. Set `locale.caller_timezone = "business"` and repeat step 3: `source: "business"`.
7. Optional, a session longer than 15 minutes (or a patched clock on the worker): one
   "Time now: …" system note at the tail of the context; the system prompt unchanged.

## Results

Not run yet.
