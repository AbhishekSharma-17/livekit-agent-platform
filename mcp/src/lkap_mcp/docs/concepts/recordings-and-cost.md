# Recordings and cost

## Recording

`config.recording` (`RecordingConfig{enabled, audio_only, storage_config_id,
retention_days}`) turns on LiveKit Egress for an agent's sessions. A
`storage_config_id` picks where recordings land (an operator-configured
storage backend); leaving it unset uses the workspace default, when one
exists. `session_get(session_id, include_recording_url=true)` returns a
freshly signed playback url when the recording is `ready` (`RecordingOut`);
the api's `GET /v1/sessions/{session_id}/recording` route is what the
console's play button calls, and 404s until it is ready.

## Cost

Every session accrues `CostLine`s (one per billed unit — an STT minute, an
LLM's tokens, a TTS character count, an avatar minute, …) rolled up into a
`SessionCost` on `session_get`. `activity()` and `session_list` do not
themselves total cost; read `session_get` per session, or
`GET /v1/analytics/summary` (via `api_request(method="GET", path=...)`) for a workspace-level
rollup bucketed over time (`AnalyticsSummary`, `AnalyticsBucket`).

Test chats (`chat_start`/`chat_send`) spend real Inference or vendor credit
and count against the agent's `max_concurrent_sessions` exactly like a
browser session — the tools' `cost_hint` says so up front.

## Related tools

`session_get`, `session_list`, `api_request`.

## Related schemas

`RecordingConfig`, `RecordingOut`, `SessionCost`, `CostLine`,
`AnalyticsSummary`, `AnalyticsBucket`.
