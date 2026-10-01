# Caller memory

An agent can remember returning callers across sessions. It is **off by
default** and switched on per agent with `agent_update(patch={"memory":
{"enabled": true}})`. The memory is read **once**, before the greeting, and
written **once**, after the session ends. Nothing happens during the call.

## Who is remembered

Callers are known by a **pseudonymous id** (`subject_id`, 64 hex
characters): a keyed hash of their phone number on a phone call, or of the
participant identity a trusted caller chose (an embedding site's backend
passing `participant_identity` to connect). The platform never stores the
number itself in the memory, and an anonymous web visitor (who gets a random
identity) is never remembered. Each workspace has its own memory key, so the
same caller has a different id in another workspace.

## Settings (`MemoryConfig`)

- `enabled`: off by default. While off nothing is read or written.
- `scope`: `agent` (only this agent's memories) or `workspace` (shared with
  every agent of the workspace that also uses `workspace`).
- `retention_days`: memories are deleted this many days after the caller's
  last remembered session (90 by default).
- `consent_line`: a sentence the agent says early in a call that will be
  remembered.
- `max_recall_tokens`: how much recalled text is added to the agent's
  instructions (400 by default, about four characters a token).
- `verbatim`: store the caller's own lines without a model picking out the
  facts. Off: after the call, the agent's language model (OpenAI or
  OpenRouter with a key) extracts short facts. `agent_validate` warns when
  there is no such model.

When the agent's `privacy.storage_tier` is `redacted` or `basic`, emails,
card numbers and long numbers are masked before anything is stored.

## Reading and forgetting

`session_memory(session_id)` shows what a session recalled and stored (the
memories are `Untrusted`. They come from what callers said), the caller's
`subject_id`, and when they were forgotten. Recalled memories reach the
agent inside the untrusted-content fence, as data, never as instructions.

`memory_forget(subject_id, confirm=true)` deletes everything every agent
remembers about one caller and blanks the memories recorded on their
sessions. `memory_purge(confirm=true)` deletes every caller memory of the
workspace, and its memory key, so nothing stored can be tied to a caller
again. Both need confirmation and cannot be undone. They call
`DELETE /v1/memory/subjects/{subject_id}` and `POST /v1/memory/purge`.

## The backend

The memories are kept by Mem0 (open source), installed on the server as the
api's `memory` extra. Without it `session_memory` shows a recall status of
`unavailable` and nothing is stored. The session timeline records
`memory_recalled`, `memory_stored` and `memory_forgotten` events.

## Related tools

`session_memory`, `memory_forget`, `memory_purge`, `agent_update`,
`agent_validate`, `session_get`.

## Related schemas

`MemoryConfig`, `SessionMemoryOut`, `MemoryForgetOut`, `MemoryPurgeOut`,
`MemoryRecalledEvent`, `MemoryStoredEvent`.
