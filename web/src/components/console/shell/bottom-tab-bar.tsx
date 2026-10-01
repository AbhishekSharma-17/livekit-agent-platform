"use client";

import * as React from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { MoreHorizontalIcon } from "lucide-react";

import { Icon } from "@/components/shared/icon";
import { useSidebar } from "@/components/ui/sidebar";
import { cn } from "@/lib/utils";
import { formatNavCount, isFullScreenTaskRoute, isNavItemActive, tabBarItems } from "./nav-config";
import { useLiveSessionCount, useTabBarAudience } from "./nav-state";

/**
 * Pages that opt out of the phone tab bar at runtime. A counter, so two
 * mounted opt-outs don't cancel each other on unmount.
 */
const HideTabBarContext = React.createContext<{ hide: () => () => void } | null>(null);

export function BottomTabBarProvider({ children }: { children: (hidden: boolean) => React.ReactNode }) {
  const [hiders, setHiders] = React.useState(0);
  const value = React.useMemo(
    () => ({
      hide: () => {
        setHiders((n) => n + 1);
        return () => setHiders((n) => n - 1);
      },
    }),
    [],
  );
  return <HideTabBarContext.Provider value={value}>{children(hiders > 0)}</HideTabBarContext.Provider>;
}

/**
 * Render on a full-screen task page that has its own bottom action bar: the
 * phone tab bar is not rendered while it is mounted (section 7.2), so the
 * page padding and toasts drop back down too.
 */
export function HideBottomTabBar() {
  const ctx = React.useContext(HideTabBarContext);
  React.useEffect(() => ctx?.hide(), [ctx]);
  return null;
}

/** Whether the shell should render the tab bar on this route. */
export function useShowBottomTabBar(hiddenByPage: boolean): boolean {
  const pathname = usePathname() ?? "/console";
  return !hiddenByPage && !isFullScreenTaskRoute(pathname);
}

const TAB =
  "relative flex min-h-12 min-w-12 flex-1 flex-col items-center justify-center gap-0.5 rounded text-tab font-medium text-text-secondary outline-none focus-visible:shadow-focus";

function TabIcon({ icon, active, badge }: { icon: React.ComponentProps<typeof Icon>["as"]; active: boolean; badge?: string }) {
  return (
    <span
      className={cn(
        "relative flex h-[26px] w-12 items-center justify-center rounded-pill transition-colors duration-(--duration-fast)",
        active && "bg-brand-subtle text-brand",
      )}
    >
      <Icon as={icon} size="lg" />
      {badge ? (
        <span
          aria-hidden="true"
          data-slot="tab-badge"
          className="absolute -top-1 right-1.5 flex h-4 min-w-4 items-center justify-center rounded-pill bg-brand px-1 text-tab leading-none font-semibold text-brand-foreground tabular-nums"
        >
          {badge}
        </span>
      ) : null}
    </span>
  );
}

/**
 * The phone bottom tab bar (docs/ui/DESIGN-SYSTEM.md section 7.2, decision
 * D8), shown at 640 px and below: the most-used destinations for the
 * person's role plus "More", which opens the Menu dialog. Fixed to the
 * bottom, 60 px plus the safe-area inset, on `--card` with a top hairline;
 * 48 px targets, a 20 px icon in a 48 × 26 pill that turns `--brand-subtle`
 * with accent ink when active, 11 px labels, live counts capped at "9+".
 *
 * `data-slot="bottom-tab-bar"` is what lifts toasts above it
 * (`globals.css`), so the shell renders it only where it belongs rather than
 * hiding it.
 */
export function BottomTabBar() {
  const pathname = usePathname() ?? "/console";
  const audience = useTabBarAudience();
  const liveCount = useLiveSessionCount();
  const { openMenu, menuOpen } = useSidebar();
  const items = tabBarItems(audience);

  return (
    <nav
      aria-label="Quick navigation"
      data-slot="bottom-tab-bar"
      className="fixed inset-x-0 bottom-0 z-30 hidden border-t border-border bg-card pb-[env(safe-area-inset-bottom)] max-[640px]:block"
    >
      <ul className="flex h-bottombar items-stretch justify-around gap-1 px-2 pr-[max(0.5rem,env(safe-area-inset-right))] pl-[max(0.5rem,env(safe-area-inset-left))]">
        {items.map((item) => {
          const active = isNavItemActive(item, pathname);
          const badge = item.href === "/console/sessions" ? formatNavCount(liveCount) : undefined;
          return (
            <li key={item.href} className="flex flex-1">
              <Link
                href={item.href}
                aria-current={active ? "page" : undefined}
                data-active={active}
                className={cn(TAB, active && "text-foreground")}
              >
                <TabIcon icon={item.icon} active={active} badge={badge} />
                <span className="max-w-full truncate">{item.label}</span>
                {badge ? <span className="sr-only">, {liveCount} live</span> : null}
              </Link>
            </li>
          );
        })}
        <li className="flex flex-1">
          <button
            type="button"
            aria-haspopup="dialog"
            aria-expanded={menuOpen}
            className={TAB}
            onClick={(event) => openMenu(event.currentTarget)}
          >
            <TabIcon icon={MoreHorizontalIcon} active={false} />
            <span>More</span>
          </button>
        </li>
      </ul>
    </nav>
  );
}
