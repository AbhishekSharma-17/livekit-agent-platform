"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import * as React from "react";

import {
  Breadcrumb,
  BreadcrumbItem,
  BreadcrumbLink,
  BreadcrumbList,
  BreadcrumbPage,
  BreadcrumbSeparator,
} from "@/components/ui/breadcrumb";
import { SidebarTrigger } from "@/components/ui/sidebar";
import { navLabelForPath } from "./nav-config";
import { useBreadcrumbTrail } from "./breadcrumb-context";
import { ThemeMenu } from "./theme-menu";

/**
 * < 1024 px top bar (docs/UI_UX_SPEC.md §3.1): 56 px, menu button opening
 * the sidebar as a sheet, current page title. On ≥ 1024 px the sidebar rail
 * is always visible, so this bar collapses to just the breadcrumb trail
 * (each page's own `PageHeader` still owns its title/actions inline, per
 * §3.2 "the page's primary action lives in the PageHeader on desktop and in
 * the top bar on mobile").
 */
export function TopBar() {
  const pathname = usePathname() ?? "/console";
  const trail = useBreadcrumbTrail();
  const items = trail ?? [{ label: navLabelForPath(pathname) }];

  return (
    <div className="sticky top-0 z-10 flex h-14 items-center gap-2 border-b border-border bg-background px-4 lg:h-12 lg:px-6">
      <SidebarTrigger className="lg:hidden" />
      <Breadcrumb className="min-w-0 flex-1">
        <BreadcrumbList className="flex-nowrap">
          {items.map((item, index) => {
            const last = index === items.length - 1;
            return (
              <React.Fragment key={`${item.label}-${index}`}>
                <BreadcrumbItem className="truncate">
                  {item.href && !last ? (
                    <BreadcrumbLink asChild>
                      <Link href={item.href}>{item.label}</Link>
                    </BreadcrumbLink>
                  ) : (
                    <BreadcrumbPage className="truncate">{item.label}</BreadcrumbPage>
                  )}
                </BreadcrumbItem>
                {!last ? <BreadcrumbSeparator /> : null}
              </React.Fragment>
            );
          })}
        </BreadcrumbList>
      </Breadcrumb>
      <ThemeMenu compact className="w-auto lg:hidden" />
    </div>
  );
}
