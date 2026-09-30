import * as React from "react";

import { Skeleton } from "@/components/ui/skeleton";
import { LoadingRegion } from "@/components/shared/loading-state";
import { cn } from "@/lib/utils";

/**
 * Loading state for the Connect lists (connections, providers, keys;
 * docs/ui/DESIGN-SYSTEM.md sections 6.5 and 8.1): a framed list of rows
 * that mirrors the loaded one — a leading mark, a title and meta line, a
 * status pill and a trailing action — so the data replaces it without a
 * layout jump.
 */
export function RowsSkeleton({
  label,
  rows = 3,
  mark = false,
  className,
}: {
  /** What is loading, announced to screen readers ("Loading connections"). */
  label: string;
  rows?: number;
  /** Draw the leading vendor mark (keys and provider rows). */
  mark?: boolean;
  className?: string;
}) {
  return (
    <LoadingRegion
      label={label}
      className={cn("flex flex-col divide-y divide-border overflow-hidden rounded-lg border border-border bg-card", className)}
    >
      {Array.from({ length: rows }, (_, index) => (
        <div key={index} className="flex min-h-14 items-center gap-3 px-3.5 py-3">
          {mark ? <Skeleton className="size-7 shrink-0 rounded" /> : null}
          <div className="flex min-w-0 flex-1 flex-col gap-1.5">
            <Skeleton className={cn("h-3.5", index % 2 === 0 ? "w-40" : "w-32")} />
            <Skeleton className="h-3 w-24" />
          </div>
          <Skeleton className="hidden h-5 w-20 rounded-pill sm:block" />
          <Skeleton className="size-7 shrink-0 rounded-sm" />
        </div>
      ))}
    </LoadingRegion>
  );
}
