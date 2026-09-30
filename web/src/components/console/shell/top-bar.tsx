"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import * as React from "react";

import type { BreadcrumbEntry } from "@/components/shared/page-header";
import {
  Breadcrumb,
  BreadcrumbItem,
  BreadcrumbLink,
  BreadcrumbList,
  BreadcrumbPage,
  BreadcrumbSeparator,
} from "@/components/ui/breadcrumb";
import { SidebarTrigger } from "@/components/ui/sidebar";
import { useActiveWorkspace } from "@/components/console/settings/use-settings-queries";
import { cn } from "@/lib/utils";
import { navLabelForPath } from "./nav-config";
import { useBreadcrumbTrail } from "./breadcrumb-context";

/**
 * The top bar (docs/ui/DESIGN-SYSTEM.md sections 7.1 and 7.2): 56 px, sticky,
 * the frosted `--scrim` with a 12 px backdrop blur and a bottom hairline.
 * Left: the hamburger (820 px and below) and the breadcrumb: the workspace as
 * context, then the page's own trail (set with `ConsoleBreadcrumbs`) or the
 * nav label, the current page last with `aria-current`. The workspace crumb
 * is dropped on phones to leave the page name room.
 *
 * Search lives on each list, not here; there are no notifications yet, so
 * there is no bell.
 */
export function TopBar() {
  const pathname = usePathname() ?? "/console";
  const trail = useBreadcrumbTrail();
  const { workspace } = useActiveWorkspace();
  const pages: BreadcrumbEntry[] = trail ?? [{ label: navLabelForPath(pathname) }];
  const items: (BreadcrumbEntry & { context?: boolean })[] = workspace
    ? [{ label: workspace.name, href: "/console", context: true }, ...pages]
    : pages;

  return (
    <header
      data-slot="top-bar"
      className="sticky top-0 z-30 flex h-topbar shrink-0 items-center gap-2 border-b border-border bg-scrim px-5 backdrop-blur-md min-[821px]:rounded-t-lg max-[820px]:px-4"
    >
      <SidebarTrigger className="-ml-1.5" />
      <Breadcrumb className="min-w-0 flex-1">
        <BreadcrumbList className="flex-nowrap gap-2 text-control text-text-secondary">
          {items.map((item, index) => {
            const last = index === items.length - 1;
            return (
              <React.Fragment key={`${item.label}-${index}`}>
                <BreadcrumbItem className={cn("min-w-0", item.context && "shrink-0 max-[640px]:hidden", last && "truncate")}>
                  {item.href && !last ? (
                    <BreadcrumbLink asChild className="truncate">
                      <Link href={item.href}>{item.label}</Link>
                    </BreadcrumbLink>
                  ) : (
                    <BreadcrumbPage className="truncate font-medium">{item.label}</BreadcrumbPage>
                  )}
                </BreadcrumbItem>
                {!last ? (
                  <BreadcrumbSeparator className={cn("text-text-tertiary", item.context && "max-[640px]:hidden")}>
                    /
                  </BreadcrumbSeparator>
                ) : null}
              </React.Fragment>
            );
          })}
        </BreadcrumbList>
      </Breadcrumb>
    </header>
  );
}
