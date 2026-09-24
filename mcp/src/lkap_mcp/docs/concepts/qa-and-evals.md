# QA and evals

`config.qa` (`QaConfig{enabled, rubric_prompt, model}`) turns on an
LLM-judge pass over each session: after the call, a judge model scores the
transcript against `rubric_prompt` and the result lands on `session_get` as
`QaOut`. A flow's `qa` node is equivalent to turning `qa.enabled` on for
that agent without you needing to set it separately — its `rubric_prompt`
(when set) is what runs; `qa.model` still picks the judge either way.

## Which model judges

The worker resolves a `qa_llm` slot by walking `qa.model` → `workflow_llm` →
`llm` → the LiveKit Inference default, and only when `qa.enabled` is true —
you never set `qa_llm` directly, only `qa.model` (a `ProviderRef`) if you
want a specific judge.

## Re-scoring

`session_rescore(session_id, confirm=true)` asks the **api itself** to
re-run QA (`POST /v1/sessions/{session_id}/qa`, `202 Accepted`), which only
works when the resolved judge is one the api process can call directly with
a vendor credential — LiveKit Inference is not (`409 qa_judge_unavailable`).
When that happens, either set `qa.model` to a vendor-key provider before
re-scoring, or accept the worker's own verdict from the original session
(it already ran a judge during the call itself, before this failure mode
can occur).

## Related tools

`session_get`, `session_rescore`, `agent_update`.

## Related schemas

`QaConfig`, `QaOut`, `QaVerdict`, `SessionQaIn`.
