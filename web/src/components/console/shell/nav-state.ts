"use client";

import { useSessions } from "@/components/console/lib/api-hooks";
import { useActiveWorkspace } from "@/components/console/settings/use-settings-queries";
import type { TabBarAudience } from "./nav-config";

/**
 * Which phone tab bar set the person gets (decision D8). Only a known
 * `viewer` gets the viewer set. An unknown role (loading, or the admin-token
 * mode where `auth/me` isn't wired up) gets the builder set: the admin token
 * is an admin, and the bar doesn't flip from one set to the other on load.
 */
export function useTabBarAudience(): TabBarAudience {
  const { workspace } = useActiveWorkspace();
  return workspace?.role === "viewer" ? "viewer" : "builder";
}

/** Live sessions right now: the Sessions link's pulsing dot and the tab bar's badge. */
export function useLiveSessionCount(): number {
  const { data } = useSessions(undefined, "active");
  return data?.items.filter((session) => session.status === "active").length ?? 0;
}
