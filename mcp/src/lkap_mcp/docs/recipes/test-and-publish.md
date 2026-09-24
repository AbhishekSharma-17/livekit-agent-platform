# Recipe: test in chat, then publish

Goal: confirm an agent actually behaves before making it live, using a real
session over the LiveKit room rather than reading the config and guessing.

## 1. Start a chat

`chat_start(...)`
```json
{ "agent_id_or_slug": "<agent>", "wait_for_greeting": true }
```
If this comes back `ok=false, code="no_worker"`, there is no ready worker on
the agent's connection yet — follow `next_steps` (start a worker for an
`external` connection, or `connection_fleet(action="start")` for a
`supervised` one) before trying again.

## 2. Send a few realistic turns

`chat_send(...)`
```json
{ "chat_id": "<chat id>", "text": "My basement flooded during last night's storm, policy H0-44721." }
```
Read the reply and the `events` list — a knowledge-base hit or a tool call
should show up here if the agent is wired the way you expect. Send at least
one more turn to see how it follows up.

## 3. Rewind if a turn went wrong

`chat_rewind(...)`
```json
{ "chat_id": "<chat id>", "turn_index": 1, "replace_text": "Actually, it was a burst pipe, not a storm." }
```

## 4. End the chat

`chat_end(...)`
```json
{ "chat_id": "<chat id>" }
```
The transcript and QA verdict land on `session_get` once the worker's
summary arrives (usually within a few seconds).

## 5. Publish

Only after `agent_validate` is clean and the test read the way you wanted:

`agent_publish(...)`
```json
{ "id_or_slug": "<agent>", "published": true }
```

## Related concepts

`lkap_explain("sessions-and-test-chat")`.
