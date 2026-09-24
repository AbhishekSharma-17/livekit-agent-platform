import type { AgentOut, CallOut, ConnectionOut, PhoneNumberOut } from "@/contracts/lkap-contracts";

/** Call status → chip label and tone (`StatusChip` tones). */
export type CallStatus = NonNullable<CallOut["status"]>;
export type ChipTone = "neutral" | "info" | "success" | "warning" | "danger" | "live";

export const CALL_STATUS_META: Record<CallStatus, { label: string; tone: ChipTone }> = {
  dialing: { label: "Dialing", tone: "info" },
  ringing: { label: "Ringing", tone: "info" },
  answered: { label: "In call", tone: "live" },
  completed: { label: "Completed", tone: "success" },
  transferred: { label: "Transferred", tone: "success" },
  no_answer: { label: "No answer", tone: "warning" },
  busy: { label: "Busy", tone: "warning" },
  failed: { label: "Failed", tone: "danger" },
};

export function callStatusMeta(status: CallOut["status"]) {
  return CALL_STATUS_META[status ?? "dialing"];
}

/** Whether a call can still be hung up / transferred / sent DTMF. */
export function isLiveCall(call: Pick<CallOut, "status">): boolean {
  return call.status === "answered";
}

export function isOpenCall(call: Pick<CallOut, "status">): boolean {
  const status = call.status ?? "dialing";
  return status === "dialing" || status === "ringing" || status === "answered";
}

/** A connection reports SIP after its capability probe (`Test connection`). */
export function sipEnabled(connection: ConnectionOut | undefined): boolean {
  return Boolean(connection?.capabilities?.sip_enabled);
}

/** The connection an agent's calls run on: its own, else the workspace default. */
export function connectionForAgent(
  agent: Pick<AgentOut, "connection_id">,
  connections: ConnectionOut[],
): ConnectionOut | undefined {
  return (
    connections.find((c) => c.id === agent.connection_id) ?? connections.find((c) => c.is_default) ?? connections[0]
  );
}

/** Numbers as the api stores them: strip spaces, dashes and dots from what people paste. */
export function normalizeE164(value: string): string {
  return value.replace(/[\s().-]/g, "");
}

export function splitNumbers(value: string): string[] {
  return value
    .split(/[,\n]/)
    .map((part) => normalizeE164(part))
    .filter(Boolean);
}

/**
 * Value patterns, mirroring `lkap_contracts.telephony` (R-V2-21; the generated
 * `.d.ts` carries types, not regexes). The api re-checks every value.
 */
/** E.164: `+`, a non-zero country digit, 7–15 digits. */
export const E164_PATTERN = /^\+[1-9]\d{6,14}$/;

/** Keys `POST /v1/calls/{id}/dtmf` accepts. */
export const DTMF_PATTERN = /^[0-9*#A-D]{1,32}$/;

/** Transfer targets: E.164 or a `tel:` / `sip:` / `sips:` URI. */
export const TRANSFER_TARGET_PATTERN = /^(\+[1-9]\d{6,14}|tel:\+?[0-9]{3,20}|sips?:[^\s@]+@\S+)$/;

/** Trunk direction and carrier hint (`TrunkOut.direction` / `provider_hint`). */
export type TrunkDirection = "inbound" | "outbound";
export type ProviderHint = "twilio" | "telnyx" | "other";

/** Where a number comes from (V4-05): typed in on a trunk, or hosted by LiveKit. */
export function isHostedNumber(number: Pick<PhoneNumberOut, "source">): boolean {
  return number.source === "livekit";
}

/** `PhoneNumberOut.attach_state` (derived by the api, PHONE-NUMBERS.md §4.4). */
export type AttachState = NonNullable<PhoneNumberOut["attach_state"]>;

export const ATTACH_STATE_META: Record<AttachState, { label: string; tone: ChipTone; hint: string }> = {
  routed: { label: "Routed", tone: "success", hint: "Calls to this number reach its inbound agent." },
  detached: {
    label: "Detached",
    tone: "warning",
    hint: "The number is no longer attached to its LKAP dispatch rule in LiveKit. Re-attach to route it again.",
  },
  not_routed: { label: "Not routed", tone: "neutral", hint: "Pick an inbound agent to route calls." },
  pending: { label: "Pending", tone: "info", hint: "LiveKit is still activating this number." },
  offline: { label: "Offline", tone: "warning", hint: "LiveKit reports this number offline." },
  released: { label: "Released", tone: "neutral", hint: "The number is no longer in your LiveKit project." },
};

/** A number's routing state; older payloads without `attach_state` fall back to the rule id. */
export function attachStateOf(number: Pick<PhoneNumberOut, "attach_state" | "dispatch_rule_id">): AttachState {
  return number.attach_state ?? (number.dispatch_rule_id ? "routed" : "not_routed");
}

/** Agents whose calls run on `connectionId` (a hosted number only reaches its own project's workers). */
export function agentsOnConnection(
  agents: AgentOut[],
  connections: ConnectionOut[],
  connectionId: string | null | undefined,
): AgentOut[] {
  if (!connectionId) return [];
  return agents.filter((agent) => connectionForAgent(agent, connections)?.id === connectionId);
}

/** The `lk` CLI line the "Get a number" dialog shows as text; the user runs it (LKAP never buys). */
export const LK_PURCHASE_COMMAND = "lk number purchase --country-code US";
