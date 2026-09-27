import {
  ArrowRightLeftIcon,
  AudioLinesIcon,
  BrainIcon,
  CoinsIcon,
  GaugeIcon,
  GridIcon,
  HashIcon,
  PhoneForwardedIcon,
  ShieldIcon,
  UserXIcon,
} from "lucide-react";

import type { SessionDetailExtension, TimelineEventKind } from "@/components/console/sessions/detail/types";
import { RecordingTab } from "./recording-tab";
import { CostTab } from "./cost-tab";
import { MemoryTab } from "./memory-tab";
import { QaTab } from "./qa-tab";

/**
 * V2-14's session-detail extension (PLAN-V2 V2-14; `../sessions/README.md`
 * "Adding tabs or row kinds without touching WP-7 files"): the Recording,
 * Cost and QA tabs, plus the v2 event kinds CONTRACTS-V2 §3.4 adds
 * (`handoff`, `block_update`, `form_submitted`, `dtmf`, `transfer`,
 * `recording` — note the `recording` *event kind* here is the worker's
 * `agent/src/lkap_agent/main.py::_record_event("recording", ...)` progress
 * marker on the timeline, a different thing from the Recording *tab*, which
 * reads `session.recording` from the detail response).
 *
 * Payload shapes are copied from where each event is actually emitted today
 * (grepped, not guessed): `handoff`/`transfer` from
 * `agent/src/lkap_agent/flow/runtime.py`, `block_update`/`form_submitted`
 * from `agent/src/lkap_agent/ui/channel.py`, `recording` from
 * `agent/src/lkap_agent/main.py`. `dtmf` is not emitted as a session event
 * by anything yet (V2-17, telephony, is still stretch) — its shape below
 * mirrors the RPC payload `telephony/calls.py` already sends
 * (`{op: "dtmf", digits, call_id}`) so the kind is ready the day V2-17 adds
 * the matching `_record_event` call; it renders as "Other" if that key
 * layout turns out different.
 */
const EVENT_KINDS: TimelineEventKind[] = [
  {
    type: "handoff",
    filter: "other",
    tone: "info",
    icon: ArrowRightLeftIcon,
    title: (p) => `Moved to ${String(p.to ?? "next step")}`,
    summary: (p) => (p.reason ? String(p.reason) : p.from ? `from ${String(p.from)}` : null),
  },
  {
    type: "transfer",
    filter: "other",
    tone: "warning",
    icon: PhoneForwardedIcon,
    title: (p) => `Transfer to ${String(p.to ?? "unknown")}`,
    summary: (p) => (p.status ? String(p.status) : p.error ? String(p.error) : null),
  },
  {
    type: "block_update",
    filter: "other",
    icon: GridIcon,
    title: (p) => `Panel block updated (${String(p.op ?? "set")})`,
    summary: (p) => (p.block_type ? String(p.block_type) : null),
    collapse: true,
  },
  {
    type: "form_submitted",
    filter: "other",
    tone: "success",
    icon: GridIcon,
    title: () => "Form submitted",
  },
  {
    type: "dtmf",
    filter: "other",
    icon: HashIcon,
    title: (p) => `DTMF: ${String(p.digits ?? "")}`,
  },
  {
    type: "recording",
    filter: "other",
    icon: AudioLinesIcon,
    title: (p) => `Recording ${String(p.status ?? "updated")}`,
    summary: (p) => (p.error ? String(p.error) : null),
  },
  // V5-34 (ask #178(10)): the post-call privacy scrub's own event
  // (`api/src/lkap_api/privacy/scrub.py::PRIVACY_SCRUBBED_EVENT`) had no
  // Timeline row; `SessionDetailOut.scrubbed_at` (the QA tab's own "Personal
  // details cleaned up" line) reads its `ts`, this is the Timeline's copy.
  {
    type: "privacy_scrubbed",
    filter: "other",
    tone: "info",
    icon: ShieldIcon,
    title: () => "Transcript cleaned",
    summary: (p) => {
      const replaced = p.replaced as Record<string, number> | undefined;
      const total = replaced ? Object.values(replaced).reduce((sum, n) => sum + (typeof n === "number" ? n : 0), 0) : 0;
      return total > 0 ? `${total} detail${total === 1 ? "" : "s"} masked` : null;
    },
  },
  // V5-42 (ask #263): the three caller-memory events (`MemoryRecalledEvent`,
  // `MemoryStoredEvent`, `MemoryForgottenEvent`, `lkap_contracts.api_models`).
  // Titles never show the memory text itself (R-V5-15: third-party data) —
  // just what happened; the full recalled/stored text is on the Memory tab.
  {
    type: "memory_recalled",
    filter: "other",
    tone: "info",
    icon: BrainIcon,
    title: (p) => {
      const status = String(p.status ?? "empty");
      const count = Number(p.count ?? 0);
      if (status === "recalled") return `Recalled ${count} caller ${count === 1 ? "memory" : "memories"}`;
      if (status === "no_identity") return "No caller id to recall memories for";
      if (status === "disabled") return "Memory was off";
      if (status === "unavailable") return "Memory wasn't installed on the server";
      if (status === "failed") return "Recalling memories failed";
      return "Nothing to recall (first call)";
    },
    summary: (p) => (p.forgotten ? "This caller has since been forgotten" : null),
  },
  {
    type: "memory_stored",
    filter: "other",
    tone: "success",
    icon: BrainIcon,
    title: (p) => {
      const status = String(p.status ?? "skipped");
      const count = Number(p.count ?? 0);
      if (status === "stored") return `Stored ${count} caller ${count === 1 ? "memory" : "memories"}`;
      if (status === "nothing_new") return "Nothing new to store";
      if (status === "failed") return "Storing memory failed";
      return "Memory not stored";
    },
    summary: (p) => (p.reason ? String(p.reason) : p.forgotten ? "This caller has since been forgotten" : null),
  },
  {
    type: "memory_forgotten",
    filter: "other",
    tone: "warning",
    icon: UserXIcon,
    title: () => "Caller forgotten",
    summary: (p) => {
      const reason = String(p.reason ?? "caller");
      if (reason === "workspace") return "The whole workspace's memories were purged";
      if (reason === "retention") return "Removed automatically after its retention period";
      return "Forgotten by an admin";
    },
  },
];

export const sessionsV2Extension: SessionDetailExtension = {
  id: "V2-14",
  tabs: [
    {
      id: "recording",
      label: "Recording",
      icon: AudioLinesIcon,
      order: 30,
      Component: RecordingTab,
      // V5-17: a consent-gated recording that never started stays
      // `status: "none"` (no new status, `api/src/lkap_api/recordings/consent.py`)
      // but still has something to say ("Not recorded: consent declined") —
      // show the tab for that case too, not only once a recording exists.
      visible: ({ session }) => (session.recording?.status ?? "none") !== "none" || Boolean(session.recording?.error),
    },
    { id: "cost", label: "Cost", icon: CoinsIcon, order: 40, Component: CostTab },
    { id: "qa", label: "QA", icon: GaugeIcon, order: 50, Component: QaTab },
    // V5-42: always shown (like Cost/QA) — the tab itself explains an
    // off/anonymous/unavailable state rather than being hidden for it.
    { id: "memory", label: "Memory", icon: BrainIcon, order: 55, Component: MemoryTab },
  ],
  eventKinds: EVENT_KINDS,
};
