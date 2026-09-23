"use client";

import * as React from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { ExternalLinkIcon } from "lucide-react";

import { Icon } from "@/components/shared/icon";
import { StateMeter } from "@/components/shared/state-meter";
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarSeparator,
} from "@/components/ui/sidebar";
import { useHealth } from "@/components/console/lib/api-hooks";
import { NAV_GROUPS, isNavItemActive } from "./nav-config";
import { AccountMenu } from "./account-menu";
import { ThemeMenu } from "./theme-menu";
import { WorkspaceSwitcher } from "./workspace-switcher";

/**
 * docs/UI_UX_SPEC.md §3.2 (wordmark, footer) + §7.2 item 2; nav groups per
 * docs/v2/UI_UX_SPEC-V2-AMENDMENTS.md §1. Rendered both as the ≥1024px rail
 * and, via shadcn `Sidebar`'s own mobile mode, inside the < 1024px sheet —
 * one component, so the footer (workspace switcher, theme, docs link)
 * never has to be built twice.
 */
export function AppSidebar() {
  const pathname = usePathname() ?? "/console";
  const { data: health } = useHealth();
  const docsUrl = process.env.NEXT_PUBLIC_DOCS_URL;

  return (
    <Sidebar collapsible="icon" role="navigation" aria-label="Console">
      <SidebarHeader>
        <Link
          href="/console"
          className="flex items-center gap-2 rounded-md px-2 py-1.5 outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <StateMeter state="idle" size="sm" bars={4} />
          <span className="min-w-0 flex-1 truncate text-sm group-data-[collapsible=icon]:hidden">
            <span className="font-semibold text-sidebar-foreground">LKAP</span>{" "}
            <span className="font-normal text-muted-foreground">Console</span>
          </span>
        </Link>
      </SidebarHeader>
      <SidebarContent>
        {NAV_GROUPS.map((group, index) => {
          const items = group.items.filter((item) => !item.hidden);
          if (items.length === 0) return null;
          return (
            <SidebarGroup key={group.label ?? `ungrouped-${index}`}>
              {group.label ? <SidebarGroupLabel>{group.label}</SidebarGroupLabel> : null}
              <SidebarGroupContent>
                <SidebarMenu>
                  {items.map((item) => {
                    const active = isNavItemActive(item, pathname);
                    return (
                      <SidebarMenuItem key={item.href}>
                        <SidebarMenuButton
                          asChild
                          isActive={active}
                          tooltip={item.label}
                          className="data-[active=true]:bg-brand-soft data-[active=true]:text-brand-text data-[active=true]:font-medium"
                        >
                          <Link href={item.href}>
                            <Icon as={item.icon} size="lg" />
                            <span>{item.label}</span>
                          </Link>
                        </SidebarMenuButton>
                      </SidebarMenuItem>
                    );
                  })}
                </SidebarMenu>
              </SidebarGroupContent>
            </SidebarGroup>
          );
        })}
      </SidebarContent>
      <SidebarFooter>
        <WorkspaceSwitcher />
        <AccountMenu />
        <SidebarSeparator className="my-1" />
        <ThemeMenu />
        {docsUrl ? (
          <a
            href={docsUrl}
            target="_blank"
            rel="noreferrer noopener"
            className="flex items-center gap-2 rounded-md px-2 py-1.5 text-sm text-sidebar-foreground outline-none hover:bg-sidebar-accent hover:text-sidebar-accent-foreground focus-visible:ring-2 focus-visible:ring-ring"
          >
            <Icon as={ExternalLinkIcon} size="md" />
            <span className="min-w-0 flex-1 truncate group-data-[collapsible=icon]:hidden">Documentation</span>
          </a>
        ) : null}
        {health?.version ? (
          <p className="truncate px-2 pb-1 text-xs text-muted-foreground group-data-[collapsible=icon]:hidden">
            v{health.version}
          </p>
        ) : null}
      </SidebarFooter>
    </Sidebar>
  );
}
