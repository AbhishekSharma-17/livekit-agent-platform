# V5-40 live check — caller memory

Status: **deferred** (not run by the implementing agent: it needs the `lkap-api[memory]` extra in the
dev venv, migration `v5_008_memory` applied, and an api and a worker from this branch, which the
package's rules leave to the coordinator). The recall at session start also needs ask #255 (the one
`main.py` line); without it the second session is written to memory but does not greet with it.
Offline evidence: `api/tests/test_memory.py` (50 tests, including one Mem0 round trip on a local Qdrant
folder with a fake embedder and a mocked model endpoint: verbatim and extracted writes, recall, forget,
purge; no `~/.mem0` written, telemetry off) and the agent's recall tests in
`agent/tests/unit/test_pipeline_modes.py`.

## Steps

1. `cd api && uv sync --extra memory`; apply the migration (ask #262); restart the api and a worker
   under a fresh agent name (HANDOFF rule 3).
2. Create a `Demo — memory` agent: cascaded, `llm` = an OpenRouter model with the stored key, and
   `memory = {"enabled": true, "consent_line": "We remember our calls to help you next time."}`.
   `agent_validate` shows no `memory.*` warning.
3. Session 1: `POST /v1/agents/{id}/connect` with a Builder key and
   `{"participant_identity": "demo-caller-1"}` (the console's Test chat passes no identity, ask #263);
   join, say "Please call me Ada, and mornings suit me best", hang up.
   `GET /v1/sessions/{id}/memory` → `store_status: stored`, facts in `stored`, a 64-hex `subject_id`.
   The session timeline shows `memory_recalled {status: empty}` and `memory_stored`.
4. Session 2 with the same identity: the agent's greeting or first reply uses a fact from session 1;
   `recall_status: recalled`. The agent says the consent line early in the call.
5. Forget: `DELETE /v1/memory/subjects/{subject_id}` (or V5-42's **Forget this caller**) →
   `forgotten: true`; sessions 1–2 now show empty `recalled`/`stored` and a `memory_forgotten` event.
6. Session 3 with the same identity: the agent knows nothing (`recall_status: empty`).
7. A session with no identity (a plain browser visit): `recall_status: no_identity`, nothing stored.
8. Clean up: `POST /v1/memory/purge {"confirm": true}`, delete the `Demo — ` agent, revoke the key.
