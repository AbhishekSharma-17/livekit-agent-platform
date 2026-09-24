# LiveKit Phone Numbers in LKAP (V4-05)

Status: **decided** (Fable 5.1, 2026-09-25). Package card **V4-05** and rulings **R-V4-10 … R-V4-16** are in [`PLAN-V4.md`](PLAN-V4.md). Decisions here are **D-V4-14 … D-V4-22** (the numbering continues `OPENROUTER.md`).

The user wants a free LiveKit Cloud phone number to ring an LKAP agent. Today LKAP's telephony is trunk-based: a `PhoneNumber` row binds to a `SipTrunk`, and the inbound agent's dispatch rule is built on that trunk (`api/src/lkap_api/telephony/service.py::_create_rule_on_livekit`). A LiveKit-hosted number has no trunk, so the trunk requirement has to become optional along one narrow path, and the number itself has to be read from LiveKit rather than typed in.

## 1. Facts (verified 2026-09-25; where a fact is unverified it says so)

**LiveKit Phone Numbers (product).** US-only, inbound-only for now, no SIP trunk; managed with `lk number search/purchase/list/get/update/release` and the matching server API; a number is attached to a dispatch rule by `sip_dispatch_rule_id`; one free number and 50 inbound minutes on the Build plan; the user buys the number (purchases are the user's action).

**The server API** is `livekit.PhoneNumberService` (`protobufs/livekit_phone_number.proto` on `livekit/protocol` `main`; the generated Go lives in `livekit/livekit_phone_number.twirp.go`). RPCs and the fields LKAP uses:

| RPC | Request | Response |
|---|---|---|
| `ListPhoneNumbers` | `limit?`, `statuses[]` (`PhoneNumberStatus`), `page_token?` (`TokenPagination`), `sip_dispatch_rule_id?` | `items[]` (`PhoneNumber`), `next_page_token`, `total_count`, `offline_count` |
| `GetPhoneNumber` | `id?` **or** `phone_number?` | `phone_number` |
| `UpdatePhoneNumber` | `id?` or `phone_number?`, `sip_dispatch_rule_id?` (**singular** `optional string`), `name?` | `phone_number` |
| `SearchPhoneNumbers`, `PurchasePhoneNumber`, `ReleasePhoneNumbers` | — | not called by LKAP (D-V4-15, D-V4-22) |

`PhoneNumber`: `id`, `name`, `e164_format`, `country_code`, `area_code`, `number_type` (`MOBILE|LOCAL|TOLL_FREE|UNKNOWN`), `locality`, `region`, `spam_score`, `capabilities[]` (e.g. `voice`, `sms`), `status` (`ACTIVE|PENDING|RELEASED|OFFLINE`), `inbound_status` (`ACTIVE|UNAVAILABLE|DETACHED` — "not associated with dispatch rules"), `outbound_status` (`ACTIVE|UNAVAILABLE`), `assigned_at`, `released_at`, `sip_dispatch_rule_id` (**deprecated**), `sip_dispatch_rule_ids[]` (**repeated**), `created_at`, `updated_at`. **There is no price field** anywhere in the service.

**The installed Python SDK has no bindings.** `api/.venv` holds `livekit-api 1.2.1` and `livekit-protocol 1.1.27`, which are also the newest on PyPI (checked 2026-09-25). `livekit/api/` has `sip_service.py`, `room_service.py`, … and no phone-number service; `livekit/protocol/` has no `phone_number` module. The SDK's `TwirpClient.request` takes a protobuf `Message`, hardcodes `Content-Type: application/protobuf` and decodes with `response_class.FromString`, so it cannot carry a message the SDK does not have. LKAP therefore calls the service itself (D-V4-16).

**The `lk` CLI on this machine is 2.16.2** and has the commands: `lk number search --country-code --area-code --limit --json`, `lk number purchase`, `lk number list --limit --offset --status <active|pending|released|offline> --sip-dispatch-rule-id --json`, `lk number get`, `lk number update (--id | --number) --sip-dispatch-rule-id <id>`, `lk number release`. `--curl` prints the exact Twirp path and body (the implementer may read the path and the body from a redirected file; the printed `Authorization` header is a signed token and is never copied).

**Dispatch rules** (`SIPDispatchRuleInfo`, `livekit_sip.proto`): `trunk_ids` — "if empty, all trunks match"; `numbers` (field 13) — "will only accept a call made **to** these numbers"; `inbound_numbers` (field 7) — "made **from** these numbers" (a caller filter, not what we want); `room_config.agents[]` is the `RoomAgentDispatch` LKAP already sets. LiveKit refuses a new rule that conflicts with an existing one (same match set). The shared Cloud project also hosts `other-project-agent`, whose rules LKAP cannot see from its own tables.

**Worker and webhooks today.** The worker needs nothing trunk-related: the job's `RoomAgentDispatch.metadata` (`inbound_dispatch_metadata`: `agent_id`, `connection_id`, `channel="sip_in"`) is what it resolves, it creates the session when the dispatch carries none, and it reports the leg through `POST /internal/v1/telephony/calls/report` from the `sip.*` participant attributes. The api's `participant_joined` handler creates the pre-session inbound `calls` row only when `sip.trunkID` matches one of the workspace's trunks (`webhooks._is_our_trunk`), because the project may carry other apps' SIP traffic. **Unverified until the live check:** what `sip.trunkID` and `sip.trunkPhoneNumber` hold on a hosted-number leg (LiveKit may set an internal trunk id, or none).

**Unverified until the live check, and the design plans for either outcome:** (a) the grant the service wants — the SIP service signs with `SIPGrants(admin=True)`; the shim starts with that and the check records what worked; (b) how to *detach* a number — `UpdatePhoneNumber{sip_dispatch_rule_id: ""}` may clear it or may be rejected; the fallback is "delete the rule", after which the number reads `IN_STATUS_DETACHED`; (c) whether a rule attached to a number can be deleted while attached (order chosen: detach first, then delete); (d) whether the user's India South project can hold a US number at all (the product is US-only; region limits are not documented in what we have); (e) that LiveKit Cloud's Twirp endpoint accepts `Content-Type: application/json` for this service — standard Go Twirp servers do, and it is the load-bearing assumption of D-V4-16; step 0 of the live check settles it (`lk number list --curl` redirected to a file, reading only the path, the body and the content type, never the header), and if the endpoint is protobuf-only the fallback is the vendored `_pb2` option D-V4-16 rejects, generated from the pinned proto commit.

## 2. Decisions

### D-V4-14 — A number has a source: `trunk` or `livekit`
`phone_numbers.source ∈ {"trunk", "livekit"}` (default `trunk` for every existing row). A `trunk` number is what V2-17 built: typed in, bound to a `SipTrunk`, routed through a trunk rule. A `livekit` number is a LiveKit-hosted number mirrored from the project; it has a `connection_id`, an `lk_number_id`, no trunk, and is routed through a **trunk-less** rule. One table, one `PhoneNumberOut`, one Numbers section; the source decides which fields are editable. Rejected: a second table (`livekit_numbers`) — the console, the MCP overview and the webhook lookup would all need two paths for what is one concept, "a number that reaches an agent".

### D-V4-15 — LiveKit is the source of truth for hosted numbers; LKAP mirrors on demand and never buys or releases
`POST /v1/telephony/numbers/refresh` lists the project's numbers and upserts the mirror. LKAP never calls `SearchPhoneNumbers`, `PurchasePhoneNumber` or `ReleasePhoneNumbers`. The user buys in the LiveKit dashboard or with `lk number purchase`, then presses **Refresh from LiveKit**. Deleting a `livekit` number in LKAP detaches it and drops the mirror row; the number stays in the project. Reason: purchase and release are billing actions in the user's LiveKit account, the API exposes no price to confirm against (§1), and the platform's standing rule is that purchases are the user's action. Rejected: a periodic background sync — nothing consumes it between two console visits, and it would add a LiveKit call per connection per interval for no user-visible benefit; the console and the MCP overview show `lk_synced_at` so staleness is visible.

### D-V4-16 — The api talks Twirp-JSON to `PhoneNumberService` itself, in one module, until `livekit-api` ships the service
`api/src/lkap_api/telephony/phone_numbers.py` owns a small `PhoneNumberClient`: it POSTs proto3-JSON to `<https origin>/twirp/livekit.PhoneNumberService/<Method>` with `Content-Type: application/json`, through the same `net_guard`-guarded aiohttp session and url check `ConnectionClientFactory.api` uses (the `wss://` → `https://` conversion the SDK applies), and signs with `AccessToken(api_key, api_secret).with_sip_grants(SIPGrants(admin=True)).to_jwt()` from the decrypted credentials the factory already holds. It parses both the lowerCamelCase keys Twirp-JSON emits (`e164Format`, `sipDispatchRuleIds`) and the snake_case originals, and maps Twirp errors like `common.raise_upstream` does (`invalid_argument`/`not_found` → 422 or 404 with the LiveKit code; `unauthenticated`/`permission_denied` → 409 `phone_numbers_unavailable` naming the grant; anything else → 502 `livekit_error`). Three methods only: `list()`, `get(id)`, `update(id, sip_dispatch_rule_id=…)`. Rejected: vendoring `_pb2` files generated from the proto — a `grpcio-tools` build step, a pinned proto copy in the repo and a second Twirp client to maintain, for three calls. **Removal condition:** when `livekit-api` gains a phone-number service (`hasattr(LiveKitAPI, "phone_number")` or similar), the shim is replaced by SDK calls and the JSON codec deleted; `test_phone_numbers.py` carries one test that fails when the installed SDK grows that attribute, so the replacement is not forgotten.

### D-V4-17 — Assigning an agent creates a trunk-less managed rule, attaches it, and the rule row points at the number
`sip_dispatch_rules.trunk_id` becomes nullable and the table gains `phone_number_id` (FK, nullable, `ondelete=SET NULL`). `_create_rule_on_livekit(factory, conn, trunk | None, rule)` builds, for `trunk is None`, `SIPDispatchRuleInfo(rule=SIPDispatchRule(dispatch_rule_individual=…(room_prefix=MANAGED_ROOM_PREFIX)), trunk_ids=[], numbers=[e164], name="lkap:<rule.id>", metadata=…, room_config=RoomConfiguration(agents=[RoomAgentDispatch(agent_name=conn.agent_name, metadata=inbound_dispatch_metadata(agent_id, conn.id))]))` — the same room config as today, so the worker sees the same dispatch. `numbers=[e164]` is **mandatory** on a trunk-less rule: with `trunk_ids=[]` LiveKit matches every trunk in the project, and only the called-number filter keeps the rule from answering the user's own Twilio/Telnyx trunks or another app's traffic. Then `UpdatePhoneNumber{id: lk_number_id, sip_dispatch_rule_id: <lk_rule_id>}` attaches it. The managed-rule lookup (`_managed_rule_in`) stops matching on `(trunk_id, numbers == [e164])` and uses `phone_number_id`; a backfill in the migration sets it for existing trunk-managed rules where the old match holds. Rejected: attaching the number to a catch-all rule (`numbers=[]`) — it would swallow trunk calls; and reusing a trunk rule — there is no trunk.

### D-V4-18 — Sessions, calls and caller attributes need no worker change; the api's "our leg" test gains a number clause
The worker path is untouched: metadata → `channel=sip_in` → session created on dispatch → `report_call` with `from`/`to`/`call_id`/`trunk_id` from the `sip.*` attributes → `apply_report` creates or links the `calls` row. The only api change is in `telephony/webhooks.py`: `_is_our_trunk(ctx, lk_trunk_id)` becomes `_is_our_leg(ctx, attributes)` = the existing trunk match **or** `sip.trunkPhoneNumber` equals the `e164` of a `source="livekit"` row of this workspace and connection. Without it the pre-session `participant_joined` (which usually beats the worker's session) would skip the orphan row and the call would exist only from the worker's report; with it, both paths produce one row, linked by `sip.callID` as today. `sessions.caller.trunk_id` holds whatever LiveKit sets on a hosted leg (possibly empty or an internal id); it is informational and the console shows "LiveKit Cloud number" from the number row, not from that field.

### D-V4-19 — Inbound only: the dialing policy does not apply, and a hosted number is never a caller id
LiveKit numbers are inbound-only today. `outbound_status` is stored on the mirror row for display and ignored. `call_place` and `prepare_outbound_call` keep requiring an outbound trunk; a `livekit` number is never offered as `from`, and the workspace dialing policy (R-V2-23, R-V2-28/29) is not consulted for it because no outbound leg can originate from it. When LiveKit adds outbound on hosted numbers, that is a new decision with the policy in front of it, not a flag flip.

### D-V4-20 — Console: the Numbers section only, dialogs only
No new page and no new section. `numbers-section.tsx` gains: a source chip per row (`LiveKit` / trunk name), a **Refresh from LiveKit** button (with a connection picker when the workspace has more than one SIP-capable connection), an attach-state chip, a **Get a number** `Dialog` with the dashboard and `lk number purchase` steps, and the existing inbound-agent picker working for both sources. The "Add number" dialog stays for trunk numbers. `rules-section.tsx` tolerates `trunk_id: null` (shows the number instead of a trunk). R-V3-2: dialogs, never drawers.

### D-V4-21 — MCP: read-only pass-through, no new tool
`telephony_overview` already proxies `GET /v1/telephony/numbers`, so the new fields flow through unchanged; it adds one warning line per `livekit` number whose `attach_state != "routed"` and a `livekit_numbers` count. No refresh or assignment tool: R-V3-7 ("trunk, number and dispatch-rule writes stay console-only") stands, and `api_request` keeps refusing `/v1/telephony` writes.

### D-V4-22 — Purchase is never in-platform in v1, never via MCP, and only becomes a v5 candidate under three conditions
Not in v1 (D-V4-15). It may be reconsidered only when (1) `livekit-api` ships the service (D-V4-16's removal condition), (2) the API returns a price or the plan's included quota in the search/purchase response so a dialog can show what will be charged, and (3) the dialog takes a typed confirmation (the number itself) in the console. It is never an MCP tool, whatever the scopes (R-V3-7's rule that the policy and money-shaped actions are not tools). Until then the **Get a number** dialog carries the instructions and a `lk number purchase --country-code US --sip-dispatch-rule-id <id>` line only as text the user runs themselves.

## 3. Contracts (`lkap_contracts.api_models`)

```python
NumberSource = Literal["trunk", "livekit"]
LkNumberStatus = Literal["active", "pending", "released", "offline", "unknown"]
LkInboundStatus = Literal["active", "unavailable", "detached", "unknown"]
AttachState = Literal["routed", "detached", "not_routed", "pending", "offline", "released"]

class PhoneNumberOut(BaseModel):
    id: str
    e164: str
    source: NumberSource = "trunk"
    trunk_id: str | None = None
    connection_id: str | None = None          # livekit rows: the project's connection
    inbound_agent_id: str | None = None
    label: str = ""
    dispatch_rule_id: str | None = None       # the managed LKAP rule (both sources)
    lk_number_id: str | None = None           # livekit rows only
    lk_status: LkNumberStatus | None = None
    lk_inbound_status: LkInboundStatus | None = None
    lk_rule_ids: list[str] = []               # the number's sip_dispatch_rule_ids at last refresh
    attach_state: AttachState = "not_routed"  # derived, §4.4
    region: str = ""                          # "San Francisco, CA" for the row's subtitle
    lk_synced_at: datetime | None = None

class PhoneNumberUpdate(BaseModel):           # unchanged fields; the api refuses trunk_id on a livekit row
    trunk_id: str | None = None
    inbound_agent_id: str | None = None
    label: str | None = None

class NumbersRefreshIn(BaseModel):
    connection_id: str | None = None          # default: every SIP-capable connection of the workspace

class NumbersRefreshOut(BaseModel):
    connection_id: str
    seen: int; added: int; updated: int; released: int
    conflicts: list[str] = []                 # e164s that exist as trunk numbers (409 per number, not per call)
    warnings: list[str] = []                  # e.g. "3 numbers offline"

class DispatchRuleOut(BaseModel):             # trunk_id becomes optional; phone_number_id added
    trunk_id: str | None = None
    phone_number_id: str | None = None
    ...
```

`PhoneNumberCreate` is unchanged and refuses `source` (it always creates a `trunk` number; a `livekit` row only comes from refresh). The `.d.ts` is regenerated by the contracts gate; the console never hand-edits it.

## 4. Api

### 4.1 Schema and migration `v4_001_livekit_numbers`
`phone_numbers` += `source String(16) NOT NULL DEFAULT 'trunk'` (check `IN ('trunk','livekit')`), `connection_id String(32) NULL FK livekit_connections ON DELETE CASCADE`, `lk_number_id String(128) NULL` (unique with `workspace_id`), `lk_status String(16) NULL`, `lk_inbound_status String(16) NULL`, `lk_rule_ids JSON NOT NULL DEFAULT []`, `region String(200) NOT NULL DEFAULT ''`, `lk_synced_at UtcDateTime NULL`. `sip_dispatch_rules.trunk_id` → nullable; += `phone_number_id String(32) NULL FK phone_numbers ON DELETE SET NULL`. Backfill: for every rule whose `(workspace_id, trunk_id, numbers)` equals a number's `(workspace_id, trunk_id, [e164])` and that number has `inbound_agent_id == rule.agent_id`, set `phone_number_id`. `op.batch_alter_table` for SQLite; `downgrade()` drops the new columns and restores `NOT NULL` on `trunk_id` (rows with a null trunk are deleted first in the downgrade, and it says so). HANDOFF rule 4: rehearsed on a copy of `api/data/lkap.db` (`upgrade head` → `downgrade v3_001_agent_keys` → `upgrade head`) and on the Postgres CI job; **the coordinator applies it after a `.backup`** (R-V4-11 pre-authorises it).

### 4.2 `telephony/phone_numbers.py` (the shim, D-V4-16)
`class PhoneNumberClient` with `async list(statuses=("active","pending","offline")) -> list[LkPhoneNumber]` (follows `next_page_token` until empty; `limit` 50), `async get(id) -> LkPhoneNumber`, `async update(id, *, sip_dispatch_rule_id: str | None) -> LkPhoneNumber` (`None` means "send an empty string", the detach attempt of §1(b)). `LkPhoneNumber` is a Pydantic model of the fields in §1 with enum values lowered to the contract literals (`PHONE_NUMBER_STATUS_ACTIVE` → `active`; unknown → `unknown`). `ConnectionClientFactory.phone_numbers(row)` yields one like `.api(row)` does. `require_sip(conn)` runs before every call: hosted numbers are a SIP-service feature and the capability probe is the cheapest "this connection cannot" answer.

### 4.3 Service functions (`telephony/service.py`)
- `refresh_numbers(db, factory, workspace_id, connection_id | None) -> list[NumbersRefreshOut]`: for each target connection, `client.list()`; for each `LkPhoneNumber`: find the row by `(workspace_id, lk_number_id)`; if missing, find by `e164` across workspaces (`e164` is globally unique) — a `trunk` row or another workspace's row is a **conflict** (recorded, row skipped); else insert `source="livekit"`; update `lk_status`, `lk_inbound_status`, `lk_rule_ids`, `region`, `label` (LiveKit's `name` when the LKAP label is empty), `lk_synced_at`. Rows of that connection not in the list → `lk_status="released"` (kept, so the console can say what happened). Never touches `inbound_agent_id` or rules.
- `assign_number(db, factory, workspace_id, number, agent_id)` (called by `update_number` when `inbound_agent_id` changes on a `livekit` row): `get_agent`, `resolve_agent_connection(agent)` must be the number's `connection_id` (else 422 "the agent's connection is not the number's project"); the **conflict pre-check**: `lk.sip.list_dispatch_rule()` and warn (not refuse) when any existing rule has `numbers` containing the e164 or has both `trunk_ids=[]` and `numbers=[]` (a project catch-all; LiveKit will refuse ours and the 422 relay names it); drop the previous managed rule (detach first, then delete); `_new_rule(trunk=None, phone_number=number, numbers=[e164])`; `client.update(lk_number_id, sip_dispatch_rule_id=rule.lk_rule_id)`; store the returned `sip_dispatch_rule_ids` in `lk_rule_ids`.
- `unassign_number(...)` (`inbound_agent_id: null`): `client.update(lk_number_id, sip_dispatch_rule_id=None)` — if LiveKit rejects the empty value (§1 b), log at INFO and continue; `_delete_rule_everywhere(rule)`; then `client.get(lk_number_id)` to store the fresh `lk_rule_ids`/`lk_inbound_status`.
- `update_number`: on a `livekit` row, `trunk_id` present in the body → 422 "a LiveKit-hosted number has no trunk"; `label` edits stay local (LiveKit's `name` is not written in v1).
- `delete_number`: on a `livekit` row, `unassign_number` when routed, then delete the LKAP row. Never `ReleasePhoneNumbers`.
- `create_number`: unchanged; a body whose `e164` matches a `livekit` row anywhere → the existing 409.

### 4.4 `attach_state` (derived in `number_out`)
`released` if `lk_status == "released"`; `offline` if `lk_status == "offline"`; `pending` if `lk_status == "pending"`; else, for a `livekit` row: `routed` if the managed rule exists and its `lk_rule_id ∈ lk_rule_ids`; `detached` if the managed rule exists but is not in `lk_rule_ids` (someone changed it in the dashboard, or the attach failed after the rule was created); `not_routed` otherwise. For a `trunk` row: `routed` if the managed rule exists, else `not_routed`.

### 4.5 Routes (`routers/telephony.py`, all `AdminCtxDep`, `require("admin")` per the existing `/v1/telephony` policy row)
- `POST /v1/telephony/numbers/refresh` → 200 `list[NumbersRefreshOut]`; 409 `sip_disabled` from `require_sip`; 409 `phone_numbers_unavailable` when the service refuses the grant (the message says which grant was tried); 502 on transport failure. Audit action `telephony.numbers.refresh`.
- `GET /v1/telephony/numbers`: unchanged path, new fields.
- `PUT /v1/telephony/numbers/{id}`: the rules of §4.3.
- `DELETE /v1/telephony/numbers/{id}`: description updated ("a LiveKit-hosted number is detached and forgotten here; it stays in your LiveKit project").
- `POST /v1/telephony/dispatch-rules` (manual rules): unchanged, still needs `trunk_id`; a trunk-less rule exists only as a number's managed rule.

## 5. Calls, sessions and caller attributes (D-V4-18)
- Worker: no change. The dispatch metadata is identical to a trunk rule's; `channel=sip_in`; the session is created on dispatch; `report_call` fills `caller` and the `calls` row.
- Api webhooks: `_is_our_leg` (§2, D-V4-18). `caller_from_attributes` unchanged. `_session`'s connection check unchanged (the number's `connection_id` is the webhook's connection).
- `calls.direction="inbound"`, `to_e164` = the hosted number, `from_e164` = the caller: identical to a trunk call in the Calls section and in `call_list`.
- Cost: LiveKit's 50 inbound minutes are the project's quota, not visible to LKAP; the RUNBOOK line says so. The `usage` rollup (R-V2 recordings-and-cost) is unchanged.

## 6. Console (D-V4-20)
- **Numbers section rows.** Number (+ `region` subtitle for `livekit` rows), Source chip (`LiveKit` in `info` tone; else the trunk name), Inbound agent picker (both sources; for a `livekit` row the picker lists only agents whose connection is the number's), Routing chip from `attach_state` (`Routed` success / `Detached` warning with a **Re-attach** button that re-runs assignment / `Not routed` neutral / `Pending` info / `Offline` warning / `Released` muted), a delete button (confirm copy per source), and "Synced <RelativeTime>" for `livekit` rows.
- **Header actions.** "Add number" (trunk, unchanged), **Refresh from LiveKit** (disabled with the SIP hint when no connection reports SIP; a `NativeSelect` of connections when more than one qualifies; toast with `added/updated/released` and one line per conflict), **Get a number** opens a `Dialog`: three numbered steps — (1) buy in the LiveKit dashboard's Telephony → Phone numbers page or run `lk number purchase --country-code US` (copyable, no key in it); (2) press Refresh here; (3) pick the inbound agent. A footnote: "US numbers only, inbound only; the Build plan includes one number and 50 inbound minutes" — the copy names the dashboard page, never a URL, and the console test pins it (the MCP doc-lint's hostname rule applies only to `mcp/src/lkap_mcp/docs/**`; the same restraint is kept here by convention).
- **Empty state** when the workspace has no numbers: both buttons plus the same three steps.
- **Rules section.** A rule with `trunk_id: null` shows "LiveKit number <e164>" in the Trunk column and `managed_by_number`; the create-rule dialog is unchanged (trunk rules only).
- **Telephony page banner**: unchanged (SIP warning). No new page, no drawer.

## 7. MCP (D-V4-21)
`telephony_overview` returns the numbers with the new fields; `warnings` gains `"number <e164>: detached"` / `"offline"` lines; `data["livekit_numbers"]` = count of `source == "livekit"`. `docs/concepts/telephony.md` gets one paragraph ("LiveKit-hosted numbers: no trunk; assign an inbound agent in the console; the overview shows `attach_state`"); the doc lint's field check covers the new keys. No new tool, no snapshot change beyond the `TelephonyOverview` data description if it changes.

## 8. Tests (offline, `api/tests/test_phone_numbers.py`, the `FakeLiveKit` Twirp fake from `connection_fakes.py` extended with `/twirp/livekit.PhoneNumberService/*` routes answering JSON)
- Shim: `list()` follows two pages; enum lowering; camelCase and snake_case bodies both parse; `unauthenticated` → 409 `phone_numbers_unavailable`; 500 → 502; the "SDK grew the service" sentinel test.
- Refresh: inserts `livekit` rows with `connection_id`, `lk_number_id`, `region`; second run is idempotent (`added=0`); a number missing from the list becomes `released`; an `e164` already held as a `trunk` number is reported in `conflicts` and not inserted; `sip_disabled` when the connection reports no SIP.
- Assignment: the fake records `CreateSIPDispatchRule` with `trunk_ids == []`, `numbers == [e164]`, `room_config.agents[0].agent_name == conn.agent_name` and the metadata `{"agent_id", "connection_id", "channel": "sip_in"}`; then `UpdatePhoneNumber{id, sipDispatchRuleId}`; the row's `dispatch_rule_id` and `attach_state == "routed"`; the agent on another connection → 422; the pre-check warning appears when the fake holds a catch-all rule; LiveKit's conflict error → 422 relay and no rule row left behind.
- Unassign and delete: detach called before delete; the fake rejecting the empty rule id does not fail the request; the LKAP row is gone and no `ReleasePhoneNumbers` call was made (the fake 500s on it to prove it).
- Webhooks: a `participant_joined` with an unknown `sip.trunkID` but `sip.trunkPhoneNumber` equal to a `livekit` row's e164 creates the inbound `calls` row; the same with a number of another workspace does not.
- Contracts: `PhoneNumberOut` defaults keep every existing console fixture valid (`source="trunk"`); `DispatchRuleOut.trunk_id` optional.
- Console (`web/tests/console-telephony-numbers.test.tsx`): the chips per `attach_state`; Refresh posts to `/numbers/refresh` and renders the toast; Re-attach posts the current `inbound_agent_id`; the Get-a-number dialog renders the three steps and no `lkap_`/key literal; a `livekit` row's picker excludes agents of other connections; axe on the section and both dialogs; no `@/components/ui/sheet` import.

## 9. Live check (the user's project; recorded in `docs/v4/_briefs/v4-05-live.md`)
Preconditions: the user bought a number (`lk number list` shows it `active`), the dev api runs the migration, the user's `lkap-agent` worker serves the default connection. Steps: (0) before any code: `lk number list --curl > <scratchpad>/v405/curl.txt`, read the Twirp path, the request body and the content type from it (never the `Authorization` line; the file is deleted afterwards) and confirm the JSON transport of §1(e) — if the endpoint is protobuf-only, stop and file the ask before building the shim; (1) Refresh → the row appears with `region` and `lk_inbound_status=detached`; (2) assign the `Demo — Phone agent` from V4-06 (or any published agent) → LiveKit shows the rule (`lk sip dispatch list`) with no trunk and the number's `sip_dispatch_rule_ids` containing it; record which grant worked (§1 a); (3) the user calls the number from a phone → a `sip_in` session, one `calls` row with `to_e164` = the number, `caller.from` = the caller, the Calls section shows it, `call_list` shows it; record what `sip.trunkID` and `sip.trunkPhoneNumber` carried (§1); (4) unassign → record whether the empty `sip_dispatch_rule_id` cleared the attachment or the rule delete alone did (§1 b, c); (5) re-assign, then change the rule in the dashboard and Refresh → `Detached`, Re-attach → `Routed`; (6) delete the number in LKAP → the row is gone, `lk number list` still shows the number. Minutes used are the user's; keep the test call under two minutes.

## 10. Out of scope, recorded
Outbound from hosted numbers (D-V4-19); purchase and release (D-V4-22); writing LiveKit's `name` from the LKAP label; a background sync (D-V4-15); SMS (the `capabilities[]` field is stored, unused); multiple numbers on one rule (LKAP keeps one managed rule per number); Phase 2 backlog: fold the shim away when the SDK ships the service (D-V4-16).
