"use client";

import * as React from "react";
import { CheckIcon } from "lucide-react";

import { Icon } from "@/components/shared/icon";
import type { WorkspaceMembership } from "@/contracts/lkap-contracts";
import { cn } from "@/lib/utils";

/**
 * The workspace switcher inside the account menu (docs/ui/DESIGN-SYSTEM.md
 * section 7.1; docs/v2/UI_UX_SPEC-V2-AMENDMENTS.md §1: name + role, a choice
 * only when the person belongs to more than one workspace).
 *
 * The active workspace is the `lkap_workspace` cookie; the console proxy
 * forwards it as `X-Workspace` (`app/api/console/[...path]/route.ts`), so a
 * switch reloads the page to refetch everything under the new workspace.
 */
const ACTIVE_WORKSPACE_COOKIE = "lkap_workspace";

type Workspace = WorkspaceMembership;

function setActiveWorkspaceCookie(slug: string) {
  document.cookie = `${ACTIVE_WORKSPACE_COOKIE}=${encodeURIComponent(slug)}; path=/; max-age=${60 * 60 * 24 * 365}`;
}

function readActiveWorkspaceCookie(): string | undefined {
  const match = document.cookie.match(new RegExp(`(?:^|; )${ACTIVE_WORKSPACE_COOKIE}=([^;]*)`));
  return match ? decodeURIComponent(match[1]) : undefined;
}

/** The workspace the console is acting in: the cookie's, else the first. */
export function useActiveWorkspaceSlug(workspaces: readonly Workspace[]): [Workspace | undefined, (slug: string) => void] {
  const [activeSlug, setActiveSlug] = React.useState<string | undefined>(undefined);
  // Client-only read after mount, so server and client render the same first pass.
  React.useEffect(() => {
    const stored = readActiveWorkspaceCookie();
    if (stored) setActiveSlug(stored);
  }, []);
  const active = workspaces.find((w) => w.slug === activeSlug) ?? workspaces[0];
  const choose = React.useCallback((slug: string) => {
    setActiveSlug(slug);
    setActiveWorkspaceCookie(slug);
    window.location.reload();
  }, []);
  return [active, choose];
}

/**
 * One workspace: its name and role as plain text. Several: a list to switch
 * between, the active one checked.
 */
export function WorkspaceSwitcher({ workspaces }: { workspaces: readonly Workspace[] }) {
  const [active, choose] = useActiveWorkspaceSlug(workspaces);
  if (!active) return null;

  if (workspaces.length < 2) {
    return (
      <div data-slot="workspace-current" className="px-2 py-1.5">
        <p className="text-caption text-text-tertiary">Workspace</p>
        <p className="truncate text-label font-medium text-foreground">{active.name}</p>
        <p className="truncate text-caption text-text-secondary capitalize">{active.role}</p>
      </div>
    );
  }

  return (
    <div role="group" aria-labelledby="workspace-switcher-label" data-slot="workspace-switcher">
      <p id="workspace-switcher-label" className="px-2 pt-1 pb-1 text-caption font-medium text-text-tertiary">
        Workspaces
      </p>
      <ul className="flex flex-col gap-0.5">
        {workspaces.map((workspace) => {
          const current = workspace.slug === active.slug;
          return (
            <li key={workspace.id}>
              <button
                type="button"
                aria-current={current ? "true" : undefined}
                aria-label={`Workspace: ${workspace.name}`}
                onClick={() => {
                  if (!current) choose(workspace.slug);
                }}
                className={cn(
                  "flex min-h-[34px] w-full items-center gap-2 rounded-sm px-2 py-1 text-left outline-none transition-colors duration-(--duration-fast) hover:bg-muted focus-visible:shadow-focus",
                )}
              >
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-control text-foreground">{workspace.name}</span>
                  <span className="block truncate text-caption text-text-secondary capitalize">{workspace.role}</span>
                </span>
                {current ? <Icon as={CheckIcon} size="md" className="text-brand-text" /> : null}
              </button>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
