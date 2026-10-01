"use client";

import * as React from "react";
import { usePathname } from "next/navigation";

import { Toaster } from "@/components/ui/sonner";
import { SidebarProvider, useSidebar } from "@/components/ui/sidebar";
import { TooltipProvider } from "@/components/ui/tooltip";
import { ConsoleQueryProvider } from "@/components/console/lib/query-provider";
import { cn } from "@/lib/utils";
import { AppSidebar } from "./app-sidebar";
import { ApiHealthBanner } from "./api-health-banner";
import { BottomTabBar, BottomTabBarProvider, useShowBottomTabBar } from "./bottom-tab-bar";
import { BreadcrumbProvider } from "./breadcrumb-context";
import { OfflineBanner } from "./offline-banner";
import { TopBar } from "./top-bar";

/**
 * Transitional page frame. A screen that renders `Page`
 * (`components/shared/page-header.tsx`) owns its width and padding; until
 * every screen does, anything else gets the default page container (1200 px,
 * `28px 32px 72px`, then `20px 20px 56px` at 900 px and `16px 16px 48px` at
 * 640 px) so no route goes edge to edge.
 */
const LEGACY_PAGE_FRAME = [
  "w-full min-w-0",
  "[&:not(:has([data-slot=page]))]:mx-auto [&:not(:has([data-slot=page]))]:max-w-[1200px]",
  "[&:not(:has([data-slot=page]))]:px-8 [&:not(:has([data-slot=page]))]:pt-7 [&:not(:has([data-slot=page]))]:pb-18",
  "max-[900px]:[&:not(:has([data-slot=page]))]:px-5 max-[900px]:[&:not(:has([data-slot=page]))]:pt-5 max-[900px]:[&:not(:has([data-slot=page]))]:pb-14",
  "max-[640px]:[&:not(:has([data-slot=page]))]:px-4 max-[640px]:[&:not(:has([data-slot=page]))]:pt-4 max-[640px]:[&:not(:has([data-slot=page]))]:pb-12",
].join(" ");

function ShellFrame({ children, tabBarHiddenByPage }: { children: React.ReactNode; tabBarHiddenByPage: boolean }) {
  const pathname = usePathname();
  const { setMenuOpen } = useSidebar();
  const showTabBar = useShowBottomTabBar(tabBarHiddenByPage);

  // A route change (a link in the Menu dialog, the tab bar, a page) closes the menu.
  React.useEffect(() => {
    setMenuOpen(false);
  }, [pathname, setMenuOpen]);

  return (
    <div
      data-slot="console-shell"
      data-tab-bar={showTabBar ? "" : undefined}
      className={cn(
        "flex min-h-dvh w-full bg-sidebar text-foreground max-[820px]:bg-background",
        // Screens offset their own sticky bars by the top bar's height.
        "[--console-topbar-height:var(--layout-topbar)]",
        // Toasts clear the phone tab bar from 640 px down (sonner's own phone
        // offset only starts at 600 px).
        showTabBar &&
          "max-[640px]:[--shell-toast-bottom:calc(var(--layout-bottombar)+env(safe-area-inset-bottom)+var(--toast-offset-mobile))]",
      )}
    >
      <a
        href="#main-content"
        className="sr-only focus:not-sr-only focus:fixed focus:top-2 focus:left-2 focus:z-[60] focus:rounded focus:bg-card focus:px-3 focus:py-2 focus:text-label focus:font-medium focus:text-foreground focus:shadow-focus focus:outline-none"
      >
        Skip to content
      </a>
      <AppSidebar />
      <div data-slot="main-panel-frame" className="flex min-w-0 flex-1 flex-col min-[821px]:py-panel-inset min-[821px]:pr-panel-inset">
        <div
          data-slot="main-panel"
          className="flex min-w-0 flex-1 flex-col bg-background min-[821px]:rounded-lg min-[821px]:border min-[821px]:border-border min-[821px]:shadow-raised"
        >
          <TopBar />
          <OfflineBanner />
          <ApiHealthBanner />
          <main
            id="main-content"
            tabIndex={-1}
            className={cn(
              "flex min-w-0 flex-1 flex-col outline-none",
              showTabBar && "max-[640px]:pb-[calc(var(--layout-bottombar)+env(safe-area-inset-bottom))]",
            )}
          >
            <div data-slot="page-frame" className={LEGACY_PAGE_FRAME}>
              {children}
            </div>
          </main>
        </div>
      </div>
      {showTabBar ? <BottomTabBar /> : null}
      <Toaster
        offset={{
          top: "var(--toast-offset)",
          right: "var(--toast-offset)",
          bottom: "var(--shell-toast-bottom, var(--toast-offset))",
          left: "var(--toast-offset)",
        }}
      />
    </div>
  );
}

/**
 * The console app shell (docs/ui/DESIGN-SYSTEM.md sections 7.1 and 7.2), the
 * client half of `app/console/layout.tsx` (`console-shell.tsx` is the server
 * half, which reads the remembered sidebar state from its cookie):
 *
 * - wider than 820 px: the 248 px sidebar on the app background (or the
 *   58 px icon rail once collapsed, `defaultCollapsed` on the first paint)
 *   and the inset main panel (8 px inset, 12 px radius, hairline, raised
 *   shadow) holding the sticky frosted top bar and `<main id="main-content">`.
 *   The panel is `flex-1`, so it fills whatever the sidebar leaves;
 * - 820 px and below: no sidebar, a full-bleed panel and a hamburger that
 *   opens the full-screen Menu dialog;
 * - 640 px and below: the phone bottom tab bar, with the page and toasts
 *   lifted above it.
 *
 * The document scrolls (not the panel), so `window.scrollTo` and sticky
 * elements keep working. `TooltipProvider` stays above everything that
 * renders a Radix tooltip.
 */
export function ConsoleShellClient({
  children,
  defaultCollapsed = false,
}: {
  children: React.ReactNode;
  /** The desktop sidebar starts as the icon rail (the remembered choice). */
  defaultCollapsed?: boolean;
}) {
  return (
    <ConsoleQueryProvider>
      <BreadcrumbProvider>
        <TooltipProvider>
          <SidebarProvider defaultCollapsed={defaultCollapsed}>
            <BottomTabBarProvider>
              {(hidden) => <ShellFrame tabBarHiddenByPage={hidden}>{children}</ShellFrame>}
            </BottomTabBarProvider>
          </SidebarProvider>
        </TooltipProvider>
      </BreadcrumbProvider>
    </ConsoleQueryProvider>
  );
}
