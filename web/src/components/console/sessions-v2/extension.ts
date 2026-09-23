import { ArrowRightLeftIcon, AudioLinesIcon, CoinsIcon, GaugeIcon, GridIcon, HashIcon, PhoneForwardedIcon } from "lucide-react";

import type { SessionDetailExtension, TimelineEventKind } from "@/components/console/sessions/detail/types";
import { RecordingTab } from "./recording-tab";
import { CostTab } from "./cost-tab";
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
      visible: ({ session }) => (session.recording?.status ?? "none") !== "none",
    },
    { id: "cost", label: "Cost", icon: CoinsIcon, order: 40, Component: CostTab },
    { id: "qa", label: "QA", icon: GaugeIcon, order: 50, Component: QaTab },
  ],
  eventKinds: EVENT_KINDS,
};
