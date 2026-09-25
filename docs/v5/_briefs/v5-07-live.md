# V5-07 live check — conversation presets

Status: **deferred** (not run by the implementing agent: it needs a dev-stack voice session on
LiveKit Inference and a worker restart, both outside the package's rules). The coordinator runs it
after merge.

## Steps

1. Scratch api on its own port and database; worker from this branch under a fresh agent name
   (never `lkap-agent`), a Builder key minted for the run (R-V4-17).
2. `Demo — Phone agent`: `agent_update(patch={"pipeline": {"conversation_preset": "snappy"}})`;
   `agent_validate` shows no preset warning (cascaded pipeline).
3. Open the agent in a browser session; speak five short turns with natural pauses.
4. Session detail: record EOU→first-audio p50 (and p95).
5. Set `conversation_preset` to `patient`; repeat steps 3–4 in a new session.
6. Expected: `snappy` p50 lower than `patient` by roughly the endpointing difference
   (0.3 s vs 1.0 s minimum wait); the worker log line "session built" shows
   `conversation_preset` and `preemptive_generation=True` for `snappy`.
7. Optional: `voice.ambient_sound = "office_ambience"` — the clip plays under the call and never on
   a typed chat.
8. Restore the agent to `custom`, revoke the key, stop the scratch worker and api.

## Results

| Preset | Turns | EOU→first-audio p50 | p95 | Notes |
|---|---|---|---|---|
| snappy | | | | |
| patient | | | | |

Telephony noise cancellation: deferred (needs the LiveKit number online, Cloud NC is metered, and
the Cloud filter package is not yet in the worker image — `docs/v5/_asks.md` #31).
