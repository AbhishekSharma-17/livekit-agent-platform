"use client";

import * as React from "react";

import { useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api";
import { useMe } from "@/components/console/shell/use-me";
import type { WorkspaceMembership } from "@/contracts/lkap-contracts";
import type { ApiKeyOut, AuditOut, MemberOut, Page, WorkspaceOut } from "./api-types";

/**
 * Settings tabs need the *path* workspace id (`/v1/workspaces/{id}/...`),
 * not just the `X-Workspace` header the proxy already attaches from the
 * `lkap_workspace` cookie (`lib/auth.ts::activeWorkspace`) — so this reads
 * the same cookie WP-1's `WorkspaceSwitcher` writes
 * (`components/console/shell/workspace-switcher.tsx`) to pick the same
 * membership out of `useMe()`, falling back to the caller's first workspace.
 */
const ACTIVE_WORKSPACE_COOKIE = "lkap_workspace";

function readActiveWorkspaceCookie(): string | undefined {
  if (typeof document === "undefined") return undefined;
  const match = document.cookie.match(new RegExp(`(?:^|; )${ACTIVE_WORKSPACE_COOKIE}=([^;]*)`));
  return match ? decodeURIComponent(match[1]) : undefined;
}

export interface ActiveWorkspaceState {
  workspace: WorkspaceMembership | undefined;
  isLoading: boolean;
}

/** The workspace the Settings tabs act on, and the caller's role in it. */
export function useActiveWorkspace(): ActiveWorkspaceState {
  const { me, isLoading } = useMe();
  const workspace = React.useMemo(() => {
    const workspaces = me?.workspaces ?? [];
    const slug = readActiveWorkspaceCookie();
    return workspaces.find((w) => w.slug === slug) ?? workspaces[0];
  }, [me]);
  return { workspace, isLoading };
}

/**
 * `routers/workspaces.py` has no `GET /v1/workspaces/{id}` — only the list
 * (`GET /v1/workspaces`) and `PUT /v1/workspaces/{id}`. So this reads the
 * list (which, unlike `Me.workspaces`, carries the full `WorkspaceOut`
 * including `settings`) and picks the active one out of it.
 */
export function useWorkspace(workspaceId: string | undefined) {
  return useQuery({
    queryKey: ["settings", "workspace", workspaceId] as const,
    queryFn: () => api.get<Page<WorkspaceOut>>("workspaces"),
    enabled: Boolean(workspaceId),
    select: (page) => page.items.find((w) => w.id === workspaceId),
  });
}

/** `GET /v1/workspaces/{id}/members` — every member of the active workspace, with their role. */
export function useMembers(workspaceId: string | undefined) {
  return useQuery({
    queryKey: ["settings", "members", workspaceId] as const,
    queryFn: () => api.get<Page<MemberOut>>(`workspaces/${workspaceId}/members`, { limit: 200 }),
    enabled: Boolean(workspaceId),
  });
}

export function useApiKeys(workspaceId: string | undefined) {
  return useQuery({
    queryKey: ["settings", "api-keys", workspaceId] as const,
    queryFn: () => api.get<Page<ApiKeyOut>>("api-keys", { limit: 200 }),
    enabled: Boolean(workspaceId),
  });
}

export function useAudit(workspaceId: string | undefined) {
  return useQuery({
    queryKey: ["settings", "audit", workspaceId] as const,
    queryFn: () => api.get<Page<AuditOut>>("audit", { limit: 50 }),
    enabled: Boolean(workspaceId),
  });
}

/** Invalidate everything Settings reads for the active workspace (after a mutation). */
export function useInvalidateSettings() {
  const queryClient = useQueryClient();
  return (workspaceId: string | undefined) => {
    queryClient.invalidateQueries({ queryKey: ["settings"] });
    queryClient.invalidateQueries({ queryKey: ["auth", "me"] });
    if (workspaceId) queryClient.invalidateQueries({ queryKey: ["settings", "workspace", workspaceId] });
  };
}
