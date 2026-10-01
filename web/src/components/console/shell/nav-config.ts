import type { LucideIcon } from "lucide-react";
import {
  Activity,
  BarChart3,
  BookOpen,
  Headset,
  History,
  LayoutDashboard,
  Phone,
  Plug,
  Puzzle,
  Settings2,
  Table2,
  Wrench,
} from "lucide-react";

/**
 * Console IA (docs/ui/DESIGN-SYSTEM.md section 7.1, docs/v2/UI_UX_SPEC-V2-AMENDMENTS.md §1).
 * Single source of truth for the sidebar, the Menu dialog, the phone tab bar
 * and the breadcrumb fallback label. Tests assert against this list rather
 * than a hard-coded count. Items are grouped by job: Build, Connect, Observe,
 * Settings.
 *
 * Credentials (v1) drops out of the sidebar per the amendments: Providers is
 * now the entry point, and `/console/keys` (the credentials list) remains reachable only as
 * a link from Providers (WP-4/V2-13). Evals (Phase 2) and Widget (a dialog
 * inside the agent editor, not a page) are intentionally not nav items.
 *
 * `hidden` lets a stretch destination (Telephony: V2-17, wave 3) be flipped
 * on without touching the group shape once its pages exist.
 */
/**
 * The one icon for "Agents" (voice and video agents) everywhere: the sidebar, the Menu dialog,
 * the phone tab bar, the Overview stat and the agents list's empty states. `Headset` rather than
 * `AudioLines`: in the 17 px nav, AudioLines' vertical strokes sit two rows above Analytics'
 * bar chart and repeat the product mark's bars, while a headset reads as "an agent on a call"
 * for voice and video alike and stays distinct from Telephony's handset. The Settings "AI agents"
 * tab keeps `Bot`, because that is coding agents over MCP, a different thing.
 */
export const AGENTS_ICON: LucideIcon = Headset;

export interface NavItem {
  label: string;
  href: string;
  /** Matches the active route; defaults to a prefix match on `href`. */
  match?: (pathname: string) => boolean;
  icon: LucideIcon;
  hidden?: boolean;
}

export interface NavGroup {
  /** `null` for the ungrouped top entry (Overview). */
  label: string | null;
  icon?: LucideIcon;
  items: NavItem[];
}

export const NAV_GROUPS: NavGroup[] = [
  {
    label: null,
    items: [
      {
        label: "Overview",
        href: "/console",
        match: (p) => p === "/console",
        icon: LayoutDashboard,
      },
    ],
  },
  {
    label: "Build",
    icon: AGENTS_ICON,
    items: [
      { label: "Agents", href: "/console/agents", icon: AGENTS_ICON },
      { label: "Knowledge", href: "/console/knowledge", icon: BookOpen },
      { label: "Tools", href: "/console/tools", icon: Wrench },
      // V6-19: lookup tables (`datasets` in code and paths; "lookup table" everywhere the caller sees it).
      { label: "Lookup tables", href: "/console/datasets", icon: Table2 },
    ],
  },
  {
    label: "Connect",
    icon: Plug,
    items: [
      { label: "Connections", href: "/console/connections", icon: Plug },
      { label: "Providers", href: "/console/providers", icon: Puzzle },
      // Stretch (V2-17, wave 3): kept visible per the amendments' IA table
      // (no "coming soon" styling, as it is a real destination once V2-17
      // lands, not a placeholder). Flip `hidden: true` if it should be
      // pulled before that.
      { label: "Telephony", href: "/console/telephony", icon: Phone },
    ],
  },
  {
    label: "Observe",
    icon: Activity,
    items: [
      { label: "Sessions", href: "/console/sessions", icon: History },
      { label: "Analytics", href: "/console/analytics", icon: BarChart3 },
      // Evals (Phase 2): deliberately absent, as the amendments call out no
      // "coming soon" nav entries.
    ],
  },
  {
    label: "Settings",
    icon: Settings2,
    items: [{ label: "Settings", href: "/console/settings", icon: Settings2 }],
  },
];

export function isNavItemActive(item: NavItem, pathname: string): boolean {
  if (item.match) return item.match(pathname);
  return pathname === item.href || pathname.startsWith(`${item.href}/`);
}

/** Flat list, for the mobile top-bar fallback label and tests. */
export const NAV_ITEMS: NavItem[] = NAV_GROUPS.flatMap((group) => group.items).filter((item) => !item.hidden);

/** Best-match static label for a pathname, used when no page has set a richer breadcrumb trail. */
export function navLabelForPath(pathname: string): string {
  const match = NAV_ITEMS.filter((item) => isNavItemActive(item, pathname)).sort(
    (a, b) => b.href.length - a.href.length,
  )[0];
  return match?.label ?? "Console";
}

/**
 * Phone bottom tab bar (docs/ui/DESIGN-SYSTEM.md section 7.2, decision D8):
 * the most-used destinations for the person's role, then "More", which opens
 * the Menu dialog. Builders, admins and owners build; viewers watch.
 */
export type TabBarAudience = "builder" | "viewer";

const TAB_BAR_HREFS: Record<TabBarAudience, readonly string[]> = {
  builder: ["/console", "/console/agents", "/console/sessions", "/console/knowledge"],
  viewer: ["/console", "/console/sessions", "/console/analytics"],
};

export function tabBarItems(audience: TabBarAudience): NavItem[] {
  return TAB_BAR_HREFS[audience]
    .map((href) => NAV_ITEMS.find((item) => item.href === href))
    .filter((item): item is NavItem => item !== undefined);
}

/**
 * Full-screen task pages with their own bottom action bar: the phone tab bar
 * is not rendered there (section 7.2). None exists yet. A page can also opt
 * out at runtime with `<HideBottomTabBar />` (`bottom-tab-bar.tsx`).
 */
export const FULL_SCREEN_TASK_ROUTES: ReadonlyArray<(pathname: string) => boolean> = [];

export function isFullScreenTaskRoute(pathname: string): boolean {
  return FULL_SCREEN_TASK_ROUTES.some((matches) => matches(pathname));
}

/** A live count as a badge: "9+" past nine; nothing for zero. */
export function formatNavCount(count: number | undefined): string | undefined {
  if (!count || count <= 0) return undefined;
  return count > 9 ? "9+" : String(count);
}
