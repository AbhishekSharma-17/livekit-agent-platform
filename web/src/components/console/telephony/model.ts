import type { AgentOut, CallOut, ConnectionOut } from "@/contracts/lkap-contracts";

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
