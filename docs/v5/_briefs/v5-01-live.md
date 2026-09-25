# V5-01 live check: knowledge ingest on the dev stack

**Status: steps written, not run yet.** The package ran in an isolated worktree with no api of its own, and the dev api and database are the user's. Whoever runs this fills in the **Result** lines and flips the status. PLAN-V5 §6 records "live: deferred (needs a scratch api run)" until then.

The card's check is: upload the fixture types to a scratch KB, and `POST .../search` returns hits whose chunks carry `meta.heading_path` and `page`. `KbHit` gains `meta` only in V5-04 (`_asks.md` #12 and #13). Until then, step 4 reads the locators of the returned `chunk_id`s from the scratch database.

## Rules (HANDOFF rule 3, PLAN-V5 §0.1)

- Use a **scratch api** on its own port, its own `LKAP_DATA_DIR` and its own SQLite file under the session scratchpad. Never use the dev api on `:8080`, never `api/data/lkap.db`, and never the user's `lkap-agent` worker; this check needs no worker at all.
- Read no `.env*` file. Export only the variables below into the scratch shell.
- Stop only the PID you started, never with a broad `pkill`.

## 0. Scratch api

From the worktree (or `main` after the merge):

```
S=<scratchpad>/v501-live
mkdir -p "$S/data"
# optional: saves the ~70 MB model download on the first ingest
cp -R <main checkout>/api/data/models "$S/data/models"
cd api
export LKAP_DATA_DIR="$S/data" LKAP_DATABASE_URL="sqlite+aiosqlite:///$S/data/lkap.db"
export LKAP_ADMIN_TOKEN=v501-admin LKAP_SERVICE_TOKEN=v501-service LKAP_MASTER_KEY=<a fresh Fernet key>
export LIVEKIT_URL=wss://example.livekit.cloud LIVEKIT_API_KEY=unused LIVEKIT_API_SECRET=unused-secret-long-enough-for-hs256
uv run alembic upgrade head                       # fresh DB, ends at v5_001_knowledge_p0
uv run uvicorn lkap_api.main:app --port 8091 &    # note the PID
A='-H X-Admin-Token:v501-admin'; B=http://127.0.0.1:8091
```

A fresh Fernet key: `uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`.

**Result:**

## 1. Create a scratch KB: the embedder is recorded

```
curl -s $A -X POST $B/v1/knowledge-bases -H 'content-type: application/json' -d '{"name":"Demo — V5-01 locators"}'
```

Expect `dimension: 384`, `embedder_model: "BAAI/bge-small-en-v1.5"` and `chunking: {"max_tokens":256,"overlap":32}`. Keep the id as `$KB`.

**Result:**

## 2. Upload the four fixtures

```
for f in policy_guide.md policy_summary.pdf claims_handbook.docx claims_faq.html; do
  curl -s $A -F "file=@tests/fixtures/kb/$f" $B/v1/knowledge-bases/$KB/documents; echo; done
```

Each answers 202 with `progress: 0.0`.

**Result:**

## 3. Poll the documents: status and progress

```
curl -s $A $B/v1/knowledge-bases/$KB/documents | python -m json.tool
```

Expect all four `ready`, each with `progress: 1.0` and a non-zero `chunk_count`. Record the chunk counts, which come from the real bge tokenizer (the unit tests pin the counts from the heuristic counter). A document with more than 50 chunks shows an intermediate `progress` if you poll while it runs; the fixtures are too small for that.

**Result:**

## 4. Search, then read the hits' locators

```
curl -s $A -X POST $B/v1/knowledge-bases/$KB/search -H 'content-type: application/json' \
  -d '{"query":"is a burst pipe covered in winter","k":4}' | python -m json.tool
curl -s $A -X POST $B/v1/knowledge-bases/$KB/search -H 'content-type: application/json' \
  -d '{"query":"flood rider exclusion","k":4}' | python -m json.tool
sqlite3 "$S/data/lkap.db" "SELECT d.filename, json_extract(c.meta,'$.heading_path'), json_extract(c.meta,'$.page') \
  FROM kb_chunks c JOIN kb_documents d ON d.id=c.document_id WHERE c.id IN ('<chunk ids from the hits>')"
```

Expect:

- The first query's top hit comes from `policy_guide.md`, with `heading_path = ["Harbor Lane Mutual policy guide","Water damage","Burst pipes in winter"]` and `page` null.
- Among the second query's hits, the `policy_summary.pdf` chunk carries `page = 2`. The exact ranking against `policy_guide.md` depends on the model and is recorded, not asserted.
- Some hit or chunk of `claims_handbook.docx` has `heading_path` `["Claims handbook","Reporting a loss","Water damage"]`.
- `SELECT count(*) FROM kb_chunks WHERE text LIKE '%<%'` for the `claims_faq.html` document is 0.
- `SELECT count(*) FROM kb_chunks_fts` equals `SELECT count(*) FROM kb_chunks`, because the triggers indexed every new chunk.

**Result:**

## 5. Evals CRUD

```
DOC=<the policy_guide.md document id>
curl -s $A -X PUT $B/v1/knowledge-bases/$KB/evals -H 'content-type: application/json' -d "{\"items\":[
  {\"question\":\"Is a burst pipe covered?\",\"expected_document_id\":\"$DOC\",\"tags\":[\"water\"]},
  {\"question\":\"What does the backup rider cost?\",\"expected_text\":\"forty dollars\"}]}"
curl -s $A $B/v1/knowledge-bases/$KB/evals
```

Expect the same two items in order, each with an `id`, and `total: 2`. A `PUT` with an item that has neither expectation answers 422.

**Result:**

## 6. Re-index

```
curl -s $A -X POST $B/v1/knowledge-bases/$KB/reindex
curl -s $A $B/v1/knowledge-bases/$KB/documents
```

Expect all four ids in `queued` and `skipped: []`, then all four `ready` again with the same chunk counts.

**Result:**

## 7. The embedder mismatch refusal

Stop the scratch api (its PID only). Restart it with `LKAP_EMBED_MODEL=BAAI/bge-base-en-v1.5` (768 dimensions; nothing is downloaded until something embeds), then:

```
curl -s $A -X POST $B/v1/knowledge-bases/$KB/search -H 'content-type: application/json' -d '{"query":"x","k":2}'
```

Expect 422, `error.code = "kb_embedder_mismatch"`, a message naming "Demo — V5-01 locators", and `details.kb_dimension = 384`, `details.embedder_dimension = 768`. Restart without the variable and the same search answers 200.

**Result:**

## 8. Clean up

Stop the scratch api (its PID only) and `rm -rf "$S"`. Nothing was written outside the scratchpad.

**Result:**
