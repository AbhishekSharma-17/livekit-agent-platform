import * as React from "react";

import { Skeleton } from "@/components/ui/skeleton";
import { LoadingRegion } from "@/components/shared/loading-state";
import { SectionRow } from "@/components/shared/section";
import { cn } from "@/lib/utils";

/**
 * Layout pieces shared by the Settings cards (docs/ui/DESIGN-SYSTEM.md
 * sections 6.7 and 7.4): the card footer that holds a card's own Save, and
 * loading skeletons that mirror a card's fields or rows.
 */

/** A card footer row: muted fill, top hairline (from the section's `divide-y`), actions right-aligned, primary last. */
export function SettingsCardFooter({ className, children }: { className?: string; children: React.ReactNode }) {
  return (
    <SectionRow
      data-slot="settings-card-footer"
      className={cn("flex flex-wrap items-center justify-end gap-2 rounded-b-lg bg-muted py-3", className)}
    >
      {children}
    </SectionRow>
  );
}

/** One form field while loading: a label line over a 36 px control. */
export function FieldSkeleton({ className, tall = false }: { className?: string; tall?: boolean }) {
  return (
    <div className={cn("flex flex-col gap-1.5", className)}>
      <Skeleton className="h-3.5 w-28" />
      <Skeleton className={tall ? "h-16 w-full" : "h-9 w-full"} />
    </div>
  );
}

/** A form card's body while loading: `fields` field skeletons in the card's own grid. */
export function FormSkeleton({
  label,
  fields = 2,
  columns = 1,
  className,
}: {
  label: string;
  fields?: number;
  columns?: 1 | 2;
  className?: string;
}) {
  return (
    <LoadingRegion
      label={label}
      className={cn("grid max-w-lg gap-4", columns === 2 && "sm:grid-cols-2", className)}
    >
      {Array.from({ length: fields }, (_, index) => (
        <FieldSkeleton key={index} />
      ))}
    </LoadingRegion>
  );
}

/** A list card's rows while loading: a name-and-meta block, a status pill and an action, per row. */
export function RowsSkeleton({ label, rows = 3 }: { label: string; rows?: number }) {
  return (
    <LoadingRegion label={label} className="flex flex-col divide-y divide-border rounded-lg border border-border">
      {Array.from({ length: rows }, (_, index) => (
        <div key={index} className="flex items-center gap-4 px-3.5 py-3">
          <div className="flex min-w-0 flex-1 flex-col gap-1.5">
            <Skeleton className={cn("h-3.5", index % 2 === 0 ? "w-40" : "w-32")} />
            <Skeleton className="h-3 w-24" />
          </div>
          <Skeleton className="h-5 w-16 rounded-pill" />
          <Skeleton className="hidden h-7 w-16 sm:block" />
        </div>
      ))}
    </LoadingRegion>
  );
}
