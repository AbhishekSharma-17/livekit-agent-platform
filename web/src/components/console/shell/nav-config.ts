import type { LucideIcon } from "lucide-react";
import {
  Activity,
  BarChart3,
  BookOpen,
  Bot,
  History,
  LayoutDashboard,
  Phone,
  Plug,
  Puzzle,
  Settings2,
  Wrench,
} from "lucide-react";

/**
 * Console IA (docs/UI_UX_SPEC.md §3.2, docs/v2/UI_UX_SPEC-V2-AMENDMENTS.md §1).
 * Single source of truth for the sidebar, the mobile sheet nav and the
 * breadcrumb fallback label — tests assert against this list rather than a
 * hard-coded count.
 *
 * Credentials (v1) drops out of the sidebar per the amendments: Providers is
 * now the entry point, and `/console/keys` (the credentials list) remains reachable only as
 * a link from Providers (WP-4/V2-13). Evals (Phase 2) and Widget (a dialog
 * inside the agent editor, not a page) are intentionally not nav items.
 *
 * `hidden` lets a stretch destination (Telephony: V2-17, wave 3) be flipped
 * on without touching the group shape once its pages exist.
 */
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
    icon: Bot,
    items: [
      { label: "Agents", href: "/console/agents", icon: Bot },
      { label: "Knowledge", href: "/console/knowledge", icon: BookOpen },
      { label: "Tools", href: "/console/tools", icon: Wrench },
    ],
  },
  {
    label: "Connect",
    icon: Plug,
    items: [
      { label: "Connections", href: "/console/connections", icon: Plug },
      { label: "Providers", href: "/console/providers", icon: Puzzle },
      // Stretch (V2-17, wave 3): kept visible per the amendments' IA table
      // (no "coming soon" styling — it is a real destination once V2-17
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
      // Evals (Phase 2): deliberately absent — the amendments call out no
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
