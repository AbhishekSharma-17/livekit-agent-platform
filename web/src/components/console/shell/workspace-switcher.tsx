"use client";

import * as React from "react";
import { ChevronsUpDownIcon, CheckIcon } from "lucide-react";

import { Icon } from "@/components/shared/icon";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { cn } from "@/lib/utils";
import { useMe } from "./use-me";

/**
 * Compact popover in the sidebar footer (docs/v2/UI_UX_SPEC-V2-AMENDMENTS.md
 * §1: "name + role chip, only when the user belongs to > 1 workspace").
 *
 * `GET /v1/auth/me` (V2-02) is landing in parallel with this package; until
 * it exists `useMe` reports `unavailable` and this component renders
 * nothing rather than a broken switcher. Persisting the *active* workspace
 * across requests needs a cookie the console proxy reads (`X-Workspace`,
 * docs/v2/CONTRACTS-V2.md §3.1) — that cookie is `lkap_workspace`, set here,
 * but nothing yet reads it server-side; that wiring is V2-02's proxy change
 * (see the hand-off note), not a file WP-1 owns.
 */
const ACTIVE_WORKSPACE_COOKIE = "lkap_workspace";

function setActiveWorkspaceCookie(slug: string) {
  document.cookie = `${ACTIVE_WORKSPACE_COOKIE}=${encodeURIComponent(slug)}; path=/; max-age=${60 * 60 * 24 * 365}`;
}

function readActiveWorkspaceCookie(): string | undefined {
  const match = document.cookie.match(new RegExp(`(?:^|; )${ACTIVE_WORKSPACE_COOKIE}=([^;]*)`));
  return match ? decodeURIComponent(match[1]) : undefined;
}

export function WorkspaceSwitcher() {
  const { me, unavailable } = useMe();
  const workspaces = React.useMemo(() => me?.workspaces ?? [], [me]);
  const [activeSlug, setActiveSlug] = React.useState<string | undefined>(undefined);

  // Client-only read (the cookie isn't sent to a server component here, so
  // there's no hydration mismatch): seeds the trigger from whatever was
  // last picked, instead of always falling back to `workspaces[0]`.
  React.useEffect(() => {
    const stored = readActiveWorkspaceCookie();
    if (stored) setActiveSlug(stored);
  }, []);

  if (unavailable || workspaces.length < 2) return null;

  const active = workspaces.find((w) => w.slug === activeSlug) ?? workspaces[0];

  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm text-sidebar-foreground outline-none hover:bg-sidebar-accent hover:text-sidebar-accent-foreground focus-visible:ring-2 focus-visible:ring-ring"
        aria-label={`Workspace: ${active.name}`}
      >
        <span className="min-w-0 flex-1 truncate">
          <span className="block truncate font-medium">{active.name}</span>
          <span className="block truncate text-xs text-muted-foreground capitalize">{active.role}</span>
        </span>
        <Icon as={ChevronsUpDownIcon} size="sm" className="text-muted-foreground" />
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" side="right" className="w-56">
        <DropdownMenuLabel>Workspaces</DropdownMenuLabel>
        <DropdownMenuSeparator />
        {workspaces.map((workspace) => (
          <DropdownMenuItem
            key={workspace.id}
            onSelect={() => {
              setActiveSlug(workspace.slug);
              setActiveWorkspaceCookie(workspace.slug);
              window.location.reload();
            }}
            className={cn("flex items-center justify-between gap-2")}
          >
            <span className="min-w-0 flex-1 truncate">
              <span className="block truncate">{workspace.name}</span>
              <span className="block truncate text-xs text-muted-foreground capitalize">{workspace.role}</span>
            </span>
            {workspace.slug === active.slug ? <Icon as={CheckIcon} size="sm" /> : null}
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
