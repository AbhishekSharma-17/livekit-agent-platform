# Telephony

Telephony is **read by default**; dialing out is behind three gates you
cannot bypass from here. The workspace's dialing policy is never something a
tool can write.

## Reading

`telephony_overview()` returns trunks (`has_password` only, never the
password itself), numbers with their inbound agents, dispatch rules, and the
dialing policy summary: `allowed_prefixes` (E.164 prefixes; empty means no
dialing and no transfer at all), `allowed_sip_hosts` (hosts a non-numeric
`sip:` destination may reach), `max_calls_per_min` and
`max_concurrent_outbound`. `call_list(direction=, status=)` and
`call_get(call_id)` read call history and status (`CallOut`).
`workspace_get()` also echoes the same policy summary.

## LiveKit-hosted numbers

A number bought from LiveKit itself has no trunk: it appears in the overview
with `source` `livekit`, its `lk_status` and a derived `attach_state`
(`routed`, `detached`, `not_routed`, `pending`, `offline` or `released`).
`livekit_numbers` counts them, and every one that is not `routed` adds a
warning line. Buying a number, mirroring it (**Refresh from LiveKit**) and
picking its inbound agent all happen in the console's Telephony page; no
tool buys, refreshes or assigns a number.

## An agent's transfer targets

`AgentConfig.telephony.transfer_targets` (`TransferTarget{label, to}`) are
the only destinations that agent's `transfer_call` tool may hand a caller
to — the model never dials free text, and every `to` must also pass the
workspace policy above. Set it with `agent_update(patch={"telephony":
{"transfer_targets": [{"label": "Claims desk", "to": "+18005550123"}]}}
)`.

## Dialing out

`call_place(agent_id, to_e164, trunk_id=, variables=, confirm=true)` and
`call_control(call_id, action="hangup"|"dtmf"|"transfer", confirm=true)`
only appear in your tool list at all when **every** one of these is true:
the API key has `calls:write`; the MCP process was started with
`LKAP_MCP_ALLOW_DIAL=1`; and the call itself passes `confirm=true`. If
either tool is missing, that is the platform refusing dialing by default,
not a bug — ask the operator to enable it rather than looking for a
workaround (there is none; `api_request` also refuses `/v1/calls` writes
and every `/v1/telephony` write). A destination outside `allowed_prefixes`
comes back as a policy refusal with `details.allowed_prefixes` listing what
would be accepted.

Trunk, number and dispatch-rule creation stay console-only in this version —
there is no tool for them.

## Related tools

`telephony_overview`, `call_list`, `call_get`, `call_place`, `call_control`,
`agent_update`, `workspace_get`.

## Related schemas

`TelephonyConfig`, `TransferTarget`, `CallCreate`, `CallOut`,
`CallTransferIn`, `CallDtmfIn`, `CallDtmfOut`, `TrunkOut`,
`DispatchRuleOut`, `PhoneNumberOut`.
