import { BracesIcon, HeadphonesIcon, LayoutPanelLeftIcon, ListTreeIcon, MessageSquareTextIcon } from "lucide-react";

import { LiveSessionTab } from "../live/live-tab";
import { SessionPanelTab } from "../session-panel-tab";
import { SessionRawEvents } from "../session-raw-events";
import { SessionTimeline } from "../session-timeline";
import { SessionTranscript } from "../session-transcript";
import type { SessionTabDef } from "./types";

/**
 * The four WP-7 tabs (docs/UI_UX_SPEC.md §4.10) plus V5-38's Live tab. Orders
 * leave 30/40/50 free for V2-14's Recording, Cost and QA
 * (docs/v2/UI_UX_SPEC-V2-AMENDMENTS.md §1: `timeline|transcript|recording|cost|qa|panel|raw`).
 * "Live" sits before Timeline (order 5): while a call is active it's the tab
 * a builder or on-call reviewer wants first, and it hides itself
 * (`visible`) the moment the session isn't `active` any more.
 */
export const BUILTIN_SESSION_TABS: SessionTabDef[] = [
  {
    id: "live",
    label: "Live",
    icon: HeadphonesIcon,
    order: 5,
    Component: LiveSessionTab,
    visible: ({ session }) => session.status === "active",
  },
  { id: "timeline", label: "Timeline", icon: ListTreeIcon, order: 10, Component: SessionTimeline },
  { id: "transcript", label: "Transcript", icon: MessageSquareTextIcon, order: 20, Component: SessionTranscript },
  { id: "panel", label: "Panel at end of call", icon: LayoutPanelLeftIcon, order: 60, Component: SessionPanelTab },
  { id: "raw", label: "Raw events", icon: BracesIcon, order: 90, Component: SessionRawEvents },
];
