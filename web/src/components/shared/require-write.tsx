"use client";

import { LockIcon } from "lucide-react";

import { useWriteAccess, writeAccessReason, type Role } from "@/components/console/lib/roles";
import { EmptyState } from "@/components/shared/empty-state";
import { LoadingRow } from "@/components/shared/loading-state";

export interface RequireWriteProps {
  children: React.ReactNode;
  /** Role floor; defaults to `"builder"`. */
  min?: Role;
  title?: string;
  /** The next step, e.g. "Ask an admin to add connections." */
  description?: string;
}

/**
 * Gates an entire page or section behind a role floor (decision D12), for a
 * create form reached by direct URL (`/console/*\/new`) rather than from a
 * button the page already hides. While the role loads it shows a loading
 * row (never a blank area); below the floor it shows a complete alternative
 * that names the next step ("Ask an admin"), not a lone padlock.
 *
 * Kept as a small client wrapper so the page itself can stay a server
 * component (and keep exporting `metadata`).
 */
export function RequireWrite({ children, min = "builder", title, description }: RequireWriteProps) {
  const { canWrite, isLoading } = useWriteAccess(min);
  if (isLoading) return <LoadingRow label="Checking your access…" />;
  if (canWrite) return <>{children}</>;
  return (
    <EmptyState
      icon={LockIcon}
      title={title ?? "You don't have access to this"}
      description={description ?? writeAccessReason(min)}
    />
  );
}
