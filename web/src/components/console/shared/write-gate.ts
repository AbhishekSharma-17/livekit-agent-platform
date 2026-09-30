"use client";

import { useWriteAccess, type Role } from "@/components/console/lib/roles";

export interface WriteGate {
  /** Render the control: the person may use it, or their role is still loading. */
  show: boolean;
  /** Render it disabled: the role is still loading, so it never flashes on and then away. */
  pending: boolean;
  /** The resolved answer (false while loading). */
  can: boolean;
}

/**
 * The permission pattern for a single control (docs/ui/DESIGN-SYSTEM.md
 * section 8.5, decision D12): a control the person can't use is **not
 * rendered**; while the role is still loading it renders disabled, exactly
 * like `NewResourceButton`, so nothing appears and then disappears. Page-level
 * primaries pair this with a `ReadOnlyNote` fallback.
 */
export function useWriteGate(min: Role = "builder"): WriteGate {
  const { canWrite, isLoading } = useWriteAccess(min);
  return { show: canWrite || isLoading, pending: isLoading, can: canWrite };
}
