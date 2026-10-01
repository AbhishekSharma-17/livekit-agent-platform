"use client";

import * as React from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { ExternalLinkIcon } from "lucide-react";

import { Icon } from "@/components/shared/icon";
import { LkapLogo } from "@/components/shared/lkap-logo";
import {
  Sidebar,
  SidebarCollapseTrigger,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuDialog,
  SidebarMenuItem,
  SidebarMenuLabel,
  SidebarRailTooltip,
  useSidebar,
} from "@/components/ui/sidebar";
import { useHealth } from "@/components/console/lib/api-hooks";
import { cn } from "@/lib/utils";
import { NAV_GROUPS, isNavItemActive, type NavItem } from "./nav-config";
import { useLiveSessionCount } from "./nav-state";
import { AccountMenu } from "./account-menu";

/**
 * The Sessions link's live indicator: a 7 px pulsing dot with its count for
 * screen readers. In the icon rail it moves onto the icon's top right corner.
 */
function LiveDot({ count }: { count: number }) {
  if (count <= 0) return null;
  return (
    <span
      data-slot="sidebar-live-dot"
      className="ml-auto flex items-center group-data-[state=collapsed]/sidebar:absolute group-data-[state=collapsed]/sidebar:top-1.5 group-data-[state=collapsed]/sidebar:left-[27px]"
    >
      <span aria-hidden="true" data-slot="status-dot" data-pulse="" className="size-[7px] rounded-pill bg-brand" />
      <span className="sr-only">, {count} live</span>
    </span>
  );
}

/** In the icon rail (desktop only), a link's name also shows in a tooltip. */
function RailTooltip({ label, touch, children }: { label: string; touch: boolean; children: React.ReactElement }) {
  return touch ? children : <SidebarRailTooltip label={label}>{children}</SidebarRailTooltip>;
}

/**
 * The sidebar's content (docs/ui/DESIGN-SYSTEM.md section 7.1): the LKAP
 * logo (28 px mark and wordmark, a link to Overview), the nav grouped by job, then the account menu at the foot above a
 * hairline. Rendered twice from one component: in the desktop sidebar, and in
 * the full-screen Menu dialog at 820 px and below (48 px rows there).
 *
 * On desktop the header also holds the collapse control. Collapsed, the
 * sidebar is a 58 px icon rail: the logo shows only its mark, centred, the
 * link labels fade (every link keeps its name, the logo's is "LKAP Console"), each link gets a tooltip, the live dot sits on
 * its icon and the account menu is just the avatar. The Menu dialog never
 * collapses.
 */
function SidebarBody({ touch = false }: { touch?: boolean }) {
  const pathname = usePathname() ?? "/console";
  const { data: health } = useHealth();
  const liveCount = useLiveSessionCount();
  const { setMenuOpen, collapsed } = useSidebar();
  const docsUrl = process.env.NEXT_PUBLIC_DOCS_URL;
  // In the Menu dialog, following a link closes it (focus goes back to the hamburger).
  const onNavigate = touch ? () => setMenuOpen(false) : undefined;

  const renderItem = (item: NavItem) => {
    const active = isNavItemActive(item, pathname);
    return (
      <SidebarMenuItem key={item.href}>
        <RailTooltip label={item.label} touch={touch}>
          <SidebarMenuButton asChild isActive={active} size={touch ? "touch" : "default"}>
            <Link href={item.href} onClick={onNavigate}>
              <Icon as={item.icon} size="nav" />
              <SidebarMenuLabel>{item.label}</SidebarMenuLabel>
              {item.href === "/console/sessions" ? <LiveDot count={liveCount} /> : null}
            </Link>
          </SidebarMenuButton>
        </RailTooltip>
      </SidebarMenuItem>
    );
  };

  return (
    <>
      <SidebarHeader
        className={
          touch
            ? "pr-12"
            : "group-data-[state=collapsed]/sidebar:flex-col group-data-[state=collapsed]/sidebar:items-stretch group-data-[state=collapsed]/sidebar:gap-1"
        }
      >
        <RailTooltip label="LKAP Console" touch={touch}>
          <Link
            href="/console"
            onClick={onNavigate}
            data-slot="sidebar-logo"
            className="flex h-11 min-w-0 flex-1 items-center rounded px-1.5 outline-none focus-visible:shadow-focus group-data-[state=collapsed]/sidebar:flex-none group-data-[state=collapsed]/sidebar:justify-center group-data-[state=collapsed]/sidebar:px-0"
          >
            <LkapLogo
              size="md"
              product="Console"
              textClassName="group-data-[state=collapsed]/sidebar:sr-only"
              className="group-data-[state=collapsed]/sidebar:gap-0"
            />
          </Link>
        </RailTooltip>
        {touch ? null : <SidebarCollapseTrigger className="group-data-[state=collapsed]/sidebar:self-center" />}
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
          <RailTooltip label="Documentation" touch={touch}>
            <a
              href={docsUrl}
              target="_blank"
              rel="noreferrer noopener"
              className={cn(
                touch ? "h-12" : "h-[34px]",
                "flex items-center gap-2.5 rounded px-2.5 text-control font-medium whitespace-nowrap text-text-secondary outline-none transition-colors duration-(--duration-fast) hover:bg-sidebar-hover hover:text-foreground focus-visible:shadow-focus",
              )}
            >
              <Icon as={ExternalLinkIcon} size="nav" />
              <SidebarMenuLabel>Documentation</SidebarMenuLabel>
            </a>
          </RailTooltip>
        ) : null}
        <AccountMenu version={health?.version} touch={touch} rail={!touch && collapsed} />
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
