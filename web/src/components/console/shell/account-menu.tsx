"use client";

import * as React from "react";
import Link from "next/link";
import { LogOutIcon, UserRoundIcon } from "lucide-react";

import { Icon } from "@/components/shared/icon";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { BREAK_GLASS_USER_ID, signOut } from "@/components/console/lib/sign-out";
import { useMe } from "./use-me";

/** Up to two initials from a name, else the email's first letter. */
export function initialsFor(name: string | undefined, email: string): string {
  const words = (name ?? "").trim().split(/\s+/).filter(Boolean);
  if (words.length > 0) return words.slice(0, 2).map((word) => word[0]!.toUpperCase()).join("");
  return (email[0] ?? "?").toUpperCase();
}

/**
 * The signed-in user's menu in the sidebar footer (ask V2-14-1): who you are,
 * a link to the Account settings and **Sign out**, reachable from every
 * console screen (the rail on desktop, the sheet on phones). Hidden while
 * `/v1/auth/me` is unavailable. The break-glass admin token has no session to
 * end, so its menu says so instead of offering a sign-out that does nothing.
 */
export function AccountMenu() {
  const { me, unavailable } = useMe();
  if (unavailable || !me) return null;

  const { user } = me;
  const breakGlass = user.id === BREAK_GLASS_USER_ID;
  const displayName = user.name?.trim() || user.email;

  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        data-testid="account-menu-trigger"
        aria-label={`Account: ${displayName}`}
        className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm text-sidebar-foreground outline-none hover:bg-sidebar-accent hover:text-sidebar-accent-foreground focus-visible:ring-2 focus-visible:ring-ring group-data-[collapsible=icon]:size-8 group-data-[collapsible=icon]:justify-center group-data-[collapsible=icon]:p-0"
      >
        <span
          aria-hidden="true"
          className="flex size-6 shrink-0 items-center justify-center rounded-full bg-muted text-[0.6875rem] font-semibold text-muted-foreground"
        >
          {initialsFor(user.name, user.email)}
        </span>
        <span className="min-w-0 flex-1 group-data-[collapsible=icon]:hidden">
          <span className="block truncate font-medium">{displayName}</span>
          {user.name ? <span className="block truncate text-xs text-muted-foreground">{user.email}</span> : null}
        </span>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" side="right" className="w-60">
        <DropdownMenuLabel className="font-normal">
          <span className="block truncate text-sm font-medium text-foreground">{displayName}</span>
          <span className="block truncate text-xs text-muted-foreground">{user.email}</span>
        </DropdownMenuLabel>
        <DropdownMenuSeparator />
        <DropdownMenuItem asChild>
          <Link href="/console/settings?tab=workspace#account">
            <Icon as={UserRoundIcon} size="sm" />
            Account settings
          </Link>
        </DropdownMenuItem>
        <DropdownMenuSeparator />
        {breakGlass ? (
          <p className="px-2 py-1.5 text-xs text-pretty text-muted-foreground">
            Signed in with the break-glass admin token — there is no session to sign out of.
          </p>
        ) : (
          <DropdownMenuItem onSelect={() => void signOut()}>
            <Icon as={LogOutIcon} size="sm" />
            Sign out
          </DropdownMenuItem>
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
