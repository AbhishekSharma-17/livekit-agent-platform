"""Job kind names shared across packages (PLAN-V2 V2-08 scope line).

V2-08 owns the job *engine* (this package) and registers the handlers it also
owns (:data:`KB_INGEST`, :data:`WEBHOOK_DELIVERY`, :data:`QA_SCORING`,
:data:`USAGE_DAILY_ROLLUP`). :data:`CONNECTION_PROBE`, :data:`CATALOG_REFRESH`,
:data:`WORKER_SWEEP` and :data:`RECORDING_FINALIZE` are reserved names for
other packages' own business logic (their files, not this package's) —
nothing here enqueues them itself; a job row with one of these kinds and no
registered handler is logged and marked ``failed`` rather than retried
forever (see :meth:`lkap_api.jobs.service.JobsService.run_due`).
"""

from __future__ import annotations

#: Owned and enqueued by this package.
KB_INGEST = "kb_ingest"
WEBHOOK_DELIVERY = "webhook_delivery"
QA_SCORING = "qa_scoring"
USAGE_DAILY_ROLLUP = "usage_daily_rollup"

#: V5-04 (D-V5-12, single writer): removes a deleted document's or knowledge
#: base's vectors (and any chunk row an in-flight ingest left behind), so in
#: production only the `jobs` process writes to the vector store. Enqueued by
#: the delete routes in `routers/knowledge.py`; handler in `kb/jobs.py`.
KB_DELETE = "kb_delete"

#: V5-05 (K §2 P0-9, the eval harness): runs a knowledge base's golden
#: questions through the knowledge search and stores recall@k and MRR in the
#: job row's own payload (``payload["result"]``; the table has no result
#: column). Enqueued by ``POST /v1/knowledge-bases/{id}/evaluate``; handler in
#: `kb/evals.py`.
KB_EVALUATE = "kb_evaluate"

#: V5-13 (D-V5-13): re-embeds a knowledge base's chunk rows with the current
#: embedder and replaces its vectors in the current vector store, recording
#: the embedder on the knowledge base. The move from LanceDB to pgvector and
#: every embedder change go through it. Progress and the outcome are written
#: into the job row's own payload (``progress``, ``result``). Enqueued by
#: ``python -m lkap_api.kb.jobs reindex``; handler in `kb/jobs.py`.
KB_REINDEX = "kb_reindex"

#: V4-17 (docs/v4/COSTS.md D-V4-45): looks up the vendor's own charge for a finished
#: session's per-request ids (OpenRouter `/generation`) and writes `vendor_usd` /
#: `reconciled_usd`. Enqueued after the session summary commits, only when the
#: workspace opted in; handler in `jobs/reconcile.py`.
COST_RECONCILE = "cost_reconcile"

#: V5-29 (D-V5-29): plays an agent's test cases (a simulated caller per case over a
#: scratch text session) and judges each transcript; verdicts land in
#: ``agent_test_results``, the totals on ``agent_test_runs``. Enqueued by
#: ``POST /v1/agents/{id}/tests/run``; handler in `agent_tests/runner.py`.
AGENT_TESTS_RUN = "agent_tests_run"

#: V5-30 (P §4.2 C10): the post-call privacy scrub. Masks emails, card numbers and long
#: numbers (plus the optional `privacy.scrub_model` LLM pass) in a finished session's
#: transcript, event payloads and final UI state, and drops tool payloads for the `basic`
#: tier; records a `privacy_scrubbed` session event. Enqueued once the summary commits when
#: `privacy.storage_tier != "full"`, or by `POST /v1/sessions/{id}/scrub`; handler in
#: `privacy/scrub.py`.
SESSION_SCRUB = "session_scrub"

#: V5-40 (D-V5-17): writes a finished session to the caller's memory (the transcript, masked
#: when `privacy.storage_tier != "full"`, through the agent's model or verbatim); records a
#: `memory_stored` session event, once per session. Enqueued once the summary commits when
#: `memory.enabled`; handler in `memory/jobs.py`.
MEMORY_REMEMBER = "memory_remember"

#: V5-40: deletes a purged workspace's memories from the backend (`{workspace_id,
#: subject_ids}`). Enqueued by `POST /v1/memory/purge` after the key and the subject rows are
#: gone; handler in `memory/jobs.py`.
MEMORY_PURGE = "memory_purge"

#: V6-16 (D-V6-27): imports an uploaded dataset's rows and normalised keys in batches,
#: writing ``done``/``total`` (and ``error`` on failure) into the job row's own payload, then
#: marks the dataset ``ready`` or ``failed``. Enqueued by ``POST /v1/datasets``; handler in
#: `jobs/dataset_import.py`.
DATASET_IMPORT = "dataset_import"

#: Reserved for other packages' handlers (PLAN-V2 §"V2-08" scope line); this
#: package never enqueues these itself.
CONNECTION_PROBE = "connection_probe"
CATALOG_REFRESH = "catalog_refresh"
WORKER_SWEEP = "worker_sweep"

#: Owned and enqueued by V2-12 (`lkap_api.recordings.job`, ask #41): fans the
#: `recording.ready` webhook out once an Egress finishes. Rows of this kind
#: are inserted directly on the caller's open `AsyncSession` (never via
#: `JobsService.enqueue`, which opens its own connection) because both
#: producers — the `egress_ended` webhook handler and the worker-posted
#: `POST /internal/v1/sessions/{id}/recording` finaliser — already hold an
#: open transaction when they learn the outcome; the inline poller (or the
#: `arq` worker) picks the row up on its own connection a tick later, the
#: same "commit before you call something that opens a session" rule
#: `docs/v2/_asks.md` #40 documents for the session summary path.
RECORDING_FINALIZE = "recording_finalize"
