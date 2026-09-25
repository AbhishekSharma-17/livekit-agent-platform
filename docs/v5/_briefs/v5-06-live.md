# V5-06 live check — retrieval gate, pre-fetch, conversation query

Status: **deferred** (not run by the implementing agent: it needs a dev-stack voice session and a
worker restart, both outside the package's rules). The coordinator runs it after merge. The
recall comparison against `KNOWLEDGE-BASELINE.md` is deferred too: that file is V5-05's and does
not exist yet.

## Steps

1. Scratch api on its own port and database (migrated to head); worker from this branch under a
   fresh agent name (never `lkap-agent`), a Builder key minted for the run (R-V4-17).
2. `Demo — Knowledge assistant`: `agent_validate` shows no `knowledge.min_score` or
   `knowledge.rerank` issue; `agent_get` shows `knowledge.mode = "hybrid"`, `prefetch = true`,
   `query_mode = "conversation"` (the defaults; the stored config has none of these keys).
3. Browser session, ten turns: four questions from the sample prompts, three backchannels
   ("okay", "yes", "haan ji"), one digits-only turn ("4 2 7 1"), and two follow-ups that only make
   sense with the previous answer (for example "and the deductible?" or "and for the Pro plan?").
4. Session detail, raw events: one `knowledge` event per retrieving turn and none for the
   backchannels or the digits. Record for each: `prefetch_hit`, `prefetch_ready_before_final`,
   `hits`, `deduped`, `tokens`.
5. Expected: `prefetch_hit = true` on at least 7 of the retrieving turns; `tokens` ≤ 1200 on
   every one; each follow-up answered from the right passage.
6. **Watch the follow-ups** (`docs/v5/_asks.md`, the dedupe question): the injected note is not
   kept in the conversation history (livekit-agents 1.8.3 keeps only the user and assistant
   messages), so a chunk deduped on a follow-up is one the model no longer has. If a follow-up
   answer misses a detail that was in a chunk injected one or two turns earlier and `deduped > 0`
   on that turn, record it; it is the evidence for the open ruling.
7. EOU→first-audio p50 (and p95) from the session latency panel, next to the V4 brief's number for
   the same agent.
8. Revoke the key, stop the scratch worker and api.

## Results

| Turn | Kind | `prefetch_hit` | `prefetch_ready_before_final` | `hits` | `deduped` | `tokens` | Notes |
|---|---|---|---|---|---|---|---|
| 1 | | | | | | | |

EOU→first-audio p50: — (V4: —)
