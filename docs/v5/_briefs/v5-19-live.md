# V5-19 live check: caller files, describe_asset, cited documents

Status: **deferred** (not run by the implementing agent, it needs the `v5_002_session_uploads`
migration applied, an api and a worker restart and a browser session, all outside the package's
rules). The card runs its live check on the dev stack after V5-23 (the upload renderer and sender).
Steps 2, 3 and 7 can run before V5-23 with direct calls, steps 4 to 6 need it.

## Steps

1. Scratch api on its own port and a scratch database migrated to head (`v5_002_session_uploads`).
   Worker from this branch under a fresh agent name (never `lkap-agent`). A Builder key minted for the
   run and revoked at the end, `Demo — ` objects only. A cascaded agent whose LLM can see images
   (OpenRouter with a vision model, the existing key), with a panel of `upload`
   (`{"accept": ["image/*", "application/pdf"], "camera_capture": true}`), `gallery`, `details`,
   `document` and `kb_citations` blocks, and a knowledge base holding one PDF and one Markdown file.
   `recording.retention_days: 1` on a second, throwaway agent for step 7.
2. Api, with the service token: `POST /internal/v1/sessions/{id}/assets` for a live session with a
   fictional sample photo and `meta={"block_id": "<upload block>"}` → 201 with the sha256 of the file.
   The same call with an `.html` file renamed `.png` → 415, with 30 MB → 413. `kind=document` → 422.
   `GET /v1/sessions/{id}/assets` as the Builder → the file with a `url`. Open the `url` in a private
   window (no cookie) → the image, `X-Content-Type-Options: nosniff`, a `sandbox` CSP. Change one
   character of `sig` → 401, after 15 minutes the link → 401.
3. `POST /internal/v1/sessions/{id}/assets/from-document {"document_id": <the PDF>}` → 201, again →
   200 with the same id. A document of another knowledge base → 404. The Markdown one → 201,
   `mime: text/markdown`.
4. With V5-23, at 375 px in a phone-width browser: the agent calls `request_upload`. The picker opens
   (the camera on a phone with `camera_capture`). Send one photo: it appears in the upload block and
   the gallery, the agent says it received it, `describe_asset(task="describe")` describes it. Send a
   31 MB video: refused on the block before the upload starts, with the plain reason. Send a fourth file
   to a `max_files: 3` block: refused. Record the time from send to the block showing the file. From
   an iPhone, send a HEIC photo from the library (not the camera): note whether it shows in the
   gallery and whether `describe_asset` can read it (ask #134).
5. A printed **fictional** sample ID (never a real person's): the agent calls `request_upload`, then
   `describe_asset(task="extract_id")`. The fields land in the `details` block through `set_details`
   after the agent confirms them with the caller. The worker log shows `builtin_tool.describe_asset`
   with the task and asset id only, no field values. The api log shows `session_asset_stored` with ids,
   type and size only.
6. Tap a `kb_citations` entry that cites the PDF: the `document` block opens on the cited page (the
   first tap copies the document into the session: one `from-document` call in the api log, a second
   tap makes none). A citation of a Word document answers `no_preview` and the passage opens in the
   dialog.
7. Retention: end the throwaway agent's session, set its `ended_at` back two days on the scratch
   database, wait one sweep (`LKAP_SESSION_SWEEP_INTERVAL_S`): its rows are gone from
   `session_assets` and the files from `LKAP_DATA_DIR/storage/sessions/<id>/`.
8. Barge-in (ask #130): while the picker is open, say "one moment": the request is withdrawn, a file
   sent afterwards is refused as not requested, and the agent asks again. Note how often this happens in
   the run, it decides #114.
9. Clean up: the `Demo — ` agents, the knowledge base, the Builder key. Stop the scratch api and worker.
