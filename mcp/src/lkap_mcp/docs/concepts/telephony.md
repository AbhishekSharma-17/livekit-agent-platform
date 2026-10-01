# Telephony

Telephony is **read by default**. Dialing out is behind three gates you
cannot bypass from here. The workspace's dialing policy is never something a
tool can write.

## Reading

`telephony_overview()` returns trunks (`has_password` only, never the
password itself), numbers with their inbound agents, dispatch rules, and the
dialing policy summary: `allowed_prefixes` (E.164 prefixes, empty means no
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
picking its inbound agent all happen in the console's Telephony page. No
tool buys, refreshes or assigns a number.

## An agent's transfer targets

`AgentConfig.telephony.transfer_targets` (`TransferTarget{label, to}`) are
the only destinations that agent's `transfer_call` tool may hand a caller
to. The model never dials free text, and every `to` must also pass the
workspace policy above. Set it with `agent_update(patch={"telephony":
{"transfer_targets": [{"label": "Claims desk", "to": "+18005550123"}]}}
)`.

## Transfer modes

Each target has a `mode`: `cold` (the default. The caller is put through
at once and the agent leaves) or `warm` (the agent first calls the person on
a private line and briefs them from the conversation while the caller hears
hold music, then joins them to the call). Warm needs a LiveKit Cloud
connection with exactly one outbound phone line, and the target must pass
the dialing policy when the call starts. Anywhere else the transfer runs
cold and the agent's summary is kept on the call instead of being spoken
(`agent_validate` warns about it). If nobody answers a warm transfer the
caller is back with the agent. `transfer_call` takes an optional `summary`
for the person taking the call. `CallOut.transfer_mode` and
`transfer_summary` record what happened. The panel's `handoff` block shows
the caller where the hand-off stands.

## Voicemail on outbound calls

`AgentConfig.telephony.amd` (`AmdConfig{enabled, on_machine, message,
ivr_detection}`, off by default) makes the agent listen to how an outbound
call is answered before it speaks, using the agent's own speech-to-text and
language model. A person (or an unsure result) hears the usual greeting. A
voicemail greeting either gets `message` read after the beep
(`on_machine="leave_message"`) or is hung up on (`"hangup"`). A full mailbox
is always hung up on. A phone menu is worked through when `ivr_detection`
is on. `CallOut.amd_result` is the verdict (human, machine-vm,
machine-unavailable, machine-ivr or uncertain), a machine also records a
`voicemail` session event, and the workspace webhook `call.voicemail`
fires. It needs a speech-to-text plus language-model pipeline and an
outbound phone line; `agent_validate` warns otherwise.

## Dialing out

`call_place(agent_id, to_e164, trunk_id=, variables=, confirm=true)` and
`call_control(call_id, action="hangup"|"dtmf"|"transfer", confirm=true)`
only appear in your tool list at all when **every** one of these is true:
the API key has `calls:write`, the MCP process was started with
`LKAP_MCP_ALLOW_DIAL=1`, and the call itself passes `confirm=true`. If
either tool is missing, that is the platform refusing dialing by default,
not a bug. Ask the operator to enable it rather than looking for a
workaround (there is none; `api_request` also refuses `/v1/calls` writes
and every `/v1/telephony` write). A destination outside `allowed_prefixes`
comes back as a policy refusal with `details.allowed_prefixes` listing what
would be accepted.

Trunk, number and dispatch-rule creation stay console-only in this version.
There is no tool for them.

## Related tools

`telephony_overview`, `call_list`, `call_get`, `call_place`, `call_control`,
`agent_update`, `workspace_get`.

## Related schemas

`TelephonyConfig`, `TransferTarget`, `AmdConfig`, `CallCreate`, `CallOut`,
`CallTransferIn`, `CallDtmfIn`, `CallDtmfOut`, `TrunkOut`,
`DispatchRuleOut`, `PhoneNumberOut`, `VoicemailEvent`, `TransferEvent`,
`HandoffBlockState`.
