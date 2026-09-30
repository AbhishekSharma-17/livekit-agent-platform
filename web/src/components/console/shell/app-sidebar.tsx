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
  SidebarGroupLabel,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuDialog,
  SidebarMenuItem,
  useSidebar,
} from "@/components/ui/sidebar";
import { useHealth } from "@/components/console/lib/api-hooks";
import { NAV_GROUPS, isNavItemActive, type NavItem } from "./nav-config";
import { useLiveSessionCount } from "./nav-state";
import { AccountMenu } from "./account-menu";

/** The Sessions link's live indicator: a 7 px pulsing dot with its count for screen readers. */
function LiveDot({ count }: { count: number }) {
  if (count <= 0) return null;
  return (
    <span className="ml-auto flex items-center">
      <span aria-hidden="true" data-slot="status-dot" data-pulse="" className="size-[7px] rounded-pill bg-brand" />
      <span className="sr-only">, {count} live</span>
    </span>
  );
}

/**
 * The sidebar's content (docs/ui/DESIGN-SYSTEM.md section 7.1): the
 * wordmark, the nav grouped by job, then the account menu at the foot above a
 * hairline. Rendered twice from one component: in the desktop sidebar, and in
 * the full-screen Menu dialog at 820 px and below (48 px rows there).
 */
function SidebarBody({ touch = false }: { touch?: boolean }) {
  const pathname = usePathname() ?? "/console";
  const { data: health } = useHealth();
  const liveCount = useLiveSessionCount();
  const { setMenuOpen } = useSidebar();
  const docsUrl = process.env.NEXT_PUBLIC_DOCS_URL;
  // In the Menu dialog, following a link closes it (focus goes back to the hamburger).
  const onNavigate = touch ? () => setMenuOpen(false) : undefined;

  const renderItem = (item: NavItem) => {
    const active = isNavItemActive(item, pathname);
    return (
      <SidebarMenuItem key={item.href}>
        <SidebarMenuButton asChild isActive={active} size={touch ? "touch" : "default"}>
          <Link href={item.href} onClick={onNavigate}>
            <Icon as={item.icon} size="nav" />
            <span>{item.label}</span>
            {item.href === "/console/sessions" ? <LiveDot count={liveCount} /> : null}
          </Link>
        </SidebarMenuButton>
      </SidebarMenuItem>
    );
  };

  return (
    <>
      <SidebarHeader className={touch ? "pr-12" : undefined}>
        <Link
          href="/console"
          onClick={onNavigate}
          className="flex h-9 min-w-0 flex-1 items-center gap-2 rounded px-2 outline-none focus-visible:shadow-focus"
        >
          <StateMeter state="idle" size="sm" bars={4} />
          <span className="min-w-0 flex-1 truncate text-control">
            <span className="font-semibold text-foreground">LKAP</span>{" "}
            <span className="font-normal text-text-secondary">Console</span>
          </span>
        </Link>
      </SidebarHeader>
      <SidebarContent>
        <nav aria-label="Console" className="flex flex-col gap-4">
          {NAV_GROUPS.map((group, index) => {
            const items = group.items.filter((item) => !item.hidden);
            if (items.length === 0) return null;
            const labelId = group.label ? `sidebar-group-${group.label.toLowerCase()}${touch ? "-menu" : ""}` : undefined;
            return (
              <SidebarGroup key={group.label ?? `ungrouped-${index}`}>
                {group.label ? <SidebarGroupLabel id={labelId}>{group.label}</SidebarGroupLabel> : null}
                <SidebarMenu aria-labelledby={labelId}>{items.map(renderItem)}</SidebarMenu>
              </SidebarGroup>
            );
          })}
        </nav>
      </SidebarContent>
      <SidebarFooter>
        {docsUrl ? (
          <a
            href={docsUrl}
            target="_blank"
            rel="noreferrer noopener"
            className="flex h-[34px] items-center gap-2.5 rounded px-2.5 text-control font-medium text-text-secondary outline-none transition-colors duration-(--duration-fast) hover:bg-sidebar-hover hover:text-foreground focus-visible:shadow-focus"
          >
            <Icon as={ExternalLinkIcon} size="nav" />
            <span className="min-w-0 flex-1 truncate">Documentation</span>
          </a>
        ) : null}
        <AccountMenu version={health?.version} />
      </SidebarFooter>
    </>
  );
}

export function AppSidebar() {
  return (
    <>
      <Sidebar>
        <SidebarBody />
      </Sidebar>
      <SidebarMenuDialog>
        <SidebarBody touch />
      </SidebarMenuDialog>
    </>
  );
}
