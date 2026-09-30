"use client";

import * as React from "react";

import { useWriteAccess, type Role } from "@/components/console/lib/roles";
import { ReadOnlyNote } from "@/components/shared/read-only-note";

/**
 * The permission pattern (docs/ui/DESIGN-SYSTEM.md section 8.5, decision D12).
 *
 * - **Row and edit actions** the person can't use are not rendered at all:
 *   wrap them in `<IfCan min="builder">…</IfCan>`.
 * - **Page-level primaries** are replaced by a read-only note:
 *   `<IfCan min="admin" fallback={<ReadOnlyNote>Ask an admin to add connections.</ReadOnlyNote>}>`.
 *
 * The server stays the real gate (`auth/roles.py::ROUTE_POLICY`); this only
 * stops offering controls the api would refuse. While the role loads,
 * `IfCan` renders `loading` (nothing by default), so a control never flashes
 * on and then disappears.
 */
export function useCan(min: Role = "builder"): { can: boolean; isLoading: boolean; role: Role | undefined } {
  const { canWrite, isLoading, role } = useWriteAccess(min);
  return { can: canWrite, isLoading, role };
}

export interface IfCanProps {
  /** Role floor; defaults to `"builder"`. */
  min?: Role;
  children: React.ReactNode;
  /** Rendered when the role is below the floor (e.g. a `ReadOnlyNote`). Default: nothing. */
  fallback?: React.ReactNode;
  /** Rendered while the role is still loading. Default: nothing. */
  loading?: React.ReactNode;
}

export function IfCan({ min = "builder", children, fallback = null, loading = null }: IfCanProps) {
  const { can, isLoading } = useCan(min);
  if (isLoading) return <>{loading}</>;
  return <>{can ? children : fallback}</>;
}

/** The standard note for a page-level action the person can't take. */
export function readOnlyCopy(min: Role = "builder", action?: string): string {
  const who = min === "admin" || min === "owner" ? "an admin" : "a builder or admin";
  return action ? `Ask ${who} to ${action}.` : `You can view this. Ask ${who} to make changes.`;
}

export { ReadOnlyNote };
