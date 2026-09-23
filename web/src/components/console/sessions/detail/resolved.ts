import { BUILTIN_EVENT_KINDS } from "./builtin-event-kinds";
import { BUILTIN_SESSION_TABS } from "./builtin-tabs";
import { SESSION_DETAIL_EXTENSIONS } from "./extensions";
import {
  resolveEventKinds,
  resolveSessionDetailSlots,
  resolveSessionTabs,
  type ResolvedSessionDetailSlots,
} from "./registry";
import type { SessionTabDef, TimelineEventKind } from "./types";

/**
 * The session detail's registries: built-ins plus `SESSION_DETAIL_EXTENSIONS`,
 * resolved on first use (lazily, so an extension module may import WP-7
 * helpers without an import-order cycle).
 */

/** The tab shown when `?tab=` is absent, unknown or hidden. */
export const DEFAULT_SESSION_TAB_ID = "timeline";

let tabs: SessionTabDef[] | undefined;
let kinds: Map<string, TimelineEventKind> | undefined;
let slots: ResolvedSessionDetailSlots | undefined;

export function sessionTabs(): SessionTabDef[] {
  tabs ??= resolveSessionTabs(BUILTIN_SESSION_TABS, SESSION_DETAIL_EXTENSIONS);
  return tabs;
}

export function sessionEventKinds(): Map<string, TimelineEventKind> {
  kinds ??= resolveEventKinds(BUILTIN_EVENT_KINDS, SESSION_DETAIL_EXTENSIONS);
  return kinds;
}

export function sessionDetailSlots(): ResolvedSessionDetailSlots {
  slots ??= resolveSessionDetailSlots(SESSION_DETAIL_EXTENSIONS);
  return slots;
}
