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

## Knowledge connections (your own vector database, a re-ranking service)

By default a knowledge base's vectors live in the platform's own store. A
**knowledge connection** keeps them in a vector database the workspace
already runs instead — Qdrant, Pinecone or Weaviate — or adds a hosted
re-ranking service (Cohere or Voyage AI) for the search tool. The chunk text
always stays on the platform; the vector database holds only the vectors and
their ids.

1. Store the vendor key first (`provider_key_create(provider_id="pinecone",
   ...)`; ids `qdrant`, `pinecone`, `weaviate`, `cohere-rerank`,
   `voyage-rerank`). A local Qdrant or Weaviate may run without one.
2. `kb_connection_create(name, kind="qdrant", settings={"url": ...},
   credential_id=...)` stores the connection and runs its test. `settings`
   are the kind's non-secret fields: `url`, `collection` and
   `native_hybrid` (Qdrant, Weaviate), `index`, `cloud` and `region`
   (Pinecone), `model` (the re-rankers). A url must be https and pass the
   api's outbound network guard.
3. `kb_connection_test(connection_id)` lists the collections or indexes the
   key can see and checks the vector width of the one the connection uses
   against the width knowledge bases are built with; a mismatch fails with a
   message naming the collection or index.
4. `kb_create(name, connection_id=...)` creates a knowledge base whose vectors
   live there (one Pinecone namespace or one Weaviate tenant per knowledge
   base; in Qdrant every point carries the knowledge base and workspace it
   belongs to). Where a knowledge base lives is fixed once it exists.

`kb_connection_list()` shows every connection with its status, last error and
the key's fingerprint (never the key); `kb_connection_update(connection_id,
...)` renames it or changes its settings or key. The url, collection or index
cannot change while knowledge bases are stored through it, and a connection
with knowledge bases cannot be deleted (the error names them).

Re-ranking services: set `config.knowledge.rerank = "connection:<id>"`. Only
the `search_knowledge` tool uses it, never automatic knowledge, so
`agent_validate` refuses it while `auto_inject` is on. Each re-ranked search
reports what it used and its price (`rerank_usage`; "no price" when the
price is unknown).

## Managed search (Ragie)

A **managed search** knowledge base keeps its documents in a search service
that ingests and ranks them itself; the platform stores none of them. Ragie
is the first such service. Each knowledge base reads one Ragie **partition**.

1. Store the Ragie key (`provider_key_create(provider_id="ragie", ...)`).
2. `kb_connection_create(name, kind="ragie", settings={}, credential_id=...)`.
   Its settings are Ragie's search options: `rerank` (Ragie keeps only the
   passages it judges relevant; more accurate, slower) and `recency_bias`
   (newer documents rank higher). Both are off by default.
3. `kb_connection_test(connection_id)` checks the key and lists the
   partitions it can see (the first 100) in `collections`.
4. Create the knowledge base with `kind: "external"`, the connection's id and
   the partition as `external_ref` (lower-case letters, digits, `_` and `-`):
   the console's **New knowledge base** dialog does this under "Managed
   search (Ragie)". The kind and partition are fixed once it exists; it
   records no embedder.

Documents are added in Ragie, not here: an upload, url import or re-index to
a managed search knowledge base is refused (409). The knowledge base's
source (a `KbSourceOut`) reports how many documents the partition holds.
Deleting the knowledge base leaves the documents in Ragie.

Searching: attach it to an agent like any knowledge base. Ragie is asked for
the top `k` passages at the same time as the platform's own knowledge bases
are searched. Its scores are relative to that one search, so results are
merged **by rank**: the best passage of each knowledge base first, then the
second of each, and so on. A Ragie hit has `score_source: "external"`,
`meta.document_name`, `meta.source: "ragie"` and, when Ragie knows the
document's address, `meta.url`. `min_score`, `mode` and the platform's
re-rankers apply to the platform's own knowledge bases only. Automatic
knowledge skips Ragie knowledge bases — they answer through the agent's search
tool only — and every search counts against the Ragie plan; a slow or failing
Ragie is skipped with a `kb_timeout` or `kb_error` warning and the other
knowledge bases still answer. Passages reach the model inside the same
untrusted-content fence as every other knowledge result.

## Related tools

`kb_list`, `kb_get`, `kb_create`, `kb_add_document`, `kb_search`,
`kb_evals_set`, `kb_evaluate`, `kb_evaluate_result`, `agent_attach`,
`kb_connection_list`, `kb_connection_create`, `kb_connection_update`,
`kb_connection_test`.

## Related schemas

`KbCreate`, `KbOut`, `KbDocumentOut`, `KbImportIn`, `KbSearchRequest`,
`KbHit`, `KbSearchResponse`, `KbSeed`, `KbEvalIn`, `KbEvalSetOut`,
`KnowledgeConnectionCreate`, `KnowledgeConnectionOut`,
`KnowledgeConnectionTestOut`, `KbSourceOut`.
