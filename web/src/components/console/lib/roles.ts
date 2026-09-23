"use client";

import { useActiveWorkspace } from "@/components/console/settings/use-settings-queries";
import type { WorkspaceMembership } from "@/contracts/lkap-contracts";

/**
 * Role-based UI gating (docs/v2/_asks.md V2-20-5).
 *
 * The server is the real gate — every write route already checks role/scope
 * (`auth/roles.py::ROUTE_POLICY`) and refuses anything below its floor. This
 * module exists so the console stops *offering* a control the api will 403,
 * which is only ever a usability improvement, never a security boundary on
 * its own (a `viewer` who forges a request still gets a clean 403).
 *
 * Before this ask, three files each rolled their own admin/owner check
 * (`settings/workspace-tab.tsx`, `settings/team-tab.tsx`,
 * `telephony/dialing-policy.tsx`) — this is the shared version of that
 * pattern, generalised to any role floor.
 */
export type Role = WorkspaceMembership["role"];

const ROLE_RANK: Record<Role, number> = { viewer: 0, builder: 1, admin: 2, owner: 3 };

/** Whether `role` meets or exceeds `min` in the `viewer < builder < admin < owner` order. */
export function roleAtLeast(role: Role | undefined, min: Role): boolean {
  if (!role) return false;
  return ROLE_RANK[role] >= ROLE_RANK[min];
}

export interface WriteAccessState {
  /** The caller's role in the active workspace; `undefined` while loading or signed out. */
  role: Role | undefined;
  /** `roleAtLeast(role, min)` — `false` while loading, same as an unprivileged role. */
  canWrite: boolean;
  isLoading: boolean;
}

/**
 * Whether the active workspace membership can perform a `min`+ mutation.
 *
 * Defaults to `"builder"`, the floor for every ordinary create/edit/delete
 * action; pass `"admin"` for the stricter controls REVIEW-V2 calls out
 * (provider credentials, workspace/dialing policy, member management).
 * Loading and signed-out both read as `canWrite: false` — a control this
 * gates should never flash enabled before the real role is known.
 */
export function useWriteAccess(min: Role = "builder"): WriteAccessState {
  const { workspace, isLoading } = useActiveWorkspace();
  return { role: workspace?.role, canWrite: roleAtLeast(workspace?.role, min), isLoading };
}

/** The tooltip/inline copy a gated control shows when `min` isn't met. */
export function writeAccessReason(min: Role = "builder"): string {
  return min === "admin"
    ? "Only admins and owners can do this."
    : "Viewers can't do this — ask a builder, admin or owner.";
}
