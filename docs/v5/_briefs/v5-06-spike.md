# V5-06 spike — can the knowledge pre-fetch keep preemptive generation?

Verdict: **the rule stands.** An agent with knowledge auto-inject and a knowledge base keeps
preemptive generation off (research-v4 knowledge-and-memory P0-0, `session_builder.py`), whether
`knowledge.prefetch` is on or off. The pre-fetch ships as a latency fix only: the hook usually
finds the search already answered, and the note still joins the per-turn context. The card is
complete either way (PLAN-V5 D-V5-18); this brief records why, and what the live check measures so
the rule can be revisited with data.

## What livekit-agents 1.8.3 does (read from the installed source)

- **The snapshot.** `AgentActivity.on_preemptive_generation` (`voice/agent_activity.py`, around
  line 2604) starts the speculative reply from `self._agent.chat_ctx.copy()`. It is called from
  `voice/audio_recognition.py` (around lines 1250 and 1302) when an STT **final segment** (or the
  preflight transcript) arrives, before end-of-turn is decided, and only for a cascaded `llm.LLM`
  (line 2573). It is retried on each changed final transcript, up to `max_retries`.
- **The hook.** `_user_turn_completed_task` (around line 2798) hands
  `on_user_turn_completed` a fresh `self._agent.chat_ctx.copy()`; what the hook adds to it is not
  kept in `Agent.chat_ctx` (the SDK's own comment next to it).
- **The decision.** The speculative reply is used only when the final transcript matches and
  `preemptive.chat_ctx.is_equivalent(temp_mutable_chat_ctx)` (around line 2858): same items, same
  ids, same content. Any note added in the hook makes the two differ, so the reply is cancelled and
  generated again (the second LLM call P0-0 removed).
- **What persists.** Only the user message and the assistant replies are inserted into
  `Agent.chat_ctx` (lines 2659, 2763, 3622, 3870). `update_chat_ctx` (line 769) replaces
  `Agent.chat_ctx` outright.

## The mechanism the spike tried

Commit the note to `Agent.chat_ctx` through `update_chat_ctx` while the caller is still talking,
and add nothing in the hook: the snapshot and the hook copy then both contain the note and the
reply survives. `agent/tests/unit/test_preemptive_generation.py` pins the predicate on real
`ChatContext`s:

| Case | `is_equivalent` | Reply |
|---|---|---|
| note added to the hook's copy (today, and what ships) | false | discarded |
| note committed to `Agent.chat_ctx` before the snapshot | true | kept |
| note committed after the snapshot | false | discarded |

## Why it is not shipped

1. **The race is usually lost.** The snapshot is taken on the final STT segment, which a streaming
   STT sends within a few hundred milliseconds of the caller pausing. The pre-fetch fires 300 ms
   after the last transcript change and then needs the search itself (tens of milliseconds for
   vector or hybrid on a warm local store; 95–193 ms more with `rerank="local"`,
   `_briefs/v5-04-live.md`). So the note normally lands *after* the snapshot, and a late commit
   discards the speculative reply exactly as today — having paid for it. Preemptive generation on
   plus a mostly-late note is strictly worse than preemptive off.
2. **A committed note is permanent history.** It stays in every later request until something
   removes it; removing it in the next hook would itself break that turn's snapshot, and removing
   it at the next interim joins the same race. That is a context-growth and correctness surface
   this card should not open on the strength of a unit test.
3. **The lift is not this card's file.** Turning preemptive generation back on for
   `prefetch=True` agents is `session_builder.py` (`disable_preemptive=auto_inject`), owned by the
   conversation-tuning package; it is filed as an ask to act on once the live numbers below say
   the race is usually won.

## What ships

- `knowledge.prefetch` (default on): a session-level `user_input_transcribed` listener
  (`main.py`, `lkap_agent.knowledge.prefetch_listener`) asks the current agent to search the
  running transcript 300 ms after it last changed; the hook reuses the result (awaiting at most
  150 ms) when the final text overlaps it (Jaccard ≥ 0.6) and the knowledge-base scope is the same,
  else searches with a 400 ms bound.
- The note still goes into the hook's per-turn copy; the pre-fetch never writes `Agent.chat_ctx`
  (pinned by `test_spike_verdict_prefetch_never_writes_the_agent_chat_context`).
- Preemptive generation stays off for auto-inject agents with a knowledge base, `prefetch` on or
  off (pinned by `test_spike_verdict_auto_inject_keeps_preemptive_off_with_or_without_prefetch`).
- **The measurement.** Each injected turn records a `knowledge` session event with
  `prefetch_hit` and `prefetch_ready_before_final`: whether the reused pre-fetch answered before
  the turn's last final STT segment, i.e. whether the note *could* have been in the preemptive
  snapshot. `None` when there is nothing to compare (typed turns, realtime models that transcribe
  themselves, turns that searched again).

## When to revisit

If the live check (`_briefs/v5-06-live.md`) shows `prefetch_ready_before_final = true` on most
retrieving turns, the next step is a package that (a) commits the note through `update_chat_ctx`
only when the pre-fetch answered before the last final segment, (b) prunes it at the next
interim, and (c) lifts the rule for `prefetch=True` in `session_builder.py`, with a fake-session
test that drives `AgentActivity` end to end.
