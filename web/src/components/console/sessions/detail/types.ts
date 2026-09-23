import type * as React from "react";
import type { LucideIcon } from "lucide-react";

import type { SessionDetailOut } from "@/contracts/lkap-contracts";

/**
 * Tab, timeline-row and slot contracts of the session detail (WP-7). Later
 * packages (V2-14: Recording, Cost, QA, and the v2 timeline rows) plug in
 * through `SessionDetailExtension`s listed in `./extensions.ts`; see
 * `../README.md` for the rules.
 */

/** Props every tab component receives. Tabs fetch anything else they need themselves. */
export interface SessionTabProps {
  session: SessionDetailOut;
}

/** What a tab's `visible` predicate can look at. */
export interface SessionTabContext {
  session: SessionDetailOut;
}

export interface SessionTabDef {
  /** The `?tab=` value. Stable; never rename once shipped (links and bookmarks use it). */
  id: string;
  /** Tab label, sentence case. */
  label: string;
  icon: LucideIcon;
  /**
   * Position in the tab list. Built-ins: timeline 10 · transcript 20 ·
   * panel 60 · raw 90, leaving 30/40/50 for recording/cost/qa (the order of
   * docs/v2/UI_UX_SPEC-V2-AMENDMENTS.md §1).
   */
  order: number;
  Component: React.ComponentType<SessionTabProps>;
  /** Hide the tab (trigger and deep link) unless this returns true. Default: always visible. */
  visible?: (ctx: SessionTabContext) => boolean;
}

/** A partial update of an already-registered tab, matched by `id`. */
export type SessionTabPatch = { id: string; patch: Partial<Omit<SessionTabDef, "id">> };

/** The Timeline's filter chips (docs/UI_UX_SPEC.md §7.8). */
export type TimelineFilter = "turns" | "tools" | "state" | "errors" | "other";

/** Row emphasis. `danger`/`warning` rows get the soft surface (errors, escalations). */
export type TimelineTone = "neutral" | "info" | "success" | "warning" | "danger";

export type EventPayload = Record<string, unknown>;

/**
 * How the Timeline renders one `SessionEventOut.type`. The core types
 * `user_turn`, `agent_turn`, `tool_call_started`, `tool_call_ended` and
 * `agent_state` are handled by the timeline itself (turn rows, paired tool
 * rows, the state track) and cannot be overridden; everything else is a
 * kind. Types with no kind render as a neutral "Other" row titled after the
 * type. The payload is always reachable through the row's "Details"
 * disclosure — never render raw JSON in `title`/`summary`.
 */
export interface TimelineEventKind {
  /** `SessionEventOut.type`, e.g. `"handoff"`. */
  type: string;
  /** Which filter chip shows the row. */
  filter: TimelineFilter;
  tone?: TimelineTone;
  icon?: LucideIcon;
  /** Row title, sentence case ("Handed off to Billing"). */
  title: (payload: EventPayload) => string;
  /** Optional one-line detail under/next to the title. Plain text or small inline markup. */
  summary?: (payload: EventPayload) => React.ReactNode;
  /** Consecutive rows of this type merge into one row with a "×n" count (e.g. usage snapshots). */
  collapse?: boolean;
  /** Keep the event out of the Timeline entirely (it still shows in Raw events). */
  hidden?: boolean;
}

/** Named places in the detail page a later package can fill. */
export interface SessionDetailSlots {
  /** Extra actions at the right of the header (V2-14: "Re-score", "Download recording"). */
  headerActions?: React.ComponentType<{ session: SessionDetailOut }>[];
}

export interface SessionDetailExtension {
  /** Package id, for debugging ("V2-14"). */
  id: string;
  /** Full definitions add a tab or replace the one with the same `id`. */
  tabs?: SessionTabDef[];
  /** Partial updates of existing tabs (label, `visible`, …). Applied after `tabs`. */
  tabPatches?: SessionTabPatch[];
  /** Timeline row kinds; a kind with an existing `type` replaces it. */
  eventKinds?: TimelineEventKind[];
  /** Array slots are concatenated in extension order. */
  slots?: SessionDetailSlots;
}
