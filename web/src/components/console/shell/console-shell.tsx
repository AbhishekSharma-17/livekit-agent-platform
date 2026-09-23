"use client";

import * as React from "react";

import { Toaster } from "@/components/ui/sonner";
import { SidebarInset, SidebarProvider } from "@/components/ui/sidebar";
import { TooltipProvider } from "@/components/ui/tooltip";
import { ConsoleQueryProvider } from "@/components/console/lib/query-provider";
import { AppSidebar } from "./app-sidebar";
import { ApiHealthBanner } from "./api-health-banner";
import { BreadcrumbProvider } from "./breadcrumb-context";
import { TopBar } from "./top-bar";

/**
 * docs/UI_UX_SPEC.md §3.1, §7.2 item 1. Client half of `app/console/layout.tsx`
 * (kept server-only so its `metadata` export and the sidebar-state cookie
 * read work); this owns everything that needs interactivity: the query
 * client, the sidebar, the top bar, the toaster.
 *
 * Provider order matters: `TooltipProvider` sits above `SidebarProvider`
 * because the collapsed rail's `SidebarMenuButton tooltip` renders a Radix
 * tooltip that needs it (the shadcn `Sidebar` does not ship its own, per
 * the WP-0 hand-off note). `ThemeProvider` wraps this from the server layout,
 * outside `ConsoleQueryProvider`, unchanged from WP-0.
 */
export function ConsoleShell({
  children,
  defaultSidebarOpen,
}: {
  children: React.ReactNode;
  defaultSidebarOpen: boolean;
}) {
  return (
    <ConsoleQueryProvider>
      <BreadcrumbProvider>
        <TooltipProvider>
          <SidebarProvider defaultOpen={defaultSidebarOpen}>
            <a
              href="#console-main"
              className="sr-only focus:not-sr-only focus:absolute focus:top-2 focus:left-2 focus:z-50 focus:rounded-md focus:bg-background focus:px-3 focus:py-2 focus:text-sm focus:shadow-lg focus:outline-none focus:ring-2 focus:ring-ring"
            >
              Skip to content
            </a>
            <AppSidebar />
            {/* shadcn's `SidebarInset` already renders a `<main>` — a second
                one here would be a duplicate landmark (axe
                `landmark-no-duplicate-main`, docs/UI_UX_SPEC.md §7.2
                verification). This is a plain `div`; `tabIndex={-1}` is what
                lets the skip link actually move focus to it. */}
            <SidebarInset>
              <TopBar />
              <ApiHealthBanner />
              <div
                id="console-main"
                tabIndex={-1}
                className="mx-auto w-full max-w-[1200px] flex-1 px-4 py-6 outline-none md:px-6 lg:px-8"
              >
                {children}
              </div>
            </SidebarInset>
          </SidebarProvider>
        </TooltipProvider>
      </BreadcrumbProvider>
      <Toaster position="bottom-right" />
    </ConsoleQueryProvider>
  );
}
