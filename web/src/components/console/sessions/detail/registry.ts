import type {
  SessionDetailExtension,
  SessionDetailSlots,
  SessionTabContext,
  SessionTabDef,
  TimelineEventKind,
} from "./types";

/**
 * Pure resolution of the session detail's tabs, timeline row kinds and slots
 * from the built-ins plus the extensions list (`./extensions.ts`). No
 * module-level state: tests pass their own lists.
 */

/** Event types the timeline renders itself; kinds for these are ignored. */
export const CORE_EVENT_TYPES = new Set([
  "user_turn",
  "agent_turn",
  "tool_call_started",
  "tool_call_ended",
  "agent_state",
]);

export function resolveSessionTabs(
  builtins: readonly SessionTabDef[],
  extensions: readonly SessionDetailExtension[] = [],
): SessionTabDef[] {
  const byId = new Map<string, SessionTabDef>();
  for (const def of builtins) byId.set(def.id, def);
  for (const extension of extensions) {
    for (const def of extension.tabs ?? []) byId.set(def.id, def);
  }
  for (const extension of extensions) {
    for (const { id, patch } of extension.tabPatches ?? []) {
      const current = byId.get(id);
      if (current) byId.set(id, { ...current, ...patch, id });
    }
  }
  return [...byId.values()].sort((a, b) => a.order - b.order || a.id.localeCompare(b.id));
}

export function visibleTabs(tabs: readonly SessionTabDef[], ctx: SessionTabContext): SessionTabDef[] {
  return tabs.filter((tab) => (tab.visible ? tab.visible(ctx) : true));
}

/** The tab for a `?tab=` value; unknown or hidden ids fall back to `fallbackId` (then the first tab). */
export function pickTab(
  tabs: readonly SessionTabDef[],
  requested: string | null | undefined,
  fallbackId: string,
): SessionTabDef | undefined {
  return (
    tabs.find((tab) => tab.id === requested) ?? tabs.find((tab) => tab.id === fallbackId) ?? tabs[0]
  );
}

export function resolveEventKinds(
  builtins: readonly TimelineEventKind[],
  extensions: readonly SessionDetailExtension[] = [],
): Map<string, TimelineEventKind> {
  const byType = new Map<string, TimelineEventKind>();
  const add = (kind: TimelineEventKind) => {
    if (!CORE_EVENT_TYPES.has(kind.type)) byType.set(kind.type, kind);
  };
  builtins.forEach(add);
  for (const extension of extensions) (extension.eventKinds ?? []).forEach(add);
  return byType;
}

export interface ResolvedSessionDetailSlots {
  headerActions: NonNullable<SessionDetailSlots["headerActions"]>;
}

export function resolveSessionDetailSlots(
  extensions: readonly SessionDetailExtension[] = [],
): ResolvedSessionDetailSlots {
  const resolved: ResolvedSessionDetailSlots = { headerActions: [] };
  for (const { slots } of extensions) {
    resolved.headerActions.push(...(slots?.headerActions ?? []));
  }
  return resolved;
}
