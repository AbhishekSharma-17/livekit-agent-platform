"use client";

import { RefreshCwIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/**
 * Refresh (docs/ui/DESIGN-SYSTEM.md sections 5 and 8.7): a secondary button
 * whose `RefreshCw` spins while a refetch runs, with a gerund label and the
 * button disabled until it finishes. Used in the Observe screens' headers.
 */
export function RefreshButton({
  onRefresh,
  refreshing,
  label = "Refresh",
  className,
}: {
  onRefresh: () => void;
  refreshing: boolean;
  label?: string;
  className?: string;
}) {
  return (
    <Button type="button" variant="secondary" disabled={refreshing} onClick={onRefresh} className={className}>
      <RefreshCwIcon aria-hidden="true" className={cn(refreshing && "animate-spin")} />
      {refreshing ? "Refreshing…" : label}
    </Button>
  );
}
