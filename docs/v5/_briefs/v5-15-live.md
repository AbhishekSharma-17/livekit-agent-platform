# V5-15 live check — consent and disclosure

Status: **deferred** (not run by the implementing agent: it needs the migration applied, an api and a
worker restart, and a browser session, all outside the package's rules). The card's live check runs
after V5-17 (the consent block renderer and the banner); steps 2–5 can run before it with the MCP test
chat and direct calls, step 6 needs V5-17.

## Steps

1. Scratch api on its own port and a scratch database migrated to head (`v5_009_consent`); worker from
   this branch under a fresh agent name (never `lkap-agent`), a Builder key minted for the run and
   revoked at the end; `Demo — ` objects only. A connection whose last probe says Egress is enabled and
   an S3-compatible storage config (the recording path needs both).
2. Workspace: `GET /v1/workspaces/default/compliance` → `settings.jurisdiction = "in"`, three presets,
   the `us` counsel note mentions two-party-consent states. `PUT /v1/workspaces/default
   {"settings": {"compliance": {"jurisdiction": "eu"}}}` → 200; `{"compliance": {"states": ["CA"]}}` →
   422.
3. Disclosure: an agent with the default `disclosure` (`both`). A text chat (`chat_start`/`chat_send`)
   and a browser session both open with the EU disclosure line in front of the greeting, once. Set
   `disclosure.position = "banner"`: the browser greeting is unchanged (and `agent_validate` warns that
   no consent block shows a banner). Set `voice.greeting` to `"Hi, this is Demo Insurance. {disclosure}
   How can I help?"`: the line lands at the placeholder.
4. Recording consent without a block: `recording {enabled: true, require_consent: true}`,
   no consent block. `agent_validate` warns ("callers can agree to the recording out loud; add a
   consent block so they can also tap to accept"). Browser session: the agent asks the recording
   question word for word; answer "yes": a `consent` event `{kind: "recording", accepted: true,
   method: "voice", text_hash: <sha256 of the question>}` appears, then a `recording` event
   `{status: "active"}`, and the session row has an Egress id. Record the time between the answer and
   the Egress start.
5. Decline: same agent, answer "no": the `consent` event says `accepted: false`, no `recording` event,
   no Egress id; after the call `GET /v1/sessions/{id}` shows `recording.status = "none"` and
   `recording.error = "Not recorded: consent declined"`. Hang up without answering in a third session:
   the error reads "Not recorded: the caller did not agree to be recorded".
6. With V5-17: add a `consent` block (`kind: "recording"`, `decline_action: "end_call"`). A browser
   session shows the banner; the agent reads the disclosure and calls `request_consent`; tap Accept:
   the block shows accepted, the recording starts (Egress row present). New session, tap Decline: the
   agent says the goodbye line and the call ends; no recording. New session, answer out loud while the
   question is on screen: the question is withdrawn (barge-in) and `record_consent` records `voice`.
7. Realtime pipeline (optional, Gemini Live): repeat step 6's tap; the model stays silent after
   `request_consent` and acknowledges the tap once.
8. Clean up: `Demo — ` agents archived, the Builder key revoked, the scratch api and worker stopped.
