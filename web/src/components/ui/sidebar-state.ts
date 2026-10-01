/**
 * Where the desktop sidebar remembers whether it is collapsed to the icon
 * rail. A plain module (no "use client") so the server half of the console
 * shell can read the cookie name as a real string.
 *
 * - `localStorage` under `SIDEBAR_STORAGE_KEY` is the per-browser record.
 * - The `SIDEBAR_COOKIE` mirror lets the server render the right width on
 *   the first paint, so the sidebar never jumps after hydration.
 *
 * Both hold `"collapsed"` or `"expanded"`. Anything else is ignored.
 */
export const SIDEBAR_STORAGE_KEY = "lkap:sidebar";
export const SIDEBAR_COOKIE = "lkap-sidebar";
/** One year, in seconds. */
export const SIDEBAR_COOKIE_MAX_AGE = 60 * 60 * 24 * 365;

export type SidebarState = "collapsed" | "expanded";

/** A stored value, validated. `null` for a missing or unknown value. */
export function parseSidebarState(value: string | null | undefined): SidebarState | null {
  return value === "collapsed" || value === "expanded" ? value : null;
}

/** Read the stored state from `localStorage`, dropping a value that isn't valid. */
export function readStoredSidebarState(): SidebarState | null {
  try {
    const raw = window.localStorage.getItem(SIDEBAR_STORAGE_KEY);
    const state = parseSidebarState(raw);
    if (raw !== null && state === null) window.localStorage.removeItem(SIDEBAR_STORAGE_KEY);
    return state;
  } catch {
    // Storage blocked (private mode, a sandboxed frame): nothing remembered.
    return null;
  }
}

/** Remember the state in `localStorage` and mirror it to the cookie. */
export function writeSidebarState(state: SidebarState): void {
  try {
    window.localStorage.setItem(SIDEBAR_STORAGE_KEY, state);
  } catch {
    // Storage blocked: the cookie still carries it.
  }
  document.cookie = `${SIDEBAR_COOKIE}=${state}; path=/; max-age=${SIDEBAR_COOKIE_MAX_AGE}; SameSite=Lax`;
}
