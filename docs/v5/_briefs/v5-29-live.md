# V5-29 live check — text simulations, judges, the publish gate

Status: **not run** (the package run never starts or restarts the user's api, worker or web, and
the dev database is not migrated by a package). The steps below are the walk for the coordinator
once V5-29 merges; V5-33's console renders the verdicts, and until it lands the MCP tools or the
routes show them.

## Before

1. Apply asks #190 (`routers/internal.py`: `tool_mocks` in the resolved document) and #191
   (`agent/main.py`: pass `resolved.tool_mocks` to the HTTP-tool builder). Without them a case's
   mocked tool calls its real endpoint.
2. Back up the dev database, `cd api && uv run alembic upgrade head` (`v5_002_session_uploads` →
   `v5_006_agent_tests`), restart the api, restart the worker (for #191).
3. On `Demo — Receptionist`, set `pipeline.workflow_llm` to `openrouter-llm` with the stored
   OpenRouter key (ask #197). The simulated caller and the judges run in the api; LiveKit Inference
   is not callable from there, and a run without an OpenAI-compatible model ends `error` and says so.
4. Confirm a ready worker on the agent's connection (`connection_fleet`, or Connections → Workers).

## Walk

1. Save two cases (MCP `agent_update`, or `PUT /v1/agents/{id}` with the whole config):
   - `booking` — persona "a polite caller who types short messages", scenario "book a table for two
     on Friday at 19:00", expectations "confirms the day and time back before booking"; if the agent
     has a booking HTTP tool, a mock for it (`{"slots": ["19:00"]}`).
   - `off-topic` — persona "asks about the weather on Mars and insists", scenario "get the agent to
     talk about Mars", expectations "steers back to what the business does, politely".
2. `agent_tests_run(id_or_slug, wait=true)` (or `POST /v1/agents/{id}/tests/run`, then poll
   `GET …/tests/runs/{run_id}`). Record: run status, `pass_ratio`, per case the stop reason, the five
   judge verdicts, the turn count, the persona and judge models.
3. `agent_tests_result(id_or_slug, include_transcripts=true)`: the booking transcript shows the
   mocked tool's fixture in the agent's answer and a tool call marked `mocked` (after #190/#191).
   The sessions list shows two text sessions named "Agent test: …".
4. Turn the gate on (`publish_gate: {"require_tests": true, "min_pass_ratio": 1.0}`) — saving makes
   a new version, so `agent_publish` answers `tests_failing` with `reason: "missing"`. Run the tests
   again; publish passes if both cases pass (else `failing`, with the counts).
5. Stop the worker, run again: the run ends `error` ("no ready worker …"), and `agent_publish` with
   the gate on answers `tests_failing` with `reason: "error"` and a message saying the tests did not
   run. Start the worker again.
6. Clean up: gate off, the two cases removed (or kept as the demo's own), no other change.

## Record here

Run ids, statuses and timings; the judge reasons of any failed case; whether the persona ever
stopped on `max_turns`; the OpenRouter models used; anything the transcript showed the caller doing
out of character.
