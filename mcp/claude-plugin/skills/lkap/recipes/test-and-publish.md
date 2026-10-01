# Recipe: test in chat, then publish

Goal: confirm an agent actually behaves before making it live, using a real
session over the LiveKit room rather than reading the config and guessing.

## 1. Start a chat

`chat_start(...)`
```json
{ "agent_id_or_slug": "<agent>", "wait_for_greeting": true }
```
If this comes back `ok=false, code="no_worker"`, there is no ready worker on
the agent's connection yet. Follow `next_steps` (start a worker for an
`external` connection, or `connection_fleet(action="start")` for a
`supervised` one) before trying again.

## 2. Send a few realistic turns

`chat_send(...)`
```json
{ "chat_id": "<chat id>", "text": "My basement flooded during last night's storm, policy H0-44721." }
```
Read the reply and the `events` list. A knowledge-base hit or a tool call
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

## 5. Save repeatable test cases

A chat is a one-off. To re-check the agent after every change, save test
cases in its config: each is a simulated caller with a goal and the
statements that must hold. Mock the tools whose real call you do not want
during a test.

`agent_update(...)`
```json
{
  "id_or_slug": "<agent>",
  "patch": {
    "tests": [
      {
        "id": "booking",
        "name": "Books a table",
        "persona_instructions": "A polite caller who types short messages.",
        "scenario": "Book a table for two on Friday at 19:00.",
        "expectations": ["The agent confirms Friday at 19:00 back before booking."],
        "mocks": { "check_slots": { "slots": ["19:00", "20:30"] } }
      }
    ],
    "publish_gate": { "require_tests": true, "min_pass_ratio": 1.0 }
  }
}
```
The caller and the judges run in the api: the agent needs a `workflow_llm`
(or `qa.model`) on an OpenAI-compatible provider with a key.

## 6. Run them and read the verdicts

`agent_tests_run(...)`
```json
{ "id_or_slug": "<agent>", "wait": true }
```
Each case gets five judge verdicts (task completion, tool use, safety,
relevancy, accuracy). For a failed case, read the conversation:

`agent_tests_result(...)`
```json
{ "id_or_slug": "<agent>", "include_transcripts": true }
```
A run whose `status` is `error` did not play its cases (no worker, no usable
model): fix what `error` names and run again. It says nothing about the agent.

## 7. Publish

Only after `agent_validate` is clean and the tests read the way you wanted:

`agent_publish(...)`
```json
{ "id_or_slug": "<agent>", "published": true }
```
With `publish_gate.require_tests` on, this is refused (`tests_failing`) until
the latest run on this version passes; `next_steps` says what to do.

## Related concepts

`lkap_explain("sessions-and-test-chat")`, `lkap_explain("testing")`.
