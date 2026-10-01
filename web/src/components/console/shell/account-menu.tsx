"use client";

import * as React from "react";
import Link from "next/link";
import { ChevronsUpDownIcon, LogOutIcon, SlidersHorizontalIcon, UserRoundIcon } from "lucide-react";

import { Icon } from "@/components/shared/icon";
import { ThemeSwitcher } from "@/components/shared/theme-switcher";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { BREAK_GLASS_USER_ID, signOut } from "@/components/console/lib/sign-out";
import { cn } from "@/lib/utils";
import { DevViewAsMenuGroup } from "./dev-view-as";
import { useMe } from "./use-me";
import { WorkspaceSwitcher } from "./workspace-switcher";

/** Up to two initials from a name, else the email's first letter. */
export function initialsFor(name: string | undefined, email: string): string {
  const words = (name ?? "").trim().split(/\s+/).filter(Boolean);
  if (words.length > 0) return words.slice(0, 2).map((word) => word[0]!.toUpperCase()).join("");
  return (email[0] ?? "?").toUpperCase();
}

const TRIGGER =
  "flex w-full min-w-0 items-center gap-2.5 rounded px-2 py-1.5 text-left outline-none transition-colors duration-(--duration-fast) hover:bg-sidebar-hover focus-visible:shadow-focus data-[state=open]:bg-sidebar-hover";

const ROW =
  "flex h-[34px] w-full items-center gap-2 rounded-sm px-2 text-left text-control text-foreground outline-none transition-colors duration-(--duration-fast) hover:bg-muted focus-visible:shadow-focus [&>svg]:text-text-secondary";

function Separator() {
  return <div role="separator" className="-mx-1.5 my-1 h-px bg-border" />;
}

/**
 * The account menu at the foot of the sidebar (docs/ui/DESIGN-SYSTEM.md
 * section 7.1): avatar, name and email, then a popover with the workspace
 * switcher, the profile link, appearance (the System / Light / Dark theme
 * switcher), in development under the admin bypass a "Development" group
 * with the view-as role switch (`dev-view-as.tsx`), and sign out. It is on every console screen: in the sidebar on
 * desktop and in the Menu dialog at 820 px and below.
 *
 * While `auth/me` isn't available (the admin-token mode), there is no person
 * to show, but the menu stays so appearance is always reachable. The
 * break-glass admin token has no session to end, so its menu says so instead
 * of offering a sign-out that does nothing.
 */
export function AccountMenu({ version, touch = false }: { version?: string; /** 48 px trigger (the Menu dialog on phones). */ touch?: boolean }) {
  const { me, isLoading } = useMe();
  const [open, setOpen] = React.useState(false);

  if (!me && isLoading) {
    return <div aria-hidden="true" data-slot="account-menu-placeholder" className="h-11" />;
  }

  const user = me?.user;
  const breakGlass = user?.id === BREAK_GLASS_USER_ID;
  const displayName = user ? user.name?.trim() || user.email : undefined;
  const close = () => setOpen(false);

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger
        data-testid={user ? "account-menu-trigger" : "preferences-menu-trigger"}
        aria-label={displayName ? `Account: ${displayName}` : "Preferences"}
        className={cn(TRIGGER, touch && "min-h-12")}
      >
        {user ? (
          <span
            aria-hidden="true"
            className="flex size-7 shrink-0 items-center justify-center rounded-pill bg-muted-strong text-caption font-semibold text-text-secondary"
          >
            {initialsFor(user.name, user.email)}
          </span>
        ) : (
          <span
            aria-hidden="true"
            className="flex size-7 shrink-0 items-center justify-center rounded-pill bg-muted-strong text-text-secondary"
          >
            <Icon as={SlidersHorizontalIcon} size="sm" />
          </span>
        )}
        <span className="min-w-0 flex-1">
          <span className="block truncate text-label font-medium text-foreground">{displayName ?? "Preferences"}</span>
          {user?.name ? <span className="block truncate text-caption text-text-secondary">{user.email}</span> : null}
        </span>
        <Icon as={ChevronsUpDownIcon} size="md" className="text-text-tertiary" />
      </PopoverTrigger>
      <PopoverContent
        data-slot="account-menu"
        side="top"
        align="start"
        className="w-[min(calc(100vw-32px),260px)] gap-0"
      >
        {user ? (
          <>
            <div className="px-2 py-1.5">
              <p className="truncate text-label font-medium text-foreground">{displayName}</p>
              <p className="truncate text-caption text-text-secondary">{user.email}</p>
            </div>
            {me?.workspaces && me.workspaces.length > 0 ? (
              <>
                <Separator />
                <WorkspaceSwitcher workspaces={me.workspaces} />
              </>
            ) : null}
            <Separator />
            <Link href="/console/settings?tab=workspace#account" className={ROW} onClick={close}>
              <Icon as={UserRoundIcon} size="md" />
              Account settings
            </Link>
          </>
        ) : null}
        {user ? <Separator /> : null}
        <div className="flex items-center justify-between gap-3 px-2 py-1">
          <span className="text-control text-foreground">
            Appearance
          </span>
          <ThemeSwitcher />
        </div>
        {/* Development only, under the admin bypass (decision O6); folded away in production builds. */}
        {process.env.NODE_ENV === "development" && breakGlass ? (
          <>
            <Separator />
            <DevViewAsMenuGroup />
          </>
        ) : null}
        {user ? (
          <>
            <Separator />
            {breakGlass ? (
              <p className="px-2 py-1.5 text-caption text-pretty text-text-secondary">
                Signed in with the break-glass admin token. There is no session to sign out of.
              </p>
            ) : (
              <button
                type="button"
                className={ROW}
                onClick={() => {
                  close();
                  void signOut();
                }}
              >
                <Icon as={LogOutIcon} size="md" />
                Sign out
              </button>
            )}
          </>
        ) : null}
        {version ? (
          <p className="px-2 pt-1.5 pb-1 text-caption text-text-tertiary tabular-nums">Version {version}</p>
        ) : null}
      </PopoverContent>
    </Popover>
  );
}
