# Sessions and test chat

A **session** is one run of an agent: a browser visit to `/s/{slug}`, an
embedded widget, a phone call, or a `channel="text"` test chat you start
yourself. `session_list(agent_id=, status=, channel=, connection_id=, since=,
until=)` and `session_get(session_id, include_transcript=true)` are how you
read them back — `SessionDetailOut` carries the transcript (`Untrusted`
turns), QA, cost, latency and any variables a flow extracted.
`session_events(session_id, after_id=, types=)` streams the finer-grained
tool-call and block-update events; they are timer-flushed by the worker, so
treat them as best-effort, slightly-delayed, never a strict real-time feed.

## Test chat: your own session, over the room

`chat_start`, `chat_send`, `chat_rewind` and `chat_end` run a real
`channel="text"` session end to end — the MCP process itself joins the
LiveKit room and exchanges text with the agent, the same path the console's
Test chat panel uses. This is how you verify an agent works **before**
`agent_publish`, without a browser.

1. `chat_start(agent_id_or_slug, wait_for_greeting=true)` — preflights that
   the connection has a ready worker (`code="no_worker"` if not, with
   `next_steps`), starts the session, and returns the greeting.
2. `chat_send(chat_id, text)` — sends one turn, waits for the agent's final
   reply, and returns it plus any tool-call/block events since the last
   turn.
3. `chat_rewind(chat_id, turn_index, replace_text=)` — regenerates from an
   earlier point in the conversation, optionally replacing what you said
   there.
4. `chat_end(chat_id)` — ends the session and returns its `session_id` and
   turn count; the full transcript and QA verdict then show up on
   `session_get` once the worker's summary lands.

Limits: 3 concurrent chats per MCP process, a 5-minute idle timeout, every
chat disconnected if the server shuts down. A chat counts against the
agent's `max_concurrent_sessions` and rate limits exactly like any other
session, and spends real inference/vendor credit — the tools' `cost_hint`
says so.

## Related tools

`session_list`, `session_get`, `session_events`, `chat_start`, `chat_send`,
`chat_rewind`, `chat_end`.

## Related schemas

`SessionOut`, `SessionDetailOut`, `SessionEventOut`, `TranscriptTurn`,
`SessionLatency`.
