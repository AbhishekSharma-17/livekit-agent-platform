/**
 * Settings sections that moved to another page. Their old `?tab=` deep links
 * and bookmarks redirect there (`app/console/settings/page.tsx`).
 */
export const MOVED_SETTINGS_TABS: Readonly<Record<string, string>> = {
  // UI-R1: knowledge connections live with knowledge now.
  "knowledge-connections": "/console/knowledge?tab=connections",
};

/** Where a requested settings tab now lives, or `null` when it is still a settings section. */
export function movedSettingsTab(tab: string | string[] | undefined): string | null {
  return typeof tab === "string" ? (MOVED_SETTINGS_TABS[tab] ?? null) : null;
}
