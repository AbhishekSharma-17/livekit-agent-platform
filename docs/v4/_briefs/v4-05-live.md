# V4-05 live check: LiveKit-hosted numbers (PHONE-NUMBERS.md §9)

Status: **step 0 done by V4-05 on 2026-09-25 (read-only). Steps 1–6 are open** and wait for the coordinator: migrate first, then the demo phone agent (V4-06).

The repo is public, so this brief never records the real number, its LiveKit id, the project host or the locality: `<e164>`, `PN_PPN_…` and `<project>` stand in for them. No `Authorization` header or token is ever copied here.

## Step 0: transport and response shape (done)

The user bought one US number with `lk number purchase`. V4-05 ran `lk number list --curl` (and `--status active --status offline --status pending --limit 2 --curl`, and `lk number get --id … --curl`) into a scratchpad file. It read only the path, the body and the content type from that file, then executed the printed request once to see the raw response. The file was deleted afterwards. No attach, detach, purchase or release call was made.

**Request** (what `lk` 2.16.2 sends):

```
POST https://<project>.livekit.cloud/twirp/livekit.PhoneNumberService/ListPhoneNumbers
Content-Type: application/json
{"statuses":["PHONE_NUMBER_STATUS_ACTIVE","PHONE_NUMBER_STATUS_OFFLINE","PHONE_NUMBER_STATUS_PENDING"],
 "pageToken":{"token":"<base64 of {\"offset\":0,\"limit\":2}>"}}

POST …/twirp/livekit.PhoneNumberService/GetPhoneNumber
{"id":"PN_PPN_…"}
```

**Response** (the one number, redacted):

```json
{
 "items": [{
   "id": "PN_PPN_…", "name": "", "e164_format": "<e164>",
   "country_code": "US", "area_code": "<3 digits>", "number_type": "PHONE_NUMBER_TYPE_LOCAL",
   "locality": "<CITY IN CAPITALS>", "region": "<2-letter state>", "spam_score": 0,
   "created_at": null, "updated_at": null, "capabilities": ["voice"],
   "status": "PHONE_NUMBER_STATUS_OFFLINE",
   "inbound_status": "PHONE_NUMBER_IN_STATUS_UNSPECIFIED",
   "outbound_status": "PHONE_NUMBER_OUT_STATUS_UNSPECIFIED",
   "assigned_at": "2026-09-24T19:39:32.739773Z", "released_at": null,
   "sip_dispatch_rule_id": "", "sip_dispatch_rule_ids": []
 }],
 "next_page_token": null, "total_count": 1, "offline_count": 1
}
```

**Findings, and how the shim follows them:**
- **§1(e) is settled: the endpoint accepts JSON.** The Twirp-JSON shim of D-V4-16 stands, and no `_pb2` fallback is needed.
- **Response keys are the proto names in snake_case** (`e164_format`, `sip_dispatch_rule_ids`), not lowerCamelCase. `lk` itself sends camelCase (`pageToken`). The shim parses both styles, and the test fake answers in snake_case by default.
- **Enums are full proto names** (`PHONE_NUMBER_STATUS_OFFLINE`, `PHONE_NUMBER_IN_STATUS_UNSPECIFIED`, `PHONE_NUMBER_TYPE_LOCAL`). The shim lowers them by stripping the known prefixes, so `TOLL_FREE` stays whole, and anything unmapped (`…_UNSPECIFIED`, an int) becomes `unknown`.
- **Request `statuses` are full enum names; `page_token` is an object `{"token": …}`.** The shim sends `statuses` and `limit` on page 1 and passes `next_page_token` back verbatim as `pageToken`.
- **Unset timestamps are `null`.** The shim drops nulls, so the defaults apply.
- **A freshly bought number that is not attached reads `status=OFFLINE`, `inbound_status=UNSPECIFIED`**, not `IN_STATUS_DETACHED` as §9 step 1 expects. Under §4.4 as written, its `attach_state` is `offline`. The console keeps the inbound-agent picker **enabled** for `offline` rows, since that is the only state a new number has, and only `released` disables it. Whether LiveKit flips the number to `ACTIVE` once a rule is attached is for step 2 to record (ask 16).
- **The console region subtitle comes from the locality in title case plus the region** (for example "San Francisco, CA").

## Preconditions for steps 1–6

- The coordinator has applied `v4_001_livekit_numbers` to the dev DB (`docs/v4/_briefs/migration-rehearsal-v4.md`, "when to apply").
- The `Demo — Phone agent` (V4-06), or any published agent, runs on the default connection.
- The user's `lkap-agent` worker serves that connection.
- The number is still in the project (`lk number list`).

## Steps (coordinator)

| # | Action | Record |
|---|---|---|
| 1 | Console → Telephony → Phone numbers → **Refresh from LiveKit**, or `POST /v1/telephony/numbers/refresh {"connection_id": "<default>"}`. | That the row appears with the **LiveKit** chip, a region and "Synced". The `lk_status`/`lk_inbound_status` it shows (expect `offline`/`unknown`, so the chip reads **Offline**). **Whether `SIPGrants(admin=True)` was accepted (§1 a).** A 409 `phone_numbers_unavailable` means it was not; copy LiveKit's message, never the token. |
| 2 | Pick the phone agent in the number's **Inbound agent** picker. | The toast and any pre-check warnings (the project also holds `other-project-agent`'s rules). `lk sip dispatch list` shows `lkap:<rule id>` with **no trunk** and the number in its numbers. `lk number list` shows the number's dispatch rule id. The number's status after the attach (does it become `ACTIVE`?). The Routing chip. |
| 3 | The user calls the number from a phone for under two minutes. | A `sip_in` session; one `calls` row with `to_e164` equal to the number and `from` equal to the caller; the Calls section and `call_list` show it. **What `sip.trunkID` and `sip.trunkPhoneNumber` carried** (from the session's `caller`, or the webhook log). Whether the pre-session inbound row came from `participant_joined` (`_is_our_leg` number clause) or only from the worker's report. |
| 4 | Set the picker to **Nobody**. | **Whether `UpdatePhoneNumber{sipDispatchRuleId: ""}` cleared the attachment (§1 b)** or was refused (the api logs `phone_number_detach_refused` and continues). **Whether the rule delete succeeded while attached or after the detach (§1 c).** What `lk number list` shows afterwards. |
| 5 | Assign again, then re-point or remove the number's rule in the LiveKit dashboard, then **Refresh**. | That the chip reads **Detached** and **Re-attach** appears; press it and the chip reads **Routed**. |
| 6 | Delete the number in LKAP. | That the row is gone and `lk number list` still shows the number. Nothing was bought or given back. |

A step that fails becomes an ask in `docs/v4/_asks.md` with LiveKit's error text (no token, no url).
