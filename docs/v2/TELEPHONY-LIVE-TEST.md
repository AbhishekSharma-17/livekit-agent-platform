# Telephony live test (stage L12b, V2-17)

V2-17 was built and unit-tested with LiveKit mocked at the boundary: there is no SIP trunk yet, and nothing was created on the LiveKit Cloud project. Run this procedure once you have a carrier trunk and a phone number. It proves PLAN-V2 stage **L12b**:

- an inbound call reaches the agent, and a session with `channel=sip_in` appears;
- an outbound call from the console rings your phone;
- DTMF digits are logged;
- a cold transfer reaches a second number.

Record the results in `docs/v2/LIVE-RESULTS.md` (V2-20).

## 0. What you need

| Item | Notes |
|---|---|
| A SIP trunk at a carrier | Twilio Elastic SIP Trunking or Telnyx both work. Anything that speaks SIP to LiveKit works too. |
| One phone number on that trunk (**number A**) | This is the number the agent answers. It is also the caller ID for outbound calls. |
| Two phones you can answer | **Phone 1** is you. **Phone 2** is the transfer target (a colleague, or a second SIM). |
| The LiveKit Cloud project's **SIP URI** | Find it under LiveKit Cloud → Project settings → SIP (`sip:<id>.sip.livekit.cloud`). |
| The connection in LKAP shows `SIP` | Console → Connections → the connection → **Test**. The capability chips must include SIP (`sip_enabled=true`). It was probed true on 2026-09-23. |
| The worker wiring asks applied | `docs/v2/_asks.md` **V2-17-2**. Without it, inbound and outbound calls still reach the agent, but four things are missing: the worker's caller/status reports, keypad (DTMF) handling, the `send_dtmf`/`transfer_call` tools, and the outbound "wait for answer" (the agent may greet while the phone is still ringing). |
| Webhooks (recommended) | LiveKit Cloud → Settings → Webhooks → `https://<LKAP_PUBLIC_BASE_URL>/hooks/livekit/<connection_id>`, signed with the connection's API key. In dev, expose `:8080` with a tunnel. Without webhooks the calls log relies only on the worker's reports (V2-17-2) and the dial result. |

Safety:

- Never touch `other-project-agent`.
- Every rule LKAP creates is scoped to LKAP's own trunk (`trunk_ids`), so it cannot capture another app's calls on the shared project.
- Start exactly one `lkap-agent` worker per connection.
- Keep calls short. The Google key is free-tier.

## 1. Carrier side

Follow your carrier's docs; the values below are the ones LKAP needs.

- **Inbound (carrier → LiveKit).** Point the trunk's origination / inbound URI at the project's SIP URI. On Twilio this is Elastic SIP Trunk → Origination → `sip:<id>.sip.livekit.cloud`. Assign number A to the trunk.
- **Outbound (LiveKit → carrier).** Note the trunk's termination SIP address (Twilio: `<name>.pstn.twilio.com`). Create a credential (username and password) for it.
- **Transfers.** Allow SIP REFER / call transfer on the trunk. On Twilio, enable "Call Transfer (SIP REFER)" and allow transfers to the PSTN. Without this, step 6 fails with a SIP 403 or 405.

## 2. Create the LKAP objects (Console → Telephony)

1. **Add trunk → Inbound**:
   - Connection: the Cloud connection.
   - Name: `carrier-in`.
   - Numbers: number A.
   - Allowed source address: empty, or your carrier's signalling IP range.
   - Username/Password: only if your carrier authenticates to LiveKit.
   - Check: the row shows a `ST_…` id under "LiveKit". `lk sip inbound list` (read-only) lists the same trunk, with number A.
2. **Add trunk → Outbound**:
   - Name: `carrier-out`.
   - Numbers: number A (the caller ID).
   - SIP address: the termination address.
   - Username/Password: the credential from step 1.
   - Check: `lk sip outbound list` shows the address and number.
3. **Add number** number A:
   - Trunk: `carrier-in`.
   - Inbound agent: a published test agent on this connection.
   - Check: Numbers shows **Routed**, and Dispatch rules shows a rule with "From number" and called number A.
   - Check `lk sip dispatch list`. The rule must have `numbers: [A]` (the **called**-number filter), `trunk_ids: [ST_…]`, and `room_config.agents[0].agent_name` equal to the connection's agent name.
   - Its metadata must be `{"v":2,"session_id":null,"agent_id":"…","config_version":0,"participant_identity":"","channel":"sip_in",…}`.
   - If `lk` prints the filter as `inbound_numbers` instead, stop. That field is the *caller* filter, so the rule would only accept calls *from* A. Report it.
4. On the test agent, set a transfer allowlist and turn keypad input on:
   - In `pack_settings`, add `"transfer_targets": {"Phone 2": "+1…"}` (with PUT `/v1/agents/{id}` until the editor exposes it; see asks V2-17-8).
   - Turn on `capabilities.dtmf`.

## 3. Inbound call (L12b.1)

Call number A from phone 1. The agent should greet you. Then check:

- **Sessions.** A new row with `channel = sip_in`. The room is named like `call-_+1…_xxxx`.
  - `caller` is filled in: `{"direction":"inbound","from":"<phone 1>","to":"<A>","trunk_id":"ST_…","call_id":"SCL_…"}`.
  - With neither webhooks nor V2-17-2, `caller` stays empty. That is the known gap.
- **Telephony → Calls.** One inbound row, `In call`, from phone 1 to A.
  - After you hang up, it shows `Completed` with a duration.
  - The session's timeline has `sip_answered`.
- **Worker log.** It shows `telephony started channel=sip_in`.

## 4. DTMF (L12b.3)

**DTMF in:**

1. During an inbound call, press `1 2 #` on phone 1.
2. The session timeline gets `dtmf {direction: received, digits: "12#"}`.
3. The agent answers the keypad entry. It arrives as a user turn when `capabilities.dtmf` is on and no pack `on_dtmf` hook consumed it.
4. Wait 2.5 s without pressing `#`. The digits so far are sent as one entry.

**DTMF out, from the console:**

1. While a call is `In call`, open Telephony → Calls → the call's keypad button.
2. Send `5`. Phone 1 hears the tone.
3. The timeline gets `dtmf {direction: sent, source: console}`.
4. If nothing plays, check the worker log for `ignored dtmf control packet`.

**DTMF out, from the agent:** ask it to "press 3". The `send_dtmf` tool records `source: tool`.

## 5. Outbound call (L12b.2)

1. Open the agent editor → Test call ▾ → **Call a number…**.
2. Enter phone 1 and press Call. The dialog shows `Dialing`, then `Ringing` (webhook), then `In call` when you pick up.
3. Check:
   - Phone 1 shows number A as the caller ID.
   - The agent speaks **after** you answer, not while it rings. This needs V2-17-2's `wait_for_answer`.
   - Sessions has a row with `channel = sip_out`.
   - `GET /v1/calls/{id}` returns `answered` and a `sip_call_id`.
4. Hang up from the dialog. The call shows `Completed` and the agent leaves.
5. Repeat, and this time decline on phone 1. The call shows `Busy` (SIP 486/600) or `No answer` (408/480/487), and the agent is dismissed (its room is deleted).

## 6. Cold transfer (L12b.4)

Try the transfer two ways.

**From the console:**

1. During an answered call, open Calls → Transfer, enter phone 2 and send.
2. Phone 2 rings. Phone 1 is connected to it and the agent leaves.
3. The call row shows `Transferred`, with "to +1…".
4. The api sends `transfer_to = "tel:+1…"`. If the carrier rejects `tel:` URIs, retry with `sip:+1…@<carrier host>` and note it.

**From the agent:** tell it "transfer me to Phone 2".

1. The agent says "Please hold while I transfer your call."
2. The `transfer_call` tool calls `POST /internal/v1/telephony/sessions/{id}/transfer`.
3. The timeline gets `transfer {ok: true}` and the job ends.
4. Then check the refusal: ask for a number that is not on the allowlist. The model is told the destination is unknown, and nothing is dialled.

## 7. Clean up (optional)

Telephony → delete the number, then both trunks. This deletes the LiveKit rule and trunks too. Confirm with `lk sip dispatch list` / `lk sip inbound list` / `lk sip outbound list`.

## What each check exercises

| Check | Code |
|---|---|
| Trunk ids and the rule's shape on LiveKit | `api/src/lkap_api/telephony/service.py` |
| `sip_in` session and `caller` | Dispatch-rule metadata; worker `telephony.TelephonySession` → `POST /internal/v1/telephony/calls/report`; webhook `participant_joined` |
| Outbound dial and status | `telephony/calls.py::run_dial` (dispatch first, `wait_until_answered`, `failover=False`) |
| Status transitions | `telephony/calls.py::advance` (forward-only), fed by webhooks, the dial result and worker reports |
| DTMF | Worker `DtmfCollector` / `sip_dtmf_received`; console → `RoomService.SendData` on topic `lkap.telephony.dtmf` → `publish_dtmf` |
| Transfer | `SipService.transfer_sip_participant` (SIP REFER) via the api, from the console or the worker tool |
