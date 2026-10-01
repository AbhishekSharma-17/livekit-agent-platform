# V5-39 live check: guardrails

Status: **deferred** (not run by the implementing agent, it needs a dev-stack worker and api from
this branch, a text chat and, for the classifier, a paid model call, all outside the package's
rules). The coordinator runs it after merge. V5-41's chips come with its own check.

No migration. Restart the api (validators, the two new `builtin_providers` slots) and the worker
(the three hook points).

## What is verified offline and what is still to confirm

- Verified against the installed livekit-agents 1.8.3 source: `StopResponse` from
  `on_user_turn_completed` drops the turn (`_user_turn_completed_impl`, ~2817). The hook is not
  called when a realtime model detects turns itself (~2755). `transcription_node` runs for `say()`,
  the pipeline reply and realtime (~3255 / ~3781 / ~4411), `AgentSession.interrupt(force=True)`
  returns a future, `SpeechHandle.wait_for_playout` exists.
- OpenAI moderation: `POST /v1/moderations`, `model: "omni-moderation-latest"`, response
  `results[0].flagged` and `categories` (OpenAI's moderation guide, accessed 2026-09-27).
- **To confirm live:** (a) an output trip cuts the spoken reply mid-sentence, not after it. (b) the
  safe reply is heard once, never twice. (c) the classifier's real latency against `budget_ms`
  (300 by default, raise it if every check times out). (d) text chat and voice behave the same.

## Steps

1. Scratch api on its own port and database. A worker from this branch under a fresh agent name
   (never `lkap-agent`). A Builder key minted for the run and revoked at the end, `Demo — ` objects
   only.
2. On `Demo — Blank agent` (cascaded) set, with `agent_update`:
   `guardrails = {input: [{kind: "regex", name: "Card numbers", pattern: "\\b(?:\\d[ -]?){13,19}\\b"}],
   output: [{kind: "classifier", name: "No medical advice", prompt: "Gives medical advice: tells the
   caller what medicine or dose to take, or what their symptoms mean."}], safe_reply: "Sorry, I can't
   help with that here."}` and ask it, in `instructions`, to answer health questions freely (so the
   output rule has something to catch). Also try saving `pattern: "(a+)+"` and `pattern: "(bad"`:
   both are refused (422) with the issue at `guardrails.input[0].pattern`.
3. `chat_start`, then `chat_send("my card is 4111 1111 1111 1111")`: the reply is exactly the safe
   reply. The session events show `guardrail {stage: input, rule: "Card numbers", action:
   interrupt, excerpt_hash, excerpt: "my card is …"}` (the storage tier is `full`). The transcript has
   no caller line for that turn. Set `privacy.storage_tier = "redacted"` and repeat: `excerpt` is
   `null`.
4. `chat_send("I have a headache, how many ibuprofen should I take?")`: the reply starts, is cut, and
   the safe reply follows. One `guardrail {stage: output, kind: classifier}` event with `latency_ms`.
   Record the latency. Set `budget_ms: 50` and repeat: the full reply goes through and a
   `guardrail_timeout {reason: timeout}` event is recorded (fail open).
5. Voice: the same two turns in a browser session. Record (a), (b) and (d).
6. `on_trip: "end_call"`: the call ends after the safe reply. `on_trip: "escalate"` with
   `escalate_to_human` enabled: an `escalation {reason: "A guardrail tripped (Card numbers).",
   urgency: "high"}` event, and the reason never quotes the caller.
7. Tool output: an HTTP tool whose fixture returns a card number, with a `tool_output` regex rule:
   the model reads "This tool's result was withheld…", `guardrail {stage: tool_output, action:
   replaced, tool: <name>}`.
8. Moderation (optional, needs an OpenAI key in the vault): a `provider` rule on `input`. A hostile
   sentence trips with `categories`, without any OpenAI key the save is refused.
9. Compatibility: an agent without `guardrails` answers the same card-number turn normally and
   records no `guardrail*` event.

Clean up the `Demo — ` changes and revoke the key.
