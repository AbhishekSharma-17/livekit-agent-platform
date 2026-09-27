import type { SessionEventOut } from "@/contracts/lkap-contracts";

/**
 * The Live tab's own small feed of what a supervisor did or the agent
 * escalated (`docs/v5/_asks.md` #251: "shows the supervisor events in the
 * timeline" — this tab's timeline, not the historical Timeline tab, since a
 * hidden listener can't reuse the composite panel's `handoff` block wording
 * outside this file and `builtin-event-kinds.ts` isn't in this package's
 * files). Plain words, no raw payload.
 */
export interface LiveTimelineEntry {
  id: number;
  ts: string;
  kind: "whisper" | "escalation";
  text: string;
}

function text(payload: Record<string, unknown>, key: string): string | null {
  const value = payload[key];
  return typeof value === "string" && value.trim().length > 0 ? value.trim() : null;
}

/** `supervisor_whisper`/`escalation` session events, newest first. Everything else is ignored here. */
export function liveTimelineEntries(events: readonly SessionEventOut[]): LiveTimelineEntry[] {
  const entries: LiveTimelineEntry[] = [];
  for (const event of events) {
    if (event.type === "supervisor_whisper") {
      const by = text(event.payload, "by") ?? "A supervisor";
      const body = text(event.payload, "text") ?? "";
      const applied =
        event.payload.applied === "reply" ? "asked the agent to say this now" : "left a note for the agent";
      entries.push({ id: event.id, ts: event.ts, kind: "whisper", text: `${by} ${applied}: “${body}”` });
    } else if (event.type === "escalation") {
      const reason = text(event.payload, "reason");
      entries.push({ id: event.id, ts: event.ts, kind: "escalation", text: reason ? `Escalated: ${reason}` : "Escalated" });
    }
  }
  return entries.sort((a, b) => b.id - a.id);
}
