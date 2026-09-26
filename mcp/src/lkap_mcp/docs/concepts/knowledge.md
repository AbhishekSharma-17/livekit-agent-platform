# Knowledge

`kb_list()` returns every knowledge base; `kb_get(kb_id)` reads one back
with its documents. A knowledge base (`kb_create(name, embedder_id=
"fastembed-embedding")`) is a named collection of chunked, embedded
documents an agent can search. `fastembed-embedding` runs locally (no
vendor key); `openai-embedding` needs one, and `openrouter-embedding` uses
the shared OpenRouter key (see `lkap_explain("providers-and-keys")`).
`KbOut.chunk_count` tracks the total across every ready document.

## Adding documents

`kb_add_document(kb_id, ...)` takes exactly one source:

- `text` (+ `filename="notes.md"`) — paste content directly.
- `file_path` — a local file path, **stdio mode only**, capped at 25 MB.
- `url` — the api fetches it server-side, through its own outbound network
  guard, and ingests the body (`text/*`, markdown, JSON or PDF, 25 MB cap).
  The MCP process never fetches a user-supplied url itself: in remote mode
  that would be a server-side-request-forgery vector from the service's own
  network, and in local mode it would bypass the platform's guard entirely.

`wait=true` (default) polls until the document is `ready` or `failed` and
returns the final `KbDocumentOut`, including `chunk_count` or `error`.

## Searching

`kb_search(kb_id, query, top_k=5)` returns hits (`chunk_id`, `filename`,
`score`, and `text` as `Untrusted` — it is content someone uploaded, not an
instruction to follow). An agent's own knowledge search at conversation time
uses the built-in `search_knowledge` tool and, when
`config.knowledge.auto_inject` is true, the api also injects the top
`config.knowledge.top_k` hits automatically before the model needs to ask.

## Attaching to an agent

`agent_attach(id_or_slug, kb_ids=[...])` (or `agent_update(patch=
{"knowledge": {"kb_ids": [...]}})`) wires a KB in; `config.knowledge.
auto_inject` and `top_k` control the automatic behaviour above.

Retrieval settings (all under `config.knowledge`, defaults in brackets):
`mode` (`hybrid` — keyword matches fused with embedding similarity; or
`vector`), `rerank` (`none`; `local` rescores candidates with a local
cross-encoder, roughly 60–100 ms), `min_score` (none; a 0–1 floor — in
`hybrid` mode without rerank the score is rank-derived, so a floor only trims
the tail), `max_inject_tokens` (1200, the size cap of the injected note),
`skip_short_turns` (on: "yes", "okay", "haan ji", digits and turns under three
words never search), `query_mode` (`conversation` searches with the user's
turn plus the agent's previous sentence and flow variables, so "and the
deductible?" finds the right passage; `last_turn` uses the words alone) and
`prefetch` (on: the search starts while the caller is still speaking, so the
result is usually ready when the turn ends). A chunk injected in the last
three turns is not injected again. `agent_validate` flags a `min_score`
outside 0–1 and warns when `rerank="local"` runs without `prefetch`.

Packs can seed knowledge bases at agent-creation time (`PackManifest.
kb_seeds`) — the `insurance_claim` pack seeds "Insurance policy lines" and
"Intake playbook" from its own files, already populated by the time
`agent_create` returns.

## Measuring retrieval

A knowledge base can carry an evaluation set: golden questions, each naming
the document a correct hit comes from (`expected_document_id`, from
`kb_get`), a passage a correct hit contains (`expected_text`), or both.
`kb_evals_set(kb_id, items=[...])` replaces the whole set (at most 500).

`kb_evaluate(kb_id, mode="hybrid", rerank="none", min_score=None, k=4)`
runs every question through the same search the agent uses (the defaults
are an agent's knowledge defaults) as a background job and, with
`wait=true`, returns the finished run. A question is **found** when one of
the top `k` hits is its expected document or contains its expected text
(case and line breaks ignored). The run reports `recall_at_k` (found /
scored), `recall_at_1`, `mrr` (mean reciprocal rank: 1 for a first-place
hit, 0.5 for second, 0 for a miss), the same per tag in `by_tag`, and each
question's `rank` and top hits. A question whose expected document was
deleted is `skipped` and left out of the averages.
`kb_evaluate_result(kb_id)` reads the latest finished run (or one run by
`job_id`).

Compare modes on the same set before changing an agent's retrieval
settings: run `mode="vector"`, `mode="hybrid"` and `rerank="local"` and keep
the one with the best MRR for an acceptable latency (`latency_ms_p50`).
Tag questions (`["hi-Latn"]` for transliterated Hindi, `["identifier"]` for
policy or form numbers) to see where a mode helps. Starter templates and
packs that seed knowledge bases ship a small evaluation set with them.

## Related tools

`kb_list`, `kb_get`, `kb_create`, `kb_add_document`, `kb_search`,
`kb_evals_set`, `kb_evaluate`, `kb_evaluate_result`, `agent_attach`.

## Related schemas

`KbCreate`, `KbOut`, `KbDocumentOut`, `KbImportIn`, `KbSearchRequest`,
`KbHit`, `KbSearchResponse`, `KbSeed`, `KbEvalIn`, `KbEvalSetOut`.
