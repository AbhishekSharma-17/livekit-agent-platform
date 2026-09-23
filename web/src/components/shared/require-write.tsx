"use client";

import { LockIcon } from "lucide-react";

import { EmptyState } from "@/components/shared/empty-state";
import { useWriteAccess, writeAccessReason, type Role } from "@/components/console/lib/roles";

export interface RequireWriteProps {
  children: React.ReactNode;
  /** Role floor; defaults to `"builder"`. */
  min?: Role;
  title?: string;
}

/**
 * Gates an entire page/section behind a role floor (docs/v2/_asks.md
 * V2-20-5) — for a create form reached by direct URL (`/console/*\/new`)
 * rather than from a button this package already hides, so there is no
 * earlier point to intercept the visit. Renders nothing (not even the empty
 * state) while the role is still loading, to avoid a flash of "no access"
 * for someone who does have it.
 *
 * Kept as a small client wrapper so the page itself can stay a server
 * component (and keep exporting `metadata`, which a `"use client"` page
 * cannot).
 */
export function RequireWrite({ children, min = "builder", title }: RequireWriteProps) {
  const { canWrite, isLoading } = useWriteAccess(min);
  if (isLoading) return null;
  if (canWrite) return <>{children}</>;
  return (
    <EmptyState
      icon={LockIcon}
      title={title ?? "You don't have access to this"}
      description={writeAccessReason(min)}
    />
  );
}
